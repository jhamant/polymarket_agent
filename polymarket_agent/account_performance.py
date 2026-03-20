from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import statistics
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zipfile

from .family_classifier import build_text_haystack, classify_market_family


POSITION_FIELDNAMES = [
    "status",
    "family_key",
    "family_label",
    "proxy_wallet",
    "condition_id",
    "asset",
    "title",
    "slug",
    "event_slug",
    "outcome",
    "outcome_index",
    "opposite_outcome",
    "end_date",
    "negative_risk",
    "first_trade_at_utc",
    "last_trade_at_utc",
    "closed_at_utc",
    "days_held",
    "trade_count",
    "buy_trade_count",
    "sell_trade_count",
    "gross_buy_tokens",
    "gross_sell_tokens",
    "gross_buy_notional_usdc",
    "gross_sell_notional_usdc",
    "avg_buy_price",
    "avg_sell_price",
    "net_trade_cash_flow_usdc",
    "current_size",
    "avg_price_open",
    "current_price",
    "initial_value_open",
    "current_value_open",
    "open_mark_to_market_pnl_est",
    "reported_cash_pnl",
    "reported_percent_pnl",
    "reported_realized_pnl",
    "reported_percent_realized_pnl",
    "estimated_total_pnl",
    "source",
]


TRADE_LEDGER_FIELDNAMES = [
    "timestamp_utc",
    "proxy_wallet",
    "condition_id",
    "asset",
    "side",
    "outcome",
    "title",
    "slug",
    "event_slug",
    "family_key",
    "family_label",
    "size_tokens",
    "price",
    "notional_usdc",
    "signed_token_delta",
    "signed_cash_flow_usdc",
    "cumulative_net_tokens_for_asset",
    "cumulative_net_cash_flow_for_asset",
    "transaction_hash",
]


EXTRA_PERFORMANCE_FAMILIES = (
    (
        "crypto",
        "Crypto and on-chain markets",
        ("btc", "bitcoin", "eth", "ethereum", "solana", "crypto", "token", "airdrop"),
    ),
    (
        "politics",
        "Politics and elections",
        (
            "election",
            "president",
            "presidential",
            "nomination",
            "senate",
            "governor",
            "prime minister",
            "trump",
            "newsom",
            "kamala",
            "vance",
        ),
    ),
    (
        "tech_corporate",
        "Tech, corporate, and launch markets",
        ("openai", "launch", "hardware", "product", "release", "gta", "album"),
    ),
)


