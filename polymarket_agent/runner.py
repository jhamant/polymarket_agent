from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Any

from .config import Settings
from .history import HistoryStore, write_history_snapshot
from .market_data import MarketSnapshot, fetch_active_markets, select_market


@dataclass(frozen=True)
class StageSpec:
    name: str
    agent_path: Path
    input_files: tuple[Path, ...]
    output_files: tuple[Path, ...]
    extra_instructions: tuple[str, ...]
    required_json_reference_keys: tuple[str, ...] = ()


def run_codex_cycle(
    *,
    settings: Settings,
    market_slug: str | None,
    estimated_probability: float | None,
    edge_threshold: float,
    history_limit: int,
    stop_after: str | None = None,
) -> dict[str, Any]:
    codex_path = shutil.which("codex")
    if not codex_path:
        raise RuntimeError("`codex` was not found on PATH.")

    market = _select_market(settings, market_slug)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = settings.runs_dir / f"{timestamp}-{market.slug}"
    logs_dir = run_dir / "logs"
    run_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    request_path = run_dir / "00-request.md"
    history_snapshot_path = run_dir / "00-history.md"
    market_context_path = run_dir / "01-market-context.json"
    performance_summary_path = run_dir / "02-performance-summary.json"
    strategy_snapshot_path = run_dir / "03-strategy.md"
    research_aggregate_path = run_dir / "05-research.md"
    assessment_md_path = run_dir / "06-assessment.md"
    assessment_json_path = run_dir / "06-assessment.json"
    review_md_path = run_dir / "07-review.md"
    review_json_path = run_dir / "07-review.json"
    execution_path = run_dir / "08-execution.md"
    post_action_path = run_dir / "09-post-action.md"

    history_store = HistoryStore(settings.history_path)
    history_summary = write_history_snapshot(
        store=history_store,
        limit=history_limit,
        output_path=history_snapshot_path,
    )

    stage_outputs = _specialist_output_paths(settings.agents_dir, run_dir)
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
        market_context_path=market_context_path,
        performance_summary_path=performance_summary_path,
        strategy_snapshot_path=strategy_snapshot_path,
        specialist_paths=stage_outputs,
        research_aggregate_path=research_aggregate_path,
        assessment_md_path=assessment_md_path,
        assessment_json_path=assessment_json_path,
        review_md_path=review_md_path,
        review_json_path=review_json_path,
        execution_path=execution_path,
        post_action_path=post_action_path,
    )
    request_path.write_text(request_text, encoding="utf-8")

    stages = _build_stage_specs(
        settings=settings,
        market=market,
        timestamp=timestamp,
        request_path=request_path,
        history_snapshot_path=history_snapshot_path,
        market_context_path=market_context_path,
        performance_summary_path=performance_summary_path,
        strategy_snapshot_path=strategy_snapshot_path,
        specialist_paths=stage_outputs,
        research_aggregate_path=research_aggregate_path,
        assessment_md_path=assessment_md_path,
        assessment_json_path=assessment_json_path,
        review_md_path=review_md_path,
        review_json_path=review_json_path,
        edge_threshold=edge_threshold,
    )

    valid_stage_names = [stage.name for stage in stages]
    if stop_after and stop_after not in valid_stage_names:
        raise RuntimeError(
            f"Unknown stage '{stop_after}'. Valid values: {', '.join(valid_stage_names)}"
        )

    for stage in stages:
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
        if stop_after == stage.name:
            return {
                "run_dir": str(run_dir),
                "market_slug": market.slug,
                "last_stage": stage.name,
            }

    execution_payload = _write_execution_documents(
        market=market,
        assessment_json_path=assessment_json_path,
        review_json_path=review_json_path,
        execution_path=execution_path,
        post_action_path=post_action_path,
        live_trading=settings.live_trading,
        history_summary=history_summary,
    )
    history_store.append(
        {
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
            "request_reference": str(request_path),
            "history_snapshot_reference": str(history_snapshot_path),
            "market_context_reference": str(market_context_path),
            "performance_summary_reference": str(performance_summary_path),
            "strategy_reference": str(settings.strategy_path),
            "research_reference": str(research_aggregate_path),
            "assessment_reference": str(assessment_json_path),
            "review_reference": str(review_json_path),
            "execution_reference": str(execution_path),
            "post_action_reference": str(post_action_path),
            "decision": execution_payload["decision"],
        }
    )

    return {
        "run_dir": str(run_dir),
        "market_slug": market.slug,
        "last_stage": "post_action",
        "decision": execution_payload["decision"]["action"],
        "execution_status": execution_payload["execution"]["status"],
        "review_reference": str(review_json_path),
    }


def _select_market(settings: Settings, market_slug: str | None) -> MarketSnapshot:
    fetch_limit = max(settings.market_limit, 100) if (market_slug or settings.market_slug) else settings.market_limit
    markets = fetch_active_markets(settings.gamma_api_base, fetch_limit)
    return select_market(markets, market_slug or settings.market_slug)


