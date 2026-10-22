"""Causal, weekly LSTM allocation comparison using the existing cost simulator."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from bitcoin_agent.backtest import PreparedData, Profile, simulate
from bitcoin_agent.config import load_config
from bitcoin_agent.features import build_features
from bitcoin_agent.market_data import load_candles

MODES = ("reserve", "dca", "swing")  # First maximum makes reserve the tie-break.
PROFILES = (Profile(0, 0, 1, None), Profile(.9, 0, .1), Profile(0, .9, .1))
FEATURES = ("return_1", "return_7", "return_30", "volatility_20",
            "range_fraction", "volume_relative", "sma_distance", "ema_distance")


def daily_features(candles):
    """Index each completed day's features by its actual availability instant."""
    daily = candles.resample("1D").agg({"open": "first", "high": "max", "low": "min",
                                       "close": "last", "volume": "sum"})
    complete = candles.close.resample("1D").count().eq(24)
    daily.loc[~complete, :] = np.nan
    close = daily.close
    out = pd.DataFrame(index=daily.index)
    out["return_1"] = np.log(close / close.shift(1))
    out["return_7"] = np.log(close / close.shift(7))
    out["return_30"] = np.log(close / close.shift(30))
    out["volatility_20"] = out.return_1.rolling(20).std()
    out["range_fraction"] = (daily.high - daily.low) / close
    out["volume_relative"] = daily.volume / daily.volume.shift(1).rolling(20).mean() - 1
    out["sma_distance"] = close / close.rolling(200).mean() - 1
    out["ema_distance"] = close / close.ewm(span=20, adjust=False, min_periods=20).mean() - 1
    out.index = out.index + pd.Timedelta(days=1)
    return out.replace([np.inf, -np.inf], np.nan)


def time_split(rows, settings):
    train_end = pd.Timestamp(settings["train_end_exclusive"])
    validation_end = pd.Timestamp(settings["validation_end_exclusive"])
    start, end = pd.to_datetime(rows.start, utc=True), pd.to_datetime(rows.end, utc=True)
    return {"train": np.flatnonzero((end <= train_end).to_numpy()),
            "validation": np.flatnonzero(((start >= train_end) & (end <= validation_end)).to_numpy()),
            "test": np.flatnonzero((start >= validation_end).to_numpy())}


def build_dataset(root, settings, output):
    path = root / settings["input_csv"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != settings["expected_input_sha256"]:
        raise ValueError("Historical input differs from the declared checksum")
    app = load_config(root / "config/default.json").data
    research = json.loads((root / "config/research.json").read_text())
    candles = load_candles(path)
    hourly = build_features(candles, app, research)
    prepared = PreparedData.from_frame(hourly)
    daily = daily_features(candles)
    rows, sequences, curves, skipped = [], [], [], []
    length, horizon = settings["sequence_days"], settings["horizon_days"]
    for start in pd.date_range(candles.index[0].ceil("1D"), candles.index[-1], freq="W-MON"):
        end = start + pd.Timedelta(days=horizon)
        if end > candles.index[-1] + pd.Timedelta(hours=1):
            continue
        days = pd.date_range(start - pd.Timedelta(days=length - 1), start, freq="1D")
        sequence = daily.reindex(days).to_numpy(dtype=float)
        a, b = hourly.index.searchsorted(start), hourly.index.searchsorted(end)
        required = pd.date_range(start, end, freq="1h", inclusive="left")
        if a < 1 or not hourly.index[a:b].equals(required) or not np.isfinite(sequence).all():
            skipped.append({"start": start.isoformat(), "reason": "missing_hour_or_incomplete_feature_history"})
            continue
        if not prepared.ready[a - 1] or not prepared.contiguous[a]:
            skipped.append({"start": start.isoformat(), "reason": "prior_signal_unready"})
            continue
        row = {"start": start.isoformat(), "end": end.isoformat(), "regime": str(prepared.regime[a])}
        row["rules_choice"] = 0 if row["regime"] in {"bear", "high_volatility", "unknown"} else (2 if prepared.swing_signal[a - 1] else 1)
        episode_curves = []
        for name, profile in zip(MODES, PROFILES):
            trial = simulate(prepared, a, b, app, research, name=name, profile=profile)
            metrics = trial.metrics()
            # Include the uninvested initial point when deriving the episode loss.
            eq = trial.curve.equity_usd.to_numpy() / trial.initial_cash
            dd = float((1 - eq / np.maximum.accumulate(np.r_[1., eq])[1:]).max())
            row[name + "_return"] = float(eq[-1] - 1)
            row[name + "_drawdown"] = dd
            row[name + "_utility"] = math.log(eq[-1]) - settings["drawdown_penalty"] * dd
            row[name + "_fees_fraction"] = metrics["fees_usd"] / trial.initial_cash
            row[name + "_exposure"] = metrics["mean_btc_exposure"]
            row[name + "_fills"] = metrics["fills"]
            episode_curves.append(eq)
        row["btc_log_return"] = math.log(candles.close.iloc[b - 1] / candles.open.iloc[a])
        rows.append(row)
        sequences.append(sequence)
        curves.append(episode_curves)
    frame = pd.DataFrame(rows)
    split = time_split(frame, settings)
    if any(len(part) < 20 for part in split.values()):
        raise ValueError("Insufficient complete weekly episodes for chronological evaluation")
    frame["split"] = "boundary_excluded"
    for name, part in split.items():
        frame.loc[part, "split"] = name
    output.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output / "episodes.csv", index=False)
    (output / "excluded-episodes.json").write_text(json.dumps(skipped, indent=2) + "\n")
    return frame, np.asarray(sequences), np.asarray(curves), split, digest


