from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .account_performance import update_account_performance
from .batch_runner import run_batch
from .config import get_settings
from .evidence_connectors import (
    write_crypto_result,
    write_legal_result,
    write_macro_result,
    write_sports_result,
    write_weather_result,
)
from .market_context import write_market_context_files
from .market_data import fetch_active_markets, select_market
from .market_scanner import write_scan_result
from .place_order import place_order, write_order_result
from .position_monitor import monitor_positions, write_monitor_result
from .runner import run_codex_cycle
from .strategy_doc import load_performance_summary, write_strategy_documents
from .structural_alpha import execute_multi_leg, write_multi_leg_result
from .web_fetch import write_fetch_result, write_search_result

HELPER_COMMANDS = {
    "write-market-context",
    "write-performance",
    "write-strategy",
    "web-fetch",
    "web-search",
    "evidence-crypto",
    "evidence-sports",
    "evidence-macro",
    "evidence-legal",
    "evidence-weather",
    "place-order",
    "monitor-positions",
    "execute-multi-leg",
}

SCAN_COMMAND = "scan-markets"
BATCH_COMMAND = "batch-run"


def build_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the document-first Polymarket Codex workflow. "
            "Market selection is automatic (Stage 0): the scanner picks the "
            "highest-scoring un-assessed market. Use --market-slug only for testing."
        )
    )
    # Market selection (Stage 0) controls
    parser.add_argument(
        "--min-score",
        type=float,
        default=40.0,
        help="Minimum scanner quality score to consider a market (default: 40).",
    )
    parser.add_argument(
        "--scan-limit",
        type=int,
        default=200,
        help="Max markets to fetch from Gamma API during selection (default: 200).",
    )
    parser.add_argument(
        "--family",
        default=None,
        help="Restrict selection to one market family (e.g. crypto_onchain).",
    )
    parser.add_argument(
        "--reassess-after-hours",
        type=float,
        default=24.0,
        help="Hours before a previously-assessed market may be re-selected (default: 24).",
    )
    parser.add_argument(
        "--min-price-move",
        type=float,
        default=0.05,
        help="Minimum yes_price change to re-assess a recently-assessed market (default: 0.05).",
    )
    # Research controls
    parser.add_argument(
        "--estimated-probability",
        type=float,
        help="Optional probability hint to include in the request document.",
    )
    parser.add_argument(
        "--edge-threshold",
        type=float,
        default=0.05,
        help="Minimum probability edge required before recommending a trade.",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=5,
        help="How many recent decisions to summarize into the history snapshot.",
    )
    parser.add_argument(
        "--stop-after",
        help="Optional stage name to stop after for debugging.",
    )
    # Internal override — not part of normal usage
    parser.add_argument(
        "--market-slug",
        help=argparse.SUPPRESS,  # hidden from help; use for testing only
    )
    return parser


def build_market_context_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write the shared market context JSON for one run.")
    parser.add_argument("--market-slug", required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def build_performance_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh account performance artifacts and write the run summary JSON."
    )
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def build_strategy_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Update docs/strategy.md and write the run strategy snapshot.")
    parser.add_argument("--performance-summary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def build_web_fetch_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch a URL and write the result as JSON for agent evidence.")
    parser.add_argument("--url", required=True, help="URL to fetch.")
    parser.add_argument("--output", required=True, type=Path, help="Output JSON path.")
    return parser


def build_web_search_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search the web and write results as JSON for agent evidence.")
    parser.add_argument("--query", required=True, help="Search query.")
    parser.add_argument("--output", required=True, type=Path, help="Output JSON path.")
    parser.add_argument("--max-results", type=int, default=10, help="Maximum number of results to return.")
    return parser


def build_evidence_crypto_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch crypto price data from CoinGecko and write result as JSON.")
    parser.add_argument("--symbol", required=True, help="CoinGecko coin ID (e.g. bitcoin).")
    parser.add_argument("--vs-currency", required=True, default="usd", help="Target currency (default: usd).")
    parser.add_argument("--date", default=None, help="Historical date in YYYY-MM-DD format (optional).")
    parser.add_argument("--output", required=True, type=Path, help="Output JSON path.")
    return parser


