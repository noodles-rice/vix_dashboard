#!/usr/bin/env python3
"""基于 VectorBT 的 VIX 驱动 QQQ/QLD/TQQQ 杠杆轮动回测。

策略逻辑示例（可自定义）：
- VIX < 13：半仓 QQQ（0.5 倍）
- 13 <= VIX < 20：满仓 QQQ（1 倍）
- 20 < VIX <= 30：半仓 QLD + 半仓 QQQ（1.5 倍）
- 30 < VIX <= 40：满仓 QLD（2 倍）
- VIX > 40：半仓 QLD + 半仓 TQQQ（2.5 倍）

标的缺失时按以下链条回退：TQQQ -> QLD -> QQQ -> 空仓，QLD -> QQQ -> 空仓。

运行方式：
    source /root/vix/.venv/bin/activate && python scripts/backtest.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.subplots as sp
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

    优先使用本地 data/VIX_History.csv，若不存在则通过 yfinance 拉取 ^VIX。
    """
    local_vix = DATA_DIR / "VIX_History.csv"
    if local_vix.exists():
        print("[Backtest] 使用本地 VIX_History.csv ...")
        df = pd.read_csv(local_vix)
        df["DATE"] = pd.to_datetime(df["DATE"], format="%m/%d/%Y")
        df = df.set_index("DATE").sort_index()
        vix = df["CLOSE"].rename("VIX")
        if start is not None:
            vix = vix[vix.index >= pd.Timestamp(start)]
        if end is not None:
            vix = vix[vix.index < pd.Timestamp(end)]
        return vix

    print("[Backtest] 本地 VIX 数据不存在，从 Yahoo Finance 下载 ^VIX ...")
    vix = yf.download(DEFAULT_VIX_SYMBOL, start=start, end=end, progress=False, auto_adjust=True)["Close"]
    return vix.squeeze().rename("VIX")


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


