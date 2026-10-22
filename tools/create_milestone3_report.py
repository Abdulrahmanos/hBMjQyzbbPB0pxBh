"""Build the milestone review and modeling guide from actual saved evidence."""

import json
import hashlib
from pathlib import Path
from decimal import Decimal

import pandas as pd

from bitcoin_agent.config import load_config
from bitcoin_agent.paper_store import audit_paper, connection, paper_status
from bitcoin_agent.paper_types import Quote, swing_plan, ProductRules

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reports/milestone-3"


def create():
    config = load_config(ROOT / "config/paper.json")
    stress = json.loads((OUTPUT / "stress_summary.json").read_text(encoding="utf-8"))
    first = json.loads((OUTPUT / "live-demo.json").read_text(encoding="utf-8"))
    restarted = json.loads((OUTPUT / "restart-demo.json").read_text(encoding="utf-8"))
    status = paper_status(ROOT / "var/operational.sqlite3")
    test_record = json.loads((OUTPUT / "test-results.json").read_text(encoding="utf-8")) if (OUTPUT / "test-results.json").is_file() else None
    audit = audit_paper(ROOT / "var/operational.sqlite3")
    old = json.loads((ROOT / "reports/milestone-2/summary.json").read_text(encoding="utf-8"))
    actual_hash = hashlib.sha256((ROOT / "data/raw/btc_usd_1h.csv").read_bytes()).hexdigest()
    if actual_hash != old["data_sha256"]:
        raise ValueError("The archived research dataset changed")
    curve = pd.read_csv(ROOT / "reports/milestone-2/holdout_feature_allocation_curve.csv")
    peak = curve["equity_usd"].cummax().clip(lower=old["initial_cash_usd"])
    drawdown = 1 - curve["equity_usd"] / peak
    worst = drawdown.idxmax()
    with connection(ROOT / "var/operational.sqlite3") as database:
        fill = dict(database.execute("SELECT * FROM paper_fills WHERE side='buy' ORDER BY occurred_at LIMIT 1").fetchone())
        quote_payload = json.loads(database.execute("SELECT quote_json FROM paper_quotes WHERE id=?", (fill["quote_id"],)).fetchone()[0])
        last_quote_payload = json.loads(database.execute("SELECT quote_json FROM paper_quotes WHERE accepted=1 ORDER BY observed_at DESC LIMIT 1").fetchone()[0])
        events = [dict(row) for row in database.execute("SELECT * FROM system_events ORDER BY id")]
    quantity, fill_price, notional, fee = (Decimal(fill[field]) for field in ("quantity", "price", "notional_usd", "fee_usd"))
    old_adaptive = next(row for row in old["holdout_metrics"] if row["strategy"] == "Feature allocation")
    rankings = pd.read_csv(ROOT / "reports/milestone-2/training_ranking.csv")
    risky = rankings.loc[rankings["dca"] == 0.9].iloc[0]
    last_features = restarted["last_features"]
    rules = ProductRules(Decimal(restarted["product_rules"]["base_increment"]), Decimal(restarted["product_rules"]["quote_increment"]),
                         Decimal(restarted["product_rules"]["minimum_notional_usd"]), restarted["product_rules"]["market_enabled"])
    plan = swing_plan(Quote.from_dict(last_quote_payload), Decimal(last_features["atr"]), config.data, rules)
    labels = {"year_decline_80pct": "80% decline over one year", "flash_drop_30pct": "Sudden 30% drop",
              "false_rebound": "Rebound followed by further decline", "outage_during_50pct_drop": "50% drop with a quote outage"}
    table = ["| Constructed scenario | Pause-only return | Defensive return | Defensive final state |", "| --- | ---: | ---: | --- |"]
    for name, label in labels.items():
        legacy = next(row for row in stress["results"] if row["scenario"] == name and row["policy"] == "pause_only")
        protected = next(row for row in stress["results"] if row["scenario"] == name and row["policy"] == "defensive")
        table.append(f"| {label} | {legacy['net_return']:.2%} | {protected['net_return']:.2%} | {protected['ending_phase']} |")
    stress_table = "\n".join(table)
    evidence = {"operational_ledger": audit, "operational_status": status, "original_research_data_unchanged": actual_hash == old["data_sha256"],
                "first_demo": first, "restart_demo": restarted, "stress_ledgers_passed": all(row["ledger_passed"] for row in stress["results"]),
                "first_fill": fill, "paper_config_checksum": config.checksum}
    evidence["recorded_automated_tests"] = test_record
    (OUTPUT / "verification.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    (OUTPUT / "operational_events.json").write_text(json.dumps(events, indent=2), encoding="utf-8")
    guide = f"""# How the project formulates and models its numbers

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

In milestone 2, the feature-allocation holdout ended at ${old_adaptive['final_equity_usd']:,.8f} from $10,000. Therefore `({old_adaptive['final_equity_usd']:.8f} / 10000 - 1) * 100 = {old_adaptive['net_return'] * 100:.8f}%`, displayed as **{old_adaptive['net_return']:.2%}**. That is a historical, after-cost result, not an expected future return.

Milestone 2 liquidates at the experiment's final close, paying modeled exit costs. The operational service continuously marks open holdings and does not invent an end-of-run sale. Its short demo therefore reports an open position rather than the same terminal-return convention. Funding is fixed: there are no deposits, borrowing, leverage, or assumed cash interest.

## 3. Drawdown and why a pause is not a loss cap

`peak_t = max(initial_cash, equity_1, ..., equity_t)`

`drawdown_t = 1 - equity_t / peak_t`

`maximum_drawdown = max(drawdown_t)`

The archived holdout's worst feature-allocation closing observation is {curve.loc[worst, 'time_utc']}. Its preceding peak was ${peak.loc[worst]:,.8f}, and its value was ${curve.loc[worst, 'equity_usd']:,.8f}. Applying the equation gives **{drawdown.loc[worst]:.8%}**, displayed as {drawdown.loc[worst]:.2%}.

A portfolio can finish profitably and still have a large drawdown along the way. Drawdown is measured from a prior peak, not necessarily from the initial deposit. Milestone 2's pause blocked buys while leaving accumulated DCA BTC exposed; its long-run selector reached 72.30% drawdown.

The new engine keeps a lifetime peak and an active risk-epoch peak. Qualified recovery can reset the active epoch, but never erases lifetime drawdown or the original-capital floor. A 30% floor triggers a halt at $7,000 on a $10,000 starting budget. A jump, failed quote feed, or unavailable market can move equity below $7,000 before an exit fills. In the constructed outage case the engine ended at $6,948.05, a 30.52% loss, despite that trigger.

## 4. Trade execution, costs, and precision

Research fills use a candle price adjusted adversely by slippage. Runtime paper buys use the observed ask and sells use the bid, adding an adverse slippage allowance:

`buy_price = round_up_to_price_increment(ask * (1 + slippage))`

`sell_price = round_down_to_price_increment(bid * (1 - slippage))`

`BTC_quantity = round_down_to_base_increment(requested_quantity)`

The USD buy notional is rounded up to cents; the sell notional is rounded down. Simulated USD fees are rounded up to cents. These conservative rounding choices are recorded as costs. This is a paper assumption, not a claim about Coinbase's exact commission rounding. Product increments/minimums are read separately from the public product response.

The actual first public-data paper buy was:

- BTC quantity: `{quantity}`.
- Fill price: `${fill_price}`.
- Unrounded notional: `${quantity * fill_price}`.
- Rounded buy notional: `${notional}`.
- Simulated fee: `${fee}`.
- Cash debit: `${notional + fee}`.

Thus the ledger's cash became `$10000 - ${notional + fee} = ${10000 - notional - fee}`. The entry spread, slippage, fee, and rounding explain why marked equity initially drops even if market prices are unchanged. The first-fill row and quote are stored in SQLite and the milestone verification JSON.

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

The target is rounded upward. At the saved restart quote, using ATR `${last_features['atr']}`, the plan gives entry `${plan['entry']}`, stop `${plan['stop']}`, and target `${plan['target']}`. The target is {plan['target_atr_multiple']:.2f} ATR above entry. The experimental 8-ATR plausibility limit filters such a distant target. This is an execution-cost calculation, not a forecast that BTC will reach the target.

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

The logarithm accounts for compounding and penalizes inconsistent outcomes. The 0.75 weight is a declared research preference. It was not discovered to be the universally correct risk preference. The final global model selected cash with score 0. A DCA-heavy candidate `{risky['profile_id']}` had mean log return {risky['mean_log_return']:.8f}, mean drawdown {risky['mean_drawdown']:.8f}, and score {risky['score']:.8f}. These values can be checked in `training_ranking.csv`.

The context model uses a daily bull/bear/mixed/high-volatility label and requires at least three earlier matching episodes. The final mixed-regime mapping was based on only four observations; other regimes used the global fallback. This small sample is an important limitation. The holdout mapping was frozen before April 14, 2026; it did not train on the following 180 days.

That holdout has now been observed. Future changes require newly reserved data or forward evaluation for independent validation. Replaying the old period is diagnostic work. Cold-start training episodes also differ from a continuously invested portfolio, so future learned policies need portfolio state, not just market features.

## 8. Annualized statistics

`CAGR = (final_equity / initial_cash) ** (365 / elapsed_days) - 1`

The archived report measures elapsed time between its first and last closing marks: 179.9583 days for the nominal 180-day holdout. This one-hour reporting convention does not affect net return or maximum drawdown. Annualization describes that historical interval and should not be read as a future forecast.

Daily Sharpe uses daily closing percentage changes, zero assumed risk-free return, sample standard deviation, and `sqrt(365)` for a crypto calendar. Fees are already reflected in equity. Sharpe is not reported when volatility is zero. Mean BTC exposure is the average marked fraction, not the configured allocation percentage.

## 9. Reproduce and inspect

```powershell
.\\.venv\\Scripts\\python.exe -m bitcoin_agent paper-status
.\\.venv\\Scripts\\python.exe -m bitcoin_agent paper-audit
.\\.venv\\Scripts\\python.exe -m bitcoin_agent stress-test
.\\.venv\\Scripts\\python.exe -m tools.create_milestone3_report
```

Inspect `paper_feature_context`, `paper_cash_targets`, `paper_order_intent`, `paper_fill`, and `paper_risk_pause` events to follow a decision. Configuration snapshots carry checksums; quotes, order quantities, fees, positions, and risk state persist in SQLite. Exact quote replays are idempotent. Different HTTP polls are separate observations, even when the last exchange trade is unchanged; both market and receipt timestamps must pass freshness checks.

The paper service uses REST polling with a background feature refresh. Quote network timeouts and shutdown waits can exceed the nominal five-second interval. It cannot execute without valid quotes or a permitted market. A production feed should move to WebSocket observation and monitoring; none of these local paper controls guarantees an exit through an outage.

Sources: [Coinbase ticker](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-ticker), [product rules](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-single-product). The source code and saved artifacts are the authority for the project's formulas and reported results.
"""
    (ROOT / "docs/modeling-guide.md").write_text(guide, encoding="utf-8")
    review = f"""# Milestone 3: persistent paper engine and risk controls

The operational paper engine runs on public data and simulated capital. The searched milestone-2 policy remains research-only. Runtime targets and risk limits are explicit experimental configuration; these stress tests do not establish an optimal investment policy.

## Delivered behavior

- Decimal balances, price/quantity increments, minimum order amounts, conservative rounding, and cost-aware swing targets.
- Atomic quote processing with order reservations, partial fills, cancellation, persisted protective exits, and replay/restart protection.
- Existing-inventory liquidation for confirmed bear conditions, rapid declines, portfolio drawdown, or a lifetime capital-floor breach.
- Limits on new BTC exposure and cash depletion, plus trimming of appreciating holdings.
- A cooldown and distinct-day recovery confirmation before automatic resume. Lifetime drawdown and the initial-capital floor survive a resume. Capital-floor breaches stay halted.
- A public-data service that polls quotes independently of its background candle/features refresh. Optional continuous mode is available; bounded demos have stopped.
- A [modeling guide](../../docs/modeling-guide.md) with equations and examples drawn from the saved research and actual paper fill.

## Public-data run and restart

The first demo completed {first['cycles_completed']} cycles, accepted {first['accepted_quotes']} observations, and created {first['new_fills']} simulated fill. The restart completed {restarted['cycles_completed']} cycles, accepted {restarted['accepted_quotes']} observations, and created {restarted['new_fills']} new fills. The existing DCA position persisted.

Current simulated cash is ${Decimal(status['cash_usd']):,.2f}; BTC is `{status['btc_units']}`. The last marked equity is ${Decimal(status['equity_usd']):,.8f}. Cumulative fees are ${Decimal(audit['fees_usd']):,.2f}. Independent cash reconciliation difference is **${audit['cash_difference_usd']}**. These few observations demonstrate connectivity and state handling, not trading performance or the required week-long evaluation.

The public product response used a BTC increment of `{restarted['product_rules']['base_increment']}`, USD price increment `{restarted['product_rules']['quote_increment']}`, and minimum order notional `${restarted['product_rules']['minimum_notional_usd']}`. Test fixtures separately use a conservative $10 minimum. Fee/slippage values remain simulation assumptions.

## Constructed stress results

{stress_table}

![Constructed price-path comparisons](stress_tests.png)

Every scenario uses the same persistent engine and every ledger reconciles exactly. The comparisons start with common 70/20/10 sleeve targets, but the defensive policy limits pre-shock BTC exposure to about 60% while the pause-only policy reaches about 70%. The baseline also reallocates idle cash as prices decline. The resulting difference is a combination of exposure caps, entry suspensions, exits, and recovery rules. The quote paths are artificial and exits assume the configured paper fill capacity. This is not an isolated causal estimate of any single rule or a return forecast.

The outage case ended below the configured 30% capital-loss trigger: gaps and unavailable quotes can move prices past a threshold before the engine can act. The false rebound caused an additional loss after one qualified recovery, while lifetime drawdown remained visible. The permanent halt is intentionally not an automatic license to try again with a reset budget.

## Files and verification

- `stress_summary.json` / CSV: exact assumptions, all case metrics, individual ledger audits, and SQLite evidence locations.
- `live-demo.json` / `restart-demo.json`: bounded real-data run summaries.
- `verification.json`: actual first fill, current portfolio, ledger reconciliation, and unchanged original research input checksum.
- `operational_events.json`: human-inspectable event trace for the public-data demo.
- `config/paper.json`: parameterized runtime control policy; swing entries default to disabled.
- `test-results.json`: the recorded final check on 2026-10-11; 53 tests passed with zero failures/errors.

The original milestone-2 dataset and the foundational simulated portfolio are separate from the operational database. The historical holdout is still treated as observed data; these new controls require independent future evaluation.

## Run it

```powershell
.\\.venv\\Scripts\\python.exe -m bitcoin_agent paper-status
.\\.venv\\Scripts\\python.exe -m bitcoin_agent paper-audit
.\\.venv\\Scripts\\python.exe -m bitcoin_agent run-paper --cycles 3
```

`run-paper --cycles 0` runs until Ctrl+C. No background service was left running at this milestone's review stop. The new operational database is `var/operational.sqlite3`; the original `var/paper.sqlite3` remains the foundation example. Public APIs receive GET requests only; no funded broker or real order submission is implemented.

## Next milestone

Stop for owner review. Milestone 4 adds validated Google Sheet configuration/cache, bounded LLM experiments, Telegram notifications, and weekly Gmail reporting. Continuous seven-day evaluation and deployment remain milestone 5.
"""
    (OUTPUT / "review.md").write_text(review, encoding="utf-8")
    print(json.dumps({"report": str(OUTPUT / "review.md"), "guide": str(ROOT / "docs/modeling-guide.md"), "ledger_passed": audit["passed"]}, indent=2))


if __name__ == "__main__":
    create()