def fit_models(x, y, split, settings, output):
    import torch
    from torch import nn
    torch.set_num_threads(2)
    torch.use_deterministic_algorithms(True)
    train, validation = split["train"], split["validation"]
    mean, scale = x[train].mean(axis=(0, 1)), x[train].std(axis=(0, 1))
    scale = np.maximum(scale, 1e-8)
    ymean, yscale = y[train].mean(axis=0), np.maximum(y[train].std(axis=0), 1e-4)
    xx = torch.tensor(np.clip((x - mean) / scale, -10, 10), dtype=torch.float32)
    yy = torch.tensor((y - ymean) / yscale, dtype=torch.float32)

    class Forecast(nn.Module):
        def __init__(self):
            super().__init__()
            self.lstm = nn.LSTM(len(FEATURES), settings["hidden_units"], batch_first=True)
            self.head = nn.Linear(settings["hidden_units"], 4)

        def forward(self, values):
            return self.head(self.lstm(values)[0][:, -1])

    predictions, histories, seed_info = [], [], []
    for seed in settings["seeds"]:
        torch.manual_seed(seed)
        model = Forecast()
        optimizer = torch.optim.AdamW(model.parameters(), lr=settings["learning_rate"], weight_decay=settings["weight_decay"])
        loss_fn = nn.MSELoss()
        best, best_state, stale, best_epoch = float("inf"), None, 0, 0
        for epoch in range(1, settings["max_epochs"] + 1):
            model.train()
            optimizer.zero_grad()
            loss = loss_fn(model(xx[train]), yy[train])
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.)
            optimizer.step()
            model.eval()
            with torch.no_grad():
                val_loss = float(loss_fn(model(xx[validation]), yy[validation]))
            histories.append({"seed": seed, "epoch": epoch, "train_loss": float(loss.detach()), "validation_loss": val_loss})
            if val_loss < best - 1e-6:
                best, best_state, stale, best_epoch = val_loss, copy.deepcopy(model.state_dict()), 0, epoch
            else:
                stale += 1
            if stale >= settings["patience"]:
                break
        model.load_state_dict(best_state)
        model.eval()
        with torch.no_grad():
            prediction = model(xx).numpy() * yscale + ymean
        predictions.append(prediction)
        # NPZ avoids publishing an executable pickle; architecture is above.
        np.savez_compressed(output / f"weights-seed-{seed}.npz", **{key: value.numpy() for key, value in best_state.items()})
        seed_info.append({"seed": seed, "selected_epoch": best_epoch, "validation_mse_standardized": best,
                          "parameters": sum(p.numel() for p in model.parameters())})
        print(json.dumps({"event": "lstm_seed_complete", **seed_info[-1]}), flush=True)
    pd.DataFrame(histories).to_csv(output / "training-history.csv", index=False)
    normalization = {"input_mean": mean.tolist(), "input_scale": scale.tolist(), "target_mean": ymean.tolist(), "target_scale": yscale.tolist()}
    (output / "normalization.json").write_text(json.dumps(normalization, indent=2) + "\n")
    # A small non-sequential comparator using exactly the same training labels.
    flat = np.c_[np.ones(len(x)), np.clip((x[:, -1] - mean) / scale, -10, 10)]
    penalty = np.eye(flat.shape[1]); penalty[0, 0] = 0
    beta = np.linalg.solve(flat[train].T @ flat[train] + penalty, flat[train].T @ ((y[train] - ymean) / yscale))
    ridge = flat @ beta * yscale + ymean
    return np.asarray(predictions), ridge, seed_info, normalization, torch.__version__


def policy_metrics(rows, curves, choices):
    capital, all_equity, fees, fills, exposure = 10000., [10000.], 0., 0, []
    weekly = []
    for i, choice in enumerate(choices):
        name = MODES[int(choice)]
        all_equity.extend(capital * curves[i, choice])
        fees += capital * rows.iloc[i][name + "_fees_fraction"]
        fills += int(rows.iloc[i][name + "_fills"])
        exposure.append(float(rows.iloc[i][name + "_exposure"]))
        ret = float(rows.iloc[i][name + "_return"])
        weekly.append(math.log1p(ret))
        capital *= 1 + ret
    eq = np.asarray(all_equity)
    return {"final_equity_usd": capital, "net_return": capital / 10000 - 1,
            "max_drawdown": float((1 - eq / np.maximum.accumulate(eq)).max()),
            "fees_usd": float(fees), "fills": fills, "mean_btc_exposure": float(np.mean(exposure)),
            "choices": {mode: int(np.sum(choices == i)) for i, mode in enumerate(MODES)}}, eq, np.asarray(weekly)


def paired_bootstrap(difference, settings):
    rng = np.random.default_rng(101)
    n, block = len(difference), settings["bootstrap_block_weeks"]
    means = []
    for _ in range(settings["bootstrap_repetitions"]):
        starts = rng.integers(0, n - block + 1, size=math.ceil(n / block))
        sample = np.concatenate([difference[start:start + block] for start in starts])[:n]
        means.append(float(sample.mean()))
    return {"mean_weekly_log_return_difference": float(np.mean(difference)),
            "ci95": np.quantile(means, [.025, .975]).tolist(), "block_weeks": block,
            "repetitions": settings["bootstrap_repetitions"]}
