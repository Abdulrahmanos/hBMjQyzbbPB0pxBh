# Bitcoin walkthrough

Status: preview; regenerate after the actual week passes

Narration is synthesized locally with Microsoft David Desktop. No paid API is used.

## 1. Bitcoin: a testable strategy

This project asks whether a configurable Bitcoin strategy improves returns and drawdown relative to simpler alternatives. We assume Bitcoin remains operational and tradable. We do not assume it is profitable. All funds and fills are simulated. The project separates historical research, an operational paper engine, and a prospective evaluation. This walkthrough follows the money and explains the evidence, including unfavorable results.

Source: README.md / docs/modeling-guide.md

## 2. Budgets are spending permissions

The account starts with ten thousand dollars and zero Bitcoin. In a bullish regime, five thousand dollars is assigned to DCA, three thousand to swing, and two thousand to reserve. These are budget targets, not immediate investments. DCA spends gradually. Swing needs its own entry conditions and is disabled in this forward run, so its money stays in cash. Budget reviews consider existing holdings and pending orders before moving available cash.

Source: config/forward.json / paper_engine.py:_rebalance_cash

## 3. Features use completed observations

Daily trend compares the closing price with its two hundred day average. Momentum measures the return over thirty days. Both positive conditions identify a bullish regime; both negative identify a bearish regime. Large daily true ranges can select a high volatility regime. Hourly indicators use only completed candles. Breakout and volume references exclude the decision candle. Missing hours are recorded rather than invented, and short hourly indicators restart after gaps.

Source: bitcoin_agent/features.py

## 4. DCA: timing and spending limits

With a five thousand dollar DCA reference, a two percent tranche gives a purchase budget of one hundred dollars, including fees. A scheduled purchase is due approximately every twenty four hours. A completed hourly close at least three percent below the last actual DCA fill can also qualify. Both routes share a twelve hour cooldown and the same rolling spending cap. Fourteen percent of the reference allows seven hundred dollars over seven days. The engine also checks available cash, valid data and portfolio limits.

Source: paper_engine.py:_entries / config/forward.json

## 5. Execution costs change the decision

A buy starts from the observed ask and adds adverse slippage. A sell starts from the bid and subtracts it. The simulation assumes zero point six percent fees and zero point one percent slippage on each fill. These are experimental costs, not a verified account fee tier. Decimal arithmetic and conservative rounding keep money and quantity precise. Pending buys reserve cash, and pending sells reserve inventory. Immutable fills allow an independent reconstruction of the account after a restart.

Source: bitcoin_agent/paper_types.py / paper_store.py

## 6. Swing: signal, risk and net target

The candidate swing entry looks for upward trend, a breakout above the previous twenty hours, and elevated volume. ATR measures movement size, not direction. Position sizing begins with a planned loss budget of half a percent of portfolio equity. The stop is one point five ATR below entry. The profit target must cover costs and twice the modeled stop loss. A target more than eight ATR away is rejected. These filters do not prove profitability, and the current forward experiment leaves swing entries disabled.

Source: features.py / paper_types.py:swing_plan

## 7. The historical search can choose cash

The offline research compares forty seven distinct profiles across allocations, dip thresholds, volatility multipliers and defensive exits. It uses twenty three nonoverlapping development episodes. Each fit sees only outcomes already complete at that date. The score rewards mean logarithmic return and subtracts a drawdown penalty. That penalty expresses a declared preference, not a universal optimum. A context model needs enough matching earlier episodes and otherwise falls back to the global ranking. Cash is a valid winner. This search is not continuously retuning the operational account.

Source: bitcoin_agent/research.py / config/research.json

## 8. Read the favorable and unfavorable evidence

In the frozen historical holdout, feature allocation returned fourteen point three seven percent with fourteen point six six percent maximum drawdown. Buy and hold returned nine point eight two percent with twenty nine point four eight percent drawdown. However, the longer development selector reached seventy two point three percent drawdown. The initial swing rule lost twenty point six four percent in the holdout after costs. These limitations are why the historical winner was not simply promoted into live operation. The holdout is now observed and cannot become a fresh independent test for later revisions.

Source: reports/milestone-2/review.md

## 9. Existing holdings need defensive exits

Pausing purchases alone leaves previously accumulated Bitcoin exposed. The operational policy can request exits from existing holdings. New purchases respect a sixty percent Bitcoin exposure cap and twenty percent minimum cash. Price appreciation can trigger a trim above sixty two percent exposure. Rapid decline, confirmed bearish days and portfolio drawdown can request liquidation. The original capital floor can halt the experiment. Recovery requires cooldown and distinct qualifying days. A price gap or unavailable market can still produce a worse exit than the threshold.

Source: paper_engine.py:_risk_actions / docs/modeling-guide.md

## 10. A real forward week cannot be backfilled

The forward experiment starts fresh accounts for the candidate and three comparison books. Each receives the same prospective observations and modeled costs. All seven anchored twenty four hour days must reach at least ninety nine percent quote age coverage. The earlier window failed after a long interruption and remains archived. A separate recovery rehearsal confirmed that Task Scheduler can replace a stopped supervisor without changing balances or duplicating fills. Historical warm up and artificial tests never count as prospective days. The replacement forward run is still collecting observations. There are no completed conclusions about its investment performance. The video will be regenerated with the actual results after the full week passes.

Source: reports/final/review.md / bitcoin_agent/forward.py

## 11. Reproduce, inspect and interpret

The README provides setup and reproduction commands. The formula guide connects reported values to equations and saved examples. The final evaluation includes daily coverage, independent ledger reconciliation, source and configuration checksums, and strategy comparisons. Tests cover accounting, causality, restart behavior, risk controls and release gates. Real messaging, paid model calls and Docker execution remain unverified optional paths. A successful week establishes behavior under those observed conditions. It does not establish durable investment performance or a personal allocation recommendation.

Source: README.md / docs/modeling-guide.md / reports/final/review.md
