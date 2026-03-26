"""
Gap 2: Open Position Monitoring Agent

Reads open positions from account performance data, fetches current market
prices for each, and produces a monitoring memo recommending hold / close /
reduce / add for each open position.

Designed to run on a schedule (e.g. every 4 hours via cron or batch-run).
Writes output to data/performance/position_monitor_<timestamp>.json and
data/performance/position_monitor_latest.json.

No AI call is made here — this is a deterministic Python helper that
pre-computes the monitoring context. The actual hold/close/reduce/add
recommendation is made by passing the output to the assessor or a
dedicated position_monitor agent in a future AI-driven workflow.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PositionStatus:
    """Current state of a single open position."""

    market_slug: str
    token_id: str | None
    side: str  # YES or NO
    entry_price: float
    current_price: float
    size_shares: float
    entry_value_usd: float
    current_value_usd: float
    unrealized_pnl_usd: float
    unrealized_pnl_pct: float
    market_end_date: str | None
    question: str | None
    price_delta: float  # current_price - entry_price
    recommendation: str  # hold / close / reduce / add / unknown
    recommendation_reason: str
    fetched_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_slug": self.market_slug,
            "token_id": self.token_id,
            "side": self.side,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "size_shares": self.size_shares,
            "entry_value_usd": self.entry_value_usd,
            "current_value_usd": self.current_value_usd,
            "unrealized_pnl_usd": self.unrealized_pnl_usd,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "market_end_date": self.market_end_date,
            "question": self.question,
            "price_delta": self.price_delta,
            "recommendation": self.recommendation,
            "recommendation_reason": self.recommendation_reason,
            "fetched_at": self.fetched_at,
        }


# ---------------------------------------------------------------------------
# Thresholds for rule-based recommendations
# ---------------------------------------------------------------------------

# Close if position has moved favorably and is > 80% of max value (near resolution)
_CLOSE_PROFIT_THRESHOLD = 0.80
# Close if position has moved against us by more than 30%
_CLOSE_LOSS_THRESHOLD = -0.30
# Add flag if position moved in our favor by < 10% (thesis still valid, price better)
_ADD_OPPORTUNITY_THRESHOLD = -0.05


def _rule_based_recommendation(
    side: str,
    entry_price: float,
    current_price: float,
    unrealized_pnl_pct: float,
) -> tuple[str, str]:
    """
    Return (recommendation, reason) based on price movement rules.
    This is a lightweight deterministic pre-filter; the AI agent makes the
    final call after reading the full monitoring memo.
    """
    if current_price >= _CLOSE_PROFIT_THRESHOLD:
        return (
            "close",
            f"Market price {current_price:.2f} near resolution (≥{_CLOSE_PROFIT_THRESHOLD}); consider locking in gain.",
        )

    if unrealized_pnl_pct <= _CLOSE_LOSS_THRESHOLD:
        return (
            "close",
            f"Unrealized loss {unrealized_pnl_pct:.1%} exceeds -{abs(_CLOSE_LOSS_THRESHOLD):.0%} threshold; thesis may be invalidated.",
        )

    # Averaging-down: position price dropped ≥ 5% from entry (same logic for YES and NO).
    # Both represent a position that became cheaper; add only if thesis still holds.
    drop_pct = (entry_price - current_price) / max(entry_price, 1e-9)
    if drop_pct >= abs(_ADD_OPPORTUNITY_THRESHOLD):
        return (
            "add",
            f"Price fell {drop_pct:.1%} from entry {entry_price:.2f} → {current_price:.2f}; potential averaging-down opportunity if thesis intact.",
        )

    return "hold", "No rule-based trigger; recommend hold pending AI review."


# ---------------------------------------------------------------------------
# Position loading
# ---------------------------------------------------------------------------


def _load_open_positions(performance_summary_path: Path) -> list[dict[str, Any]]:
    """
    Read the latest performance summary JSON and return open positions list.
    Each entry is expected to have: market_slug, side, entry_price,
    size_shares, token_id, market_end_date, question.
    Returns [] if file absent or malformed.
    """
    if not performance_summary_path.exists():
        logger.debug("Performance summary not found at %s — no open positions to monitor", performance_summary_path)
        return []
    try:
        raw = json.loads(performance_summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to parse performance summary at %s: %s", performance_summary_path, exc)
        return []
    positions = raw.get("open_positions", [])
    logger.debug("Loaded %d open positions from %s", len(positions), performance_summary_path)
    return positions


def _fetch_current_price(gamma_api_base: str, market_slug: str) -> float | None:
    """Fetch the current YES price for a market from the Gamma API."""
    try:
        import urllib.request

        url = f"{gamma_api_base}/markets?slug={market_slug}&active=true&closed=false"
        req = urllib.request.Request(url, headers={"User-Agent": "polymarket-agent/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        markets = data if isinstance(data, list) else data.get("markets", [])
        for m in markets:
            if m.get("slug") == market_slug or m.get("conditionId"):
                tokens = m.get("tokens") or m.get("outcomes", [])
                for t in tokens:
                    if str(t.get("outcome", "")).upper() in {"YES", "1"}:
                        price = float(t.get("price", 0.0))
                        logger.debug("Fetched YES price %.4f for %s", price, market_slug)
                        return price
    except Exception as exc:
        logger.warning("Failed to fetch current price for %s: %s", market_slug, exc)
    logger.debug("Could not find YES price for %s — falling back to entry price", market_slug)
    return None


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def monitor_positions(
    performance_summary_path: Path,
    gamma_api_base: str,
) -> list[PositionStatus]:
    """
    Load open positions, fetch current prices, apply rule-based recommendations.
    Returns a list of PositionStatus objects (one per open position).
    """
    raw_positions = _load_open_positions(performance_summary_path)
    now = datetime.now(timezone.utc).isoformat()
    results: list[PositionStatus] = []

    for pos in raw_positions:
        slug = pos.get("market_slug", "")
        side = str(pos.get("side", "YES")).upper()
        entry_price = float(pos.get("entry_price", 0.0))
        size_shares = float(pos.get("size_shares", 0.0))
        token_id = pos.get("token_id")
        market_end_date = pos.get("market_end_date")
        question = pos.get("question")

        current_yes_price = _fetch_current_price(gamma_api_base, slug)
        if current_yes_price is None:
            current_price = entry_price  # fallback: no price movement known
        else:
            current_price = current_yes_price if side == "YES" else (1.0 - current_yes_price)

        entry_value_usd = entry_price * size_shares
        current_value_usd = current_price * size_shares
        unrealized_pnl_usd = current_value_usd - entry_value_usd
        unrealized_pnl_pct = unrealized_pnl_usd / entry_value_usd if entry_value_usd > 0 else 0.0
        price_delta = current_price - entry_price

        recommendation, recommendation_reason = _rule_based_recommendation(
            side, entry_price, current_price, unrealized_pnl_pct
        )
        logger.info(
            "Position %s (%s %s): entry=%.4f current=%.4f pnl=%.1f%% → %s",
            slug,
            side,
            size_shares,
            entry_price,
            current_price,
            unrealized_pnl_pct * 100,
            recommendation,
        )

        results.append(
            PositionStatus(
                market_slug=slug,
                token_id=token_id,
                side=side,
                entry_price=entry_price,
                current_price=current_price,
                size_shares=size_shares,
                entry_value_usd=entry_value_usd,
                current_value_usd=current_value_usd,
                unrealized_pnl_usd=unrealized_pnl_usd,
                unrealized_pnl_pct=unrealized_pnl_pct,
                market_end_date=market_end_date,
                question=question,
                price_delta=price_delta,
                recommendation=recommendation,
                recommendation_reason=recommendation_reason,
                fetched_at=now,
            )
        )

    return results


def write_monitor_result(
    positions: list[PositionStatus],
    output_path: Path,
    *,
    also_write_latest: bool = True,
) -> dict[str, Any]:
    """Write monitoring results to a timestamped JSON file and optionally latest.json."""
    payload: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "open_positions_count": len(positions),
        "positions": [p.as_dict() for p in positions],
        "summary": {
            "hold": sum(1 for p in positions if p.recommendation == "hold"),
            "close": sum(1 for p in positions if p.recommendation == "close"),
            "reduce": sum(1 for p in positions if p.recommendation == "reduce"),
            "add": sum(1 for p in positions if p.recommendation == "add"),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.debug("Monitor result written to %s", output_path)

    if also_write_latest:
        latest_path = output_path.parent / "position_monitor_latest.json"
        latest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.debug("Latest monitor result updated at %s", latest_path)

    summary = payload["summary"]
    logger.info(
        "Position monitor complete — %d positions: hold=%d close=%d add=%d",
        len(positions),
        summary["hold"],
        summary["close"],
        summary["add"],
    )
    return payload
