from __future__ import annotations

from datetime import datetime, timezone
from itertools import zip_longest
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .market_data import MarketSnapshot


def write_market_context_files(
    *,
    market: MarketSnapshot,
    gamma_api_base: str,
    clob_api_base: str,
    market_contexts_dir: Path,
    history_path: Path,
    timestamp: str,
    run_output_path: Path,
) -> dict[str, Any]:
    context_document = build_market_context_document(
        market=market,
        gamma_api_base=gamma_api_base,
        clob_api_base=clob_api_base,
        history_path=history_path,
        timestamp=timestamp,
    )
    shared_snapshot_path, latest_path = write_shared_market_context_artifact(
        market_contexts_dir=market_contexts_dir,
        market_slug=market.slug,
        timestamp=timestamp,
        document=context_document,
    )
    run_output_path.parent.mkdir(parents=True, exist_ok=True)
    context_document["shared_reference_files"]["market_context_reference"] = str(latest_path)
    context_document["shared_reference_files"]["market_context_snapshot_reference"] = str(
        shared_snapshot_path
    )
    context_document["shared_reference_files"]["run_market_context_reference"] = str(run_output_path)
    payload = json.dumps(context_document, indent=2, sort_keys=True)
    run_output_path.write_text(payload, encoding="utf-8")
    return context_document


def build_market_context_document(
    *,
    market: MarketSnapshot,
    gamma_api_base: str,
    clob_api_base: str,
    history_path: Path,
    timestamp: str,
) -> dict[str, Any]:
    generated_at = _timestamp_to_iso(timestamp)
    event = _extract_primary_event(market.raw)
    outcomes = _parse_json_list(market.raw.get("outcomes"))
    outcome_prices = _parse_json_list(market.raw.get("outcomePrices"))
    token_ids = _parse_json_list(market.raw.get("clobTokenIds"))

    tokens = []
    source_urls = [
        f"{gamma_api_base.rstrip('/')}/markets?{urlencode({'limit': 1, 'active': 'true', 'closed': 'false'})}",
    ]
    for index, (outcome, token_id, listed_price) in enumerate(
        zip_longest(outcomes, token_ids, outcome_prices, fillvalue=None)
    ):
        token_id_str = str(token_id) if token_id is not None else ""
        book_url = ""
        history_url = ""
        book_payload: dict[str, Any] = {}
        history_payload: dict[str, Any] = {}
        if token_id_str:
            book_url = f"{clob_api_base.rstrip('/')}/book?token_id={token_id_str}"
            history_url = (
                f"{clob_api_base.rstrip('/')}/prices-history?"
                f"{urlencode({'market': token_id_str, 'interval': '1d', 'fidelity': 60})}"
            )
            source_urls.extend([book_url, history_url])
            book_payload = _safe_fetch_json(book_url)
            history_payload = _safe_fetch_json(history_url)

        tokens.append(
            {
                "index": index,
                "outcome": str(outcome) if outcome is not None else f"Outcome {index + 1}",
                "token_id": token_id_str,
                "listed_price": _safe_float(listed_price),
                "order_book": summarize_order_book(book_payload),
                "price_history_1d": summarize_price_history(history_payload),
            }
        )

    context_digest = {
        "slug": market.slug,
        "question": market.question,
        "end_date": market.end_date,
        "days_to_expiry": _days_to_expiry(market.end_date, generated_at),
        "accepting_orders": bool(market.raw.get("acceptingOrders")),
        "outcomes": [
            {
                "outcome": token["outcome"],
                "listed_price": token["listed_price"],
                "best_bid": token["order_book"]["best_bid"],
                "best_ask": token["order_book"]["best_ask"],
                "mid_price": token["order_book"]["mid_price"],
                "history_latest_price": token["price_history_1d"]["latest_price"],
            }
            for token in tokens
        ],
        "liquidity": market.liquidity,
        "volume": market.volume,
        "volume24hr": _safe_float(market.raw.get("volume24hr")),
        "one_day_price_change": _safe_float(market.raw.get("oneDayPriceChange")),
        "event_title": event.get("title"),
    }

    return {
        "generated_at": generated_at,
        "market": {
            "id": market.market_id,
            "slug": market.slug,
            "question": market.question,
            "description": market.description,
            "end_date": market.end_date,
            "active": bool(market.raw.get("active")),
            "closed": bool(market.raw.get("closed")),
            "restricted": bool(market.raw.get("restricted")),
            "accepting_orders": bool(market.raw.get("acceptingOrders")),
            "order_min_size": _safe_float(market.raw.get("orderMinSize")),
            "tick_size": _safe_float(market.raw.get("orderPriceMinTickSize")),
            "yes_price": market.yes_price,
            "no_price": market.no_price,
            "best_bid": _safe_float(market.raw.get("bestBid")),
            "best_ask": _safe_float(market.raw.get("bestAsk")),
            "spread": _safe_float(market.raw.get("spread")),
            "volume": market.volume,
            "volume24hr": _safe_float(market.raw.get("volume24hr")),
            "volume1wk": _safe_float(market.raw.get("volume1wk")),
            "volume1mo": _safe_float(market.raw.get("volume1mo")),
            "liquidity": market.liquidity,
            "updated_at": str(market.raw.get("updatedAt", "")),
            "outcomes": outcomes,
        },
        "event": event,
        "tokens": tokens,
        "context_digest": context_digest,
        "shared_reference_files": {
            "decision_history_reference": str(history_path),
        },
        "sources_used": sorted(set(url for url in source_urls if url)),
    }


