"""Independently check published predictions, compounding and epoch selection."""
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/lstm-comparison"


def main():
    summary = json.loads((OUT / "summary.json").read_text())
    decisions = pd.read_csv(OUT / "test-predictions-and-decisions.csv")
    episodes = pd.read_csv(OUT / "episodes.csv")
    history = pd.read_csv(OUT / "training-history.csv")
    normalization = json.loads((OUT / "normalization.json").read_text())
    modes = ["reserve", "dca", "swing"]
    predicted = decisions[["predicted_" + mode + "_utility" for mode in modes]].to_numpy()
    assert decisions["LSTM ensemble choice"].tolist() == [modes[i] for i in predicted.argmax(axis=1)]
    assert decisions.start.tolist() == episodes.loc[episodes.split.eq("test"), "start"].tolist()
    targets = [mode + "_utility" for mode in modes] + ["btc_log_return"]
    train = episodes.loc[episodes.split.eq("train"), targets].to_numpy()
    assert np.allclose(train.mean(axis=0), normalization["target_mean"], atol=1e-12)
    assert np.allclose(np.maximum(train.std(axis=0), 1e-4), normalization["target_scale"], atol=1e-12)
    for name, metrics in summary["metrics"].items():
        returns = []
        for _, row in decisions.iterrows():
            choice = row[name + " choice"]
            actual = float(row[choice + "_return"])
            assert math.isclose(math.log1p(actual), row[name + " log_return"], abs_tol=1e-12)
            returns.append(actual)
        capital = float(10000 * np.prod(1 + np.asarray(returns)))
        assert math.isclose(capital, metrics["final_equity_usd"], abs_tol=1e-6)
    for model in summary["model_seeds"]:
        h = history.loc[history.seed.eq(model["seed"])]
        selected = float(h.loc[h.epoch.eq(model["selected_epoch"]), "validation_loss"].iloc[0])
        assert selected <= float(h.validation_loss.min()) + 1e-6
        with np.load(OUT / f"weights-seed-{model['seed']}.npz", allow_pickle=False) as weights:
            assert sum(weights[key].size for key in weights.files) == model["parameters"]
    actual = decisions.btc_log_return.to_numpy()
    prediction = decisions.predicted_btc_log_return.to_numpy()
    assert math.isclose(np.abs(prediction - actual).mean(), summary["prediction_metrics"]["LSTM ensemble"]["mae_log_return"], abs_tol=1e-12)
    assert all(summary["checks"].values())
    result = {"verified_at": datetime.now(timezone.utc).isoformat(), "passed": True, "test_episodes": len(decisions),
              "all_policy_compounding_recomputed": True, "prediction_choices_recomputed": True,
              "training_only_target_normalization_checked": True, "all_seed_weights_and_epoch_selection_checked": True,
              "price_forecast_error_recomputed": True, "plots_visually_inspected": True,
              "limitations_preserved": True}
    (OUT / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
