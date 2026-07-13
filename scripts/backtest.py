#!/usr/bin/env python3
"""基于 VectorBT 的 VIX 驱动 QQQ/QLD/TQQQ 杠杆轮动回测。

策略完全通过命令行参数 --thresholds 与 --allocations 控制。N 个阈值对应
N+1 个区间，区间边界规则统一为：
- 第 0 区间：VIX < thresholds[0]
- 第 1 区间：thresholds[0] <= VIX <= thresholds[1]
- 第 i 区间（i >= 2）：thresholds[i-1] < VIX <= thresholds[i]
- 第 N 区间：VIX > thresholds[-1]

示例（4 阈值 20/30/40/50）：
- VIX < 20：满仓 QQQ（1 倍）
- 20 <= VIX <= 30：半仓 QLD + 半仓 QQQ（1.5 倍）
- 30 < VIX <= 40：满仓 QLD（2 倍）
- 40 < VIX <= 50：半仓 QLD + 半仓 TQQQ（2.5 倍）
- VIX > 50：满仓 TQQQ（3 倍）

运行方式：
    source /root/vix/.venv/bin/activate && python scripts/backtest.py \
        --thresholds 20 30 40 50 \
        --allocations "QQQ:1.0" "QLD:0.5,QQQ:0.5" "QLD:1.0" "QLD:0.5,TQQQ:0.5" "TQQQ:1.0"

标的缺失时按以下链条回退：TQQQ -> QLD -> QQQ -> 空仓，QLD -> QQQ -> 空仓。
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import vectorbt as vbt
import yfinance as yf

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# 默认参数
DEFAULT_SYMBOLS = ["QQQ", "QLD", "TQQQ"]
DEFAULT_VIX_SYMBOL = "^VIX"
DEFAULT_START = "2006-01-01"
DEFAULT_END = None
DEFAULT_CASH = 10_000
DEFAULT_FEES = 0.0002  # IBKR 阶梯费率约 0.02% 单边（高流动性 ETF）
DEFAULT_SLIPPAGE = 0.0003  # QQQ/QLD/TQQQ 盘口紧密，约 0.03% 滑点

# 本地 ETF 缓存参数
_REQUIRED_ETF_COLUMNS = {"DATE", "OPEN", "HIGH", "LOW", "CLOSE"}
_BACKWARD_BUFFER_DAYS = 5  # 起始日变动小于此天数时不重新向后扩展
_FORWARD_OVERLAP_DAYS = 5  # 向前扩展时与本地数据的重叠天数，用于对齐和复权
_OPEN_END_GRACE_DAYS = 5  # 开放式结束日期时允许的数据延迟天数
_EXPLICIT_END_GRACE_DAYS = 1  # 显式结束日期时允许的数据延迟天数


def _local_etf_path(symbol):
    """返回某只 ETF 本地历史数据文件路径。"""
    if not isinstance(symbol, str) or not symbol:
        raise ValueError(f"无效的标的代码: {symbol!r}")
    if ".." in symbol or "/" in symbol or "\\" in symbol:
        raise ValueError(f"标的代码包含非法字符: {symbol!r}")
    return DATA_DIR / f"{symbol.upper()}_History.csv"


def _load_local_etf(symbol):
    """加载本地 ETF 历史 CSV；文件不存在返回 None，列缺失或解析失败抛出异常。"""
    path = _local_etf_path(symbol)
    if not path.exists():
        return None
    df = pd.read_csv(path)
    missing = _REQUIRED_ETF_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"本地 ETF 文件 {path} 缺少必要列: {missing}")
    df["DATE"] = pd.to_datetime(df["DATE"], format="%m/%d/%Y")
    df = df.set_index("DATE").sort_index()
    return df[["OPEN", "HIGH", "LOW", "CLOSE"]]


def _save_local_etf(symbol, df):
    """将 ETF OHLC 数据保存到本地 CSV，格式与现有历史数据一致。"""
    path = _local_etf_path(symbol)
    out = df.copy()
    out.index.name = "DATE"
    out = out.reset_index()
    out["DATE"] = out["DATE"].dt.strftime("%m/%d/%Y")
    out = out[["DATE", "OPEN", "HIGH", "LOW", "CLOSE"]]
    out.to_csv(path, index=False, float_format="%.6f")
    print(f"[Backtest] 已保存本地数据: {path}")


ETF_METADATA_FILE = DATA_DIR / "etf_metadata.json"


def _load_etf_metadata():
    """加载 ETF 数据元数据，记录每只标的曾经请求过的起始日期。"""
    if not ETF_METADATA_FILE.exists():
        return {}
    with open(ETF_METADATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_etf_metadata(metadata):
    """保存 ETF 数据元数据。"""
    with open(ETF_METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)


def _validate_symbols(symbols):
    """校验标的代码列表，返回全大写列表；禁止路径逃逸字符。"""
    if not symbols:
        raise ValueError("标的代码列表不能为空")
    validated = []
    for s in symbols:
        if not isinstance(s, str) or not s:
            raise ValueError(f"无效的标的代码: {s!r}")
        if ".." in s or "/" in s or "\\" in s:
            raise ValueError(f"标的代码包含非法字符: {s!r}")
        validated.append(s.upper())
    return validated


def _calculate_download_plan(symbols, local_data, metadata, target_start, target_end, end_is_open):
    """根据本地数据和元数据判断是否需要下载，以及下载起始日。

    返回三元组：(need_download, download_start, updated_metadata)。
    注意：本地数据默认是连续交易日序列，中间不存在缺失区间。
    """
    need_backward = False
    need_forward = False
    forward_download_starts = []

    for symbol in symbols:
        meta = metadata.setdefault(symbol, {})
        if symbol not in local_data:
            need_backward = True
            meta["requested_start"] = target_start.strftime("%Y-%m-%d")
            continue

        df = local_data[symbol]
        last_date = df.index[-1]
        recorded_start_str = meta.get("requested_start")
        recorded_start = pd.Timestamp(recorded_start_str) if recorded_start_str else target_start

        # 用户要求比历史记录更早的数据：尝试向后扩展
        if target_start < recorded_start - pd.Timedelta(days=_BACKWARD_BUFFER_DAYS):
            need_backward = True
            meta["requested_start"] = target_start.strftime("%Y-%m-%d")
        elif "requested_start" not in meta:
            meta["requested_start"] = target_start.strftime("%Y-%m-%d")

        # 向未来扩展：开放式结束允许若干天延迟，显式结束需覆盖到目标结束日前一天
        if end_is_open and last_date < target_end - pd.Timedelta(days=_OPEN_END_GRACE_DAYS):
            need_forward = True
            forward_download_starts.append(last_date - pd.Timedelta(days=_FORWARD_OVERLAP_DAYS))
        elif not end_is_open and last_date < target_end - pd.Timedelta(days=_EXPLICIT_END_GRACE_DAYS):
            need_forward = True
            forward_download_starts.append(last_date - pd.Timedelta(days=_FORWARD_OVERLAP_DAYS))

    if need_backward:
        download_start = target_start
    elif need_forward:
        download_start = min(forward_download_starts)
        download_start = max(download_start, target_start)
    else:
        download_start = target_start

    need_download = need_backward or need_forward
    return need_download, download_start, metadata


def _download_etf_data(symbols, download_start, target_end):
    """通过 yfinance 下载 ETF 数据，返回 {symbol: OHLC DataFrame}。

    仅返回非空数据的标的；完全未获取到的标的不在结果中。
    """
    print(f"[Backtest] 下载/更新数据: {symbols} ...")
    data = yf.download(
        symbols,
        start=download_start.strftime("%Y-%m-%d"),
        end=(target_end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        progress=False,
        auto_adjust=True,
        threads=True,
    )
    if data.empty:
        raise ValueError("未获取到任何数据，请检查网络或标的代码。")

    downloaded = {}
    # yfinance 单资产返回 Series，多资产返回 MultiIndex columns: (field, symbol)
    single_symbol = len(symbols) == 1
    for symbol in symbols:
        if single_symbol:
            open_series = data["Open"]
            high_series = data["High"]
            low_series = data["Low"]
            close_series = data["Close"]
        else:
            if symbol not in data["Close"].columns:
                continue
            open_series = data["Open"][symbol]
            high_series = data["High"][symbol]
            low_series = data["Low"][symbol]
            close_series = data["Close"][symbol]

        df = pd.DataFrame(
            {
                "OPEN": open_series,
                "HIGH": high_series,
                "LOW": low_series,
                "CLOSE": close_series,
            }
        ).dropna()

        if not df.empty:
            downloaded[symbol] = df

    return downloaded


def _merge_and_save_etf_data(local_data, downloaded):
    """将下载数据合并到本地缓存，仅在有变化时写回磁盘。"""
    for symbol, df in downloaded.items():
        if symbol in local_data:
            original = local_data[symbol]
            # 合并本地与下载数据，重叠日期使用新下载的数据
            combined = pd.concat([original, df])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined = combined.sort_index()
            local_data[symbol] = combined
            # 只有真正出现新数据或调整时才重写本地文件
            if not combined.equals(original):
                _save_local_etf(symbol, combined)
        else:
            local_data[symbol] = df.sort_index()
            _save_local_etf(symbol, local_data[symbol])
    return local_data


def fetch_etf_data(symbols, start, end):
    """获取多只 ETF 的日线收盘价，优先使用本地 CSV 并自动增量更新。

    每次执行时会检查本地数据是否存在、是否需要向未来/过去扩展；缺失或过时则
    通过 yfinance 下载补充，合并后写回本地文件。元数据会记录曾经请求过的
    起始日期，避免每次回测都重复尝试下载 ETF 上市前的历史数据。
    """
    symbols = _validate_symbols(symbols)
    target_start = pd.Timestamp(start) if start is not None else pd.Timestamp(DEFAULT_START)
    target_end = pd.Timestamp(end) if end is not None else pd.Timestamp.now().normalize()
    end_is_open = end is None

    metadata = _load_etf_metadata()
    local_data = {}
    for symbol in symbols:
        df = _load_local_etf(symbol)
        if df is not None:
            local_data[symbol] = df

    need_download, download_start, metadata = _calculate_download_plan(
        symbols, local_data, metadata, target_start, target_end, end_is_open
    )

    downloaded = {}
    if need_download:
        downloaded = _download_etf_data(symbols, download_start, target_end)
    else:
        print(f"[Backtest] 使用本地数据: {symbols}")

    # 任一标的在本地和本次下载中均无数据，则整体失败，避免静默缺失列
    missing_symbols = [s for s in symbols if s not in local_data and s not in downloaded]
    if missing_symbols:
        raise ValueError(f"未能获取以下标的的数据: {missing_symbols}")

    local_data = _merge_and_save_etf_data(local_data, downloaded)
    _save_etf_metadata(metadata)

    # 按请求区间过滤后返回收盘价
    close_frames = {}
    for symbol in symbols:
        if symbol not in local_data:
            continue
        df = local_data[symbol].copy()
        df = df[(df.index >= target_start) & (df.index < target_end)]
        close_frames[symbol] = df["CLOSE"]

    if not close_frames:
        raise ValueError("未获取到任何数据，请检查本地文件或标的代码。")

    close = pd.DataFrame(close_frames)
    return close


def fetch_vix_data(start, end):
    """获取 VIX 数据。

    优先使用本地 data/VIX_History.csv 的 HIGH/LOW 列，若不存在则通过 yfinance
    拉取 ^VIX。信号使用当日 VIX 中间价（最高价与最低价的平均值）。
    """
    local_vix = DATA_DIR / "VIX_History.csv"
    if local_vix.exists():
        df = pd.read_csv(local_vix)
        df["DATE"] = pd.to_datetime(df["DATE"], format="%m/%d/%Y")
        df = df.set_index("DATE").sort_index()
        if {"HIGH", "LOW"}.issubset(df.columns):
            print("[Backtest] 使用本地 VIX_History.csv (HIGH/LOW 中间价) ...")
            vix = ((df["HIGH"] + df["LOW"]) / 2).rename("VIX")
        else:
            print("[Backtest] 本地 VIX_History.csv 缺少 HIGH/LOW，使用 CLOSE ...")
            vix = df["CLOSE"].rename("VIX")
        if start is not None:
            vix = vix[vix.index >= pd.Timestamp(start)]
        if end is not None:
            vix = vix[vix.index < pd.Timestamp(end)]
        return vix

    print("[Backtest] 本地 VIX 数据不存在，从 Yahoo Finance 下载 ^VIX ...")
    vix_data = yf.download(DEFAULT_VIX_SYMBOL, start=start, end=end, progress=False, auto_adjust=True)
    if {"High", "Low"}.issubset(vix_data.columns):
        vix = ((vix_data["High"] + vix_data["Low"]) / 2).squeeze().rename("VIX")
    else:
        vix = vix_data["Close"].squeeze().rename("VIX")
    return vix


def _resolve_asset(asset, close_row):
    """根据标的回退链返回当前交易日可用的实际标的；均缺失返回 None。"""
    chains = {
        "TQQQ": ["TQQQ", "QLD", "QQQ"],
        "QLD": ["QLD", "QQQ"],
        "QQQ": ["QQQ"],
    }
    for candidate in chains.get(asset, [asset]):
        if candidate in close_row.index and pd.notna(close_row[candidate]):
            return candidate
    return None


def _regime_index_from_vix_scalar(v, thresholds):
    """标量 VIX 值对应的区间索引（0..N，N 为阈值数量）。"""
    if not thresholds:
        return 0
    n = len(thresholds)
    if v < thresholds[0]:
        return 0
    if n >= 2:
        if v <= thresholds[1]:
            return 1
        for i in range(2, n):
            if v <= thresholds[i]:
                return i
    return n


def _regime_from_vix(vix, thresholds):
    """根据 VIX Series 与阈值序列返回区间索引（0..N，N 为阈值数量）。

    区间边界规则：
    - 第 0 区间：v < thresholds[0]
    - 第 1 区间：thresholds[0] <= v <= thresholds[1]
    - 第 i 区间（2 <= i < N）：thresholds[i-1] < v <= thresholds[i]
    - 第 N 区间：v > thresholds[-1]
    """
    if not thresholds:
        return pd.Series(0, index=vix.index)

    n = len(thresholds)
    conditions = [vix < thresholds[0]]
    choices = [0]

    if n >= 2:
        # 第 1 个区间右闭，与后续区间保持一致
        conditions.append(vix <= thresholds[1])
        choices.append(1)
        for i in range(2, n):
            conditions.append(vix <= thresholds[i])
            choices.append(i)

    conditions.append(True)
    choices.append(n)

    return pd.Series(np.select(conditions, choices, default=0), index=vix.index)


def _regime_from_vix_hysteresis(vix, thresholds, hysteresis=0.0):
    """带退出滞后的 VIX 区间判断。

    上行（进入更高档位/更高杠杆）使用原始阈值；下行（退出到更低档位）
    使用原始阈值减去 hysteresis，避免 VIX 快速回落时立即降杠杆。

    例如 thresholds=(20,30,40,50)、hysteresis=5：
    - VIX 从 15 涨到 22 时立即进入 20-30 档位
    - VIX 从 32 回落到 28 时仍保持 30-40 档位，只有跌破 25 才降档
    """
    if hysteresis <= 0 or not thresholds:
        return _regime_from_vix(vix, thresholds)

    thresholds = tuple(thresholds)
    exit_thresholds = tuple(t - hysteresis for t in thresholds)

    regime = pd.Series(index=vix.index, dtype=int)
    current = 0
    initialized = False

    for i, v in enumerate(vix):
        if pd.isna(v):
            regime.iloc[i] = current
            continue

        if not initialized:
            current = int(_regime_index_from_vix_scalar(v, thresholds))
            initialized = True
            regime.iloc[i] = current
            continue

        target_up = _regime_index_from_vix_scalar(v, thresholds)
        target_down = _regime_index_from_vix_scalar(v, exit_thresholds)

        if target_up > current:
            current = target_up
        elif target_down < current:
            current = target_down

        regime.iloc[i] = current

    return regime


def _leverage_to_allocations(leverage):
    """将目标杠杆转换为资产权重列表。

    利用 QQQ（1x）、QLD（2x）、TQQQ（3x）构造近似杠杆：
    - L <= 1.0：全部 QQQ，权重 L
    - 1.0 < L <= 2.0：QLD @ (L - 1.0) + QQQ @ (2.0 - L)
    - 2.0 < L <= 3.0：TQQQ @ (L - 2.0) + QLD @ (3.0 - L)
    - L > 3.0：满仓 TQQQ（当前标的中最大杠杆为 3x）
    """
    if leverage <= 1.0:
        return [("QQQ", leverage)]
    if leverage <= 2.0:
        return [("QLD", leverage - 1.0), ("QQQ", 2.0 - leverage)]
    if leverage <= 3.0:
        return [("TQQQ", leverage - 2.0), ("QLD", 3.0 - leverage)]
    return [("TQQQ", 1.0)]


def _default_allocations(n_regimes):
    """返回默认的 N 个区间仓位分配列表（杠杆从 0.5x 起每档递增 0.5x）。"""
    return [_leverage_to_allocations((i + 1) * 0.5) for i in range(n_regimes)]


def _parse_allocation(alloc_str):
    """解析单个区间分配字符串，返回 [(asset, weight), ...]。

    格式示例："QQQ:0.5" 或 "QLD:0.5,QQQ:0.5"。
    """
    if not alloc_str or not alloc_str.strip():
        return []
    allocations = []
    for part in alloc_str.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"分配格式错误，应为 资产:权重，如 QQQ:0.5: {alloc_str!r}")
        asset, weight_str = part.split(":", 1)
        asset = asset.strip().upper()
        try:
            weight = float(weight_str)
        except ValueError as e:
            raise ValueError(f"权重必须是数字: {weight_str!r}") from e
        if weight < 0:
            raise ValueError(f"权重不能为负数: {weight}")
        allocations.append((asset, weight))
    return allocations


def _format_allocation(allocation):
    """将分配列表格式化为人类可读字符串，跳过零权重项。"""
    parts = [f"{asset} {weight:.1%}" for asset, weight in allocation if weight > 0]
    return " + ".join(parts) if parts else "空仓"


def _regime_label(i, thresholds):
    """根据区间索引和阈值生成 VIX 区间描述，与 _regime_from_vix 规则一致。"""
    n = len(thresholds)
    if n == 0:
        return "所有 VIX"
    if i == 0:
        return f"VIX < {thresholds[0]}"
    if i == 1:
        if n >= 2:
            return f"{thresholds[0]} <= VIX <= {thresholds[1]}"
        return f"VIX >= {thresholds[0]}"
    if i < n:
        return f"{thresholds[i - 1]} <= VIX <= {thresholds[i]}"
    return f"VIX > {thresholds[-1]}"


def _validate_allocations(allocations, n_regimes, available_symbols):
    """校验自定义分配列表是否合法。"""
    if len(allocations) != n_regimes:
        raise ValueError(f"分配数量必须为 {n_regimes}（阈值数量 + 1），当前为 {len(allocations)}")

    valid_symbols = set(s.upper() for s in available_symbols)
    for i, alloc in enumerate(allocations):
        total_weight = 0.0
        for asset, weight in alloc:
            if asset not in valid_symbols and asset not in {"QQQ", "QLD", "TQQQ"}:
                raise ValueError(f"第 {i} 个区间包含未知资产: {asset}")
            total_weight += weight
        if total_weight > 1.0 + 1e-9:
            raise ValueError(f"第 {i} 个区间权重和 {total_weight:.4f} 超过 1.0")

    return allocations


def build_signals(close, vix, thresholds, allocations=None, hysteresis=0.0, vix_ma=1):
    """根据 VIX 阈值生成每日目标权重矩阵（支持混合仓位与标的回退）。

    阈值数量决定仓位档位数量（N 个阈值对应 N+1 个档位）。未提供 allocations
    时，默认按杠杆从 0.5x 起每档递增 0.5x 自动映射到 QQQ/QLD/TQQQ。也可通过
    allocations 参数完全自定义每个区间的持仓比重。

    区间边界规则：
    - 第 0 区间：VIX < thresholds[0]
    - 第 1 区间：thresholds[0] <= VIX <= thresholds[1]
    - 第 i 区间（i >= 2）：thresholds[i-1] < VIX <= thresholds[i]
    - 第 N 区间：VIX > thresholds[-1]

    hysteresis 用于控制退出滞后：上行使用原始阈值，下行使用
    thresholds - hysteresis，可减少 VIX 快速回落导致的频繁交易。
    vix_ma 用于对 VIX 中间价做 N 日移动平均，进一步平滑单日尖刺。

    标的缺失时按以下链条回退：
    - TQQQ 缺失 -> QLD -> QQQ -> 空仓
    - QLD 缺失  -> QQQ -> 空仓
    - QQQ 缺失  -> 空仓

    Args:
        close: 收盘价 DataFrame，columns 为资产代码。
        vix: VIX 中间价 Series（(HIGH + LOW) / 2）。
        thresholds: 阈值序列（list/tuple）。
        allocations: 可选，自定义分配列表，长度须为 len(thresholds)+1。
            每个元素为 [(asset, weight), ...]。为 None 时使用默认杠杆映射。
        hysteresis: 退出滞后点数，默认 0（无滞后）。
        vix_ma: VIX 移动平均窗口，默认 1（不平滑）。

    Returns:
        weights: DataFrame，与 close 同形，每日权重和由 allocations 决定。
    """
    thresholds = tuple(thresholds)
    n_regimes = len(thresholds) + 1

    if allocations is None:
        allocations = _default_allocations(n_regimes)
    else:
        allocations = list(allocations)
        _validate_allocations(allocations, n_regimes, close.columns)

    # 对齐 VIX 与收盘价日期，缺失日期/缺失值均前向填充
    # 注意：VIX 使用当日中间价（HIGH/LOW 平均）生成信号；如需避免前视偏差，
    # 调用方应自行将信号滞后一日执行（run_backtest 已默认这样做）。
    vix_aligned = vix.reindex(close.index, method="ffill").ffill()

    if vix_ma > 1:
        vix_aligned = vix_aligned.rolling(window=vix_ma, min_periods=1).mean()

    regime = _regime_from_vix_hysteresis(vix_aligned, thresholds, hysteresis)

    weights = pd.DataFrame(0.0, index=close.index, columns=close.columns)

    for i, date in enumerate(close.index):
        v = vix_aligned.iloc[i]
        # VIX 缺失时保持空仓，避免 NaN 比较落入高杠杆分支
        if pd.isna(v):
            continue

        close_row = close.loc[date]
        day_allocations = allocations[int(regime.iloc[i])]

        for asset, weight in day_allocations:
            if weight <= 0:
                continue
            resolved = _resolve_asset(asset, close_row)
            if resolved:
                weights.loc[date, resolved] += weight

    # 上市前缺失数据保持空仓（安全兜底）
    weights = weights.where(close.notna(), 0.0)

    return weights


def run_backtest(
    symbols,
    start,
    end,
    thresholds,
    initial_cash,
    fees,
    slippage,
    close=None,
    vix=None,
    allocations=None,
    hysteresis=0.0,
    vix_ma=1,
):
    """执行回测并返回 Portfolio 对象。

    Args:
        close: 预拉取的收盘价 DataFrame。为 None 时自动下载。
        vix: 预拉取的 VIX Series。为 None 时自动下载。
        allocations: 可选，自定义每个 VIX 区间的持仓分配。
        hysteresis: 退出滞后点数，默认 0。
        vix_ma: VIX 移动平均窗口，默认 1。
    """
    if close is None:
        close = fetch_etf_data(symbols, start, end)
    if vix is None:
        vix = fetch_vix_data(start, end)

    # 如果只有一个资产，yfinance 返回 Series，转成 DataFrame
    if isinstance(close, pd.Series):
        close = close.to_frame()

    # 统一列名
    close.columns = [str(c).upper() for c in close.columns]

    # 对齐日期
    common_idx = close.index.intersection(vix.index)
    if common_idx.empty:
        raise ValueError(
            "ETF 与 VIX 数据没有重叠日期，请检查起始/结束日期或本地数据。"
        )
    close = close.loc[common_idx]
    vix = vix.loc[common_idx]

    weights = build_signals(close, vix, thresholds, allocations=allocations, hysteresis=hysteresis, vix_ma=vix_ma)
    # VIX 数据日终才完整可知，当日信号次日执行，避免前视偏差
    weights = weights.shift(1).fillna(0.0)

    print(f"[Backtest] 回测区间: {close.index[0].date()} ~ {close.index[-1].date()}")
    print(f"[Backtest] 资产数量: {len(close.columns)}")
    print(f"[Backtest] VIX 阈值: {thresholds}")
    if hysteresis:
        print(f"[Backtest] 退出滞后: {hysteresis}")
    if vix_ma > 1:
        print(f"[Backtest] VIX 移动平均: {vix_ma} 日")

    portfolio = vbt.Portfolio.from_orders(
        close=close,
        size=weights,
        size_type="targetpercent",
        fees=fees,
        slippage=slippage,
        freq="1d",
        init_cash=initial_cash,
        # 多资产 targetpercent 必须共享同一笔现金，否则 vectorbt 会为每个资产
        # 单独分配初始资金，导致杠杆/下单计算与预期不符。
        cash_sharing=True,
    )

    return portfolio, close, vix, weights


def _max_drawdown_from_value(value):
    """根据价值序列计算最大回撤（返回负数，如 -0.2 表示 -20%）。"""
    import report_utils
    return report_utils.max_drawdown_from_series(value)


def _portfolio_value_metrics(portfolio):
    """基于组合总价值序列计算核心绩效指标（避免多资产时 Series 格式问题）。"""
    value = portfolio.value()
    if isinstance(value, pd.DataFrame):
        # 多列时按行求和得到组合总价值
        value = value.sum(axis=1)

    total_return = value.iloc[-1] / value.iloc[0] - 1
    days = (value.index[-1] - value.index[0]).days
    if days > 0:
        # 避免 total_return <= -1 时对非正数取幂产生复数或域错误
        annual_return = (
            max(value.iloc[-1], 1e-12) / value.iloc[0]
        ) ** (365 / days) - 1
    else:
        annual_return = 0.0

    returns = value.pct_change().dropna()
    sharpe = returns.mean() / returns.std() * (252 ** 0.5) if returns.std() > 0 else 0.0

    max_dd = _max_drawdown_from_value(value)

    calmar = annual_return / abs(max_dd) if max_dd != 0 else 0.0

    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "calmar": calmar,
    }


def print_metrics(portfolio, weights, initial_cash):
    """打印回测绩效指标。"""
    metrics = _portfolio_value_metrics(portfolio)
    trades = portfolio.trades

    initial_value = float(initial_cash)
    final_value = float(initial_cash * (1 + metrics["total_return"]))

    print("\n" + "=" * 50)
    print("回测绩效")
    print("=" * 50)
    print(f"初始持仓:        {initial_value:,.2f}")
    print(f"期末持仓:        {final_value:,.2f}")
    print(f"总收益率:        {metrics['total_return']:.2%}")
    print(f"年化收益率:      {metrics['annual_return']:.2%}")
    print(f"夏普比率:        {metrics['sharpe']:.2f}")
    print(f"最大回撤:        {metrics['max_drawdown']:.2%}")
    print(f"Calmar 比率:     {metrics['calmar']:.2f}")
    print(f"总交易次数:      {int(trades.count().sum())}")
    print(f"胜率:            {float(trades.win_rate().mean()):.2%}")


def save_results(portfolio, weights, args, close):
    """保存回测结果和图表到 output 目录，HTML 顶部附带绩效数据表格。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"vix_leverage_rotation_{timestamp}"

    value = portfolio.value()
    if isinstance(value, pd.DataFrame):
        # 多列时按行求和得到组合总价值；要求所有列均为数值型
        value = value.sum(axis=1)

    value_metrics = _portfolio_value_metrics(portfolio)
    initial_value = float(args.cash)
    final_value = float(args.cash * (1 + value_metrics["total_return"]))

    # 将权益曲线缩放到以用户初始资金为起点，便于与买入持有基准同尺度对比
    scale = args.cash / value.iloc[0] if value.iloc[0] != 0 else 1.0
    chart_value = value * scale

    # 买入持有基准（支持多个标的）
    benchmark_symbols = getattr(args, "benchmark", ["QQQ"])
    if isinstance(benchmark_symbols, str):
        benchmark_symbols = [benchmark_symbols]

    import report_utils

    benchmark_values = report_utils.build_benchmark_values(
        close, args.cash, args.fees, args.slippage, benchmark_symbols
    )

    benchmark_title = "、".join(s for s, _ in benchmark_values) if benchmark_values else "买入持有"
    fig = report_utils.build_plotly_chart(
        chart_value, weights, benchmark_values,
        f"VIX 驱动 QQQ/QLD/TQQQ 杠杆轮动回测"
    )
    fig.update_layout(
        title=f"VIX 驱动 QQQ/QLD/TQQQ 杠杆轮动回测",
    )

    n_regimes = len(args.thresholds) + 1
    allocations = getattr(args, "allocations", None)
    if allocations is None:
        allocations = _default_allocations(n_regimes)
    allocation_strs = [_format_allocation(a) for a in allocations]

    metrics = {
        "symbols": args.symbols,
        "start": str(portfolio.close.index[0].date()),
        "end": str(portfolio.close.index[-1].date()),
        "thresholds": args.thresholds,
        "cash": args.cash,
        "fees": args.fees,
        "slippage": args.slippage,
        "hysteresis": getattr(args, "hysteresis", 0.0),
        "vix_ma": getattr(args, "vix_ma", 1),
        "allocations": allocation_strs,
        "initial_value": initial_value,
        "final_value": final_value,
        "total_return": float(value_metrics["total_return"]),
        "annualized_return": float(value_metrics["annual_return"]),
        "sharpe_ratio": float(value_metrics["sharpe"]),
        "max_drawdown": float(value_metrics["max_drawdown"]),
        "calmar_ratio": float(value_metrics["calmar"]),
        "trade_count": int(portfolio.trades.count().sum()),
        "win_rate": float(portfolio.trades.win_rate().mean()),
        "benchmarks": [
            {
                "symbol": symbol,
                "final_value": float(bm_value.iloc[-1]) if not bm_value.empty else None,
                "total_return": float(bm_value.iloc[-1] / bm_value.iloc[0] - 1) if len(bm_value) >= 2 else 0.0,
                "max_drawdown": float(_max_drawdown_from_value(bm_value)) if len(bm_value) >= 2 else 0.0,
            }
            for symbol, bm_value in benchmark_values
        ],
    }

    # 构建 HTML 报告面板
    config_rows = [
        ("回测标的", ", ".join(metrics["symbols"])),
        ("VIX 阈值", str(metrics["thresholds"])),
        ("初始资金", f"{metrics['initial_value']:,.2f}"),
        ("手续费率", f"{metrics['fees']:.2%}"),
        ("滑点率", f"{metrics['slippage']:.2%}"),
    ]
    hysteresis = metrics.get("hysteresis", 0.0)
    if hysteresis:
        config_rows.append(("退出滞后", f"{hysteresis}"))
    vix_ma = metrics.get("vix_ma", 1)
    if vix_ma > 1:
        config_rows.append(("VIX 移动平均", f"{vix_ma} 日"))

    thresholds = metrics.get("thresholds", ())
    allocation_rows = [
        (_regime_label(i, thresholds), allocation)
        for i, allocation in enumerate(metrics.get("allocations", []))
    ]

    perf_rows = [
        ("回测区间", f"{metrics['start']} ~ {metrics['end']}"),
        ("期末持仓", f"{metrics['final_value']:,.2f}"),
        ("总收益率", f"{metrics['total_return']:.2%}"),
        ("年化收益率", f"{metrics['annualized_return']:.2%}"),
        ("夏普比率", f"{metrics['sharpe_ratio']:.2f}"),
        ("最大回撤", f"{metrics['max_drawdown']:.2%}"),
        ("Calmar 比率", f"{metrics['calmar_ratio']:.2f}"),
        ("总交易次数", f"{metrics['trade_count']}"),
        ("胜率", f"{metrics['win_rate']:.2%}"),
    ]

    benchmark_rows = []
    for bm in metrics.get("benchmarks", []):
        symbol = bm["symbol"]
        final = bm["final_value"]
        ret = bm["total_return"]
        max_dd = bm.get("max_drawdown", 0.0)
        benchmark_rows.append((f"{symbol} 期末持仓", f"{final:,.2f}" if final is not None else "—"))
        benchmark_rows.append((f"{symbol} 总收益率", f"{ret:.2%}"))
        benchmark_rows.append((f"{symbol} 最大回撤", f"{max_dd:.2%}"))

    allocation_table_html = "\n".join(
        f"                <tr><td class='alloc-regime'>{html.escape(str(label))}</td><td class='alloc-holdings'>{html.escape(str(val))}</td></tr>"
        for label, val in allocation_rows
    )
    allocation_panel_html = f"""<table class="alloc-table">
                <thead>
                    <tr><th>VIX 区间</th><th>持仓配置</th></tr>
                </thead>
                <tbody>
{allocation_table_html}
                </tbody>
            </table>"""

    panels = [
        ("策略配置", report_utils.grid_items(config_rows)),
        ("区间持仓配置", allocation_panel_html),
        ("回测绩效", report_utils.grid_items(perf_rows)),
        ("买入持有基准", report_utils.grid_items(benchmark_rows)),
    ]

    chart_html = fig.to_html(full_html=False, include_plotlyjs=True, div_id="backtest-chart")

    html_path = OUTPUT_DIR / f"{prefix}.html"
    report_utils.write_html_report(
        html_path, "VIX 驱动 QQQ/QLD/TQQQ 杠杆轮动回测", panels, chart_html,
        plotly_js=True,
    )

    json_path = OUTPUT_DIR / f"{prefix}_metrics.json"
    report_utils.write_json_report(json_path, metrics)

    print(f"\n[Backtest] 权益曲线已保存: {html_path}")
    print(f"[Backtest] 绩效指标已保存: {json_path}")