def write_shared_market_context_artifact(
    *,
    market_contexts_dir: Path,
    market_slug: str,
    timestamp: str,
    document: dict[str, Any],
) -> tuple[Path, Path]:
    slug_dir = market_contexts_dir / market_slug
    slug_dir.mkdir(parents=True, exist_ok=True)
    timestamped_path = slug_dir / f"{timestamp}.json"
    latest_path = slug_dir / "latest.json"
    payload = json.dumps(document, indent=2, sort_keys=True)
    timestamped_path.write_text(payload, encoding="utf-8")
    latest_path.write_text(payload, encoding="utf-8")
    return timestamped_path, latest_path


def summarize_order_book(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("error"):
        return {
            "error": payload["error"],
            "best_bid": None,
            "best_ask": None,
            "mid_price": None,
            "spread": None,
            "bid_levels": [],
            "ask_levels": [],
            "bid_levels_count": 0,
            "ask_levels_count": 0,
            "top5_bid_size": 0.0,
            "top5_ask_size": 0.0,
        }

    bids = _parse_levels(payload.get("bids"))
    asks = _parse_levels(payload.get("asks"))
    sorted_bids = sorted(bids, key=lambda level: level["price"], reverse=True)
    sorted_asks = sorted(asks, key=lambda level: level["price"])

    best_bid = sorted_bids[0]["price"] if sorted_bids else None
    best_ask = sorted_asks[0]["price"] if sorted_asks else None
    mid_price = (
        round((best_bid + best_ask) / 2, 6)
        if best_bid is not None and best_ask is not None
        else None
    )
    spread = round(best_ask - best_bid, 6) if best_bid is not None and best_ask is not None else None

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid_price": mid_price,
        "spread": spread,
        "raw_last_trade_price": _safe_float(payload.get("last_trade_price")),
        "min_order_size": _safe_float(payload.get("min_order_size")),
        "tick_size": _safe_float(payload.get("tick_size")),
        "bid_levels": sorted_bids[:5],
        "ask_levels": sorted_asks[:5],
        "bid_levels_count": len(sorted_bids),
        "ask_levels_count": len(sorted_asks),
        "top5_bid_size": round(sum(level["size"] for level in sorted_bids[:5]), 6),
        "top5_ask_size": round(sum(level["size"] for level in sorted_asks[:5]), 6),
        "book_timestamp": str(payload.get("timestamp", "")),
    }


def summarize_price_history(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("error"):
        return {
            "error": payload["error"],
            "points": 0,
            "first_price": None,
            "latest_price": None,
            "min_price": None,
            "max_price": None,
            "change": None,
            "recent_points": [],
        }

    history = payload.get("history") or []
    points = [
        {"t": int(point["t"]), "p": _safe_float(point["p"])}
        for point in history
        if "t" in point and "p" in point
    ]
    if not points:
        return {
            "error": "No history points returned.",
            "points": 0,
            "first_price": None,
            "latest_price": None,
            "min_price": None,
            "max_price": None,
            "change": None,
            "recent_points": [],
        }

    prices = [point["p"] for point in points]
    first_price = points[0]["p"]
    latest_price = points[-1]["p"]
    return {
        "points": len(points),
        "first_price": first_price,
        "latest_price": latest_price,
        "min_price": min(prices),
        "max_price": max(prices),
        "change": round(latest_price - first_price, 6),
        "recent_points": points[-10:],
    }


def _parse_levels(levels: Any) -> list[dict[str, float]]:
    result = []
    for level in levels or []:
        result.append(
            {
                "price": _safe_float(level.get("price")),
                "size": _safe_float(level.get("size")),
            }
        )
    return result


def _extract_primary_event(payload: dict[str, Any]) -> dict[str, Any]:
    events = payload.get("events") or []
    if not events:
        return {}
    event = events[0]
    return {
        "id": str(event.get("id", "")),
        "slug": str(event.get("slug", "")),
        "title": str(event.get("title", "")),
        "description": str(event.get("description", "")),
        "resolution_source": str(event.get("resolutionSource", "")),
        "start_date": str(event.get("startDate", "")),
        "end_date": str(event.get("endDate", "")),
        "liquidity": _safe_float(event.get("liquidity")),
        "volume": _safe_float(event.get("volume")),
    }


def _safe_fetch_json(url: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "polymarket-agent-mvp/0.1",
        },
    )
    try:
        with urlopen(request) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"error": str(exc), "url": url}


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


def _timestamp_to_iso(timestamp: str) -> str:
    dt = datetime.strptime(timestamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _days_to_expiry(end_date: str, generated_at_iso: str) -> float | None:
    if not end_date:
        return None
    try:
        end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        generated_dt = datetime.fromisoformat(generated_at_iso)
    except ValueError:
        return None
    delta = end_dt - generated_dt
    return round(delta.total_seconds() / 86400, 4)
