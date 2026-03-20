from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class MarketSnapshot:
    market_id: str
    slug: str
    question: str
    description: str
    end_date: str
    yes_price: float | None
    no_price: float | None
    volume: float
    liquidity: float
    token_ids: list[str]
    raw: dict[str, Any]


def fetch_active_markets(base_url: str, limit: int) -> list[MarketSnapshot]:
    query = urlencode({"limit": limit, "active": "true", "closed": "false"})
    url = f"{base_url.rstrip('/')}/markets?{query}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "polymarket-agent-mvp/0.1",
        },
    )
    with urlopen(request) as response:
        payload = json.load(response)
    return [parse_market(item) for item in payload]


def select_market(markets: list[MarketSnapshot], slug: str | None) -> MarketSnapshot:
    if not markets:
        raise RuntimeError("No active markets returned from Gamma.")

    if not slug:
        return markets[0]

    for market in markets:
        if market.slug == slug:
            return market

    raise RuntimeError(f"Market slug '{slug}' was not found in the fetched active markets.")


def parse_market(payload: dict[str, Any]) -> MarketSnapshot:
    outcome_prices = _parse_json_list(payload.get("outcomePrices"))
    token_ids = _parse_json_list(payload.get("clobTokenIds"))

    yes_price = _safe_float(outcome_prices[0]) if len(outcome_prices) > 0 else None
    no_price = _safe_float(outcome_prices[1]) if len(outcome_prices) > 1 else None

    return MarketSnapshot(
        market_id=str(payload.get("id", "")),
        slug=str(payload.get("slug", "")),
        question=str(payload.get("question", "")),
        description=str(payload.get("description", "")),
        end_date=str(payload.get("endDate", "")),
        yes_price=yes_price,
        no_price=no_price,
        volume=_safe_float(payload.get("volume")),
        liquidity=_safe_float(payload.get("liquidity")),
        token_ids=[str(item) for item in token_ids],
        raw=payload,
    )


def _parse_json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
