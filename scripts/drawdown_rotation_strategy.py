#!/usr/bin/env python3
"""回撤驱动 QQQ/QLD/TQQQ 杠杆轮动策略回测。

策略逻辑：
- 初始持仓 50% QQQ + 50% QLD（有效杠杆约 1.5x）。
- 以组合持仓市值高点（High Water Mark）为锚，跟踪回撤。
- 回撤加深时逐步增加杠杆以博取反弹：
    max_dd < 8%       -> 50% QQQ + 50% QLD
     8% <= max_dd < 14% -> 20% QQQ + 80% QLD
    14% <= max_dd < 20% -> 100% QLD
    20% <= max_dd < 28% -> 60% QLD + 40% TQQQ
    28% <= max_dd < 36% -> 20% QLD + 80% TQQQ
    max_dd >= 36%      -> 100% TQQQ
- 降杠杆（滞后修复带）：
    当当前回撤修复到低于当前档位下界 × deleverage_ratio 时，
    每天降一级杠杆。例如当前在 20% 档（下界 20%），
    deleverage_ratio=0.5 时，需 current_dd < 10% 才会降级。
    --deleverage-ratio 0 退化为原单向棘轮行为。

运行方式:
    source /root/vix/.venv/bin/activate && python scripts/drawdown_rotation_strategy.py
"""

from __future__ import annotations

import argparse
import math
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
)

import vectorbt as vbt


def _allocation_for_max_dd(max_dd: float) -> dict[str, float]:
    """根据回撤返回目标资产配置（50/50 基准，回撤加深时逐步增加杠杆）。"""
    if max_dd < 0.08:
        return {"QQQ": 0.5, "QLD": 0.5}
    if max_dd < 0.14:
        return {"QQQ": 0.2, "QLD": 0.8}
    if max_dd < 0.20:
        return {"QLD": 1.0}
    if max_dd < 0.28:
        return {"QLD": 0.6, "TQQQ": 0.4}
    if max_dd < 0.36:
        return {"QLD": 0.2, "TQQQ": 0.8}
    return {"TQQQ": 1.0}


# 回撤档位上界（从低到高，共 5 个边界 = 6 档）
_TIER_BOUNDS = [0.08, 0.14, 0.20, 0.28, 0.36]


def _get_tier(dd: float) -> int:
    """返回回撤值所在的档位索引（0=最安全，8=最深）。"""
    for i, bound in enumerate(_TIER_BOUNDS):
        if dd < bound:
            return i
    return len(_TIER_BOUNDS)


def _tier_to_dd(tier: int) -> float:
    """将档位索引映射回回撤值（取档位中位数），用于调用 _allocation_for_max_dd。"""
    if tier <= 0:
        return 0.04  # 第 0 档 [0, 0.08) 中位数
    if tier >= len(_TIER_BOUNDS):
        return 0.40  # 最深档 [0.36, ∞)
    lower = _TIER_BOUNDS[tier - 1]
    upper = _TIER_BOUNDS[tier]
    return (lower + upper) / 2.0


def build_drawdown_weights(close: pd.DataFrame, deleverage_ratio: float = 0.3,
                          ma_window: int = 200) -> pd.DataFrame:
    """构造回撤驱动策略的每日目标权重。

    加杠杆：current_dd 突破更高档位 + QQQ 在 MA 上方时，active_tier 立即跟进。
    MA 过滤避免在熊市（价格 < MA200）中加杠杆。ma_window=0 关闭过滤。
    降杠杆：current_dd 修复到低于当前档位下界 × deleverage_ratio 时，
    每天最多降一级。降杠杆不受 MA 过滤影响。
    """
    weights = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    portfolio_value = 1.0
    hwm = 1.0
    max_dd = 0.0
    active_tier = 0  # 当前有效杠杆档位，可升可降
    prev_target: dict[str, float] | None = None

    # 预计算 QQQ 移动平均（用于牛熊过滤）
    qqq_ma = None
    if ma_window > 0 and "QQQ" in close.columns:
        qqq_ma = close["QQQ"].rolling(ma_window).mean()

    for i, date in enumerate(close.index):
        # 用 active_tier 决定当日目标权重（而非直接使用 max_dd）
        effective_dd = _tier_to_dd(active_tier)
        target = _allocation_for_max_dd(effective_dd)
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

            # 升级：current_dd 突破更高档位 + MA 过滤（熊市禁止加杠杆）
            current_tier = _get_tier(current_dd)
            if current_tier > active_tier:
                allow_upgrade = True
                if qqq_ma is not None:
                    qqq_price = close.loc[date, "QQQ"]
                    ma_val = qqq_ma.loc[date]
                    # MA 尚未就绪（数据不足）→ 放行；已就绪且价格在 MA 下方 → 阻止
                    if pd.notna(ma_val) and qqq_price < ma_val:
                        allow_upgrade = False
                if allow_upgrade:
                    active_tier = current_tier

            # 降级：current_dd 修复过阈值时每天降一级
            if active_tier > 0 and deleverage_ratio > 0:
                lower_bound = _TIER_BOUNDS[active_tier - 1]
                if current_dd < lower_bound * deleverage_ratio:
                    active_tier -= 1

        prev_target = target

    return weights


def run_and_report(start: str, end: str, cash: float, fees: float, slippage: float,
                   deleverage_ratio: float = 0.3, ma_window: int = 200):
    """执行回测并保存 HTML/JSON 报告。"""
    import report_utils

    close = fetch_etf_data(DEFAULT_SYMBOLS, start, end)
    if isinstance(close, pd.Series):
        close = close.to_frame()
    close.columns = [str(c).upper() for c in close.columns]

    weights = build_drawdown_weights(close, deleverage_ratio, ma_window)

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

    benchmark_values = report_utils.build_benchmark_values(
        close, cash, fees, slippage, ["QQQ", "QLD", "TQQQ"]
    )

    fig = report_utils.build_plotly_chart(
        chart_value, weights, benchmark_values,
        "回撤驱动 QQQ/QLD/TQQQ 杠杆轮动策略回测"
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"drawdown_rotation_{timestamp}"

    config_rows = [
        ("策略", "回撤驱动杠杆轮动"),
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
    report_utils.write_html_report(html_path, "回撤驱动 QLD/TQQQ 杠杆轮动策略回测", panels, chart_html)

    json_path = report_utils.OUTPUT_DIR / f"{prefix}_metrics.json"
    report_utils.write_json_report(json_path, {
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
    })

    print(f"[DrawdownRotation] 总收益: {metrics['total_return']:.2%}, 最大回撤: {metrics['max_drawdown']:.2%}")


def main():
    parser = argparse.ArgumentParser(description="回撤驱动杠杆轮动策略回测")
    parser.add_argument("--start", default="2006-07-01", help="回测起始日期")
    parser.add_argument("--end", default="2026-07-10", help="回测结束日期")
    parser.add_argument("--cash", type=float, default=DEFAULT_CASH, help="初始资金")
    parser.add_argument("--fees", type=float, default=DEFAULT_FEES, help="单边手续费比例")
    parser.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE, help="滑点比例")
    parser.add_argument("--deleverage-ratio", type=float, default=0.3,
                        help="回撤修复比例，用于决定降杠杆的滞后阈值（0=禁用，退化为单向棘轮）")
    parser.add_argument("--ma-window", type=int, default=200,
                        help="QQQ 移动平均窗口，价格在 MA 下方时禁止加杠杆（0=关闭过滤）")
    args = parser.parse_args()

    run_and_report(args.start, args.end, args.cash, args.fees, args.slippage,
                   args.deleverage_ratio, args.ma_window)


if __name__ == "__main__":
    main()
