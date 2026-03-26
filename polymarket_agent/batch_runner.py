"""
Batch runner: scan markets, deduplicate against recent assessments, and
run the Codex workflow on each qualifying candidate.

Deduplication logic:
- A market is skipped if it was assessed within `reassess_after_hours` AND
  the current yes_price has not moved more than `min_price_move` since the
  last assessment.
- If the price has moved significantly the market is re-queued with a
  "price_moved" note so agents know why it was re-assessed.
"""

from __future__ import annotations

from typing import Any

from .config import Settings
from .market_scanner import scan_markets
from .market_selector import filter_candidates, load_recent_assessments
from .runner import run_codex_cycle

# ---------------------------------------------------------------------------
# Batch orchestrator
# ---------------------------------------------------------------------------


def run_batch(
    settings: Settings,
    *,
    scan_limit: int = 200,
    min_score: float = 40.0,
    max_markets: int = 5,
    max_approved: int = 1,
    family_filter: str | None = None,
    reassess_after_hours: float = 24.0,
    min_price_move_for_reassess: float = 0.05,
    estimated_probability: float | None = None,
    edge_threshold: float = 0.05,
    history_limit: int = 5,
    stop_after: str | None = None,
) -> list[dict[str, Any]]:
    """
    Scan markets, deduplicate against recent history, and run the Codex
    workflow on each qualifying candidate in descending score order.

    Stops early if `max_approved` executions reach an approval decision.

    Returns a list of per-market result dicts (same shape as run_codex_cycle).
    """
    # --- Step 1: scan ---
    scan_result = scan_markets(
        settings.gamma_api_base,
        limit=scan_limit,
        min_score=min_score,
        family_filter=family_filter,
    )
    all_markets = scan_result["markets"]  # already sorted by score desc

    if not all_markets:
        return []

    # --- Step 2: load recent assessments and filter ---
    recent = load_recent_assessments(
        settings.history_path,
        within_hours=reassess_after_hours,
    )
    candidates = filter_candidates(
        all_markets,
        recent,
        min_price_move=min_price_move_for_reassess,
    )

    # Cap at max_markets
    candidates = candidates[:max_markets]

    if not candidates:
        return []

    # --- Step 3: run each candidate ---
    results: list[dict[str, Any]] = []
    approved_count = 0

    for market_dict, note in candidates:
        if approved_count >= max_approved:
            break

        slug = market_dict["slug"]

        result = run_codex_cycle(
            settings=settings,
            override_slug=slug,
            estimated_probability=estimated_probability,
            edge_threshold=edge_threshold,
            history_limit=history_limit,
            stop_after=stop_after,
        )

        # Attach scanner metadata
        result["scanner_score"] = market_dict.get("scores", {}).get("total")
        result["scanner_note"] = note

        results.append(result)

        # Count approvals (execution_ready or approve_for_execution_planning)
        decision = result.get("decision", "")
        if isinstance(decision, str) and decision in {
            "execution_ready",
            "approve_for_execution_planning",
            "approve_for_committee",
        }:
            approved_count += 1

    return results
