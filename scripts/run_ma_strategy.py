#!/usr/bin/env python3
"""生成 MA150 + VIX<=30 满仓 TQQQ 策略的回测报告。

运行方式:
    source /root/vix/.venv/bin/activate && python scripts/run_ma_strategy.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest import (
    DEFAULT_CASH,
    DEFAULT_FEES,
    DEFAULT_SLIPPAGE,
    DEFAULT_SYMBOLS,
    _portfolio_value_metrics,
    fetch_etf_data,
    fetch_vix_data,
)

import vectorbt as vbt


def build_ma_vix_weights(close, vix, ma_window: int, vix_thr: float):
    """构造 MA + VIX 策略权重：价格在 MA 上方且 VIX <= thr 时满仓 TQQQ，否则空仓。"""
    qqq = close["QQQ"]
    ma = qqq.rolling(ma_window).mean()
    above_ma = qqq > ma
    signal = above_ma & (vix <= vix_thr)

    weights = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    weights.loc[signal, "TQQQ"] = 1.0
    return weights


def run_and_report(ma_window: int, vix_thr: float, start: str, end: str):
    """执行回测并保存 HTML/JSON 报告。"""
    BASE_DIR = Path(__file__).resolve().parent.parent
    OUTPUT_DIR = BASE_DIR / "output"
    OUTPUT_DIR.mkdir(exist_ok=True)

    cash = DEFAULT_CASH
    fees = DEFAULT_FEES
    slippage = DEFAULT_SLIPPAGE

    close = fetch_etf_data(DEFAULT_SYMBOLS, start, end)
    vix = fetch_vix_data(start, end)
    common = close.index.intersection(vix.index)
    close = close.loc[common]
    vix = vix.loc[common]

    weights = build_ma_vix_weights(close, vix, ma_window, vix_thr)
    weights_exec = weights.shift(1).fillna(0.0)

    portfolio = vbt.Portfolio.from_orders(
        close=close,
        size=weights_exec,
        size_type="targetpercent",
        fees=fees,
        slippage=slippage,
        freq="1d",
        init_cash=cash,
        # 与 backtest.py 保持一致：多资产 targetpercent 必须共享现金
        cash_sharing=True,
    )

    metrics = _portfolio_value_metrics(portfolio)
    initial_value = float(cash)
    final_value = float(cash * (1 + metrics["total_return"]))

    value = portfolio.value()
    first_value = float(value.iloc[0])
    if first_value == 0:
        raise ValueError("组合首日价值为 0，请检查输入数据、初始资金或权重设置")
    scale = cash / first_value
    chart_value = value * scale

    import report_utils

    benchmark_values = report_utils.build_benchmark_values(
        close, cash, fees, slippage, ["QQQ", "QLD", "TQQQ"]
    )

    fig = report_utils.build_plotly_chart(
        chart_value, weights, benchmark_values,
        f"MA{ma_window} + VIX≤{vix_thr} 满仓 TQQQ 策略回测"
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"ma{ma_window}_vix{vix_thr}_{timestamp}"
    title = f"MA{ma_window} + VIX≤{vix_thr} 满仓 TQQQ 策略回测"

    config_rows = [
        ("策略", f"MA{ma_window} + VIX≤{vix_thr} 满仓 TQQQ"),
        ("回测区间", f"{close.index[0].date()} ~ {close.index[-1].date()}"),
    ]
    perf_rows = [
        ("初始资金", f"{initial_value:,.2f}"),
        ("期末持仓", f"{final_value:,.2f}"),
        ("总收益率", f"{metrics['total_return']:.2%}"),
        ("年化收益率", f"{metrics['annual_return']:.2%}"),
        ("夏普比率", f"{metrics['sharpe']:.2f}"),
        ("最大回撤", f"{metrics['max_drawdown']:.2%}"),
        ("Calmar 比率", f"{metrics['calmar']:.2f}"),
        ("总交易次数", int(portfolio.trades.count().sum())),
        ("胜率", f"{float(portfolio.trades.win_rate().mean()):.2%}"),
    ]

    benchmark_rows = []
    for symbol, bm_value in benchmark_values:
        final = bm_value.iloc[-1]
        total_ret = final / bm_value.iloc[0] - 1
        max_dd = report_utils.max_drawdown_from_series(bm_value)
        benchmark_rows.append((f"买入持有 {symbol}", f"{final:,.2f} ({total_ret:.2%})"))
        benchmark_rows.append((f"{symbol} 最大回撤", f"{max_dd:.2%}"))

    panels = [
        ("策略配置", report_utils.grid_items(config_rows)),
        ("回测绩效", report_utils.grid_items(perf_rows)),
        ("买入持有基准", report_utils.grid_items(benchmark_rows)),
    ]

    chart_html = fig.to_html(full_html=False, include_plotlyjs="cdn", div_id="backtest-chart")

    html_path = report_utils.OUTPUT_DIR / f"{prefix}.html"
    report_utils.write_html_report(html_path, title, panels, chart_html)

    json_path = report_utils.OUTPUT_DIR / f"{prefix}_metrics.json"
    report_utils.write_json_report(json_path, {
        "strategy": f"MA{ma_window} + VIX≤{vix_thr} 满仓 TQQQ",
        "start": str(close.index[0].date()),
        "end": str(close.index[-1].date()),
        "ma_window": ma_window,
        "vix_threshold": vix_thr,
        "initial_value": initial_value,
        "final_value": final_value,
        "total_return": float(metrics["total_return"]),
        "annualized_return": float(metrics["annual_return"]),
        "sharpe_ratio": float(metrics["sharpe"]),
        "max_drawdown": float(metrics["max_drawdown"]),
        "calmar_ratio": float(metrics["calmar"]),
        "trade_count": int(portfolio.trades.count().sum()),
        "win_rate": float(portfolio.trades.win_rate().mean()),
    })

    print(f"[RunMA] 总收益: {metrics['total_return']:.2%}, 最大回撤: {metrics['max_drawdown']:.2%}")


def main():
    parser = argparse.ArgumentParser(description="MA + VIX 策略回测报告生成")
    parser.add_argument("--ma", type=int, default=150, help="移动均线周期")
    parser.add_argument("--vix", type=float, default=30.0, help="VIX 阈值")
    parser.add_argument("--start", default="2015-01-02", help="回测起始日期")
    parser.add_argument("--end", default="2026-07-07", help="回测结束日期")
    args = parser.parse_args()
    run_and_report(args.ma, args.vix, args.start, args.end)


if __name__ == "__main__":
    main()
