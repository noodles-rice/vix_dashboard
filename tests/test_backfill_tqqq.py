#!/usr/bin/env python3
"""backfill_tqqq.py 的单元测试。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import backfill_tqqq as bf


class TestComputeRegression(unittest.TestCase):
    def test_perfect_linear_relation(self):
        """理想线性关系下应准确还原 alpha 与 beta。"""
        dates = pd.date_range("2020-01-01", periods=40)
        qqq = pd.DataFrame(
            {
                "OPEN": [100.0] * 40,
                "HIGH": [101.0] * 40,
                "LOW": [99.0] * 40,
                "CLOSE": [100.0 + i * 0.5 for i in range(40)],
            },
            index=dates,
        )
        # TQQQ 收益 = 0.0001 + 3 * QQQ 收益
        qqq_ret = qqq["CLOSE"].pct_change()
        tqqq_close = 10.0 * (1 + 0.0001 + 3 * qqq_ret).cumprod()
        tqqq = pd.DataFrame(
            {
                "OPEN": tqqq_close,
                "HIGH": tqqq_close * 1.01,
                "LOW": tqqq_close * 0.99,
                "CLOSE": tqqq_close,
            },
            index=dates,
        )
        alpha, beta, r2 = bf.compute_regression(qqq, tqqq)
        self.assertAlmostEqual(alpha, 0.0001, places=5)
        self.assertAlmostEqual(beta, 3.0, places=5)
        self.assertAlmostEqual(r2, 1.0, places=5)

    def test_overlap_too_short_raises(self):
        qqq = pd.DataFrame(
            {"OPEN": [1.0], "HIGH": [1.0], "LOW": [1.0], "CLOSE": [1.0]},
            index=pd.to_datetime(["2020-01-01"]),
        )
        tqqq = qqq.copy()
        with self.assertRaises(ValueError) as ctx:
            bf.compute_regression(qqq, tqqq)
        self.assertIn("重叠交易日不足", str(ctx.exception))


class TestBackfillOhlc(unittest.TestCase):
    def test_basic_backfill(self):
        """验证回填生成的 OHLC 与锚点连续，且 beta 近似 3。"""
        # 构造 2006-06-21 至 2010-02-11 的 QQQ 数据
        all_dates = pd.date_range("2006-06-21", "2010-02-11")
        # 仅用交易日（假设每天都有数据，简化测试）
        qqq_close = pd.Series(100.0, index=all_dates)
        # 让 QQQ 每天微涨 0.1%
        for i in range(1, len(qqq_close)):
            qqq_close.iloc[i] = qqq_close.iloc[i - 1] * 1.001

        qqq = pd.DataFrame(
            {
                "OPEN": qqq_close * 0.999,
                "HIGH": qqq_close * 1.002,
                "LOW": qqq_close * 0.998,
                "CLOSE": qqq_close,
            },
            index=all_dates,
        )

        # TQQQ 真实数据仅 2010-02-11
        tqqq_dates = pd.to_datetime(["2010-02-11"])
        tqqq = pd.DataFrame(
            {
                "OPEN": [0.205],
                "HIGH": [0.210],
                "LOW": [0.200],
                "CLOSE": [0.205],
            },
            index=tqqq_dates,
        )

        alpha = 0.0
        beta = 3.0
        synthetic = bf.backfill_ohlc(qqq, tqqq, alpha, beta)

        # 回填应覆盖 2006-06-21 至 2010-02-10
        self.assertEqual(synthetic.index[0], pd.Timestamp("2006-06-21"))
        self.assertEqual(synthetic.index[-1], pd.Timestamp("2010-02-10"))

        # 2010-02-10 的 close 乘以 (1 + 3 * qqq_ret) 应等于 2010-02-11 的 0.205
        qqq_ret_20100211 = qqq.loc["2010-02-11", "CLOSE"] / qqq.loc["2010-02-10", "CLOSE"] - 1
        expected_prev_close = 0.205 / (1 + alpha + beta * qqq_ret_20100211)
        self.assertAlmostEqual(synthetic.loc["2010-02-10", "CLOSE"], expected_prev_close, places=6)

        # 检查 OHLC 基本约束
        for date, row in synthetic.iterrows():
            self.assertGreaterEqual(row["HIGH"], max(row["OPEN"], row["CLOSE"]))
            self.assertLessEqual(row["LOW"], min(row["OPEN"], row["CLOSE"]))

    def test_high_low_scaled_relative_to_previous_close(self):
        """QQQ 大涨但日内高点仅略高于收盘价时，TQQQ 高点仍应高于收盘价。

        旧实现以 QQQ 当日 CLOSE 为基准缩放 H/L，会错误地把 TQQQ HIGH
        截断到 CLOSE（因为 CLOSE 相对前收的涨幅大于 HIGH 相对当日 CLOSE
        的涨幅）。本回归测试确保 H/L 与 O/C 使用一致的上一日 CLOSE 基准。
        """
        dates = pd.to_datetime(["2010-02-09", "2010-02-10", "2010-02-11"])
        qqq = pd.DataFrame(
            {
                "OPEN": [99.0, 101.0, 104.0],
                "HIGH": [100.5, 102.5, 105.0],
                "LOW": [98.5, 99.5, 103.0],
                "CLOSE": [100.0, 102.0, 104.0],
            },
            index=dates,
        )
        tqqq = pd.DataFrame(
            {
                "OPEN": [0.30],
                "HIGH": [0.32],
                "LOW": [0.29],
                "CLOSE": [0.31],
            },
            index=pd.to_datetime(["2010-02-11"]),
        )

        alpha = 0.0
        beta = 3.0
        synthetic = bf.backfill_ohlc(qqq, tqqq, alpha, beta)

        day1 = synthetic.loc[pd.Timestamp("2010-02-10")]
        # QQQ 当日涨 2%，TQQQ close 相对 open 应涨约 6%
        self.assertGreater(day1["CLOSE"], day1["OPEN"])
        # QQQ high 相对前收涨 2.5%，TQQQ high 应高于 close
        self.assertGreater(day1["HIGH"], day1["CLOSE"])
        # QQQ low 低于前收，TQQQ low 应低于 open
        self.assertLess(day1["LOW"], day1["OPEN"])


class TestMergeAndSave(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmpdir.name)
        self.tqqq_csv = self.data_dir / "TQQQ_History.csv"

        patcher = patch.object(bf, "DATA_DIR", self.data_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

        patcher_csv = patch.object(bf, "TQQQ_CSV", self.tqqq_csv)
        patcher_csv.start()
        self.addCleanup(patcher_csv.stop)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_merge_preserves_real_data(self):
        synthetic = pd.DataFrame(
            {
                "OPEN": [0.1, 0.11],
                "HIGH": [0.11, 0.12],
                "LOW": [0.09, 0.10],
                "CLOSE": [0.105, 0.115],
            },
            index=pd.to_datetime(["2009-12-30", "2009-12-31"]),
        )
        real = pd.DataFrame(
            {
                "OPEN": [0.2],
                "HIGH": [0.21],
                "LOW": [0.19],
                "CLOSE": [0.205],
            },
            index=pd.to_datetime(["2010-02-11"]),
        )
        bf.merge_and_save(synthetic, real)

        self.assertTrue(self.tqqq_csv.exists())
        df = pd.read_csv(self.tqqq_csv)
        self.assertEqual(len(df), 3)
        self.assertEqual(df.iloc[0]["DATE"], "12/30/2009")
        self.assertEqual(df.iloc[-1]["DATE"], "02/11/2010")

    def test_merge_is_idempotent(self):
        synthetic = pd.DataFrame(
            {
                "OPEN": [0.1],
                "HIGH": [0.11],
                "LOW": [0.09],
                "CLOSE": [0.105],
            },
            index=pd.to_datetime(["2009-12-31"]),
        )
        real = pd.DataFrame(
            {
                "OPEN": [0.2],
                "HIGH": [0.21],
                "LOW": [0.19],
                "CLOSE": [0.205],
            },
            index=pd.to_datetime(["2010-02-11"]),
        )
        bf.merge_and_save(synthetic, real)
        first_content = self.tqqq_csv.read_text(encoding="utf-8")

        bf.merge_and_save(synthetic, real)
        second_content = self.tqqq_csv.read_text(encoding="utf-8")

        self.assertEqual(first_content, second_content)


class TestMetadata(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmpdir.name)
        self.metadata_file = self.data_dir / "etf_metadata.json"

        patcher = patch.object(bf, "DATA_DIR", self.data_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

        patcher_meta = patch.object(bf, "METADATA_FILE", self.metadata_file)
        patcher_meta.start()
        self.addCleanup(patcher_meta.stop)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_update_metadata(self):
        bf.update_metadata(alpha=0.0001, beta=2.95, r_squared=0.98)

        self.assertTrue(self.metadata_file.exists())
        with open(self.metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        self.assertTrue(metadata["TQQQ"]["backfilled"])
        self.assertEqual(metadata["TQQQ"]["backfill_start"], "2006-06-21")
        self.assertEqual(metadata["TQQQ"]["backfill_end"], "2010-02-10")
        self.assertAlmostEqual(metadata["TQQQ"]["regression_alpha"], 0.0001)
        self.assertAlmostEqual(metadata["TQQQ"]["regression_beta"], 2.95)
        self.assertAlmostEqual(metadata["TQQQ"]["regression_r2"], 0.98)
        self.assertIn("backfill_note", metadata["TQQQ"])


class TestLoadEtf(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmpdir.name)

        patcher = patch.object(bf, "DATA_DIR", self.data_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_load_etf_normal(self):
        path = self.data_dir / "QQQ_History.csv"
        pd.DataFrame(
            {
                "DATE": ["01/03/2006", "01/04/2006"],
                "OPEN": [100.0, 101.0],
                "HIGH": [102.0, 103.0],
                "LOW": [99.0, 100.0],
                "CLOSE": [101.0, 102.0],
            }
        ).to_csv(path, index=False)

        df = bf.load_etf("QQQ")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.index[0], pd.Timestamp("2006-01-03"))

    def test_load_etf_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            bf.load_etf("QQQ")

    def test_load_etf_missing_columns(self):
        path = self.data_dir / "QQQ_History.csv"
        pd.DataFrame({"DATE": ["01/03/2006"], "CLOSE": [100.0]}).to_csv(path, index=False)
        with self.assertRaises(ValueError) as ctx:
            bf.load_etf("QQQ")
        self.assertIn("缺少必要列", str(ctx.exception))


class TestMain(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmpdir.name)
        self.tqqq_csv = self.data_dir / "TQQQ_History.csv"
        self.metadata_file = self.data_dir / "etf_metadata.json"

        patcher = patch.object(bf, "DATA_DIR", self.data_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

        patcher_csv = patch.object(bf, "TQQQ_CSV", self.tqqq_csv)
        patcher_csv.start()
        self.addCleanup(patcher_csv.stop)

        patcher_meta = patch.object(bf, "METADATA_FILE", self.metadata_file)
        patcher_meta.start()
        self.addCleanup(patcher_meta.stop)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_main_end_to_end(self):
        """构造完整 QQQ/TQQQ CSV，验证 main() 能完成回填、写入文件与元数据。"""
        dates = pd.date_range("2006-06-21", "2010-04-30")
        qqq_close = pd.Series(100.0, index=dates)
        for i in range(1, len(qqq_close)):
            # 使用变化的日收益，避免回归时因常数序列导致 RankWarning 与不稳定系数
            daily_ret = 0.0005 + 0.001 * (i % 2)
            qqq_close.iloc[i] = qqq_close.iloc[i - 1] * (1 + daily_ret)
        qqq_ret = qqq_close.pct_change()

        qqq = pd.DataFrame(
            {
                "DATE": qqq_close.index.strftime("%m/%d/%Y"),
                "OPEN": qqq_close * 0.999,
                "HIGH": qqq_close * 1.002,
                "LOW": qqq_close * 0.998,
                "CLOSE": qqq_close,
            }
        )
        qqq.to_csv(self.data_dir / "QQQ_History.csv", index=False)

        tqqq_dates = dates[dates >= pd.Timestamp("2010-02-11")]
        tqqq_close = pd.Series(0.205, index=tqqq_dates)
        for i in range(1, len(tqqq_close)):
            date = tqqq_close.index[i]
            tqqq_close.iloc[i] = tqqq_close.iloc[i - 1] * (
                1 + 0.0001 + 3 * qqq_ret.loc[date]
            )
        tqqq = pd.DataFrame(
            {
                "DATE": tqqq_close.index.strftime("%m/%d/%Y"),
                "OPEN": tqqq_close,
                "HIGH": tqqq_close * 1.01,
                "LOW": tqqq_close * 0.99,
                "CLOSE": tqqq_close,
            }
        )
        tqqq.to_csv(self.data_dir / "TQQQ_History.csv", index=False)

        ret = bf.main()
        self.assertEqual(ret, 0)

        self.assertTrue(self.tqqq_csv.exists())
        combined = pd.read_csv(self.tqqq_csv)
        self.assertEqual(combined.iloc[0]["DATE"], "06/21/2006")
        self.assertEqual(combined.iloc[-1]["DATE"], tqqq_close.index[-1].strftime("%m/%d/%Y"))

        self.assertTrue(self.metadata_file.exists())
        with open(self.metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)
        self.assertTrue(metadata["TQQQ"]["backfilled"])
        self.assertEqual(metadata["TQQQ"]["backfill_start"], "2006-06-21")
        self.assertEqual(metadata["TQQQ"]["backfill_end"], "2010-02-10")


if __name__ == "__main__":
    unittest.main()
