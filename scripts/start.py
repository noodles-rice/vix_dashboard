#!/usr/bin/env python3
"""VIX 数据看板启动脚本。

启动本地 HTTP 服务前，先从 CBOE 官方源拉取最新 VIX_History.csv，
与本地文件对比后决定是否更新，并记录更新时间到 last_update.json。
"""

import http.server
import json
import os
import socketserver
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import fetch_ndx_pe

CBOE_VIX_URL = (
    "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
)
YAHOO_NDX_SYMBOL = "^NDX"
YAHOO_SPX_SYMBOL = "^GSPC"

# 所有路径均基于脚本位置解析，确保无论从哪个目录启动行为一致
BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_CSV = str(BASE_DIR / "data" / "VIX_History.csv")
LOCAL_NDX_CSV = str(BASE_DIR / "data" / "NDX_History.csv")
LOCAL_SPX_CSV = str(BASE_DIR / "data" / "SPX_History.csv")
UPDATE_INFO = str(BASE_DIR / "data" / "last_update.json")
NDX_UPDATE_INFO = str(BASE_DIR / "data" / "ndx_last_update.json")
SPX_UPDATE_INFO = str(BASE_DIR / "data" / "spx_last_update.json")
NDX_PE_UPDATE_INFO = str(BASE_DIR / "data" / "ndx_pe_last_update.json")
TRADING_JOURNAL_PATH = str(BASE_DIR / "data" / "trading_journal.json")
MAX_JOURNAL_SIZE = 1024 * 1024  # 1 MB
JOURNAL_FIELDS = {
    "date",
    "action",
    "stockName",
    "externalFactor",
    "internalFactor",
    "result",
    "analysis",
    "improvement",
    "notes",
}
DEFAULT_PORT = 8080


def parse_csv_date(date_str):
    """解析 CBOE 日期格式 MM/DD/YYYY 为本地日期对象。"""
    date_str = date_str.strip()
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").date()
    except ValueError:
        return None


def read_last_date_from_lines(lines):
    """从 CSV 文本行列表中读取最后一个有效 DATE 列。"""
    cleaned = [line.strip() for line in lines if line.strip()]
    if len(cleaned) < 2:
        return None

    # 找到 DATE 列索引
    headers = [h.strip().upper() for h in cleaned[0].split(",")]
    try:
        date_idx = headers.index("DATE")
    except ValueError:
        return None

    # 从后往前找第一个有效日期
    for line in reversed(cleaned[1:]):
        cols = line.split(",")
        if len(cols) <= date_idx:
            continue
        date = parse_csv_date(cols[date_idx])
        if date is not None:
            return date

    return None


def read_last_date(csv_path):
    """读取 CSV 文件最后一行的 DATE 列。"""
    if not os.path.exists(csv_path):
        return None

    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None

    return read_last_date_from_lines(lines)


def fetch_remote_csv(url):
    """从远程拉取 CSV 内容，返回字节串。"""
    request = Request(
        url,
        headers={"User-Agent": "VIX-Dashboard/1.0 (local-dev)"},
    )
    with urlopen(request, timeout=30) as response:
        return response.read()


def update_csv_data(local_path, fetch_csv_text, source_name, log_prefix, append_mode=False):
    """通用 CSV 数据更新逻辑。

    Args:
        local_path: 本地 CSV 文件路径。
        fetch_csv_text: 返回 CSV 文本字符串的可调用对象。
        source_name: 数据源名称，写入 info['source']。
        log_prefix: 日志前缀，如 'VIX Updater'。
        append_mode: 为 True 时仅追加新行；为 False 时全量覆盖。
    """
    local_date = read_last_date(local_path)
    try:
        csv_text = fetch_csv_text()
    except Exception as e:
        print(f"[{log_prefix}] 拉取数据异常: {e}", file=sys.stderr)
        return {
            "status": "fetch_error",
            "message": f"拉取数据时发生异常: {e}",
            "previousLatestDate": local_date.isoformat() if local_date else None,
        }

    remote_date = read_last_date_from_lines(csv_text.splitlines())

    if remote_date is None:
        return {
            "status": "parse_error",
            "message": "远程 CSV 解析失败，无法识别有效日期",
            "previousLatestDate": local_date.isoformat() if local_date else None,
        }

    info = {
        "source": source_name,
        "latestDate": remote_date.isoformat(),
        "previousLatestDate": local_date.isoformat() if local_date else None,
    }

    if local_date is None or remote_date > local_date:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        if local_date is None:
            with open(local_path, "w", encoding="utf-8", newline="") as f:
                f.write(csv_text)
            added_rows = len(csv_text.strip().splitlines()) - 1
        elif append_mode:
            added_rows = append_csv_rows_after(local_path, csv_text, local_date)
        else:
            with open(local_path, "w", encoding="utf-8", newline="") as f:
                f.write(csv_text)
            # 估算新增行数：远程文件中日期晚于本地日期的行数
            added_rows = 0
            for line in csv_text.splitlines():
                cols = line.split(",")
                if len(cols) < 2:
                    continue
                d = parse_csv_date(cols[0])
                if d and d > local_date:
                    added_rows += 1

        info["status"] = "updated"
        info["addedRows"] = added_rows
        print(
            f"[{log_prefix}] 已更新本地数据: {local_date} -> {remote_date}"
            if local_date
            else f"[{log_prefix}] 已初始化本地数据，最新日期: {remote_date}"
        )
    else:
        info["status"] = "up_to_date"
        info["addedRows"] = 0
        print(f"[{log_prefix}] 本地数据已是最新（{remote_date}），无需更新")

    return info


