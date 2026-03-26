"""
Stage 0: Market Selection

Deterministic Python stage that runs before any AI agent. Scans active markets,
deduplicates against recent assessments, and selects the single best candidate.

Writes 00-market-selection.json so every downstream agent can see:
  - which market was chosen and why
  - what alternatives were considered
  - how many markets were filtered and why

This is always the first step of a run. --market-slug is an internal override
for testing only; normal production runs have no required flags.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .market_data import MarketSnapshot, fetch_active_markets, select_market
from .market_scanner import scan_markets

# ---------------------------------------------------------------------------
# Deduplication helpers (used by both market_selector and batch_runner)
# ---------------------------------------------------------------------------


def load_recent_assessments(
    history_path: Path,
    *,
    within_hours: float = 24.0,
) -> dict[str, dict[str, Any]]:
    """
    Read the decision_log JSONL and return the most recent record for each
    market slug assessed within the last `within_hours` hours.

    Returns: { slug: latest_history_record }
    """
    if not history_path.exists():
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)
    recent: dict[str, dict[str, Any]] = {}

    for line in history_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts_str = record.get("timestamp", "").replace("Z", "+00:00")
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            continue

        if ts < cutoff:
            continue

        slug = record.get("market", {}).get("slug", "")
        if not slug:
            continue

        existing = recent.get(slug)
        if existing is None:
            recent[slug] = record
        else:
            ex_ts_str = existing.get("timestamp", "").replace("Z", "+00:00")
            try:
                ex_ts = datetime.fromisoformat(ex_ts_str)
                if ex_ts.tzinfo is None:
                    ex_ts = ex_ts.replace(tzinfo=timezone.utc)
                if ts > ex_ts:
                    recent[slug] = record
            except (ValueError, AttributeError):
                recent[slug] = record

    return recent


def filter_candidates(
    scored_markets: list[dict[str, Any]],
    recent_assessments: dict[str, dict[str, Any]],
    *,
    min_price_move: float = 0.05,
) -> list[tuple[dict[str, Any], str | None]]:
    """
    Filter scanner results against recent assessments.

    Returns a list of (market_dict, note) tuples:
    - note is None  → fresh market, never assessed recently
    - note is "price_moved:+0.07" → re-queued because price shifted enough
    - markets assessed recently with no significant price move are excluded
    """
    candidates: list[tuple[dict[str, Any], str | None]] = []

    for m in scored_markets:
        slug = m.get("slug", "")
        prior = recent_assessments.get(slug)

        if prior is None:
            candidates.append((m, None))
            continue

        prior_yes = prior.get("market", {}).get("yes_price")
        current_yes = m.get("yes_price")

        if prior_yes is None or current_yes is None:
            candidates.append((m, "price_unknown"))
            continue

        move = current_yes - prior_yes
        if abs(move) >= min_price_move:
            sign = "+" if move >= 0 else ""
            candidates.append((m, f"price_moved:{sign}{move:.3f}"))
        # else: skip — recently assessed with no meaningful price change

    return candidates


# ---------------------------------------------------------------------------
# Selection result
# ---------------------------------------------------------------------------


@dataclass
class SelectionResult:
    slug: str
    mode: str  # "override" | "auto"
    scanner_score: float | None
    dedup_note: str | None  # None = fresh, "price_moved:+0.07" = re-queued
    alternatives: list[dict[str, Any]]  # top candidates considered (excluding winner)
    total_scanned: int
    total_qualified: int
    skipped_recently_assessed: int
    skipped_price_unchanged: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_slug": self.slug,
            "selection_mode": self.mode,
            "scanner_score": self.scanner_score,
            "dedup_note": self.dedup_note,
            "alternatives_considered": self.alternatives,
            "stats": {
                "total_scanned": self.total_scanned,
                "total_qualified": self.total_qualified,
                "skipped_recently_assessed": self.skipped_recently_assessed,
                "skipped_price_unchanged": self.skipped_price_unchanged,
            },
        }


# ---------------------------------------------------------------------------
# Core selection logic
# ---------------------------------------------------------------------------


def select_market_for_run(
    gamma_api_base: str,
    history_path: Path,
    *,
    scan_limit: int = 200,
    min_score: float = 40.0,
    family_filter: str | None = None,
    reassess_after_hours: float = 24.0,
    min_price_move: float = 0.05,
    override_slug: str | None = None,
) -> tuple[MarketSnapshot, SelectionResult]:
    """
    Select a market for the current run.

    If override_slug is provided (internal testing only), fetches that market
    directly and returns it with mode="override". No scanning or deduplication.

    Otherwise, scans markets, deduplicates against recent history, and picks
    the highest-scoring candidate.

    Raises RuntimeError if no qualifying market is found.
    """
    datetime.now(timezone.utc).isoformat()

    # -- Override path (internal testing) -----------------------------------
    if override_slug:
        markets = fetch_active_markets(gamma_api_base, 500)
        market = select_market(markets, override_slug)
        result = SelectionResult(
            slug=market.slug,
            mode="override",
            scanner_score=None,
            dedup_note=None,
            alternatives=[],
            total_scanned=len(markets),
            total_qualified=1,
            skipped_recently_assessed=0,
            skipped_price_unchanged=0,
        )
        return market, result

    # -- Auto path ----------------------------------------------------------
    scan_result = scan_markets(
        gamma_api_base,
        limit=scan_limit,
        min_score=min_score,
        family_filter=family_filter,
    )
    all_markets = scan_result["markets"]  # sorted descending by score

    recent = load_recent_assessments(history_path, within_hours=reassess_after_hours)

    # Count how many were recently assessed and unchanged (will be skipped)
    skipped_recently_assessed = 0
    skipped_price_unchanged = 0
    for m in all_markets:
        slug = m.get("slug", "")
        prior = recent.get(slug)
        if prior is None:
            continue
        skipped_recently_assessed += 1
        prior_yes = prior.get("market", {}).get("yes_price")
        current_yes = m.get("yes_price")
        if prior_yes is not None and current_yes is not None:
            if abs(current_yes - prior_yes) < min_price_move:
                skipped_price_unchanged += 1

    candidates = filter_candidates(
        all_markets,
        recent,
        min_price_move=min_price_move,
    )

    if not candidates:
        raise RuntimeError(
            f"No qualifying markets found. "
            f"Scanned {scan_result['total_fetched']}, "
            f"scored above {min_score}: {len(all_markets)}, "
            f"after deduplication: 0. "
            f"Lower --min-score or --reassess-after-hours to widen the pool."
        )

    selected_dict, dedup_note = candidates[0]
    slug = selected_dict["slug"]

    # Fetch the full MarketSnapshot for the selected slug
    markets = fetch_active_markets(gamma_api_base, 500)
    market = select_market(markets, slug)

    # Build alternatives list (next 4 candidates, not including winner)
    alternatives = []
    for m_dict, note in candidates[1:5]:
        alternatives.append(
            {
                "slug": m_dict["slug"],
                "question": m_dict.get("question", ""),
                "score": m_dict.get("scores", {}).get("total"),
                "family": m_dict.get("family_key"),
                "dedup_note": note,
            }
        )

    result = SelectionResult(
        slug=slug,
        mode="auto",
        scanner_score=selected_dict.get("scores", {}).get("total"),
        dedup_note=dedup_note,
        alternatives=alternatives,
        total_scanned=scan_result["total_fetched"],
        total_qualified=len(all_markets),
        skipped_recently_assessed=skipped_recently_assessed,
        skipped_price_unchanged=skipped_price_unchanged,
    )

    return market, result


# ---------------------------------------------------------------------------
# Artifact writer
# ---------------------------------------------------------------------------


def write_selection_artifact(
    result: SelectionResult,
    output_path: Path,
) -> dict[str, Any]:
    """Write 00-market-selection.json and return the dict."""
    payload = result.as_dict()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
