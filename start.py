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
from urllib.error import URLError
from urllib.request import Request, urlopen

CBOE_VIX_URL = (
    "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
)
LOCAL_CSV = "VIX_History.csv"
UPDATE_INFO = "last_update.json"
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

    remote_date = read_last_date_from_lines(remote_text.splitlines())

    if remote_date is None:
        return {
            "status": "parse_error",
            "message": "远程 CSV 解析失败，无法识别有效日期",
            "previousLatestDate": local_date.isoformat() if local_date else None,
        }

    info = {
        "source": CBOE_VIX_URL,
        "latestDate": remote_date.isoformat(),
        "previousLatestDate": local_date.isoformat() if local_date else None,
    }

    if local_date is None or remote_date > local_date:
        with open(LOCAL_CSV, "w", encoding="utf-8", newline="") as f:
            f.write(remote_text)

        added_rows = None
        if local_date is not None:
            # 估算新增行数：远程文件中日期晚于本地日期的行数
            added_rows = 0
            for line in remote_text.splitlines():
                cols = line.split(",")
                if len(cols) < 2:
                    continue
                d = parse_csv_date(cols[0])
                if d and d > local_date:
                    added_rows += 1

        info["status"] = "updated"
        info["addedRows"] = added_rows
        print(
            f"[VIX Updater] 已更新本地数据: {local_date} -> {remote_date}"
            if local_date
            else f"[VIX Updater] 已初始化本地数据，最新日期: {remote_date}"
        )
    else:
        info["status"] = "up_to_date"
        info["addedRows"] = 0
        print(f"[VIX Updater] 本地数据已是最新（{remote_date}），无需更新")

    return info


def write_update_info(info):
    """将更新信息写入 last_update.json。"""
    record = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        **info,
    }
    with open(UPDATE_INFO, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(f"[VIX Updater] 更新时间已记录到 {UPDATE_INFO}")


class CORSRequestHandler(http.server.SimpleHTTPRequestHandler):
    """简单的 HTTP 请求处理器，允许本地开发跨域请求。"""

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

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
    run_server(port)


if __name__ == "__main__":
    main()
