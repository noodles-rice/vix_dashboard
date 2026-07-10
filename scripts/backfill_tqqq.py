#!/usr/bin/env python3
"""TQQQ 历史数据回填脚本。

TQQQ 于 2010-02-11 上市，本脚本利用上市后 TQQQ 与 QQQ 的日收益线性回归关系，
对 2006-06-21（QLD 起始日）至 2010-02-10 的缺失交易日进行合成 OHLC 回填，
使 TQQQ 时间轴与 QLD 对齐。

回归模型：
    R_tqqq = alpha + beta * R_qqq

合成方法：
    以 2010-02-11 的真实 TQQQ 收盘价为锚点，向前递推：
    price_t = price_t+1 / (1 + alpha + beta * R_qqq_t+1)
    其中 R_qqq_t+1 = qqq_close_t+1 / qqq_close_t - 1

    对 OHLC 四项分别使用 QQQ 对应价格相对前收收益率进行缩放，
    保留 QQQ 的日内形态。

用法：
    source .venv/bin/activate && python scripts/backfill_tqqq.py
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TQQQ_CSV = DATA_DIR / "TQQQ_History.csv"
QQQ_CSV = DATA_DIR / "QQQ_History.csv"
METADATA_FILE = DATA_DIR / "etf_metadata.json"

BACKFILL_START = pd.Timestamp("2006-06-21")
TQQQ_FIRST_DATE = pd.Timestamp("2010-02-11")


def load_etf(symbol: str) -> pd.DataFrame:
    """读取项目标准格式的 ETF 历史 CSV，返回以 DATE 为索引的 OHLC DataFrame。"""
    path = DATA_DIR / f"{symbol.upper()}_History.csv"
    if not path.exists():
        raise FileNotFoundError(f"找不到本地 ETF 数据: {path}")

    df = pd.read_csv(path)
    required = {"DATE", "OPEN", "HIGH", "LOW", "CLOSE"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} 缺少必要列: {missing}")

    df["DATE"] = pd.to_datetime(df["DATE"], format="%m/%d/%Y")
    df = df.set_index("DATE").sort_index()
    return df[["OPEN", "HIGH", "LOW", "CLOSE"]]


def compute_regression(qqq: pd.DataFrame, tqqq: pd.DataFrame) -> tuple[float, float, float]:
    """计算 TQQQ 上市后真实数据期间，TQQQ 日收益对 QQQ 日收益的线性回归系数。

    仅使用 TQQQ 真实上市日（2010-02-11 及之后）的数据，避免用合成数据自我验证。
    返回 (alpha, beta, r_squared)。
    """
    # 只取 TQQQ 真实数据（上市首日无法计算收益，pct_change 会自然剔除）
    real_tqqq = tqqq.loc[tqqq.index >= TQQQ_FIRST_DATE]

    qqq_ret = qqq["CLOSE"].pct_change().dropna()
    tqqq_ret = real_tqqq["CLOSE"].pct_change().dropna()

    common_idx = qqq_ret.index.intersection(tqqq_ret.index)
    if len(common_idx) < 30:
        raise ValueError("QQQ 与 TQQQ 重叠交易日不足，无法进行回归")

    x = qqq_ret.loc[common_idx].to_numpy()
    y = tqqq_ret.loc[common_idx].to_numpy()

    # 过滤极端缺失（理论上 pct_change 已 dropna，再保险一次）
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]

    beta, alpha = np.polyfit(x, y, 1)
    r_squared = float(np.corrcoef(x, y)[0, 1] ** 2)
    return float(alpha), float(beta), r_squared


def backfill_ohlc(qqq: pd.DataFrame, tqqq: pd.DataFrame, alpha: float, beta: float) -> pd.DataFrame:
    """生成 2006-06-21 至 2010-02-10 的合成 TQQQ OHLC。

    以 2010-02-11 的真实 TQQQ 收盘价为锚点，向前递推；
    仅生成 QQQ 在该区间有数据的交易日。
    """
    # 确定回填区间：从 BACKFILL_START 到 TQQQ_FIRST_DATE 前一天
    backfill_end = TQQQ_FIRST_DATE - pd.Timedelta(days=1)

    qqq_in_range = qqq.loc[BACKFILL_START:backfill_end].copy()
    if qqq_in_range.empty:
        raise ValueError(
            f"QQQ 在回填区间 {BACKFILL_START.date()} ~ {backfill_end.date()} 没有数据"
        )

    # 获取 2010-02-11 的 QQQ 与真实 TQQQ 收盘价，作为递推锚点
    qqq_anchor_date = TQQQ_FIRST_DATE
    if qqq_anchor_date not in qqq.index:
        raise ValueError(f"QQQ 缺少锚定日 {qqq_anchor_date.date()} 数据")
    if qqq_anchor_date not in tqqq.index:
        raise ValueError(f"TQQQ 缺少锚定日 {qqq_anchor_date.date()} 数据")

    qqq_prev_close = float(qqq.loc[qqq_anchor_date, "CLOSE"])
    current_close = float(tqqq.loc[qqq_anchor_date, "CLOSE"])

    # 先向前计算 CLOSE 序列：从 2010-02-10 倒推到 2006-06-21
    dates = qqq_in_range.index[::-1]  # 从近到远
    close_values: list[float] = []

    for date in dates:
        qqq_close_today = float(qqq_in_range.loc[date, "CLOSE"])
        qqq_ret_next = (qqq_prev_close / qqq_close_today) - 1.0
        synthetic_ret_next = alpha + beta * qqq_ret_next
        # 已知 next_date 的 TQQQ close，反推 date 的 close：
        # next_close = today_close * (1 + synthetic_ret_next)
        # => today_close = next_close / (1 + synthetic_ret_next)
        denominator = 1.0 + synthetic_ret_next
        if denominator <= 0:
            raise ValueError(
                f"日期 {date.date()} 的合成收益率导致分母非正 ({denominator:.6f})，"
                "无法向前递推 TQQQ 收盘价；请检查 QQQ/TQQQ 数据或回归系数。"
            )
        today_close = current_close / denominator
        close_values.append(today_close)
        current_close = today_close
        qqq_prev_close = qqq_close_today

    # 重新正序排列（从 2006-06-21 到 2010-02-10）
    close_values = close_values[::-1]
    dates = dates[::-1]

    # 利用 QQQ 的 OHLC 相对前收收益率，分别合成 TQQQ 的 OHLC
    synthetic = pd.DataFrame(index=dates, columns=["OPEN", "HIGH", "LOW", "CLOSE"])
    prev_close = None
    for i, date in enumerate(dates):
        row = qqq_in_range.loc[date]
        if i == 0:
            # 首日开盘价：用当日 close 与 QQQ 开盘/close 比例反推
            open_ratio = row["OPEN"] / row["CLOSE"]
            synthetic_open = close_values[i] * open_ratio
            # 首日没有上一日 close，用 QQQ 当日 OPEN 作为缩放参考，
            # 使 OPEN/HIGH/LOW/CLOSE 的日内关系保持自洽
            qqq_ref = float(row["OPEN"])
        else:
            # 非首日开盘 = 前一日合成的 close
            synthetic_open = prev_close
            # 非首日以 QQQ 上一日 close 为参考，确保 scale(close) 与
            # 向后递推的 synthetic_close 一致
            qqq_ref = float(qqq_in_range.iloc[i - 1]["CLOSE"])

        synthetic_close = close_values[i]

        def scale(price: float) -> float:
            ret = price / qqq_ref - 1.0
            return synthetic_open * (1.0 + alpha + beta * ret)

        synthetic_high = max(synthetic_open, synthetic_close, scale(row["HIGH"]))
        synthetic_low = min(synthetic_open, synthetic_close, scale(row["LOW"]))

        synthetic.loc[date, "OPEN"] = synthetic_open
        synthetic.loc[date, "HIGH"] = synthetic_high
        synthetic.loc[date, "LOW"] = synthetic_low
        synthetic.loc[date, "CLOSE"] = synthetic_close
        prev_close = synthetic_close

    return synthetic.astype(float)


def merge_and_save(synthetic: pd.DataFrame, real: pd.DataFrame) -> None:
    """将合成数据与真实 TQQQ 数据合并，按日期排序后写回 CSV。"""
    # 备份原文件，使用时间戳避免重复运行覆盖上一次备份
    if TQQQ_CSV.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = TQQQ_CSV.with_name(f"{TQQQ_CSV.stem}_{timestamp}.csv.bak")
        shutil.copy2(TQQQ_CSV, backup_path)
        print(f"[Backfill] 已备份原文件: {backup_path}")

    combined = pd.concat([synthetic, real])
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined.sort_index()
    combined.index.name = "DATE"

    out = combined.reset_index()
    out["DATE"] = out["DATE"].dt.strftime("%m/%d/%Y")
    out = out[["DATE", "OPEN", "HIGH", "LOW", "CLOSE"]]
    out.to_csv(TQQQ_CSV, index=False, float_format="%.6f")
    print(f"[Backfill] 已写入: {TQQQ_CSV}")


def load_metadata() -> dict:
    """加载 ETF 元数据；文件不存在时返回空字典。"""
    if not METADATA_FILE.exists():
        return {}
    with open(METADATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_metadata(metadata: dict) -> None:
    """保存 ETF 元数据。"""
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)


def update_metadata(alpha: float, beta: float, r_squared: float) -> None:
    """在 etf_metadata.json 中记录 TQQQ 回填信息。"""
    metadata = load_metadata()
    tqqq_meta = metadata.setdefault("TQQQ", {})
    tqqq_meta["backfilled"] = True
    tqqq_meta["backfill_start"] = BACKFILL_START.strftime("%Y-%m-%d")
    tqqq_meta["backfill_end"] = (TQQQ_FIRST_DATE - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    tqqq_meta["regression_alpha"] = alpha
    tqqq_meta["regression_beta"] = beta
    tqqq_meta["regression_r2"] = r_squared
    tqqq_meta["backfill_note"] = (
        "2006-06-21 至 2010-02-10 数据基于同期 QQQ 日收益与上市后 TQQQ-QQQ "
        "线性回归系数合成，仅用于与 QLD 时间轴对齐的回测分析。"
    )
    tqqq_meta["backfilled_at"] = datetime.now(timezone.utc).isoformat()
    save_metadata(metadata)
    print(f"[Backfill] 已更新元数据: {METADATA_FILE}")


def main() -> int:
    """脚本入口。"""
    print("[Backfill] 加载 QQQ / TQQQ 本地数据...")
    qqq = load_etf("QQQ")
    tqqq = load_etf("TQQQ")

    print(f"[Backfill] QQQ  区间: {qqq.index[0].date()} ~ {qqq.index[-1].date()}")
    print(f"[Backfill] TQQQ 区间: {tqqq.index[0].date()} ~ {tqqq.index[-1].date()}")

    alpha, beta, r_squared = compute_regression(qqq, tqqq)
    print(f"[Backfill] 回归结果: alpha={alpha:.6f}, beta={beta:.4f}, R²={r_squared:.4f}")

    if not (2.5 <= beta <= 3.5):
        print(
            f"[Backfill] 警告: beta={beta:.4f} 明显偏离 3x 杠杆，请检查数据质量",
            file=sys.stderr,
        )

    print("[Backfill] 生成合成 OHLC...")
    synthetic = backfill_ohlc(qqq, tqqq, alpha, beta)
    print(
        f"[Backfill] 合成区间: {synthetic.index[0].date()} ~ {synthetic.index[-1].date()}, "
        f"共 {len(synthetic)} 个交易日"
    )
    print(
        f"[Backfill] 合成首尾价格: OPEN={synthetic.iloc[0]['OPEN']:.6f}, "
        f"CLOSE={synthetic.iloc[-1]['CLOSE']:.6f}"
    )

    merge_and_save(synthetic, tqqq)
    update_metadata(alpha, beta, r_squared)

    print("[Backfill] 完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
