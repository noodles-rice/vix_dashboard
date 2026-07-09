#!/usr/bin/env python3
"""基于 VectorBT 的 VIX 驱动 QQQ/QLD/TQQQ 杠杆轮动回测。

策略逻辑示例（可自定义）：
- VIX < 13：满仓 TQQQ（3 倍）
- 13 <= VIX < 20：满仓 QLD（2 倍）
- 20 <= VIX < 30：满仓 QQQ（1 倍）
- VIX >= 30：空仓

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
DEFAULT_FEES = 0.001  # 0.1% 单边交易费用
DEFAULT_SLIPPAGE = 0.001  # 0.1% 滑点


def fetch_etf_data(symbols, start, end):
    """从 Yahoo Finance 拉取多个 ETF 的日线数据，返回 OHLC DataFrame（wide format）。"""
    print(f"[Backtest] 下载数据: {symbols} ...")
    data = yf.download(
        symbols,
        start=start,
        end=end,
        progress=False,
        auto_adjust=True,
        threads=True,
    )
    if data.empty:
        raise ValueError("未获取到任何数据，请检查网络或标的代码。")

    # yfinance 多资产返回 MultiIndex columns: (field, symbol)
    # 转换为 (symbol, field) 以便后续处理
    close = data["Close"]
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


def build_signals(close, vix, thresholds):
    """根据 VIX 阈值生成每日目标权重矩阵。

    Args:
        close: 收盘价 DataFrame，columns 为资产代码。
        vix: VIX 收盘价 Series。
        thresholds: 阈值元组 (low, mid, high)，默认 (13, 20, 30)。

    Returns:
        weights: DataFrame，与 close 同形，每天只有一个资产权重为 1.0，其余为 0。
    """
    low, mid, high = thresholds

    # 对齐 VIX 与收盘价日期，缺失日期/缺失值均前向填充
    # 注意：VIX 是日终发布，调用方应将信号滞后一日执行以避免前视偏差
    vix_aligned = vix.reindex(close.index, method="ffill").ffill()

    weights = pd.DataFrame(0.0, index=close.index, columns=close.columns)

    # 空仓
    cash = pd.Series(True, index=close.index)

    # VIX < low -> TQQQ
    mask = vix_aligned < low
    if "TQQQ" in weights.columns:
        weights.loc[mask, "TQQQ"] = 1.0
        cash &= ~mask

    # low <= VIX < mid -> QLD
    mask = (vix_aligned >= low) & (vix_aligned < mid)
    if "QLD" in weights.columns:
        weights.loc[mask, "QLD"] = 1.0
        cash &= ~mask

    # mid <= VIX < high -> QQQ
    mask = (vix_aligned >= mid) & (vix_aligned < high)
    if "QQQ" in weights.columns:
        weights.loc[mask, "QQQ"] = 1.0
        cash &= ~mask

    # VIX >= high -> 空仓（默认权重已为 0）
    # 可在这里加入做空或持有货基的逻辑

    # 上市前缺失数据保持空仓
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
    annual_return = (1 + total_return) ** (365 / days) - 1 if days > 0 else 0.0

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


def print_metrics(portfolio, weights):
    """打印回测绩效指标。"""
    metrics = _portfolio_value_metrics(portfolio)
    trades = portfolio.trades

    print("\n" + "=" * 50)
    print("回测绩效")
    print("=" * 50)
    print(f"总收益率:        {metrics['total_return']:.2%}")
    print(f"年化收益率:      {metrics['annual_return']:.2%}")
    print(f"夏普比率:        {metrics['sharpe']:.2f}")
    print(f"最大回撤:        {metrics['max_drawdown']:.2%}")
    print(f"Calmar 比率:     {metrics['calmar']:.2f}")
    print(f"总交易次数:      {int(trades.count().sum())}")
    print(f"胜率:            {float(trades.win_rate().mean()):.2%}")


def save_results(portfolio, weights, args, close):
    """保存回测结果和图表到 output 目录。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"vix_leverage_rotation_{timestamp}"

    value = portfolio.value()
    if isinstance(value, pd.DataFrame):
        # 多列时按行求和得到组合总价值；要求所有列均为数值型
        value = value.sum(axis=1)

    # 买入持有 QQQ 作为基准
    benchmark = close["QQQ"].dropna()
    if benchmark.empty or pd.isna(benchmark.iloc[0]) or benchmark.iloc[0] == 0:
        print("[Backtest] 警告: QQQ 基准数据无效，跳过基准曲线", file=sys.stderr)
        benchmark_value = pd.Series(dtype=float)
    else:
        benchmark_value = args.cash * benchmark / benchmark.iloc[0]

    # 回撤
    cummax = value.cummax()
    drawdown = (value - cummax) / cummax

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
        go.Scatter(x=value.index, y=value, name="轮动策略", line=dict(color="#1f77b4")),
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

    html_path = OUTPUT_DIR / f"{prefix}.html"
    fig.write_html(str(html_path))
    print(f"\n[Backtest] 权益曲线已保存: {html_path}")

    value_metrics = _portfolio_value_metrics(portfolio)
    # 绩效 JSON
    metrics = {
        "symbols": args.symbols,
        "start": str(portfolio.close.index[0].date()),
        "end": str(portfolio.close.index[-1].date()),
        "thresholds": args.thresholds,
        "cash": args.cash,
        "fees": args.fees,
        "slippage": args.slippage,
        "total_return": float(value_metrics["total_return"]),
        "annualized_return": float(value_metrics["annual_return"]),
        "sharpe_ratio": float(value_metrics["sharpe"]),
        "max_drawdown": float(value_metrics["max_drawdown"]),
        "calmar_ratio": float(value_metrics["calmar"]),
        "trade_count": int(portfolio.trades.count().sum()),
        "win_rate": float(portfolio.trades.win_rate().mean()),
    }
    json_path = OUTPUT_DIR / f"{prefix}_metrics.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"[Backtest] 绩效指标已保存: {json_path}")



def parse_args():
    parser = argparse.ArgumentParser(description="VIX 驱动 QQQ/QLD/TQQQ 杠杆轮动回测")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS, help="ETF 标的列表")
    parser.add_argument("--start", default=DEFAULT_START, help="回测起始日期")
    parser.add_argument("--end", default=DEFAULT_END, help="回测结束日期")
    parser.add_argument(
        "--thresholds",
        nargs=3,
        type=float,
        default=[13.0, 20.0, 30.0],
        metavar=("LOW", "MID", "HIGH"),
        help="VIX 阈值，默认 13 20 30，必须满足 low < mid < high",
    )
    parser.add_argument("--cash", type=float, default=DEFAULT_CASH, help="初始资金")
    parser.add_argument("--fees", type=float, default=DEFAULT_FEES, help="单边手续费比例")
    parser.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE, help="滑点比例")
    args = parser.parse_args()
    args.thresholds = tuple(args.thresholds)
    if not (args.thresholds[0] < args.thresholds[1] < args.thresholds[2]):
        parser.error("阈值必须满足 low < mid < high")
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

    print_metrics(portfolio, weights)
    save_results(portfolio, weights, args, close)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[Backtest] 运行失败: {e}", file=sys.stderr)
        sys.exit(1)