def build_signals(close, vix, thresholds):
    """根据 VIX 阈值生成每日目标权重矩阵（支持混合仓位与标的回退）。

    仓位规则（默认阈值 13/20/30/40）：
    - VIX < 13：半仓 QQQ
    - 13 <= VIX < 20：满仓 QQQ
    - 20 < VIX <= 30：半仓 QLD + 半仓 QQQ
    - 30 < VIX <= 40：满仓 QLD
    - VIX > 40：半仓 QLD + 半仓 TQQQ

    标的缺失时按以下链条回退：
    - TQQQ 缺失 -> QLD -> QQQ -> 空仓
    - QLD 缺失  -> QQQ -> 空仓
    - QQQ 缺失  -> 空仓

    Args:
        close: 收盘价 DataFrame，columns 为资产代码。
        vix: VIX 收盘价 Series。
        thresholds: 阈值元组 (low, mid1, mid2, high)，默认 (13, 20, 30, 40)。

    Returns:
        weights: DataFrame，与 close 同形，每日权重和为 0.5 或 1.0，其余为 0。
    """
    low, mid1, mid2, high = thresholds

    # 对齐 VIX 与收盘价日期，缺失日期/缺失值均前向填充
    # 注意：VIX 是日终发布，调用方应将信号滞后一日执行以避免前视偏差
    vix_aligned = vix.reindex(close.index, method="ffill").ffill()

    weights = pd.DataFrame(0.0, index=close.index, columns=close.columns)

    for i, date in enumerate(close.index):
        v = vix_aligned.iloc[i]
        # VIX 缺失时保持空仓，避免 NaN 比较落入 else 分支导致激进仓位
        if pd.isna(v):
            continue
        close_row = close.loc[date]

        if v < low:
            allocations = [("QQQ", 0.5)]
        elif v < mid1:
            allocations = [("QQQ", 1.0)]
        elif v <= mid2:
            allocations = [("QLD", 0.5), ("QQQ", 0.5)]
        elif v <= high:
            allocations = [("QLD", 1.0)]
        else:
            allocations = [("QLD", 0.5), ("TQQQ", 0.5)]

        for asset, weight in allocations:
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
):
    """执行回测并返回 Portfolio 对象。

    Args:
        close: 预拉取的收盘价 DataFrame。为 None 时自动下载。
        vix: 预拉取的 VIX Series。为 None 时自动下载。
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

    weights = build_signals(close, vix, thresholds)
    # VIX 收盘后发布，当日信号次日执行，避免前视偏差
    weights = weights.shift(1).fillna(0.0)

    print(f"[Backtest] 回测区间: {close.index[0].date()} ~ {close.index[-1].date()}")
    print(f"[Backtest] 资产数量: {len(close.columns)}")
    print(f"[Backtest] VIX 阈值: {thresholds}")

    portfolio = vbt.Portfolio.from_orders(
        close=close,
        size=weights,
        size_type="targetpercent",
        fees=fees,
        slippage=slippage,
        freq="1d",
        init_cash=initial_cash,
    )

    return portfolio, close, vix, weights


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

    cummax = value.cummax()
    drawdown = (value - cummax) / cummax
    max_dd = drawdown.min()

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

    # 买入持有基准
    benchmark_symbol = getattr(args, "benchmark", "QQQ")
    if benchmark_symbol not in close.columns:
        print(
            f"[Backtest] 警告: 基准 {benchmark_symbol} 不在回测标的中，跳过基准曲线",
            file=sys.stderr,
        )
        benchmark_value = pd.Series(dtype=float)
    else:
        benchmark = close[benchmark_symbol].dropna()
        if benchmark.empty or pd.isna(benchmark.iloc[0]) or benchmark.iloc[0] == 0:
            print(
                f"[Backtest] 警告: 基准 {benchmark_symbol} 数据无效，跳过基准曲线",
                file=sys.stderr,
            )
            benchmark_value = pd.Series(dtype=float)
        else:
            benchmark_value = args.cash * benchmark / benchmark.iloc[0]

    # 回撤（基于缩放后的净值计算，比率不变）
    cummax = chart_value.cummax()
    drawdown = (chart_value - cummax) / cummax

    # 构建组合图表
    fig = sp.make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=("组合净值 vs 买入持有 QQQ", "持仓权重", "回撤"),
        row_heights=[0.5, 0.25, 0.25],
    )

    fig.add_trace(
        go.Scatter(x=chart_value.index, y=chart_value, name="轮动策略", line=dict(color="#1f77b4")),
        row=1,
        col=1,
    )
    if not benchmark_value.empty:
        fig.add_trace(
            go.Scatter(
                x=benchmark_value.index,
                y=benchmark_value,
                name="买入持有 QQQ",
                line=dict(color="#9467bd", dash="dash"),
            ),
            row=1,
            col=1,
        )

    for col in weights.columns:
        fig.add_trace(
            go.Scatter(
                x=weights.index,
                y=weights[col],
                name=f"权重 {col}",
                stackgroup="weights",
                line=dict(width=0.5),
            ),
            row=2,
            col=1,
        )

    fig.add_trace(
        go.Scatter(
            x=drawdown.index,
            y=drawdown * 100,
            name="回撤 %",
            fill="tozeroy",
            line=dict(color="#d62728"),
        ),
        row=3,
        col=1,
    )

    fig.update_layout(
        title="VIX 驱动 QQQ/QLD/TQQQ 杠杆轮动回测",
        hovermode="x unified",
        height=900,
        showlegend=True,
    )
    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="权重", row=2, col=1)
    fig.update_yaxes(title_text="回撤 %", row=3, col=1)

    metrics = {
        "symbols": args.symbols,
        "start": str(portfolio.close.index[0].date()),
        "end": str(portfolio.close.index[-1].date()),
        "thresholds": args.thresholds,
        "cash": args.cash,
        "fees": args.fees,
        "slippage": args.slippage,
        "initial_value": initial_value,
        "final_value": final_value,
        "total_return": float(value_metrics["total_return"]),
        "annualized_return": float(value_metrics["annual_return"]),
        "sharpe_ratio": float(value_metrics["sharpe"]),
        "max_drawdown": float(value_metrics["max_drawdown"]),
        "calmar_ratio": float(value_metrics["calmar"]),
        "trade_count": int(portfolio.trades.count().sum()),
        "win_rate": float(portfolio.trades.win_rate().mean()),
    }

    html_path = OUTPUT_DIR / f"{prefix}.html"
    _write_backtest_html(html_path, fig, metrics)
    print(f"\n[Backtest] 权益曲线已保存: {html_path}")

    json_path = OUTPUT_DIR / f"{prefix}_metrics.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"[Backtest] 绩效指标已保存: {json_path}")


def _write_backtest_html(html_path, fig, metrics):
    """将 Plotly 图表与紧凑绩效表格组合成完整 HTML 页面。"""
    rows = [
        ("回测区间", f"{metrics['start']} ~ {metrics['end']}"),
        ("初始持仓", f"{metrics['initial_value']:,.2f}"),
        ("期末持仓", f"{metrics['final_value']:,.2f}"),
        ("总收益率", f"{metrics['total_return']:.2%}"),
        ("年化收益率", f"{metrics['annualized_return']:.2%}"),
        ("夏普比率", f"{metrics['sharpe_ratio']:.2f}"),
        ("最大回撤", f"{metrics['max_drawdown']:.2%}"),
        ("Calmar 比率", f"{metrics['calmar_ratio']:.2f}"),
        ("总交易次数", f"{metrics['trade_count']}"),
        ("胜率", f"{metrics['win_rate']:.2%}"),
        ("手续费率", f"{metrics['fees']:.2%}"),
        ("滑点率", f"{metrics['slippage']:.2%}"),
    ]

    grid_items = "\n".join(
        f"            <div class='metric-item'><span class='metric-label'>{label}</span><span class='metric-value'>{val}</span></div>"
        for label, val in rows
    )

    chart_html = fig.to_html(full_html=False, include_plotlyjs=True, div_id="backtest-chart")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VIX 杠杆轮动回测结果</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            margin: 0;
            padding: 16px;
            background: #f8f9fa;
            color: #1f2937;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        h1 {{
            font-size: 20px;
            margin: 0 0 12px 0;
            color: #111827;
        }}
        .metrics-panel {{
            background: #fff;
            border-radius: 10px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            padding: 14px 16px;
            margin-bottom: 16px;
        }}
        .metrics-panel h2 {{
            font-size: 15px;
            margin: 0 0 10px 0;
            color: #374151;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 0 24px;
        }}
        .metric-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 5px 0;
            border-bottom: 1px solid #f3f4f6;
            font-size: 13px;
        }}
        .metric-label {{
            color: #6b7280;
            font-weight: 500;
            margin-right: 12px;
            white-space: nowrap;
        }}
        .metric-value {{
            color: #111827;
            font-weight: 600;
            text-align: right;
            white-space: nowrap;
        }}
        .chart-panel {{
            background: #fff;
            border-radius: 10px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            padding: 12px;
        }}
        @media (max-width: 480px) {{
            .metrics-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>VIX 驱动 QQQ/QLD/TQQQ 杠杆轮动回测</h1>
        <div class="metrics-panel">
            <h2>回测绩效</h2>
            <div class="metrics-grid">
{grid_items}
            </div>
        </div>
        <div class="chart-panel">
            {chart_html}
        </div>
    </div>
</body>
</html>"""

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)



