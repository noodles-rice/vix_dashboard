#!/usr/bin/env python3
"""扫描多种 VIX + 移动均线组合信号策略。

运行方式:
    source /root/vix/.venv/bin/activate && python scripts/scan_ma_signals.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest import (
    DEFAULT_SYMBOLS,
    _portfolio_value_metrics,
    fetch_etf_data,
    fetch_vix_data,
)

import vectorbt as vbt


def evaluate_weights(close, weights, cash, fees, slippage):
    """根据目标权重矩阵回测并返回指标。"""
    weights = weights.shift(1).fillna(0.0)
    pf = vbt.Portfolio.from_orders(
        close=close,
        size=weights,
        size_type="targetpercent",
        fees=fees,
        slippage=slippage,
        freq="1d",
        init_cash=cash,
        # 与 backtest.py 保持一致：多资产 targetpercent 必须共享现金
        cash_sharing=True,
    )
    return _portfolio_value_metrics(pf)


def _build_weights(close, allocations):
    """根据 (mask, [(asset, weight), ...]) 列表构造目标权重 DataFrame。"""
    weights = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for mask, alloc in allocations:
        for asset, wt in alloc:
            weights.loc[mask, asset] = wt
    return weights


def _add_result(results, name, close, weights, cash, fees, slippage):
    """执行回测并将指标追加到 results。"""
    metrics = evaluate_weights(close, weights, cash, fees, slippage)
    results.append({"name": name, **metrics})


def run_grid(close, vix, cash, fees, slippage):
    """扫描多种 VIX+MA 规则，输出结果 DataFrame。"""
    qqq = close["QQQ"]
    results = []

    # 基准
    for sym in ["QQQ", "QLD", "TQQQ"]:
        w = _build_weights(close, [(pd.Series(True, index=close.index), [(sym, 1.0)])])
        _add_result(results, f"买入持有 {sym}", close, w, cash, fees, slippage)

    # 不同周期均线
    ma_windows = [20, 50, 100, 150, 200]
    vix_levels = [15, 20, 25, 30]

    for window in ma_windows:
        ma = qqq.rolling(window).mean()
        above_ma = qqq > ma
        below_ma = qqq < ma

        # 1) 纯均线逆向：跌破均线越久，仓位越激进（连续跌破天数分级）
        below_days = (
            below_ma.astype(int)
            .groupby((below_ma != below_ma.shift()).cumsum())
            .cumsum()
        )
        allocations = [
            (~below_ma, [("QQQ", 1.0)]),
            (below_days == 1, [("QLD", 1.0)]),
            (below_days == 2, [("TQQQ", 0.5), ("QLD", 0.5)]),
            (below_days >= 3, [("TQQQ", 1.0)]),
        ]
        w = _build_weights(close, allocations)
        _add_result(results, f"均线逆向-跌破分级 MA{window}", close, w, cash, fees, slippage)

        # 2) VIX+均线逆向：恐惧（VIX高或跌破均线）加仓，贪婪（VIX低且价格在均线上方）减仓
        for vix_thr in vix_levels:
            fear = (vix > vix_thr) | below_ma
            greed = (vix <= vix_thr) & above_ma
            w = _build_weights(
                close,
                [
                    (fear, [("TQQQ", 1.0)]),
                    (greed, [("QQQ", 0.0)]),  # 空仓
                ],
            )
            _add_result(
                results,
                f"VIX>{vix_thr}或跌破MA{window}=TQQQ",
                close,
                w,
                cash,
                fees,
                slippage,
            )

        # 3) VIX+均线趋势跟踪：趋势好+VIX低时满仓TQQQ，否则空仓/降级
        for vix_thr in vix_levels:
            strong = above_ma & (vix <= vix_thr)
            weak = above_ma & (vix > vix_thr)
            fear = below_ma
            w = _build_weights(
                close,
                [
                    (strong, [("TQQQ", 1.0)]),
                    (weak, [("QQQ", 1.0)]),
                    (fear, [("QQQ", 0.0)]),  # 空仓
                ],
            )
            _add_result(
                results,
                f"趋势好+VIX<={vix_thr}=TQQQ MA{window}",
                close,
                w,
                cash,
                fees,
                slippage,
            )

        # 4) VIX+均线逆向分级：用 VIX 和均线的组合产生 0~3 级恐惧指数
        for vix_thr in vix_levels:
            fear_score = below_ma.astype(int) + (vix > vix_thr).astype(int)
            allocations = [
                (fear_score == 0, [("QQQ", 1.0)]),
                (fear_score == 1, [("QLD", 1.0)]),
                (fear_score == 2, [("TQQQ", 0.5), ("QLD", 0.5)]),
                (fear_score == 3, [("TQQQ", 1.0)]),
            ]
            w = _build_weights(close, allocations)
            _add_result(
                results,
                f"恐惧指数 VIX>{vix_thr}+MA{window}",
                close,
                w,
                cash,
                fees,
                slippage,
            )

    # 5) VIX 百分位 + 均线
    def _vix_percentile(x):
        """滚动窗口内当前值的经验分位数（raw=True 加速）。"""
        return (x <= x[-1]).mean()

    for window in [252, 504, 1260]:
        vix_pct = vix.rolling(window).apply(_vix_percentile, raw=True)
        for ma_win in [50, 100, 150, 200]:
            ma = qqq.rolling(ma_win).mean()
            above_ma = qqq > ma
            below_ma = qqq < ma
            for pct_thr in [0.3, 0.5, 0.7]:
                high_fear = (vix_pct > pct_thr) & below_ma
                mild_fear = (vix_pct > pct_thr) | below_ma
                calm = (vix_pct <= pct_thr) & above_ma
                w = _build_weights(
                    close,
                    [
                        (high_fear, [("TQQQ", 1.0)]),
                        (mild_fear & ~high_fear, [("QLD", 1.0)]),
                        (calm, [("QQQ", 0.0)]),  # 空仓
                    ],
                )
                _add_result(
                    results,
                    f"VIX百分位>{pct_thr:.0%}+MA{ma_win} 逆向",
                    close,
                    w,
                    cash,
                    fees,
                    slippage,
                )

    df = pd.DataFrame(results)
    df = df.sort_values("total_return", ascending=False)
    return df


def main():
    start = "2015-01-02"
    end = "2026-07-07"
    cash = 10000
    fees = 0.0002
    slippage = 0.0003

    print("[ScanMA] 加载数据...")
    close = fetch_etf_data(DEFAULT_SYMBOLS, start, end)
    vix = fetch_vix_data(start, end)

    # 对齐
    common = close.index.intersection(vix.index)
    close = close.loc[common]
    vix = vix.loc[common]

    print(f"[ScanMA] 回测区间: {close.index[0].date()} ~ {close.index[-1].date()}")
    print("[ScanMA] 开始扫描 VIX+MA 策略...")
    df = run_grid(close, vix, cash, fees, slippage)

    print("\n========== 总收益 Top 15 ==========")
    print(df.head(15).to_string(index=False))

    print("\n========== Calmar Top 15 ==========")
    print(df.sort_values("calmar", ascending=False).head(15).to_string(index=False))

    # 筛选满足收益 >= QLD 且回撤明显小于 QLD 的配置
    # 注意：max_drawdown 为负数，数值越大（越接近 0）表示回撤越浅
    qld_return = df.loc[df["name"] == "买入持有 QLD", "total_return"].iloc[0]
    qld_dd = df.loc[df["name"] == "买入持有 QLD", "max_drawdown"].iloc[0]
    candidates = df[
        (df["total_return"] >= qld_return) & (df["max_drawdown"] >= qld_dd * 0.7)
    ]
    print(
        f"\n========== 满足 收益>=QLD({qld_return:.2%}) 且 回撤>={qld_dd*0.7:.2%} 的配置 =========="
    )
    if candidates.empty:
        print("无满足条件的配置。")
    else:
        print(candidates.to_string(index=False))

    # 输出到 CSV
    out_dir = Path(__file__).resolve().parent.parent / "output"
    out_dir.mkdir(exist_ok=True)
    csv_path = out_dir / f"ma_signal_scan_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv"
    df.to_csv(csv_path, index=False)
    print(f"\n[ScanMA] 完整结果已保存: {csv_path}")


if __name__ == "__main__":
    main()
