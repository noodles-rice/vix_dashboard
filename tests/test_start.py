#!/usr/bin/env python3
"""start.py 的单元测试。"""

import json
import socketserver
import sys
import tempfile
import threading
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pandas as pd

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
                    with patch.object(start, "update_ndx_data", return_value={"status": "up_to_date"}):
                        with patch.object(start, "write_ndx_update_info"):
                            with patch.object(start, "update_spx_data", return_value={"status": "up_to_date"}):
                                with patch.object(start, "write_spx_update_info"):
                                    with patch.object(start, "update_ndx_pe_data", return_value={"status": "up_to_date"}):
                                        with patch.object(start, "write_ndx_pe_update_info"):
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


class TestTradingJournalEndpoints(unittest.TestCase):
    """验证交易日志 POST / OPTIONS 端点。"""

    def _start_server(self):
        server = socketserver.TCPServer(("127.0.0.1", 0), start.CORSRequestHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        return server, port

    def _make_payload(self, records=None):
        return json.dumps({"records": records or []}, ensure_ascii=False).encode("utf-8")

    def _post(self, port, path, body):
        req = Request(f"http://127.0.0.1:{port}{path}", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        return urlopen(req)

    def test_options_returns_204_with_cors_headers(self):
        original_base_dir = start.BASE_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            start.BASE_DIR = Path(tmpdir)
            (Path(tmpdir) / "index.html").write_text("<html></html>", encoding="utf-8")
            try:
                server, port = self._start_server()
                try:
                    req = Request(
                        f"http://127.0.0.1:{port}/data/trading_journal.json",
                        method="OPTIONS",
                    )
                    with urlopen(req) as resp:
                        self.assertEqual(resp.status, 204)
                        self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
                        self.assertIn("POST", resp.headers.get("Access-Control-Allow-Methods", ""))
                        self.assertIn("Content-Type", resp.headers.get("Access-Control-Allow-Headers", ""))
                finally:
                    server.shutdown()
                    server.server_close()
            finally:
                start.BASE_DIR = original_base_dir

    def test_post_saves_journal(self):
        original_base_dir = start.BASE_DIR
        original_journal_path = start.TRADING_JOURNAL_PATH
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            start.BASE_DIR = tmpdir_path
            start.TRADING_JOURNAL_PATH = str(tmpdir_path / "trading_journal.json")
            (tmpdir_path / "index.html").write_text("<html></html>", encoding="utf-8")
            try:
                server, port = self._start_server()
                try:
                    records = [{"date": "2026-07-14", "action": "买入", "stockName": "TQQQ"}]
                    body = self._make_payload(records)
                    with self._post(port, "/data/trading_journal.json", body) as resp:
                        self.assertEqual(resp.status, 200)
                        response = json.loads(resp.read().decode("utf-8"))
                        self.assertTrue(response["ok"])

                    saved = json.loads(Path(start.TRADING_JOURNAL_PATH).read_text(encoding="utf-8"))
                    self.assertEqual(saved["records"], records)
                finally:
                    server.shutdown()
                    server.server_close()
            finally:
                start.BASE_DIR = original_base_dir
                start.TRADING_JOURNAL_PATH = original_journal_path

    def test_post_wrong_path_returns_404(self):
        original_base_dir = start.BASE_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            start.BASE_DIR = Path(tmpdir)
            (Path(tmpdir) / "index.html").write_text("<html></html>", encoding="utf-8")
            try:
                server, port = self._start_server()
                try:
                    with self.assertRaises(HTTPError) as ctx:
                        self._post(port, "/data/other.json", self._make_payload())
                    self.assertEqual(ctx.exception.code, 404)
                finally:
                    server.shutdown()
                    server.server_close()
            finally:
                start.BASE_DIR = original_base_dir

    def test_post_invalid_json_returns_400(self):
        original_base_dir = start.BASE_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            start.BASE_DIR = Path(tmpdir)
            (Path(tmpdir) / "index.html").write_text("<html></html>", encoding="utf-8")
            try:
                server, port = self._start_server()
                try:
                    body = b"not-json"
                    with self.assertRaises(HTTPError) as ctx:
                        self._post(port, "/data/trading_journal.json", body)
                    self.assertEqual(ctx.exception.code, 400)
                finally:
                    server.shutdown()
                    server.server_close()
            finally:
                start.BASE_DIR = original_base_dir

    def test_post_invalid_structure_returns_400(self):
        original_base_dir = start.BASE_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            start.BASE_DIR = Path(tmpdir)
            (Path(tmpdir) / "index.html").write_text("<html></html>", encoding="utf-8")
            try:
                server, port = self._start_server()
                try:
                    body = json.dumps({"records": [{"badField": "x"}]}).encode("utf-8")
                    with self.assertRaises(HTTPError) as ctx:
                        self._post(port, "/data/trading_journal.json", body)
                    self.assertEqual(ctx.exception.code, 400)
                finally:
                    server.shutdown()
                    server.server_close()
            finally:
                start.BASE_DIR = original_base_dir

    def test_post_non_string_value_returns_400(self):
        original_base_dir = start.BASE_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            start.BASE_DIR = Path(tmpdir)
            (Path(tmpdir) / "index.html").write_text("<html></html>", encoding="utf-8")
            try:
                server, port = self._start_server()
                try:
                    body = json.dumps({"records": [{"date": 2026}]}).encode("utf-8")
                    with self.assertRaises(HTTPError) as ctx:
                        self._post(port, "/data/trading_journal.json", body)
                    self.assertEqual(ctx.exception.code, 400)
                finally:
                    server.shutdown()
                    server.server_close()
            finally:
                start.BASE_DIR = original_base_dir

    def test_post_oversized_body_returns_413(self):
        original_base_dir = start.BASE_DIR
        original_max_size = start.MAX_JOURNAL_SIZE
        with tempfile.TemporaryDirectory() as tmpdir:
            start.BASE_DIR = Path(tmpdir)
            # 临时调低阈值，避免上传 1 MB 数据导致客户端 Broken pipe
            start.MAX_JOURNAL_SIZE = 100
            (Path(tmpdir) / "index.html").write_text("<html></html>", encoding="utf-8")
            try:
                server, port = self._start_server()
                try:
                    huge = "x" * 200
                    body = json.dumps({"records": [{"notes": huge}]}).encode("utf-8")
                    with self.assertRaises(HTTPError) as ctx:
                        self._post(port, "/data/trading_journal.json", body)
                    self.assertEqual(ctx.exception.code, 413)
                finally:
                    server.shutdown()
                    server.server_close()
            finally:
                start.BASE_DIR = original_base_dir
                start.MAX_JOURNAL_SIZE = original_max_size


class TestUpdateSpxData(unittest.TestCase):
    def _make_spx_df(self):
        dates = pd.to_datetime(
            ["2024-01-02", "2024-01-03", "2024-01-04"]
        ).tz_localize("America/New_York")
        return pd.DataFrame(
            {
                "Open": [4000.0, 4010.0, 4020.0],
                "High": [4010.0, 4020.0, 4030.0],
                "Low": [3990.0, 4000.0, 4010.0],
                "Close": [4005.0, 4015.0, 4025.0],
            },
            index=dates,
        )

    def _write_csv(self, path, lines):
        path.write_text("\n".join(lines), encoding="utf-8")

    def test_updates_csv_when_remote_newer(self):
        df = self._make_spx_df()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            spx_csv = tmpdir_path / "SPX_History.csv"
            spx_info = tmpdir_path / "spx_last_update.json"
            self._write_csv(
                spx_csv,
                [
                    "DATE,OPEN,HIGH,LOW,CLOSE",
                    "01/02/2024,4000.00,4010.00,3990.00,4005.00",
                ],
            )

            original_csv = start.LOCAL_SPX_CSV
            original_info = start.SPX_UPDATE_INFO
            try:
                start.LOCAL_SPX_CSV = str(spx_csv)
                start.SPX_UPDATE_INFO = str(spx_info)
                with patch.object(start, "fetch_spx_history", return_value=df):
                    info = start.update_spx_data()
            finally:
                start.LOCAL_SPX_CSV = original_csv
                start.SPX_UPDATE_INFO = original_info

            self.assertEqual(info["status"], "updated")
            self.assertEqual(info["latestDate"], "2024-01-04")
            self.assertEqual(info["addedRows"], 2)
            content = spx_csv.read_text(encoding="utf-8").splitlines()
            self.assertEqual(content[0], "DATE,OPEN,HIGH,LOW,CLOSE")
            self.assertEqual(content[1], "01/02/2024,4000.00,4010.00,3990.00,4005.00")
            self.assertEqual(content[-1], "01/04/2024,4020.0,4030.0,4010.0,4025.0")

    def test_up_to_date_when_remote_same(self):
        df = self._make_spx_df()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            spx_csv = tmpdir_path / "SPX_History.csv"
            spx_info = tmpdir_path / "spx_last_update.json"
            self._write_csv(
                spx_csv,
                [
                    "DATE,OPEN,HIGH,LOW,CLOSE",
                    "01/02/2024,4000.00,4010.00,3990.00,4005.00",
                    "01/03/2024,4010.00,4020.00,4000.00,4015.00",
                    "01/04/2024,4020.00,4030.00,4010.00,4025.00",
                ],
            )

            original_csv = start.LOCAL_SPX_CSV
            original_info = start.SPX_UPDATE_INFO
            try:
                start.LOCAL_SPX_CSV = str(spx_csv)
                start.SPX_UPDATE_INFO = str(spx_info)
                with patch.object(start, "fetch_spx_history", return_value=df):
                    info = start.update_spx_data()
            finally:
                start.LOCAL_SPX_CSV = original_csv
                start.SPX_UPDATE_INFO = original_info

            self.assertEqual(info["status"], "up_to_date")
            self.assertEqual(info["addedRows"], 0)

    def test_missing_dependency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            spx_csv = tmpdir_path / "SPX_History.csv"
            spx_info = tmpdir_path / "spx_last_update.json"

            original_csv = start.LOCAL_SPX_CSV
            original_info = start.SPX_UPDATE_INFO
            try:
                start.LOCAL_SPX_CSV = str(spx_csv)
                start.SPX_UPDATE_INFO = str(spx_info)
                with patch.object(
                    start, "fetch_spx_history", side_effect=ImportError("No module named yfinance")
                ):
                    info = start.update_spx_data()
            finally:
                start.LOCAL_SPX_CSV = original_csv
                start.SPX_UPDATE_INFO = original_info

            self.assertEqual(info["status"], "missing_dependency")

    def test_fetch_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            spx_csv = tmpdir_path / "SPX_History.csv"
            spx_info = tmpdir_path / "spx_last_update.json"
            self._write_csv(
                spx_csv,
                [
                    "DATE,OPEN,HIGH,LOW,CLOSE",
                    "01/04/2024,4020.00,4030.00,4010.00,4025.00",
                ],
            )

            original_csv = start.LOCAL_SPX_CSV
            original_info = start.SPX_UPDATE_INFO
            try:
                start.LOCAL_SPX_CSV = str(spx_csv)
                start.SPX_UPDATE_INFO = str(spx_info)
                with patch.object(
                    start, "fetch_spx_history", side_effect=RuntimeError("connection timeout")
                ):
                    info = start.update_spx_data()
            finally:
                start.LOCAL_SPX_CSV = original_csv
                start.SPX_UPDATE_INFO = original_info

            self.assertEqual(info["status"], "fetch_error")
            self.assertEqual(info["previousLatestDate"], "2024-01-04")

    def test_up_to_date_when_remote_empty(self):
        """增量拉取返回空 DataFrame 时，应视为已是最新而非获取失败。"""
        empty_df = pd.DataFrame(
            {"Open": [], "High": [], "Low": [], "Close": []},
            index=pd.DatetimeIndex([], tz="America/New_York"),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            spx_csv = tmpdir_path / "SPX_History.csv"
            spx_info = tmpdir_path / "spx_last_update.json"
            self._write_csv(
                spx_csv,
                [
                    "DATE,OPEN,HIGH,LOW,CLOSE",
                    "01/04/2024,4020.00,4030.00,4010.00,4025.00",
                ],
            )

            original_csv = start.LOCAL_SPX_CSV
            original_info = start.SPX_UPDATE_INFO
            try:
                start.LOCAL_SPX_CSV = str(spx_csv)
                start.SPX_UPDATE_INFO = str(spx_info)
                with patch.object(start, "fetch_spx_history", return_value=empty_df):
                    info = start.update_spx_data()
            finally:
                start.LOCAL_SPX_CSV = original_csv
                start.SPX_UPDATE_INFO = original_info

            self.assertEqual(info["status"], "up_to_date")
            self.assertEqual(info["latestDate"], "2024-01-04")
            self.assertEqual(info["addedRows"], 0)


class TestUpdateNdxPeData(unittest.TestCase):
    def test_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            pe_info = tmpdir_path / "ndx_pe_last_update.json"

            original_info = start.NDX_PE_UPDATE_INFO
            try:
                start.NDX_PE_UPDATE_INFO = str(pe_info)
                with patch.object(
                    start.fetch_ndx_pe, "update_ndx_pe", return_value={"status": "updated", "data": {"forward_pe": 25.5}}
                ):
                    info = start.update_ndx_pe_data()
                    start.write_ndx_pe_update_info(info)
            finally:
                start.NDX_PE_UPDATE_INFO = original_info

            self.assertEqual(info["status"], "updated")
            self.assertTrue(pe_info.exists())
            with open(pe_info, "r", encoding="utf-8") as f:
                record = json.load(f)
            self.assertIn("updatedAt", record)

    def test_fetch_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            pe_info = tmpdir_path / "ndx_pe_last_update.json"

            original_info = start.NDX_PE_UPDATE_INFO
            try:
                start.NDX_PE_UPDATE_INFO = str(pe_info)
                with patch.object(
                    start.fetch_ndx_pe, "update_ndx_pe", side_effect=RuntimeError("network error")
                ):
                    info = start.update_ndx_pe_data()
            finally:
                start.NDX_PE_UPDATE_INFO = original_info

            self.assertEqual(info["status"], "fetch_error")
            self.assertEqual(info["message"], "network error")


class TestUpdateNdxData(unittest.TestCase):
    def _make_ndx_df(self):
        dates = pd.to_datetime(
            ["2024-01-02", "2024-01-03", "2024-01-04"]
        ).tz_localize("America/New_York")
        return pd.DataFrame(
            {
                "Open": [100.0, 101.0, 102.0],
                "High": [101.0, 102.0, 103.0],
                "Low": [99.0, 100.0, 101.0],
                "Close": [100.5, 101.5, 102.5],
            },
            index=dates,
        )

    def _write_csv(self, path, lines):
        path.write_text("\n".join(lines), encoding="utf-8")

    def test_updates_csv_when_remote_newer(self):
        df = self._make_ndx_df()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            ndx_csv = tmpdir_path / "NDX_History.csv"
            ndx_info = tmpdir_path / "ndx_last_update.json"
            # 本地数据比远程旧一天
            self._write_csv(
                ndx_csv,
                [
                    "DATE,OPEN,HIGH,LOW,CLOSE",
                    "01/02/2024,100.00,101.00,99.00,100.50",
                ],
            )

            original_csv = start.LOCAL_NDX_CSV
            original_info = start.NDX_UPDATE_INFO
            try:
                start.LOCAL_NDX_CSV = str(ndx_csv)
                start.NDX_UPDATE_INFO = str(ndx_info)
                with patch.object(start, "fetch_ndx_history", return_value=df):
                    info = start.update_ndx_data()
            finally:
                start.LOCAL_NDX_CSV = original_csv
                start.NDX_UPDATE_INFO = original_info

            self.assertEqual(info["status"], "updated")
            self.assertEqual(info["latestDate"], "2024-01-04")
            self.assertEqual(info["addedRows"], 2)
            content = ndx_csv.read_text(encoding="utf-8").splitlines()
            self.assertEqual(content[0], "DATE,OPEN,HIGH,LOW,CLOSE")
            # 增量追加保留本地已有行格式，仅新增行使用 pandas 默认格式
            self.assertEqual(content[1], "01/02/2024,100.00,101.00,99.00,100.50")
            self.assertEqual(content[-1], "01/04/2024,102.0,103.0,101.0,102.5")

    def test_up_to_date_when_remote_same(self):
        df = self._make_ndx_df()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            ndx_csv = tmpdir_path / "NDX_History.csv"
            ndx_info = tmpdir_path / "ndx_last_update.json"
            self._write_csv(
                ndx_csv,
                [
                    "DATE,OPEN,HIGH,LOW,CLOSE",
                    "01/02/2024,100.00,101.00,99.00,100.50",
                    "01/03/2024,101.00,102.00,100.00,101.50",
                    "01/04/2024,102.00,103.00,101.00,102.50",
                ],
            )

            original_csv = start.LOCAL_NDX_CSV
            original_info = start.NDX_UPDATE_INFO
            try:
                start.LOCAL_NDX_CSV = str(ndx_csv)
                start.NDX_UPDATE_INFO = str(ndx_info)
                with patch.object(start, "fetch_ndx_history", return_value=df):
                    info = start.update_ndx_data()
            finally:
                start.LOCAL_NDX_CSV = original_csv
                start.NDX_UPDATE_INFO = original_info

            self.assertEqual(info["status"], "up_to_date")
            self.assertEqual(info["addedRows"], 0)

    def test_up_to_date_when_remote_empty(self):
        """增量拉取返回空 DataFrame 时，应视为已是最新而非获取失败。"""
        empty_df = pd.DataFrame(
            {"Open": [], "High": [], "Low": [], "Close": []},
            index=pd.DatetimeIndex([], tz="America/New_York"),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            ndx_csv = tmpdir_path / "NDX_History.csv"
            ndx_info = tmpdir_path / "ndx_last_update.json"
            self._write_csv(
                ndx_csv,
                [
                    "DATE,OPEN,HIGH,LOW,CLOSE",
                    "01/04/2024,102.00,103.00,101.00,102.50",
                ],
            )

            original_csv = start.LOCAL_NDX_CSV
            original_info = start.NDX_UPDATE_INFO
            try:
                start.LOCAL_NDX_CSV = str(ndx_csv)
                start.NDX_UPDATE_INFO = str(ndx_info)
                with patch.object(start, "fetch_ndx_history", return_value=empty_df):
                    info = start.update_ndx_data()
            finally:
                start.LOCAL_NDX_CSV = original_csv
                start.NDX_UPDATE_INFO = original_info

            self.assertEqual(info["status"], "up_to_date")
            self.assertEqual(info["latestDate"], "2024-01-04")
            self.assertEqual(info["addedRows"], 0)

    def test_missing_dependency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            ndx_csv = tmpdir_path / "NDX_History.csv"
            ndx_info = tmpdir_path / "ndx_last_update.json"

            original_csv = start.LOCAL_NDX_CSV
            original_info = start.NDX_UPDATE_INFO
            try:
                start.LOCAL_NDX_CSV = str(ndx_csv)
                start.NDX_UPDATE_INFO = str(ndx_info)
                with patch.object(
                    start, "fetch_ndx_history", side_effect=ImportError("No module named yfinance")
                ):
                    info = start.update_ndx_data()
            finally:
                start.LOCAL_NDX_CSV = original_csv
                start.NDX_UPDATE_INFO = original_info

            self.assertEqual(info["status"], "missing_dependency")

    def test_fetch_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            ndx_csv = tmpdir_path / "NDX_History.csv"
            ndx_info = tmpdir_path / "ndx_last_update.json"
            self._write_csv(
                ndx_csv,
                [
                    "DATE,OPEN,HIGH,LOW,CLOSE",
                    "01/04/2024,102.00,103.00,101.00,102.50",
                ],
            )

            original_csv = start.LOCAL_NDX_CSV
            original_info = start.NDX_UPDATE_INFO
            try:
                start.LOCAL_NDX_CSV = str(ndx_csv)
                start.NDX_UPDATE_INFO = str(ndx_info)
                with patch.object(
                    start, "fetch_ndx_history", side_effect=RuntimeError("connection timeout")
                ):
                    info = start.update_ndx_data()
            finally:
                start.LOCAL_NDX_CSV = original_csv
                start.NDX_UPDATE_INFO = original_info

            self.assertEqual(info["status"], "fetch_error")
            self.assertEqual(info["previousLatestDate"], "2024-01-04")


if __name__ == "__main__":
    unittest.main()
