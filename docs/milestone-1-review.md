# Milestone 1 review

Completed on 2026-10-11. Development stops here until the owner says **continue**.

## Delivered

- A local Python package with `validate-config`, `init-db`, and `status` commands.
- Strict, non-secret JSON configuration with paper-only execution and unsupported integration flags rejected.
- A simulated cash portfolio persisted in SQLite, with configuration snapshots and initialization event history.
- Decimal cash allocation that preserves every cent. Reinitializing existing state does not reset balances, and changed initial funding requires a separate experiment database.
- Trading and evaluation rules covering dip references, spending caps, separate inventory, swing sizing/exits, portfolio pauses, realistic backtests, and the planned LLM comparison.
- Setup instructions, credential placeholders, ignored runtime artifacts, and the milestone roadmap.

## Verified result

| Bucket | Simulated USD cash | BTC |
| --- | ---: | ---: |
| DCA | 7,000.00 | 0 |
| Swing | 2,000.00 | 0 |
| Reserve | 1,000.00 | 0 |
| Total | 10,000.00 | 0 |

The default configuration checksum is `ca1e2a7a28eb489118f192de427da202687fcee6a21daf235a1583db15c1e3d2`.

All 12 automated tests passed. They cover cash conservation and rounding, malformed and inconsistent settings, duplicate JSON keys, non-finite values, configuration immutability, persistence across repeated initialization, changed funding, missing databases, unrelated databases, and unsupported database schema versions. CLI validation, initialization, repeated initialization, help, and status were also exercised.

An early test run found SQLite connections in the test fixtures were left open on Windows. The fixtures now close those connections explicitly; application connections already used explicit cleanup. Windows sandbox access to private temporary directories also required approved test execution outside the sandbox.

## Inspect it yourself

From the project folder in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m bitcoin_agent status
.\.venv\Scripts\python.exe -m bitcoin_agent validate-config
```

The actual persisted state is `var/paper.sqlite3`. These commands review simulated state; there is no running market or trading loop in this milestone.

## Next milestone, after continue

Implement historical market-data ingestion, data-quality checks, technical features, and benchmark backtests. The next review will include actual data coverage, charts, baseline results after simulated costs, and the limitations of those results. The hybrid trading engine follows in milestone 3.
