from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .history import HistoryStore, write_history_snapshot
from .market_data import MarketSnapshot
from .market_selector import select_market_for_run, write_selection_artifact
from .memory_log import append_memory_entry, format_memory_summary, load_family_memories
from .risk_limits import (
    check_risk_limits,
    load_exposure_snapshot,
    load_risk_limits,
    write_risk_check_artifact,
)

# ---------------------------------------------------------------------------
# Stage vocabulary
# ---------------------------------------------------------------------------

ALL_STAGE_NAMES: tuple[str, ...] = (
    "market_data",
    "performance_data",
    "performance_analyst",
    "market_quality_gate",
    "rules_resolution",
    "structural_alpha",
    # specialist stages are dynamically added here at runtime
    "researcher",
    "assessor",
    "reviewer",
    "execution_microstructure",
    "reconciliation_attribution",
)

# Quality gate decisions that allow the workflow to continue past the gate.
# "reject" is the only terminal decision at the gate.
GATE_CONTINUE_DECISIONS = {
    "admit_directional",
    "admit_structural",
    "admit_rules_only",
    "paper_only",
}

# Quality gate decisions that enable structural alpha routing.
GATE_STRUCTURAL_DECISIONS = {"admit_structural"}

# Quality gate decisions that enable directional specialist routing.
GATE_DIRECTIONAL_DECISIONS = {"admit_directional", "admit_structural"}


