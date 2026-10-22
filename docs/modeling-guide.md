# How the project formulates and models its numbers

This guide links the reported numbers to equations, data, and saved examples. The research premise is that Bitcoin remains operational and tradable. Profitability is evaluated; it is not assumed. All holdings and fills in this project are simulated.

## 1. What is observed and what is chosen

| Item | Origin |
| --- | --- |
| Historical OHLC and volume | Coinbase public hourly candles; archived input checksum and gaps |
| Runtime bid, ask, last-trade time, product increments/minimum | Coinbase public REST responses |
| EMA, ATR, RSI, daily trend, and momentum | Calculated from completed observations |
| DCA percentages and ATR multipliers | Predeclared research candidates or explicit configuration |
| 0.6% fee and 0.1% adverse slippage | Simulation assumptions; not a verified account fee tier |
| Allocation objective penalty 0.75 | A declared preference for balancing return against drawdown |
| Runtime exposure, crash, exit, and recovery limits | Experimental engineering controls in `config/paper.json` |

Market observations are not predictions. A favorable indicator does not establish that the next trade will profit. The runtime's regime targets are transparent configured rules, not the searched model promoted into production. The milestone-2 selector remains research-only because of its long-period drawdown.

## 2. Portfolio value and return

At each accepted quote, the paper engine marks BTC at the midpoint:

`mark = (bid + ask) / 2`

`equity = total_USD_cash + total_BTC_units * mark`

`net_return = final_equity / initial_cash - 1`

In milestone 2, the feature-allocation holdout ended at $11,436.56082719 from $10,000. Therefore `(11436.56082719 / 10000 - 1) * 100 = 14.36560827%`, displayed as **14.37%**. That is a historical, after-cost result, not an expected future return.

Milestone 2 liquidates at the experiment's final close, paying modeled exit costs. The operational service continuously marks open holdings and does not invent an end-of-run sale. Its short demo therefore reports an open position rather than the same terminal-return convention. Funding is fixed: there are no deposits, borrowing, leverage, or assumed cash interest.

## 3. Drawdown and why a pause is not a loss cap

`peak_t = max(initial_cash, equity_1, ..., equity_t)`

`drawdown_t = 1 - equity_t / peak_t`

`maximum_drawdown = max(drawdown_t)`

The archived holdout's worst feature-allocation closing observation is 2026-06-25 14:00:00+00:00. Its preceding peak was $10,271.48482275, and its value was $8,766.02799367. Applying the equation gives **14.65666216%**, displayed as 14.66%.

A portfolio can finish profitably and still have a large drawdown along the way. Drawdown is measured from a prior peak, not necessarily from the initial deposit. Milestone 2's pause blocked buys while leaving accumulated DCA BTC exposed; its long-run selector reached 72.30% drawdown.

The new engine keeps a lifetime peak and an active risk-epoch peak. Qualified recovery can reset the active epoch, but never erases lifetime drawdown or the original-capital floor. A 30% floor triggers a halt at $7,000 on a $10,000 starting budget. A jump, failed quote feed, or unavailable market can move equity below $7,000 before an exit fills. In the constructed outage case the engine ended at $6,948.05, a 30.52% loss, despite that trigger.

## 4. Trade execution, costs, and precision

Research fills use a candle price adjusted adversely by slippage. Runtime paper buys use the observed ask and sells use the bid, adding an adverse slippage allowance:

`buy_price = round_up_to_price_increment(ask * (1 + slippage))`

`sell_price = round_down_to_price_increment(bid * (1 - slippage))`

`BTC_quantity = round_down_to_base_increment(requested_quantity)`

The USD buy notional is rounded up to cents; the sell notional is rounded down. Simulated USD fees are rounded up to cents. These conservative rounding choices are recorded as costs. This is a paper assumption, not a claim about Coinbase's exact commission rounding. Product increments/minimums are read separately from the public product response.

The actual first public-data paper buy was:

