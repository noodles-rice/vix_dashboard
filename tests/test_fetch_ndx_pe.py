#!/usr/bin/env python3
"""fetch_ndx_pe.py 的单元测试。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import fetch_ndx_pe


class TestIsValidNumber(unittest.TestCase):
    def test_valid_numbers(self):
        self.assertTrue(fetch_ndx_pe._is_valid_number(20.5))
        self.assertTrue(fetch_ndx_pe._is_valid_number("20.5"))
        self.assertTrue(fetch_ndx_pe._is_valid_number(0))
        self.assertTrue(fetch_ndx_pe._is_valid_number(-10))

    def test_invalid_numbers(self):
        self.assertFalse(fetch_ndx_pe._is_valid_number(None))
        self.assertFalse(fetch_ndx_pe._is_valid_number(float("nan")))
        self.assertFalse(fetch_ndx_pe._is_valid_number(float("inf")))
        self.assertFalse(fetch_ndx_pe._is_valid_number("not-a-number"))
        self.assertFalse(fetch_ndx_pe._is_valid_number(""))


class TestFetchNdxPeInfo(unittest.TestCase):
    def test_success(self):
        mock_info = {"forwardPE": None, "trailingPE": 30.0}
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = mock_info
            result = fetch_ndx_pe.fetch_ndx_pe_info()

        self.assertIsNotNone(result)
        self.assertIsNone(result["forward_pe"])
        self.assertEqual(result["trailing_pe"], 30.0)
        self.assertEqual(result["source"], "QQQ via Yahoo Finance")
        self.assertIn("as_of", result)
        self.assertIn("fetched_at", result)
        self.assertIn("note", result)

    def test_success_with_forward_pe(self):
        mock_info = {"forwardPE": 25.5, "trailingPE": 30.0}
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = mock_info
            result = fetch_ndx_pe.fetch_ndx_pe_info()

        self.assertIsNotNone(result)
        self.assertEqual(result["forward_pe"], 25.5)
        self.assertEqual(result["trailing_pe"], 30.0)

    def test_missing_trailing_pe(self):
        mock_info = {"forwardPE": 25.5}
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = mock_info
            result = fetch_ndx_pe.fetch_ndx_pe_info()

        self.assertIsNone(result)

    def test_invalid_trailing_pe(self):
        mock_info = {"forwardPE": 25.5, "trailingPE": float("nan")}
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = mock_info
            result = fetch_ndx_pe.fetch_ndx_pe_info()

        self.assertIsNone(result)

    def test_non_positive_trailing_pe(self):
        for value in (0, -1.0, "0", "-5"):
            mock_info = {"forwardPE": 25.5, "trailingPE": value}
            with patch("yfinance.Ticker") as MockTicker:
                MockTicker.return_value.info = mock_info
                result = fetch_ndx_pe.fetch_ndx_pe_info()
            self.assertIsNone(result, f"trailingPE={value!r} 应被视为无效")

    def test_fetch_exception(self):
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.side_effect = RuntimeError("connection timeout")
            result = fetch_ndx_pe.fetch_ndx_pe_info()

        self.assertIsNone(result)

    def test_missing_dependency(self):
        with patch.dict("sys.modules", {"yfinance": None}):
            result = fetch_ndx_pe.fetch_ndx_pe_info()
        self.assertIsNone(result)


class TestUpdateNdxPe(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.original_path = fetch_ndx_pe.NDX_PE_PATH
        self.original_history_path = fetch_ndx_pe.NDX_PE_HISTORY_PATH
        fetch_ndx_pe.NDX_PE_PATH = Path(self.tmpdir.name) / "ndx_pe.json"
        fetch_ndx_pe.NDX_PE_HISTORY_PATH = Path(self.tmpdir.name) / "ndx_pe_history.json"

    def tearDown(self):
        fetch_ndx_pe.NDX_PE_PATH = self.original_path
        fetch_ndx_pe.NDX_PE_HISTORY_PATH = self.original_history_path
        self.tmpdir.cleanup()

    def test_creates_file_when_fetch_succeeds(self):
        mock_info = {"forwardPE": None, "trailingPE": 30.0}
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = mock_info
            result = fetch_ndx_pe.update_ndx_pe()

        self.assertEqual(result["status"], "updated")
        self.assertTrue(fetch_ndx_pe.NDX_PE_PATH.exists())
        with open(fetch_ndx_pe.NDX_PE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIsNone(data["forward_pe"])
        self.assertEqual(data["trailing_pe"], 30.0)

    def test_preserves_existing_on_fetch_error(self):
        existing = {
            "forward_pe": None,
            "trailing_pe": 28.0,
            "source": "QQQ via Yahoo Finance",
            "as_of": "2024-01-01",
            "fetched_at": "2024-01-01T00:00:00+00:00",
        }
        with open(fetch_ndx_pe.NDX_PE_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f)

        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = {}
            result = fetch_ndx_pe.update_ndx_pe()

        self.assertEqual(result["status"], "fetch_error")
        self.assertEqual(result["data"]["trailing_pe"], 28.0)

    def test_no_write_when_unchanged(self):
        mock_info = {"forwardPE": None, "trailingPE": 30.0}
        # 第一次写入
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = mock_info
            first = fetch_ndx_pe.update_ndx_pe()

        self.assertEqual(first["status"], "updated")
        as_of = first["data"]["as_of"]
        mtime = fetch_ndx_pe.NDX_PE_PATH.stat().st_mtime

        # 相同数据再次更新
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = mock_info
            result = fetch_ndx_pe.update_ndx_pe()

        self.assertEqual(result["status"], "up_to_date")
        self.assertEqual(result["data"]["as_of"], as_of)
        self.assertEqual(fetch_ndx_pe.NDX_PE_PATH.stat().st_mtime, mtime)

    def test_updates_file_when_data_changes(self):
        # 先写入旧数据
        old_info = {"forwardPE": None, "trailingPE": 28.0}
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = old_info
            first = fetch_ndx_pe.update_ndx_pe()
        self.assertEqual(first["status"], "updated")
        old_mtime = fetch_ndx_pe.NDX_PE_PATH.stat().st_mtime

        # 再获取新数据
        new_info = {"forwardPE": None, "trailingPE": 30.0}
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = new_info
            result = fetch_ndx_pe.update_ndx_pe()

        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["data"]["trailing_pe"], 30.0)
        self.assertGreater(fetch_ndx_pe.NDX_PE_PATH.stat().st_mtime, old_mtime)
        with open(fetch_ndx_pe.NDX_PE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["trailing_pe"], 30.0)


class TestUpdateHistory(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.original_history_path = fetch_ndx_pe.NDX_PE_HISTORY_PATH
        fetch_ndx_pe.NDX_PE_HISTORY_PATH = Path(self.tmpdir.name) / "ndx_pe_history.json"

    def tearDown(self):
        fetch_ndx_pe.NDX_PE_HISTORY_PATH = self.original_history_path
        self.tmpdir.cleanup()

    def test_creates_history_file(self):
        fetched = {
            "forward_pe": None,
            "trailing_pe": 30.0,
            "source": "QQQ via Yahoo Finance",
            "as_of": "2024-01-15",
        }
        result = fetch_ndx_pe._update_history(fetched)

        self.assertTrue(fetch_ndx_pe.NDX_PE_HISTORY_PATH.exists())
        self.assertEqual(len(result["history"]), 1)
        self.assertEqual(result["history"][0]["as_of"], "2024-01-15")
        self.assertEqual(result["history"][0]["trailing_pe"], 30.0)
        self.assertIsNone(result["history"][0]["forward_pe"])

    def test_appends_new_dates(self):
        fetch_ndx_pe._update_history(
            {"as_of": "2024-01-15", "trailing_pe": 30.0, "forward_pe": None, "source": "QQQ"}
        )
        result = fetch_ndx_pe._update_history(
            {"as_of": "2024-01-16", "trailing_pe": 31.0, "forward_pe": 25.0, "source": "QQQ"}
        )

        self.assertEqual(len(result["history"]), 2)
        self.assertEqual(result["history"][0]["as_of"], "2024-01-15")
        self.assertEqual(result["history"][1]["as_of"], "2024-01-16")
        self.assertEqual(result["history"][1]["forward_pe"], 25.0)

    def test_dedupes_same_date(self):
        fetch_ndx_pe._update_history(
            {"as_of": "2024-01-15", "trailing_pe": 30.0, "forward_pe": None, "source": "QQQ"}
        )
        result = fetch_ndx_pe._update_history(
            {"as_of": "2024-01-15", "trailing_pe": 30.5, "forward_pe": None, "source": "QQQ"}
        )

        self.assertEqual(len(result["history"]), 1)
        self.assertEqual(result["history"][0]["trailing_pe"], 30.5)

    def test_sorts_history_by_date(self):
        fetch_ndx_pe._update_history(
            {"as_of": "2024-01-20", "trailing_pe": 31.0, "forward_pe": None, "source": "QQQ"}
        )
        fetch_ndx_pe._update_history(
            {"as_of": "2024-01-10", "trailing_pe": 29.0, "forward_pe": None, "source": "QQQ"}
        )
        result = fetch_ndx_pe._update_history(
            {"as_of": "2024-01-15", "trailing_pe": 30.0, "forward_pe": None, "source": "QQQ"}
        )

        dates = [entry["as_of"] for entry in result["history"]]
        self.assertEqual(dates, ["2024-01-10", "2024-01-15", "2024-01-20"])

    def test_load_history_handles_corrupt_file(self):
        with open(fetch_ndx_pe.NDX_PE_HISTORY_PATH, "w", encoding="utf-8") as f:
            f.write("not valid json")

        result = fetch_ndx_pe._load_history()
        self.assertEqual(result["history"], [])

    def test_update_history_in_update_ndx_pe(self):
        mock_info = {"forwardPE": None, "trailingPE": 30.0}
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = mock_info
            fetch_ndx_pe.update_ndx_pe()

        self.assertTrue(fetch_ndx_pe.NDX_PE_HISTORY_PATH.exists())
        with open(fetch_ndx_pe.NDX_PE_HISTORY_PATH, "r", encoding="utf-8") as f:
            history_data = json.load(f)
        self.assertEqual(len(history_data["history"]), 1)
        self.assertEqual(history_data["history"][0]["trailing_pe"], 30.0)


if __name__ == "__main__":
    unittest.main()
