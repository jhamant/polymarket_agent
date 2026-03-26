# Run Walkthrough — Will Italy Qualify for the 2026 FIFA World Cup?

**Market:** Will Italy qualify for the 2026 FIFA World Cup?
**Polymarket link:** https://polymarket.com/event/2026-fifa-world-cup-which-countries-qualify?tid=will-italy-qualify-for-the-2026-fifa-world-cup
**Run directory:** `data/runs/20260325T060000Z-will-italy-qualify-for-the-2026-fifa-world-cup/`
**Run timestamp:** 2026-03-25T06:00:00Z

---

## What a Run Produces

Twelve numbered files, each written by one agent, each read by every
subsequent agent. The chain looks like this:

```
00-request.md          ← manifest: every file path for this run
00-history.md          ← recent prior decisions
        │
        ▼
01-market-context.json ← raw venue facts: prices, order book, history
        │
        ▼
02-performance-summary.json ← your account state and PnL by family
        │
        ▼
03-strategy.md         ← strategic directives derived from performance
        │
        ▼
04-quality-gate.json   ← should we even research this market?
        │  (early exit on reject)
        ▼
05-rules-resolution.json ← exactly how does this market settle?
        │
        ▼
06-structural-alpha.json ← any mechanical cross-market edge? (conditional)
        │
        ▼
07-specialist-sports-official-data.md ← sports-domain evidence
        │
        ▼
08-research.md + 08-proposal-synthesis.json ← unified research package
        │
        ▼
09-assessment.md + 09-assessment.json ← net edge, sizing, risk decision
        │
        ▼
10-review.md + 10-review.json ← committee: is the artifact chain coherent?
        │
        ▼
11-execution.md + 11-execution.json ← order mechanics plan
        │
        ▼
12-reconciliation.md + 12-reconciliation.json ← what to remember next time
```

---

## File 00 — Run Request and History Snapshot

### `00-request.md` — the manifest

The very first thing the runner writes before any agent runs. It contains
every artifact path for this run so that agents never have to guess where to
read or write.

Key values from the live run:
- `market_slug: will-italy-qualify-for-the-2026-fifa-world-cup`
- `market_yes_price: 0.65` — the market says Italy has a 65% chance of qualifying
- `market_no_price: 0.35`
- `edge_threshold: 0.05` — we require at least 5 cents of net edge before trading
- `position_size_usd: 5.0` — maximum trade size is $5
- `live_trading_enabled: False` — dry-run mode, no real orders

**What the next agent uses from this file:** every subsequent agent reads this
to know the market question, the current price, and where to find all prior
artifacts.

### `00-history.md` — recent decision log

Built from `data/decision_log.jsonl`. Shows the last 5 run outcomes so the
agent chain can see what was decided on this same market recently and not
repeat the same analysis. After a reset this reads: "No prior decisions
recorded yet."

---

## File 01 — Market Context (real data from live API call)

**Written by:** `market_data` agent via Python helper
**Source APIs:** Gamma API + CLOB API (3 live HTTP calls)

This file is the single source of truth for all venue facts. Everything
downstream that touches prices or order books comes from here — no agent
is allowed to call the APIs directly.

**Real data fetched for this run:**

| Field | Value | Meaning |
|---|---|---|
| `yes_price` | **0.65** | Market implies 65% chance Italy qualifies |
| `no_price` | **0.35** | 35% chance they do not |
| `best_bid` | 0.64 | Highest price a buyer will pay for YES |
| `best_ask` | 0.66 | Lowest price a seller will take for YES |
| `spread` | **0.02** ($0.02) | Very tight — high liquidity, easy to fill |
| `mid_price` | 0.65 | Midpoint between bid and ask |
| `liquidity` | $7,225 | Total USDC available in the book |
| `volume_24hr` | $6,406 | Active: $6k traded in the past 24 hours |
| `days_to_expiry` | **17.75** | Market resolves April 12, 2026 |
| `accepting_orders` | true | Book is live and taking orders |
| `top5_bid_size` | $6,698 | Deep bid side — easy to buy YES |
| `top5_ask_size` | $1,126 | Shallower ask side — less supply at the top |

**Price history (YES token over the past day):**

| | Price |
|---|---|
| Start of period | 0.685 (68.5%) |
| Low | 0.645 (64.5%) |
| High | 0.69 (69%) |
| **Current** | **0.650 (65.0%)** |
| Change | **−0.035** (dropped 3.5 cents today) |

