"""Standalone research figures and a readable review report."""

from __future__ import annotations

from pathlib import Path
import os
import hashlib
import json

# Keep this project's font/cache artifacts in its own ignored runtime directory.
os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[1] / "var" / "matplotlib"))
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter, StrMethodFormatter
import pandas as pd

from .backtest import BacktestResult
from .market_data import DataError, load_candles

COLORS = {"Cash": "#8c919b", "Buy and hold": "#b45309", "Scheduled DCA": "#7c3aed", "Dip DCA": "#9c6ade",
          "Swing only": "#64748b", "Fixed hybrid": "#2563eb", "Defensive hybrid": "#059669",
          "Optimized global": "#db2777", "Feature allocation": "#0891b2"}
PLOT_NAMES = {"Buy and hold", "Scheduled DCA", "Fixed hybrid", "Defensive hybrid", "Optimized global", "Feature allocation", "Cash"}


def _style():
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.15,
                         "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white"})


def _performance_chart(path, results, title, subtitle):
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    for result in results:
        if result.name not in PLOT_NAMES:
            continue
        equity = result.curve["equity_usd"].resample("1D").last()
        hourly = result.curve["equity_usd"]
        drawdown = (hourly / hourly.cummax().clip(lower=result.initial_cash) - 1).resample("1D").min()
        axes[0].plot(equity.index, equity, label=result.name, color=COLORS[result.name], linewidth=1.8)
        axes[1].plot(drawdown.index, drawdown, color=COLORS[result.name], linewidth=1.5)
    axes[0].set_ylabel("Portfolio value (USD)")
    axes[0].yaxis.set_major_formatter(StrMethodFormatter("${x:,.0f}"))
    axes[0].legend(ncol=3, loc="upper left", fontsize=9)
    axes[1].set_ylabel("Drawdown")
    axes[1].yaxis.set_major_formatter(PercentFormatter(1))
    axes[1].xaxis.set_major_formatter(mdates.ConciseDateFormatter(axes[1].xaxis.get_major_locator()))
    fig.suptitle(title, fontsize=18, fontweight="bold", x=0.08, ha="left")
    fig.text(0.08, 0.925, subtitle, fontsize=10, color="#475569")
    fig.subplots_adjust(top=0.87, bottom=0.08, left=0.10, right=0.97, hspace=0.13)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _table(metrics):
    lines = ["| Strategy | Net return | Max drawdown | Final USD | Fees USD | Fills |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in metrics:
        lines.append(f"| {row['strategy']} | {row['net_return']:.2%} | {row['max_drawdown']:.2%} | {row['final_equity_usd']:,.2f} | {row['fees_usd']:,.2f} | {row['fills']} |")
    return "\n".join(lines)


def create_report(output: Path, summary: dict, candles, features, development, holdout, ranking, quality):
    _style()
    _performance_chart(output / "development.png", development, "Walk-forward development results",
                       "Earlier completed 90-day experiments select each model; portfolio holdings continue across model updates.")
    _performance_chart(output / "holdout.png", holdout, "Sealed 180-day holdout",
                       f"The selector was frozen before this period. All strategies start with ${summary['initial_cash_usd']:,.0f} and include simulated execution costs.")
    feature_result = next(result for result in holdout if result.name == "Feature allocation")
    curve = feature_result.curve.resample("1D").last()
    fig, axes = plt.subplots(3, 1, figsize=(13, 9), sharex=True, gridspec_kw={"height_ratios": [1.1, 1, 1]})
    prices = candles.loc[(candles.index >= pd.Timestamp(summary["holdout_start_utc"]))]["close"].resample("1D").last()
    axes[0].plot(prices.index, prices, color="#b45309", linewidth=1.7)
    axes[0].set_ylabel("BTC price (USD)")
    axes[0].yaxis.set_major_formatter(StrMethodFormatter("${x:,.0f}"))
    axes[1].stackplot(curve.index, curve["target_dca"], curve["target_swing"], curve["target_reserve"],
                      labels=["DCA target", "Swing target", "Reserve target"], colors=["#2563eb", "#059669", "#cbd5e1"], alpha=0.9, step="post")
    axes[1].set_ylim(0, 1)
    axes[1].set_ylabel("Selected targets")
    axes[1].yaxis.set_major_formatter(PercentFormatter(1))
    axes[1].legend(ncol=3, loc="upper left", fontsize=9)
    axes[2].plot(curve.index, curve["btc_exposure"], color="#0f172a", label="Actual BTC exposure", linewidth=1.8)
    axes[2].fill_between(curve.index, 0, curve["btc_exposure"], color="#0f172a", alpha=0.1)
    axes[2].set_ylim(0, 1)
    axes[2].set_ylabel("Actual BTC / equity")
    axes[2].yaxis.set_major_formatter(PercentFormatter(1))
    axes[2].legend(loc="upper left", fontsize=9)
    axes[2].annotate("Experiment-end liquidation", xy=(curve.index[-1], 0.02), xytext=(-180, 35),
                     textcoords="offset points", fontsize=9, color="#475569",
                     arrowprops={"arrowstyle": "->", "color": "#475569"})
    axes[2].xaxis.set_major_formatter(mdates.ConciseDateFormatter(axes[2].xaxis.get_major_locator()))
    fig.suptitle("Feature-based allocation: targets and actual exposure", x=0.09, ha="left", fontsize=17, fontweight="bold")
    fig.text(0.09, 0.927, "Target changes move available cash. Existing DCA BTC is sold only under an explicit defensive rule.", color="#475569")
    fig.subplots_adjust(top=0.87, left=0.10, right=0.97, bottom=0.07, hspace=0.17)
    fig.savefig(output / "allocation.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 7))
    scatter = ax.scatter(ranking["mean_drawdown"], ranking["mean_return"], c=ranking["dca"], cmap="viridis",
                         s=50 + ranking["reserve"] * 160, alpha=0.8, edgecolors="white", linewidth=0.6)
    best = ranking.iloc[0]
    ax.scatter([best["mean_drawdown"]], [best["mean_return"]], marker="*", s=330, color="#ef4444", label="Selected by training objective", zorder=5)
    ax.axhline(0, color="#64748b", linewidth=0.8)
    ax.set_xlabel("Mean maximum drawdown per training episode")
    ax.set_ylabel("Mean net return per training episode")
    ax.xaxis.set_major_formatter(PercentFormatter(1))
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.legend(loc="upper left")
    fig.colorbar(scatter, ax=ax, label="DCA allocation fraction")
    fig.suptitle("Parameter search: return versus drawdown", x=0.10, ha="left", fontsize=17, fontweight="bold")
    fig.text(0.10, 0.925, "Training data only. Larger dots represent larger cash reserves; the selected point is not a universal optimum.", color="#475569")
    fig.subplots_adjust(top=0.86, left=0.10, right=0.95, bottom=0.10)
    fig.savefig(output / "parameter_search.png", dpi=160)
    plt.close(fig)

    holdout_by_name = {row["strategy"]: row for row in summary["holdout_metrics"]}
    adaptive, global_result, fixed = (holdout_by_name[name] for name in ("Feature allocation", "Optimized global", "Fixed hybrid"))
    feature_delta = adaptive["net_return"] - global_result["net_return"]
    fixed_delta = adaptive["net_return"] - fixed["net_return"]
    decisions = pd.DataFrame(feature_result.decisions)
    context_decisions = int((decisions["selection_source"] == "regime").sum())
    fallback_decisions = int((decisions["selection_source"] == "global_fallback").sum())
    annual = pd.read_csv(output / "development_annual_results.csv")
    bear_year = annual.loc[annual["year"] == 2022]
    bear_lines = ["| Strategy | 2022 return | Within-year drawdown |", "| --- | ---: | ---: |"]
    for row in bear_year.to_dict("records"):
        bear_lines.append(f"| {row['strategy']} | {row['return']:.2%} | {row['within_year_drawdown']:.2%} |")
    bear_table = "\n".join(bear_lines)
    verification_text = "Run `python tools/verify_research.py` to independently reconcile exported results, then `python -m bitcoin_agent render-report` to include the verification record."
    verification_path = output / "verification.json"
    if verification_path.is_file():
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
        expected_hash = hashlib.sha256(json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if verification.get("passed") and verification.get("summary_sha256") == expected_hash:
            verification_text = (f"The independent export audit passed for {verification['strategies_audited']} strategy-period comparisons. "
                                 "It checked the input checksum, training cutoffs, frozen holdout model dates, cash and inventory reconciliation, "
                                 "nonnegative cash, and allocation totals. See `verification.json`. "
                                 f"Of {verification['swing_holdout_profit_target_exits']} swing profit-target exits, "
                                 f"{verification['swing_holdout_profit_target_exits_with_net_loss']} still lost money after modeled costs. "
                                 "A price target above entry therefore does not necessarily mean a profitable trade.")
    report = f"""# Milestone 2: data, backtests, and allocation research

This milestone adds an offline research simulator and selection experiments. Trading-service execution, notifications, and deployment remain later milestones. The original persisted paper portfolio is independent of these experiments.

## What the experiment established

The feature-based selector had a {adaptive['net_return']:.2%} net return and {adaptive['max_drawdown']:.2%} maximum drawdown in the final 180 days. Buy-and-hold returned {holdout_by_name['Buy and hold']['net_return']:.2%} with {holdout_by_name['Buy and hold']['max_drawdown']:.2%} drawdown. This is an encouraging single-period observation.

The continuously carried development portfolio exposes a serious limitation: feature allocation had {next(row['max_drawdown'] for row in summary['development_metrics'] if row['strategy'] == 'Feature allocation'):.2%} maximum drawdown. Selecting more cash cannot reduce existing BTC exposure while selling remains restricted. The 25% pause blocks new purchases but leaves holdings exposed, and it can prevent participation in later recoveries. Cold-start 90-day training episodes therefore do not fully represent a portfolio with years of accumulated inventory.

The initial swing rule lost {abs(holdout_by_name['Swing only']['net_return']):.2%} in the holdout and paid ${holdout_by_name['Swing only']['fees_usd']:,.2f} in modeled fees. Its trading activity is not justified by this experiment under the chosen cost assumptions. The frozen feature model had enough observations to specialize only for the mixed regime; other regimes use the global fallback.

These results support further research, not promotion of this selector into an operational policy. Milestone 3 must explicitly address existing-inventory exits, portfolio-aware allocation constraints, and pause/resume behavior. The current parameters were not changed after observing the holdout.

The holdout is now observed. Future strategy revisions may use it for diagnostics, but independent validation requires newly reserved data or future forward operation; it cannot be presented as an untouched test of those revisions.

## Research premise

We assume Bitcoin remains operational and tradable. We evaluate whether a configurable DCA and trading system improves returns and drawdown relative to simpler strategies, including during prolonged declines.

## Data and chronology

- Source: Coinbase Exchange public BTC-USD hourly candles.
- Coverage: {quality['first_candle_utc']} through {quality['last_candle_utc']}.
- Candles: {quality['rows']:,}; missing hours: {quality['missing_hours']}; invalid rows: {quality['invalid_rows']}.
- Research warm-up starts before {summary['research_start_utc']}; development starts {summary['development_start_utc']}.
- Sealed holdout: {summary['holdout_start_utc']} to {summary['end_exclusive_utc']} (exclusive).
- Input SHA-256: `{summary['data_sha256']}`.

Missing prices are not filled. Short hourly indicators restart after gaps and entries are blocked immediately across a gap. Daily range/volume features require 24 candles. Daily trend can use a verified final-hour close even if an earlier hour is missing. Daily context becomes available only after that UTC day ends.

## Allocation search

The predeclared search evaluates {summary['candidate_profiles']} distinct profiles: allocation weights, 3%/6% dip thresholds, 1.5/2.5 ATR multipliers, and an optional defensive DCA exit. All-cash is a valid candidate. A seven-day confirmed bearish daily trend triggers the optional DCA exit; it uses a 200-day moving average and 30-day momentum. These rules are candidates, not validated defaults for deployment.

There are {summary['nonoverlapping_training_episodes']} nonoverlapping 90-day development experiments. Each starts with ${summary['initial_cash_usd']:,.0f} and includes final liquidation costs. Rankings use up to two years of prior episodes. The score is mean log return minus {summary['sealed_model']['drawdown_penalty']} times mean maximum drawdown. That penalty expresses a research preference, not a mathematically unique definition of "best." `objective_sensitivity.csv` compares training selections with alternative penalties; holdout results are not used to choose the penalty.

The global selector uses all eligible earlier episodes. The feature selector uses the starting market regime (bull, bear, mixed, or high volatility), requiring at least three earlier matching episodes, and otherwise falls back to the global selector. It made {context_decisions} regime-based and {fallback_decisions} fallback decisions during the holdout. This is a coarse context model; the small historical sample limits its credibility.

Development uses {summary['walk_forward_models']} chronological model fits. Only experiments whose outcomes end before or at each fit time are eligible. The final model was frozen before the holdout and never refitted using holdout outcomes. Allocation targets are reviewed weekly; inventory continues between reviews. Targets redistribute available cash, without forcing a DCA sale. A reserve target is not guaranteed when existing BTC exceeds the permitted target.

## Sealed holdout results

{_table(summary['holdout_metrics'])}

The feature selector's return difference is {feature_delta:+.2%} versus the global selector and {fixed_delta:+.2%} versus the fixed hybrid. Compare drawdown and exposure too; a return difference alone does not establish superiority. Treat this as one historical holdout, not proof of future profitability.

![Holdout equity and drawdown](holdout.png)

![Selected targets and actual exposure](allocation.png)

## Development results

{_table(summary['development_metrics'])}

Development is useful for understanding behavior, but its aggregate results are not a second untouched final test.

![Development equity and drawdown](development.png)

## Prolonged decline check: 2022

The following figures come from each continuously carried development portfolio during 2022; starting inventories differ because of earlier strategy decisions. They are an explanation of path dependence, not fresh equal-cash annual experiments.

{bear_table}

## Search tradeoffs

![Training parameter tradeoffs](parameter_search.png)

Full candidates, trial outcomes, rankings, frozen models, weekly decisions, fills, curves, annual results, application settings, and research settings are included as CSV/JSON files beside this report.

## Independent verification

{verification_text}

## Execution assumptions and limits

- Signals use completed candles and entries execute at the following available open. No entries are made across missing hours.
- Simulated per-fill fees are {summary['fee_fraction_per_fill']:.1%} and adverse slippage is {summary['slippage_fraction_per_fill']:.1%}; these values are saved in `application_settings.json`. They are scenario assumptions, not a verified exchange fee tier. Cash earns zero interest.
- Swing size accounts for estimated entry/exit fees and normal slippage. Gaps can produce larger losses.
- Hourly bars cannot establish the ordering of intrabar prices. If both swing exits are touched, the simulator assumes the stop happens first. Protection is not a simulation of a five-second quote feed or an order book.
- The portfolio drawdown pause is checked at hourly open/close observations, latches for the experiment, and blocks new buys. It does not cap loss or automatically liquidate DCA.
- Simple buy-and-hold and DCA benchmarks have no portfolio pause. Hybrids and swing trading do. DCA cash spending caps and cooldowns still apply.
- New allocation targets move only uncommitted cash. A previously accumulated DCA position can exceed a reduced target until an explicit defensive rule exits it.
- Changing a profile updates the DCA tranche reference; an unchanged profile keeps its original reference. Spending history and cooldowns continue across allocation changes.
- Each complete experiment liquidates holdings at its final close with modeled costs. Intermediate weekly/model changes do not liquidate holdings.
- Offline research uses floating-point arithmetic with invariant checks. The operational broker, decimal accounting, partial fills, exchange minimum sizes, and restart recovery will be implemented in milestone 3.
- Hyperparameter selection can overfit even with chronological evaluation. Fixed search choices, sparse regimes, one venue, and one final test limit what these results show. No settings are automatically applied to the saved paper portfolio.

## Next review gate

Milestone 2 stops after verification and owner review. Milestone 3 will turn the reviewed rules into the operational paper engine, refine execution realism, and add explicit stress paths for prolonged declines and failed execution. LLM and connected reporting remain milestone 4.

## Sources

- [Coinbase candle schema, limits, and incomplete historical data](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles)
- [Chronological validation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)
"""
    (output / "review.md").write_text(report, encoding="utf-8")


def render_saved_report(output: str | Path, candle_path: str | Path) -> dict:
    """Refresh presentation from saved results without rerunning selection or tests."""
    output = Path(output)
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    if hashlib.sha256(Path(candle_path).read_bytes()).hexdigest() != summary["data_sha256"]:
        raise DataError("Saved results and current candle file have different checksums")
    candles = load_candles(candle_path)
    quality = json.loads((output / "data_quality.json").read_text(encoding="utf-8"))
    ranking = pd.read_csv(output / "training_ranking.csv")
    def restore(label):
        results = []
        for metric in summary[f"{label}_metrics"]:
            identifier = metric["strategy"].lower().replace(" ", "_")
            curve = pd.read_csv(output / f"{label}_{identifier}_curve.csv")
            curve["time_utc"] = pd.to_datetime(curve["time_utc"], utc=True)
            curve = curve.set_index("time_utc")
            decision_path = output / f"{label}_{identifier}_decisions.csv"
            decisions = pd.read_csv(decision_path).to_dict("records") if decision_path.is_file() else []
            results.append(BacktestResult(metric["strategy"], curve, [], decisions, summary["initial_cash_usd"]))
        return results
    create_report(output, summary, candles, None, restore("development"), restore("holdout"), ranking, quality)
    return {"report": str((output / "review.md").resolve()), "charts": 4, "models_refitted": False}
