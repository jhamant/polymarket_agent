"""
Gap 4: Structural Alpha Multi-Leg Execution

Reads the structural alpha plan from 06-structural-alpha.json (produced by
the structural_alpha agent) and executes each leg sequentially via py-clob-client.

Each leg is submitted and polled independently. If a leg fails after the first
leg fills, the system records the partial state and surfaces it for human review
rather than attempting to unwind automatically (safe default).

CLI: python3 main.py execute-multi-leg --structural-alpha-json <path> --output <path>

Required env vars when live_trading = true:
  POLYMARKET_PRIVATE_KEY      EVM private key
  POLYMARKET_CLOB_API_BASE    override if not using prod

Output: JSON with per-leg fill results and aggregate summary.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .place_order import _build_clob_client, _poll_order_fill, _submit_limit_order

logger = logging.getLogger(__name__)


@dataclass
class LegResult:
    """Result for a single leg of a multi-leg structural alpha trade."""

    leg_index: int
    market_slug: str
    token_id: str | None
    side: str
    size: float
    planned_price: float
    submitted: bool
    order_id: str | None
    status: str  # filled | open | cancelled | error | dry_run | skipped
    fill_price: float | None
    fill_size: float | None
    slippage_pct: float | None
    submitted_at: str | None
    error: str | None
    raw_response: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "leg_index": self.leg_index,
            "market_slug": self.market_slug,
            "token_id": self.token_id,
            "side": self.side,
            "size": self.size,
            "planned_price": self.planned_price,
            "submitted": self.submitted,
            "order_id": self.order_id,
            "status": self.status,
            "fill_price": self.fill_price,
            "fill_size": self.fill_size,
            "slippage_pct": self.slippage_pct,
            "submitted_at": self.submitted_at,
            "error": self.error,
        }


@dataclass
class MultiLegResult:
    """Aggregate result for all legs of a multi-leg execution."""

    dry_run: bool
    total_legs: int
    legs_submitted: int
    legs_filled: int
    legs_failed: int
    legs_skipped: int
    partial: bool  # True if some but not all legs filled
    status: str  # completed | partial | failed | dry_run
    legs: list[LegResult] = field(default_factory=list)
    error: str | None = None
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at_utc": self.generated_at,
            "dry_run": self.dry_run,
            "total_legs": self.total_legs,
            "legs_submitted": self.legs_submitted,
            "legs_filled": self.legs_filled,
            "legs_failed": self.legs_failed,
            "legs_skipped": self.legs_skipped,
            "partial": self.partial,
            "status": self.status,
            "error": self.error,
            "legs": [leg.as_dict() for leg in self.legs],
        }


# ---------------------------------------------------------------------------
# Parsing structural alpha JSON
# ---------------------------------------------------------------------------


def _parse_structural_alpha_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Structural alpha JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_legs(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract leg definitions from structural alpha JSON.
    Expected schema:
    {
      "alpha_type": "correlated_spread" | "arb" | ...,
      "legs": [
        {
          "market_slug": str,
          "token_id": str,
          "side": "YES" | "NO",
          "size": float,
          "price": float,
        },
        ...
      ]
    }
    """
    legs = raw.get("legs", [])
    if not legs:
        raise ValueError("No legs found in structural alpha JSON. Cannot execute multi-leg trade.")
    return legs


# ---------------------------------------------------------------------------
# Single-leg execution (wraps place_order internals)
# ---------------------------------------------------------------------------


