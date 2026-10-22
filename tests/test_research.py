"""Causality, cost accounting, gap execution, and training-cutoff checks."""

import copy
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from bitcoin_agent.backtest import PreparedData, Profile, simulate
from bitcoin_agent.config import load_config
from bitcoin_agent.features import build_features
from bitcoin_agent.market_data import DataError, quality_report, validate_candles
from bitcoin_agent.research import candidate_profiles, fit_selector, load_research_settings, rank_profiles

ROOT = Path(__file__).resolve().parents[1]
APP = load_config(ROOT / "config/default.json").data
RESEARCH = load_research_settings(ROOT / "config/research.json")


def candles(hours=60):
    index = pd.date_range("2020-01-01", periods=hours, freq="h", tz="UTC")
    close = 100 + np.arange(hours) * 0.003
    return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close,
                         "volume": np.full(hours, 10.0)}, index=index)


def prepared(frame=None):
    frame = candles() if frame is None else frame.copy()
    frame["atr"] = 2.0
    frame["swing_signal"] = False
    frame["hourly_ready"] = True
    frame["contiguous_previous"] = frame.index.to_series().diff().eq(pd.Timedelta(hours=1))
    frame["confirmed_bear"] = False
    frame["regime"] = "bull"
    return PreparedData.from_frame(frame)


class DataAndFeatureTests(unittest.TestCase):
    def test_bad_ohlc_and_duplicates_rejected(self):
        frame = candles()
        frame.iloc[3, frame.columns.get_loc("low")] = 999
        with self.assertRaises(DataError):
            validate_candles(frame)
        frame = candles()
        with self.assertRaises(DataError):
            validate_candles(pd.concat([frame.iloc[:1], frame]))

    def test_missing_candle_is_reported_without_imputation(self):
        frame = candles().drop(candles().index[10])
        report = quality_report(frame)
        self.assertEqual(report["missing_hours"], 1)
        self.assertEqual(len(frame), 59)

    def test_future_prices_cannot_change_past_features(self):
        frame = candles(220 * 24)
        baseline = build_features(frame, APP, RESEARCH)
        altered = frame.copy()
        altered.loc[altered.index[215 * 24]:, ["open", "high", "low", "close"]] *= 1.9
        altered.loc[altered.index[215 * 24]:, "volume"] *= 9
        result = build_features(altered, APP, RESEARCH)
        pd.testing.assert_frame_equal(baseline.iloc[:215 * 24], result.iloc[:215 * 24])

    def test_daily_feature_is_not_available_during_its_own_day(self):
        settings = copy.deepcopy(RESEARCH)
        settings["bear_trend_days"] = 3
        settings["bear_momentum_days"] = 2
        result = build_features(candles(40 * 24), APP, settings)
        self.assertTrue(pd.isna(result.loc["2020-01-03 23:00:00+00:00", "daily_sma200_distance"]))
        self.assertTrue(pd.notna(result.loc["2020-01-04 00:00:00+00:00", "daily_sma200_distance"]))

    def test_hourly_indicators_restart_after_gap(self):
        frame = candles(200).drop(candles(200).index[100])
        result = build_features(frame, APP, RESEARCH)
        first_after = result.loc["2020-01-05 05:00:00+00:00"]
        self.assertFalse(first_after["contiguous_previous"])
        self.assertFalse(first_after["hourly_ready"])
        self.assertTrue(pd.isna(first_after["atr"]))

    def test_partial_day_retains_verified_close_but_not_daily_atr(self):
        frame = candles(220 * 24)
        missing = frame.index[210 * 24 + 5]
        baseline = build_features(frame, APP, RESEARCH)
        result = build_features(frame.drop(missing), APP, RESEARCH)
        next_day = frame.index[211 * 24]
        self.assertAlmostEqual(result.loc[next_day, "daily_sma200_distance"], baseline.loc[next_day, "daily_sma200_distance"])
        self.assertTrue(pd.isna(result.loc[next_day, "daily_atr_pct"]))


