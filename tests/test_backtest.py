#!/usr/bin/env python3
"""backtest.py 的单元测试。"""

import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import backtest


class TestBuildSignals(unittest.TestCase):
    def _make_data(self, n=5):
        dates = pd.date_range("2024-01-01", periods=n)
        close = pd.DataFrame(
            {
                "QQQ": [100.0] * n,
                "QLD": [50.0] * n,
                "TQQQ": [25.0] * n,
            },
            index=dates,
        )
        return close, dates

    def test_threshold_mapping(self):
        close, dates = self._make_data()
        # VIX 序列: 12(<13) -> TQQQ, 15(13-20) -> QLD, 25(20-30) -> QQQ,
        # 35(>=30) -> cash, 20(20-30) -> QQQ
        vix = pd.Series([12.0, 15.0, 25.0, 35.0, 20.0], index=dates)
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0))

        self.assertAlmostEqual(weights.loc[dates[0], "TQQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[1], "QLD"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[2], "QQQ"], 1.0)
        self.assertTrue((weights.loc[dates[3]] == 0.0).all())
        self.assertAlmostEqual(weights.loc[dates[4], "QQQ"], 1.0)

    def test_weights_sum_to_one_or_zero(self):
        close, dates = self._make_data()
        vix = pd.Series([12.0, 15.0, 25.0, 35.0, 20.0], index=dates)
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0))
        row_sums = weights.sum(axis=1)
        self.assertTrue(((row_sums - 1.0).abs() < 1e-12).all() or (row_sums == 0.0).any())
        # 更精确：每一天权重和应为 1 或 0
        self.assertTrue(((row_sums == 1.0) | (row_sums == 0.0)).all())

    def test_missing_vix_forward_filled(self):
        close, dates = self._make_data()
        vix = pd.Series([12.0, np.nan, 25.0, np.nan, 20.0], index=dates)
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0))
        # 缺失 VIX 应被前向填充：12 -> TQQQ, 12(ffill) -> TQQQ, 25 -> QQQ, 25(ffill) -> QQQ, 20 -> QQQ
        self.assertAlmostEqual(weights.loc[dates[0], "TQQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[1], "TQQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[2], "QQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[3], "QQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[4], "QQQ"], 1.0)

    def test_pre_listing_nan_keeps_cash(self):
        close, dates = self._make_data(n=2)
        close.loc[dates[0], "TQQQ"] = np.nan
        vix = pd.Series([12.0, 12.0], index=dates)
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0))
        # 第一行 TQQQ 未上市，即使 VIX<13 也应空仓
        self.assertTrue((weights.loc[dates[0]] == 0.0).all())
        self.assertAlmostEqual(weights.loc[dates[1], "TQQQ"], 1.0)

    def test_unknown_symbol_ignored(self):
        close, dates = self._make_data()
        close = close.drop(columns=["TQQQ", "QLD"])
        vix = pd.Series([12.0, 15.0, 25.0, 35.0, 20.0], index=dates)
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0))
        # 只有 QQQ 列，所有非 QQQ 区间都应为空仓
        self.assertAlmostEqual(weights.loc[dates[2], "QQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[4], "QQQ"], 1.0)
        self.assertTrue((weights.loc[dates[0]] == 0.0).all())
        self.assertTrue((weights.loc[dates[1]] == 0.0).all())
        self.assertTrue((weights.loc[dates[3]] == 0.0).all())


class TestPortfolioValueMetrics(unittest.TestCase):
    def test_flat_value_returns_zero_metrics(self):
        class FlatPortfolio:
            def value(self):
                return pd.Series([10000.0, 10000.0, 10000.0], index=pd.date_range("2024-01-01", periods=3))

        metrics = backtest._portfolio_value_metrics(FlatPortfolio())
        self.assertAlmostEqual(metrics["total_return"], 0.0)
        self.assertAlmostEqual(metrics["annual_return"], 0.0)
        self.assertAlmostEqual(metrics["sharpe"], 0.0)
        self.assertAlmostEqual(metrics["max_drawdown"], 0.0)
        self.assertAlmostEqual(metrics["calmar"], 0.0)

    def test_positive_return(self):
        class UpPortfolio:
            def value(self):
                return pd.Series([100.0, 110.0, 121.0], index=pd.date_range("2024-01-01", periods=3))

        metrics = backtest._portfolio_value_metrics(UpPortfolio())
        self.assertGreater(metrics["total_return"], 0.0)
        self.assertGreater(metrics["annual_return"], 0.0)
        self.assertAlmostEqual(metrics["max_drawdown"], 0.0)

    def test_positive_return_with_volatility(self):
        class WigglyPortfolio:
            def value(self):
                return pd.Series([100.0, 110.0, 105.0, 115.0], index=pd.date_range("2024-01-01", periods=4))

        metrics = backtest._portfolio_value_metrics(WigglyPortfolio())
        self.assertGreater(metrics["total_return"], 0.0)
        self.assertGreater(metrics["sharpe"], 0.0)

    def test_dataframe_value_summed(self):
        class MultiPortfolio:
            def value(self):
                index = pd.date_range("2024-01-01", periods=3)
                return pd.DataFrame({"A": [100.0, 110.0, 105.0], "B": [50.0, 55.0, 60.0]}, index=index)

        metrics = backtest._portfolio_value_metrics(MultiPortfolio())
        self.assertAlmostEqual(metrics["total_return"], 165.0 / 150.0 - 1.0, places=10)


class TestParseArgs(unittest.TestCase):
    def _run_parse(self, argv):
        with patch.object(sys, "argv", argv):
            return backtest.parse_args()

    def test_default_thresholds(self):
        args = self._run_parse(["backtest.py"])
        self.assertEqual(args.thresholds, (13.0, 20.0, 30.0))

    def test_custom_thresholds(self):
        args = self._run_parse(["backtest.py", "--thresholds", "15", "25", "35"])
        self.assertEqual(args.thresholds, (15.0, 25.0, 35.0))

    def test_unordered_thresholds_exit(self):
        stderr = StringIO()
        with patch.object(sys, "argv", ["backtest.py", "--thresholds", "30", "20", "13"]):
            with patch.object(sys, "stderr", stderr):
                with self.assertRaises(SystemExit):
                    backtest.parse_args()


if __name__ == "__main__":
    unittest.main()
