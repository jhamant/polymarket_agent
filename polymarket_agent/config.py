from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    root_dir: Path
    agents_dir: Path
    data_dir: Path
    docs_dir: Path
    runs_dir: Path
    market_contexts_dir: Path
    performance_dir: Path
    strategy_path: Path
    history_path: Path
    gamma_api_base: str
    data_api_base: str
    clob_api_base: str
    market_limit: int
    position_size_usd: float
    live_trading: bool
    market_slug: str | None
    account_address: str | None
    performance_max_pages: int
    codex_model: str | None
    memory_log_path: Path
    risk_limits_path: Path


def get_settings() -> Settings:
    data_dir = ROOT / "data"
    return Settings(
        root_dir=ROOT,
        agents_dir=ROOT / "agents",
        data_dir=data_dir,
        docs_dir=ROOT / "docs",
        runs_dir=data_dir / "runs",
        market_contexts_dir=data_dir / "market_contexts",
        performance_dir=data_dir / "performance",
        strategy_path=ROOT / "docs" / "strategy.md",
        history_path=data_dir / "decision_log.jsonl",
        gamma_api_base=os.getenv("POLYMARKET_GAMMA_API_BASE", "https://gamma-api.polymarket.com"),
        data_api_base=os.getenv("POLYMARKET_DATA_API_BASE", "https://data-api.polymarket.com"),
        clob_api_base=os.getenv("POLYMARKET_CLOB_API_BASE", "https://clob.polymarket.com"),
        market_limit=int(os.getenv("POLYMARKET_MARKET_LIMIT", "10")),
        position_size_usd=float(os.getenv("POLYMARKET_POSITION_SIZE_USD", "5")),
        live_trading=os.getenv("POLYMARKET_LIVE_TRADING", "false").lower() == "true",
        market_slug=os.getenv("POLYMARKET_MARKET_SLUG") or None,
        account_address=os.getenv("POLYMARKET_ACCOUNT_ADDRESS") or None,
        performance_max_pages=int(os.getenv("POLYMARKET_PERFORMANCE_MAX_PAGES", "20")),
        codex_model=os.getenv("POLYMARKET_CODEX_MODEL") or "gpt-5.4-mini",
        memory_log_path=data_dir / "memory" / "memory_log.jsonl",
        risk_limits_path=ROOT / "risk_limits.json",
    )
