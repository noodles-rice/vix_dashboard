#!/usr/bin/env python3
"""run_ma_strategy.py 的单元测试。"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import run_ma_strategy


class TestBuildMAVIXWeights(unittest.TestCase):
    def _make_data(self, periods: int = 10):
        dates = pd.date_range("2024-01-01", periods=periods)
        close = pd.DataFrame(
            {
                "QQQ": [100.0 + i for i in range(periods)],
                "QLD": [50.0] * periods,
                "TQQQ": [25.0] * periods,
            },
            index=dates,
        )
        vix = pd.Series([25.0] * periods, index=dates)
        return close, vix

    def test_above_ma_and_low_vix_goes_full_tqqq(self):
        close, vix = self._make_data()
        weights = run_ma_strategy.build_ma_vix_weights(
            close, vix, ma_window=5, vix_thr=30.0
        )
        last = weights.iloc[-1]
        self.assertAlmostEqual(last["TQQQ"], 1.0)
        self.assertAlmostEqual(last.drop("TQQQ").sum(), 0.0)

    def test_below_ma_or_high_vix_goes_cash(self):
        close, vix = self._make_data()
        close.loc[close.index[-1], "QQQ"] = 90.0
        vix.loc[vix.index[-1]] = 35.0
        weights = run_ma_strategy.build_ma_vix_weights(
            close, vix, ma_window=5, vix_thr=30.0
        )
        self.assertTrue((weights.iloc[-1] == 0.0).all())

    def test_weights_shape_and_row_sums(self):
        close, vix = self._make_data()
        weights = run_ma_strategy.build_ma_vix_weights(
            close, vix, ma_window=5, vix_thr=30.0
        )
        self.assertEqual(weights.shape, close.shape)
        row_sums = weights.sum(axis=1)
        # 每一行要么满仓 TQQQ，要么空仓
        self.assertTrue(((row_sums == 0.0) | (row_sums == 1.0)).all())

    def test_earliest_window_is_cash(self):
        """MA 窗口前的数据因无法计算均线而保持空仓。"""
        close, vix = self._make_data(periods=10)
        weights = run_ma_strategy.build_ma_vix_weights(
            close, vix, ma_window=5, vix_thr=30.0
        )
        # 前 4 天没有完整 MA5，signal 为 False
        self.assertTrue((weights.iloc[:4] == 0.0).all().all())


if __name__ == "__main__":
    unittest.main()
