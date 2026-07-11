#!/usr/bin/env python3
"""获取并保存纳斯达克100估值PE代理数据。

纳斯达克100指数（^NDX）本身没有官方免费的 PE 接口，且 Yahoo Finance 对 QQQ 等
ETF 仅提供 trailingPE（TTM），不提供 forwardPE。因此本脚本保存 QQQ 的 trailingPE
（TTM）作为纳斯达克100估值的代理指标，并预留 forward_pe 字段供未来接入付费数据源时使用。

运行方式:
    source /root/vix/.venv/bin/activate && python scripts/fetch_ndx_pe.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
NDX_PE_PATH = DATA_DIR / "ndx_pe.json"

PROXY_SYMBOL = "QQQ"


def _load_existing():
    """读取已有的 ndx_pe.json；不存在或损坏时返回 None。"""
    if not NDX_PE_PATH.exists():
        return None
    try:
        with open(NDX_PE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _is_valid_number(value):
    """判断值是否为有效数字（非 None、NaN、Infinity）。"""
    if value is None:
        return False
    try:
        num = float(value)
        return num == num and num != float("inf") and num != float("-inf")
    except (TypeError, ValueError):
        return False


def fetch_ndx_pe_info():
    """从 Yahoo Finance 获取 QQQ 的滚动市盈率（TTM）信息。

    Yahoo Finance 对 QQQ 等 ETF 不提供 forwardPE，因此以 trailingPE（TTM）
    作为纳斯达克100估值的代理指标。若未来 forwardPE 可用，会一并保存。

    Returns:
        dict: 包含 forward_pe、trailing_pe、source、as_of 的字典；
              获取失败时返回 None。
    """
    try:
        import yfinance as yf
    except ImportError as e:
        print(
            f"[NDX PE] 缺少 yfinance 依赖，请运行: pip install -r requirements.txt ({e})",
            file=sys.stderr,
        )
        return None

    try:
        ticker = yf.Ticker(PROXY_SYMBOL)
        info = ticker.info or {}
    except Exception as e:
        print(f"[NDX PE] 获取 {PROXY_SYMBOL} 信息失败: {e}", file=sys.stderr)
        return None

    forward_pe = info.get("forwardPE")
    trailing_pe = info.get("trailingPE")

    # Yahoo Finance 对 ETF 通常只提供 trailingPE，不提供 forwardPE；
    # trailingPE 必须为正数，forwardPE 保留为 null 等待后续数据源。
    if not _is_valid_number(trailing_pe) or float(trailing_pe) <= 0:
        print(
            f"[NDX PE] Yahoo Finance 未返回有效的 trailingPE（当前值: {trailing_pe}）",
            file=sys.stderr,
        )
        return None

    # 如果 info 里没有明确日期，用当前 UTC 日期作为数据日期
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return {
        "forward_pe": float(forward_pe) if _is_valid_number(forward_pe) else None,
        "trailing_pe": float(trailing_pe),
        "source": f"{PROXY_SYMBOL} via Yahoo Finance",
        "as_of": today,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "note": "Yahoo Finance 对 QQQ 等 ETF 仅提供滚动市盈率（TTM），未提供前瞻PE。",
    }


def update_ndx_pe():
    """更新本地 ndx_pe.json 中的滚动市盈率（TTM）数据，失败时保留旧数据。

    Returns:
        dict: 包含 status、data 的更新结果字典。
    """
    existing = _load_existing()
    fetched = fetch_ndx_pe_info()

    if fetched is None:
        message = "获取 NDX PE 失败，保留上一次有效数据" if existing else "获取 NDX PE 失败，无本地缓存"
        print(f"[NDX PE] {message}", file=sys.stderr)
        return {
            "status": "fetch_error",
            "message": message,
            "data": existing,
        }

    # 只有当数值或日期发生变化时才写入文件，避免无意义的文件修改
    def _same_optional_number(old, new):
        """判断两个可选数字是否相等（都为空或都有效且差值极小）。"""
        old_valid = _is_valid_number(old)
        new_valid = _is_valid_number(new)
        if not old_valid and not new_valid:
            return True
        if old_valid and new_valid:
            return abs(float(old) - float(new)) < 1e-9
        return False

    should_write = True
    if existing:
        same_forward = _same_optional_number(existing.get("forward_pe"), fetched["forward_pe"])
        same_trailing = _same_optional_number(existing.get("trailing_pe"), fetched["trailing_pe"])
        same_as_of = existing.get("as_of") == fetched["as_of"]
        if same_forward and same_trailing and same_as_of:
            should_write = False

    if should_write:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(NDX_PE_PATH, "w", encoding="utf-8") as f:
            json.dump(fetched, f, ensure_ascii=False, indent=2)
        parts = [f"trailing={fetched['trailing_pe']:.2f}"]
        if fetched["forward_pe"] is not None:
            parts.append(f"forward={fetched['forward_pe']:.2f}")
        print(f"[NDX PE] 已更新: {', '.join(parts)}")
    else:
        print("[NDX PE] 本地数据已是最新，无需更新")

    return {"status": "updated" if should_write else "up_to_date", "data": fetched}


def main():
    result = update_ndx_pe()
    if result["status"] == "fetch_error" and result["data"] is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