@dataclass(frozen=True)
class StageSpec:
    name: str
    agent_path: Path
    input_files: tuple[Path, ...]
    output_files: tuple[Path, ...]
    extra_instructions: tuple[str, ...]
    required_json_reference_keys: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_codex_cycle(
    *,
    settings: Settings,
    override_slug: str | None = None,
    scan_limit: int = 200,
    min_score: float = 40.0,
    family_filter: str | None = None,
    reassess_after_hours: float = 24.0,
    min_price_move: float = 0.05,
    estimated_probability: float | None,
    edge_threshold: float,
    history_limit: int,
    stop_after: str | None = None,
) -> dict[str, Any]:
    codex_path = shutil.which("codex")
    if not codex_path:
        raise RuntimeError("`codex` was not found on PATH.")

    # ------------------------------------------------------------------
    # Stage 0: Market Selection (deterministic Python, no AI call)
    # ------------------------------------------------------------------
    market, selection_result = select_market_for_run(
        settings.gamma_api_base,
        settings.history_path,
        scan_limit=scan_limit,
        min_score=min_score,
        family_filter=family_filter,
        reassess_after_hours=reassess_after_hours,
        min_price_move=min_price_move,
        override_slug=override_slug or settings.market_slug,
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = settings.runs_dir / f"{timestamp}-{market.slug}"
    logs_dir = run_dir / "logs"
    run_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Write Stage 0 artifact immediately so all agents can see selection context
    market_selection_path = run_dir / "00-market-selection.json"
    write_selection_artifact(selection_result, market_selection_path)

    # ------------------------------------------------------------------
    # Stage 0b: Risk Limits Check (deterministic Python, no AI call)
    # ------------------------------------------------------------------
    from .family_classifier import classify_market_family as _classify_for_risk

    _risk_family_key, _, _ = _classify_for_risk(market.question, getattr(market, "description", "") or "")
    _limits = load_risk_limits(settings.risk_limits_path)
    _perf_summary_for_risk = settings.performance_dir / "unconfigured" / "latest_summary.json"
    _exposure = load_exposure_snapshot(_perf_summary_for_risk)
    risk_check_result = check_risk_limits(_limits, _exposure, _risk_family_key, settings.position_size_usd)
    risk_check_path = run_dir / "00-risk-check.json"
    write_risk_check_artifact(risk_check_result, risk_check_path)

    if not risk_check_result.passed:
        violations_text = "; ".join(risk_check_result.violations)
        return {
            "run_dir": str(run_dir),
            "market_slug": market.slug,
            "last_stage": "risk_check",
            "aborted": True,
            "risk_violations": risk_check_result.violations,
            "message": f"Run aborted by risk limits: {violations_text}",
        }

    # ------------------------------------------------------------------
    # Artifact paths — numbered to match workflow order
    # ------------------------------------------------------------------
    request_path = run_dir / "00-request.md"
    history_snapshot_path = run_dir / "00-history.md"
    market_context_path = run_dir / "01-market-context.json"
    performance_summary_path = run_dir / "02-performance-summary.json"
    strategy_snapshot_path = run_dir / "03-strategy.md"
    quality_gate_md_path = run_dir / "04-quality-gate.md"
    quality_gate_json_path = run_dir / "04-quality-gate.json"
    rules_resolution_md_path = run_dir / "05-rules-resolution.md"
    rules_resolution_json_path = run_dir / "05-rules-resolution.json"
    structural_alpha_md_path = run_dir / "06-structural-alpha.md"
    structural_alpha_json_path = run_dir / "06-structural-alpha.json"
    # specialist paths derived below (07-specialist-*.md)
    research_aggregate_path = run_dir / "08-research.md"
    proposal_synthesis_path = run_dir / "08-proposal-synthesis.json"
    assessment_md_path = run_dir / "09-assessment.md"
    assessment_json_path = run_dir / "09-assessment.json"
    review_md_path = run_dir / "10-review.md"
    review_json_path = run_dir / "10-review.json"
    execution_md_path = run_dir / "11-execution.md"
    execution_json_path = run_dir / "11-execution.json"
    execution_fill_path = run_dir / "11-execution-fill.json"
    reconciliation_md_path = run_dir / "12-reconciliation.md"
    reconciliation_json_path = run_dir / "12-reconciliation.json"

    # ------------------------------------------------------------------
    # Validate stop_after before running anything
    # ------------------------------------------------------------------
    specialist_paths = _specialist_output_paths(settings.agents_dir, run_dir)
    all_valid_stages = list(ALL_STAGE_NAMES)
    # Insert specialist stage names after structural_alpha
    insert_idx = all_valid_stages.index("researcher")
    for specialist_name in sorted(specialist_paths.keys()):
        all_valid_stages.insert(insert_idx, specialist_name)
        insert_idx += 1

    if stop_after and stop_after not in all_valid_stages:
        raise RuntimeError(f"Unknown stage '{stop_after}'. Valid values: {', '.join(all_valid_stages)}")

    # ------------------------------------------------------------------
    # Write history snapshot and run request
    # ------------------------------------------------------------------
    history_store = HistoryStore(settings.history_path)
    from .family_classifier import classify_market_family

    family_key, _, _ = classify_market_family(market.question, getattr(market, "description", "") or "")
    family_memories = load_family_memories(settings.memory_log_path, family_key=family_key, limit=5)
    memory_section = format_memory_summary(family_memories)
    write_history_snapshot(
        store=history_store,
        limit=history_limit,
        output_path=history_snapshot_path,
        memory_section=memory_section,
    )

    request_text = _build_request_text(
        market=market,
        timestamp=timestamp,
        estimated_probability=estimated_probability,
        edge_threshold=edge_threshold,
        position_size_usd=settings.position_size_usd,
        live_trading=settings.live_trading,
        strategy_path=settings.strategy_path,
        decision_history_path=settings.history_path,
        history_snapshot_path=history_snapshot_path,
        market_selection_path=market_selection_path,
        risk_check_path=risk_check_path,
        market_context_path=market_context_path,
        performance_summary_path=performance_summary_path,
        strategy_snapshot_path=strategy_snapshot_path,
        quality_gate_md_path=quality_gate_md_path,
        quality_gate_json_path=quality_gate_json_path,
        rules_resolution_md_path=rules_resolution_md_path,
        rules_resolution_json_path=rules_resolution_json_path,
        structural_alpha_md_path=structural_alpha_md_path,
        structural_alpha_json_path=structural_alpha_json_path,
        specialist_paths=specialist_paths,
        research_aggregate_path=research_aggregate_path,
        proposal_synthesis_path=proposal_synthesis_path,
        assessment_md_path=assessment_md_path,
        assessment_json_path=assessment_json_path,
        review_md_path=review_md_path,
        review_json_path=review_json_path,
        execution_md_path=execution_md_path,
        execution_json_path=execution_json_path,
        execution_fill_path=execution_fill_path,
        reconciliation_md_path=reconciliation_md_path,
        reconciliation_json_path=reconciliation_json_path,
    )
    request_path.write_text(request_text, encoding="utf-8")

    # ------------------------------------------------------------------
    # Helper: run one stage and check stop_after
    # ------------------------------------------------------------------
    def run_stage(stage: StageSpec) -> bool:
        """Run a stage. Returns True if the caller should stop (stop_after hit)."""
        _run_codex_stage(
            codex_path=codex_path,
            settings=settings,
            run_dir=run_dir,
            market=market,
            stage=stage,
            request_path=request_path,
            history_snapshot_path=history_snapshot_path,
            estimated_probability=estimated_probability,
            edge_threshold=edge_threshold,
        )
        return stop_after == stage.name

    # ------------------------------------------------------------------
    # Phase 1: Pre-gate stages (always run)
    # ------------------------------------------------------------------
    pre_gate_stages = [
        StageSpec(
            name="market_data",
            agent_path=settings.agents_dir / "market_data.md",
            input_files=(request_path, history_snapshot_path),
            output_files=(market_context_path,),
            extra_instructions=(
                "Do not browse or explore. Run the helper command immediately, then verify the JSON file exists.",
                "Use the deterministic helper command below to create the market context JSON. Do not hand-write market data.",
                _shell_command(
                    "python3",
                    "main.py",
                    "write-market-context",
                    "--market-slug",
                    market.slug,
                    "--timestamp",
                    timestamp,
                    "--output",
                    str(market_context_path),
                ),
            ),
            required_json_reference_keys=(
                "shared_reference_files.decision_history_reference",
                "shared_reference_files.market_context_reference",
                "shared_reference_files.market_context_snapshot_reference",
            ),
        ),
        StageSpec(
            name="performance_data",
            agent_path=settings.agents_dir / "performance_data.md",
            input_files=(request_path, market_context_path),
            output_files=(performance_summary_path,),
            extra_instructions=(
                "Do not browse or explore. Run the helper command immediately, then verify the referenced CSV and JSON files exist.",
                "Use the deterministic helper command below to refresh shared account artifacts and write the run-level summary JSON.",
                _shell_command(
                    "python3",
                    "main.py",
                    "write-performance",
                    "--timestamp",
                    timestamp,
                    "--output",
                    str(performance_summary_path),
                ),
            ),
            required_json_reference_keys=(
                "reference_files.performance_position_csv_reference",
                "reference_files.performance_trade_ledger_csv_reference",
                "reference_files.performance_summary_reference",
            ),
        ),
        StageSpec(
            name="performance_analyst",
            agent_path=settings.agents_dir / "performance_analyst.md",
            input_files=(request_path, performance_summary_path, history_snapshot_path),
            output_files=(strategy_snapshot_path, settings.strategy_path),
            extra_instructions=(
                "Do not browse or explore. Run the helper command immediately, then verify both strategy files were updated.",
                "Use the deterministic helper command below to update the shared strategy document and write the run snapshot.",
                _shell_command(
                    "python3",
                    "main.py",
                    "write-strategy",
                    "--performance-summary",
                    str(performance_summary_path),
                    "--output",
                    str(strategy_snapshot_path),
                ),
            ),
        ),
    ]

    for stage in pre_gate_stages:
        if run_stage(stage):
            return _early_result(run_dir, market, stage.name)

    # ------------------------------------------------------------------
    # Phase 2: Market quality gate
    # ------------------------------------------------------------------
    quality_gate_stage = StageSpec(
        name="market_quality_gate",
        agent_path=settings.agents_dir / "market_quality_gate.md",
        input_files=(
            request_path,
            history_snapshot_path,
            market_context_path,
            performance_summary_path,
            settings.strategy_path,
        ),
        output_files=(quality_gate_md_path, quality_gate_json_path),
        extra_instructions=(
            f"Write the quality gate narrative to {quality_gate_md_path}.",
            f"Write the machine-readable quality gate payload to {quality_gate_json_path}.",
            """The JSON must include at minimum:
{
  "decision": "admit_directional" | "admit_structural" | "admit_rules_only" | "paper_only" | "reject",
  "quality_score": number,
  "eligible_strategy_families": [string],
  "downgrade_reasons": [string],
  "reject_reasons": [string],
  "operator_review_recommended": boolean
}""",
        ),
    )

    if run_stage(quality_gate_stage):
        return _early_result(run_dir, market, "market_quality_gate")

    # Read gate decision — early exit if rejected
    quality_gate_output = _read_json_safe(quality_gate_json_path)
    gate_decision = str(quality_gate_output.get("decision", "admit_directional")).lower()

    if gate_decision == "reject":
        reject_reasons = quality_gate_output.get("reject_reasons", [])
        _write_rejection_stub(
            run_dir=run_dir,
            market=market,
            quality_gate_json_path=quality_gate_json_path,
            reject_reasons=reject_reasons,
            assessment_json_path=assessment_json_path,
            review_json_path=review_json_path,
            execution_md_path=execution_md_path,
            execution_json_path=execution_json_path,
        )
        result = {
            "run_dir": str(run_dir),
            "market_slug": market.slug,
            "last_stage": "market_quality_gate",
            "decision": "reject",
            "gate_reject_reasons": reject_reasons,
        }
        history_store.append(
            _history_entry(
                timestamp,
                run_dir,
                market,
                "reject",
                str(request_path),
                str(history_snapshot_path),
                str(market_context_path),
                str(performance_summary_path),
                str(settings.strategy_path),
                str(research_aggregate_path),
                str(assessment_json_path),
                str(review_json_path),
                str(execution_md_path),
            )
        )
        return result

    # ------------------------------------------------------------------
    # Phase 3: Rules resolution (always runs when market is admitted)
    # ------------------------------------------------------------------
    rules_resolution_stage = StageSpec(
        name="rules_resolution",
        agent_path=settings.agents_dir / "rules_resolution.md",
        input_files=(
            request_path,
            history_snapshot_path,
            market_context_path,
            quality_gate_md_path,
            quality_gate_json_path,
        ),
        output_files=(rules_resolution_md_path, rules_resolution_json_path),
        extra_instructions=(
            f"Write the rules and resolution narrative to {rules_resolution_md_path}.",
            f"Write the machine-readable resolution payload to {rules_resolution_json_path}.",
            """The JSON must include at minimum:
{
  "resolution_path_status": string,
  "resolution_source": string,
  "yes_conditions": [string],
  "no_conditions": [string],
  "ambiguous_terms": [string],
  "resolution_risk_grade": "low" | "moderate" | "high" | "unacceptable",
  "paper_only_recommended": boolean,
  "operator_review_recommended": boolean
}""",
        ),
    )

    if run_stage(rules_resolution_stage):
        return _early_result(run_dir, market, "rules_resolution")

    # ------------------------------------------------------------------
    # Phase 4: Structural alpha (conditional on gate decision)
    # ------------------------------------------------------------------
    structural_alpha_produced = False
    if gate_decision in GATE_STRUCTURAL_DECISIONS:
        structural_alpha_stage = StageSpec(
            name="structural_alpha",
            agent_path=settings.agents_dir / "structural_alpha.md",
            input_files=(
                request_path,
                history_snapshot_path,
                market_context_path,
                quality_gate_md_path,
                quality_gate_json_path,
                rules_resolution_md_path,
                rules_resolution_json_path,
                settings.strategy_path,
            ),
            output_files=(structural_alpha_md_path, structural_alpha_json_path),
            extra_instructions=(
                f"Write the structural alpha narrative to {structural_alpha_md_path}.",
                f"Write the machine-readable structural alpha payload to {structural_alpha_json_path}.",
                """The JSON must include at minimum:
{
  "structural_setup_type": string,
  "gross_structural_edge": number or null,
  "net_structural_edge_estimate": number or null,
  "decision": "structural_opportunity" | "monitor_only" | "no_structural_edge" | "insufficient_context",
  "confidence": "low" | "medium" | "high"
}""",
            ),
        )

        if run_stage(structural_alpha_stage):
            return _early_result(run_dir, market, "structural_alpha")
        structural_alpha_produced = True

    # ------------------------------------------------------------------
    # Phase 5: Market-family specialists (conditional on gate decision)
    # ------------------------------------------------------------------
    specialists_produced: list[Path] = []
    if gate_decision in GATE_DIRECTIONAL_DECISIONS:
        for specialist_name, output_path in sorted(specialist_paths.items()):
            specialist_stage = StageSpec(
                name=specialist_name,
                agent_path=settings.agents_dir / f"{specialist_name}.md",
                input_files=(
                    request_path,
                    history_snapshot_path,
                    market_context_path,
                    quality_gate_md_path,
                    quality_gate_json_path,
                    rules_resolution_md_path,
                    rules_resolution_json_path,
                    performance_summary_path,
                    settings.strategy_path,
                ),
                output_files=(output_path,),
                extra_instructions=(
                    "This runner is document-first. Use the referenced local artifacts as your primary evidence base.",
                    "If a web-fetch helper is available, use it to pull authoritative sources and cite URLs.",
                    "Run: `python3 main.py web-fetch --url <url> --output <path>` to retrieve a specific URL.",
                    "Run: `python3 main.py web-search --query <query> --output <path>` to run a web search.",
                    "If fresh external evidence is missing, say so explicitly, keep confidence low, and name the next evidence source to add.",
                    f"Write one Markdown memo to {output_path}.",
                ),
            )

            if run_stage(specialist_stage):
                return _early_result(run_dir, market, specialist_name)
            specialists_produced.append(output_path)

    # ------------------------------------------------------------------
    # Phase 6: Research aggregation / proposal synthesis
    # ------------------------------------------------------------------
    structural_inputs: tuple[Path, ...] = (
        (structural_alpha_md_path, structural_alpha_json_path) if structural_alpha_produced else ()
    )
    researcher_stage = StageSpec(
        name="researcher",
        agent_path=settings.agents_dir / "researcher.md",
        input_files=(
            request_path,
            history_snapshot_path,
            market_context_path,
            quality_gate_md_path,
            quality_gate_json_path,
            rules_resolution_md_path,
            rules_resolution_json_path,
            performance_summary_path,
            settings.strategy_path,
            *structural_inputs,
            *tuple(specialists_produced),
        ),
        output_files=(research_aggregate_path, proposal_synthesis_path),
        extra_instructions=(
            f"Write the aggregate research memo to {research_aggregate_path}.",
            f"Write the proposal synthesis JSON to {proposal_synthesis_path}.",
            """The proposal synthesis JSON must include at minimum:
{
  "strategy_family": string,
  "market_family": string,
  "specialists_consulted": [string],
  "fair_value_estimate_or_range": string or null,
  "uncertainty_summary": string,
  "missing_inputs": [string],
  "ready_for_risk_review": boolean
}""",
        ),
    )

    if run_stage(researcher_stage):
        return _early_result(run_dir, market, "researcher")

    # ------------------------------------------------------------------
    # Phase 7: Assessment / portfolio risk
    # ------------------------------------------------------------------
    assessor_stage = StageSpec(
        name="assessor",
        agent_path=settings.agents_dir / "assessor.md",
        input_files=(
            request_path,
            history_snapshot_path,
            market_context_path,
            quality_gate_md_path,
            quality_gate_json_path,
            rules_resolution_md_path,
            rules_resolution_json_path,
            performance_summary_path,
            settings.strategy_path,
            research_aggregate_path,
            proposal_synthesis_path,
            *((structural_alpha_md_path, structural_alpha_json_path) if structural_alpha_produced else ()),
        ),
        output_files=(assessment_md_path, assessment_json_path),
        extra_instructions=(
            f"Write the assessment narrative to {assessment_md_path}.",
            f"Write the machine-readable assessment payload to {assessment_json_path}.",
            f"Use the configured edge threshold of {edge_threshold}.",
            "If there is no defensible fair probability, set decision to `hold` and recommended_size to 0.",
            """The JSON must include at minimum:
{
  "decision": "hold" | "paper_only" | "approve_for_committee" | "reject",
  "market_probability": number or null,
  "fair_value_estimate_or_range": string or null,
  "raw_edge": number or null,
  "net_edge": number or null,
  "cost_assumptions": { "fee_estimate": number, "slippage_estimate": number },
  "fill_assumptions": string,
  "resolution_risk_penalty": number,
  "portfolio_fit": "good" | "acceptable" | "poor" | "unknown",
  "recommended_size": number,
  "max_loss_assumption": number,
  "exposure_change_summary": string,
  "downgrade_reasons": [string],
  "reject_reasons": [string]
}""",
        ),
    )

    if run_stage(assessor_stage):
        return _early_result(run_dir, market, "assessor")

    # ------------------------------------------------------------------
    # Phase 8: Trade committee review
    # ------------------------------------------------------------------
    reviewer_stage = StageSpec(
        name="reviewer",
        agent_path=settings.agents_dir / "reviewer.md",
        input_files=(
            request_path,
            history_snapshot_path,
            market_context_path,
            quality_gate_md_path,
            quality_gate_json_path,
            rules_resolution_md_path,
            rules_resolution_json_path,
            performance_summary_path,
            settings.strategy_path,
            research_aggregate_path,
            proposal_synthesis_path,
            assessment_md_path,
            assessment_json_path,
            *((structural_alpha_md_path, structural_alpha_json_path) if structural_alpha_produced else ()),
        ),
        output_files=(review_md_path, review_json_path),
        extra_instructions=(
            f"Write the review memo to {review_md_path}.",
            f"Write the machine-readable review payload to {review_json_path}.",
            "If the proposed trade cannot be fully explained from the run artifacts, block it.",
            "If research lacks real-world primary-source evidence, block it.",
            """The JSON must include at minimum:
{
  "decision": "approve_for_execution_planning" | "paper_only" | "hold" | "reject",
  "artifact_chain_status": "complete" | "incomplete",
  "evidence_status": "sufficient" | "weak" | "missing",
  "strategy_alignment_status": "aligned" | "misaligned" | "unknown",
  "risk_alignment_status": "aligned" | "misaligned" | "unknown",
  "required_conditions_before_execution": [string],
  "blockers": [string],
  "operator_review_required": boolean
}""",
        ),
    )

    if run_stage(reviewer_stage):
        return _early_result(run_dir, market, "reviewer")

    # ------------------------------------------------------------------
    # Phase 9: Execution microstructure (agent-based, replaces Python stub)
    # ------------------------------------------------------------------
    execution_stage = StageSpec(
        name="execution_microstructure",
        agent_path=settings.agents_dir / "execution_microstructure.md",
        input_files=(
            request_path,
            market_context_path,
            quality_gate_md_path,
            quality_gate_json_path,
            assessment_json_path,
            review_json_path,
        ),
        output_files=(execution_md_path, execution_json_path),
        extra_instructions=(
            f"Write the execution plan narrative to {execution_md_path}.",
            f"Write the machine-readable execution plan to {execution_json_path}.",
            f"Live trading enabled: {settings.live_trading}. If live_trading is false, set execution_ready to false and explain.",
            "If the reviewer decision is not `approve_for_execution_planning`, set execution_ready to false.",
            "Do not change the approved size from the assessment. If execution constraints destroy economics, halt.",
            """The JSON must include at minimum:
{
  "execution_ready": boolean,
  "order_plan": string,
  "maker_or_taker": "maker" | "taker" | "none",
  "order_type": string,
  "price_or_price_band": string or null,
  "size": number,
  "timeout_and_cancel_rules": string,
  "retry_policy": string,
  "halt_conditions": [string],
  "live_risks": [string],
  "reconciliation_notes": string
}""",
        ),
    )

    if run_stage(execution_stage):
        return _early_result(run_dir, market, "execution_microstructure")

    # ------------------------------------------------------------------
    # Gap 1: Live order placement (only when live_trading=True)
    # ------------------------------------------------------------------
    if settings.live_trading:
        from .place_order import place_order as _place_order
        from .place_order import write_order_result as _write_order_result

        _order_result = _place_order(
            execution_json_path,
            clob_api_base=settings.clob_api_base,
            live_trading=True,
        )
        _write_order_result(_order_result, execution_fill_path)

    # ------------------------------------------------------------------
    # Phase 10: Reconciliation and attribution
    # ------------------------------------------------------------------
    reconciliation_stage = StageSpec(
        name="reconciliation_attribution",
        agent_path=settings.agents_dir / "reconciliation_attribution.md",
        input_files=(
            request_path,
            market_context_path,
            assessment_md_path,
            assessment_json_path,
            review_md_path,
            review_json_path,
            execution_md_path,
            execution_json_path,
            *((execution_fill_path,) if execution_fill_path.exists() else ()),
            performance_summary_path,
        ),
        output_files=(reconciliation_md_path, reconciliation_json_path),
        extra_instructions=(
            f"Write the reconciliation narrative to {reconciliation_md_path}.",
            f"Write the machine-readable reconciliation payload to {reconciliation_json_path}.",
            (
                f"Fill receipt available at {execution_fill_path}. Read it for actual fill data and compare planned vs executed."
                if execution_fill_path.exists()
                else "This is a dry-run workflow. Actual fill data is not yet available. Record planned-vs-actual as planned-only."
            ),
            "Create a structured memory entry in the JSON that future runs can consume.",
            """The JSON must include at minimum:
{
  "trade_outcome_status": "dry_run" | "live_pending" | "filled" | "cancelled" | "partial",
  "planned_trade_summary": string,
  "actual_fill_summary": string or null,
  "slippage_summary": string or null,
  "forecast_edge_assessment": string,
  "execution_edge_assessment": string,
  "risk_policy_assessment": string,
  "rules_or_resolution_assessment": string,
  "memory_entry": {
    "market_slug": string,
    "market_family": string,
    "decision": string,
    "key_lesson": string,
    "reuse_pattern": string or null,
    "avoid_pattern": string or null
  },
  "follow_up_actions": [string]
}""",
        ),
    )

    if run_stage(reconciliation_stage):
        return _early_result(run_dir, market, "reconciliation_attribution")

    # Gap 8: persist memory_entry from reconciliation to cross-run memory log
    reconciliation_output = _read_json_safe(reconciliation_json_path)
    memory_entry = reconciliation_output.get("memory_entry")
    if isinstance(memory_entry, dict) and memory_entry:
        append_memory_entry(settings.memory_log_path, memory_entry)

    # ------------------------------------------------------------------
    # Record to history and return
    # ------------------------------------------------------------------
    review_output = _read_json_safe(review_json_path)
    execution_output = _read_json_safe(execution_json_path)
    final_decision = _derive_final_decision(review_output, execution_output, gate_decision)

    history_store.append(
        _history_entry(
            timestamp,
            run_dir,
            market,
            final_decision,
            str(request_path),
            str(history_snapshot_path),
            str(market_context_path),
            str(performance_summary_path),
            str(settings.strategy_path),
            str(research_aggregate_path),
            str(assessment_json_path),
            str(review_json_path),
            str(execution_md_path),
        )
    )

    return {
        "run_dir": str(run_dir),
        "market_slug": market.slug,
        "last_stage": "reconciliation_attribution",
        "decision": final_decision,
        "execution_ready": bool(execution_output.get("execution_ready", False)),
        "gate_decision": gate_decision,
        "review_reference": str(review_json_path),
        "execution_reference": str(execution_json_path),
        "reconciliation_reference": str(reconciliation_json_path),
    }


# ---------------------------------------------------------------------------
# Helpers: market selection
# ---------------------------------------------------------------------------


def _specialist_output_paths(agents_dir: Path, run_dir: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for agent_path in sorted(agents_dir.glob("research_*.md")):
        slug = agent_path.stem.removeprefix("research_").replace("_", "-")
        paths[agent_path.stem] = run_dir / f"07-specialist-{slug}.md"
    return paths


# ---------------------------------------------------------------------------
# Helpers: request document
# ---------------------------------------------------------------------------


def _build_request_text(
    *,
    market: MarketSnapshot,
    timestamp: str,
    estimated_probability: float | None,
    edge_threshold: float,
    position_size_usd: float,
    live_trading: bool,
    strategy_path: Path,
    decision_history_path: Path,
    history_snapshot_path: Path,
    market_selection_path: Path,
    risk_check_path: Path,
    market_context_path: Path,
    performance_summary_path: Path,
    strategy_snapshot_path: Path,
    quality_gate_md_path: Path,
    quality_gate_json_path: Path,
    rules_resolution_md_path: Path,
    rules_resolution_json_path: Path,
    structural_alpha_md_path: Path,
    structural_alpha_json_path: Path,
    specialist_paths: dict[str, Path],
    research_aggregate_path: Path,
    proposal_synthesis_path: Path,
    assessment_md_path: Path,
    assessment_json_path: Path,
    review_md_path: Path,
    review_json_path: Path,
    execution_md_path: Path,
    execution_json_path: Path,
    execution_fill_path: Path,
    reconciliation_md_path: Path,
    reconciliation_json_path: Path,
) -> str:
    specialist_lines = "\n".join(f"- {name}: {path}" for name, path in sorted(specialist_paths.items()))
    return f"""# Run Request

- generated_at_utc: {datetime.now(timezone.utc).isoformat()}
- run_timestamp: {timestamp}
- market_slug: {market.slug}
- market_question: {market.question}
- market_end_date: {market.end_date}
- market_yes_price: {market.yes_price}
- market_no_price: {market.no_price}
- estimated_probability_hint: {estimated_probability}
- edge_threshold: {edge_threshold}
- position_size_usd: {position_size_usd}
- live_trading_enabled: {live_trading}

## Shared References

- decision_history_reference: {decision_history_path}
- history_snapshot_reference: {history_snapshot_path}
- strategy_reference: {strategy_path}

## Run Artifact Contract

- market_selection_output: {market_selection_path}
- risk_check_output: {risk_check_path}
- market_context_output: {market_context_path}
- performance_summary_output: {performance_summary_path}
- strategy_snapshot_output: {strategy_snapshot_path}
- quality_gate_markdown_output: {quality_gate_md_path}
- quality_gate_json_output: {quality_gate_json_path}
- rules_resolution_markdown_output: {rules_resolution_md_path}
- rules_resolution_json_output: {rules_resolution_json_path}
- structural_alpha_markdown_output: {structural_alpha_md_path}
- structural_alpha_json_output: {structural_alpha_json_path}
{specialist_lines}
- research_aggregate_output: {research_aggregate_path}
- proposal_synthesis_output: {proposal_synthesis_path}
- assessment_markdown_output: {assessment_md_path}
- assessment_json_output: {assessment_json_path}
- review_markdown_output: {review_md_path}
- review_json_output: {review_json_path}
- execution_markdown_output: {execution_md_path}
- execution_json_output: {execution_json_path}
- execution_fill_output: {execution_fill_path}
- reconciliation_markdown_output: {reconciliation_md_path}
- reconciliation_json_output: {reconciliation_json_path}

## Architecture Rule

Every stage must read prior artifacts from disk and write its own output artifact to disk.
The filesystem is the interface between stages.
Decision vocabulary: reject | paper_only | hold | admit_directional | admit_structural |
admit_rules_only | approve_for_committee | approve_for_execution_planning | execution_ready |
do_not_execute
"""


# ---------------------------------------------------------------------------
# Helpers: stage execution
# ---------------------------------------------------------------------------


def _run_codex_stage(
    *,
    codex_path: str,
    settings: Settings,
    run_dir: Path,
    market: MarketSnapshot,
    stage: StageSpec,
    request_path: Path,
    history_snapshot_path: Path,
    estimated_probability: float | None,
    edge_threshold: float,
) -> None:
    prompt = _build_stage_prompt(
        settings=settings,
        stage=stage,
        run_dir=run_dir,
        market=market,
        request_path=request_path,
        history_snapshot_path=history_snapshot_path,
        estimated_probability=estimated_probability,
        edge_threshold=edge_threshold,
    )
    logs_dir = run_dir / "logs"
    final_message_path = logs_dir / f"{stage.name}-final-message.txt"
    transcript_path = logs_dir / f"{stage.name}-transcript.log"

    command = [
        codex_path,
        "exec",
        "--ephemeral",
        "-s",
        "danger-full-access",
        "-C",
        str(settings.root_dir),
        "-o",
        str(final_message_path),
    ]
    if settings.codex_model:
        command.extend(["-m", settings.codex_model])

    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        cwd=settings.root_dir,
    )
    transcript = "=== STDOUT ===\n" + completed.stdout + "\n=== STDERR ===\n" + completed.stderr
    transcript_path.write_text(transcript, encoding="utf-8")

    if completed.returncode != 0:
        lower_stderr = completed.stderr.lower()
        if "usage limit" in lower_stderr or "purchase more credits" in lower_stderr:
            raise RuntimeError(f"Codex CLI failed because the account hit its usage limit. See {transcript_path}.")
        raise RuntimeError(
            f"Codex stage '{stage.name}' failed with exit code {completed.returncode}. See {transcript_path}."
        )

    for output_path in stage.output_files:
        if not output_path.exists():
            raise RuntimeError(f"Codex stage '{stage.name}' did not create expected output {output_path}.")
        if output_path.suffix == ".json":
            json.loads(output_path.read_text(encoding="utf-8"))

    for key in stage.required_json_reference_keys:
        value = _read_nested_json_key(stage.output_files[0], key)
        if not value:
            raise RuntimeError(f"Codex stage '{stage.name}' did not populate required reference '{key}'.")
        if not Path(str(value)).exists():
            raise RuntimeError(f"Codex stage '{stage.name}' produced reference '{key}' to missing file {value}.")


def _build_stage_prompt(
    *,
    settings: Settings,
    stage: StageSpec,
    run_dir: Path,
    market: MarketSnapshot,
    request_path: Path,
    history_snapshot_path: Path,
    estimated_probability: float | None,
    edge_threshold: float,
) -> str:
    input_block = "\n".join(f"- {path}" for path in stage.input_files)
    output_block = "\n".join(f"- {path}" for path in stage.output_files)
    extra_block = "\n".join(f"- {line}" for line in stage.extra_instructions)
    agent_instructions = stage.agent_path.read_text(encoding="utf-8").strip()
    return f"""You are executing one stage of a document-first Polymarket workflow.

Agent instructions:
{agent_instructions}

Runtime context:
- repository_root: {settings.root_dir}
- run_directory: {run_dir}
- market_slug: {market.slug}
- market_question: {market.question}
- market_end_date: {market.end_date}
- estimated_probability_hint: {estimated_probability}
- edge_threshold: {edge_threshold}
- request_file: {request_path}
- history_snapshot_file: {history_snapshot_path}

Required input files:
{input_block}

Required output files:
{output_block}

Stage-specific instructions:
{extra_block}

Execution rules:
- Read every listed input file before writing outputs.
- Update only the required output files for this stage, plus any shared file explicitly named in the instructions.
- If the current evidence is incomplete, say so directly in the output file and stay conservative.
- Keep the output auditable and concrete.
- End with one short sentence naming the file or files you updated.
"""


# ---------------------------------------------------------------------------
# Helpers: decision derivation and result construction
# ---------------------------------------------------------------------------


def _derive_final_decision(
    review_output: dict[str, Any],
    execution_output: dict[str, Any],
    gate_decision: str,
) -> str:
    review_decision = str(review_output.get("decision", "hold")).lower()
    if review_decision == "approve_for_execution_planning":
        if execution_output.get("execution_ready"):
            return "execution_ready"
        return "approved_not_executed"
    return review_decision


def _write_rejection_stub(
    *,
    run_dir: Path,
    market: MarketSnapshot,
    quality_gate_json_path: Path,
    reject_reasons: list[str],
    assessment_json_path: Path,
    review_json_path: Path,
    execution_md_path: Path,
    execution_json_path: Path,
) -> None:
    """Write minimal stub artifacts when the quality gate rejects a market."""
    reasons_text = "; ".join(reject_reasons) if reject_reasons else "market failed quality gate"

    assessment_stub = {
        "decision": "reject",
        "market_probability": None,
        "fair_value_estimate_or_range": None,
        "raw_edge": None,
        "net_edge": None,
        "cost_assumptions": {"fee_estimate": 0, "slippage_estimate": 0},
        "fill_assumptions": "n/a — market rejected at quality gate",
        "resolution_risk_penalty": 0,
        "portfolio_fit": "unknown",
        "recommended_size": 0,
        "max_loss_assumption": 0,
        "exposure_change_summary": "no exposure — rejected at quality gate",
        "downgrade_reasons": [],
        "reject_reasons": reject_reasons,
    }
    review_stub = {
        "decision": "reject",
        "artifact_chain_status": "incomplete",
        "evidence_status": "missing",
        "strategy_alignment_status": "unknown",
        "risk_alignment_status": "unknown",
        "required_conditions_before_execution": [],
        "blockers": [f"Quality gate rejected: {reasons_text}"],
        "operator_review_required": False,
    }
    execution_stub_json = {
        "execution_ready": False,
        "order_plan": "none — market rejected at quality gate",
        "maker_or_taker": "none",
        "order_type": "none",
        "price_or_price_band": None,
        "size": 0,
        "timeout_and_cancel_rules": "n/a",
        "retry_policy": "n/a",
        "halt_conditions": [],
        "live_risks": [],
        "reconciliation_notes": f"Market rejected at quality gate: {reasons_text}",
    }

    assessment_json_path.write_text(json.dumps(assessment_stub, indent=2), encoding="utf-8")
    review_json_path.write_text(json.dumps(review_stub, indent=2), encoding="utf-8")
    execution_md_path.write_text(
        f"# Execution\n\n- status: rejected_at_gate\n- market: {market.slug}\n- reasons: {reasons_text}\n",
        encoding="utf-8",
    )
    execution_json_path.write_text(json.dumps(execution_stub_json, indent=2), encoding="utf-8")


def _early_result(run_dir: Path, market: MarketSnapshot, last_stage: str) -> dict[str, Any]:
    return {
        "run_dir": str(run_dir),
        "market_slug": market.slug,
        "last_stage": last_stage,
    }


def _history_entry(
    timestamp: str,
    run_dir: Path,
    market: MarketSnapshot,
    decision: str,
    request_reference: str,
    history_snapshot_reference: str,
    market_context_reference: str,
    performance_summary_reference: str,
    strategy_reference: str,
    research_reference: str,
    assessment_reference: str,
    review_reference: str,
    execution_reference: str,
) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "run_dir": str(run_dir),
        "market": {
            "slug": market.slug,
            "question": market.question,
            "yes_price": market.yes_price,
            "no_price": market.no_price,
            "liquidity": market.liquidity,
            "volume": market.volume,
            "end_date": market.end_date,
        },
        "decision": decision,
        "request_reference": request_reference,
        "history_snapshot_reference": history_snapshot_reference,
        "market_context_reference": market_context_reference,
        "performance_summary_reference": performance_summary_reference,
        "strategy_reference": strategy_reference,
        "research_reference": research_reference,
        "assessment_reference": assessment_reference,
        "review_reference": review_reference,
        "execution_reference": execution_reference,
    }


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _read_json_safe(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _read_nested_json_key(path: Path, dotted_key: str) -> Any:
    payload = _read_json_safe(path)
    value: Any = payload
    for part in dotted_key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _shell_command(*parts: str) -> str:
    return "Run: `" + " ".join(shlex.quote(part) for part in parts) + "`"