def parse_args():
    parser = argparse.ArgumentParser(description="VIX 驱动 QQQ/QLD/TQQQ 杠杆轮动回测")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS, help="ETF 标的列表")
    parser.add_argument("--start", default=DEFAULT_START, help="回测起始日期")
    parser.add_argument("--end", default=DEFAULT_END, help="回测结束日期")
    parser.add_argument(
        "--thresholds",
        nargs="+",
        type=float,
        default=[13.0, 20.0, 30.0, 40.0],
        metavar="T",
        help="VIX 阈值序列，默认 13 20 30 40；阈值数量决定仓位档位（N 个阈值对应 N+1 档），需严格递增",
    )
    parser.add_argument("--cash", type=float, default=DEFAULT_CASH, help="初始资金")
    parser.add_argument("--fees", type=float, default=DEFAULT_FEES, help="单边手续费比例")
    parser.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE, help="滑点比例")
    parser.add_argument(
        "--hysteresis",
        type=float,
        default=0.0,
        help="退出滞后点数，默认 0。上行使用原始阈值，下行使用阈值减去 hysteresis，可减少 VIX 快速回落导致的频繁交易",
    )
    parser.add_argument(
        "--vix-ma",
        type=int,
        default=1,
        help="VIX 中间价 N 日移动平均窗口，默认 1（不平滑）。大于 1 时可平滑 VIX 单日尖刺，减少频繁交易",
    )
    parser.add_argument(
        "--benchmark",
        nargs="+",
        type=str,
        default=["QQQ", "QLD", "TQQQ"],
        metavar="SYM",
        help="买入持有基准标的列表，默认 QQQ QLD TQQQ；可传入多个，如 --benchmark QQQ QLD",
    )
    parser.add_argument(
        "--allocations",
        nargs="+",
        type=str,
        default=None,
        metavar="ALLOC",
        help=(
            "自定义每个 VIX 区间的持仓分配，数量必须等于阈值数量+1。"
            "默认按杠杆从 0.5x 起每档递增 0.5x 映射到 QQQ/QLD/TQQQ。"
            "格式：每个区间为 '资产:权重,资产:权重,...'，如 "
            "'QQQ:1.0' 'QLD:0.5,QQQ:0.5' 'QLD:1.0' 'QLD:0.5,TQQQ:0.5' 'TQQQ:1.0'"
        ),
    )
    args = parser.parse_args()
    args.thresholds = tuple(args.thresholds)
    if len(args.thresholds) < 1:
        parser.error("至少需要 1 个阈值")
    if not all(args.thresholds[i] < args.thresholds[i + 1] for i in range(len(args.thresholds) - 1)):
        parser.error("阈值必须严格递增")

    if args.allocations is not None:
        expected = len(args.thresholds) + 1
        if len(args.allocations) != expected:
            parser.error(f"--allocations 数量必须为 {expected}（阈值数量 + 1）")
        parsed = []
        for alloc_str in args.allocations:
            try:
                parsed.append(_parse_allocation(alloc_str))
            except ValueError as e:
                parser.error(str(e))
        try:
            _validate_allocations(parsed, expected, args.symbols)
        except ValueError as e:
            parser.error(str(e))
        args.allocations = parsed

    if args.cash <= 0:
        parser.error("初始资金必须大于 0")
    if args.fees < 0 or args.slippage < 0:
        parser.error("手续费率和滑点率不能为负数")
    if args.hysteresis < 0:
        parser.error("退出滞后点数不能为负数")
    if args.vix_ma < 1:
        parser.error("VIX 移动平均窗口必须大于等于 1")
    return args


def main():
    args = parse_args()

    portfolio, close, vix, weights = run_backtest(
        symbols=args.symbols,
        start=args.start,
        end=args.end,
        thresholds=args.thresholds,
        initial_cash=args.cash,
        fees=args.fees,
        slippage=args.slippage,
        allocations=args.allocations,
        hysteresis=args.hysteresis,
        vix_ma=args.vix_ma,
    )

    print_metrics(portfolio, weights, args.cash)
    save_results(portfolio, weights, args, close)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[Backtest] 运行失败: {e}", file=sys.stderr)
        sys.exit(1)