def parse_args():
    parser = argparse.ArgumentParser(description="VIX 驱动 QQQ/QLD/TQQQ 杠杆轮动回测")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS, help="ETF 标的列表")
    parser.add_argument("--start", default=DEFAULT_START, help="回测起始日期")
    parser.add_argument("--end", default=DEFAULT_END, help="回测结束日期")
    parser.add_argument(
        "--thresholds",
        nargs=4,
        type=float,
        default=[13.0, 20.0, 30.0, 40.0],
        metavar=("LOW", "MID1", "MID2", "HIGH"),
        help="VIX 阈值，默认 13 20 30 40，必须满足 low < mid1 < mid2 < high",
    )
    parser.add_argument("--cash", type=float, default=DEFAULT_CASH, help="初始资金")
    parser.add_argument("--fees", type=float, default=DEFAULT_FEES, help="单边手续费比例")
    parser.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE, help="滑点比例")
    parser.add_argument(
        "--benchmark",
        type=str,
        default="QQQ",
        help="买入持有基准标的，默认 QQQ",
    )
    args = parser.parse_args()
    args.thresholds = tuple(args.thresholds)
    if not (args.thresholds[0] < args.thresholds[1] < args.thresholds[2] < args.thresholds[3]):
        parser.error("阈值必须满足 low < mid1 < mid2 < high")
    if args.cash <= 0:
        parser.error("初始资金必须大于 0")
    if args.fees < 0 or args.slippage < 0:
        parser.error("手续费率和滑点率不能为负数")
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
    )

    print_metrics(portfolio, weights, args.cash)
    save_results(portfolio, weights, args, close)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[Backtest] 运行失败: {e}", file=sys.stderr)
        sys.exit(1)
