# MVP Map

## Goal

Build a small, inspectable trading workflow where agent reasoning happens through Codex CLI actions and each stage communicates through documents on disk.

## Core Architecture Rule

The filesystem is the interface.

That means:

- each stage is a separate Codex CLI action
- each stage reads explicit input files
- each stage writes or updates explicit output files
- Python only handles thin orchestration, file paths, and simple validation
- no stage should depend on hidden in-memory state from a prior stage

## Why This Architecture

- it keeps the code extremely small
- it makes every intermediate decision inspectable
- it reduces framework complexity
- it makes retries and audits simple because the documents remain on disk
- it gives a clean path to gradual automation without hiding reasoning inside Python objects

## Target MVP Scope

The redefined MVP is:

1. One thin runner.
2. One filesystem contract for a run.
3. Agent roles defined in Markdown.
4. Codex CLI invoked once per stage.
5. Shared artifacts that later agents must reference.
6. Dry-run-only execution until the document loop is reliable.

## Target Run Contract

Each run should have a folder such as `data/runs/<timestamp>-<slug>/` with documents like:

- `00-request.md`
- `01-market-context.json`
- `02-performance-summary.json`
- `03-research.md`
- `04-assessment.md`
- `05-review.md`
- `06-execution.md`
- `07-post-action.md`

Cross-run shared references should remain stable:

- `data/decision_log.jsonl`
- `data/performance/<account>/account_position_performance.csv`
- `data/performance/<account>/account_trade_ledger.csv`
- `data/performance/<account>/latest_summary.json`
- `docs/strategy.md`

## MVP Stages

### Stage 0: File Contract And Prompts

Status: done.

Goal:
Define exactly what each agent reads and writes.

Deliverables:

- standard run-folder naming
- standard artifact names per stage
- agent prompts that explicitly name their input and output files
- a clear rule that each stage updates files instead of returning Python-only data

### Stage 1: Thin Codex CLI Runner

Goal:
Replace in-process stage logic with sequential Codex CLI invocations.

Status: done in the current pass.

Deliverables:

- one thin runner script
- one Codex CLI call per stage
- passing file references instead of Python object payloads
- simple validation that expected artifacts were written
- readable failure handling when a stage does not produce its document

### Stage 2: First Usable Dry-Run Market Family

Goal:
Make one market family work end to end in the new document-first flow.

Deliverables:

- one chosen market family
- one authoritative evidence path
- one research document schema for that family
- one assessment rule set
- one review rule set
- at least three saved dry runs using the document contract

### Stage 3: Performance Feedback Loop

Goal:
Use account history and strategy guidance as first-class inputs to every decision document.

Deliverables:

- account performance CSV refresh
- performance summary document
- continuously updated `docs/strategy.md`
- explicit references to strategy and performance files in research, assessment, and review

### Stage 4: Read-Only Trading Integration

Goal:
Add authenticated trading preparation without allowing live orders yet.

Deliverables:

- authenticated account check
- order payload preparation document
- pre-flight risk checks written to a reviewable file
- explicit dry-run execution output

### Stage 5: Small Live Execution

Goal:
Allow tightly constrained live orders only after the document workflow is reliable.

Deliverables:

- fixed max position sizing
- supported-market allowlist
- kill switch
- post-trade monitoring document
- strategy updates informed by realized results

## Design Notes

- History is part of the decision loop, not an afterthought.
- Every stage should leave behind an artifact a human can audit quickly.
- The system should remain biased toward `HOLD` until evidence, review, and performance feedback are reliable.
- If a stage cannot justify a decision in a document, it should not authorize a trade.
