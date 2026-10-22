# Trading and evaluation methodology

The foundation, offline research simulator, and persistent operational paper engine are implemented through milestone 3. Integrations and deployment remain later milestones. Defaults are experiment inputs, not claims of profitability. The milestone reports record observed limitations and separate historical research from artificial control tests.

## Agreed research premise

We assume Bitcoin remains operational and tradable. We evaluate whether a configurable DCA and trading system improves returns and drawdown relative to simpler strategies, including during prolonged declines.

## Scope

Use spot BTC-USD with a fixed starting cash budget, no leverage, and no external deposits during an experiment. The first operational system will paper trade using real exchange prices. Maintain separate DCA and swing holdings with a common portfolio valuation and an explicit reserve. A swing exit cannot consume DCA inventory.

Start with one exchange, hourly completed candles, and one swing strategy. Keep the signal evaluation schedule separate from active-position protection. No price-forecast model is required for the first hybrid; any later model must improve out-of-sample decisions relative to the deterministic baseline.

## Budget and DCA rules

- Initial DCA/swing/reserve fractions sum to one. DCA and swing cash are rounded down to cents; reserve receives the residue.
- Budget means all available experiment capital, not an unlimited recurring contribution. Tranche sizes reference the original DCA allocation, and cash availability always limits spending.
- After valid market data and indicator warm-up, scheduled DCA makes its first eligible purchase and then follows the configured interval.
- Optional dip buys reference the price of the last **filled DCA buy**. Before that first buy there is no dip reference.
- Scheduled and dip purchases share a cooldown and rolling seven-day spending cap, including fees. If both trigger at one decision point, make at most one purchase.
- Do not replay missed scheduled purchases in a burst after an outage. An eligible restart can produce at most one new purchase.
- Under a fixed profile, exhausted DCA spending waits. Following the owner's allocation-research request, milestone 2 also tests weekly target updates that redistribute available cash. Existing inventory is retained unless an explicit defensive exit applies. Targets and actual exposure are reported separately. Operational cross-bucket allocation is still pending milestone 3.

## Swing entry, sizing, and exits

The initial candidate rule requires a fast EMA above the slow EMA, a close above the preceding breakout-lookback highs, and sufficient relative volume. Compute the breakout threshold and volume reference using preceding candles, excluding the decision candle. Indicators and their warm-up periods will be specified and tested in milestone 2.

At a filled entry, set `stop = fill_price - atr_multiplier * ATR_at_signal`. Use an initial stop anchored to entry; the first version will not widen or dynamically move that stop. Position size in BTC is limited by `permitted_dollar_risk / (entry_price - stop_price)`, available swing cash including fees, and the notional cap. Estimated fees and adverse execution must be incorporated into final sizing; a stop cannot guarantee a fixed loss through a gap.

Exit on stop, configured profit multiple, or maximum holding time. Allow one swing position initially. Maintain order intent, submission, partial-fill, fill, cancellation, and failure states when execution is added. Reconcile ambiguous submissions before retrying.

## Portfolio safeguards

Mark equity as all USD cash plus all BTC holdings valued at the latest accepted price. Measure drawdown from the portfolio equity high-water mark. With fixed funding, a configured drawdown breach latches a pause on new buys. Keep exits and monitoring active; require an explicit resume rather than automatically resuming on a price bounce. Pausing is not assumed to cap losses or automatically liquidate DCA holdings.

Milestone 3 adds an explicit operational policy that can liquidate existing inventory. The 25% active-epoch drawdown trigger is supplemented by a 15% rapid-decline trigger, confirmed bear exits, a new-purchase BTC limit of 60%, a trim band above 62%, and a minimum 20% cash constraint. A 30% loss of original capital causes a persistent halt. These are experimental risk preferences, not optimized or guaranteed limits.

Configured automatic recovery requires completed exits (or only untradable dust), a 72-hour cooldown, and seven distinct qualified bullish daily observations. It resets the active risk epoch while retaining lifetime drawdown, initial capital, weekly spending history, and fill cooldowns. An owner liquidation request remains paused until explicit resume. Resume cannot override a capital-floor halt.

Reject new entries when data is stale, incomplete, or invalid. Existing protective exits need an independent execution path. The proposed five-second paper stop monitoring must use fresh quotes; it cannot reuse a quote fetched thirty minutes ago. Any future live adapter must account for exchange-supported protective-order behavior, gaps, partial fills, and connectivity failures.

## Backtest protocol

1. Gather available historical candles covering differing market regimes. Save provenance, UTC timestamps, interval, retrieval time, and data checksum.
2. Check ordering, gaps, duplicates, non-positive prices, OHLC consistency, and volume. Quarantine invalid data and report excluded periods.
3. Compare buy-and-hold, scheduled DCA, dip DCA, and hybrid with the same initial capital and no external deposits. Account for all idle cash and fees.
4. Generate each signal after its candle closes and execute no earlier than the following tradable observation. ATR and other features must use information available at that decision time.
5. Model adverse slippage, fees, and gaps. A stop is not filled automatically at its trigger price. Use finer data for exit evaluation when available; when stop and profit thresholds occur within one unresolved candle, use a documented conservative ordering.
6. Use earlier training/tuning periods, chronological walk-forward validation, and a final untouched holdout. Fit transformations only on the training portion. Purge overlapping outcomes if predictive labels are added.
7. Limit parameter searches. Report sensitivity around selected values and performance by market regime, not just the highest-return combination.
8. Measure final USD equity, BTC holdings, net return, drawdown, turnover, costs, exposure, and trade statistics. Daily risk metrics use a documented crypto calendar convention; any Sharpe estimate should state its risk-free assumption and limited interpretation.