def update_account_performance(
    *,
    gamma_api_base: str,
    data_api_base: str,
    performance_dir: Path,
    account_address: str | None,
    max_pages: int,
    timestamp: str,
    run_output_path: Path,
) -> dict[str, Any]:
    if not account_address:
        return _write_placeholder_performance(
            performance_dir=performance_dir,
            account_key="unconfigured",
            timestamp=timestamp,
            reason="POLYMARKET_ACCOUNT_ADDRESS is not configured.",
            run_output_path=run_output_path,
        )

    profile = _resolve_profile(gamma_api_base, account_address)
    proxy_wallet = str(profile.get("proxyWallet") or account_address).lower()
    account_key = proxy_wallet

    trades = _fetch_paginated_list(
        base_url=data_api_base,
        endpoint="/trades",
        params={"user": proxy_wallet, "limit": 1000, "offset": 0, "takerOnly": "false"},
        max_pages=max_pages,
    )
    positions = _fetch_paginated_list(
        base_url=data_api_base,
        endpoint="/positions",
        params={
            "user": proxy_wallet,
            "limit": 500,
            "offset": 0,
            "sortBy": "TITLE",
            "sortDirection": "ASC",
        },
        max_pages=max_pages,
    )
    closed_positions = _fetch_paginated_list(
        base_url=data_api_base,
        endpoint="/closed-positions",
        params={
            "user": proxy_wallet,
            "limit": 500,
            "offset": 0,
            "sortDirection": "DESC",
        },
        max_pages=max_pages,
    )
    position_value = _safe_fetch_json(
        f"{data_api_base.rstrip('/')}/value?{urlencode({'user': proxy_wallet})}"
    )
    traded_summary = _safe_fetch_json(
        f"{data_api_base.rstrip('/')}/traded?{urlencode({'user': proxy_wallet})}"
    )
    accounting_snapshot = _download_accounting_snapshot(data_api_base, proxy_wallet)

    trade_ledger_rows, trade_rollups = _build_trade_ledger_rows(trades)
    position_rows = _build_position_rows(
        proxy_wallet=proxy_wallet,
        trade_rollups=trade_rollups,
        positions=positions,
        closed_positions=closed_positions,
    )
    family_rollup = _build_family_rollup(position_rows)
    summary_metrics = _build_summary_metrics(
        proxy_wallet=proxy_wallet,
        input_account_address=account_address,
        profile=profile,
        trades=trades,
        positions=positions,
        closed_positions=closed_positions,
        position_rows=position_rows,
        family_rollup=family_rollup,
        position_value=position_value,
        traded_summary=traded_summary,
        accounting_snapshot=accounting_snapshot,
    )

    account_dir = performance_dir / account_key
    account_dir.mkdir(parents=True, exist_ok=True)
    position_csv_path = account_dir / "account_position_performance.csv"
    trade_ledger_csv_path = account_dir / "account_trade_ledger.csv"
    latest_summary_path = account_dir / "latest_summary.json"
    snapshot_dir = account_dir / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_summary_path = snapshot_dir / f"{timestamp}-summary.json"

    _write_csv(position_csv_path, POSITION_FIELDNAMES, position_rows)
    _write_csv(trade_ledger_csv_path, TRADE_LEDGER_FIELDNAMES, trade_ledger_rows)

    summary_document = {
        "generated_at": _timestamp_to_iso(timestamp),
        "input_account_address": account_address,
        "resolved_proxy_wallet": proxy_wallet,
        "profile": {
            "name": profile.get("name"),
            "pseudonym": profile.get("pseudonym"),
            "display_username_public": profile.get("displayUsernamePublic"),
        },
        "summary_metrics": summary_metrics,
        "family_rollup": family_rollup,
        "reference_files": {
            "performance_position_csv_reference": str(position_csv_path),
            "performance_trade_ledger_csv_reference": str(trade_ledger_csv_path),
            "performance_summary_reference": str(latest_summary_path),
            "performance_snapshot_reference": str(snapshot_summary_path),
            "run_performance_summary_reference": str(run_output_path),
        },
        "source_counts": {
            "trades": len(trades),
            "positions": len(positions),
            "closed_positions": len(closed_positions),
        },
    }
    payload = json.dumps(summary_document, indent=2, sort_keys=True)
    latest_summary_path.write_text(payload, encoding="utf-8")
    snapshot_summary_path.write_text(payload, encoding="utf-8")
    run_output_path.parent.mkdir(parents=True, exist_ok=True)
    run_output_path.write_text(payload, encoding="utf-8")
    return summary_document


