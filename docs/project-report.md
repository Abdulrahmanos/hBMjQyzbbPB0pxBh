# Project setup and interpretation

The supplied Apziva project asks for a configurable Bitcoin DCA/ATR hybrid, adaptive strategy choices, Google Sheet configuration, alerts, a weekly Gmail report and deployable operation. It does not require LSTM. The owner requested the LSTM comparison as an extension and waived the public YouTube deliverable. The [requirements audit](../reports/submission/requirements.md) distinguishes code, tested demonstrations and unverified external operation.

## Architecture

`market_data` fetches completed public BTC-USD candles; `features` constructs causal indicators. `research` and `backtest` compare historical policies. `paper_engine` applies the operational policy using Decimal arithmetic and persistent SQLite orders, fills, inventory, reservations and risk state. `integrations` runs configuration, reporting and bounded advice separately from protective quote ticks. `research_lstm` is a separate offline experiment and is never imported by the paper trader.

Google Sheet controls are read hourly with validated cached fallback. Local risk limits and initial funding are outside remote authority. The optional LLM module produces restricted shadow suggestions, with validation, quotas, timeout and deterministic fallback. It has not been demonstrated to improve performance and cannot place funded orders. Real Sheet OAuth and real LLM calls remain unverified.

## Trading and reporting

DCA buys over time or after a configured dip. Active entries use trend, breakout and volume checks; ATR determines the stop distance. Operational profit targets account for modeled transaction costs. Portfolio controls can pause new purchases, reduce exposure or request exits; gaps may produce losses beyond a trigger. The [formula guide](modeling-guide.md) derives these calculations.

Committed buys and sells enqueue Telegram messages under stable fill IDs. Gmail reports summarize period-filtered equity, returns, drawdown, fees and trades, every Monday 09:00 Asia/Riyadh. Persistence avoids duplicate ordinary delivery on restart. Ambiguous external timeouts are held for review because the providers cannot guarantee exactly-once delivery. The [notification guide](notification-setup.md) explains account setup and real tests.

## Historical evidence

The earlier nine-policy research and the later LSTM experiment use different protocols and are not directly comparable. The LSTM study is a bounded weekly flat-to-flat experiment with a small, strictly filtered sample. It chose reserve in every retained test week, matching a model-free cash policy. Its edge over losing rules is not evidence of learned profitable market timing. Previously inspected history prevents calling this a pristine independent holdout. No LSTM has been deployed.

The [real-data log](../reports/real-data-evaluation/review.md) preserves actual observations from 11–22 October across separate accounts and demonstrations. Standby/hibernation caused large gaps. All exported account snapshots reconcile, but the system did not demonstrate continuous 24/7 operation. Missing quotes are not backfilled with historical or synthetic prices.

## Local or cloud startup

Create a Python environment and install `requirements-runtime.lock.txt` for paper operation or `requirements-research.lock.txt` for charts/tests. The CLI provides `validate-config`, `paper-init`, `paper-audit`, `run-paper`, `research` and report commands. `run-paper --cycles 0` runs continuously until stopped. No funded exchange broker is implemented.

`docker compose up -d --build` starts the plain paper service with persistent state. For real notifications after account setup, use `docker compose -f compose.yaml -f compose.notifications.yaml up -d --build`. The notification override uses the same persistent service account path, so choose configuration before creating that account. The image runs as a non-root user with a read-only filesystem, scoped writable state, and a read-only quote-freshness check. Credentials enter through ignored `.env`, never the image or Sheet. Review the cloud operator's secret storage, network, backups and availability before running on AWS or DigitalOcean. No cloud account was provisioned.

GitHub CI runs tests, validates Compose, builds the image and checks the CLI. A workflow file alone is not a successful build; inspect the actual run status. Running Docker on this Windows host was unavailable.

## Scope and limitations

This repository is research and paper simulation. Profitability is not assumed, and favorable backtests do not establish future returns. Real broker execution, market liquidity, personal investment suitability, uninterrupted cloud operation, external notification delivery without credentials, and optional provider accounts are not claimed. The source and reports can be reviewed independently of those account setup steps.
