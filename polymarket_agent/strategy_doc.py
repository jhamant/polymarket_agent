from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_strategy_documents(
    *,
    performance_summary: dict[str, Any],
    strategy_path: Path,
    run_output_path: Path,
) -> str:
    summary_metrics = performance_summary.get("summary_metrics", {})
    family_rollup = performance_summary.get("family_rollup", [])
    directives = build_strategy_directives(summary_metrics, family_rollup)
    strategy_text = build_strategy_text(
        performance_summary=performance_summary,
        summary_metrics=summary_metrics,
        family_rollup=family_rollup,
        directives=directives,
    )
    strategy_path.parent.mkdir(parents=True, exist_ok=True)
    strategy_path.write_text(strategy_text, encoding="utf-8")
    run_output_path.parent.mkdir(parents=True, exist_ok=True)
    run_output_path.write_text(strategy_text, encoding="utf-8")
    return strategy_text


def load_performance_summary(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_strategy_directives(
    summary_metrics: dict[str, Any],
    family_rollup: list[dict[str, Any]],
) -> list[str]:
    directives: list[str] = []

    if not summary_metrics.get("configured"):
        return [
            "No account performance data is configured yet. Use baseline conservative mode.",
            "Do not lean on prior performance for sizing or family preference until a real account snapshot exists.",
            "Prefer HOLD when evidence is thin because no live performance feedback loop exists yet.",
        ]

    estimated_total_pnl = float(summary_metrics.get("estimated_total_pnl") or 0.0)
    closed_win_rate = summary_metrics.get("closed_win_rate")
    top_family = family_rollup[0] if family_rollup else None
    worst_family = family_rollup[-1] if family_rollup else None

    if estimated_total_pnl < 0:
        directives.append(
            "Estimated total account PnL is negative. Reduce size, demand stronger evidence, and default to HOLD more often."
        )
    else:
        directives.append(
            "Estimated total account PnL is non-negative. Keep sizing disciplined and avoid relaxing evidence standards."
        )

    if closed_win_rate is not None and closed_win_rate < 50:
        directives.append(
            "Closed-position win rate is below 50%. Raise the required edge threshold before approving trades."
        )
    elif closed_win_rate is not None:
        directives.append(
            "Closed-position win rate is at least 50%. Maintain the edge threshold, but keep family-level discipline."
        )

    if (
        top_family
        and top_family.get("closed_count", 0) >= 2
        and float(top_family.get("estimated_total_pnl", 0) or 0.0) > 0
    ):
        directives.append(
            f"Current strongest family is {top_family['family_label']}. Prioritize it only when the market context and evidence match the existing winning pattern."
        )

    if (
        worst_family
        and worst_family.get("position_count", 0) >= 2
        and float(worst_family.get("estimated_total_pnl", 0) or 0.0) < 0
        and worst_family.get("family_label") != (top_family or {}).get("family_label")
    ):
        directives.append(
            f"Current weakest family is {worst_family['family_label']}. Require extra confirmation or smaller size there."
        )

    if not directives:
        directives.append(
            "Performance evidence is mixed. Keep strategy neutral and conservative until a stronger pattern emerges."
        )

    return directives


def build_strategy_text(
    *,
    performance_summary: dict[str, Any],
    summary_metrics: dict[str, Any],
    family_rollup: list[dict[str, Any]],
    directives: list[str],
) -> str:
    generated_at = datetime.now(timezone.utc).isoformat()
    reference_files = performance_summary.get("reference_files", {})
    top_families = family_rollup[:3]
    bottom_families = _bottom_families(family_rollup)

    return f"""# Strategy

Updated: {generated_at}

## References

- performance position CSV: {reference_files.get("performance_position_csv_reference")}
- performance trade ledger CSV: {reference_files.get("performance_trade_ledger_csv_reference")}
- performance summary JSON: {reference_files.get("performance_summary_reference")}

## Performance Snapshot

- configured: {summary_metrics.get("configured")}
- resolved proxy wallet: {summary_metrics.get("resolved_proxy_wallet")}
- trade count: {summary_metrics.get("trade_count")}
- open positions: {summary_metrics.get("open_positions_count")}
- closed positions: {summary_metrics.get("closed_positions_count")}
- estimated total pnl: {summary_metrics.get("estimated_total_pnl")}
- closed realized pnl total: {summary_metrics.get("closed_realized_pnl_total")}
- open mark-to-market pnl total: {summary_metrics.get("open_mark_to_market_pnl_total")}
- closed win rate: {summary_metrics.get("closed_win_rate")}
- equity: {summary_metrics.get("equity")}
- cash balance: {summary_metrics.get("cash_balance")}
- positions value: {summary_metrics.get("positions_value")}

## What Is Working

{_family_lines(top_families)}

## What Is Not Working

{_family_lines(bottom_families)}

## Strategy Directives

{chr(10).join("- " + directive for directive in directives)}
"""


def _family_lines(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "- none yet"
    return "\n".join(
        "- "
        f"{row['family_label']}: est_total_pnl={row['estimated_total_pnl']}, "
        f"realized={row['reported_realized_pnl']}, "
        f"open_mtm={row['open_mark_to_market_pnl_est']}, "
        f"positions={row['position_count']}"
        for row in rows
    )


def _bottom_families(family_rollup: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(family_rollup) <= 1:
        return []
    bottom = list(reversed(family_rollup[-3:]))
    top_label = family_rollup[0].get("family_label")
    filtered = [row for row in bottom if row.get("family_label") != top_label]
    return filtered or bottom