- BTC quantity: `0.00119731`.
- Fill price: `$82988.60`.
- Unrounded notional: `$99.3630806660`.
- Rounded buy notional: `$99.37`.
- Simulated fee: `$0.60`.
- Cash debit: `$99.97`.

Thus the ledger's cash became `$10000 - $99.97 = $9900.03`. The entry spread, slippage, fee, and rounding explain why marked equity initially drops even if market prices are unchanged. The first-fill row and quote are stored in SQLite and the milestone verification JSON.

The artificial per-observation fill cap is $1,000 by default. It creates partial fills for testing; it is not measured exchange liquidity. Pending buys reserve USD, pending sells reserve BTC, and new intents cannot spend those reservations again. Cash is released when an unfilled remainder is canceled. A partial fill of an already valid order can be smaller than the venue's minimum order size; the minimum applies to submitting the whole order.

## 5. ATR, position size, and net swing targets

For each completed hourly bar:

`TR = max(high - low, abs(high - previous_close), abs(low - previous_close))`

ATR(14) is seeded by the first 14 true ranges' arithmetic mean. Subsequently `ATR_t = (13 * ATR_(t-1) + TR_t) / 14`. Hourly indicators reset after a missing hour. An EMA uses `alpha = 2 / (period + 1)`, starts from the first available close, and waits for its configured warm-up. Breakout highs and relative-volume denominators exclude the decision candle. Entries use the following observation; no current unfinished candle supplies a signal.

The stop is `entry_fill - k * ATR_at_signal`. Per-BTC planned loss includes the entry cost and expected net stop proceeds:

`entry_cost_per_BTC = entry_price * (1 + fee)`

`expected_stop_proceeds_per_BTC = rounded_stop_execution_price * (1 - fee)`

`planned_loss_per_BTC = entry_cost_per_BTC - expected_stop_proceeds_per_BTC`

`risk_budget_USD = current_equity * risk_fraction`

`quantity_by_risk = risk_budget_USD / planned_loss_per_BTC`

The actual quantity is also limited by sleeve cash, order reserves, total BTC exposure, minimum cash, minimum trade size, and partial-fill capacity. A normal modeled stop aims to respect this budget; a gap can produce a larger loss.

Instead of setting a gross price target that ignores costs, the paper engine solves for a bid target whose net proceeds cover entry costs plus the configured reward multiple of planned loss. The target also includes a rounding allowance for expected partial fills:

`target_bid = (entry_cost_per_BTC + reward_multiple * planned_loss_per_BTC + rounding_allowance) / ((1 - slippage) * (1 - fee))`

The target is rounded upward. At the saved restart quote, using ATR `$168.67412361403947`, the plan gives entry `$83064.63`, stop `$82811.61`, and target `$86831.52`. The target is 22.33 ATR above entry. The experimental 8-ATR plausibility limit filters such a distant target. This is an execution-cost calculation, not a forecast that BTC will reach the target.

Swing entries are currently disabled by default because the initial rule lost money after costs. Enabling them permits this filtered candidate rule; it does not mean it has been validated as profitable.

## 6. DCA, allocations, and available cash

An allocation such as 50% DCA / 30% swing / 20% reserve describes sleeve targets. DCA does not immediately buy 50% of the portfolio. Its default tranche is 2% of its $5,000 reference budget: $100, including fees. A rounding buffer slightly reduces the submitted quantity.

Scheduled buys and 3% dip buys share the 12-hour cooldown and a seven-day spending cap of 14% of the reference budget. Dip reference is the last actual DCA fill price. With partial fills, the latest fill updates the reference and time. Spending history is taken from the fill ledger and includes fees; a restart does not clear it.

When the regime changes, only available cash is redistributed according to deficits between sleeve target value and existing BTC value. Existing positions are sold only by an explicit risk or position-exit rule. In regime mode, configured bull/mixed/bear/unknown targets select a budget; they do not predict returns. Bear and unknown targets initially stop new allocation to BTC. Seven confirmed bearish daily observations can trigger the separate liquidation rule.

