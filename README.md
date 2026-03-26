# Polymarket Agent

[![CI](https://github.com/jhamant/polymarket_agent/actions/workflows/ci.yml/badge.svg)](https://github.com/jhamant/polymarket_agent/actions/workflows/ci.yml)

A document-first Polymarket trading agent. Python stays thin; every decision
is made by an AI agent reading and writing files on disk. The filesystem is
the contract between stages.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full pipeline design,
development sequence, and gap tracking.

**Pipeline summary:**

```
Stage 0   Market selection + risk check (Python, no AI)
Stage 1   Market data context
Stage 2   Account performance
Stage 3   Strategy update
Stage 4   Quality gate
Stage 5   Rules resolution + structural alpha
Stage 6–7 Specialist research → assessor → reviewer
Stage 8   Execution planning
Stage 9   Live order placement (requires POLYMARKET_LIVE_TRADING=true)
Stage 10  Reconciliation + memory
```

## Quickstart

```bash
# Dry-run: scan markets, auto-select best, run full pipeline
python3 main.py

# Scan and print ranked quality table
python3 main.py scan-markets --limit 200 --top 20 --print-table

# Batch: assess up to 5 markets in one session
python3 main.py batch-run --max-markets 5

# Monitor open positions
python3 main.py monitor-positions --output data/performance/monitor.json
```

Run `python3 main.py --help` for all options.

## Development Setup

```bash
pip install -e ".[dev]"
```

This installs the package in editable mode plus `pytest` and `ruff`.

## Testing

```bash
pytest
```

77 unit tests covering: `risk_limits`, `place_order`, `position_monitor`,
`structural_alpha`. All tests use mocks — no live API calls required.

## Linting

```bash
ruff check .        # lint
ruff format .       # format
```

The CI pipeline runs both on every push and pull request.

## Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `POLYMARKET_LIVE_TRADING` | No | `false` | Enable live CLOB order placement |
| `POLYMARKET_PRIVATE_KEY` | If live | — | EVM wallet key (0x-prefixed hex) |
| `POLYMARKET_MARKET_LIMIT` | No | `10` | Markets fetched per scan |
| `POLYMARKET_POSITION_SIZE_USD` | No | `5` | Default order size |
| `POLYMARKET_MARKET_SLUG` | No | — | Override auto-selection (testing only) |
| `POLYMARKET_ACCOUNT_ADDRESS` | No | — | Wallet address for performance data |
| `FRED_API_KEY` | No | — | Enables `evidence-macro` FRED connector |
| `COURTLISTENER_API_TOKEN` | No | — | Enables `evidence-legal` connector |

Copy `.env.example` (if present) to `.env` and fill in secrets. Never commit `.env`.

## Risk Limits

Edit `risk_limits.json` to set portfolio-level controls. The runner checks these
before any AI stage runs and aborts with structured violations if limits are
breached. See the schema in [polymarket_agent/risk_limits.py](polymarket_agent/risk_limits.py).

## References

- Gamma Markets API: <https://docs.polymarket.com/developers/gamma-markets-api/overview>
- CLOB API: <https://docs.polymarket.com/developers/CLOB/introduction>
- py-clob-client: <https://github.com/Polymarket/py-clob-client>
