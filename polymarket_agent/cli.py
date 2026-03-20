from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .account_performance import update_account_performance
from .config import get_settings
from .market_context import write_market_context_files
from .market_data import fetch_active_markets, select_market
from .runner import run_codex_cycle
from .strategy_doc import load_performance_summary, write_strategy_documents


HELPER_COMMANDS = {
    "write-market-context",
    "write-performance",
    "write-strategy",
}


def build_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the document-first Polymarket Codex workflow."
    )
    parser.add_argument(
        "--market-slug",
        help="Exact market slug to target. Defaults to the first active market.",
    )
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
    return parser


def build_market_context_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write the shared market context JSON for one run."
    )
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
    parser = argparse.ArgumentParser(
        description="Update docs/strategy.md and write the run strategy snapshot."
    )
    parser.add_argument("--performance-summary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in HELPER_COMMANDS:
        return _run_helper_command(argv[0], argv[1:])
    return _run_main_command(argv)


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
        market_slug=args.market_slug or settings.market_slug,
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

    raise RuntimeError(f"Unsupported helper command: {command}")