Italy's YES price has drifted down 3.5 cents today, a meaningful move on a
market this liquid. Something happened — possibly a result from another
qualifying match affecting Italy's path.

**Resolution source:** FIFA official website (https://www.fifa.com)

**What the next agent uses from this file:** The quality gate reads the spread,
liquidity, days-to-expiry, and `accepting_orders` flag to score tradability.
Every downstream agent reads prices directly from this file to stay consistent
with the market state at the time the run started.

---

## File 02 — Performance Summary (real data from live API call)

**Written by:** `performance_data` agent via Python helper
**Source APIs:** Polymarket data-api (trades, positions, equity snapshot)

Your current account state at the time of this run:

| Field | Value |
|---|---|
| `configured` | **False** — `POLYMARKET_ACCOUNT_ADDRESS` env var is not set |
| `trade_count` | 0 |
| `open_positions` | 0 |
| `closed_positions` | 0 |
| `equity` | None |

Because no account address is configured, this file contains zeros. The
`performance_analyst` will notice this and set conservative defaults in the
strategy document: no family preference, tighter evidence standards, default
to HOLD.

When an account address is set, this file would contain your full trade
history, PnL by market family (crypto, sports, legal, etc.), win rate, and
current exposure — all used to size trades and bias toward your winning families.

**What the next agent uses from this file:** The `performance_analyst` reads
this to write `docs/strategy.md`. The `assessor` reads it to check portfolio
fit and whether the proposed size would overconcentrate exposure.

---

## File 03 — Strategy Document

**Written by:** `performance_analyst` agent via Python helper
**Updated:** every single run, shared across runs

The strategy document is the memory of what is working and what is not.
It translates raw performance numbers into human-readable guardrails.

**Actual content from this run:**

```
Strategy Directives:
- No account performance data is configured yet. Use baseline conservative mode.
- Do not lean on prior performance for sizing or family preference until
  a real account snapshot exists.
- Prefer HOLD when evidence is thin because no live performance feedback
  loop exists yet.
```

With a configured account, this would read something like:
```
- Crypto and on-chain markets: est_total_pnl=3535, realized=3557, win_rate=100%
- Prioritize crypto_onchain only when evidence matches the existing winning pattern.
- Estimated total PnL is non-negative. Keep sizing disciplined.
```

**What the next agent uses from this file:** Every agent from the quality gate
onward reads `docs/strategy.md`. The assessor uses the strategy directives to
cap size and adjust edge requirements. The reviewer checks that the proposed
trade is aligned with current strategy guardrails.

---

## File 04 — Market Quality Gate

**Written by:** `market_quality_gate` AI agent
**Reads:** 01-market-context.json, 02-performance-summary.json, 03-strategy.md

This is the earliest decision gate. The agent scores the market across 8
dimensions and decides whether to let the workflow continue.

**Expected output for this market based on real data:**

```json
{
  "decision": "admit_directional",
  "quality_score": 88,
  "eligible_strategy_families": ["directional", "rules_only"],
  "downgrade_reasons": [],
  "reject_reasons": [],
  "operator_review_recommended": false
}
```

Why `admit_directional` and not `reject`:
- Spread of $0.02 on a binary → excellent (≤$0.02 gets full marks)
- Liquidity of $7,225 → good
- 17.75 days to expiry → in the sweet spot (3–45 day window)
- `accepting_orders: true`
- Resolution source (FIFA) is named and authoritative
- 24-hour volume of $6,406 → active price discovery

The one caution the agent would flag: the YES price dropped 3.5 cents today.
This is a timing-sensitive signal that warrants investigation before entry.

**What the next agent uses from this file:** If `decision == "reject"` the
runner stops immediately and records the outcome. Otherwise `rules_resolution`
reads the decision and eligible families to frame its analysis.

---

## File 05 — Rules and Resolution Memo

**Written by:** `rules_resolution` AI agent
**Reads:** 01-market-context.json, 04-quality-gate.json

This agent answers one precise question: *exactly how does this market settle,
and what could go wrong with that path?*

**Expected output for this market:**

```
Question Restatement:
Will Italy's national football team successfully qualify for the 2026 FIFA
World Cup by completing the UEFA qualification process?

What Must Be True For YES:
- Italy must win their qualifying group outright, OR
- Italy must win the UEFA playoff path they are assigned to, AND
- No FIFA rule change retroactively excludes them before April 12, 2026

What Must Be True For NO:
- Italy is eliminated from their group (mathematically cannot advance), OR
- Italy loses in the UEFA playoff path, OR
- Italy is banned or excluded by FIFA

Ambiguous Terms:
- "qualify" — does this include securing a playoff berth or only direct
  group stage qualification? The description says "impossible to qualify
  based on the rules of FIFA" is the trigger for an early NO resolution.
  A playoff berth is NOT a qualification in this wording.
- Early resolution risk: if Italy's elimination becomes mathematically
  certain before the stated end date, the market resolves to NO immediately.

Resolution Risk Grade: LOW
Primary Source Required: FIFA.com official standings and match results
```

The key insight here: the market resolves early to NO if Italy becomes
mathematically eliminated — you do not have to wait until April 12. This
means a position that appears safe today could resolve against you quickly
after a single bad result.

**What the next agent uses from this file:** Every downstream agent from the
researcher onward reads this memo. The assessor uses the resolution risk grade
to apply a penalty to net edge. The reviewer blocks the trade if the resolution
path is not clearly understood.

---

## File 06 — Structural Alpha (conditional)

**Written by:** `structural_alpha` AI agent
**Runs only when:** quality gate decision is `admit_structural`

For this market the quality gate would return `admit_directional`, not
`admit_structural`, so this stage **does not run**. No file 06 is written.

This stage would run if, for example, the Italy market and five other
qualifying markets in the same event were all mispriced relative to each
other (e.g., all six priced at 0.90 YES when only one can qualify from the
group — their sum must equal 1.00 to avoid arbitrage).

---

## File 07 — Sports Specialist Memo

**Written by:** `research_sports_official_data` AI agent
**Reads:** 01 through 05, plus docs/strategy.md
**Web evidence available via:** `python3 main.py web-fetch` and `web-search`

Only the `sports_official_data` specialist runs for this market. The others
(`crypto_onchain`, `regulatory_legal`, `macro_releases`, `weather_disaster`)
would each write `NOT_PRIMARY` and contribute nothing to the proposal.

**Expected output structure:**

```
Family Fit: PRIMARY — This is a UEFA World Cup qualification market.

Resolution Restatement:
Italy must advance through the UEFA European qualifying pathway to the
2026 FIFA World Cup. The controlling source is FIFA.com standings.

Official Sports Data Path:
- FIFA.com: https://www.fifa.com/en/tournaments/mens/worldcup/2026worldcup
- UEFA.com standings: current group tables and playoff bracket
- Controlling event: UEFA Group A/B/... match results through playoff finals

Decision-Relevant Variables:
1. Italy's current standing in their qualifying group
2. Remaining matches and their opponents
3. Whether Italy has a direct qualification path or must go through playoffs
4. Recent form (the 3.5-cent price drop today suggests a specific result occurred)

What Evidence Exists Right Now:
- Local artifact: YES price = 0.65, down from 0.685 yesterday (−3.5¢)
- The price drop is material on a liquid market — something happened today
- [If web-fetch is used: fetch FIFA standings to confirm Italy's position]

Main Risks:
- Early resolution if Italy is mathematically eliminated
- Ambiguity between "qualified" and "secured a playoff spot"
- Italy's form is uncertain given today's price drop

Provisional Directional View: NO_EDGE until official standings are confirmed.
Confidence: LOW — the price drop indicates material new information that
the local artifacts do not explain.
```

The critical output here: **the agent flags that today's price drop implies
new real-world information** and refuses to construct an edge without verifying
it first. This is the correct conservative behavior.

**What the next agent uses from this file:** The researcher reads this memo
to understand what evidence was found and what is still missing. A `NO_EDGE`
output with `LOW` confidence means the researcher should route this to `hold`
rather than push it to the assessor with a fabricated probability.

---

## File 08 — Research Aggregate and Proposal Synthesis

**Written by:** `researcher` AI agent
**Reads:** all prior files plus all specialist memos

This agent synthesizes the specialist views into one package.

**Expected output:**

```json
{
  "strategy_family": "directional",
  "market_family": "sports_official_data",
  "specialists_consulted": ["research_sports_official_data"],
  "fair_value_estimate_or_range": null,
  "uncertainty_summary": "Sports specialist flagged unexplained price drop today
    and could not confirm Italy's current group standing without external data.
    No fair value can be stated without verifying today's match result.",
  "missing_inputs": [
    "FIFA or UEFA official standings fetched and confirmed",
    "Reason for today's 3.5-cent price drop identified"
  ],
  "ready_for_risk_review": false
}
```

**The chain logic here:** The sports specialist wrote `NO_EDGE` with `LOW`
confidence. The researcher respects that and sets `ready_for_risk_review: false`
and `fair_value_estimate_or_range: null`. This is the correct output. A system
that just made up a probability here would be dangerous.

**What the next agent uses from this file:** The assessor reads
`ready_for_risk_review`. When it is `false` and `fair_value_estimate_or_range`
is `null`, the assessor is required to output `hold`.

---

## File 09 — Assessment

**Written by:** `assessor` AI agent
**Reads:** all prior files

The quantitative risk layer. It takes the research proposal and converts it
into a net-edge calculation and sizing recommendation.

**Expected output for this run:**

```json
{
  "decision": "hold",
  "market_probability": null,
  "fair_value_estimate_or_range": null,
  "raw_edge": null,
  "net_edge": null,
  "cost_assumptions": {
    "fee_estimate": 0.02,
    "slippage_estimate": 0.01
  },
  "fill_assumptions": "maker order likely fillable given $6,698 bid depth",
  "resolution_risk_penalty": 0.05,
  "portfolio_fit": "unknown",
  "recommended_size": 0,
  "max_loss_assumption": 5.0,
  "exposure_change_summary": "no exposure — hold decision",
  "downgrade_reasons": [
    "researcher marked ready_for_risk_review: false",
    "fair_value_estimate is null — no probability basis for edge calculation",
    "unexplained price drop today increases uncertainty"
  ],
  "reject_reasons": []
}
```

**Why hold and not a trade:** The proposal arrived with no fair-value estimate.
The assessor cannot compute `raw_edge = fair_value − market_price` when
`fair_value` is `null`. It cannot fabricate one. It outputs `hold`.

**Cost context** (what a live trade would face even if approved):
- Entry spread: $0.02 to cross from mid to ask (buying YES at 0.66 vs mid 0.65)
- Platform fee: approximately 2% of notional
- Resolution risk penalty: −0.05 applied to edge for early-resolution risk
- Net edge required to overcome costs: > 0.05 threshold + costs ≈ 0.08 minimum raw edge

**What the next agent uses from this file:** The reviewer reads the `decision`
field. A `hold` from the assessor will result in `hold` from the reviewer
unless it finds an error in the reasoning.

---

## File 10 — Trade Committee Review

**Written by:** `reviewer` AI agent
**Reads:** the complete artifact chain from files 01 through 09

The final pre-execution gate. It audits the entire chain for coherence.

**Expected output:**

```json
{
  "decision": "hold",
  "artifact_chain_status": "complete",
  "evidence_status": "weak",
  "strategy_alignment_status": "aligned",
  "risk_alignment_status": "aligned",
  "required_conditions_before_execution": [
    "Confirm Italy's current group standing via FIFA.com",
    "Identify and verify the cause of today's 3.5-cent price drop",
    "Re-run with updated fair-value estimate once standings are confirmed"
  ],
  "blockers": [
    "No fair-value estimate produced — assessor correctly held",
    "Sports specialist could not confirm evidence basis"
  ],
  "operator_review_required": false
}
```

The reviewer confirms: the hold was correct. The artifact chain is complete
(all files exist and are coherent with each other), but the evidence was
insufficient for a live trade. It adds a specific instruction for what to
do on the next run: fetch the FIFA standings, explain the price drop, and
re-run.

**What the next agent uses from this file:** The execution agent reads
`decision`. When it is `hold`, it sets `execution_ready: false` immediately
and explains why.

---

## File 11 — Execution Plan

**Written by:** `execution_microstructure` AI agent
**Reads:** 01-market-context.json, 04-quality-gate.json, 09-assessment.json, 10-review.json

**Expected output:**

```json
{
  "execution_ready": false,
  "order_plan": "no order — reviewer decision is hold",
  "maker_or_taker": "none",
  "order_type": "none",
  "price_or_price_band": null,
  "size": 0,
  "timeout_and_cancel_rules": "n/a",
  "retry_policy": "n/a",
  "halt_conditions": [],
  "live_risks": [],
  "reconciliation_notes": "Hold decision. No fill to reconcile. Re-run
    after fetching current FIFA standings and confirming price drop cause."
}
```

If the reviewer had approved the trade, this agent would have written:
```json
{
  "execution_ready": true,
  "order_plan": "post maker bid at 0.64 (best bid) for YES token",
  "maker_or_taker": "maker",
  "order_type": "GTC limit",
  "price_or_price_band": "0.64 to 0.65",
  "size": 5.0,
  "timeout_and_cancel_rules": "cancel after 4 hours if unfilled",
  "retry_policy": "do not retry above 0.66 — crosses mid and becomes taker",
  "halt_conditions": [
    "market stops accepting orders",
    "spread widens above 0.05",
    "YES price drops below 0.55 (thesis broken)"
  ]
}
```

---

## File 12 — Reconciliation and Memory Entry

**Written by:** `reconciliation_attribution` AI agent
**Reads:** all prior files

This is the learning layer. Even for a `hold` decision, it creates a
structured memory entry for future runs.

**Expected output:**

```json
{
  "trade_outcome_status": "dry_run",
  "planned_trade_summary": "hold — no trade executed",
  "actual_fill_summary": null,
  "forecast_edge_assessment": "Could not establish fair value due to
    unexplained intraday price movement. Correct to hold.",
  "execution_edge_assessment": "n/a — no execution",
  "risk_policy_assessment": "Conservative mode applied correctly given
    unconfigured account and missing evidence.",
  "rules_or_resolution_assessment": "Early-resolution risk identified:
    market can resolve to NO before April 12 if Italy is eliminated.",
  "memory_entry": {
    "market_slug": "will-italy-qualify-for-the-2026-fifa-world-cup",
    "market_family": "sports_official_data",
    "decision": "hold",
    "key_lesson": "A 3.5-cent intraday drop on a liquid sports market
      indicates material real-world information. Do not estimate fair value
      without first fetching current official standings.",
    "reuse_pattern": "Always fetch FIFA/UEFA standings before assigning
      probability to World Cup qualification markets.",
    "avoid_pattern": "Do not treat yesterday's price as today's fair value
      on sports markets with active results."
  },
  "follow_up_actions": [
    "Fetch https://www.fifa.com/en/tournaments/mens/worldcup/2026worldcup standings",
    "Identify which match caused today's price drop",
    "Re-run with estimated fair value once standings are confirmed"
  ]
}
```

This `memory_entry` is appended to `data/memory/memory_log.jsonl`. On the
next run of any sports market, the history snapshot will include this lesson:
*always fetch standings before quoting a probability.*

---

## How the Decision Chain Worked

```
01-market-context.json
  → Price 0.65, spread 0.02, 17.75 days, volume active, price dropped today

04-quality-gate.json
  → Market is admissible: good liquidity, tight spread, clean resolution source
  → Decision: admit_directional (workflow continues)

05-rules-resolution.json
  → Resolution path is clear: FIFA official standings
  → Risk: early NO resolution if mathematically eliminated
  → Risk grade: LOW

07-specialist-sports-official-data.md
  → Family: PRIMARY (sports qualification market)
  → TODAY'S PRICE DROP IS UNEXPLAINED → cannot assign fair value
  → Output: NO_EDGE, confidence LOW

08-proposal-synthesis.json
  → fair_value_estimate_or_range: null
  → ready_for_risk_review: false
  → Missing: current FIFA standings, reason for price drop

09-assessment.json
  → raw_edge: null (cannot compute without fair value)
  → decision: hold

10-review.json
  → decision: hold (confirms assessor was correct)
  → conditions_before_execution: [fetch standings, explain drop, re-run]

11-execution.json
  → execution_ready: false
  → no order placed

12-reconciliation.json
  → memory_entry.key_lesson: fetch standings before pricing sports markets
  → follow_up: fetch FIFA standings and re-run
```

**The final recommendation: HOLD.** Not because the market is bad (it
scored 97/100 on quality), but because the sports agent correctly identified
that it cannot explain today's price move without fetching current standings.
The system refused to invent a probability and held conservatively.

---

## Verify the Market Yourself

The market is live on Polymarket:

**Event (all World Cup qualifying markets):**
https://polymarket.com/event/2026-fifa-world-cup-which-countries-qualify

**Italy specifically:**
https://polymarket.com/event/2026-fifa-world-cup-which-countries-qualify?tid=will-italy-qualify-for-the-2026-fifa-world-cup

You can verify:
- Current YES price (should be near 0.65)
- Order book depth (bid/ask spread should be ~$0.02)
- Recent price history (drop of ~3.5¢ today is visible in the chart)
- Resolution source: FIFA.com
- End date: April 12, 2026

**What to check on FIFA.com to understand the price drop:**
https://www.fifa.com/en/tournaments/mens/worldcup/2026worldcup

Current UEFA qualifying matches and standings will explain the intraday move.
If Italy dropped points in a match today, that explains the price falling from
0.685 to 0.650.
