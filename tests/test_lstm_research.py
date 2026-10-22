"""Chronology, gap handling and comparable-account invariants for the LSTM study."""
import unittest
import numpy as np
import pandas as pd
from research_lstm.experiment import daily_features, time_split, policy_metrics, paired_bootstrap


class LstmResearchTests(unittest.TestCase):
    def candles(self):
        index = pd.date_range("2020-01-01", periods=24 * 260, freq="1h", tz="UTC")
        price = np.linspace(100, 200, len(index))
        return pd.DataFrame({"open": price, "high": price + 1, "low": price - 1, "close": price, "volume": 10.}, index=index)

    def test_future_prices_cannot_change_past_features(self):
        candles = self.candles()
        before = daily_features(candles)
        cutoff = pd.Timestamp("2020-08-20", tz="UTC")
        candles.loc[candles.index >= cutoff, ["open", "high", "low", "close"]] *= 10
        after = daily_features(candles)
        pd.testing.assert_frame_equal(before.loc[:cutoff], after.loc[:cutoff])

    def test_daily_features_available_only_after_day_closes(self):
        daily = daily_features(self.candles())
        self.assertEqual(daily.index[0], pd.Timestamp("2020-01-02", tz="UTC"))

    def test_missing_hour_does_not_become_complete_daily_input(self):
        candles = self.candles().drop(pd.Timestamp("2020-08-20T12:00:00Z"))
        daily = daily_features(candles)
        self.assertTrue(daily.loc["2020-08-21", "range_fraction"] != daily.loc["2020-08-21", "range_fraction"])

    def test_boundary_crossing_labels_are_excluded(self):
        rows = pd.DataFrame({"start": ["2023-12-20Z", "2023-12-29Z", "2024-02-01Z", "2024-12-29Z", "2025-01-06Z"],
                             "end": ["2023-12-27Z", "2024-01-05Z", "2024-02-08Z", "2025-01-05Z", "2025-01-13Z"]})
        for col in rows:
            rows[col] = rows[col].str.replace("Z", "T00:00:00Z")
        split = time_split(rows, {"train_end_exclusive": "2024-01-01Z".replace("Z", "T00:00:00Z"),
                                 "validation_end_exclusive": "2025-01-01T00:00:00Z"})
        self.assertEqual(split["train"].tolist(), [0])
        self.assertEqual(split["validation"].tolist(), [2])
        self.assertEqual(split["test"].tolist(), [4])

    def test_flat_to_flat_returns_compound_and_include_initial_drawdown(self):
        rows = pd.DataFrame({"reserve_return": [-.1, .1], "reserve_fees_fraction": [0., 0.],
                             "reserve_fills": [0, 0], "reserve_exposure": [0., 0.]})
        curves = np.array([[[.9]], [[1.1]]])
        result, equity, weekly = policy_metrics(rows, curves, np.array([0, 0]))
        self.assertAlmostEqual(result["final_equity_usd"], 9900)
        self.assertAlmostEqual(result["max_drawdown"], .1)
        self.assertAlmostEqual(np.exp(weekly.sum()), .99)

    def test_reserve_wins_exact_predicted_utility_tie(self):
        self.assertEqual(np.array([[0., 0., 0.]]).argmax(axis=1).tolist(), [0])

    def test_paired_bootstrap_preserves_zero_difference(self):
        result = paired_bootstrap(np.zeros(30), {"bootstrap_block_weeks": 4, "bootstrap_repetitions": 100})
        self.assertEqual(result["ci95"], [0., 0.])


if __name__ == "__main__":
    unittest.main()
