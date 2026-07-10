#!/usr/bin/env python3
"""VIX 阈值参数扫描示例。

扫描不同的 VIX 阈值组合，比较策略绩效，输出最佳组合。

运行方式：
    source /root/vix/.venv/bin/activate && python scripts/optimize.py
"""

from __future__ import annotations

import argparse
import itertools
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# 将 scripts 目录加入路径以复用 backtest 模块
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest import (
    DEFAULT_CASH,
    DEFAULT_END,
    DEFAULT_FEES,
    DEFAULT_SLIPPAGE,
    DEFAULT_START,
    DEFAULT_SYMBOLS,
    _portfolio_value_metrics,
    fetch_etf_data,
    fetch_vix_data,
    run_backtest,
)

# 默认阈值扫描范围，必须满足 low < mid1 < mid2 < high
DEFAULT_LOW_VALUES = [10.0, 13.0, 15.0]
DEFAULT_MID1_VALUES = [17.0, 20.0, 22.0]
DEFAULT_MID2_VALUES = [25.0, 30.0, 35.0]
DEFAULT_HIGH_VALUES = [35.0, 40.0, 45.0]


def scan_thresholds(
    symbols,
    start,
    end,
    low_values,
    mid1_values,
    mid2_values,
    high_values,
    initial_cash,
    fees,
    slippage,
    close,
    vix,
):
    """遍历所有满足 low < mid1 < mid2 < high 的阈值组合，返回结果 DataFrame。

    close 和 vix 由调用方一次性拉取，避免重复网络请求。
    """
    results = []

    for low, mid1, mid2, high in itertools.product(
        low_values, mid1_values, mid2_values, high_values
    ):
        if not (low < mid1 < mid2 < high):
            continue

        thresholds = (low, mid1, mid2, high)
        try:
            portfolio, _, _, _ = run_backtest(
                symbols=symbols,
                start=start,
                end=end,
                thresholds=thresholds,
                initial_cash=initial_cash,
                fees=fees,
                slippage=slippage,
                close=close,
                vix=vix,
            )
            metrics = _portfolio_value_metrics(portfolio)
            results.append(
                {
                    "low": low,
                    "mid1": mid1,
                    "mid2": mid2,
                    "high": high,
                    "total_return": metrics["total_return"],
                    "annual_return": metrics["annual_return"],
                    "sharpe": metrics["sharpe"],
                    "max_drawdown": metrics["max_drawdown"],
                    "calmar": metrics["calmar"],
                    "trade_count": int(portfolio.trades.count().sum()),
                }
            )
        except Exception as e:
            print(f"[Optimize] 阈值 {thresholds} 回测失败: {e}", file=sys.stderr)

    return pd.DataFrame(results)


def main():
    parser = argparse.ArgumentParser(description="VIX 阈值参数扫描")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS, help="ETF 标的列表")
    parser.add_argument("--start", default=DEFAULT_START, help="回测起始日期")
    parser.add_argument("--end", default=DEFAULT_END, help="回测结束日期")
    parser.add_argument(
        "--low", nargs="+", type=float, default=DEFAULT_LOW_VALUES, help="低阈值候选值"
    )
    parser.add_argument(
        "--mid1", nargs="+", type=float, default=DEFAULT_MID1_VALUES, help="第一中阈值候选值"
    )
    parser.add_argument(
        "--mid2", nargs="+", type=float, default=DEFAULT_MID2_VALUES, help="第二中阈值候选值"
    )
    parser.add_argument(
        "--high", nargs="+", type=float, default=DEFAULT_HIGH_VALUES, help="高阈值候选值"
    )
    parser.add_argument("--cash", type=float, default=DEFAULT_CASH, help="初始资金")
    parser.add_argument("--fees", type=float, default=DEFAULT_FEES, help="单边手续费比例")
    parser.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE, help="滑点比例")
    parser.add_argument(
        "--metric",
        default="calmar",
        choices=["total_return", "annual_return", "sharpe", "calmar"],
        help="用于排序选择最佳组合的指标",
    )
    args = parser.parse_args()

    print("[Optimize] 开始参数扫描...")
    close = fetch_etf_data(args.symbols, args.start, args.end)
    vix = fetch_vix_data(args.start, args.end)

    df = scan_thresholds(
        symbols=args.symbols,
        start=args.start,
        end=args.end,
        low_values=args.low,
        mid1_values=args.mid1,
        mid2_values=args.mid2,
        high_values=args.high,
        initial_cash=args.cash,
        fees=args.fees,
        slippage=args.slippage,
        close=close,
        vix=vix,
    )

    if df.empty:
        print("[Optimize] 没有有效的参数组合。", file=sys.stderr)
        sys.exit(1)

    # 输出前 10 名
    top = df.sort_values(args.metric, ascending=False).head(10)
    print(f"\n[Optimize] 按 '{args.metric}' 排序的前 10 组阈值：")
    print(top.to_string(index=False))

    # 保存完整结果
    output_dir = Path(__file__).resolve().parent.parent / "output"
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"vix_threshold_scan_{timestamp}.csv"
    df.to_csv(csv_path, index=False)
    print(f"\n[Optimize] 完整扫描结果已保存: {csv_path}")

    best = top.iloc[0]
    print(
        f"\n[Optimize] 最佳组合: low={best['low']}, mid1={best['mid1']}, "
        f"mid2={best['mid2']}, high={best['high']}"
    )
    print(f"  总收益率:   {best['total_return']:.2%}")
    print(f"  年化收益:   {best['annual_return']:.2%}")
    print(f"  夏普比率:   {best['sharpe']:.2f}")
    print(f"  最大回撤:   {best['max_drawdown']:.2%}")
    print(f"  Calmar:     {best['calmar']:.2f}")
    print(f"  交易次数:   {int(best['trade_count'])}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[Optimize] 运行失败: {e}", file=sys.stderr)
        sys.exit(1)