def _specialist_output_paths(agents_dir: Path, run_dir: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for agent_path in sorted(agents_dir.glob("research_*.md")):
        slug = agent_path.stem.removeprefix("research_").replace("_", "-")
        paths[agent_path.stem] = run_dir / f"04-specialist-{slug}.md"
    return paths


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
    market_context_path: Path,
    performance_summary_path: Path,
    strategy_snapshot_path: Path,
    specialist_paths: dict[str, Path],
    research_aggregate_path: Path,
    assessment_md_path: Path,
    assessment_json_path: Path,
    review_md_path: Path,
    review_json_path: Path,
    execution_path: Path,
    post_action_path: Path,
) -> str:
    specialist_lines = "\n".join(
        f"- {name}: {path}" for name, path in sorted(specialist_paths.items())
    )
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

- market_context_output: {market_context_path}
- performance_summary_output: {performance_summary_path}
- strategy_snapshot_output: {strategy_snapshot_path}
{specialist_lines}
- research_aggregate_output: {research_aggregate_path}
- assessment_markdown_output: {assessment_md_path}
- assessment_json_output: {assessment_json_path}
- review_markdown_output: {review_md_path}
- review_json_output: {review_json_path}
- execution_output: {execution_path}
- post_action_output: {post_action_path}

## Architecture Rule

