#!/usr/bin/env python3
"""optimize.py 的单元测试。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import optimize


class TestScanThresholds(unittest.TestCase):
    def _make_data(self):
        dates = pd.date_range("2024-01-01", periods=3)
        close = pd.DataFrame({"QQQ": [100.0, 101.0, 102.0]}, index=dates)
        vix = pd.Series([20.0, 25.0, 30.0], index=dates)
        return close, vix

    def test_filters_invalid_order(self):
        close, vix = self._make_data()
        with patch("optimize.run_backtest") as mock_run:
            mock_run.return_value = (MagicMock(), close, vix, None)
            with patch("optimize._portfolio_value_metrics") as mock_metrics:
                mock_metrics.return_value = {
                    "total_return": 0.0,
                    "annual_return": 0.0,
                    "sharpe": 0.0,
                    "max_drawdown": 0.0,
                    "calmar": 0.0,
                }
                df = optimize.scan_thresholds(
                    symbols=["QQQ"],
                    start="2024-01-01",
                    end=None,
                    low_values=[20.0, 25.0],
                    mid1_values=[22.0, 27.0],
                    mid2_values=[25.0, 30.0],
                    high_values=[35.0, 40.0],
                    initial_cash=10000,
                    fees=0.0,
                    slippage=0.0,
                    close=close,
                    vix=vix,
                )
        # 只应保留 low < mid1 < mid2 < high 的组合
        for _, row in df.iterrows():
            self.assertLess(row["low"], row["mid1"])
            self.assertLess(row["mid1"], row["mid2"])
            self.assertLess(row["mid2"], row["high"])

    def test_reuses_prefetched_data(self):
        close, vix = self._make_data()
        with patch("optimize.run_backtest") as mock_run:
            mock_portfolio = MagicMock()
            mock_portfolio.trades.count.return_value.sum.return_value = 0
            mock_run.return_value = (mock_portfolio, close, vix, None)
            with patch("optimize._portfolio_value_metrics") as mock_metrics:
                mock_metrics.return_value = {
                    "total_return": 0.0,
                    "annual_return": 0.0,
                    "sharpe": 0.0,
                    "max_drawdown": 0.0,
                    "calmar": 0.0,
                }
                df = optimize.scan_thresholds(
                    symbols=["QQQ"],
                    start="2024-01-01",
                    end=None,
                    low_values=[10.0],
                    mid1_values=[15.0],
                    mid2_values=[20.0],
                    high_values=[30.0],
                    initial_cash=10000,
                    fees=0.0,
                    slippage=0.0,
                    close=close,
                    vix=vix,
                )
        self.assertEqual(len(df), 1)
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        # 确认预拉取的数据被传入
        self.assertTrue(kwargs["close"] is close)
        self.assertTrue(kwargs["vix"] is vix)

    def test_default_scan_ranges_are_numeric(self):
        self.assertTrue(all(isinstance(v, float) for v in optimize.DEFAULT_LOW_VALUES))
        self.assertTrue(all(isinstance(v, float) for v in optimize.DEFAULT_MID1_VALUES))
        self.assertTrue(all(isinstance(v, float) for v in optimize.DEFAULT_MID2_VALUES))
        self.assertTrue(all(isinstance(v, float) for v in optimize.DEFAULT_HIGH_VALUES))

    def test_passes_four_thresholds_to_run_backtest(self):
        close, vix = self._make_data()
        with patch("optimize.run_backtest") as mock_run:
            mock_portfolio = MagicMock()
            mock_portfolio.trades.count.return_value.sum.return_value = 0
            mock_run.return_value = (mock_portfolio, close, vix, None)
            with patch("optimize._portfolio_value_metrics") as mock_metrics:
                mock_metrics.return_value = {
                    "total_return": 0.0,
                    "annual_return": 0.0,
                    "sharpe": 0.0,
                    "max_drawdown": 0.0,
                    "calmar": 0.0,
                }
                optimize.scan_thresholds(
                    symbols=["QQQ"],
                    start="2024-01-01",
                    end=None,
                    low_values=[10.0],
                    mid1_values=[15.0],
                    mid2_values=[20.0],
                    high_values=[30.0],
                    initial_cash=10000,
                    fees=0.0,
                    slippage=0.0,
                    close=close,
                    vix=vix,
                )
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["thresholds"], (10.0, 15.0, 20.0, 30.0))


if __name__ == "__main__":
    unittest.main()