def _write_placeholder_performance(
    *,
    performance_dir: Path,
    account_key: str,
    timestamp: str,
    reason: str,
    run_output_path: Path,
) -> dict[str, Any]:
    account_dir = performance_dir / account_key
    account_dir.mkdir(parents=True, exist_ok=True)
    position_csv_path = account_dir / "account_position_performance.csv"
    trade_ledger_csv_path = account_dir / "account_trade_ledger.csv"
    latest_summary_path = account_dir / "latest_summary.json"
    snapshot_dir = account_dir / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_summary_path = snapshot_dir / f"{timestamp}-summary.json"

    _write_csv(position_csv_path, POSITION_FIELDNAMES, [])
    _write_csv(trade_ledger_csv_path, TRADE_LEDGER_FIELDNAMES, [])

    summary_document = {
        "generated_at": _timestamp_to_iso(timestamp),
        "summary_metrics": {
            "configured": False,
            "reason": reason,
            "trade_count": 0,
            "open_positions_count": 0,
            "closed_positions_count": 0,
            "estimated_total_pnl": 0.0,
        },
        "family_rollup": [],
        "reference_files": {
            "performance_position_csv_reference": str(position_csv_path),
            "performance_trade_ledger_csv_reference": str(trade_ledger_csv_path),
            "performance_summary_reference": str(latest_summary_path),
            "performance_snapshot_reference": str(snapshot_summary_path),
            "run_performance_summary_reference": str(run_output_path),
        },
    }
    payload = json.dumps(summary_document, indent=2, sort_keys=True)
    latest_summary_path.write_text(payload, encoding="utf-8")
    snapshot_summary_path.write_text(payload, encoding="utf-8")
    run_output_path.parent.mkdir(parents=True, exist_ok=True)
    run_output_path.write_text(payload, encoding="utf-8")
    return summary_document


def _resolve_profile(gamma_api_base: str, address: str) -> dict[str, Any]:
    url = f"{gamma_api_base.rstrip('/')}/public-profile?{urlencode({'address': address})}"
    payload = _safe_fetch_json(url)
    if payload.get("error"):
        return {}
    return payload


def _fetch_paginated_list(
    *,
    base_url: str,
    endpoint: str,
    params: dict[str, Any],
    max_pages: int,
) -> list[dict[str, Any]]:
    limit = int(params.get("limit", 100))
    offset = int(params.get("offset", 0))
    all_rows: list[dict[str, Any]] = []

    for _ in range(max_pages):
        page_params = dict(params)
        page_params["offset"] = offset
        url = f"{base_url.rstrip('/')}{endpoint}?{urlencode(page_params)}"
        payload = _safe_fetch_json(url)
        if isinstance(payload, dict) and payload.get("error"):
            break
        if not isinstance(payload, list):
            break
        all_rows.extend(payload)
        if len(payload) < limit:
            break
        offset += limit
    return all_rows