def build_evidence_sports_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch sports data from the unofficial ESPN API and write result as JSON."
    )
    parser.add_argument("--sport", required=True, help="Sport slug (e.g. football).")
    parser.add_argument("--league", required=True, help="League slug (e.g. nfl).")
    parser.add_argument(
        "--query-type",
        required=True,
        choices=["scoreboard", "team-record", "team-schedule"],
        help="Type of data to fetch.",
    )
    parser.add_argument("--team-slug", default=None, help="Team slug (required for team-record and team-schedule).")
    parser.add_argument("--date", default=None, help="Date in YYYYMMDD format (required for scoreboard).")
    parser.add_argument("--output", required=True, type=Path, help="Output JSON path.")
    return parser


def build_evidence_macro_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch macroeconomic series data from FRED and write result as JSON.")
    parser.add_argument("--series-id", required=True, help="FRED series ID (e.g. UNRATE).")
    parser.add_argument("--observation-date", default=None, help="Observation date in YYYY-MM-DD format (optional).")
    parser.add_argument("--output", required=True, type=Path, help="Output JSON path.")
    return parser


def build_evidence_legal_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search CourtListener for legal cases and write result as JSON.")
    parser.add_argument("--query", required=True, help="Search query.")
    parser.add_argument("--court", default=None, help="Court filter (e.g. scotus).")
    parser.add_argument(
        "--result-type",
        default="dockets",
        choices=["dockets", "opinions"],
        help="Type of results to return (default: dockets).",
    )
    parser.add_argument("--max-results", type=int, default=5, help="Maximum number of results (default: 5).")
    parser.add_argument("--output", required=True, type=Path, help="Output JSON path.")
    return parser


def build_place_order_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read 11-execution.json and submit a signed limit order to the Polymarket CLOB. "
            "Requires POLYMARKET_PRIVATE_KEY env var. Dry-runs when live_trading is false."
        )
    )
    parser.add_argument(
        "--execution-json",
        required=True,
        type=Path,
        help="Path to 11-execution.json produced by the execution_microstructure agent.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Output path for 11-execution-fill.json.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Override dry-run mode and submit the order live. Also respects POLYMARKET_LIVE_TRADING=true env var.",
    )
    return parser


def build_monitor_positions_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read open positions from the performance summary and fetch current "
            "market prices to generate a monitoring memo with hold/close/reduce/add "
            "recommendations. Writes output to data/performance/."
        )
    )
    parser.add_argument(
        "--performance-summary",
        type=Path,
        default=None,
        help="Path to the latest performance summary JSON. "
        "Defaults to data/performance/unconfigured/latest_summary.json.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Output path for the monitoring JSON (e.g. data/performance/position_monitor_<ts>.json).",
    )
    return parser


def build_execute_multi_leg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read 06-structural-alpha.json and execute each leg sequentially via CLOB. "
            "Safe-stops on first leg failure. Dry-runs by default."
        )
    )
    parser.add_argument(
        "--structural-alpha-json",
        required=True,
        type=Path,
        help="Path to 06-structural-alpha.json produced by the structural_alpha agent.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Output path for multi-leg execution result JSON.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Override dry-run mode and submit orders live. Also respects POLYMARKET_LIVE_TRADING=true env var.",
    )
    return parser


def build_evidence_weather_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch weather data from NOAA NWS and write result as JSON.")
    parser.add_argument(
        "--query-type",
        required=True,
        choices=["alerts", "forecast"],
        help="Type of weather data to fetch.",
    )
    parser.add_argument("--area", default=None, help="State code for alerts (e.g. TX).")
    parser.add_argument("--lat", type=float, default=None, help="Latitude for forecast.")
    parser.add_argument("--lon", type=float, default=None, help="Longitude for forecast.")
    parser.add_argument("--output", required=True, type=Path, help="Output JSON path.")
    return parser