def update_vix_data():
    """拉取并更新 VIX 数据，返回更新信息字典。"""
    local_date = read_last_date(LOCAL_CSV)

    try:
        print(f"[VIX Updater] 正在从 CBOE 拉取最新数据...")
        raw = fetch_remote_csv(CBOE_VIX_URL)
    except URLError as e:
        reason = getattr(e, "reason", str(e))
        print(f"[VIX Updater] 网络请求失败: {reason}", file=sys.stderr)
        return {
            "status": "network_error",
            "message": f"无法连接到 CBOE 数据源: {reason}",
            "previousLatestDate": local_date.isoformat() if local_date else None,
        }
    except Exception as e:
        print(f"[VIX Updater] 拉取数据异常: {e}", file=sys.stderr)
        return {
            "status": "fetch_error",
            "message": f"拉取数据时发生异常: {e}",
            "previousLatestDate": local_date.isoformat() if local_date else None,
        }

    try:
        remote_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        remote_text = raw.decode("utf-8", errors="replace")

    return update_csv_data(
        LOCAL_CSV,
        lambda: remote_text,
        CBOE_VIX_URL,
        "VIX Updater",
        append_mode=False,
    )


def fetch_yahoo_history(symbol, start=None):
    """通过 yfinance 拉取 Yahoo Finance 日线 OHLC，返回 DataFrame。

    Args:
        symbol: Yahoo Finance 代码，例如 ^NDX 或 ^GSPC。
        start: 起始日期（date 或 datetime）。传入 None 时拉取全部历史数据。
    """
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    if start is not None:
        df = ticker.history(start=start, auto_adjust=False)
    else:
        df = ticker.history(period="max", auto_adjust=False)
    return df


def fetch_ndx_history(start=None):
    """通过 yfinance 拉取纳斯达克100指数 (^NDX) 日线 OHLC，返回 DataFrame。"""
    return fetch_yahoo_history(YAHOO_NDX_SYMBOL, start)


def fetch_spx_history(start=None):
    """通过 yfinance 拉取标普500指数 (^GSPC) 日线 OHLC，返回 DataFrame。"""
    return fetch_yahoo_history(YAHOO_SPX_SYMBOL, start)


def yahoo_dataframe_to_csv(df):
    """将 yfinance DataFrame 转换为项目统一格式的 CSV 字符串。"""
    import pandas as pd

    dt = df.index.tz_convert("America/New_York") if df.index.tz else df.index
    out = pd.DataFrame(
        {
            "DATE": dt.strftime("%m/%d/%Y"),
            "OPEN": df["Open"].to_numpy(),
            "HIGH": df["High"].to_numpy(),
            "LOW": df["Low"].to_numpy(),
            "CLOSE": df["Close"].to_numpy(),
            "_sort": dt,
        }
    )
    out = out.sort_values("_sort").drop(columns=["_sort"]).reset_index(drop=True)
    return out.to_csv(index=False)


