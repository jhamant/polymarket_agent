"""
Market scanner: fetch active markets, score each against quality criteria,
and return a ranked shortlist of the best candidates for research.

Scoring dimensions (25 points each, max 100):
  1. Liquidity depth    — higher raw liquidity is better
  2. Spread tightness   — tighter spread is better (uses spread field or bid/ask derivation)
  3. Timing window      — days-to-expiry in the sweet spot (3–45 days)
  4. Volume activity    — recent 24-hour volume signals active price discovery

Each dimension scores 0–25. Total score is 0–100.
A market with score < 40 is unlikely to be worth deep research.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .family_classifier import classify_market_family
from .market_context import _days_to_expiry, _safe_float
from .market_data import MarketSnapshot, fetch_active_markets

# ---------------------------------------------------------------------------
# Thresholds (can be overridden via env vars in a future iteration)
# ---------------------------------------------------------------------------

LIQUIDITY_TARGET = 5_000.0  # USDC — above this gets full points
SPREAD_MAX = 0.10  # 10 cents — above this gets 0 spread points
SPREAD_TARGET = 0.02  # 2 cents — at or below gets full points
DTE_SWEET_MIN = 3.0  # days
DTE_SWEET_MAX = 45.0  # days
DTE_SWEET_PEAK = 14.0  # days — highest score
VOLUME24H_TARGET = 500.0  # USDC — above this gets full points
MIN_SCORE_TO_SHOW = 20  # skip completely dead markets in output


@dataclass
class MarketScore:
    slug: str
    question: str
    end_date: str
    yes_price: float | None
    no_price: float | None
    liquidity: float
    volume24hr: float
    spread: float | None
    days_to_expiry: float | None
    family_key: str
    family_label: str
    family_matched_keywords: list[str]
    score_liquidity: float
    score_spread: float
    score_timing: float
    score_volume: float
    total_score: float
    flags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "question": self.question,
            "end_date": self.end_date,
            "yes_price": self.yes_price,
            "no_price": self.no_price,
            "liquidity": round(self.liquidity, 2),
            "volume_24hr": round(self.volume24hr, 2),
            "spread": round(self.spread, 4) if self.spread is not None else None,
            "days_to_expiry": round(self.days_to_expiry, 1) if self.days_to_expiry is not None else None,
            "family_key": self.family_key,
            "family_label": self.family_label,
            "family_keywords_matched": self.family_matched_keywords,
            "scores": {
                "liquidity": round(self.score_liquidity, 1),
                "spread": round(self.score_spread, 1),
                "timing": round(self.score_timing, 1),
                "volume": round(self.score_volume, 1),
                "total": round(self.total_score, 1),
            },
            "flags": self.flags,
        }


def score_market(market: MarketSnapshot, *, now_iso: str | None = None) -> MarketScore:
    if now_iso is None:
        now_iso = datetime.now(timezone.utc).isoformat()

    raw = market.raw

    # -- Spread --
    best_bid = _safe_float(raw.get("bestBid"))
    best_ask = _safe_float(raw.get("bestAsk"))
    spread_raw = _safe_float(raw.get("spread"))
    if spread_raw == 0.0 and best_bid and best_ask and best_ask > best_bid:
        spread_raw = round(best_ask - best_bid, 6)
    spread: float | None = spread_raw if spread_raw > 0 else None

    # -- Days to expiry --
    dte = _days_to_expiry(market.end_date, now_iso)

    # -- Volume 24h --
    volume24hr = _safe_float(raw.get("volume24hr"))

    # -- Family --
    family_key, family_label, family_keywords = classify_market_family(market.question, market.description)

    # -- Score liquidity (0–25) --
    liq = market.liquidity
    score_liquidity = min(25.0, 25.0 * liq / LIQUIDITY_TARGET) if liq > 0 else 0.0

    # -- Score spread (0–25) --
    if spread is None:
        score_spread = 10.0  # unknown spread: middle ground
    elif spread >= SPREAD_MAX:
        score_spread = 0.0
    elif spread <= SPREAD_TARGET:
        score_spread = 25.0
    else:
        # Linear interpolation between target and max
        score_spread = 25.0 * (SPREAD_MAX - spread) / (SPREAD_MAX - SPREAD_TARGET)

    # -- Score timing (0–25) — bell curve centred on peak --
    if dte is None or dte <= 0:
        score_timing = 0.0
    elif dte < DTE_SWEET_MIN:
        score_timing = 0.0  # too close to expiry
    elif dte > DTE_SWEET_MAX * 3:
        score_timing = 2.0  # very far out
    elif dte > DTE_SWEET_MAX:
        # Gradual decay past sweet spot
        score_timing = max(2.0, 25.0 * (DTE_SWEET_MAX * 1.5 - dte) / (DTE_SWEET_MAX * 1.0))
    else:
        # Triangle peak centred on DTE_SWEET_PEAK
        if dte <= DTE_SWEET_PEAK:
            score_timing = 25.0 * (dte - DTE_SWEET_MIN) / (DTE_SWEET_PEAK - DTE_SWEET_MIN)
        else:
            score_timing = 25.0 * (DTE_SWEET_MAX - dte) / (DTE_SWEET_MAX - DTE_SWEET_PEAK)
        score_timing = max(0.0, score_timing)

    # -- Score volume (0–25) --
    score_volume = min(25.0, 25.0 * volume24hr / VOLUME24H_TARGET) if volume24hr > 0 else 0.0

    total_score = score_liquidity + score_spread + score_timing + score_volume

    # -- Flags --
    flags: list[str] = []
    if not bool(raw.get("active", True)):
        flags.append("inactive")
    if bool(raw.get("closed", False)):
        flags.append("closed")
    if not bool(raw.get("acceptingOrders", True)):
        flags.append("not_accepting_orders")
    if spread is not None and spread >= SPREAD_MAX:
        flags.append("wide_spread")
    if liq < 500:
        flags.append("low_liquidity")
    if dte is not None and dte <= 0:
        flags.append("expired")
    if dte is not None and 0 < dte < DTE_SWEET_MIN:
        flags.append("imminent_expiry")
    if market.yes_price is not None and (market.yes_price < 0.03 or market.yes_price > 0.97):
        flags.append("near_resolution")

    return MarketScore(
        slug=market.slug,
        question=market.question,
        end_date=market.end_date,
        yes_price=market.yes_price,
        no_price=market.no_price,
        liquidity=liq,
        volume24hr=volume24hr,
        spread=spread,
        days_to_expiry=dte,
        family_key=family_key,
        family_label=family_label,
        family_matched_keywords=family_keywords,
        score_liquidity=score_liquidity,
        score_spread=score_spread,
        score_timing=score_timing,
        score_volume=score_volume,
        total_score=total_score,
        flags=flags,
    )


def scan_markets(
    gamma_api_base: str,
    *,
    limit: int = 200,
    min_score: float = MIN_SCORE_TO_SHOW,
    family_filter: str | None = None,
    top_n: int | None = None,
) -> dict[str, Any]:
    """
    Fetch active markets, score each, and return a ranked result dict.

    Returns:
      {
        "scanned_at": ISO timestamp,
        "total_fetched": int,
        "total_scored_above_min": int,
        "markets": [ MarketScore.as_dict(), ... ],   # sorted descending by total_score
        "family_summary": { family_key: count },
      }
    """
    scanned_at = datetime.now(timezone.utc).isoformat()
    markets = fetch_active_markets(gamma_api_base, limit)

    scored: list[MarketScore] = []
    for market in markets:
        ms = score_market(market, now_iso=scanned_at)
        # Skip dead / rejected markets
        if "expired" in ms.flags or "closed" in ms.flags:
            continue
        if ms.total_score < min_score:
            continue
        if family_filter and ms.family_key != family_filter:
            continue
        scored.append(ms)

    scored.sort(key=lambda ms: ms.total_score, reverse=True)
    if top_n:
        scored = scored[:top_n]

    family_summary: dict[str, int] = {}
    for ms in scored:
        family_summary[ms.family_key] = family_summary.get(ms.family_key, 0) + 1

    return {
        "scanned_at": scanned_at,
        "total_fetched": len(markets),
        "total_scored_above_min": len(scored),
        "markets": [ms.as_dict() for ms in scored],
        "family_summary": family_summary,
    }


def write_scan_result(
    gamma_api_base: str,
    output_path: Path,
    *,
    limit: int = 200,
    min_score: float = MIN_SCORE_TO_SHOW,
    family_filter: str | None = None,
    top_n: int | None = None,
) -> dict[str, Any]:
    result = scan_markets(
        gamma_api_base,
        limit=limit,
        min_score=min_score,
        family_filter=family_filter,
        top_n=top_n,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
