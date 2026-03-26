"""
Gap 1 + Gap 3: Authenticated CLOB Order Placement and Fill Polling

Reads 11-execution.json produced by the execution_microstructure agent,
constructs a signed limit or market order via py-clob-client, submits it
to the Polymarket CLOB, polls for fill status, and writes a fill receipt
to 11-execution-fill.json.

Required env vars when live_trading = true:
  POLYMARKET_PRIVATE_KEY      EVM private key (hex, 0x-prefixed or raw)
  POLYMARKET_CLOB_API_BASE    override if not using prod (default: https://clob.polymarket.com)

Optional env vars:
  POLYMARKET_CHAIN_ID         137 for Polygon mainnet (default), 80002 for Amoy testnet

The helper is a no-op (dry_run=True) when POLYMARKET_LIVE_TRADING != "true".
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Gap 3: How long to wait and how often to poll for fill status after submission
_FILL_POLL_INTERVAL_SECONDS = 5
_FILL_POLL_MAX_ATTEMPTS = 6  # 30 seconds total before giving up


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class OrderResult:
    """Outcome written to 11-execution-fill.json."""

    dry_run: bool
    execution_ready: bool
    submitted: bool
    order_id: str | None
    status: str  # "filled" | "open" | "cancelled" | "dry_run" | "error" | "rejected"
    side: str  # "YES" | "NO"
    token_id: str | None
    size: float
    price: float
    fill_price: float | None
    fill_size: float | None
    slippage_pct: float | None
    submitted_at: str | None
    error: str | None
    raw_response: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "execution_ready": self.execution_ready,
            "submitted": self.submitted,
            "order_id": self.order_id,
            "status": self.status,
            "side": self.side,
            "token_id": self.token_id,
            "size": self.size,
            "price": self.price,
            "fill_price": self.fill_price,
            "fill_size": self.fill_size,
            "slippage_pct": self.slippage_pct,
            "submitted_at": self.submitted_at,
            "error": self.error,
            "raw_response": self.raw_response,
        }


# ---------------------------------------------------------------------------
# Execution JSON parsing
# ---------------------------------------------------------------------------


def _parse_execution_json(execution_json_path: Path) -> dict[str, Any]:
    if not execution_json_path.exists():
        raise FileNotFoundError(f"Execution JSON not found: {execution_json_path}")
    raw = json.loads(execution_json_path.read_text(encoding="utf-8"))
    return raw


def _validate_execution_ready(raw: dict[str, Any]) -> None:
    ready = raw.get("execution_ready", "no")
    if str(ready).lower() not in {"yes", "true", "1"}:
        raise ValueError(
            f"execution_ready = {ready!r}. The execution agent did not approve this order. "
            "Resolve the execution plan before placing."
        )


def _extract_order_params(raw: dict[str, Any]) -> tuple[str, str, float, float]:
    """Return (side, token_id, size, price) from execution JSON."""
    order_plan = raw.get("order_plan", {})
    side = str(order_plan.get("side", raw.get("side", ""))).upper()
    if side not in {"YES", "NO"}:
        raise ValueError(f"Invalid order side: {side!r}. Must be YES or NO.")

    token_id = str(order_plan.get("token_id", raw.get("token_id", "")))
    if not token_id:
        raise ValueError("token_id is missing from execution JSON order_plan.")

    size = float(order_plan.get("size", raw.get("size", 0.0)))
    if size <= 0:
        raise ValueError(f"Order size must be positive, got {size}")

    price_or_band = order_plan.get("price_or_price_band", raw.get("price_or_price_band", {}))
    if isinstance(price_or_band, dict):
        price = float(price_or_band.get("limit", price_or_band.get("mid", 0.0)))
    else:
        price = float(price_or_band)
    if not (0.01 <= price <= 0.99):
        raise ValueError(f"Price {price} out of valid range [0.01, 0.99].")

    return side, token_id, size, price


# ---------------------------------------------------------------------------
# CLOB client wrapper
# ---------------------------------------------------------------------------


def _build_clob_client(clob_api_base: str, chain_id: int):  # type: ignore[return]
    """Build and return a py_clob_client.ClobClient. Raises ImportError if not installed."""
    try:
        from py_clob_client.client import ClobClient  # type: ignore[import]
        from py_clob_client.clob_types import ApiCreds  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "py-clob-client is not installed. Run: pip install py-clob-client\n"
            "See: https://github.com/Polymarket/py-clob-client"
        ) from exc

    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    if not private_key:
        raise EnvironmentError(
            "POLYMARKET_PRIVATE_KEY env var is required for live order placement. "
            "Set it to your EVM wallet private key (0x-prefixed hex)."
        )

    # Create an unauthenticated client first to derive credentials
    client = ClobClient(clob_api_base, key=private_key, chain_id=chain_id)
    # Derive API key from private key
    creds: ApiCreds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    return client


def _submit_limit_order(
    client: Any,
    token_id: str,
    price: float,
    size: float,
    side: str,
) -> dict[str, Any]:
    from py_clob_client.clob_types import OrderArgs, OrderType  # type: ignore[import]

    # Map YES/NO to BUY/SELL: buying YES shares = BUY; buying NO shares = SELL YES
    buy_side = "BUY" if side == "YES" else "SELL"
    order_args = OrderArgs(
        token_id=token_id,
        price=price,
        size=size,
        side=buy_side,
    )
    signed_order = client.create_limit_order(order_args)
    response = client.post_order(signed_order, OrderType.GTC)
    return response if isinstance(response, dict) else {"raw": str(response)}


def _poll_order_fill(client: Any, order_id: str) -> dict[str, Any]:
    """
    Gap 3: Poll the CLOB for fill status until the order is terminal or
    the max poll attempts are exhausted. Returns the final order state dict.

    Terminal statuses: filled, cancelled, expired.
    Non-terminal: open, matched, delayed.
    """
    logger.info(
        "Polling fill status for order %s (max %ds)", order_id, _FILL_POLL_MAX_ATTEMPTS * _FILL_POLL_INTERVAL_SECONDS
    )
    for attempt in range(_FILL_POLL_MAX_ATTEMPTS):
        try:
            order_state = client.get_order(order_id)
            state = order_state if isinstance(order_state, dict) else {}
            status = str(state.get("status", "")).lower()
            logger.debug(
                "Poll attempt %d/%d — order %s status: %s", attempt + 1, _FILL_POLL_MAX_ATTEMPTS, order_id, status
            )
            if status in {"filled", "cancelled", "expired"}:
                logger.info("Order %s reached terminal status: %s", order_id, status)
                return state
        except Exception as exc:
            logger.warning("Poll attempt %d failed for order %s: %s", attempt + 1, order_id, exc)
        if attempt < _FILL_POLL_MAX_ATTEMPTS - 1:
            time.sleep(_FILL_POLL_INTERVAL_SECONDS)

    logger.warning(
        "Poll exhausted for order %s after %d attempts — returning last known state", order_id, _FILL_POLL_MAX_ATTEMPTS
    )
    # Return last known state (may be open / partial)
    try:
        final_state = client.get_order(order_id)
        return final_state if isinstance(final_state, dict) else {}
    except Exception as exc:
        logger.error("Final state fetch failed for order %s: %s", order_id, exc)
        return {"order_id": order_id, "status": "unknown", "poll_exhausted": True}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def place_order(
    execution_json_path: Path,
    *,
    clob_api_base: str = "https://clob.polymarket.com",
    chain_id: int = 137,
    live_trading: bool = False,
) -> OrderResult:
    """
    Read execution JSON, validate it, and (if live_trading) submit to CLOB.

    Returns an OrderResult regardless of mode. In dry_run mode, status = "dry_run"
    and no network call is made. All outcomes are suitable for writing to
    11-execution-fill.json.
    """
    raw = _parse_execution_json(execution_json_path)

    execution_ready_flag = str(raw.get("execution_ready", "no")).lower() in {"yes", "true", "1"}

    if not execution_ready_flag:
        return OrderResult(
            dry_run=not live_trading,
            execution_ready=False,
            submitted=False,
            order_id=None,
            status="rejected",
            side="",
            token_id=None,
            size=0.0,
            price=0.0,
            fill_price=None,
            fill_size=None,
            slippage_pct=None,
            submitted_at=None,
            error="execution_ready is not 'yes' — order rejected before submission.",
        )

    try:
        side, token_id, size, price = _extract_order_params(raw)
    except (ValueError, KeyError) as exc:
        return OrderResult(
            dry_run=not live_trading,
            execution_ready=True,
            submitted=False,
            order_id=None,
            status="error",
            side="",
            token_id=None,
            size=0.0,
            price=0.0,
            fill_price=None,
            fill_size=None,
            slippage_pct=None,
            submitted_at=None,
            error=f"Failed to parse order params: {exc}",
        )

    if not live_trading:
        logger.info(
            "DRY-RUN: would place %s order — side=%s token=%s size=%.4f price=%.4f",
            "limit",
            side,
            token_id,
            size,
            price,
        )
        return OrderResult(
            dry_run=True,
            execution_ready=True,
            submitted=False,
            order_id=None,
            status="dry_run",
            side=side,
            token_id=token_id,
            size=size,
            price=price,
            fill_price=None,
            fill_size=None,
            slippage_pct=None,
            submitted_at=None,
            error=None,
        )

    # Live trading path
    logger.info(
        "LIVE: submitting %s order — side=%s token=%s size=%.4f price=%.4f", "limit", side, token_id, size, price
    )
    try:
        client = _build_clob_client(clob_api_base, chain_id)
        submitted_at = datetime.now(timezone.utc).isoformat()
        response = _submit_limit_order(client, token_id, price, size, side)
    except (ImportError, EnvironmentError, Exception) as exc:
        logger.error("Order submission failed: %s", exc)
        return OrderResult(
            dry_run=False,
            execution_ready=True,
            submitted=False,
            order_id=None,
            status="error",
            side=side,
            token_id=token_id,
            size=size,
            price=price,
            fill_price=None,
            fill_size=None,
            slippage_pct=None,
            submitted_at=None,
            error=str(exc),
        )

    order_id = response.get("orderID") or response.get("order_id") or response.get("id")

    # Gap 3: Poll for fill status if we have an order ID
    if order_id:
        fill_state = _poll_order_fill(client, order_id)
        response = {**response, **fill_state}

    status = response.get("status", "open")

    # Compute slippage if fill price is available
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
        "Order result — id=%s status=%s fill_price=%s slippage_pct=%s",
        order_id,
        status,
        fill_price,
        slippage_pct,
    )
    return OrderResult(
        dry_run=False,
        execution_ready=True,
        submitted=True,
        order_id=order_id,
        status=status,
        side=side,
        token_id=token_id,
        size=size,
        price=price,
        fill_price=fill_price,
        fill_size=fill_size,
        slippage_pct=slippage_pct,
        submitted_at=submitted_at,
        error=None,
        raw_response=response,
    )


def write_order_result(result: OrderResult, output_path: Path) -> dict[str, Any]:
    """Write the order result to 11-execution-fill.json and return the dict."""
    payload = result.as_dict()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.debug("Order result written to %s", output_path)
    return payload
