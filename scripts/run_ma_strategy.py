#!/usr/bin/env python3
"""生成 MA150 + VIX<=30 满仓 TQQQ 策略的回测报告。

运行方式:
    source /root/vix/.venv/bin/activate && python scripts/run_ma_strategy.py
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.subplots as sp

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

    # 买入持有基准：将初始价格上调 (1 + fees + slippage) 作为近似开仓成本，
    # 从而在同一坐标轴上与策略净值做粗略对比。该处理为简化估算，不等价于
    # 实际逐笔交易成本。
    benchmark_values = []
    for symbol in ["QQQ", "QLD", "TQQQ"]:
        if symbol not in close.columns:
            continue
        bm = close[symbol].dropna()
        if bm.empty or pd.isna(bm.iloc[0]) or bm.iloc[0] == 0:
            continue
        adjusted_initial = bm.iloc[0] * (1 + fees + slippage)
        benchmark_values.append((symbol, cash * bm / adjusted_initial))

    cummax = chart_value.cummax()
    drawdown = (chart_value - cummax) / cummax

    fig = sp.make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=("组合净值 vs 买入持有 QQQ/QLD/TQQQ", "持仓权重", "回撤"),
        row_heights=[0.5, 0.25, 0.25],
    )

    fig.add_trace(
        go.Scatter(x=chart_value.index, y=chart_value, name="轮动策略", line=dict(color="#1f77b4")),
        row=1,
        col=1,
    )
    for symbol, bm_value in benchmark_values:
        fig.add_trace(
            go.Scatter(
                x=bm_value.index,
                y=bm_value,
                name=f"买入持有 {symbol}",
                line=dict(dash="dash"),
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
        title=f"MA{ma_window} + VIX≤{vix_thr} 满仓 TQQQ 策略回测",
        hovermode="x unified",
        height=900,
        showlegend=True,
    )
    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="权重", row=2, col=1)
    fig.update_yaxes(title_text="回撤 %", row=3, col=1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"ma{ma_window}_vix{vix_thr}_{timestamp}"

    perf_rows = [
        ("策略", f"MA{ma_window} + VIX≤{vix_thr} 满仓 TQQQ"),
        ("回测区间", f"{close.index[0].date()} ~ {close.index[-1].date()}"),
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

    benchmark_rows = [
        (f"买入持有 {symbol}", f"{bm_value.iloc[-1]:,.2f} ({bm_value.iloc[-1]/bm_value.iloc[0]-1:.2%})")
        for symbol, bm_value in benchmark_values
    ]

    def _grid_items(rows):
        return "\n".join(
            f"            <div class='metric-item'><span class='metric-label'>{html.escape(str(label))}</span><span class='metric-value'>{html.escape(str(val))}</span></div>"
            for label, val in rows
        )

    chart_html = fig.to_html(full_html=False, include_plotlyjs="cdn", div_id="backtest-chart")

    page_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MA{ma_window} + VIX≤{vix_thr} 策略回测</title>
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
        <h1>MA{ma_window} + VIX≤{vix_thr} 满仓 TQQQ 策略回测</h1>
        <div class="metrics-panel">
            <h2>策略配置</h2>
            <div class="metrics-grid">
{_grid_items(perf_rows[:2])}
            </div>
        </div>
        <div class="metrics-panel">
            <h2>回测绩效</h2>
            <div class="metrics-grid">
{_grid_items(perf_rows[2:])}
            </div>
        </div>
        <div class="metrics-panel">
            <h2>买入持有基准</h2>
            <div class="metrics-grid">
{_grid_items(benchmark_rows)}
            </div>
        </div>
        <div class="chart-panel">
            {chart_html}
        </div>
    </div>
</body>
</html>"""

    html_path = OUTPUT_DIR / f"{prefix}.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page_html)

    json_path = OUTPUT_DIR / f"{prefix}_metrics.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
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
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"[RunMA] HTML 报告: {html_path}")
    print(f"[RunMA] JSON 指标: {json_path}")
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