def append_csv_rows_after(csv_path, new_csv_text, after_date):
    """将 new_csv_text 中日期晚于 after_date 的行追加到 csv_path。

    会跳过 new_csv_text 的表头，并保证追加前文件以换行符结尾。
    返回实际追加的行数。
    """
    lines = new_csv_text.strip().splitlines()
    if len(lines) <= 1:
        return 0

    new_rows = []
    for line in lines[1:]:
        cols = line.split(",")
        if len(cols) < 2:
            continue
        d = parse_csv_date(cols[0])
        if d and d > after_date:
            new_rows.append(line)

    if not new_rows:
        return 0

    needs_newline = False
    if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
        with open(csv_path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                needs_newline = True

    with open(csv_path, "a", encoding="utf-8", newline="") as f:
        if needs_newline:
            f.write("\n")
        f.write("\n".join(new_rows) + "\n")

    return len(new_rows)


def update_index_data(local_csv, symbol, fetch_history, log_prefix):
    """通用指数数据更新逻辑（NDX / SPX）。

    Args:
        local_csv: 本地 CSV 文件路径。
        symbol: Yahoo Finance 代码。
        fetch_history: 接收 start 参数并返回 DataFrame 的可调用对象。
        log_prefix: 日志前缀，如 'NDX Updater'。
    """
    local_date = read_last_date(local_csv)
    # 若已有本地数据，从本地最后日期开始拉取，避免每次全量下载。
    start = local_date if local_date else None

    try:
        print(f"[{log_prefix}] 正在从 Yahoo Finance 拉取最新数据...")
        df = fetch_history(start=start)
    except ImportError as e:
        print(
            f"[{log_prefix}] 缺少 yfinance 依赖，请运行: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return {
            "status": "missing_dependency",
            "message": f"缺少依赖: {e}",
            "source": f"Yahoo Finance ({symbol})",
            "previousLatestDate": local_date.isoformat() if local_date else None,
        }
    except Exception as e:
        print(f"[{log_prefix}] 拉取数据异常: {e}", file=sys.stderr)
        return {
            "status": "fetch_error",
            "message": f"拉取数据时发生异常: {e}",
            "previousLatestDate": local_date.isoformat() if local_date else None,
        }

    # 增量拉取时，本地最新日期之后可能暂无新数据（如当日未收盘、周末或节假日），
    # 此时返回空 DataFrame 应视为已是最新，而不是获取失败。
    if df.empty:
        if local_date is not None:
            print(
                f"[{log_prefix}] 未获取到新数据，本地数据已是最新（{local_date}）",
                file=sys.stderr,
            )
            return {
                "status": "up_to_date",
                "source": f"Yahoo Finance ({symbol})",
                "latestDate": local_date.isoformat(),
                "previousLatestDate": local_date.isoformat(),
                "addedRows": 0,
            }
        return {
            "status": "fetch_error",
            "message": "yfinance 返回空数据",
            "previousLatestDate": None,
        }

    try:
        csv_text = yahoo_dataframe_to_csv(df)
    except ImportError as e:
        print(
            f"[{log_prefix}] 缺少 pandas 依赖，请运行: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return {
            "status": "missing_dependency",
            "message": f"缺少依赖: {e}",
            "source": f"Yahoo Finance ({symbol})",
            "previousLatestDate": local_date.isoformat() if local_date else None,
        }

    return update_csv_data(
        local_csv,
        lambda: csv_text,
        f"Yahoo Finance ({symbol})",
        log_prefix,
        append_mode=True,
    )


def update_ndx_data():
    """拉取并更新纳斯达克100数据，返回更新信息字典。"""
    return update_index_data(
        LOCAL_NDX_CSV, YAHOO_NDX_SYMBOL, fetch_ndx_history, "NDX Updater"
    )


def update_spx_data():
    """拉取并更新标普500数据，返回更新信息字典。"""
    return update_index_data(
        LOCAL_SPX_CSV, YAHOO_SPX_SYMBOL, fetch_spx_history, "SPX Updater"
    )


def write_update_info(info):
    """将更新信息写入 last_update.json。"""
    record = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        **info,
    }
    os.makedirs(os.path.dirname(UPDATE_INFO), exist_ok=True)
    with open(UPDATE_INFO, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(f"[VIX Updater] 更新时间已记录到 {UPDATE_INFO}")


def write_ndx_update_info(info):
    """将纳斯达克100更新信息写入 ndx_last_update.json。"""
    record = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        **info,
    }
    os.makedirs(os.path.dirname(NDX_UPDATE_INFO), exist_ok=True)
    with open(NDX_UPDATE_INFO, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(f"[NDX Updater] 更新时间已记录到 {NDX_UPDATE_INFO}")


def write_spx_update_info(info):
    """将标普500更新信息写入 spx_last_update.json。"""
    record = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        **info,
    }
    os.makedirs(os.path.dirname(SPX_UPDATE_INFO), exist_ok=True)
    with open(SPX_UPDATE_INFO, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(f"[SPX Updater] 更新时间已记录到 {SPX_UPDATE_INFO}")


def update_ndx_pe_data():
    """更新纳斯达克100前瞻PE代理数据，失败时不阻塞启动。"""
    try:
        return fetch_ndx_pe.update_ndx_pe()
    except Exception as e:
        print(f"[NDX PE Updater] 更新异常: {e}", file=sys.stderr)
        return {"status": "fetch_error", "message": str(e)}


def write_ndx_pe_update_info(info):
    """将纳斯达克100前瞻PE更新信息写入 ndx_pe_last_update.json。"""
    record = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        **info,
    }
    # 将 CSV 合并结果展平，方便前端直接读取
    csv_merge = record.pop("csvMerge", None)
    if isinstance(csv_merge, dict) and csv_merge.get("status") == "updated":
        record["csvLatestMonth"] = csv_merge.get("latestMonth")
        record["csvTrailingPE"] = csv_merge.get("trailingPE")
    # 增加 latestDate 字段，使前端时间提示与 VIX/NDX/SPX 一致
    data = record.get("data")
    if isinstance(data, dict) and data.get("as_of"):
        record["latestDate"] = data["as_of"]
    os.makedirs(os.path.dirname(NDX_PE_UPDATE_INFO), exist_ok=True)
    with open(NDX_PE_UPDATE_INFO, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(f"[NDX PE Updater] 更新时间已记录到 {NDX_PE_UPDATE_INFO}")


def _validate_journal_payload(data):
    """校验交易日志请求体结构。

    要求顶层为 dict，records 为 list，每条记录为 dict 且字段值均为字符串。
    返回 (ok, error_message)。
    """
    if not isinstance(data, dict):
        return False, "请求体必须是 JSON 对象"
    records = data.get("records")
    if not isinstance(records, list):
        return False, "records 必须是数组"
    for idx, rec in enumerate(records):
        if not isinstance(rec, dict):
            return False, f"records[{idx}] 必须是对象"
        for field, value in rec.items():
            if field not in JOURNAL_FIELDS:
                return False, f"records[{idx}] 包含未知字段: {field}"
            if not isinstance(value, str):
                return False, f"records[{idx}].{field} 必须是字符串"
    return True, None


class CORSRequestHandler(http.server.SimpleHTTPRequestHandler):
    """简单的 HTTP 请求处理器，允许本地开发跨域请求。

    始终以项目根目录作为静态资源服务根，避免依赖启动时的工作目录。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        # 交易日志保存接口：POST /data/trading_journal.json
        if self.path.rstrip("/") == "/data/trading_journal.json":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length > MAX_JOURNAL_SIZE:
                    self.send_response(413)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        json.dumps({"ok": False, "error": "请求体过大"}, ensure_ascii=False).encode("utf-8")
                    )
                    return

                body = self.rfile.read(content_length)
                data = json.loads(body.decode("utf-8"))

                ok, error_msg = _validate_journal_payload(data)
                if not ok:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": error_msg}, ensure_ascii=False).encode("utf-8"))
                    return

                # 格式化写入
                os.makedirs(os.path.dirname(TRADING_JOURNAL_PATH), exist_ok=True)
                with open(TRADING_JOURNAL_PATH, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.write("\n")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8"))
                print(f"[HTTP] 交易日志已保存 ({len(data.get('records', []))} 条记录)")
            except json.JSONDecodeError as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "JSON 解析失败"}, ensure_ascii=False).encode("utf-8"))
                print(f"[HTTP] 交易日志保存失败: JSON 解析失败 - {e}", file=sys.stderr)
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"ok": False, "error": "服务器内部错误"}, ensure_ascii=False).encode("utf-8")
                )
                print(f"[HTTP] 交易日志保存失败: {e}", file=sys.stderr)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        # 保持输出简洁
        print(f"[HTTP] {self.address_string()} - {fmt % args}")


def run_server(port):
    """启动本地 HTTP 服务。"""
    with socketserver.TCPServer(("", port), CORSRequestHandler) as httpd:
        print(f"[VIX Server] 服务已启动: http://localhost:{port}")
        print("[VIX Server] 按 Ctrl+C 停止服务")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[VIX Server] 正在关闭服务...")
            httpd.shutdown()


def main():
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print(f"[VIX Server] 端口参数无效，使用默认端口 {DEFAULT_PORT}")
            port = DEFAULT_PORT

    if not (1 <= port <= 65535):
        print(f"[VIX Server] 端口必须在 1-65535 之间，使用默认端口 {DEFAULT_PORT}")
        port = DEFAULT_PORT

    info = update_vix_data()
    try:
        write_update_info(info)
    except OSError as e:
        print(f"[VIX Updater] 记录更新时间失败: {e}", file=sys.stderr)

    ndx_info = update_ndx_data()
    try:
        write_ndx_update_info(ndx_info)
    except OSError as e:
        print(f"[NDX Updater] 记录更新时间失败: {e}", file=sys.stderr)

    spx_info = update_spx_data()
    try:
        write_spx_update_info(spx_info)
    except OSError as e:
        print(f"[SPX Updater] 记录更新时间失败: {e}", file=sys.stderr)

    ndx_pe_info = update_ndx_pe_data()
    try:
        write_ndx_pe_update_info(ndx_pe_info)
    except OSError as e:
        print(f"[NDX PE Updater] 记录更新时间失败: {e}", file=sys.stderr)

    run_server(port)


if __name__ == "__main__":
    main()