New purchases are constrained to 60% total BTC exposure and at least 20% total cash. Appreciating holdings are trimmed if exposure exceeds 62%, using a 2-percentage-point buffer to reduce tiny repeated trades. Partial fills and missing liquidity can delay compliance. A target is not a guaranteed instant allocation.

The 80%-decline stress comparison begins with 60 days of accumulation. Before the decline, the pause-only policy has about 70% BTC exposure and the defensive policy about 60%. The pause-only policy continues buying and reallocating cash during the decline until its pause triggers; this explains why its eventual 61.72% loss differs from the simple static calculation `70% * 80% = 56%`. The experiment compares a bundle of controls, not identical exposures or an isolated exit rule.

## 7. How the parameter search chooses a profile

The research grid has 47 distinct combinations after removing irrelevant duplicates. Every profile is tried on the same 23 nonoverlapping 90-day development episodes. Each trial starts with the same cash, has no external deposits, and includes terminal liquidation costs.

For each fit time, only fully completed earlier episodes within the training window are eligible:

`score = mean(log(1 + episode_net_return)) - 0.75 * mean(episode_max_drawdown)`

The logarithm accounts for compounding and penalizes inconsistent outcomes. The 0.75 weight is a declared research preference. It was not discovered to be the universally correct risk preference. The final global model selected cash with score 0. A DCA-heavy candidate `d0.9_s0_r0.1_dip0.06_atr1.5_exit1` had mean log return -0.00229372, mean drawdown 0.13856188, and score -0.10621513. These values can be checked in `training_ranking.csv`.

The context model uses a daily bull/bear/mixed/high-volatility label and requires at least three earlier matching episodes. The final mixed-regime mapping was based on only four observations; other regimes used the global fallback. This small sample is an important limitation. The holdout mapping was frozen before April 14, 2026; it did not train on the following 180 days.

That holdout has now been observed. Future changes require newly reserved data or forward evaluation for independent validation. Replaying the old period is diagnostic work. Cold-start training episodes also differ from a continuously invested portfolio, so future learned policies need portfolio state, not just market features.

## 8. Annualized statistics

`CAGR = (final_equity / initial_cash) ** (365 / elapsed_days) - 1`

The archived report measures elapsed time between its first and last closing marks: 179.9583 days for the nominal 180-day holdout. This one-hour reporting convention does not affect net return or maximum drawdown. Annualization describes that historical interval and should not be read as a future forecast.

Daily Sharpe uses daily closing percentage changes, zero assumed risk-free return, sample standard deviation, and `sqrt(365)` for a crypto calendar. Fees are already reflected in equity. Sharpe is not reported when volatility is zero. Mean BTC exposure is the average marked fraction, not the configured allocation percentage.

## 9. Reproduce and inspect

```powershell
.\.venv\Scripts\python.exe -m bitcoin_agent paper-status
.\.venv\Scripts\python.exe -m bitcoin_agent paper-audit
.\.venv\Scripts\python.exe -m bitcoin_agent stress-test
.\.venv\Scripts\python.exe -m tools.create_milestone3_report
```

Inspect `paper_feature_context`, `paper_cash_targets`, `paper_order_intent`, `paper_fill`, and `paper_risk_pause` events to follow a decision. Configuration snapshots carry checksums; quotes, order quantities, fees, positions, and risk state persist in SQLite. Exact quote replays are idempotent. Different HTTP polls are separate observations, even when the last exchange trade is unchanged; both market and receipt timestamps must pass freshness checks.

The paper service uses REST polling with a background feature refresh. Quote network timeouts and shutdown waits can exceed the nominal five-second interval. It cannot execute without valid quotes or a permitted market. A production feed should move to WebSocket observation and monitoring; none of these local paper controls guarantees an exit through an outage.

Sources: [Coinbase ticker](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-ticker), [product rules](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-single-product). The source code and saved artifacts are the authority for the project's formulas and reported results.
