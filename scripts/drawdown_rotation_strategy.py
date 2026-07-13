#!/usr/bin/env python3
"""回撤驱动 QQQ/QLD/TQQQ 杠杆轮动策略回测。

策略逻辑：
- 初始满仓 QQQ。
- 以组合持仓市值高点（High Water Mark）为锚，跟踪回撤。
- 回撤加深时单向加杠杆（只加不减）：
    max_dd < 10%      -> 100% QQQ
    10% <= max_dd < 15% -> 80% QQQ + 20% QLD
    15% <= max_dd < 20% -> 50% QQQ + 50% QLD
    20% <= max_dd < 25% -> 100% QLD
    25% <= max_dd < 30% -> 80% QLD + 20% TQQQ
    30% <= max_dd < 35% -> 60% QLD + 40% TQQQ
    35% <= max_dd < 40% -> 40% QLD + 60% TQQQ
    40% <= max_dd < 45% -> 20% QLD + 80% TQQQ
    max_dd >= 45%      -> 100% TQQQ
- 当组合从回撤中修复时，维持已达成的最高杠杆档位（单向棘轮）。

运行方式:
    source /root/vix/.venv/bin/activate && python scripts/drawdown_rotation_strategy.py
"""

from __future__ import annotations

import argparse
import html
import json
import math
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
)

import vectorbt as vbt


def _allocation_for_max_dd(max_dd: float) -> dict[str, float]:
    """根据历史最大回撤返回目标资产配置（权重和不超过 1.0）。"""
    if max_dd < 0.10:
        return {"QQQ": 1.0}
    if max_dd < 0.15:
        return {"QQQ": 0.8, "QLD": 0.2}
    if max_dd < 0.20:
        return {"QQQ": 0.5, "QLD": 0.5}
    if max_dd < 0.25:
        return {"QLD": 1.0}
    if max_dd < 0.30:
        return {"QLD": 0.8, "TQQQ": 0.2}
    if max_dd < 0.35:
        return {"QLD": 0.6, "TQQQ": 0.4}
    if max_dd < 0.40:
        return {"QLD": 0.4, "TQQQ": 0.6}
    if max_dd < 0.45:
        return {"QLD": 0.2, "TQQQ": 0.8}
    return {"TQQQ": 1.0}


def build_drawdown_weights(close: pd.DataFrame) -> pd.DataFrame:
    """构造回撤驱动策略的每日目标权重。

    第 0 天初始权重为 100% QQQ。后续每一天的权重基于截至上一交易日收盘
    所达到过的最大回撤确定，避免前视偏差。组合净值与 HWM 通过独立迭代
    模拟，用于生成信号；实际净值由 vectorbt 根据目标权重与交易成本计算。
    """
    weights = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    portfolio_value = 1.0
    hwm = 1.0
    max_dd = 0.0
    prev_target: dict[str, float] | None = None

    for i, date in enumerate(close.index):
        target = _allocation_for_max_dd(max_dd)
        for asset, w in target.items():
            if asset in weights.columns:
                weights.loc[date, asset] = w

        if i > 0 and prev_target is not None:
            prev_date = close.index[i - 1]
            returns = close.loc[date] / close.loc[prev_date] - 1
            # 缺失或异常价格视为 0 收益（稳健兜底，同时覆盖 NaN 与 Inf）
            returns = returns.replace([float('inf'), float('-inf')], 0.0).fillna(0.0)
            port_return = sum(
                prev_target.get(asset, 0.0) * returns.get(asset, 0.0)
                for asset in close.columns
            )
            portfolio_value *= 1.0 + port_return
            hwm = max(hwm, portfolio_value)
            current_dd = (hwm - portfolio_value) / hwm if math.isfinite(hwm) and hwm > 0 else 0.0
            max_dd = max(max_dd, current_dd)

        prev_target = target

    return weights


def run_and_report(start: str, end: str, cash: float, fees: float, slippage: float):
    """执行回测并保存 HTML/JSON 报告。"""
    BASE_DIR = Path(__file__).resolve().parent.parent
    OUTPUT_DIR = BASE_DIR / "output"
    OUTPUT_DIR.mkdir(exist_ok=True)

    close = fetch_etf_data(DEFAULT_SYMBOLS, start, end)
    if isinstance(close, pd.Series):
        close = close.to_frame()
    close.columns = [str(c).upper() for c in close.columns]

    weights = build_drawdown_weights(close)

    portfolio = vbt.Portfolio.from_orders(
        close=close,
        size=weights,
        size_type="targetpercent",
        fees=fees,
        slippage=slippage,
        freq="1d",
        init_cash=cash,
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
        title="回撤驱动 QQQ/QLD/TQQQ 杠杆轮动策略回测",
        hovermode="x unified",
        height=900,
        showlegend=True,
    )
    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="权重", row=2, col=1)
    fig.update_yaxes(title_text="回撤 %", row=3, col=1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"drawdown_rotation_{timestamp}"

    perf_rows = [
        ("策略", "回撤驱动杠杆轮动"),
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
    <title>回撤驱动杠杆轮动策略回测</title>
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
        <h1>回撤驱动杠杆轮动策略回测</h1>
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
                "strategy": "回撤驱动杠杆轮动",
                "start": str(close.index[0].date()),
                "end": str(close.index[-1].date()),
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

    print(f"[DrawdownRotation] HTML 报告: {html_path}")
    print(f"[DrawdownRotation] JSON 指标: {json_path}")
    print(f"[DrawdownRotation] 总收益: {metrics['total_return']:.2%}, 最大回撤: {metrics['max_drawdown']:.2%}")


def main():
    parser = argparse.ArgumentParser(description="回撤驱动杠杆轮动策略回测")
    parser.add_argument("--start", default="2006-07-01", help="回测起始日期")
    parser.add_argument("--end", default="2026-07-10", help="回测结束日期")
    parser.add_argument("--cash", type=float, default=DEFAULT_CASH, help="初始资金")
    parser.add_argument("--fees", type=float, default=DEFAULT_FEES, help="单边手续费比例")
    parser.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE, help="滑点比例")
    args = parser.parse_args()

    run_and_report(args.start, args.end, args.cash, args.fees, args.slippage)


if __name__ == "__main__":
    main()
