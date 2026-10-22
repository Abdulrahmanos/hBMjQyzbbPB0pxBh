"""Run once: python -m research_lstm.run (CPU research, no market writes)."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Optional isolated installation keeps the frozen collector's environment intact.
if (ROOT / "var/lstm-libs").is_dir():
    sys.path.insert(0, str(ROOT / "var/lstm-libs"))

import numpy as np
import pandas as pd

from .experiment import MODES, FEATURES, build_dataset, fit_models, paired_bootstrap, policy_metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="reports/lstm-comparison")
    args = parser.parse_args()
    output = ROOT / args.output
    settings_path = ROOT / "config/lstm-experiment.json"
    settings = json.loads(settings_path.read_text())
    output.mkdir(parents=True, exist_ok=True)
    # Write the protocol snapshot before labels are constructed or models fitted.
    (output / "protocol.json").write_text(json.dumps({"recorded_before_fitting": datetime.now(timezone.utc).isoformat(),
                                                     "settings": settings}, indent=2) + "\n")
    rows, x, curves, split, digest = build_dataset(ROOT, settings, output)
    targets = [mode + "_utility" for mode in MODES] + ["btc_log_return"]
    y = rows[targets].to_numpy(dtype=float)
    print(json.dumps({"event": "dataset_ready", "split_counts": {k: len(v) for k, v in split.items()}}), flush=True)
    predictions, ridge, seeds, normalization, torch_version = fit_models(x, y, split, settings, output)
    ensemble = predictions.mean(axis=0)
    test = split["test"]
    test_rows, test_curves = rows.iloc[test].reset_index(drop=True), curves[test]
    learned_global = int(y[split["train"], :3].mean(axis=0).argmax())
    policies = {"Rules alone": test_rows.rules_choice.to_numpy(dtype=int),
                "LSTM ensemble": ensemble[test, :3].argmax(axis=1),
                "Ridge comparator": ridge[test, :3].argmax(axis=1),
                "Training-average mode": np.full(len(test), learned_global, dtype=int),
                "Always DCA": np.full(len(test), 1, dtype=int),
                "Always swing": np.full(len(test), 2, dtype=int),
                "Always reserve": np.zeros(len(test), dtype=int)}
    for i, seed in enumerate(settings["seeds"]):
        policies[f"LSTM seed {seed}"] = predictions[i, test, :3].argmax(axis=1)
    metrics, equity, weekly = {}, {}, {}
    decisions = test_rows.copy()
    for name, choices in policies.items():
        metrics[name], equity[name], weekly[name] = policy_metrics(test_rows, test_curves, choices)
        decisions[name + " choice"] = [MODES[c] for c in choices]
        decisions[name + " log_return"] = weekly[name]
    for j, mode in enumerate(MODES):
        decisions["predicted_" + mode + "_utility"] = ensemble[test, j]
    decisions["predicted_btc_log_return"] = ensemble[test, 3]
    decisions["ridge_btc_log_return"] = ridge[test, 3]
    decisions.to_csv(output / "test-predictions-and-decisions.csv", index=False)
    bootstrap = paired_bootstrap(weekly["LSTM ensemble"] - weekly["Rules alone"], settings)
    actual = y[test, 3]
    forecast = {}
    for name, prediction in (("LSTM ensemble", ensemble[test, 3]), ("Ridge comparator", ridge[test, 3]),
                             ("Zero return", np.zeros(len(test)))):
        forecast[name] = {"mae_log_return": float(np.abs(prediction - actual).mean()),
                          "rmse_log_return": float(np.sqrt(np.square(prediction - actual).mean())),
                          "direction_accuracy": None if name == "Zero return" else float((np.sign(prediction) == np.sign(actual)).mean())}
    best_actual = y[test, :3].argmax(axis=1)
    choice_accuracy = float((policies["LSTM ensemble"] == best_actual).mean())
    lower, upper = bootstrap["ci95"]
    conclusion = ("The LSTM chose reserve in every test week. It avoided rules' losses but matched the no-model cash baseline; no added value over always-reserve was demonstrated."
                  if np.all(policies["LSTM ensemble"] == 0) else
                  "Positive retrospective evidence against rules in this sample; independent future confirmation is still required."
                  if lower > 0 else "The LSTM underperforms rules in this retrospective sample."
                  if upper < 0 else "No clear evidence that the LSTM improves on rules; the paired confidence interval includes zero.")
    checks = {
        "input_checksum_matches_declared_archive": digest == settings["expected_input_sha256"],
        "training_outcomes_finish_before_validation": bool((pd.to_datetime(rows.iloc[split["train"]].end, utc=True) <= pd.Timestamp(settings["train_end_exclusive"])).all()),
        "validation_outcomes_finish_before_test": bool((pd.to_datetime(rows.iloc[split["validation"]].end, utc=True) <= pd.Timestamp(settings["validation_end_exclusive"])).all()),
        "test_starts_after_training_and_validation": bool((pd.to_datetime(test_rows.start, utc=True) >= pd.Timestamp(settings["validation_end_exclusive"])).all()),
        "nonoverlapping_target_periods": bool((pd.to_datetime(rows.start.iloc[1:], utc=True).reset_index(drop=True) >= pd.to_datetime(rows.end.iloc[:-1], utc=True).reset_index(drop=True)).all()),
        "all_models_use_same_test_episodes_and_action_set": all(len(p) == len(test) and np.isin(p, [0, 1, 2]).all() for p in policies.values()),
        "all_fixed_seeds_reported": len(predictions) == len(settings["seeds"]),
        "reserve_has_zero_return_and_costs": metrics["Always reserve"]["final_equity_usd"] == 10000 and metrics["Always reserve"]["fees_usd"] == 0,
        "finite_predictions_and_equities": bool(np.isfinite(predictions).all() and all(np.isfinite(v).all() for v in equity.values())),
        "not_a_pristine_project_level_holdout": True,
    }
    source_hash = hashlib.sha256(b"".join(p.read_bytes() for p in sorted((ROOT / "research_lstm").glob("*.py")))).hexdigest()
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(), "scope": settings["scope"],
               "test_start": test_rows.start.iloc[0], "test_end": test_rows.end.iloc[-1],
               "split_counts": {k: len(v) for k, v in split.items()}, "features": list(FEATURES),
               "input_sha256": digest, "experiment_code_sha256": source_hash,
               "settings_sha256": hashlib.sha256(settings_path.read_bytes()).hexdigest(),
               "environment": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__, "torch": torch_version},
               "model_seeds": seeds, "metrics": metrics, "prediction_metrics": forecast,
               "best_realized_utility_choice_accuracy": choice_accuracy, "paired_bootstrap": bootstrap,
               "conclusion": conclusion, "checks": checks,
               "limitations": ["Previously inspected historical archive; retrospective model test only.",
                               "Small weekly sample and one cryptocurrency; no hyperparameter search or fresh prospective validation.",
                               "Weekly liquidation/reset differs from continuous DCA; mode budgets are not equal immediate exposure.",
                               "No live broker, latency or observed depth; fixed modeled transaction costs.",
                               "Bootstrap interval does not remove researcher selection or regime dependence."]}
    (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    pd.DataFrame([{ "strategy": k, **{f: v for f, v in m.items() if f != "choices"}} for k, m in metrics.items()]).to_csv(output / "metrics.csv", index=False)
    render_report(output, summary, decisions, equity)
    if not all(checks.values()):
        raise ArithmeticError("One or more experiment integrity checks failed")
    print(json.dumps({"event": "complete", "conclusion": conclusion, "lstm": metrics["LSTM ensemble"],
                      "rules": metrics["Rules alone"], "paired_bootstrap": bootstrap}), flush=True)


def render_report(output, summary, decisions, equity):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"figure.dpi": 140, "axes.spines.top": False, "axes.spines.right": False})
    dates = pd.to_datetime(decisions.end, utc=True)
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), constrained_layout=True)
    for name in ("Rules alone", "LSTM ensemble", "Ridge comparator", "Always DCA", "Always reserve"):
        values = equity[name]
        axes[0].plot(dates, values[168::168], label=name, linewidth=1.7)
        axes[1].plot(dates, (1 - values / np.maximum.accumulate(values))[168::168] * 100, label=name)
    axes[0].set(title="Retrospective weekly strategy selection", ylabel="Compounded simulated equity (USD)")
    axes[0].legend(ncol=3, fontsize=8)
    axes[1].set(ylabel="Drawdown at week-end (%)", xlabel="Episode end (UTC)")
    fig.savefig(output / "strategy-comparison.png")
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(11, 3.8), constrained_layout=True)
    ax.plot(dates, decisions.btc_log_return * 100, label="Actual next-week BTC log return", alpha=.7)
    ax.plot(dates, decisions.predicted_btc_log_return * 100, label="LSTM prediction", linewidth=1.8)
    ax.axhline(0, color="gray", linewidth=.7)
    ax.set(title="Predictions made before each week versus observed outcomes", ylabel="Seven-day log return (%)", xlabel="Episode end (UTC)")
    ax.legend(fontsize=8)
    fig.savefig(output / "prediction-versus-actual.png")
    plt.close(fig)
    lines = ["# Does an LSTM improve DCA / swing / reserve selection?", "", "**" + summary["conclusion"] + "**", "",
             "This is a retrospective experiment, not a pristine unseen project-level holdout or a deployed trading model.", "",
             f"Test: {summary['test_start']} to {summary['test_end']}; {summary['split_counts']['test']} complete, nonoverlapping weeks. "
             f"Training: {summary['split_counts']['train']} weeks; validation: {summary['split_counts']['validation']} weeks.", "",
             "## Profit and risk", "", "| Policy | Net return | Hourly max drawdown | Fees USD | DCA / swing / reserve weeks |",
             "| --- | ---: | ---: | ---: | --- |"]
    for name, m in summary["metrics"].items():
        c = m["choices"]
        lines.append(f"| {name} | {m['net_return']*100:.2f}% | {m['max_drawdown']*100:.2f}% | {m['fees_usd']:.2f} | {c['dca']} / {c['swing']} / {c['reserve']} |")
    b = summary["paired_bootstrap"]
    lines += ["", "![Strategy comparison](strategy-comparison.png)", "", "## Prediction versus actual", "",
              "The model predicts each mode's next-week utility plus a separate BTC log return. The highest predicted utility chooses the mode. "
              "Forecast accuracy and trading profitability are separate questions.", "",
              "| Forecast | Mean absolute log-return error | Direction accuracy |", "| --- | ---: | ---: |"]
    for name, m in summary["prediction_metrics"].items():
        direction = "Not applicable (predicts zero)" if m['direction_accuracy'] is None else f"{m['direction_accuracy']*100:.1f}%"
        lines.append(f"| {name} | {m['mae_log_return']*100:.3f} percentage points | {direction} |")
    lines += ["", "![Forecast comparison](prediction-versus-actual.png)", "", "## Evidence strength", "",
              f"The paired mean weekly log-return difference (LSTM minus rules) is {b['mean_weekly_log_return_difference']*100:.4f} percentage points; "
              f"the four-week-block 95% interval is [{b['ci95'][0]*100:.4f}, {b['ci95'][1]*100:.4f}]. "
              "This is an exploratory uncertainty estimate, not proof of durable profitability.", "",
              "Three fixed seeds are all shown; the primary policy averages their predictions. Validation MSE alone selects the epoch. "
              "The test never selects seeds, thresholds, stopping epochs or the preferred conclusion.", "",
              "## What was held constant", "",
              "The rules baseline and LSTM choose the same three allocation profiles: 90% DCA, 90% swing, or full reserve. "
              "A 10% reserve accompanies the first two modes. Both use identical hourly prices, entry/exit rules, 0.6% fees and 0.1% adverse slippage per fill. "
              "A swing allocation waits for the existing breakout/volume signal. Every complete week begins in cash and ends with liquidation; returns compound across weeks.", "",
              "The model uses 30 completed daily observations of returns, volatility, range, relative volume and moving-average distances. "
              "Incomplete periods are excluded, never filled. The strict 200-day complete-close feature history also excludes long stretches after a missing daily input; "
              "the resulting test ends in October 2025 although the archive extends into October 2026. The old operational paper account is untouched.", "", "## Limits", ""]
    lines += ["- " + value for value in summary["limitations"]]
    lines += ["", "## Reproduce and inspect", "", "Run `python -m research_lstm.run` after installing the research and LSTM requirements. "
              "See `docs/lstm-experiment.md` and `config/lstm-experiment.json` for the declared protocol. "
              "`test-predictions-and-decisions.csv` contains each prediction, realized mode outcome and choice; "
              "`training-history.csv`, `normalization.json`, NPZ weights and `summary.json` preserve the model and checks.", "",
              "The existing historical archive has already been used in earlier research. A new prospective or independently reserved dataset is required before making a confirmatory claim or changing the paper policy.", ""]
    (output / "review.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