def build_batch_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan markets and run the Codex workflow on the best qualifying candidates."
    )
    parser.add_argument(
        "--max-markets",
        type=int,
        default=5,
        help="Maximum number of markets to assess in one batch run (default: 5).",
    )
    parser.add_argument(
        "--max-approved",
        type=int,
        default=1,
        help="Stop after this many markets reach an approval decision (default: 1).",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=40.0,
        help="Minimum scanner quality score to include a market (default: 40).",
    )
    parser.add_argument(
        "--scan-limit",
        type=int,
        default=200,
        help="Max number of markets to fetch from Gamma API when scanning (default: 200).",
    )
    parser.add_argument(
        "--family",
        default=None,
        help="Restrict batch to one market family (e.g. crypto_onchain).",
    )
    parser.add_argument(
        "--reassess-after-hours",
        type=float,
        default=24.0,
        help="Hours before a previously-assessed market may be re-selected (default: 24).",
    )
    parser.add_argument(
        "--min-price-move",
        type=float,
        default=0.05,
        help="Minimum yes_price change to re-assess a recently-assessed market (default: 0.05).",
    )
    parser.add_argument(
        "--edge-threshold",
        type=float,
        default=0.05,
        help="Minimum probability edge passed to each Codex run (default: 0.05).",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=5,
        help="How many recent decisions to summarize per run (default: 5).",
    )
    parser.add_argument(
        "--stop-after",
        default=None,
        help="Optional stage name to stop after (useful for debugging).",
    )
    return parser