The initial fee and slippage values are configurable scenario assumptions, not verified current exchange fees. Calibrate costs to the intended fee tier and observed execution before interpreting results.

## Regimes and LLM comparison

First implement a deterministic trend/volatility regime controller with hysteresis and a minimum residence time. Allow it to select among validated behavior modes and bounded spending rates, while preserving the original allocation caps and inventory separation.

Milestone 2 implements an initial offline experiment: 47 distinct profiles, nonoverlapping 90-day training trials, up to two years of earlier outcomes, and a return/drawdown objective. A coarse daily regime selects a specialized ranking only after three earlier matching trials; otherwise it uses the global ranking. Targets update weekly. The final 180 days use a frozen mapping. This initial controller has a weekly update interval but does not yet implement full regime hysteresis.

The experiment exposed the difference between cold-start trial outcomes and continuous accumulated inventory. A target change cannot ensure reduced exposure when existing DCA BTC is retained. Future policy work must include portfolio state, explicit inventory exit rules, and resume behavior after a latched pause. Do not treat a strong recent holdout as evidence that this problem has been solved.

Then evaluate an optional lightweight LLM receiving only timestamped numerical features and the approved action/configuration vocabulary. Require structured output, validation, a timeout, a cost budget, and deterministic fallback. It cannot execute orders, raise hard exposure limits, change credentials, or create executable strategy code.

Log model/prompt versions, input timestamps, accepted/rejected proposals, applied configuration version, and decision reasons. Backtests must prevent future knowledge or later market information from entering historical decisions. Compare the hybrid without LLM against the hybrid with LLM on the same data and costs. If historical LLM contamination cannot be excluded, treat forward paper evaluation as the stronger evidence and label historical results accordingly.

LLM-generated feature ideas go through offline implementation and validation before joining the approved library. Free-form live feature generation is outside the initial scope.

## Configuration, reporting, and operations

Milestone 4 implements restricted hourly Sheet reads, full candidate validation, an atomic JSON cache and application between quote ticks. Remote input cannot alter initial funding or the locally owned core risk policy. Malformed remote settings retain the valid cache; a corrupt cache uses a separately validated local baseline. An invalid local baseline prevents startup. Existing stops, inventory and lifetime risk history survive accepted parameter edits. The demo uses a verified local copy of the initial private Sheet values; Python Google OAuth is separate from the Codex connector and remains unconfigured.

Keep credentials outside application configuration. The source brief alternates between `.env` and local JSON credential examples; this project standardizes ignored `.env` for local development and managed secrets for cloud deployment.

Confirmed fills feed a persistent outbox under unique fill IDs. Failures cannot undo a fill or place another trade. The local demo delivers deterministic files; Telegram/Gmail adapters are available for separately configured credentials. Known transient failures retry independently; ambiguous live delivery is held for explicit review because provider delivery is not guaranteed to be exactly once. Monday 09:00 Riyadh reports use period-filtered marks/fills, retain a fixed period identifier and label partial/stale observations. The scheduler catches up the latest due week while the process runs. No external message or paid model call was made in the milestone demo.

The milestone-4 LLM component records bounded shadow suggestions only. Local validation, an eight-second timeout, four persistent attempts per UTC day and six-hour spacing constrain provider use. The demo's deterministic fixture is labeled; real model calls need owner-selected account-supported models/keys and provider spending limits. No advice changes risk rules or execution, and no profitability comparison is claimed. See [integration methodology and setup](integrations.md).

Use a single Python service and SQLite initially, with clear data, strategy, risk, broker, configuration, and reporting interfaces. Persist order intents before submission and reconcile state after restarts. Docker deployment needs persistent storage, automatic restart, health/heartbeat monitoring, and controlled shutdown.

## Forward evaluation and final evidence

Collect seven actual consecutive days of real-data paper operation, including periods with no trades. Record market freshness, decisions, fills, portfolio marks, configuration changes, notifications, failures, and recovery. Do not relabel a historical replay as a week of forward operation.

Milestone 3's bounded public-data demos demonstrate connectivity and persistence only. Its four stress paths are constructed prices, with daily observations plus five-second partial-exit ticks at unchanged modeled prices. They use the exact operational engine but do not estimate actual exchange liquidity or future performance. The quote outage test illustrates that losses may pass a configured trigger before execution becomes possible.

Separate operational evidence from investment-performance evidence: seven days can demonstrate reliability, but cannot establish durable profitability. Run explicit stress cases for a 30% drop, stale quotes, unavailable APIs, invalid sheet values, exhausted budget, process restart, and ambiguous execution.

Deliver reproducible historical results, the real-data evaluation log, setup/architecture report, and a walkthrough. Coinbase's sandbox has static mocked responses, so it is suitable for API contract checks rather than strategy-performance validation.

## References

- [Project brief](https://go.apziva.com/project/detail/36/)
- [Coinbase Advanced Trade sandbox limitations](https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/sandbox)
- [Coinbase order API](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/create-order)
- [Chronological validation with TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)
