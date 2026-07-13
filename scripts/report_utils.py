#!/usr/bin/env python3
"""回测 HTML/JSON 报告生成公共模块。

所有回测脚本（backtest.py、drawdown_rotation_strategy.py、run_ma_strategy.py）
统一通过本模块输出格式一致的 HTML 报告和 JSON 指标文件。
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.subplots as sp

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def max_drawdown_from_series(value):
    """根据价值序列计算最大回撤（返回负数，如 -0.2 表示 -20%）。"""
    cummax = value.cummax()
    drawdown = (value - cummax) / cummax
    return drawdown.min()


def grid_items(rows):
    """将 (标签, 值) 列表渲染为指标网格的 HTML。"""
    return "\n".join(
        f"            <div class='metric-item'><span class='metric-label'>{html.escape(str(label))}</span><span class='metric-value'>{html.escape(str(val))}</span></div>"
        for label, val in rows
    )


def build_benchmark_values(close, cash, fees, slippage, symbols):
    """计算买入持有基准净值序列。

    将初始价格上调 (1 + fees + slippage) 作为近似开仓成本，使基准与
    策略在交易成本口径上尽可能可比。

    Returns:
        list of (symbol, pd.Series): 每个标的名与其缩放后净值序列。
    """
    benchmark_values = []
    for symbol in symbols:
        if symbol not in close.columns:
            continue
        bm = close[symbol].dropna()
        if bm.empty or pd.isna(bm.iloc[0]) or bm.iloc[0] == 0:
            continue
        adjusted_initial = bm.iloc[0] * (1 + fees + slippage)
        benchmark_values.append((symbol, cash * bm / adjusted_initial))
    return benchmark_values


def build_plotly_chart(chart_value, weights, benchmark_values, title, height=900):
    """构建 3 行 Plotly 子图：净值 + 权重 + 回撤。

    Args:
        chart_value: 缩放后的策略净值 Series。
        weights: 每日权重 DataFrame（columns 为资产代码）。
        benchmark_values: build_benchmark_values() 的返回值。
        title: 图表标题。
        height: 图表总高度（默认 900px）。

    Returns:
        plotly.graph_objects.Figure。
    """
    cummax = chart_value.cummax()
    drawdown = (chart_value - cummax) / cummax

    fig = sp.make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=("组合净值 vs 买入持有", "持仓权重", "回撤"),
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
        title=title,
        hovermode="x unified",
        height=height,
        showlegend=True,
    )
    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="权重", row=2, col=1)
    fig.update_yaxes(title_text="回撤 %", row=3, col=1)

    return fig


def write_json_report(json_path, metrics_dict):
    """写入 JSON 指标文件。"""
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metrics_dict, f, ensure_ascii=False, indent=2)
    print(f"  绩效指标已保存: {json_path}")


# ---------------------------------------------------------------------------
# 统一 HTML 报告模板（CSS + 面板骨架）
# ---------------------------------------------------------------------------

_REPORT_CSS = """\
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            margin: 0;
            padding: 16px;
            background: #f8f9fa;
            color: #1f2937;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        h1 {
            font-size: 20px;
            margin: 0 0 12px 0;
            color: #111827;
        }
        .metrics-panel {
            background: #fff;
            border-radius: 10px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            padding: 14px 16px;
            margin-bottom: 16px;
        }
        .metrics-panel h2 {
            font-size: 15px;
            margin: 0 0 10px 0;
            color: #374151;
        }
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 0 24px;
        }
        .metric-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 5px 0;
            border-bottom: 1px solid #f3f4f6;
            font-size: 13px;
        }
        .metric-label {
            color: #6b7280;
            font-weight: 500;
            margin-right: 12px;
            white-space: nowrap;
        }
        .metric-value {
            color: #111827;
            font-weight: 600;
            text-align: right;
            white-space: nowrap;
        }
        .alloc-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        .alloc-table th,
        .alloc-table td {
            padding: 6px 8px;
            text-align: left;
            border-bottom: 1px solid #f3f4f6;
        }
        .alloc-table th {
            color: #6b7280;
            font-weight: 500;
            background: #f9fafb;
        }
        .alloc-regime {
            color: #374151;
            font-weight: 500;
            white-space: nowrap;
            width: 40%;
        }
        .alloc-holdings {
            color: #111827;
            font-weight: 600;
        }
        .chart-panel {
            background: #fff;
            border-radius: 10px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            padding: 12px;
        }
        @media (max-width: 480px) {
            .metrics-grid { grid-template-columns: 1fr; }
            .alloc-regime { white-space: normal; }
        }"""


def _render_panel(heading, body_html):
    """渲染单个指标面板的 HTML。"""
    return f"""        <div class="metrics-panel">
            <h2>{html.escape(heading)}</h2>
            {body_html}
        </div>"""


def write_html_report(html_path, title, panels, chart_html,
                      chart_div_id="backtest-chart",
                      plotly_js="cdn",
                      extra_head=""):
    """生成统一格式的回测 HTML 报告。

    Args:
        html_path: 输出 HTML 文件路径。
        title: 页面标题（同时用于 <title> 和 <h1>，也用于 Plotly 图表标题）。
        panels: list of (heading, body_html) — 指标面板列表。
            body_html 为面板内容 HTML（不含 <h2>），可为空字符串跳过该面板。
        chart_html: Plotly 图表的 HTML 片段（由 fig.to_html() 生成）。
        chart_div_id: 图表 div 的 id（默认 "backtest-chart"）。
        plotly_js: 传递给 fig.to_html() 的 include_plotlyjs 参数，
            "cdn"（默认）或 True（内联）。
        extra_head: 额外的 <head> 内容（可选）。
    """
    panel_html = "\n".join(
        _render_panel(heading, body) for heading, body in panels if body
    )

    page_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(title)}</title>
    <style>
{_REPORT_CSS}
    </style>
    {extra_head}
</head>
<body>
    <div class="container">
        <h1>{html.escape(title)}</h1>
{panel_html}
        <div class="chart-panel">
            {chart_html}
        </div>
    </div>
</body>
</html>"""

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page_html)
    print(f"  HTML 报告已保存: {html_path}")