def _execute_leg(
    client: Any,
    leg: dict[str, Any],
    leg_index: int,
    *,
    dry_run: bool,
) -> LegResult:
    market_slug = leg.get("market_slug", f"leg_{leg_index}")
    token_id = leg.get("token_id", "")
    side = str(leg.get("side", "YES")).upper()
    size = float(leg.get("size", 0.0))
    price = float(leg.get("price", 0.0))

    if dry_run:
        # In dry-run mode return a preview result without validating token_id
        if not token_id:
            logger.warning("DRY-RUN leg %d (%s): token_id missing (would fail live)", leg_index, market_slug)
        else:
            logger.info("DRY-RUN leg %d (%s): side=%s size=%.4f price=%.4f", leg_index, market_slug, side, size, price)
        return LegResult(
            leg_index=leg_index,
            market_slug=market_slug,
            token_id=token_id or None,
            side=side,
            size=size,
            planned_price=price,
            submitted=False,
            order_id=None,
            status="dry_run",
            fill_price=None,
            fill_size=None,
            slippage_pct=None,
            submitted_at=None,
            error=None,
        )

    if not token_id:
        logger.error("Leg %d (%s): token_id missing — cannot execute", leg_index, market_slug)
        return LegResult(
            leg_index=leg_index,
            market_slug=market_slug,
            token_id=None,
            side=side,
            size=size,
            planned_price=price,
            submitted=False,
            order_id=None,
            status="error",
            fill_price=None,
            fill_size=None,
            slippage_pct=None,
            submitted_at=None,
            error="token_id missing from leg definition.",
        )

    logger.info("LIVE leg %d (%s): side=%s size=%.4f price=%.4f", leg_index, market_slug, side, size, price)
    try:
        submitted_at = datetime.now(timezone.utc).isoformat()
        response = _submit_limit_order(client, token_id, price, size, side)
        order_id = response.get("orderID") or response.get("order_id") or response.get("id")

        if order_id:
            fill_state = _poll_order_fill(client, order_id)
            response = {**response, **fill_state}

        status = response.get("status", "open")
        fill_price: float | None = None
        fill_size: float | None = None
        slippage_pct: float | None = None
        for fill_key in ("fillPrice", "avg_price", "avgPrice"):
            if fill_key in response:
                fill_price = float(response[fill_key])
                break
        for size_key in ("fillSize", "size_matched", "sizeMatched"):
            if size_key in response:
                fill_size = float(response[size_key])
                break
        if fill_price is not None:
            slippage_pct = round(abs(fill_price - price) / price * 100, 4)

        logger.info(
            "Leg %d (%s) result — order_id=%s status=%s fill_price=%s slippage=%s%%",
            leg_index,
            market_slug,
            order_id,
            status,
            fill_price,
            slippage_pct,
        )
        return LegResult(
            leg_index=leg_index,
            market_slug=market_slug,
            token_id=token_id,
            side=side,
            size=size,
            planned_price=price,
            submitted=True,
            order_id=order_id,
            status=status,
            fill_price=fill_price,
            fill_size=fill_size,
            slippage_pct=slippage_pct,
            submitted_at=submitted_at,
            error=None,
            raw_response=response,
        )
    except Exception as exc:
        logger.error("Leg %d (%s) failed: %s", leg_index, market_slug, exc)
        return LegResult(
            leg_index=leg_index,
            market_slug=market_slug,
            token_id=token_id,
            side=side,
            size=size,
            planned_price=price,
            submitted=False,
            order_id=None,
            status="error",
            fill_price=None,
            fill_size=None,
            slippage_pct=None,
            submitted_at=None,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def execute_multi_leg(
    structural_alpha_json_path: Path,
    *,
    clob_api_base: str = "https://clob.polymarket.com",
    chain_id: int = 137,
    live_trading: bool = False,
) -> MultiLegResult:
    """
    Read the structural alpha plan and execute each leg sequentially.

    Safe-stop on first failure: if a leg fails after a previous leg has already
    filled, records partial state and returns immediately for human review.
    No automatic unwinding is attempted.
    """
    try:
        raw = _parse_structural_alpha_json(structural_alpha_json_path)
        legs_def = _extract_legs(raw)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return MultiLegResult(
            dry_run=not live_trading,
            total_legs=0,
            legs_submitted=0,
            legs_filled=0,
            legs_failed=0,
            legs_skipped=0,
            partial=False,
            status="failed",
            error=str(exc),
        )

    total_legs = len(legs_def)

    if not live_trading:
        leg_results = [_execute_leg(None, leg, i, dry_run=True) for i, leg in enumerate(legs_def)]
        return MultiLegResult(
            dry_run=True,
            total_legs=total_legs,
            legs_submitted=0,
            legs_filled=0,
            legs_failed=0,
            legs_skipped=0,
            partial=False,
            status="dry_run",
            legs=leg_results,
        )

    # Live execution
    try:
        client = _build_clob_client(clob_api_base, chain_id)
    except (ImportError, EnvironmentError) as exc:
        return MultiLegResult(
            dry_run=False,
            total_legs=total_legs,
            legs_submitted=0,
            legs_filled=0,
            legs_failed=0,
            legs_skipped=0,
            partial=False,
            status="failed",
            error=str(exc),
        )

    leg_results: list[LegResult] = []
    legs_submitted = 0
    legs_filled = 0
    legs_failed = 0
    any_filled = False

    for i, leg in enumerate(legs_def):
        result = _execute_leg(client, leg, i, dry_run=False)
        leg_results.append(result)

        if result.submitted:
            legs_submitted += 1
        if result.status == "filled":
            legs_filled += 1
            any_filled = True
        elif result.status == "error":
            legs_failed += 1
            # Safe-stop: do not attempt remaining legs after failure
            logger.warning("Leg %d failed — triggering safe-stop; skipping remaining %d legs", i, total_legs - i - 1)
            remaining = total_legs - i - 1
            for j in range(remaining):
                skipped_leg = legs_def[i + 1 + j]
                leg_results.append(
                    LegResult(
                        leg_index=i + 1 + j,
                        market_slug=skipped_leg.get("market_slug", f"leg_{i + 1 + j}"),
                        token_id=skipped_leg.get("token_id"),
                        side=str(skipped_leg.get("side", "YES")).upper(),
                        size=float(skipped_leg.get("size", 0.0)),
                        planned_price=float(skipped_leg.get("price", 0.0)),
                        submitted=False,
                        order_id=None,
                        status="skipped",
                        fill_price=None,
                        fill_size=None,
                        slippage_pct=None,
                        submitted_at=None,
                        error="Skipped due to prior leg failure (safe-stop).",
                    )
                )
            break

    legs_skipped = sum(1 for r in leg_results if r.status == "skipped")
    partial = any_filled and (legs_filled < total_legs or legs_failed > 0)

    if legs_failed > 0:
        status = "partial" if partial else "failed"
    elif legs_filled == total_legs:
        status = "completed"
    elif legs_submitted == total_legs:
        status = "open"  # all submitted, not yet filled
    else:
        status = "partial"

    logger.info(
        "Multi-leg execution complete — status=%s legs=%d submitted=%d filled=%d failed=%d skipped=%d partial=%s",
        status,
        total_legs,
        legs_submitted,
        legs_filled,
        legs_failed,
        legs_skipped,
        partial,
    )
    return MultiLegResult(
        dry_run=False,
        total_legs=total_legs,
        legs_submitted=legs_submitted,
        legs_filled=legs_filled,
        legs_failed=legs_failed,
        legs_skipped=legs_skipped,
        partial=partial,
        status=status,
        legs=leg_results,
    )


def write_multi_leg_result(result: MultiLegResult, output_path: Path) -> dict[str, Any]:
    """Write the multi-leg execution result to JSON and return the dict."""
    payload = result.as_dict()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.debug("Multi-leg result written to %s", output_path)
    return payload
