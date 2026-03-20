# Polymarket Agent MVP

Bare-bones scaffold for a Polymarket trading workflow that is being narrowed toward a document-first Codex CLI architecture.

## Direction

The intended MVP is no longer "Python runs the agent logic internally."

The intended MVP is:

- Python stays thin and boring
- Codex CLI performs each agent step
- every step reads prior artifacts from disk
- every step writes or updates a document for the next step
- the filesystem is the contract between agents

This keeps the code small, makes every decision auditable, and avoids building a heavy in-process agent framework too early.

## Current Architecture

The repo now runs a document-first Codex CLI workflow:

1. A thin runner selects the market and creates a run folder.
2. A Codex CLI call using `agents/market_data.md` writes the shared market context file.
3. A Codex CLI call using `agents/performance_data.md` updates account performance CSVs and summary JSON.
4. A Codex CLI call using `agents/performance_analyst.md` updates `docs/strategy.md`.
5. Separate Codex CLI calls using `agents/research_*.md` write specialist research memos.
6. A Codex CLI call using `agents/researcher.md` aggregates those specialist memos.
7. A Codex CLI call using `agents/assessor.md` writes the probability and sizing assessment.
8. A Codex CLI call using `agents/reviewer.md` writes the final pre-trade review and approval or block decision.
9. The runner writes dry-run execution and post-action documents.

The important constraint is that later steps reference files, not in-memory Python objects.

## Document Contract

Each run now converges toward a stable artifact set such as:

- `data/runs/<timestamp>-<slug>/00-request.md`
- `data/runs/<timestamp>-<slug>/00-history.md`
- `data/runs/<timestamp>-<slug>/01-market-context.json`
- `data/runs/<timestamp>-<slug>/02-performance-summary.json`
- `data/runs/<timestamp>-<slug>/03-strategy.md`
- `data/runs/<timestamp>-<slug>/04-specialist-*.md`
- `data/runs/<timestamp>-<slug>/05-research.md`
- `data/runs/<timestamp>-<slug>/06-assessment.md`
- `data/runs/<timestamp>-<slug>/06-assessment.json`
- `data/runs/<timestamp>-<slug>/07-review.md`
- `data/runs/<timestamp>-<slug>/07-review.json`
- `data/runs/<timestamp>-<slug>/08-execution.md`
- `data/runs/<timestamp>-<slug>/09-post-action.md`
- `docs/strategy.md`
- `data/decision_log.jsonl`

Some account-level artifacts should remain shared across runs:

- `data/performance/<account>/account_position_performance.csv`
- `data/performance/<account>/account_trade_ledger.csv`
- `data/performance/<account>/latest_summary.json`

## Current Repo Status

The current codebase already has:

- agent definitions stored in Markdown
- Polymarket market intake
- market context artifacts
- account performance CSV generation
- a continuously updated strategy document
- a thin Codex CLI runner

The old in-process pipeline has been removed. The active path is the file-driven runner.

## Current Dry Run

Run the workflow with:

```bash
python3 main.py --market-slug bitboy-convicted --estimated-probability 0.32
```

Useful flags:

```bash
python3 main.py --help
```

To stop after a specific stage while debugging:

```bash
python3 main.py --market-slug bitboy-convicted --stop-after performance_analyst
```

## Project Layout

```text
agents/                  Markdown definitions for each agent role
data/                    Runtime artifacts, history, reports, and performance output
docs/                    MVP map, strategy, and next-step planning
polymarket_agent/        Thin Python package and Codex runner helpers
main.py                  Entrypoint for the Codex-driven workflow
```

## What Is Intentionally Deferred

- live order placement through the CLOB client
- broad multi-family autonomous trading
- deep Python-side agent abstractions
- any non-auditable decision path

## Official References

- Gamma Markets API overview: <https://docs.polymarket.com/developers/gamma-markets-api/overview>
- CLOB overview: <https://docs.polymarket.com/developers/CLOB/introduction>
- Official Python client: <https://github.com/Polymarket/py-clob-client>

## Planning Docs

Read [`docs/mvp-map.md`](./docs/mvp-map.md) for the updated staged build plan and [`docs/next-step.md`](./docs/next-step.md) for the next step after the runner rearchitecture.
