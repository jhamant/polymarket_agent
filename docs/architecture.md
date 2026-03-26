# Polymarket Agent — Architecture and Data Flow

## What This System Is

This is a document-first AI trading workflow for [Polymarket](https://polymarket.com), a prediction market where users buy and sell shares in binary outcomes (YES/NO) on real-world events. The system uses a chain of AI agents to evaluate a market, assess whether there is a tradable edge, and ultimately produce an execution plan — all while maintaining a complete, auditable trail of every decision.

The core design principle: **the filesystem is the interface.** Every agent reads from files and writes to files. No agent receives outputs from another agent as in-memory Python objects. This makes every intermediate decision inspectable, replayable, and debuggable.

---

## Repository Structure

```
polymarket_agent/
├── agents/                  Agent prompt definitions (Markdown)
├── data/
│   ├── decision_log.jsonl   Append-only log of every run outcome
│   ├── market_contexts/     Shared per-market context snapshots
│   ├── performance/         Account trade history and PnL CSVs
│   ├── runs/                One folder per run, all intermediate artifacts
│   └── scans/               Daily market scanner output
├── docs/
│   └── strategy.md          Live strategy document, updated every run
├── polymarket_agent/        Python package (thin orchestration layer)
│   ├── cli.py               CLI entry points and command routing
│   ├── config.py            Settings loaded from environment variables
│   ├── runner.py            Main orchestration loop (single market, 12 stages)
│   ├── market_selector.py   Stage 0: scan, deduplicate, select best market
│   ├── batch_runner.py      Multi-market orchestration (batch-run command)
│   ├── market_data.py       Gamma API market fetching and parsing
│   ├── market_context.py    CLOB order book and price history fetching
│   ├── account_performance.py  Trade/position history and PnL computation
│   ├── family_classifier.py Market topic classification by keyword
│   ├── market_scanner.py    Multi-market scoring and ranking
│   ├── strategy_doc.py      Performance-to-strategy-directive translation
│   ├── history.py           Decision log reading and writing
│   └── web_fetch.py         URL fetch and web search helpers
└── main.py                  Entry point
```

---

## How a Run Works — End-to-End Data Flow

A run is triggered with a single command — no market slug required:

```bash
# Normal production run — market selected automatically
python3 main.py

# Restrict selection to one family
python3 main.py --family crypto_onchain

# Batch: assess up to N markets, stop after 1 approval
python3 main.py batch-run --max-markets 5 --max-approved 1

# Internal testing override (hidden from help)
python3 main.py --market-slug will-italy-qualify-for-the-2026-world-cup
```

**Stage 0 runs first on every invocation.** Before any AI agent is called, `market_selector.py` scans up to 200 active markets, scores each on four quality dimensions, deduplicates against the last 24 hours of `decision_log.jsonl` (skipping markets whose price hasn't moved), and selects the highest-scoring candidate. This selection is recorded in `00-market-selection.json` so every downstream agent knows which market was chosen and why.

The runner then creates a timestamped folder under `data/runs/<timestamp>-<slug>/` and orchestrates **12 sequential AI stages**. Each stage is an AI agent invoked via the Codex CLI (`codex exec`). The agent reads the files listed in its prompt, does its reasoning, and writes its output file(s). The next stage reads those files.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  ENTRY POINT: python3 main.py                                               │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  STAGE 0 — market_selector.py  (Python, no AI call)                        │
│  Writes: 00-market-selection.json                                           │
│                                                                             │
│  1. Fetches up to 200 active markets from Gamma API                        │
│  2. Scores each on: liquidity, spread, timing window, 24h volume           │
│  3. Loads decision_log.jsonl — builds recent-assessment index               │
│  4. Filters out markets assessed < 24h ago with no significant price move  │
│  5. Selects the highest-scoring remaining candidate                         │
│  6. Writes 00-market-selection.json with: chosen slug, score, alternatives │
│     considered, skip counts, and dedup notes (e.g. "price_moved:+0.07")    │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  runner.py: run_codex_cycle()                                               │
│  - Creates run directory  data/runs/<timestamp>-<slug>/                     │
│  - Writes 00-request.md (manifest listing every artifact path)              │
│  - Writes 00-history.md (recent decisions from decision_log.jsonl)          │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
                 ┌───────────▼──────────────────────────────────────────────┐
                 │            PHASE 1: ACCOUNT + MARKET CONTEXT             │
                 └──────────────────────────────────────────────────────────┘

STAGE 1 — market_data  →  01-market-context.json
  Python helper: python3 main.py write-market-context
  - Calls Gamma API for full market metadata (question, end date, description,
    event tags, neg-risk flag, resolution source)
  - Calls CLOB API for live order book (best bid, best ask, depth levels)
  - Calls CLOB price-history API for recent price trajectory
  - Writes one JSON per run + shared per-market snapshot in
    data/market_contexts/<slug>/latest.json

STAGE 2 — performance_data  →  02-performance-summary.json
  Python helper: python3 main.py write-performance
  - Calls Gamma API to resolve account address → proxy wallet
  - Pages through all trades, open positions, and closed positions
  - Downloads the ZIP accounting snapshot from data-api.polymarket.com
  - Classifies every position by market family (crypto, sports, legal, etc.)
  - Writes two shared CSVs (trade ledger, position performance) and a
    summary JSON with PnL totals, win rates, and family-level rollups

STAGE 3 — performance_analyst  →  03-strategy.md + docs/strategy.md
  Python helper: python3 main.py write-strategy
  - Reads the performance summary JSON
  - Translates raw performance numbers into strategic directives
    (e.g. "top family is crypto — stay disciplined", "win rate below 50% —
    raise edge threshold")
  - Writes both a run-level strategy snapshot and the shared docs/strategy.md
    which every downstream agent reads

                 ┌───────────▼──────────────────────────────────────────────┐
                 │                PHASE 2: GATE AND ROUTING                 │
                 └──────────────────────────────────────────────────────────┘

STAGE 4 — market_quality_gate  →  04-quality-gate.md + 04-quality-gate.json
  AI agent reads: market context, performance summary, strategy
  - Scores the market on 8 dimensions: tradability, liquidity, spread,
    data freshness, rules clarity, timing risk, operational risk,
    strategy-family fit
  - Emits one of 5 decisions:
      admit_directional   → proceed with specialist research
      admit_structural    → proceed with structural + specialist research
      admit_rules_only    → proceed with rules-focused research only
      paper_only          → research allowed but no live execution
      reject              → STOP immediately, no further stages run
  - A "reject" writes stub artifacts for assessment/review/execution and
    records the decision to the history log — no further Codex calls are made
    (saves ~8 expensive AI calls per bad market)

  ⚡ EARLY EXIT: if decision == "reject", the runner returns immediately.

STAGE 5 — rules_resolution  →  05-rules-resolution.md + 05-rules-resolution.json
  AI agent reads: market context, quality gate output
  - Restates the market question precisely in plain English
  - Identifies the exact settlement path: which source resolves it, what
    must be true for YES vs NO
  - Identifies ambiguous wording, edge cases, and measurement traps
  - Assigns a resolution risk grade: low / moderate / high / unacceptable
  - An "unacceptable" grade will cause the assessor and reviewer to block
    the trade regardless of any directional view

STAGE 6 — structural_alpha (conditional)  →  06-structural-alpha.md + .json
  Runs ONLY when quality gate decision is "admit_structural"
  AI agent reads: market context, quality gate, rules resolution, strategy
  - Evaluates whether the market is part of a basket or linked-market setup
    where the pricing relationships create a mechanical edge
    (e.g. mutually exclusive outcomes that don't sum to 1.00)
  - If a structural opportunity exists, this becomes the primary trade basis
    and directional research is secondary

                 ┌───────────▼──────────────────────────────────────────────┐
                 │            PHASE 3: DIRECTIONAL RESEARCH                 │
                 └──────────────────────────────────────────────────────────┘

STAGE 7 — specialist research agents (conditional, parallel-eligible)
  Run ONLY when quality gate is admit_directional or admit_structural
  07-specialist-<family>.md — one file per specialist

  Available specialists, selected based on market content:
    research_crypto_onchain      → BTC/ETH prices, on-chain events, airdrops,
                                   governance votes, exchange listings
    research_regulatory_legal    → court rulings, agency actions, legislation
    research_sports_official_data → game results, standings, league decisions
    research_macro_releases      → CPI, GDP, Fed policy, scheduled data releases
    research_weather_disaster    → storm tracks, official measurements, declarations

  Each specialist:
  - Reads all prior artifacts (market context, quality gate, rules memo)
  - Assesses whether this market belongs to its family (NOT_PRIMARY if not)
  - Identifies the authoritative data source path for the outcome
  - Checks for actual real-world evidence (can call web-fetch / web-search helpers)
  - States confidence honestly: NO_EDGE / EDGE_POSSIBLE_BUT_UNVERIFIED /
    DIRECTIONAL_VIEW_WITH_LIMITED_CONFIDENCE / DIRECTIONAL_VIEW_WITH_EVIDENCE

STAGE 8 — researcher  →  08-research.md + 08-proposal-synthesis.json
  AI agent reads: everything produced so far, all specialist memos
  - Synthesizes all specialist views into one unified research package
  - Resolves conflicts between specialists
  - Identifies what evidence is still missing
  - Produces a single fair-value estimate or range
  - Determines whether the package is ready for risk review
  - Routes rules-only markets to a conservative framing, structural markets
    to the structural alpha view, directional markets to the specialist view

                 ┌───────────▼──────────────────────────────────────────────┐
                 │            PHASE 4: RISK AND COMMITTEE                   │
                 └──────────────────────────────────────────────────────────┘

STAGE 9 — assessor  →  09-assessment.md + 09-assessment.json
  AI agent reads: full research package + strategy + performance summary
  - Converts the proposal into a quantitative risk assessment
  - Computes raw edge: (estimated probability − market price)
  - Computes net edge: raw edge − fees − slippage − resolution risk penalty
  - A positive raw edge with a negative net edge is not a trade
  - Considers portfolio fit: would this trade concentrate exposure too heavily
    in one family, one direction, or one time horizon?
  - Recommends size conservatively and caps it when evidence quality is weak
  - Outputs one of: hold / paper_only / approve_for_committee / reject

STAGE 10 — reviewer  →  10-review.md + 10-review.json
  AI agent reads: the complete artifact chain from stages 1–9
  - Acts as a trade committee: checks the entire artifact chain for coherence
  - Verifies that the assessment used conservative assumptions
  - Verifies that primary-source evidence exists where required
  - Blocks any trade that cannot be fully explained from the run files alone
  - Cannot be overridden by persuasive research — the audit chain must hold
  - Outputs one of: approve_for_execution_planning / paper_only / hold / reject

                 ┌───────────▼──────────────────────────────────────────────┐
                 │            PHASE 5: EXECUTION AND LEARNING               │
                 └──────────────────────────────────────────────────────────┘

STAGE 11 — execution_microstructure  →  11-execution.md + 11-execution.json
  AI agent reads: market context, quality gate, assessment JSON, review JSON
  - Only runs meaningfully when review says approve_for_execution_planning
  - If live_trading = false (current state), sets execution_ready = false
    and documents what the order plan would be
  - Plans the order mechanics: maker vs taker, order type, price band,
    size (cannot exceed the assessed size), timeout, cancel rules
  - Defines halt conditions: what would trigger a stop before the fill
  - Currently produces a dry-run plan, not a live order

STAGE 12 — reconciliation_attribution  →  12-reconciliation.md + .json
  AI agent reads: assessment, review, execution plan, performance summary
  - Creates a structured post-trade record
  - Separates: was the forecast right? was the execution right? was the
    sizing right? were the rules understood correctly?
  - Writes a memory_entry JSON object with:
      key_lesson, reuse_pattern, avoid_pattern
  - This is the learning layer — future runs consume these entries through
    the history snapshot so the system improves over time

FINAL — history log + return
  runner.py appends one JSON record to data/decision_log.jsonl
  Returns: run_dir, market_slug, decision, gate_decision, execution_ready,
           and references to review, execution, and reconciliation artifacts
```

---

## How the Shared Artifacts Work

**`00-market-selection.json`** — written by Stage 0 before any AI agent runs.
Contains the selected slug, scanner score, deduplication note, up to 4 alternative
candidates that were considered, and stats on how many markets were scanned and
skipped. Downstream agents can read this to understand why this specific market
was chosen over others.

Three artifacts persist across runs and feed into every new run:

**`docs/strategy.md`** — updated every run by the `write-strategy` helper.
Translates live account PnL and win rates into plain-English guardrails:
- which family is strongest and should be prioritized
- whether to raise or lower the edge threshold
- whether to tighten sizing due to recent underperformance

**`data/decision_log.jsonl`** — append-only JSONL, one record per run.
The `00-history.md` artifact shown to every agent is built from the most
recent N entries in this file. Agents see what was decided recently and why,
which prevents repeating the same analysis on the same market.

**`data/market_contexts/<slug>/latest.json`** — the most recent market context
snapshot for each market. A new run can compare today's price against yesterday's
to detect meaningful movement before spending AI calls on full research.

---

## The Market Scanner (Daily Automation)

`python3 main.py scan-markets` is a separate, lightweight path that does not
invoke any AI agents. It fetches up to 200 markets from the Gamma API, scores
each on four dimensions (0–25 points each), and writes a ranked JSON report.

```
Score = liquidity_score + spread_score + timing_score + volume_score (max 100)

Liquidity (0–25): how much USDC depth is in the book
Spread   (0–25): how tight the bid/ask spread is (≤2¢ = full, ≥10¢ = zero)
Timing   (0–25): is the expiry in the 3–45 day sweet spot?
Volume   (0–25): how active was the last 24 hours?
```

The daily scheduled trigger (08:00 UTC) runs this scan, commits the result,
and highlights any market with score ≥ 80 and no disqualifying flags as a
high-priority candidate for the full research workflow.

---

## The Web Research Helpers

Two CLI commands give specialist agents access to real-world evidence:

```bash
python3 main.py web-fetch --url <url> --output research/coingecko.json
python3 main.py web-search --query "ETH airdrop date confirmed" --output research/search.json
```

`web-fetch` retrieves a specific URL (CoinGecko, court records, league tables,
NOAA storm tracks, etc.) and saves the full response as JSON.

`web-search` queries DuckDuckGo HTML and returns the top 10 results. If
`BRAVE_SEARCH_API_KEY` is set in the environment, Brave Search is used instead
for higher-quality results.

Agents cite the URL and timestamp of every source they used, keeping the
artifact chain auditable.

---

## Products Being Built

**Product 1: Auditable Pre-Trade Research Workflow**
Status: functional in dry-run mode.
A complete AI-powered research and risk review chain that evaluates any
Polymarket market and produces a structured, auditable recommendation. Every
decision is traceable to a specific file in the run directory.

**Product 2: Self-Improving Strategy Layer**
Status: functional.
The `docs/strategy.md` file is updated every run from real account performance.
Agents consume it as a first-class input. The reconciliation agent writes
structured lessons that feed back into the history snapshot.

**Product 3: Market Discovery, Triage, and Automatic Selection**
Status: functional.
The scanner scores all active markets daily, identifies the best candidates,
and flags high-priority markets for research without requiring human curation.
The `--auto` flag and `batch-run` command extend this so the system can select
and assess markets without any manual slug input. Deduplication prevents
re-running unchanged markets within a configurable time window.

**Product 4: Live Execution Engine**
Status: designed but not implemented.
The execution_microstructure agent produces a complete dry-run order plan.
The Python runner has a `live_trading` flag. But the CLOB client integration
that would translate that plan into actual signed orders is not yet wired in.

---

## What Is Missing for Fully Autonomous Trading

The system is architecturally complete for everything except placing real orders
and monitoring open positions. Gaps are listed in implementation order — research
quality improvements first (zero risk), then risk controls, then live trading,
then position lifecycle:

### ✓ Gap 7 — Automatic Market Selection and Batch Runs (COMPLETE)
**What was built** (`market_selector.py`, `batch_runner.py`, `cli.py`):
- Stage 0 runs before every AI agent: scans markets, scores and ranks them,
  deduplicates against recent assessments, and selects the best candidate
- `python3 main.py` with no flags runs a full workflow automatically
- `batch-run` command assesses up to N markets per session with `--max-approved`
  budget control
- Deduplication skips markets assessed within 24h unless price moved > 5 cents
- `00-market-selection.json` records what was chosen and what alternatives existed

---

### ✓ Gap 5 — Evidence Connectors for Each Market Family (COMPLETE)
**What was built** (`evidence_connectors.py`, `cli.py`):
- `evidence-crypto`: CoinGecko price API — current or historical price, market cap, 24h volume. No key required.
- `evidence-sports`: ESPN unofficial API — scoreboard by date, team record, team schedule. No key required.
- `evidence-macro`: FRED economic series — any series by ID and date. Requires `FRED_API_KEY` env var; fails gracefully with a clear message if missing.
- `evidence-legal`: CourtListener dockets and opinions search. Requires `COURTLISTENER_API_TOKEN` env var (free registration); returns a clear registration link when missing.
- `evidence-weather`: NOAA NWS alerts by US state and point forecasts by lat/lon. No key required. US locations only — non-US coordinates return 404.

All connectors write a consistent JSON envelope (`connector`, `fetched_at`, `status`, `params`, `data`, `error`) so agents can cite the source and timestamp in their memos.

```bash
python3 main.py evidence-crypto --symbol bitcoin --vs-currency usd --output research/btc.json
python3 main.py evidence-sports --sport soccer --league ita.1 --query-type scoreboard --date 20260323 --output research/soccer.json
python3 main.py evidence-macro --series-id UNRATE --output research/unemployment.json
python3 main.py evidence-legal --query "SEC v Ripple" --output research/ripple.json
python3 main.py evidence-weather --query-type alerts --area TX --output research/wx.json
python3 main.py evidence-weather --query-type forecast --lat 40.71 --lon -74.01 --output research/nyc-wx.json
```

### ✓ Gap 8 — Persistent Memory Store (COMPLETE)
**What was built** (`memory_log.py`, `config.py`, `history.py`, `runner.py`):
- After every reconciliation stage, the runner extracts `memory_entry` from `12-reconciliation.json` and appends it to `data/memory/memory_log.jsonl`
- At the start of each run, the runner classifies the selected market's family and loads the 5 most recent memories for that family
- Those memories are injected into `00-history.md` under a `## Family Memory Entries` section so every AI agent sees them
- Memory entries include: `market_slug`, `market_family`, `decision`, `key_lesson`, `reuse_pattern`, `avoid_pattern`
- The system now recalls patterns across runs, not just recent decision outcomes

### Gap 6 — Risk Limits and Portfolio-Level Controls ✓
**Complete. Validated in dry-run mode.**

**What it is**: The assessor evaluates each trade independently. There is no
system-level guard that enforces maximum total exposure, maximum exposure per
family, maximum drawdown before shutting down, or daily loss limits.

**What was implemented**:
- `risk_limits.json` config file with per-family caps, max open positions,
  daily loss circuit-breaker, and per-trade size ceiling
- `polymarket_agent/risk_limits.py` — loads limits, reads exposure snapshot,
  checks all limits, writes `00-risk-check.json` artifact
- Stage 0b added to runner: runs after market selection, before any AI stage;
  aborts the run with structured violations list if any hard limit is breached
- Exposure snapshot reads from `performance_dir/*/latest_summary.json`
- All violations surface in `00-risk-check.json` for agent review

### Gap 1 — Authenticated CLOB Order Placement ✓
**Complete. Requires POLYMARKET_PRIVATE_KEY and POLYMARKET_LIVE_TRADING=true to activate.**

**What it is**: The `execution_microstructure` agent produces a full order plan
(price, size, maker/taker, timeout) but no code submits it to the CLOB API.

**What was implemented**:
- `polymarket_agent/place_order.py` — reads `11-execution.json`, validates
  `execution_ready`, extracts `(side, token_id, size, price)`, builds and
  submits a signed GTC limit order via `py-clob-client`
- `python3 main.py place-order --execution-json <path> --output <path>` CLI helper
- Runner automatically calls `place_order()` between execution and reconciliation
  stages when `POLYMARKET_LIVE_TRADING=true`; dry-run is the default
- Fill receipt written to `11-execution-fill.json`; reconciliation stage reads it
  when present and compares planned vs actual fill
- `py-clob-client>=0.20.0` added to `pyproject.toml` dependencies
- Requires env var: `POLYMARKET_PRIVATE_KEY` (EVM wallet key, 0x-prefixed hex)

### Gap 3 — Fill Receipt and True Reconciliation ✓
**Complete. Implemented alongside Gap 1 in place_order.py.**

**What it is**: The `reconciliation_attribution` agent currently operates in
dry-run mode — it records what was planned but has no actual fill data to
compare against.

**What was implemented**:
- `_poll_order_fill()` in `place_order.py` — polls CLOB every 5s up to 30s
  for terminal fill state (filled / cancelled / expired)
- Fill state merged into `OrderResult` with `fill_price`, `fill_size`, and
  `slippage_pct` computed from planned vs actual price
- `11-execution-fill.json` written after every live order
- Reconciliation stage reads fill receipt when present and compares
  planned vs executed; records slippage in memory entry

### Gap 2 — Open Position Monitoring Agent ✓
**Complete. Run via `python3 main.py monitor-positions`.**

**What it is**: Once a position is open, nothing watches it. There is no agent
that checks whether a position should be closed early, whether the thesis has
been invalidated by new information, or whether the market is approaching
resolution.

**What was implemented**:
- `polymarket_agent/position_monitor.py` — reads open positions from
  performance summary JSON, fetches current YES prices from Gamma API,
  computes unrealized PnL and applies rule-based pre-filters
- Rule triggers: close if price ≥ 0.80 (near resolution), close if loss ≥ 30%,
  add if price moved favorably by < 5% since entry (improved entry opportunity)
- Default recommendation is hold pending AI review
- `python3 main.py monitor-positions --output <path>` CLI helper
- Writes timestamped JSON + `position_monitor_latest.json` to `data/performance/`
- Output is the input context for a future AI-driven position_monitor agent

### Gap 4 — Structural Alpha Execution ✓
**Complete. Run via `python3 main.py execute-multi-leg`.**

**What it is**: The `structural_alpha` agent can identify cross-market pricing
inconsistencies (e.g. three mutually exclusive outcomes priced above 1.00 total).
But the execution layer did not know how to place a multi-leg trade.

**What was implemented**:
- `polymarket_agent/structural_alpha.py` — reads `06-structural-alpha.json`,
  executes each leg sequentially via `_submit_limit_order` + `_poll_order_fill`
- Safe-stop on first leg failure: remaining legs are skipped, partial state
  is recorded; no automatic unwinding
- `python3 main.py execute-multi-leg --structural-alpha-json <path> --output <path>`
- Leg schema: `{market_slug, token_id, side, size, price}` per leg
- Returns `MultiLegResult` with per-leg `LegResult` including fill price,
  fill size, slippage_pct, and status
- Dry-run by default; `--live` or `POLYMARKET_LIVE_TRADING=true` enables live

---

## Development Sequence Toward Full Autonomy

```
Stage 4 ✓ DONE
  Full 12-stage research chain in dry-run mode — no orders placed
  Automatic market selection (Stage 0) with deduplication
  Batch-run: assess multiple markets per session (Gap 7 ✓ DONE)
  Self-improving strategy layer (docs/strategy.md)
  ↓
Stage 5 ✓ DONE — Research quality improvements
  Gap 5 ✓: Evidence connectors — CoinGecko, ESPN, FRED, CourtListener, NOAA
  Gap 8 ✓: Persistent memory store — data/memory/memory_log.jsonl
            family memories injected into 00-history.md each run
  ↓
Stage 6 ✓ DONE — Risk controls
  Gap 6 ✓: Risk limits config (risk_limits.json)
            Per-family exposure caps, max open positions, daily loss circuit-breaker
            Stage 0b in runner aborts run before any AI stage if limits breached
            00-risk-check.json artifact written every run
  ↓
Stage 7 ✓ DONE — Live trading infrastructure
  Gap 1 ✓: Authenticated CLOB order placement
            py-clob-client integration; POLYMARKET_PRIVATE_KEY env var
            python3 main.py place-order --execution-json <path>
            Runner auto-places when POLYMARKET_LIVE_TRADING=true
            Fill receipt written to 11-execution-fill.json
  Gap 3 ✓: Fill receipts + true reconciliation (polling)
            _poll_order_fill() polls CLOB every 5s up to 30s after submission
            Fill state merged into OrderResult; slippage computed
            Reconciliation agent reads 11-execution-fill.json when present
  ↓
Stage 8 ✓ DONE — Position lifecycle management
  Gap 2 ✓: Open position monitoring agent
            python3 main.py monitor-positions --output <path>
            Fetches current prices, computes unrealized PnL
            Rule-based pre-filter: hold / close / reduce / add
            Writes position_monitor_latest.json to data/performance/
  ↓
Stage 9 ✓ DONE — Advanced execution strategies
  Gap 4 ✓: Structural alpha multi-leg execution
            python3 main.py execute-multi-leg --structural-alpha-json <path>
            Sequential leg execution with fill polling
            Safe-stop on failure; partial state recorded for review
  ↓
Stage 10 ✓ DONE — Testing and production hardening
  77 unit tests passing across 4 test files
  All new modules: structured Python logging (logger = logging.getLogger(__name__))
  Bugs fixed:
    - position_monitor: "add" rule now uses drop-from-entry % (same for YES/NO)
    - structural_alpha: dry-run mode previews plans without token_id validation
  Run tests: python3 -m pytest tests/ -v
  ↓
Stage 11 — Fully autonomous ← CURRENT FRONTIER
  scan → select → research → execute → monitor → reconcile → learn
  All gaps implemented. Remaining work: live validation with
  POLYMARKET_LIVE_TRADING=true and connecting monitor-positions output
  to the execution agent for auto-close of resolved positions.
```

## Testing

Unit tests live in `tests/`. Install pytest once (`pip install pytest`) then run:

```bash
python3 -m pytest tests/ -v
```

### Test coverage by module

| File | Tests | What is covered |
|------|-------|-----------------|
| `tests/test_risk_limits.py` | 21 | `load_risk_limits`, `load_exposure_snapshot`, `check_risk_limits`, `write_risk_check_artifact` — all limit types, partial configs, malformed JSON |
| `tests/test_place_order.py` | 17 | `_extract_order_params`, `place_order` dry-run and live (mocked CLOB), `write_order_result` — validation errors, client errors, slippage calculation |
| `tests/test_position_monitor.py` | 20 | `_rule_based_recommendation`, `_load_open_positions`, `monitor_positions`, `write_monitor_result` — all thresholds, YES/NO price handling, API failure fallback |
| `tests/test_structural_alpha.py` | 19 | `_extract_legs`, `execute_multi_leg` dry-run and live, safe-stop on failure, partial fills, `write_multi_leg_result` |

### Logging

All new modules (`risk_limits`, `place_order`, `position_monitor`, `structural_alpha`) use
Python's standard `logging` module via `logger = logging.getLogger(__name__)`. To enable
verbose output during a run, set the log level in your entry point or environment:

```bash
PYTHONPATH=. python3 -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from polymarket_agent.risk_limits import load_risk_limits
print(load_risk_limits('risk_limits.json'))
"
```

Or configure logging in `main.py`:
```python
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s %(message)s',
)
```
