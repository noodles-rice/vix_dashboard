#!/usr/bin/env python3
"""backtest.py 的单元测试。"""

import json
import sys
import tempfile
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
        # VIX 序列: 12(<13) -> 半仓 QQQ, 15(13-20] -> 满仓 QQQ,
        # 25(20-30] -> 半仓 QLD+半仓 QQQ, 35(30-40] -> 满仓 QLD,
        # 20(13-20] -> 满仓 QQQ
        vix = pd.Series([12.0, 15.0, 25.0, 35.0, 20.0], index=dates)
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0, 40.0))

        self.assertAlmostEqual(weights.loc[dates[0], "QQQ"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[1], "QQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[2], "QLD"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[2], "QQQ"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[3], "QLD"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[4], "QQQ"], 1.0)

    def test_weights_sum_to_half_or_one(self):
        close, dates = self._make_data()
        vix = pd.Series([12.0, 15.0, 25.0, 35.0, 45.0], index=dates)
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0, 40.0))
        row_sums = weights.sum(axis=1)
        # 每一天权重和应为 0.5 或 1.0
        self.assertTrue(((row_sums == 0.5) | (row_sums == 1.0)).all())

    def test_missing_vix_forward_filled(self):
        close, dates = self._make_data()
        vix = pd.Series([12.0, np.nan, 25.0, np.nan, 25.0], index=dates)
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0, 40.0))
        # 缺失 VIX 前向填充：12 -> 半仓 QQQ, 12 -> 半仓 QQQ,
        # 25 -> 半仓 QLD+半仓 QQQ, 25 -> 半仓 QLD+半仓 QQQ, 25 -> 半仓 QLD+半仓 QQQ
        self.assertAlmostEqual(weights.loc[dates[0], "QQQ"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[1], "QQQ"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[2], "QLD"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[2], "QQQ"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[3], "QLD"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[3], "QQQ"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[4], "QLD"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[4], "QQQ"], 0.5)

    def test_pre_listing_qqq_nan_keeps_cash(self):
        close, dates = self._make_data(n=2)
        close.loc[dates[0], "QQQ"] = np.nan
        vix = pd.Series([12.0, 12.0], index=dates)
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0, 40.0))
        # VIX<13 目标 QQQ 缺失，无回退标的，应保持空仓
        self.assertTrue((weights.loc[dates[0]] == 0.0).all())
        self.assertAlmostEqual(weights.loc[dates[1], "QQQ"], 0.5)

    def test_high_vix_tqqq_nan_falls_back_to_qld(self):
        close, dates = self._make_data(n=2)
        close.loc[dates[0], "TQQQ"] = np.nan
        vix = pd.Series([45.0, 45.0], index=dates)
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0, 40.0))
        # VIX>40 目标半仓 TQQQ 缺失，应回退到 QLD，即 50% QLD + 50% QLD = 100% QLD
        self.assertAlmostEqual(weights.loc[dates[0], "QLD"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[1], "QLD"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[1], "TQQQ"], 0.5)

    def test_high_vix_tqqq_and_qld_nan_fall_back_to_qqq(self):
        close, dates = self._make_data(n=2)
        close.loc[dates[0], ["TQQQ", "QLD"]] = np.nan
        vix = pd.Series([45.0, 45.0], index=dates)
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0, 40.0))
        # VIX>40 半仓 QLD、半仓 TQQQ 均缺失，均回退到 QQQ，即满仓 QQQ
        self.assertAlmostEqual(weights.loc[dates[0], "QQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[1], "QLD"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[1], "TQQQ"], 0.5)

    def test_mid_vix_qld_nan_falls_back_to_qqq(self):
        close, dates = self._make_data(n=2)
        close.loc[dates[0], "QLD"] = np.nan
        vix = pd.Series([25.0, 25.0], index=dates)
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0, 40.0))
        # VIX 在 (20,30] 半仓 QLD 缺失 -> 半仓 QQQ，加上原半仓 QQQ = 满仓 QQQ
        self.assertAlmostEqual(weights.loc[dates[0], "QQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[1], "QLD"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[1], "QQQ"], 0.5)

    def test_all_target_nan_falls_back_to_cash(self):
        close, dates = self._make_data(n=2)
        close.loc[dates[0], ["TQQQ", "QLD", "QQQ"]] = np.nan
        vix = pd.Series([35.0, 35.0], index=dates)
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0, 40.0))
        # 所有候选标的均缺失，应保持空仓
        self.assertTrue((weights.loc[dates[0]] == 0.0).all())
        self.assertAlmostEqual(weights.loc[dates[1], "QLD"], 1.0)

    def test_unknown_symbol_ignored(self):
        close, dates = self._make_data()
        close = close.drop(columns=["TQQQ", "QLD"])
        vix = pd.Series([12.0, 15.0, 25.0, 35.0, 45.0], index=dates)
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0, 40.0))
        # 只有 QQQ 列，所有包含 QQQ 的区间均回退到 QQQ
        self.assertAlmostEqual(weights.loc[dates[0], "QQQ"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[1], "QQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[2], "QQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[3], "QQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[4], "QQQ"], 1.0)

    def test_all_nan_vix_keeps_cash(self):
        close, dates = self._make_data()
        vix = pd.Series([np.nan] * len(dates), index=dates)
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0, 40.0))
        self.assertTrue((weights == 0.0).all().all())

    def test_leading_nan_vix_keeps_cash_until_first_valid(self):
        close, dates = self._make_data(n=5)
        # VIX 从第 4 个交易日才有数据
        vix = pd.Series(
            [np.nan, np.nan, np.nan, 12.0, 15.0],
            index=dates,
        )
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0, 40.0))
        # 前三天应空仓，避免 NaN 落入高杠杆分支
        self.assertTrue((weights.iloc[:3] == 0.0).all().all())
        self.assertAlmostEqual(weights.loc[dates[3], "QQQ"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[4], "QQQ"], 1.0)

    def test_single_threshold_mapping(self):
        close, dates = self._make_data(n=4)
        # 1 个阈值：2 档仓位
        vix = pd.Series([10.0, 20.0, 25.0, 35.0], index=dates)
        weights = backtest.build_signals(close, vix, (20.0,))
        # v < 20 -> 0.5x QQQ；v >= 20 -> 1.0x QQQ
        self.assertAlmostEqual(weights.loc[dates[0], "QQQ"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[1], "QQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[2], "QQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[3], "QQQ"], 1.0)

    def test_three_thresholds_mapping(self):
        close, dates = self._make_data(n=5)
        # 3 个阈值：4 档仓位
        vix = pd.Series([10.0, 15.0, 22.0, 28.0, 35.0], index=dates)
        weights = backtest.build_signals(close, vix, (13.0, 20.0, 30.0))
        # v < 13 -> 0.5x QQQ
        self.assertAlmostEqual(weights.loc[dates[0], "QQQ"], 0.5)
        # 13 <= v < 20 -> 1.0x QQQ
        self.assertAlmostEqual(weights.loc[dates[1], "QQQ"], 1.0)
        # 20 <= v <= 30 -> 1.5x (QLD + QQQ)
        self.assertAlmostEqual(weights.loc[dates[2], "QLD"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[2], "QQQ"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[3], "QLD"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[3], "QQQ"], 0.5)
        # v > 30 -> 2.0x QLD
        self.assertAlmostEqual(weights.loc[dates[4], "QLD"], 1.0)

    def test_five_thresholds_mapping(self):
        close, dates = self._make_data(n=6)
        # 5 个阈值：6 档仓位
        vix = pd.Series([8.0, 11.0, 15.0, 25.0, 35.0, 45.0], index=dates)
        weights = backtest.build_signals(close, vix, (10.0, 13.0, 20.0, 30.0, 40.0))
        # v < 10 -> 0.5x QQQ
        self.assertAlmostEqual(weights.loc[dates[0], "QQQ"], 0.5)
        # 10 <= v < 13 -> 1.0x QQQ
        self.assertAlmostEqual(weights.loc[dates[1], "QQQ"], 1.0)
        # 13 <= v < 20 -> 1.5x (QLD + QQQ)
        self.assertAlmostEqual(weights.loc[dates[2], "QLD"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[2], "QQQ"], 0.5)
        # 20 <= v <= 30 -> 2.0x QLD
        self.assertAlmostEqual(weights.loc[dates[3], "QLD"], 1.0)
        # 30 < v <= 40 -> 2.5x (TQQQ + QLD)
        self.assertAlmostEqual(weights.loc[dates[4], "TQQQ"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[4], "QLD"], 0.5)
        # v > 40 -> 3.0x TQQQ
        self.assertAlmostEqual(weights.loc[dates[5], "TQQQ"], 1.0)

    def test_excessive_leverage_capped_to_full_tqqq(self):
        close, dates = self._make_data(n=2)
        # 6 个阈值产生最大 3.5x 档位，应限制为满仓 TQQQ
        vix = pd.Series([60.0, 60.0], index=dates)
        weights = backtest.build_signals(close, vix, (10.0, 13.0, 20.0, 30.0, 40.0, 50.0))
        self.assertAlmostEqual(weights.loc[dates[0], "TQQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[1], "TQQQ"], 1.0)

    def test_custom_strategy_mapping(self):
        """验证通过参数传入的 20/30/40/50 策略与对应 5 档仓位。"""
        close, dates = self._make_data(n=6)
        allocations = [
            [("QQQ", 1.0)],
            [("QLD", 0.5), ("QQQ", 0.5)],
            [("QLD", 1.0)],
            [("QLD", 0.5), ("TQQQ", 0.5)],
            [("TQQQ", 1.0)],
        ]
        # 取各区间代表值及边界 30
        vix = pd.Series([15.0, 25.0, 30.0, 35.0, 45.0, 55.0], index=dates)
        weights = backtest.build_signals(close, vix, (20.0, 30.0, 40.0, 50.0), allocations=allocations)

        # VIX < 20：满仓 QQQ
        self.assertAlmostEqual(weights.loc[dates[0], "QQQ"], 1.0)
        # 20 <= VIX <= 30：半仓 QLD + 半仓 QQQ
        self.assertAlmostEqual(weights.loc[dates[1], "QLD"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[1], "QQQ"], 0.5)
        # VIX = 30 应落在 20-30 区间
        self.assertAlmostEqual(weights.loc[dates[2], "QLD"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[2], "QQQ"], 0.5)
        # 30 < VIX <= 40：满仓 QLD
        self.assertAlmostEqual(weights.loc[dates[3], "QLD"], 1.0)
        # 40 < VIX <= 50：半仓 QLD + 半仓 TQQQ
        self.assertAlmostEqual(weights.loc[dates[4], "QLD"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[4], "TQQQ"], 0.5)
        # VIX > 50：满仓 TQQQ
        self.assertAlmostEqual(weights.loc[dates[5], "TQQQ"], 1.0)

    def test_hysteresis_delays_downgrade(self):
        """验证 hysteresis 可在 VIX 回落时延迟降档。"""
        close, dates = self._make_data(n=5)
        allocations = [
            [("QQQ", 1.0)],
            [("QLD", 0.5), ("QQQ", 0.5)],
            [("QLD", 1.0)],
            [("QLD", 0.5), ("TQQQ", 0.5)],
            [("TQQQ", 1.0)],
        ]
        # VIX 序列：15 -> 35（升档到 QLD） -> 28（回落，但 hysteresis=5 应保持 QLD）
        # -> 24（跌破 30-5=25 的退出阈值，降档） -> 55（再升档到 TQQQ）
        vix = pd.Series([15.0, 35.0, 28.0, 24.0, 55.0], index=dates)
        weights = backtest.build_signals(
            close, vix, (20.0, 30.0, 40.0, 50.0), allocations=allocations, hysteresis=5.0
        )

        self.assertAlmostEqual(weights.loc[dates[0], "QQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[1], "QLD"], 1.0)
        # VIX 28 仍高于 30-5=25，保持 QLD
        self.assertAlmostEqual(weights.loc[dates[2], "QLD"], 1.0)
        # VIX 24 跌破 25，降回 20-30 档位
        self.assertAlmostEqual(weights.loc[dates[3], "QLD"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[3], "QQQ"], 0.5)
        # VIX 55 立即升档到 TQQQ
        self.assertAlmostEqual(weights.loc[dates[4], "TQQQ"], 1.0)

    def test_vix_ma_smooths_signal(self):
        """验证 VIX 移动平均可平滑单日尖刺，避免立即进入高杠杆档位。"""
        close, dates = self._make_data(n=6)
        allocations = [
            [("QQQ", 1.0)],
            [("QLD", 0.5), ("QQQ", 0.5)],
            [("QLD", 1.0)],
            [("QLD", 0.5), ("TQQQ", 0.5)],
            [("TQQQ", 1.0)],
        ]
        # VIX 单日尖刺到 35 后迅速回落：15 -> 35 -> 25 -> 25 -> 25 -> 25
        vix = pd.Series([15.0, 35.0, 25.0, 25.0, 25.0, 25.0], index=dates)

        # 无 MA：第 1 天 VIX=35 直接进入 30-40 档位（满仓 QLD）
        weights_no_ma = backtest.build_signals(
            close, vix, (20.0, 30.0, 40.0, 50.0), allocations=allocations
        )
        self.assertAlmostEqual(weights_no_ma.loc[dates[1], "QLD"], 1.0)

        # MA=3：第 1 天 MA=(15+35)/2=25，仍在 20-30 档位（半 QLD + 半 QQQ）
        # 不会直接满仓 QLD
        weights_ma = backtest.build_signals(
            close, vix, (20.0, 30.0, 40.0, 50.0), allocations=allocations, vix_ma=3
        )
        self.assertAlmostEqual(weights_ma.loc[dates[1], "QLD"], 0.5)
        self.assertAlmostEqual(weights_ma.loc[dates[1], "QQQ"], 0.5)

    def test_hysteresis_initial_nan_uses_first_valid_value(self):
        """验证 hysteresis 在首值为 NaN 时，从第一个有效 VIX 值开始计算档位。"""
        close, dates = self._make_data(n=5)
        allocations = [
            [("QQQ", 1.0)],
            [("QLD", 0.5), ("QQQ", 0.5)],
            [("QLD", 1.0)],
            [("QLD", 0.5), ("TQQQ", 0.5)],
            [("TQQQ", 1.0)],
        ]
        # NaN -> 35（应初始化到 QLD 档位） -> 28（hysteresis=5 应保持 QLD）
        vix = pd.Series([np.nan, 35.0, 28.0, 24.0, 55.0], index=dates)
        weights = backtest.build_signals(
            close, vix, (20.0, 30.0, 40.0, 50.0), allocations=allocations, hysteresis=5.0
        )

        self.assertTrue((weights.loc[dates[0]] == 0.0).all())
        self.assertAlmostEqual(weights.loc[dates[1], "QLD"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[2], "QLD"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[3], "QLD"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[3], "QQQ"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[4], "TQQQ"], 1.0)

    def test_vix_ma_with_leading_nan_keeps_cash(self):
        """验证 vix_ma 在存在前导 NaN 时不会提前生成非空仓信号。"""
        close, dates = self._make_data(n=5)
        allocations = [
            [("QQQ", 1.0)],
            [("QLD", 0.5), ("QQQ", 0.5)],
            [("QLD", 1.0)],
            [("QLD", 0.5), ("TQQQ", 0.5)],
            [("TQQQ", 1.0)],
        ]
        vix = pd.Series([np.nan, np.nan, 35.0, 25.0, 15.0], index=dates)
        weights = backtest.build_signals(
            close, vix, (20.0, 30.0, 40.0, 50.0), allocations=allocations, vix_ma=3
        )

        self.assertTrue((weights.loc[dates[0]] == 0.0).all())
        self.assertTrue((weights.loc[dates[1]] == 0.0).all())
        # 第 2 天 MA=35，进入 30-40 档位（满仓 QLD）
        self.assertAlmostEqual(weights.loc[dates[2], "QLD"], 1.0)

    def test_hysteresis_and_vix_ma_combined(self):
        """验证 hysteresis 与 vix_ma 同时启用时行为正确。"""
        close, dates = self._make_data(n=6)
        allocations = [
            [("QQQ", 1.0)],
            [("QLD", 0.5), ("QQQ", 0.5)],
            [("QLD", 1.0)],
            [("QLD", 0.5), ("TQQQ", 0.5)],
            [("TQQQ", 1.0)],
        ]
        # 15 -> 45 -> 25 -> 25 -> 25 -> 15
        # MA=3 时第 1 天 MA=(15+45)/2=30，刚好在 20-30 档位边界（右闭），不会进 QLD
        vix = pd.Series([15.0, 45.0, 25.0, 25.0, 25.0, 15.0], index=dates)
        weights = backtest.build_signals(
            close, vix, (20.0, 30.0, 40.0, 50.0), allocations=allocations, hysteresis=5.0, vix_ma=3
        )

        self.assertAlmostEqual(weights.loc[dates[0], "QQQ"], 1.0)
        # MA=30 落在 20-30 档位（半 QLD + 半 QQQ），而非 30-40 档位
        self.assertAlmostEqual(weights.loc[dates[1], "QLD"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[1], "QQQ"], 0.5)

    def test_custom_allocations_override_default(self):
        close, dates = self._make_data(n=3)
        # 1 个阈值：2 个区间，自定义分配
        vix = pd.Series([10.0, 20.0, 30.0], index=dates)
        allocations = [
            [("QQQ", 0.25), ("QLD", 0.25)],
            [("TQQQ", 0.5)],
        ]
        weights = backtest.build_signals(close, vix, (20.0,), allocations=allocations)
        # v < 20: QQQ 0.25 + QLD 0.25
        self.assertAlmostEqual(weights.loc[dates[0], "QQQ"], 0.25)
        self.assertAlmostEqual(weights.loc[dates[0], "QLD"], 0.25)
        # v >= 20: TQQQ 0.5
        self.assertAlmostEqual(weights.loc[dates[1], "TQQQ"], 0.5)
        self.assertAlmostEqual(weights.loc[dates[2], "TQQQ"], 0.5)

    def test_custom_allocations_partial_cash(self):
        close, dates = self._make_data(n=2)
        vix = pd.Series([10.0, 30.0], index=dates)
        # 权重和 0.3，剩余现金
        allocations = [
            [("QQQ", 0.3)],
            [("QLD", 0.3)],
        ]
        weights = backtest.build_signals(close, vix, (20.0,), allocations=allocations)
        self.assertAlmostEqual(weights.loc[dates[0], "QQQ"], 0.3)
        self.assertAlmostEqual(weights.loc[dates[1], "QLD"], 0.3)
        self.assertAlmostEqual(weights.sum(axis=1).iloc[0], 0.3)
        self.assertAlmostEqual(weights.sum(axis=1).iloc[1], 0.3)

    def test_invalid_allocations_length_raises(self):
        close, dates = self._make_data(n=2)
        vix = pd.Series([10.0, 30.0], index=dates)
        # 1 个阈值需要 2 个分配，但只给 1 个
        allocations = [[("QQQ", 1.0)]]
        with self.assertRaises(ValueError) as ctx:
            backtest.build_signals(close, vix, (20.0,), allocations=allocations)
        self.assertIn("分配数量", str(ctx.exception))

    def test_invalid_allocations_weight_raises(self):
        close, dates = self._make_data(n=2)
        vix = pd.Series([10.0, 30.0], index=dates)
        allocations = [
            [("QQQ", 0.6), ("QLD", 0.6)],
            [("QQQ", 0.5)],
        ]
        with self.assertRaises(ValueError) as ctx:
            backtest.build_signals(close, vix, (20.0,), allocations=allocations)
        self.assertIn("权重和", str(ctx.exception))


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

    def test_total_loss_does_not_produce_complex_annual_return(self):
        class TotalLossPortfolio:
            def value(self):
                return pd.Series([100.0, 50.0, 0.0], index=pd.date_range("2024-01-01", periods=3))

        metrics = backtest._portfolio_value_metrics(TotalLossPortfolio())
        self.assertAlmostEqual(metrics["total_return"], -1.0)
        self.assertIsInstance(metrics["annual_return"], float)
        self.assertLess(metrics["annual_return"], 0.0)
        self.assertFalse(isinstance(metrics["annual_return"], complex))


class TestParseArgs(unittest.TestCase):
    def _run_parse(self, argv):
        with patch.object(sys, "argv", argv):
            return backtest.parse_args()

    def test_default_thresholds(self):
        args = self._run_parse(["backtest.py"])
        self.assertEqual(args.thresholds, (13.0, 20.0, 30.0, 40.0))

    def test_custom_thresholds(self):
        args = self._run_parse(["backtest.py", "--thresholds", "15", "25", "35", "45"])
        self.assertEqual(args.thresholds, (15.0, 25.0, 35.0, 45.0))

    def test_unordered_thresholds_exit(self):
        stderr = StringIO()
        with patch.object(sys, "argv", ["backtest.py", "--thresholds", "40", "30", "20", "13"]):
            with patch.object(sys, "stderr", stderr):
                with self.assertRaises(SystemExit):
                    backtest.parse_args()

    def test_negative_cash_rejected(self):
        with patch.object(sys, "argv", ["backtest.py", "--cash", "-1000"]):
            with self.assertRaises(SystemExit):
                backtest.parse_args()

    def test_negative_fees_rejected(self):
        with patch.object(sys, "argv", ["backtest.py", "--fees", "-0.001"]):
            with self.assertRaises(SystemExit):
                backtest.parse_args()

    def test_negative_hysteresis_rejected(self):
        with patch.object(sys, "argv", ["backtest.py", "--hysteresis", "-1"]):
            with self.assertRaises(SystemExit):
                backtest.parse_args()

    def test_hysteresis_parsed(self):
        args = self._run_parse(["backtest.py", "--hysteresis", "5"])
        self.assertAlmostEqual(args.hysteresis, 5.0)

    def test_vix_ma_parsed(self):
        args = self._run_parse(["backtest.py", "--vix-ma", "5"])
        self.assertEqual(args.vix_ma, 5)

    def test_vix_ma_below_one_rejected(self):
        with patch.object(sys, "argv", ["backtest.py", "--vix-ma", "0"]):
            with self.assertRaises(SystemExit):
                backtest.parse_args()

    def test_benchmark_defaults_to_all_three(self):
        args = self._run_parse(["backtest.py"])
        self.assertEqual(args.benchmark, ["QQQ", "QLD", "TQQQ"])

    def test_custom_benchmark(self):
        args = self._run_parse(["backtest.py", "--benchmark", "QLD"])
        self.assertEqual(args.benchmark, ["QLD"])

    def test_multiple_benchmarks(self):
        args = self._run_parse(["backtest.py", "--benchmark", "QQQ", "QLD"])
        self.assertEqual(args.benchmark, ["QQQ", "QLD"])

    def test_variable_length_thresholds(self):
        args = self._run_parse(["backtest.py", "--thresholds", "10", "20", "30"])
        self.assertEqual(args.thresholds, (10.0, 20.0, 30.0))

    def test_single_threshold(self):
        args = self._run_parse(["backtest.py", "--thresholds", "20"])
        self.assertEqual(args.thresholds, (20.0,))

    def test_unordered_thresholds_exit_with_variable_length(self):
        with patch.object(sys, "argv", ["backtest.py", "--thresholds", "30", "20", "10"]):
            with self.assertRaises(SystemExit):
                backtest.parse_args()

    def test_duplicate_thresholds_exit(self):
        with patch.object(sys, "argv", ["backtest.py", "--thresholds", "20", "20", "30"]):
            with self.assertRaises(SystemExit):
                backtest.parse_args()

    def test_custom_allocations_parsed(self):
        args = self._run_parse([
            "backtest.py",
            "--thresholds", "20", "30",
            "--allocations", "QQQ:1.0", "QLD:0.5,QQQ:0.5", "TQQQ:1.0",
        ])
        self.assertEqual(len(args.allocations), 3)
        self.assertEqual(args.allocations[0], [("QQQ", 1.0)])
        self.assertEqual(args.allocations[1], [("QLD", 0.5), ("QQQ", 0.5)])
        self.assertEqual(args.allocations[2], [("TQQQ", 1.0)])

    def test_allocations_wrong_count_exit(self):
        with patch.object(sys, "argv", [
            "backtest.py",
            "--thresholds", "20", "30",
            "--allocations", "QQQ:1.0", "QLD:1.0",
        ]):
            with self.assertRaises(SystemExit):
                backtest.parse_args()

    def test_allocations_invalid_format_exit(self):
        with patch.object(sys, "argv", [
            "backtest.py",
            "--thresholds", "20",
            "--allocations", "QQQ_1.0", "TQQQ:1.0",
        ]):
            with self.assertRaises(SystemExit):
                backtest.parse_args()


class TestRunBacktest(unittest.TestCase):
    def test_empty_common_index_raises(self):
        close = pd.DataFrame(
            {"QQQ": [100.0, 101.0]},
            index=pd.date_range("2024-01-01", periods=2),
        )
        vix = pd.Series(
            [20.0, 21.0],
            index=pd.date_range("2025-01-01", periods=2),
        )
        with self.assertRaises(ValueError) as ctx:
            backtest.run_backtest(
                symbols=["QQQ"],
                start="2024-01-01",
                end="2025-01-02",
                thresholds=(13.0, 20.0, 30.0, 40.0),
                initial_cash=10000,
                fees=0.001,
                slippage=0.001,
                close=close,
                vix=vix,
            )
        self.assertIn("没有重叠日期", str(ctx.exception))

    def test_multi_asset_target_percent_with_cash_sharing(self):
        """验证多资产 targetpercent 在 cash_sharing=True 下资金从初始现金正确起步。"""
        dates = pd.date_range("2024-01-01", periods=5)
        close = pd.DataFrame(
            {
                "QQQ": [100.0, 101.0, 102.0, 103.0, 104.0],
                "QLD": [50.0, 51.0, 52.0, 53.0, 54.0],
            },
            index=dates,
        )
        vix = pd.Series([15.0] * 5, index=dates)
        portfolio, _, _, _ = backtest.run_backtest(
            symbols=["QQQ", "QLD"],
            start="2024-01-01",
            end="2024-01-06",
            thresholds=(20.0,),
            initial_cash=10000.0,
            fees=0.0,
            slippage=0.0,
            close=close,
            vix=vix,
            allocations=[[("QQQ", 0.5), ("QLD", 0.5)], [("QQQ", 1.0)]],
        )
        value = portfolio.value()
        self.assertAlmostEqual(float(value.iloc[0]), 10000.0)
        self.assertFalse(value.isna().any())
        self.assertGreater(float(value.iloc[-1]), 0.0)

    def test_signal_executed_next_day_to_avoid_lookahead(self):
        """验证 run_backtest 将信号滞后一日执行，避免前视偏差。"""
        dates = pd.date_range("2024-01-01", periods=3)
        close = pd.DataFrame({"QQQ": [100.0, 101.0, 102.0]}, index=dates)
        vix = pd.Series([15.0, 25.0, 25.0], index=dates)
        _, _, _, weights = backtest.run_backtest(
            symbols=["QQQ"],
            start="2024-01-01",
            end="2024-01-04",
            thresholds=(20.0,),
            initial_cash=10000.0,
            fees=0.0,
            slippage=0.0,
            close=close,
            vix=vix,
            allocations=[[("QQQ", 1.0)], [("QQQ", 1.0)]],
        )
        # 第 0 天无信号，保持空仓；第 1 天执行第 0 天的信号（满仓 QQQ）
        self.assertAlmostEqual(weights.loc[dates[0], "QQQ"], 0.0)
        self.assertAlmostEqual(weights.loc[dates[1], "QQQ"], 1.0)
        self.assertAlmostEqual(weights.loc[dates[2], "QQQ"], 1.0)


class TestSaveResults(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.tmpdir.name)
        patcher = patch.object(backtest, "OUTPUT_DIR", self.output_dir)
        self.addCleanup(patcher.stop)
        patcher.start()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_args(self, **overrides):
        class Args:
            symbols = ["QQQ"]
            thresholds = (13.0, 20.0, 30.0, 40.0)
            cash = 10000.0
            fees = 0.001
            slippage = 0.001
            benchmark = ["QQQ"]
            allocations = None

        args = Args()
        for k, v in overrides.items():
            setattr(args, k, v)
        return args

    def _make_mock_portfolio(self, dates):
        class MockTrades:
            def count(self):
                return pd.Series([0], dtype=int)

            def win_rate(self):
                return pd.Series([0.0])

        class MockPortfolio:
            def __init__(self):
                self.trades = MockTrades()

            def value(self):
                return pd.Series([10000.0, 10200.0, 10400.0], index=dates)

        return MockPortfolio()

    def test_missing_benchmark_skips_benchmark_curve(self):
        dates = pd.date_range("2024-01-01", periods=3)
        close = pd.DataFrame({"QLD": [50.0, 51.0, 52.0]}, index=dates)
        portfolio = self._make_mock_portfolio(dates)
        portfolio.close = close
        args = self._make_args(symbols=["QLD"], benchmark="QQQ")
        weights = pd.DataFrame({"QLD": [1.0, 1.0, 1.0]}, index=dates)
        # 不应抛出 KeyError
        backtest.save_results(portfolio, weights, args, close)

    def test_custom_benchmark_used(self):
        dates = pd.date_range("2024-01-01", periods=3)
        close = pd.DataFrame(
            {"QQQ": [100.0, 110.0, 120.0], "QLD": [50.0, 51.0, 52.0]},
            index=dates,
        )
        portfolio = self._make_mock_portfolio(dates)
        portfolio.close = close
        args = self._make_args(benchmark="QLD")
        weights = pd.DataFrame({"QLD": [1.0, 1.0, 1.0]}, index=dates)
        backtest.save_results(portfolio, weights, args, close)
        # 验证 output 目录生成了文件
        files = list(self.output_dir.glob("vix_leverage_rotation_*.html"))
        self.assertTrue(len(files) > 0)

    def test_multiple_benchmarks_drawn(self):
        dates = pd.date_range("2024-01-01", periods=3)
        close = pd.DataFrame(
            {"QQQ": [100.0, 110.0, 120.0], "QLD": [50.0, 51.0, 52.0]},
            index=dates,
        )
        portfolio = self._make_mock_portfolio(dates)
        portfolio.close = close
        args = self._make_args(benchmark=["QQQ", "QLD"])
        weights = pd.DataFrame({"QQQ": [1.0, 1.0, 1.0]}, index=dates)
        # 不应抛出异常
        backtest.save_results(portfolio, weights, args, close)
        files = list(self.output_dir.glob("vix_leverage_rotation_*.html"))
        self.assertTrue(len(files) > 0)

    def test_html_includes_config_panel(self):
        dates = pd.date_range("2024-01-01", periods=3)
        close = pd.DataFrame({"QQQ": [100.0, 110.0, 120.0]}, index=dates)
        portfolio = self._make_mock_portfolio(dates)
        portfolio.close = close
        args = self._make_args()
        weights = pd.DataFrame({"QQQ": [1.0, 1.0, 1.0]}, index=dates)
        backtest.save_results(portfolio, weights, args, close)
        files = list(self.output_dir.glob("vix_leverage_rotation_*.html"))
        self.assertTrue(len(files) > 0)
        html = files[0].read_text(encoding="utf-8")
        self.assertIn("策略配置", html)
        self.assertIn("区间持仓配置", html)
        self.assertIn("VIX 阈值", html)
        self.assertIn("回测标的", html)
        self.assertIn("买入持有基准", html)

    def test_json_metrics_saved(self):
        dates = pd.date_range("2024-01-01", periods=3)
        close = pd.DataFrame({"QQQ": [100.0, 110.0, 120.0]}, index=dates)
        portfolio = self._make_mock_portfolio(dates)
        portfolio.close = close
        args = self._make_args()
        weights = pd.DataFrame({"QQQ": [1.0, 1.0, 1.0]}, index=dates)
        backtest.save_results(portfolio, weights, args, close)
        html_files = list(self.output_dir.glob("vix_leverage_rotation_*.html"))
        json_files = list(self.output_dir.glob("vix_leverage_rotation_*_metrics.json"))
        self.assertTrue(len(html_files) > 0)
        self.assertTrue(len(json_files) > 0)
        metrics = json.loads(json_files[0].read_text(encoding="utf-8"))
        self.assertIn("total_return", metrics)
        self.assertIn("benchmarks", metrics)


class TestRegimeLabel(unittest.TestCase):
    def test_four_threshold_boundary_labels(self):
        thresholds = (13.0, 20.0, 30.0, 40.0)
        self.assertEqual(backtest._regime_label(0, thresholds), "VIX < 13.0")
        self.assertEqual(backtest._regime_label(1, thresholds), "13.0 <= VIX <= 20.0")
        self.assertEqual(backtest._regime_label(2, thresholds), "20.0 <= VIX <= 30.0")
        self.assertEqual(backtest._regime_label(3, thresholds), "30.0 <= VIX <= 40.0")
        self.assertEqual(backtest._regime_label(4, thresholds), "VIX > 40.0")

    def test_single_threshold_labels(self):
        thresholds = (20.0,)
        self.assertEqual(backtest._regime_label(0, thresholds), "VIX < 20.0")
        self.assertEqual(backtest._regime_label(1, thresholds), "VIX >= 20.0")

    def test_empty_thresholds_label(self):
        self.assertEqual(backtest._regime_label(0, ()), "所有 VIX")


class TestFetchVixData(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmpdir.name)
        patcher = patch.object(backtest, "DATA_DIR", self.data_dir)
        self.addCleanup(patcher.stop)
        patcher.start()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_local_vix_high_low_midpoint(self):
        dates = pd.date_range("2024-01-01", periods=3)
        path = self.data_dir / "VIX_History.csv"
        pd.DataFrame(
            {
                "DATE": [d.strftime("%m/%d/%Y") for d in dates],
                "OPEN": [10.0, 20.0, 30.0],
                "HIGH": [12.0, 22.0, 32.0],
                "LOW": [8.0, 18.0, 28.0],
                "CLOSE": [10.0, 20.0, 30.0],
            }
        ).to_csv(path, index=False)
        vix = backtest.fetch_vix_data("2024-01-01", "2024-01-04")
        self.assertEqual(len(vix), 3)
        self.assertAlmostEqual(vix.iloc[0], 10.0)  # (12 + 8) / 2
        self.assertAlmostEqual(vix.iloc[1], 20.0)  # (22 + 18) / 2

    def test_local_vix_fallback_to_close_when_high_low_missing(self):
        dates = pd.date_range("2024-01-01", periods=3)
        path = self.data_dir / "VIX_History.csv"
        pd.DataFrame(
            {
                "DATE": [d.strftime("%m/%d/%Y") for d in dates],
                "CLOSE": [10.0, 20.0, 30.0],
            }
        ).to_csv(path, index=False)
        vix = backtest.fetch_vix_data("2024-01-01", "2024-01-04")
        self.assertEqual(len(vix), 3)
        self.assertAlmostEqual(vix.iloc[0], 10.0)
        self.assertAlmostEqual(vix.iloc[2], 30.0)

    @patch("backtest.yf.download")
    def test_yfinance_vix_high_low_midpoint(self, mock_download):
        dates = pd.date_range("2024-01-01", periods=3)
        mock_download.return_value = pd.DataFrame(
            {
                "Open": [10.0, 20.0, 30.0],
                "High": [12.0, 22.0, 32.0],
                "Low": [8.0, 18.0, 28.0],
                "Close": [10.0, 20.0, 30.0],
            },
            index=dates,
        )
        vix = backtest.fetch_vix_data("2024-01-01", "2024-01-04")
        self.assertEqual(len(vix), 3)
        self.assertAlmostEqual(vix.iloc[0], 10.0)

    @patch("backtest.yf.download")
    def test_yfinance_vix_fallback_to_close_when_high_low_missing(self, mock_download):
        dates = pd.date_range("2024-01-01", periods=3)
        mock_download.return_value = pd.DataFrame(
            {"Close": [10.0, 20.0, 30.0]},
            index=dates,
        )
        vix = backtest.fetch_vix_data("2024-01-01", "2024-01-04")
        self.assertEqual(len(vix), 3)
        self.assertAlmostEqual(vix.iloc[0], 10.0)


class TestEtfDataHelpers(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmpdir.name)
        self.metadata_file = self.data_dir / "etf_metadata.json"
        patcher_data = patch.object(backtest, "DATA_DIR", self.data_dir)
        patcher_meta = patch.object(backtest, "ETF_METADATA_FILE", self.metadata_file)
        self.addCleanup(patcher_data.stop)
        self.addCleanup(patcher_meta.stop)
        patcher_data.start()
        patcher_meta.start()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_etf_csv(self, symbol, dates, close_values):
        """按项目格式写入 ETF 本地 CSV。"""
        path = self.data_dir / f"{symbol}_History.csv"
        df = pd.DataFrame(
            {
                "DATE": [d.strftime("%m/%d/%Y") for d in dates],
                "OPEN": close_values,
                "HIGH": close_values,
                "LOW": close_values,
                "CLOSE": close_values,
            }
        )
        df.to_csv(path, index=False, float_format="%.6f")
        return path

    def _make_yf_download_result(self, symbols, dates, value=100.0):
        """构造与 yfinance 返回结构一致的 DataFrame。"""
        if len(symbols) == 1:
            return pd.DataFrame(
                {
                    "Open": [value] * len(dates),
                    "High": [value + 1] * len(dates),
                    "Low": [value - 1] * len(dates),
                    "Close": [value] * len(dates),
                },
                index=dates,
            )
        cols = pd.MultiIndex.from_product([["Open", "High", "Low", "Close"], symbols])
        data = {}
        for field in ["Open", "High", "Low", "Close"]:
            for symbol in symbols:
                data[(field, symbol)] = [value] * len(dates)
        return pd.DataFrame(data, index=dates, columns=cols)

    def test_validate_symbols_normalizes_and_rejects_traversal(self):
        self.assertEqual(backtest._validate_symbols(["qqq", "Qld"]), ["QQQ", "QLD"])
        with self.assertRaises(ValueError):
            backtest._validate_symbols(["../QQQ"])
        with self.assertRaises(ValueError):
            backtest._validate_symbols(["QQQ/QLD"])
        with self.assertRaises(ValueError):
            backtest._validate_symbols([])

    def test_local_etf_path_rejects_traversal(self):
        with self.assertRaises(ValueError):
            backtest._local_etf_path("../QQQ")
        path = backtest._local_etf_path("QQQ")
        self.assertTrue(path.is_relative_to(self.data_dir))

    def test_load_local_etf_missing_returns_none(self):
        self.assertIsNone(backtest._load_local_etf("QQQ"))

    def test_load_local_etf_missing_columns_raises(self):
        path = self.data_dir / "QQQ_History.csv"
        pd.DataFrame({"DATE": ["01/01/2024"], "CLOSE": [100.0]}).to_csv(path, index=False)
        with self.assertRaises(ValueError):
            backtest._load_local_etf("QQQ")

    def test_calculate_download_plan_no_local_needs_backward(self):
        metadata = {}
        local_data = {}
        need, start, meta = backtest._calculate_download_plan(
            ["QQQ"], local_data, metadata,
            pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-10"), False
        )
        self.assertTrue(need)
        self.assertEqual(start, pd.Timestamp("2024-01-01"))
        self.assertEqual(meta["QQQ"]["requested_start"], "2024-01-01")

    def test_calculate_download_plan_local_fresh_no_download(self):
        dates = pd.date_range("2024-01-01", periods=10)
        local_data = {"QQQ": pd.DataFrame({"OPEN": [100.0] * 10, "HIGH": [101.0] * 10, "LOW": [99.0] * 10, "CLOSE": [100.0] * 10}, index=dates)}
        metadata = {"QQQ": {"requested_start": "2024-01-01"}}
        need, start, meta = backtest._calculate_download_plan(
            ["QQQ"], local_data, metadata,
            pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-10"), False
        )
        self.assertFalse(need)

    def test_calculate_download_plan_stale_needs_forward(self):
        dates = pd.date_range("2024-01-01", periods=5)
        local_data = {"QQQ": pd.DataFrame({"OPEN": [100.0] * 5, "HIGH": [101.0] * 5, "LOW": [99.0] * 5, "CLOSE": [100.0] * 5}, index=dates)}
        metadata = {"QQQ": {"requested_start": "2024-01-01"}}
        need, start, meta = backtest._calculate_download_plan(
            ["QQQ"], local_data, metadata,
            pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-15"), True
        )
        self.assertTrue(need)
        # 向前扩展起始日应不早于 target_start
        self.assertGreaterEqual(start, pd.Timestamp("2024-01-01"))

    def test_merge_and_save_combines_and_saves(self):
        dates_old = pd.date_range("2024-01-01", periods=3)
        dates_new = pd.date_range("2024-01-03", periods=3)
        local_data = {
            "QQQ": pd.DataFrame(
                {"OPEN": [1.0] * 3, "HIGH": [2.0] * 3, "LOW": [0.5] * 3, "CLOSE": [1.0] * 3},
                index=dates_old,
            )
        }
        downloaded = {
            "QQQ": pd.DataFrame(
                {"OPEN": [1.5] * 3, "HIGH": [2.5] * 3, "LOW": [1.0] * 3, "CLOSE": [1.5] * 3},
                index=dates_new,
            )
        }
        result = backtest._merge_and_save_etf_data(local_data, downloaded)
        self.assertEqual(len(result["QQQ"]), 5)
        # 重叠日期 2024-01-03 应使用下载数据
        self.assertAlmostEqual(result["QQQ"].loc[dates_old[2], "CLOSE"], 1.5)
        self.assertTrue((self.data_dir / "QQQ_History.csv").exists())

    @patch("backtest.yf.download")
    def test_fetch_uses_local_data_without_download(self, mock_download):
        dates = pd.date_range("2024-01-01", periods=5)
        self._write_etf_csv("QQQ", dates, [100.0, 101.0, 102.0, 103.0, 104.0])
        close = backtest.fetch_etf_data(["QQQ"], "2024-01-01", "2024-01-05")
        mock_download.assert_not_called()
        self.assertIn("QQQ", close.columns)
        self.assertEqual(len(close), 4)  # end exclusive

    @patch("backtest.yf.download")
    def test_fetch_downloads_when_local_missing(self, mock_download):
        dates = pd.date_range("2024-01-01", periods=5)
        mock_download.return_value = self._make_yf_download_result(["QQQ"], dates, 100.0)
        close = backtest.fetch_etf_data(["QQQ"], "2024-01-01", "2024-01-05")
        mock_download.assert_called_once()
        self.assertIn("QQQ", close.columns)
        self.assertTrue((self.data_dir / "QQQ_History.csv").exists())

    @patch("backtest.yf.download")
    def test_fetch_raises_when_symbol_missing(self, mock_download):
        dates = pd.date_range("2024-01-01", periods=5)
        # 多标的下载结构中包含 TQQQ 列但全为 NaN，模拟该标的无数据
        cols = pd.MultiIndex.from_product([["Open", "High", "Low", "Close"], ["QQQ", "TQQQ"]])
        data = {}
        for field in ["Open", "High", "Low", "Close"]:
            data[(field, "QQQ")] = [100.0] * len(dates)
            data[(field, "TQQQ")] = [np.nan] * len(dates)
        mock_download.return_value = pd.DataFrame(data, index=dates, columns=cols)
        with self.assertRaises(ValueError) as ctx:
            backtest.fetch_etf_data(["QQQ", "TQQQ"], "2024-01-01", "2024-01-05")
        self.assertIn("TQQQ", str(ctx.exception))

    @patch("backtest.yf.download")
    def test_fetch_raises_on_empty_downloaded_single_symbol(self, mock_download):
        dates = pd.date_range("2024-01-01", periods=3)
        # 单标的返回全 NaN，dropna 后为空
        mock_download.return_value = pd.DataFrame(
            {"Open": [np.nan] * 3, "High": [np.nan] * 3, "Low": [np.nan] * 3, "Close": [np.nan] * 3},
            index=dates,
        )
        with self.assertRaises(ValueError):
            backtest.fetch_etf_data(["QQQ"], "2024-01-01", "2024-01-04")


if __name__ == "__main__":
    unittest.main()