def build_scan_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan active Polymarket markets and rank by quality score.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON output path. Defaults to data/scans/<timestamp>-scan.json.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max number of markets to fetch from Gamma API.",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=20.0,
        help="Minimum total quality score to include in output.",
    )
    parser.add_argument(
        "--family",
        default=None,
        help="Filter to a specific family key (e.g. crypto_onchain, sports_official_data).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Return only the top N markets by score.",
    )
    parser.add_argument(
        "--print-table",
        action="store_true",
        default=False,
        help="Print a summary table to stdout in addition to writing JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == SCAN_COMMAND:
        return _run_scan_command(argv[1:])
    if argv and argv[0] == BATCH_COMMAND:
        return _run_batch_command(argv[1:])
    if argv and argv[0] in HELPER_COMMANDS:
        return _run_helper_command(argv[0], argv[1:])
    return _run_main_command(argv)


def _run_scan_command(argv: list[str]) -> int:
    from datetime import datetime, timezone

    parser = build_scan_parser()
    args = parser.parse_args(argv)
    settings = get_settings()

    if args.output is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = settings.data_dir / "scans" / f"{timestamp}-scan.json"
    else:
        output_path = args.output.resolve()

    result = write_scan_result(
        settings.gamma_api_base,
        output_path,
        limit=args.limit,
        min_score=args.min_score,
        family_filter=args.family,
        top_n=args.top,
    )

    print(f"scanned_at:    {result['scanned_at']}")
    print(f"total_fetched: {result['total_fetched']}")
    print(f"above_min:     {result['total_scored_above_min']}")
    print(f"output:        {output_path}")

    if result["family_summary"]:
        print("\nFamily breakdown:")
        for fam_key, count in sorted(result["family_summary"].items(), key=lambda x: -x[1]):
            print(f"  {fam_key:<30s}  {count}")

    if args.print_table and result["markets"]:
        print(f"\n{'Score':>5}  {'DTE':>5}  {'Liq':>8}  {'Spread':>6}  {'Family':<22}  Question")
        print("-" * 110)
        for m in result["markets"]:
            score = m["scores"]["total"]
            dte = m["days_to_expiry"]
            liq = m["liquidity"]
            spread = m["spread"]
            family = m["family_key"]
            q = m["question"][:55]
            flags = ",".join(m["flags"][:2]) if m["flags"] else ""
            dte_str = f"{dte:.0f}d" if dte is not None else "?"
            liq_str = f"${liq:,.0f}"
            spd_str = f"{spread:.3f}" if spread is not None else "?"
            flag_note = f" [{flags}]" if flags else ""
            print(f"{score:5.1f}  {dte_str:>5}  {liq_str:>8}  {spd_str:>6}  {family:<22}  {q}{flag_note}")

    return 0


def _run_main_command(argv: list[str]) -> int:
    parser = build_run_parser()
    args = parser.parse_args(argv)
    settings = get_settings()

    if args.estimated_probability is not None and not 0 <= args.estimated_probability <= 1:
        parser.error("--estimated-probability must be between 0 and 1.")

    if args.edge_threshold <= 0 or args.edge_threshold >= 1:
        parser.error("--edge-threshold must be between 0 and 1.")

    result = run_codex_cycle(
        settings=settings,
        override_slug=args.market_slug,  # None in normal use; set for testing only
        scan_limit=args.scan_limit,
        min_score=args.min_score,
        family_filter=args.family,
        reassess_after_hours=args.reassess_after_hours,
        min_price_move=args.min_price_move,
        estimated_probability=args.estimated_probability,
        edge_threshold=args.edge_threshold,
        history_limit=args.history_limit,
        stop_after=args.stop_after,
    )

    print(f"market: {result['market_slug']}")
    print(f"run_dir: {result['run_dir']}")
    print(f"last_stage: {result['last_stage']}")
    if "decision" in result:
        print(f"decision: {result['decision']}")
    if "execution_status" in result:
        print(f"execution_status: {result['execution_status']}")
    if "review_reference" in result:
        print(f"review: {result['review_reference']}")
    return 0


def _run_batch_command(argv: list[str]) -> int:
    parser = build_batch_parser()
    args = parser.parse_args(argv)
    settings = get_settings()

    print(
        f"batch: scanning up to {args.scan_limit} markets  "
        f"min_score={args.min_score}  max_markets={args.max_markets}  "
        f"max_approved={args.max_approved}"
    )
    if args.family:
        print(f"batch: family filter = {args.family}")

    results = run_batch(
        settings,
        scan_limit=args.scan_limit,
        min_score=args.min_score,
        max_markets=args.max_markets,
        max_approved=args.max_approved,
        family_filter=args.family,
        reassess_after_hours=args.reassess_after_hours,
        min_price_move_for_reassess=args.min_price_move,
        edge_threshold=args.edge_threshold,
        history_limit=args.history_limit,
        stop_after=args.stop_after,
    )

    if not results:
        print("batch: no qualifying markets found after deduplication.")
        return 0

    print(f"\nbatch: ran {len(results)} market(s)")
    print(f"{'#':<3}  {'Score':>5}  {'Decision':<35}  {'Market'}")
    print("-" * 100)
    for i, r in enumerate(results, 1):
        slug = r.get("market_slug", "?")
        decision = str(r.get("decision", "?"))
        score = r.get("scanner_score")
        score_str = f"{score:.1f}" if score is not None else "  ?"
        note = r.get("scanner_note") or ""
        note_str = f"  [{note}]" if note else ""
        print(f"{i:<3}  {score_str:>5}  {decision:<35}  {slug}{note_str}")

    return 0


def _run_helper_command(command: str, argv: list[str]) -> int:
    settings = get_settings()

    if command == "write-market-context":
        parser = build_market_context_parser()
        args = parser.parse_args(argv)
        fetch_limit = max(settings.market_limit, 100)
        market = select_market(fetch_active_markets(settings.gamma_api_base, fetch_limit), args.market_slug)
        write_market_context_files(
            market=market,
            gamma_api_base=settings.gamma_api_base,
            clob_api_base=settings.clob_api_base,
            market_contexts_dir=settings.market_contexts_dir,
            history_path=settings.history_path,
            timestamp=args.timestamp,
            run_output_path=args.output.resolve(),
        )
        print(args.output.resolve())
        return 0

    if command == "write-performance":
        parser = build_performance_parser()
        args = parser.parse_args(argv)
        update_account_performance(
            gamma_api_base=settings.gamma_api_base,
            data_api_base=settings.data_api_base,
            performance_dir=settings.performance_dir,
            account_address=settings.account_address,
            max_pages=settings.performance_max_pages,
            timestamp=args.timestamp,
            run_output_path=args.output.resolve(),
        )
        print(args.output.resolve())
        return 0

    if command == "write-strategy":
        parser = build_strategy_parser()
        args = parser.parse_args(argv)
        performance_summary = load_performance_summary(args.performance_summary.resolve())
        write_strategy_documents(
            performance_summary=performance_summary,
            strategy_path=settings.strategy_path,
            run_output_path=args.output.resolve(),
        )
        print(args.output.resolve())
        return 0

    if command == "web-fetch":
        parser = build_web_fetch_parser()
        args = parser.parse_args(argv)
        result = write_fetch_result(args.url, args.output.resolve())
        status = result.get("status", "unknown")
        print(f"status: {status}  output: {args.output.resolve()}")
        return 0 if status == "ok" else 1

    if command == "web-search":
        parser = build_web_search_parser()
        args = parser.parse_args(argv)
        result = write_search_result(args.query, args.output.resolve(), max_results=args.max_results)
        status = result.get("status", "unknown")
        result_count = len(result.get("results", []))
        source = result.get("source", "unknown")
        print(f"status: {status}  source: {source}  results: {result_count}  output: {args.output.resolve()}")
        return 0 if status == "ok" else 1

    if command == "evidence-crypto":
        parser = build_evidence_crypto_parser()
        args = parser.parse_args(argv)
        result = write_crypto_result(args.symbol, args.vs_currency, args.date, args.output.resolve())
        status = result.get("status", "unknown")
        print(f"status: {status}  output: {args.output.resolve()}")
        return 0 if status == "ok" else 1

    if command == "evidence-sports":
        parser = build_evidence_sports_parser()
        args = parser.parse_args(argv)
        query_type = args.query_type.replace("-", "_")
        result = write_sports_result(
            args.sport, args.league, query_type, args.team_slug, args.date, args.output.resolve()
        )
        status = result.get("status", "unknown")
        print(f"status: {status}  output: {args.output.resolve()}")
        return 0 if status == "ok" else 1

    if command == "evidence-macro":
        parser = build_evidence_macro_parser()
        args = parser.parse_args(argv)
        result = write_macro_result(args.series_id, args.observation_date, args.output.resolve())
        status = result.get("status", "unknown")
        print(f"status: {status}  output: {args.output.resolve()}")
        return 0 if status == "ok" else 1

    if command == "evidence-legal":
        parser = build_evidence_legal_parser()
        args = parser.parse_args(argv)
        result = write_legal_result(args.query, args.court, args.result_type, args.max_results, args.output.resolve())
        status = result.get("status", "unknown")
        print(f"status: {status}  output: {args.output.resolve()}")
        return 0 if status == "ok" else 1

    if command == "evidence-weather":
        parser = build_evidence_weather_parser()
        args = parser.parse_args(argv)
        result = write_weather_result(args.query_type, args.area, args.lat, args.lon, args.output.resolve())
        status = result.get("status", "unknown")
        print(f"status: {status}  output: {args.output.resolve()}")
        return 0 if status == "ok" else 1

    if command == "monitor-positions":
        parser = build_monitor_positions_parser()
        args = parser.parse_args(argv)
        perf_path = args.performance_summary
        if perf_path is None:
            perf_path = settings.performance_dir / "unconfigured" / "latest_summary.json"
        positions = monitor_positions(perf_path.resolve(), settings.gamma_api_base)
        result = write_monitor_result(positions, args.output.resolve())
        count = result.get("open_positions_count", 0)
        summary = result.get("summary", {})
        print(
            f"positions: {count}  hold: {summary.get('hold', 0)}  "
            f"close: {summary.get('close', 0)}  add: {summary.get('add', 0)}  "
            f"output: {args.output.resolve()}"
        )
        return 0

    if command == "execute-multi-leg":
        parser = build_execute_multi_leg_parser()
        args = parser.parse_args(argv)
        import os as _os2

        live_trading = (
            args.live or settings.live_trading or _os2.getenv("POLYMARKET_LIVE_TRADING", "false").lower() == "true"
        )
        result = execute_multi_leg(
            args.structural_alpha_json.resolve(),
            clob_api_base=settings.clob_api_base,
            live_trading=live_trading,
        )
        write_multi_leg_result(result, args.output.resolve())
        mode = "LIVE" if live_trading else "DRY-RUN"
        print(
            f"[{mode}] status: {result.status}  legs: {result.total_legs}  "
            f"filled: {result.legs_filled}  failed: {result.legs_failed}  "
            f"partial: {result.partial}  output: {args.output.resolve()}"
        )
        if result.error:
            print(f"error: {result.error}", file=sys.stderr)
        return 0 if result.status not in {"failed"} else 1

    if command == "place-order":
        parser = build_place_order_parser()
        args = parser.parse_args(argv)
        live_trading = args.live or settings.live_trading
        import os as _os

        live_trading = live_trading or _os.getenv("POLYMARKET_LIVE_TRADING", "false").lower() == "true"
        result = place_order(
            args.execution_json.resolve(),
            clob_api_base=settings.clob_api_base,
            live_trading=live_trading,
        )
        write_order_result(result, args.output.resolve())
        mode = "LIVE" if live_trading else "DRY-RUN"
        print(
            f"[{mode}] status: {result.status}  side: {result.side}  "
            f"size: {result.size}  price: {result.price}  "
            f"order_id: {result.order_id}  output: {args.output.resolve()}"
        )
        if result.error:
            print(f"error: {result.error}", file=sys.stderr)
        return 0 if result.status not in {"error", "rejected"} else 1

    raise RuntimeError(f"Unsupported helper command: {command}")
