"""Small-grid, chronological allocation experiments with a sealed final holdout."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import platform
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import PreparedData, Profile, simulate
from .config import AgentConfig
from .features import build_features
from .market_data import DataError, load_candles, quality_report

RESEARCH_FIELDS = {"train_days", "episode_days", "episode_stride_days", "holdout_days", "allocation_update_days",
                   "minimum_context_episodes", "drawdown_penalty", "allocation_grid", "dip_thresholds",
                   "atr_multipliers", "defensive_dca_exit_choices", "bear_confirmation_days", "bear_momentum_days", "bear_trend_days"}


def load_research_settings(path: str | Path) -> dict:
    settings = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(settings, dict) or set(settings) != RESEARCH_FIELDS:
        raise ValueError("Research configuration must contain exactly the documented fields")
    for field in RESEARCH_FIELDS - {"drawdown_penalty", "allocation_grid", "dip_thresholds", "atr_multipliers", "defensive_dca_exit_choices"}:
        if type(settings[field]) is not int or settings[field] <= 0:
            raise ValueError(f"research.{field} must be a positive integer")
    if settings["train_days"] < 4 * settings["episode_days"]:
        raise ValueError("Training history must allow at least four complete episodes")
    if settings["episode_stride_days"] < settings["episode_days"]:
        raise ValueError("Research episodes must not overlap")
    penalty = settings["drawdown_penalty"]
    if isinstance(penalty, bool) or not isinstance(penalty, (int, float)) or not math.isfinite(penalty) or penalty < 0:
        raise ValueError("Drawdown penalty must be finite and nonnegative")
    if not settings["defensive_dca_exit_choices"] or any(type(value) is not bool for value in settings["defensive_dca_exit_choices"]):
        raise ValueError("Defensive exit choices must be a nonempty list of booleans")
    if not settings["allocation_grid"] or not settings["dip_thresholds"] or not settings["atr_multipliers"]:
        raise ValueError("Search grids cannot be empty")
    for weights in settings["allocation_grid"]:
        if not isinstance(weights, list) or len(weights) != 3:
            raise ValueError("Each allocation must contain DCA, swing, and reserve fractions")
        Profile(*weights)
    for threshold, multiplier in itertools.product(settings["dip_thresholds"], settings["atr_multipliers"]):
        Profile(0.7, 0.2, 0.1, threshold, multiplier)
    return settings


def candidate_profiles(settings: dict) -> list[Profile]:
    profiles = {}
    for weights, dip, atr, defensive in itertools.product(settings["allocation_grid"], settings["dip_thresholds"],
                                                         settings["atr_multipliers"], settings["defensive_dca_exit_choices"]):
        dca, swing, reserve = weights
        profile = Profile(dca, swing, reserve, dip if dca > 0 else None,
                          atr if swing > 0 else 1.5, defensive if dca > 0 else False)
        profiles[profile.identifier] = profile
    return list(profiles.values())


def rank_profiles(episodes: pd.DataFrame, cutoff: pd.Timestamp, settings: dict, regime: str | None = None) -> pd.DataFrame:
    """Select only outcomes fully observed by cutoff; never use a future episode."""
    start_limit = cutoff - pd.Timedelta(days=settings["train_days"])
    eligible = episodes.loc[(episodes["end_utc"] <= cutoff) & (episodes["start_utc"] >= start_limit)].copy()
    if regime is not None:
        eligible = eligible.loc[eligible["start_regime"] == regime]
    if eligible.empty:
        return pd.DataFrame()
    ranked = eligible.groupby("profile_id", sort=False).agg(
        episodes=("net_return", "size"), mean_log_return=("log_return", "mean"),
        mean_drawdown=("max_drawdown", "mean"), worst_drawdown=("max_drawdown", "max"),
        mean_return=("net_return", "mean"), mean_fees_usd=("fees_usd", "mean"))
    ranked["score"] = ranked["mean_log_return"] - settings["drawdown_penalty"] * ranked["mean_drawdown"]
    ranked = ranked.reset_index().sort_values(["score", "mean_drawdown", "profile_id"], ascending=[False, True, True])
    return ranked


def fit_selector(episodes: pd.DataFrame, cutoff: pd.Timestamp, settings: dict) -> dict:
    global_ranking = rank_profiles(episodes, cutoff, settings)
    if global_ranking.empty or int(global_ranking.iloc[0]["episodes"]) < 4:
        raise ValueError("At least four earlier complete episodes are needed for allocation selection")
    regimes = {}
    for regime in ("bull", "bear", "mixed", "high_volatility"):
        ranking = rank_profiles(episodes, cutoff, settings, regime)
        if not ranking.empty and int(ranking.iloc[0]["episodes"]) >= settings["minimum_context_episodes"]:
            regimes[regime] = {"profile_id": str(ranking.iloc[0]["profile_id"]),
                              "training_episodes": int(ranking.iloc[0]["episodes"])}
    return {"fitted_before_utc": cutoff.isoformat(), "global_profile_id": str(global_ranking.iloc[0]["profile_id"]),
            "global_training_episodes": int(global_ranking.iloc[0]["episodes"]), "regimes": regimes,
            "objective": "mean(log(1 + net_return)) - drawdown_penalty * mean(max_drawdown)",
            "drawdown_penalty": settings["drawdown_penalty"]}


def _policy(data: PreparedData, windows: list[dict], profiles: dict[str, Profile], contextual: bool):
    def choose(index: int):
        timestamp = data.frame.index[index]
        window = next(window for window in reversed(windows) if timestamp >= window["start_utc"])
        model = window["model"]
        context = model["regimes"].get(str(data.regime[index])) if contextual else None
        profile_id = context["profile_id"] if context else model["global_profile_id"]
        return profiles[profile_id], {"selection_source": "regime" if context else "global_fallback" if contextual else "global",
                                      "model_fitted_before_utc": model["fitted_before_utc"],
                                      "training_episodes": context["training_episodes"] if context else model["global_training_episodes"]}
    return choose


def _benchmarks(data: PreparedData, start: int, end: int, app: dict, settings: dict, windows: list[dict], profiles: dict):
    default = Profile(app["portfolio"]["dca_fraction"], app["portfolio"]["swing_fraction"], app["portfolio"]["reserve_fraction"],
                      app["dca"]["dip_trigger_fraction"], app["swing"]["atr_multiplier"])
    configurations = [
        ("Cash", Profile(0, 0, 1, None), False, False),
        ("Buy and hold", Profile(1, 0, 0, None), True, False),
        ("Scheduled DCA", Profile(1, 0, 0, None), False, False),
        ("Dip DCA", Profile(1, 0, 0, app["dca"]["dip_trigger_fraction"]), False, False),
        ("Swing only", Profile(0, 1, 0, None, app["swing"]["atr_multiplier"]), False, True),
        ("Fixed hybrid", default, False, True),
        ("Defensive hybrid", Profile(default.dca, default.swing, default.reserve, default.dip_threshold, default.atr_multiplier, True), False, True),
    ]
    results = [simulate(data, start, end, app, settings, name=name, profile=profile,
                        buy_and_hold=buy_hold, guard_enabled=guard) for name, profile, buy_hold, guard in configurations]
    for name, contextual in (("Optimized global", False), ("Feature allocation", True)):
        results.append(simulate(data, start, end, app, settings, name=name,
                                policy=_policy(data, windows, profiles, contextual)))
    return results


def run_research(candle_path: str | Path, output: str | Path, config: AgentConfig,
                 research_path: str | Path, progress=print) -> dict:
    settings = load_research_settings(research_path)
    candles = load_candles(candle_path)
    manifest_path = Path(candle_path).with_suffix(".manifest.json")
    quality = quality_report(candles)
    data_hash = hashlib.sha256(Path(candle_path).read_bytes()).hexdigest()
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("csv_sha256") != data_hash:
            raise DataError("Data manifest checksum does not match the input CSV")
        quality = manifest
    features = build_features(candles, config.data, settings)
    data = PreparedData.from_frame(features)
    ready = np.flatnonzero((features["regime"] != "unknown").to_numpy() & data.ready)
    if not len(ready):
        raise DataError("No complete long-term feature history is available")
    first_time = features.index[max(int(ready[0]), 1)].ceil("1D")
    end_time = features.index[-1] + pd.Timedelta(hours=1)
    holdout_start = (end_time - pd.Timedelta(days=settings["holdout_days"])).floor("1D")
    first_test = first_time + pd.Timedelta(days=4 * settings["episode_stride_days"])
    if first_test >= holdout_start:
        raise DataError("Not enough history for training, walk-forward development, and sealed holdout")

    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    profiles_list = candidate_profiles(settings)
    profiles = {profile.identifier: profile for profile in profiles_list}
    episode_rows = []
    episode_start = first_time
    number = 0
    while episode_start + pd.Timedelta(days=settings["episode_days"]) <= holdout_start:
        episode_end = episode_start + pd.Timedelta(days=settings["episode_days"])
        start_index, end_index = features.index.searchsorted(episode_start), features.index.searchsorted(episode_end)
        if end_index - start_index < 24:
            raise DataError("An episode has insufficient candle coverage")
        for profile in profiles_list:
            trial = simulate(data, start_index, end_index, config.data, settings, name=profile.identifier, profile=profile, detailed=False)
            metrics = trial.metrics()
            episode_rows.append({"start_utc": episode_start, "end_utc": episode_end,
                                 "start_regime": str(data.regime[start_index]), "profile_id": profile.identifier,
                                 "net_return": metrics["net_return"], "log_return": math.log1p(metrics["net_return"]),
                                 "max_drawdown": metrics["max_drawdown"], "fees_usd": metrics["fees_usd"]})
        number += 1
        progress(json.dumps({"event": "training_episode_complete", "episode": number, "through_utc": episode_end.isoformat(),
                             "candidate_profiles": len(profiles_list)}))
        episode_start += pd.Timedelta(days=settings["episode_stride_days"])
    episodes = pd.DataFrame(episode_rows)
    episodes.to_csv(output_path / "training_episodes.csv", index=False)
    pd.DataFrame([profile.to_dict() for profile in profiles_list]).to_csv(output_path / "candidate_profiles.csv", index=False)

    windows = []
    cursor = first_test
    while cursor < holdout_start:
        windows.append({"start_utc": cursor, "model": fit_selector(episodes, cursor, settings)})
        cursor += pd.Timedelta(days=settings["episode_days"])
    sealed_model = fit_selector(episodes, holdout_start, settings)
    windows.append({"start_utc": holdout_start, "model": sealed_model})
    serialized_windows = [{"start_utc": window["start_utc"].isoformat(), "model": window["model"]} for window in windows]
    (output_path / "selector_models.json").write_text(json.dumps(serialized_windows, indent=2), encoding="utf-8")
    ranking = rank_profiles(episodes, holdout_start, settings)
    ranking = ranking.merge(pd.DataFrame([profile.to_dict() for profile in profiles_list]), on="profile_id")
    ranking.to_csv(output_path / "training_ranking.csv", index=False)
    sensitivity = []
    for penalty in sorted({0.0, settings["drawdown_penalty"], 1.5}):
        alternative = {**settings, "drawdown_penalty": penalty}
        best = rank_profiles(episodes, holdout_start, alternative).iloc[0]
        sensitivity.append({"drawdown_penalty": penalty, **best.to_dict()})
    pd.DataFrame(sensitivity).to_csv(output_path / "objective_sensitivity.csv", index=False)
    # The final model is frozen throughout the holdout. Its inputs may evolve;
    # its rankings and regime mappings never learn from holdout outcomes.
    holdout_windows = [{"start_utc": holdout_start, "model": sealed_model}]
    start_index = features.index.searchsorted(first_test)
    holdout_index = features.index.searchsorted(holdout_start)
    development = _benchmarks(data, start_index, holdout_index, config.data, settings, windows, profiles)
    progress(json.dumps({"event": "development_backtests_complete", "strategies": len(development)}))
    holdout = _benchmarks(data, holdout_index, len(features), config.data, settings, holdout_windows, profiles)
    progress(json.dumps({"event": "sealed_holdout_complete", "strategies": len(holdout)}))
    for label, results in (("development", development), ("holdout", holdout)):
        pd.DataFrame([result.metrics() for result in results]).to_csv(output_path / f"{label}_metrics.csv", index=False)
        for result in results:
            identifier = result.name.lower().replace(" ", "_")
            result.curve.to_csv(output_path / f"{label}_{identifier}_curve.csv")
            pd.DataFrame(result.trades).to_csv(output_path / f"{label}_{identifier}_fills.csv", index=False)
            if result.decisions:
                pd.DataFrame(result.decisions).to_csv(output_path / f"{label}_{identifier}_decisions.csv", index=False)
    features.to_csv(output_path / "features.csv")
    (output_path / "data_quality.json").write_text(json.dumps(quality, indent=2), encoding="utf-8")
    (output_path / "research_settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    (output_path / "application_settings.json").write_text(json.dumps(config.data, indent=2), encoding="utf-8")
    annual_rows = []
    for result in development:
        prior = result.initial_cash
        for year, group in result.curve.groupby(result.curve.index.year):
            values = group["equity_usd"]
            annual_rows.append({"strategy": result.name, "year": year,
                                "return": float(values.iloc[-1] / prior - 1),
                                "within_year_drawdown": float((1 - values / values.cummax().clip(lower=prior)).max()),
                                "first_observation": values.index[0].isoformat(), "last_observation": values.index[-1].isoformat()})
            prior = float(values.iloc[-1])
    pd.DataFrame(annual_rows).to_csv(output_path / "development_annual_results.csv", index=False)
    summary = {"candidate_profiles": len(profiles_list), "nonoverlapping_training_episodes": number,
               "walk_forward_models": len(windows) - 1, "research_start_utc": first_time.isoformat(),
               "development_start_utc": first_test.isoformat(), "holdout_start_utc": holdout_start.isoformat(),
               "end_exclusive_utc": end_time.isoformat(), "config_checksum": config.checksum,
               "data_sha256": data_hash,
               "initial_cash_usd": float(config.initial_cash),
               "fee_fraction_per_fill": config.data["risk"]["fee_fraction_per_fill"],
               "slippage_fraction_per_fill": config.data["risk"]["slippage_fraction_per_fill"],
               "python_version": platform.python_version(), "numpy_version": np.__version__, "pandas_version": pd.__version__,
               "sealed_model": sealed_model, "holdout_metrics": [result.metrics() for result in holdout],
               "development_metrics": [result.metrics() for result in development]}
    from .research_report import create_report

    create_report(output_path, summary, candles, features, development, holdout, ranking, quality)
    (output_path / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    return summary
