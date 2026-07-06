#!/usr/bin/env python3
"""start.py 的单元测试。"""

import socketserver
import sys
import tempfile
import threading
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
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


class TestServerRoot(unittest.TestCase):
    """验证 HTTP 服务始终以项目根目录作为静态资源根。"""

    def test_handler_serves_from_project_root(self):
        original_base_dir = start.BASE_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            (tmpdir_path / "index.html").write_text("<html>test</html>", encoding="utf-8")
            (tmpdir_path / "assets").mkdir()
            (tmpdir_path / "assets" / "dashboard.js").write_text("// js", encoding="utf-8")

            start.BASE_DIR = tmpdir_path
            try:
                server = socketserver.TCPServer(("127.0.0.1", 0), start.CORSRequestHandler)
                port = server.server_address[1]
                thread = threading.Thread(target=server.serve_forever)
                thread.daemon = True
                thread.start()

                try:
                    with urlopen(f"http://127.0.0.1:{port}/index.html") as resp:
                        self.assertEqual(resp.read().decode("utf-8"), "<html>test</html>")
                    with urlopen(f"http://127.0.0.1:{port}/assets/dashboard.js") as resp:
                        self.assertEqual(resp.read().decode("utf-8"), "// js")
                finally:
                    server.shutdown()
                    server.server_close()
            finally:
                start.BASE_DIR = original_base_dir


if __name__ == "__main__":
    unittest.main()
