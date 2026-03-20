# Strategy

Updated: 2026-03-20T22:45:04.114990+00:00

## References

- performance position CSV: /home/jason/Projects/polymarket_agent/data/performance/0x83381bbf137478904f47d4fee82cd3295a0343d7/account_position_performance.csv
- performance trade ledger CSV: /home/jason/Projects/polymarket_agent/data/performance/0x83381bbf137478904f47d4fee82cd3295a0343d7/account_trade_ledger.csv
- performance summary JSON: /home/jason/Projects/polymarket_agent/data/performance/0x83381bbf137478904f47d4fee82cd3295a0343d7/latest_summary.json

## Performance Snapshot

- configured: True
- resolved proxy wallet: 0x83381bbf137478904f47d4fee82cd3295a0343d7
- trade count: 1167
- open positions: 1
- closed positions: 50
- estimated total pnl: 3535.972606
- closed realized pnl total: 3557.070741
- open mark-to-market pnl total: -21.098135
- closed win rate: 100.0
- equity: 1466.358931
- cash balance: 1455.519446
- positions value: 10.839485

## What Is Working

- Crypto and on-chain markets: est_total_pnl=3535.972606, realized=3557.070741, open_mtm=-21.098135, positions=256

## What Is Not Working

- none yet

## Strategy Directives

- Estimated total account PnL is non-negative. Keep sizing disciplined and avoid relaxing evidence standards.
- Closed-position win rate is at least 50%. Maintain the edge threshold, but keep family-level discipline.
- Current strongest family is Crypto and on-chain markets. Prioritize it only when the market context and evidence match the existing winning pattern.
