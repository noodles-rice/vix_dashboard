#!/usr/bin/env python3
"""start.py 的单元测试。"""

import sys
import unittest
from io import StringIO
from unittest.mock import patch

import start


class TestParseCsvDate(unittest.TestCase):
    def test_valid_date(self):
        self.assertEqual(start.parse_csv_date("12/31/2020").isoformat(), "2020-12-31")

    def test_whitespace_trimmed(self):
        self.assertEqual(start.parse_csv_date(" 01/02/1990 ").isoformat(), "1990-01-02")

    def test_invalid_date_returns_none(self):
        self.assertIsNone(start.parse_csv_date("not-a-date"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(start.parse_csv_date(""))


class TestReadLastDateFromLines(unittest.TestCase):
    def test_valid_csv(self):
        lines = [
            "DATE,OPEN,HIGH,LOW,CLOSE\n",
            "01/02/1990,17.24,17.24,17.24,17.24\n",
            "12/31/2020,20.00,21.00,19.00,20.50\n",
        ]
        self.assertEqual(start.read_last_date_from_lines(lines).isoformat(), "2020-12-31")

    def test_missing_date_header(self):
        lines = ["FOO,BAR\n", "01/02/1990,1\n"]
        self.assertIsNone(start.read_last_date_from_lines(lines))

    def test_only_header(self):
        lines = ["DATE,CLOSE\n"]
        self.assertIsNone(start.read_last_date_from_lines(lines))

    def test_empty_lines_filtered(self):
        lines = [
            "DATE,CLOSE\n",
            "\n",
            "\n",
            "01/02/1990,17.24\n",
        ]
        self.assertEqual(start.read_last_date_from_lines(lines).isoformat(), "1990-01-02")

    def test_invalid_dates_skipped(self):
        lines = [
            "DATE,CLOSE\n",
            "bad-date,17.24\n",
            "01/03/1990,18.00\n",
        ]
        self.assertEqual(start.read_last_date_from_lines(lines).isoformat(), "1990-01-03")


class TestMainPortValidation(unittest.TestCase):
    def _run_main_with_argv(self, argv):
        with patch.object(sys, "argv", argv):
            with patch.object(start, "update_vix_data", return_value={"status": "up_to_date"}):
                with patch.object(start, "write_update_info"):
                    with patch.object(start, "run_server") as mock_run:
                        start.main()
                        return mock_run.call_args[0][0]

    def test_default_port(self):
        port = self._run_main_with_argv(["start.py"])
        self.assertEqual(port, 8080)

    def test_valid_port(self):
        port = self._run_main_with_argv(["start.py", "9000"])
        self.assertEqual(port, 9000)

    def test_invalid_string_uses_default(self):
        port = self._run_main_with_argv(["start.py", "abc"])
        self.assertEqual(port, 8080)

    def test_port_too_high_uses_default(self):
        port = self._run_main_with_argv(["start.py", "70000"])
        self.assertEqual(port, 8080)

    def test_port_zero_uses_default(self):
        port = self._run_main_with_argv(["start.py", "0"])
        self.assertEqual(port, 8080)


if __name__ == "__main__":
    unittest.main()