Every stage must read prior artifacts from disk and write its own output artifact to disk. The filesystem is the interface between stages.
"""


def _build_stage_specs(
    *,
    settings: Settings,
    market: MarketSnapshot,
    timestamp: str,
    request_path: Path,
    history_snapshot_path: Path,
    market_context_path: Path,
    performance_summary_path: Path,
    strategy_snapshot_path: Path,
    specialist_paths: dict[str, Path],
    research_aggregate_path: Path,
    assessment_md_path: Path,
    assessment_json_path: Path,
    review_md_path: Path,
    review_json_path: Path,
    edge_threshold: float,
) -> list[StageSpec]:
    stages: list[StageSpec] = [
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

    for specialist_name, output_path in sorted(specialist_paths.items()):
        stages.append(
            StageSpec(
                name=specialist_name,
                agent_path=settings.agents_dir / f"{specialist_name}.md",
                input_files=(
                    request_path,
                    history_snapshot_path,
                    market_context_path,
                    performance_summary_path,
                    settings.strategy_path,
                ),
                output_files=(output_path,),
                extra_instructions=(
                    "This first thin runner is local-document-first. Use the referenced local artifacts as your evidence base for now.",
                    "If fresh external evidence is missing, say so explicitly, keep confidence low, and recommend the next evidence connector to add.",
                    f"Write one Markdown memo to {output_path}.",
                ),
            )
        )

    stages.extend(
        [
            StageSpec(
                name="researcher",
                agent_path=settings.agents_dir / "researcher.md",
                input_files=(
                    request_path,
                    history_snapshot_path,
                    market_context_path,
                    performance_summary_path,
                    settings.strategy_path,
                    *tuple(path for _, path in sorted(specialist_paths.items())),
                ),
                output_files=(research_aggregate_path,),
                extra_instructions=(
                    "Aggregate the specialist memos into one run-level research memo.",
                    f"Write the final aggregate memo to {research_aggregate_path}.",
                ),
            ),
            StageSpec(
                name="assessor",
                agent_path=settings.agents_dir / "assessor.md",
                input_files=(
                    request_path,
                    history_snapshot_path,
                    market_context_path,
                    performance_summary_path,
                    settings.strategy_path,
                    research_aggregate_path,
                ),
                output_files=(assessment_md_path, assessment_json_path),
                extra_instructions=(
                    f"Write the narrative assessment to {assessment_md_path}.",
                    f"Write the machine-readable decision payload to {assessment_json_path}.",
                    "If there is no defensible fair probability from the current documents, set `estimated_probability` to null and `recommended_action` to `HOLD`.",
                    f"Use the configured edge threshold of {edge_threshold}.",
                    """The JSON output must include:
{
  "estimated_probability": number or null,
  "market_yes_price": number or null,
  "edge_threshold": number,
  "edge": number or null,
  "recommended_action": "HOLD" | "BUY_YES" | "BUY_NO",
  "position_size_usd": number,
  "confidence": "low" | "medium" | "high",
  "rationale": string
}""",
                ),
            ),
            StageSpec(
                name="reviewer",
                agent_path=settings.agents_dir / "reviewer.md",
                input_files=(
                    request_path,
                    history_snapshot_path,
                    market_context_path,
                    performance_summary_path,
                    settings.strategy_path,
                    research_aggregate_path,
                    assessment_md_path,
                    assessment_json_path,
                ),
                output_files=(review_md_path, review_json_path),
                extra_instructions=(
                    f"Write the review memo to {review_md_path}.",
                    f"Write the machine-readable review payload to {review_json_path}.",
                    "If the proposed action is not HOLD and the research still lacks real-world evidence, block the trade.",
                    """The JSON output must include:
{
  "approved": boolean,
  "final_action": "HOLD" | "BUY_YES" | "BUY_NO",
  "final_size_usd": number,
  "blockers": [string],
  "rationale": string
}""",
                ),
            ),
        ]
    )
    return stages


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
    transcript = (
        "=== STDOUT ===\n"
        + completed.stdout
        + "\n=== STDERR ===\n"
        + completed.stderr
    )
    transcript_path.write_text(transcript, encoding="utf-8")

    if completed.returncode != 0:
        lower_stderr = completed.stderr.lower()
        if "usage limit" in lower_stderr or "purchase more credits" in lower_stderr:
            raise RuntimeError(
                "Codex CLI failed because the account hit its usage limit. "
                f"See {transcript_path}."
            )
        raise RuntimeError(
            f"Codex stage '{stage.name}' failed with exit code {completed.returncode}. "
            f"See {transcript_path}."
        )

    for output_path in stage.output_files:
        if not output_path.exists():
            raise RuntimeError(
                f"Codex stage '{stage.name}' did not create expected output {output_path}."
            )
        if output_path.suffix == ".json":
            json.loads(output_path.read_text(encoding="utf-8"))

    for key in stage.required_json_reference_keys:
        value = _read_json_key(stage.output_files[0], key)
        if not value:
            raise RuntimeError(
                f"Codex stage '{stage.name}' did not populate required reference '{key}'."
            )
        if not Path(str(value)).exists():
            raise RuntimeError(
                f"Codex stage '{stage.name}' produced reference '{key}' to missing file {value}."
            )


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


def _write_execution_documents(
    *,
    market: MarketSnapshot,
    assessment_json_path: Path,
    review_json_path: Path,
    execution_path: Path,
    post_action_path: Path,
    live_trading: bool,
    history_summary: str,
) -> dict[str, Any]:
    assessment = json.loads(assessment_json_path.read_text(encoding="utf-8"))
    review = json.loads(review_json_path.read_text(encoding="utf-8"))

    approved = bool(review.get("approved"))
    final_action = str(review.get("final_action") or "HOLD")
    final_size_usd = float(review.get("final_size_usd") or 0.0)
    rationale = str(review.get("rationale") or assessment.get("rationale") or "")

    if not approved:
        decision = {
            "action": "HOLD",
            "rationale": rationale or "Review blocked the trade.",
            "estimated_probability": assessment.get("estimated_probability"),
            "market_yes_price": assessment.get("market_yes_price"),
            "edge": assessment.get("edge"),
            "size_usd": 0.0,
        }
        execution = {
            "status": "blocked",
            "message": "Review blocked execution.",
        }
    elif final_action == "HOLD":
        decision = {
            "action": "HOLD",
            "rationale": rationale or "Assessment and review stayed in HOLD.",
            "estimated_probability": assessment.get("estimated_probability"),
            "market_yes_price": assessment.get("market_yes_price"),
            "edge": assessment.get("edge"),
            "size_usd": 0.0,
        }
        execution = {
            "status": "skipped",
            "message": "No trade was proposed after review.",
        }
    elif not live_trading:
        decision = {
            "action": final_action,
            "rationale": rationale,
            "estimated_probability": assessment.get("estimated_probability"),
            "market_yes_price": assessment.get("market_yes_price"),
            "edge": assessment.get("edge"),
            "size_usd": final_size_usd,
        }
        execution = {
            "status": "dry_run",
            "message": "Trade was not sent because live trading is disabled.",
        }
    else:
        decision = {
            "action": final_action,
            "rationale": rationale,
            "estimated_probability": assessment.get("estimated_probability"),
            "market_yes_price": assessment.get("market_yes_price"),
            "edge": assessment.get("edge"),
            "size_usd": final_size_usd,
        }
        execution = {
            "status": "blocked",
            "message": "Live trading is enabled, but authenticated order placement is not wired in yet.",
        }

    execution_path.write_text(
        f"""# Execution

- market_slug: {market.slug}
- action: {decision['action']}
- size_usd: {decision['size_usd']}
- status: {execution['status']}
- message: {execution['message']}
- assessment_reference: {assessment_json_path}
- review_reference: {review_json_path}
""",
        encoding="utf-8",
    )
    post_action_path.write_text(
        f"""# Post Action

- decision_action: {decision['action']}
- execution_status: {execution['status']}
- next_review_trigger: Add fresh external evidence or rerun when market pricing changes materially.
- history_reference_used:

{history_summary}
""",
        encoding="utf-8",
    )
    return {"decision": decision, "execution": execution}


def _read_json_key(path: Path, dotted_key: str) -> Any:
    payload = json.loads(path.read_text(encoding="utf-8"))
    value: Any = payload
    for part in dotted_key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _shell_command(*parts: str) -> str:
    return "Run: `" + " ".join(shlex.quote(part) for part in parts) + "`"
