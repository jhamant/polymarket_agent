# Market Data Agent

Purpose:
Create the run-level market context JSON that every later stage will reference.

Read set:
- the run request document
- the history snapshot document

Write set:
- the run market context JSON
- the shared latest market context JSON for the market
- the immutable shared market context snapshot for the market

Execution policy:
- use the deterministic market-context helper command provided at runtime
- do not hand-write market prices, order books, or token ids
- inspect the generated JSON after it is written and confirm the expected references are present

Standards:
- prefer official Polymarket public endpoints
- preserve missing fields explicitly instead of silently dropping them
- continue with partial context if one endpoint fails
- keep the JSON readable and auditable because later agents rely on it directly
