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
        dates = pd.date_range("2024-01-01", periods=len(qqq_values))
        if qld_values is None:
            qld_values = qqq_values
        if tqqq_values is None:
            tqqq_values = qqq_values
        close = pd.DataFrame(
            {"QQQ": qqq_values, "QLD": qld_values, "TQQQ": tqqq_values},
            index=dates,
        )
        return close

    # ── 基础行为 ──────────────────────────────────────────────

    def test_initial_day_is_50_50(self):
        """第 0 天初始权重为 50% QQQ + 50% QLD。"""
        close = self._make_data([100.0, 95.0, 90.0])
        weights = drawdown_rotation_strategy.build_drawdown_weights(close)
        self.assertAlmostEqual(weights.iloc[0]["QQQ"], 0.5)
        self.assertAlmostEqual(weights.iloc[0]["QLD"], 0.5)
        self.assertAlmostEqual(weights.iloc[0]["TQQQ"], 0.0)

    def test_first_rebalance_uses_only_past_drawdown(self):
        """第 1 天权重仅由第 0 天收盘决定（无前视偏差）。"""
        close_a = self._make_data([100.0, 85.0])
        close_b = self._make_data([100.0, 100.0])
        weights_a = drawdown_rotation_strategy.build_drawdown_weights(close_a)
        weights_b = drawdown_rotation_strategy.build_drawdown_weights(close_b)
        # 第 0 天收盘后回撤均为 0，第 1 天权重同为 50/50
        self.assertAlmostEqual(weights_a.iloc[1]["QQQ"], 0.5)
        self.assertAlmostEqual(weights_a.iloc[1]["QLD"], 0.5)
        self.assertAlmostEqual(weights_b.iloc[1]["QQQ"], 0.5)
        self.assertAlmostEqual(weights_b.iloc[1]["QLD"], 0.5)

    def test_drawdown_threshold_mapping(self):
        """持续下跌时验证各回撤档位权重映射。"""
        # 逐日下跌触发不同档位（三标的同向变动，portfolio_value 跟踪 QQQ 价格）
        qqq_values = [100.0, 90.0, 80.0, 70.0, 60.0, 50.0]
        close = self._make_data(qqq_values)
        weights = drawdown_rotation_strategy.build_drawdown_weights(close)

        # 第 0 天：50% QQQ + 50% QLD
        self.assertAlmostEqual(weights.iloc[0]["QQQ"], 0.5)
        self.assertAlmostEqual(weights.iloc[0]["QLD"], 0.5)

        # 第 1 天收盘后 dd=10%（tier 1），第 2 天权重：QQQ 20% + QLD 80%
        self.assertAlmostEqual(weights.iloc[2]["QQQ"], 0.2)
        self.assertAlmostEqual(weights.iloc[2]["QLD"], 0.8)

        # 第 2 天收盘后 dd=20%（tier 3），第 3 天权重：QLD 60% + TQQQ 40%
        self.assertAlmostEqual(weights.iloc[3]["QLD"], 0.6)
        self.assertAlmostEqual(weights.iloc[3]["TQQQ"], 0.4)

        # 第 3 天收盘后 dd=30%（tier 4），第 4 天权重：QLD 20% + TQQQ 80%
        self.assertAlmostEqual(weights.iloc[4]["QLD"], 0.2)
        self.assertAlmostEqual(weights.iloc[4]["TQQQ"], 0.8)

        # 第 4 天收盘后 dd=40%（tier 5），第 5 天起满仓 TQQQ
        self.assertAlmostEqual(weights.iloc[5]["TQQQ"], 1.0)

    def test_deep_drawdown_ends_all_tqqq(self):
        """足够深的回撤（≥36%）后全部转为 TQQQ。"""
        qqq_values = [100.0, 90.0, 80.0, 70.0, 55.0, 40.0]
        close = self._make_data(qqq_values)
        weights = drawdown_rotation_strategy.build_drawdown_weights(close)
        final = weights.iloc[-1]
        self.assertAlmostEqual(final["TQQQ"], 1.0)

    def test_weights_row_sums(self):
        """每一天权重和为 0 或 1。"""
        close = self._make_data([100.0, 95.0, 85.0, 75.0, 65.0, 55.0])
        weights = drawdown_rotation_strategy.build_drawdown_weights(close)
        row_sums = weights.sum(axis=1)
        self.assertTrue(((row_sums == 0.0) | (row_sums == 1.0)).all())

    # ── 降杠杆 ────────────────────────────────────────────────

    def test_one_way_ratchet_when_ratio_zero(self):
        """deleverage_ratio=0 时退化为只加不减。"""
        qqq_values = [100.0, 90.0, 90.0, 95.0, 95.0]
        close = self._make_data(qqq_values)
        weights = drawdown_rotation_strategy.build_drawdown_weights(
            close, deleverage_ratio=0.0
        )
        # 第 1 天收盘 dd=10%，第 2 天起 QQQ 20%+QLD 80% 且不再回落
        self.assertAlmostEqual(weights.iloc[2]["QQQ"], 0.2)
        self.assertAlmostEqual(weights.iloc[2]["QLD"], 0.8)
        self.assertAlmostEqual(weights.iloc[4]["QQQ"], 0.2)
        self.assertAlmostEqual(weights.iloc[4]["QLD"], 0.8)

    def test_deleverage_on_recovery(self):
        """跌到 tier 3 后反弹修复，逐日降级回到 50/50。"""
        # Day 0→1: 跌 22% → tier 3 (QLD 60% + TQQQ 40%)
        # Day 2→: 反弹到 95（5% 回撤），触发逐日降级
        qqq_values = [100.0, 78.0, 95.0, 95.0, 95.0, 95.0]
        close = self._make_data(qqq_values)
        weights = drawdown_rotation_strategy.build_drawdown_weights(
            close, deleverage_ratio=0.5
        )

        # Day 0: QQQ 50% + QLD 50%
        self.assertAlmostEqual(weights.iloc[0]["QQQ"], 0.5)
        self.assertAlmostEqual(weights.iloc[0]["QLD"], 0.5)

        # Day 2 (基于 Day 1 收盘 tier 3): QLD 60% + TQQQ 40%
        self.assertAlmostEqual(weights.iloc[2]["QLD"], 0.6)
        self.assertAlmostEqual(weights.iloc[2]["TQQQ"], 0.4)

        # Day 2 收盘 dd=5%，降级：tier 3→2: 5%<10% YES
        # Day 3: QLD 100%
        self.assertAlmostEqual(weights.iloc[3]["QLD"], 1.0)

        # Day 3 收盘 dd=5%，降级：tier 2→1: 5%<7% YES
        # Day 4: QQQ 20% + QLD 80%
        self.assertAlmostEqual(weights.iloc[4]["QQQ"], 0.2)
        self.assertAlmostEqual(weights.iloc[4]["QLD"], 0.8)

        # Day 4 收盘 dd=5%，降级：tier 1→0: 5%<4% NO → stays tier 1
        self.assertAlmostEqual(weights.iloc[5]["QQQ"], 0.2)
        self.assertAlmostEqual(weights.iloc[5]["QLD"], 0.8)

    def test_no_deleverage_when_ratio_zero(self):
        """deleverage_ratio=0 时降杠杆完全关闭。"""
        qqq_values = [100.0, 78.0, 95.0, 95.0, 95.0]
        close = self._make_data(qqq_values)
        weights = drawdown_rotation_strategy.build_drawdown_weights(
            close, deleverage_ratio=0.0
        )
        # 反弹后保持 tier 3 不降 (QLD 60% + TQQQ 40%)
        self.assertAlmostEqual(weights.iloc[4]["QLD"], 0.6)
        self.assertAlmostEqual(weights.iloc[4]["TQQQ"], 0.4)

    def test_re_upgrade_after_new_drawdown(self):
        """完全修复后，新回撤能重新触发加杠杆。"""
        # 跌 22% → 修复到 5% → 降到 tier 1 → 又跌 15%
        qqq_values = [
            100.0,  # Day 0
            78.0,   # Day 1: dd=22% → tier 3
            95.0,   # Day 2: 修复 dd=5%，开始逐日降级
            95.0,   # Day 3: 继续降 → tier 1 (QQQ 20%+QLD 80%)
            85.0,   # Day 4: 新回撤 dd=15% → tier 2
            85.0,   # Day 5: 权重反映 Day 4 的 upgrade
        ]
        close = self._make_data(qqq_values)
        weights = drawdown_rotation_strategy.build_drawdown_weights(
            close, deleverage_ratio=0.5
        )

        # Day 0: QQQ 50% + QLD 50%
        self.assertAlmostEqual(weights.iloc[0]["QQQ"], 0.5)
        self.assertAlmostEqual(weights.iloc[0]["QLD"], 0.5)
        # Day 2 (基于 Day 1 收盘 tier 3): QLD 60% + TQQQ 40%
        self.assertAlmostEqual(weights.iloc[2]["QLD"], 0.6)
        self.assertAlmostEqual(weights.iloc[2]["TQQQ"], 0.4)
        # Day 4: 已降到 tier 1 (QQQ 20% + QLD 80%)
        self.assertAlmostEqual(weights.iloc[4]["QQQ"], 0.2)
        self.assertAlmostEqual(weights.iloc[4]["QLD"], 0.8)
        # Day 5: Day 4 收盘 dd=15% 触发 upgrade 到 tier 2 (QLD 100%)
        self.assertAlmostEqual(weights.iloc[5]["QLD"], 1.0)


if __name__ == "__main__":
    unittest.main()
