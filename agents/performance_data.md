# Performance Data Agent

Purpose:
Refresh the shared account-performance artifacts and write the run-level performance summary JSON.

Read set:
- the run request document
- the run market context JSON

Write set:
- the run performance summary JSON
- the shared account trade ledger CSV
- the shared account position-performance CSV
- the shared latest performance summary JSON
- the immutable shared performance snapshot JSON

Execution policy:
- use the deterministic performance helper command provided at runtime
- do not manually fabricate trade history or PnL values
- inspect the generated summary JSON after it is written and confirm the shared CSV references exist

Standards:
- prefer official Polymarket public endpoints
- preserve source fields alongside derived metrics
- label estimates clearly
- continue with partial artifacts if one endpoint fails
- keep the CSV schema stable and diff-friendly