def _download_accounting_snapshot(data_api_base: str, proxy_wallet: str) -> dict[str, Any]:
    url = f"{data_api_base.rstrip('/')}/v1/accounting/snapshot?{urlencode({'user': proxy_wallet})}"
    request = Request(
        url,
        headers={
            "Accept": "application/zip",
            "User-Agent": "polymarket-agent-mvp/0.1",
        },
    )
    try:
        with urlopen(request) as response:
            payload = response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        return {"error": str(exc)}

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        return {"error": str(exc)}

    parsed: dict[str, Any] = {"positions": [], "equity": []}
    for name in archive.namelist():
        with archive.open(name) as handle:
            rows = list(csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8")))
        if name == "positions.csv":
            parsed["positions"] = rows
        elif name == "equity.csv":
            parsed["equity"] = rows
    return parsed


def _build_trade_ledger_rows(
    trades: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    sorted_trades = sorted(trades, key=lambda item: int(item.get("timestamp", 0)))
    running_by_asset: dict[tuple[str, str], dict[str, float]] = {}
    rollups: dict[tuple[str, str], dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []

    for trade in sorted_trades:
        condition_id = str(trade.get("conditionId", ""))
        asset = str(trade.get("asset", ""))
        key = (condition_id, asset)
        side = str(trade.get("side", "")).upper()
        size_tokens = _safe_float(trade.get("size"))
        price = _safe_float(trade.get("price"))
        notional_usdc = round(size_tokens * price, 6)
        signed_token_delta = size_tokens if side == "BUY" else -size_tokens
        signed_cash_flow = -notional_usdc if side == "BUY" else notional_usdc
        family_key, family_label = _classify_performance_family(
            str(trade.get("title", "")),
            str(trade.get("slug", "")),
            str(trade.get("eventSlug", "")),
            str(trade.get("outcome", "")),
        )

        state = running_by_asset.setdefault(key, {"tokens": 0.0, "cash_flow": 0.0})
        state["tokens"] += signed_token_delta
        state["cash_flow"] += signed_cash_flow

        row = {
            "timestamp_utc": _timestamp_to_iso_from_unix(trade.get("timestamp")),
            "proxy_wallet": str(trade.get("proxyWallet", "")),
            "condition_id": condition_id,
            "asset": asset,
            "side": side,
            "outcome": str(trade.get("outcome", "")),
            "title": str(trade.get("title", "")),
            "slug": str(trade.get("slug", "")),
            "event_slug": str(trade.get("eventSlug", "")),
            "family_key": family_key,
            "family_label": family_label,
            "size_tokens": size_tokens,
            "price": price,
            "notional_usdc": notional_usdc,
            "signed_token_delta": signed_token_delta,
            "signed_cash_flow_usdc": round(signed_cash_flow, 6),
            "cumulative_net_tokens_for_asset": round(state["tokens"], 6),
            "cumulative_net_cash_flow_for_asset": round(state["cash_flow"], 6),
            "transaction_hash": str(trade.get("transactionHash", "")),
        }
        rows.append(row)

        aggregate = rollups.setdefault(
            key,
            {
                "proxy_wallet": str(trade.get("proxyWallet", "")),
                "condition_id": condition_id,
                "asset": asset,
                "title": str(trade.get("title", "")),
                "slug": str(trade.get("slug", "")),
                "event_slug": str(trade.get("eventSlug", "")),
                "outcome": str(trade.get("outcome", "")),
                "outcome_index": trade.get("outcomeIndex"),
                "family_key": family_key,
                "family_label": family_label,
                "first_trade_ts": int(trade.get("timestamp", 0)),
                "last_trade_ts": int(trade.get("timestamp", 0)),
                "trade_count": 0,
                "buy_trade_count": 0,
                "sell_trade_count": 0,
                "gross_buy_tokens": 0.0,
                "gross_sell_tokens": 0.0,
                "gross_buy_notional_usdc": 0.0,
                "gross_sell_notional_usdc": 0.0,
            },
        )
        aggregate["first_trade_ts"] = min(aggregate["first_trade_ts"], int(trade.get("timestamp", 0)))
        aggregate["last_trade_ts"] = max(aggregate["last_trade_ts"], int(trade.get("timestamp", 0)))
        aggregate["trade_count"] += 1
        if side == "BUY":
            aggregate["buy_trade_count"] += 1
            aggregate["gross_buy_tokens"] += size_tokens
            aggregate["gross_buy_notional_usdc"] += notional_usdc
        else:
            aggregate["sell_trade_count"] += 1
            aggregate["gross_sell_tokens"] += size_tokens
            aggregate["gross_sell_notional_usdc"] += notional_usdc

    return rows, rollups


def _build_position_rows(
    *,
    proxy_wallet: str,
    trade_rollups: dict[tuple[str, str], dict[str, Any]],
    positions: list[dict[str, Any]],
    closed_positions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    for position in positions:
        condition_id = str(position.get("conditionId", ""))
        asset = str(position.get("asset", ""))
        key = (condition_id, asset)
        seen_keys.add(key)
        aggregate = trade_rollups.get(key, {})
        rows.append(
            _build_position_row(
                proxy_wallet=proxy_wallet,
                aggregate=aggregate,
                position=position,
                status="OPEN",
            )
        )

    for position in closed_positions:
        condition_id = str(position.get("conditionId", ""))
        asset = str(position.get("asset", ""))
        key = (condition_id, asset)
        seen_keys.add(key)
        aggregate = trade_rollups.get(key, {})
        rows.append(
            _build_position_row(
                proxy_wallet=proxy_wallet,
                aggregate=aggregate,
                position=position,
                status="CLOSED",
            )
        )

    for key, aggregate in trade_rollups.items():
        if key in seen_keys:
            continue
        rows.append(
            _build_position_row(
                proxy_wallet=proxy_wallet,
                aggregate=aggregate,
                position={},
                status="TRADE_ONLY",
            )
        )

    rows.sort(key=lambda row: (row["status"], row["slug"], row["outcome"]))
    return rows


def _build_position_row(
    *,
    proxy_wallet: str,
    aggregate: dict[str, Any],
    position: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    family_key = str(aggregate.get("family_key") or "misc")
    family_label = str(aggregate.get("family_label") or "Misc or unclear markets")
    first_trade_ts = aggregate.get("first_trade_ts")
    last_trade_ts = aggregate.get("last_trade_ts")
    current_size = _safe_float(position.get("size"))
    avg_price_open = _safe_float(position.get("avgPrice"))
    current_price = _safe_float(position.get("curPrice"))
    initial_value_open = _safe_float(position.get("initialValue"))
    current_value_open = _safe_float(position.get("currentValue"))
    open_mark_to_market = round(current_value_open - initial_value_open, 6)
    reported_realized = _safe_float(position.get("realizedPnl"))
    estimated_total = round(reported_realized + open_mark_to_market, 6)
    source = status
    if status == "CLOSED":
        current_size = 0.0
        avg_price_open = _safe_float(position.get("avgPrice"))
        current_price = _safe_float(position.get("curPrice"))
        initial_value_open = 0.0
        current_value_open = 0.0
        open_mark_to_market = 0.0
        estimated_total = reported_realized
    if status == "TRADE_ONLY":
        estimated_total = 0.0
        source = "TRADE_ONLY"

    gross_buy_tokens = round(_safe_float(aggregate.get("gross_buy_tokens")), 6)
    gross_sell_tokens = round(_safe_float(aggregate.get("gross_sell_tokens")), 6)
    gross_buy_notional = round(_safe_float(aggregate.get("gross_buy_notional_usdc")), 6)
    gross_sell_notional = round(_safe_float(aggregate.get("gross_sell_notional_usdc")), 6)
    avg_buy_price = round(gross_buy_notional / gross_buy_tokens, 6) if gross_buy_tokens else 0.0
    avg_sell_price = round(gross_sell_notional / gross_sell_tokens, 6) if gross_sell_tokens else 0.0
    net_trade_cash_flow = round(gross_sell_notional - gross_buy_notional, 6)

    return {
        "status": status,
        "family_key": family_key,
        "family_label": family_label,
        "proxy_wallet": proxy_wallet,
        "condition_id": str(position.get("conditionId") or aggregate.get("condition_id") or ""),
        "asset": str(position.get("asset") or aggregate.get("asset") or ""),
        "title": str(position.get("title") or aggregate.get("title") or ""),
        "slug": str(position.get("slug") or aggregate.get("slug") or ""),
        "event_slug": str(position.get("eventSlug") or aggregate.get("event_slug") or ""),
        "outcome": str(position.get("outcome") or aggregate.get("outcome") or ""),
        "outcome_index": position.get("outcomeIndex", aggregate.get("outcome_index")),
        "opposite_outcome": str(position.get("oppositeOutcome", "")),
        "end_date": str(position.get("endDate", "")),
        "negative_risk": bool(position.get("negativeRisk", False)),
        "first_trade_at_utc": _timestamp_to_iso_from_unix(first_trade_ts) if first_trade_ts else "",
        "last_trade_at_utc": _timestamp_to_iso_from_unix(last_trade_ts) if last_trade_ts else "",
        "closed_at_utc": _timestamp_to_iso_from_unix(position.get("timestamp"))
        if position.get("timestamp")
        else "",
        "days_held": _days_held(first_trade_ts, position.get("timestamp")),
        "trade_count": int(aggregate.get("trade_count", 0)),
        "buy_trade_count": int(aggregate.get("buy_trade_count", 0)),
        "sell_trade_count": int(aggregate.get("sell_trade_count", 0)),
        "gross_buy_tokens": gross_buy_tokens,
        "gross_sell_tokens": gross_sell_tokens,
        "gross_buy_notional_usdc": gross_buy_notional,
        "gross_sell_notional_usdc": gross_sell_notional,
        "avg_buy_price": avg_buy_price,
        "avg_sell_price": avg_sell_price,
        "net_trade_cash_flow_usdc": net_trade_cash_flow,
        "current_size": round(current_size, 6),
        "avg_price_open": round(avg_price_open, 6),
        "current_price": round(current_price, 6),
        "initial_value_open": round(initial_value_open, 6),
        "current_value_open": round(current_value_open, 6),
        "open_mark_to_market_pnl_est": round(open_mark_to_market, 6),
        "reported_cash_pnl": round(_safe_float(position.get("cashPnl")), 6),
        "reported_percent_pnl": round(_safe_float(position.get("percentPnl")), 6),
        "reported_realized_pnl": round(reported_realized, 6),
        "reported_percent_realized_pnl": round(_safe_float(position.get("percentRealizedPnl")), 6),
        "estimated_total_pnl": round(estimated_total, 6),
        "source": source,
    }


def _build_family_rollup(position_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_family: dict[str, dict[str, Any]] = {}
    for row in position_rows:
        family_key = str(row["family_key"])
        family = by_family.setdefault(
            family_key,
            {
                "family_key": family_key,
                "family_label": row["family_label"],
                "position_count": 0,
                "open_count": 0,
                "closed_count": 0,
                "trade_count": 0,
                "reported_realized_pnl": 0.0,
                "open_mark_to_market_pnl_est": 0.0,
                "estimated_total_pnl": 0.0,
                "current_value_open": 0.0,
                "wins_closed": 0,
                "losses_closed": 0,
            },
        )
        family["position_count"] += 1
        family["trade_count"] += int(row["trade_count"])
        family["reported_realized_pnl"] += _safe_float(row["reported_realized_pnl"])
        family["open_mark_to_market_pnl_est"] += _safe_float(row["open_mark_to_market_pnl_est"])
        family["estimated_total_pnl"] += _safe_float(row["estimated_total_pnl"])
        family["current_value_open"] += _safe_float(row["current_value_open"])
        if row["status"] == "OPEN":
            family["open_count"] += 1
        if row["status"] == "CLOSED":
            family["closed_count"] += 1
            pnl = _safe_float(row["reported_realized_pnl"])
            if pnl > 0:
                family["wins_closed"] += 1
            elif pnl < 0:
                family["losses_closed"] += 1

    result = []
    for family in by_family.values():
        closed_count = family["closed_count"]
        family["closed_win_rate"] = round(
            100 * family["wins_closed"] / closed_count, 2
        ) if closed_count else None
        family["reported_realized_pnl"] = round(family["reported_realized_pnl"], 6)
        family["open_mark_to_market_pnl_est"] = round(family["open_mark_to_market_pnl_est"], 6)
        family["estimated_total_pnl"] = round(family["estimated_total_pnl"], 6)
        family["current_value_open"] = round(family["current_value_open"], 6)
        result.append(family)

    result.sort(key=lambda row: row["estimated_total_pnl"], reverse=True)
    return result


def _build_summary_metrics(
    *,
    proxy_wallet: str,
    input_account_address: str,
    profile: dict[str, Any],
    trades: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    closed_positions: list[dict[str, Any]],
    position_rows: list[dict[str, Any]],
    family_rollup: list[dict[str, Any]],
    position_value: dict[str, Any],
    traded_summary: dict[str, Any],
    accounting_snapshot: dict[str, Any],
) -> dict[str, Any]:
    closed_realized_values = [
        _safe_float(row["reported_realized_pnl"])
        for row in position_rows
        if row["status"] == "CLOSED"
    ]
    open_mtm_values = [
        _safe_float(row["open_mark_to_market_pnl_est"])
        for row in position_rows
        if row["status"] == "OPEN"
    ]
    estimated_total_values = [_safe_float(row["estimated_total_pnl"]) for row in position_rows]
    equity_row = (accounting_snapshot.get("equity") or [{}])[0] if accounting_snapshot else {}
    position_value_row = _first_row(position_value)

    top_winner = max(position_rows, key=lambda row: _safe_float(row["estimated_total_pnl"]), default=None)
    top_loser = min(position_rows, key=lambda row: _safe_float(row["estimated_total_pnl"]), default=None)

    return {
        "configured": True,
        "input_account_address": input_account_address,
        "resolved_proxy_wallet": proxy_wallet,
        "profile_name": profile.get("name"),
        "profile_pseudonym": profile.get("pseudonym"),
        "trade_count": len(trades),
        "distinct_markets_traded": len({row["slug"] for row in position_rows if row["slug"]}),
        "open_positions_count": len(positions),
        "closed_positions_count": len(closed_positions),
        "reported_traded_markets": int(_safe_float(traded_summary.get("traded"))),
        "position_value": round(_safe_float(position_value_row.get("value")), 6),
        "cash_balance": round(_safe_float(equity_row.get("cashBalance")), 6),
        "positions_value": round(_safe_float(equity_row.get("positionsValue")), 6),
        "equity": round(_safe_float(equity_row.get("equity")), 6),
        "valuation_time": str(equity_row.get("valuationTime", "")),
        "closed_realized_pnl_total": round(sum(closed_realized_values), 6),
        "open_mark_to_market_pnl_total": round(sum(open_mtm_values), 6),
        "estimated_total_pnl": round(sum(estimated_total_values), 6),
        "closed_win_rate": round(
            100 * sum(1 for value in closed_realized_values if value > 0) / len(closed_realized_values),
            2,
        )
        if closed_realized_values
        else None,
        "average_closed_realized_pnl": round(statistics.mean(closed_realized_values), 6)
        if closed_realized_values
        else None,
        "median_closed_realized_pnl": round(statistics.median(closed_realized_values), 6)
        if closed_realized_values
        else None,
        "top_family": family_rollup[0]["family_label"] if family_rollup else None,
        "worst_family": family_rollup[-1]["family_label"] if family_rollup else None,
        "top_winner_slug": top_winner["slug"] if top_winner else None,
        "top_winner_pnl": _safe_float(top_winner["estimated_total_pnl"]) if top_winner else None,
        "top_loser_slug": top_loser["slug"] if top_loser else None,
        "top_loser_pnl": _safe_float(top_loser["estimated_total_pnl"]) if top_loser else None,
    }


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _safe_fetch_json(url: str) -> dict[str, Any] | list[dict[str, Any]]:
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


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _first_row(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        return payload[0] if payload else {}
    if isinstance(payload, dict):
        return payload
    return {}


def _classify_performance_family(*parts: str) -> tuple[str, str]:
    family_key, family_label, _ = classify_market_family(*parts)
    if family_key != "misc":
        return family_key, family_label

    haystack = build_text_haystack(*parts)
    for extra_key, extra_label, keywords in EXTRA_PERFORMANCE_FAMILIES:
        for keyword in keywords:
            if keyword in haystack:
                return extra_key, extra_label
    return ("misc", "Misc or unclear markets")


def _timestamp_to_iso(timestamp: str) -> str:
    dt = datetime.strptime(timestamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _timestamp_to_iso_from_unix(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        dt = datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return ""
    return dt.isoformat()


def _days_held(first_trade_ts: Any, close_ts: Any | None) -> float | None:
    if not first_trade_ts:
        return None
    try:
        start = datetime.fromtimestamp(int(first_trade_ts), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None

    if close_ts:
        try:
            end = datetime.fromtimestamp(int(close_ts), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            end = datetime.now(timezone.utc)
    else:
        end = datetime.now(timezone.utc)

    return round((end - start).total_seconds() / 86400, 4)
