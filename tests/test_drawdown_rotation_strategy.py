#!/usr/bin/env python3
"""drawdown_rotation_strategy.py 的单元测试。"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import drawdown_rotation_strategy


class TestBuildDrawdownWeights(unittest.TestCase):
    def _make_data(self, qqq_values, qld_values=None, tqqq_values=None):
        """构造收盘价 DataFrame；未指定 QLD/TQQQ 时与 QQQ 同向变动。"""
        dates = pd.date_range("2024-01-01", periods=len(qqq_values))
        if qld_values is None:
            qld_values = qqq_values
        if tqqq_values is None:
            tqqq_values = qqq_values
        close = pd.DataFrame(
            {
                "QQQ": qqq_values,
                "QLD": qld_values,
                "TQQQ": tqqq_values,
            },
            index=dates,
        )
        return close

    def test_initial_day_is_full_qqq(self):
        close = self._make_data([100.0, 95.0, 90.0])
        weights = drawdown_rotation_strategy.build_drawdown_weights(close)
        self.assertAlmostEqual(weights.iloc[0]["QQQ"], 1.0)
        self.assertAlmostEqual(weights.iloc[0][["QLD", "TQQQ"]].sum(), 0.0)

    def test_first_rebalance_uses_only_past_drawdown(self):
        """验证权重不依赖当日未来数据：第 1 天权重仅由第 0 天收盘决定。"""
        close_a = self._make_data([100.0, 85.0])  # 第 1 天大跌
        close_b = self._make_data([100.0, 100.0])  # 第 1 天持平
        weights_a = drawdown_rotation_strategy.build_drawdown_weights(close_a)
        weights_b = drawdown_rotation_strategy.build_drawdown_weights(close_b)
        # 第 0 天收盘后最大回撤均为 0，第 1 天权重应同为 100% QQQ
        self.assertAlmostEqual(weights_a.iloc[1]["QQQ"], 1.0)
        self.assertAlmostEqual(weights_b.iloc[1]["QQQ"], 1.0)

    def test_drawdown_threshold_mapping(self):
        """使用 QQQ/QLD/TQQQ 同幅度下跌序列，验证各回撤档位权重。

        当三只标的同涨同跌时，组合净值跟踪 QQQ 价格；第 t 天权重基于
        1 - QQQ[t-1]/QQQ[0] 所落入的回撤档位。
        """
        # 价格序列：第 0 天 100，之后每天下跌，用于触发不同档位
        qqq_values = [100.0, 88.0, 78.0, 68.0, 58.0, 48.0, 38.0, 28.0, 18.0, 10.0]
        close = self._make_data(qqq_values)
        weights = drawdown_rotation_strategy.build_drawdown_weights(close)

        # 第 0 天：100% QQQ
        self.assertAlmostEqual(weights.iloc[0]["QQQ"], 1.0)

        # 第 1 天收盘后最大回撤 12%，第 2 天权重应为 80% QQQ + 20% QLD
        self.assertAlmostEqual(weights.iloc[2]["QQQ"], 0.8)
        self.assertAlmostEqual(weights.iloc[2]["QLD"], 0.2)

        # 第 2 天收盘后最大回撤 22%，第 3 天权重应为 100% QLD
        self.assertAlmostEqual(weights.iloc[3]["QLD"], 1.0)

        # 第 3 天收盘后最大回撤 32%，第 4 天权重应为 60% QLD + 40% TQQQ
        self.assertAlmostEqual(weights.iloc[4]["QLD"], 0.6)
        self.assertAlmostEqual(weights.iloc[4]["TQQQ"], 0.4)

        # 第 4 天收盘后最大回撤 42%，第 5 天权重应为 20% QLD + 80% TQQQ
        self.assertAlmostEqual(weights.iloc[5]["QLD"], 0.2)
        self.assertAlmostEqual(weights.iloc[5]["TQQQ"], 0.8)

        # 第 5 天收盘后最大回撤 52%，第 6 天起应满仓 TQQQ
        self.assertAlmostEqual(weights.iloc[6]["TQQQ"], 1.0)
        self.assertAlmostEqual(weights.iloc[9]["TQQQ"], 1.0)

    def test_one_way_ratchet_no_reduction_on_recovery(self):
        """验证只加不减：反弹未创新高时保持已达成的最高杠杆档位。"""
        # 跌 12% 后反弹，但未回到前高
        qqq_values = [100.0, 88.0, 88.0, 92.0, 92.0]
        close = self._make_data(qqq_values)
        weights = drawdown_rotation_strategy.build_drawdown_weights(close)

        # 第 1 天收盘后最大回撤 12%，第 2 天起应保持在 80/20 档位
        self.assertAlmostEqual(weights.iloc[2]["QQQ"], 0.8)
        self.assertAlmostEqual(weights.iloc[2]["QLD"], 0.2)
        # 反弹到 92（仍低于 100）后不应回到 100% QQQ
        self.assertAlmostEqual(weights.iloc[4]["QQQ"], 0.8)
        self.assertAlmostEqual(weights.iloc[4]["QLD"], 0.2)

    def test_deep_drawdown_ends_all_tqqq(self):
        """验证足够深的回撤后全部转为 TQQQ。"""
        qqq_values = [100.0, 95.0, 80.0, 70.0, 55.0, 40.0]
        close = self._make_data(qqq_values)
        weights = drawdown_rotation_strategy.build_drawdown_weights(close)

        # 第 5 天收盘后最大回撤至少 45%，最终应满仓 TQQQ
        final = weights.iloc[-1]
        self.assertAlmostEqual(final["TQQQ"], 1.0)
        self.assertAlmostEqual(final[["QQQ", "QLD"]].sum(), 0.0)

    def test_weights_row_sums(self):
        """验证每一天权重和为 0 或 1。"""
        close = self._make_data([100.0, 95.0, 85.0, 75.0, 65.0, 55.0])
        weights = drawdown_rotation_strategy.build_drawdown_weights(close)
        row_sums = weights.sum(axis=1)
        self.assertTrue(((row_sums == 0.0) | (row_sums == 1.0)).all())


if __name__ == "__main__":
    unittest.main()