class ExecutionTests(unittest.TestCase):
    def test_buy_and_hold_accounts_for_both_sides_costs(self):
        frame = candles(4)
        frame.loc[:, ["open", "close"]] = 100.0
        frame.loc[:, "high"] = 101.0
        frame.loc[:, "low"] = 99.0
        result = simulate(prepared(frame), 1, 4, APP, RESEARCH, name="buyhold", profile=Profile(1, 0, 0, None),
                          buy_and_hold=True, guard_enabled=False)
        fee, slip = APP["risk"]["fee_fraction_per_fill"], APP["risk"]["slippage_fraction_per_fill"]
        expected = 10000 / ((1 + slip) * (1 + fee)) * (1 - slip) * (1 - fee)
        self.assertAlmostEqual(result.metrics()["final_equity_usd"], expected, places=7)
        self.assertEqual(len(result.trades), 2)

    def test_swing_signal_enters_at_next_open(self):
        data = prepared()
        data.frame.iloc[0, data.frame.columns.get_loc("swing_signal")] = True
        # PreparedData arrays reflect the shared frame column in this fixture.
        data = PreparedData.from_frame(data.frame)
        result = simulate(data, 1, 4, APP, RESEARCH, name="swing", profile=Profile(0, 1, 0, None))
        entry = next(trade for trade in result.trades if trade["side"] == "buy")
        self.assertEqual(entry["time_utc"], data.frame.index[1].isoformat())
        self.assertAlmostEqual(entry["reference_price"], data.open[1])

    def test_gap_stop_uses_gap_price_and_can_exceed_planned_loss(self):
        frame = candles(4)
        frame.loc[frame.index[2]:, ["open", "close"]] = 80.0
        frame.loc[frame.index[2]:, "high"] = 81.0
        frame.loc[frame.index[2]:, "low"] = 79.0
        data = prepared(frame)
        data.frame.loc[data.frame.index[0], "swing_signal"] = True
        data = PreparedData.from_frame(data.frame)
        result = simulate(data, 1, 4, APP, RESEARCH, name="gap", profile=Profile(0, 1, 0, None))
        exit_fill = next(trade for trade in result.trades if trade["side"] == "sell")
        self.assertEqual(exit_fill["reason"], "gap_stop")
        self.assertAlmostEqual(exit_fill["reference_price"], 80.0)
        self.assertGreater(10000 - result.metrics()["final_equity_usd"], 50)

    def test_ambiguous_bar_uses_stop_before_profit_target(self):
        data = prepared(candles(3))
        data.frame.loc[data.frame.index[0], "swing_signal"] = True
        data.frame.loc[data.frame.index[1], ["low", "high"]] = [90.0, 110.0]
        data = PreparedData.from_frame(data.frame)
        result = simulate(data, 1, 3, APP, RESEARCH, name="ambiguous", profile=Profile(0, 1, 0, None))
        self.assertEqual(next(trade for trade in result.trades if trade["side"] == "sell")["reason"], "intrabar_stop")

    def test_no_entry_immediately_across_missing_hour(self):
        data = prepared(candles(4).drop(candles(4).index[1]))
        data.frame.loc[data.frame.index[0], "swing_signal"] = True
        data = PreparedData.from_frame(data.frame)
        result = simulate(data, 1, 3, APP, RESEARCH, name="missing", profile=Profile(0, 1, 0, None))
        self.assertEqual(len(result.trades), 0)

    def test_cash_target_does_not_force_existing_dca_sale(self):
        data = prepared(candles(60))
        settings = copy.deepcopy(RESEARCH)
        settings["allocation_update_days"] = 1
        def policy(index):
            return (Profile(1, 0, 0, None) if index < 25 else Profile(0, 0, 1, None)), {}
        result = simulate(data, 1, 60, APP, settings, name="targets", policy=policy, guard_enabled=False)
        row = result.curve.loc[data.frame.index[25] + pd.Timedelta(hours=1)]
        self.assertEqual(row["target_dca"], 0)
        self.assertGreater(row["dca_btc"], 0)
        self.assertTrue(all(trade["reason"] == "experiment_end" for trade in result.trades if trade["side"] == "sell"))

    def test_seven_day_dca_cap_includes_fees(self):
        app = copy.deepcopy(APP)
        app["dca"]["tranche_fraction_of_dca_budget"] = 0.1
        result = simulate(prepared(candles(150)), 1, 150, app, RESEARCH, name="cap", profile=Profile(1, 0, 0, None), guard_enabled=False)
        spent = sum(trade["btc_units"] * trade["fill_price"] + trade["fee_usd"] for trade in result.trades if trade["side"] == "buy")
        self.assertAlmostEqual(spent, 1400.0, places=6)
        self.assertTrue((result.curve["cash_usd"] >= 0).all())

    def test_defensive_dca_exit_sells_existing_holdings_and_blocks_dca(self):
        data = prepared(candles(60))
        data.frame.loc[data.frame.index[40]:, "confirmed_bear"] = True
        data = PreparedData.from_frame(data.frame)
        result = simulate(data, 1, 60, APP, RESEARCH, name="defense", profile=Profile(1, 0, 0, None, defensive_dca_exit=True))
        exits = [trade for trade in result.trades if trade["reason"] == "confirmed_bear_exit"]
        self.assertEqual(len(exits), 1)
        self.assertEqual(result.curve.iloc[-2]["dca_btc"], 0)


class SelectionTests(unittest.TestCase):
    def episodes(self):
        rows = []
        for start in pd.date_range("2020-01-01", periods=4, freq="90D", tz="UTC"):
            for profile, net_return, drawdown in (("A", 0.1, 0.05), ("B", 0.0, 0.0)):
                rows.append({"start_utc": start, "end_utc": start + pd.Timedelta(days=90), "start_regime": "bull",
                             "profile_id": profile, "net_return": net_return, "log_return": np.log1p(net_return),
                             "max_drawdown": drawdown, "fees_usd": 10.0})
        return pd.DataFrame(rows)

    def test_future_outcomes_cannot_influence_selection(self):
        episodes = self.episodes()
        future = episodes.iloc[[0]].copy()
        future["profile_id"] = "B"
        future["start_utc"] = pd.Timestamp("2021-01-01", tz="UTC")
        future["end_utc"] = pd.Timestamp("2021-04-01", tz="UTC")
        future["net_return"], future["log_return"] = 1000.0, np.log1p(1000.0)
        combined = pd.concat([episodes, future], ignore_index=True)
        cutoff = pd.Timestamp("2020-12-26", tz="UTC")
        self.assertEqual(rank_profiles(combined, cutoff, RESEARCH).iloc[0]["profile_id"], "A")
        self.assertEqual(fit_selector(combined, cutoff, RESEARCH)["global_training_episodes"], 4)

    def test_sparse_regimes_fall_back_to_global(self):
        model = fit_selector(self.episodes(), pd.Timestamp("2020-12-26", tz="UTC"), RESEARCH)
        self.assertIn("bull", model["regimes"])
        self.assertNotIn("bear", model["regimes"])

    def test_candidate_grid_conserves_capital_and_deduplicates_unused_parameters(self):
        profiles = candidate_profiles(RESEARCH)
        identifiers = [profile.identifier for profile in profiles]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertTrue(all(abs(profile.dca + profile.swing + profile.reserve - 1) < 1e-9 for profile in profiles))
        self.assertEqual(len([profile for profile in profiles if profile.reserve == 1]), 1)


if __name__ == "__main__":
    unittest.main()
