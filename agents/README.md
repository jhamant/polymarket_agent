# Revised Polymarket Runtime Agent Set

This pack updates the uploaded runtime agents so they match the trading workflow we discussed:

1. **Market data normalization**
2. **Market-quality gate**
3. **Strategy-family routing**
4. **Rules / resolution review**
5. **Structural alpha review when relevant**
6. **Market-family research**
7. **Proposal synthesis**
8. **Quant risk / assessment**
9. **Trade committee review**
10. **Execution microstructure**
11. **Reconciliation, attribution, and structured memory**

## Included updated original filenames

- `market_data.md`
- `performance_data.md`
- `performance_analyst.md`
- `researcher.md`
- `research_macro_releases.md`
- `research_regulatory_legal.md`
- `research_sports_official_data.md`
- `research_weather_disaster.md`
- `assessor.md`
- `reviewer.md`

## Added agents that were missing from the original set

- `market_quality_gate.md`
- `rules_resolution.md`
- `structural_alpha.md`
- `execution_microstructure.md`
- `reconciliation_attribution.md`

## Design changes from the original prompts

The earlier agent set was too close to a simple sequence of:
market data -> topic research -> assessment -> review.

This revision changes the system so it behaves like a trading system instead of a research workflow:

- **Market quality comes before research.** Poor markets should be rejected before expensive reasoning begins.
- **Strategy-family routing comes before market-family enthusiasm.** Structural opportunities and rules ambiguity are assessed before topic specialists are asked for a view.
- **Rules / resolution risk is explicit.** Ambiguous wording and unclear measurement paths become hard penalties.
- **Assessment is net-EV and portfolio-aware.** Fees, slippage, fill uncertainty, exposure concentration, and correlation all matter.
- **Execution is its own agent.** Approval does not equal an order plan.
- **Post-trade learning is structured.** Future agents should consume measured lessons, not narrative drift.

## Recommended runtime artifact flow

- `run_request.md`
- `run_market_context.json` via `market_data.md`
- `run_market_quality.md` and `run_market_quality.json` via `market_quality_gate.md`
- `run_rules_resolution.md` and `run_rules_resolution.json` via `rules_resolution.md`
- optional `run_structural_alpha.md` and `run_structural_alpha.json` via `structural_alpha.md`
- zero or more market-family specialist memos
- `run_aggregate_research.md` and `run_proposal_synthesis.json` via `researcher.md`
- `run_performance_summary.json` via `performance_data.md`
- `docs/strategy.md`, `run_strategy_snapshot.md`, and memory artifacts via `performance_analyst.md`
- `run_assessment.md` and `run_assessment.json` via `assessor.md`
- `run_review.md` and `run_review.json` via `reviewer.md`
- `run_execution_plan.md` and `run_execution_plan.json` via `execution_microstructure.md`
- `run_reconciliation.md`, `run_reconciliation.json`, and structured memory entries via `reconciliation_attribution.md`

## Intended decision vocabulary

Use consistent decisions across agents:

- `reject`
- `paper_only`
- `hold`
- `route_directional`
- `route_structural`
- `route_rules_only`
- `approve_for_committee`
- `approve_for_execution_planning`
- `execution_ready`
- `do_not_execute`

## One important behavioral rule

No single market-family research agent is allowed to create a trade by itself.
A live trade should only reach execution after the market-quality gate, rules review, proposal synthesis, quantitative risk assessment, and committee review all agree that the trade is admissible.
