# 策略回测记录

## MA150 + VIX≤30 满仓 TQQQ

### 策略逻辑
- **买入/持仓条件**：`QQQ 收盘价 > MA150` 且 `VIX 收盘价 ≤ 30` 时，满仓 TQQQ（`run_ma_strategy.py` 本身不实现标的回退链；`scripts/backtest.py` 才支持 TQQQ → QLD → QQQ 回退）。
- **卖出/空仓条件**：当 `QQQ 收盘价 ≤ MA150` 或 `VIX 收盘价 > 30` 时，次日开盘清仓 TQQQ，转为空仓。
- **执行延迟**：信号基于当日收盘计算，次日执行（`shift(1)`），避免前视偏差。

### 回测参数
| 参数 | 值 |
|---|---|
| 标的 | QQQ、QLD、TQQQ |
| 起始日期 | 2006-06-21 |
| 结束日期 | 2026-07-06 |
| 初始资金 | 10,000 USD |
| 单边手续费 | 0.02% |
| 滑点 | 0.03% |
| 移动均线 | MA150 |
| VIX 阈值 | 30 |

### 回测结果
| 指标 | MA150+VIX≤30 满仓 TQQQ |
|---|---|
| 总收益 | **+6520.43%** |
| 年化收益 | **23.25%** |
| 最大回撤 | **-51.96%** |
| 夏普比率 | 0.73 |
| Calmar 比率 | 0.45 |
| 交易次数 | 70 |
| 胜率 | 51.43% |

### 同期基准对比
| 标的 | 总收益 | 年化收益 | 最大回撤 |
|---|---|---|---|
| 买入持有 QQQ | +2112.43% | 16.70% | -53.40% |
| 买入持有 QLD | +9601.33% | 25.62% | -83.13% |
| 买入持有 TQQQ | +37053.93% | 34.32% | -81.66% |

### 关键观察
- 该策略在包含 2008 年金融危机的完整长周期中，最大回撤仅 **-51.96%**，显著优于 QLD（-83.13%）和 TQQQ（-81.66%），甚至略低于 QQQ（-53.40%）。
- 收益（+6520%）介于 QQQ 与 QLD 之间，未能在全区间跑赢长持 QLD。
- 2008 年危机期间，VIX 飙升和 QQQ 跌破 MA150 双重信号帮助策略大幅减仓/空仓，是回撤控制优秀的核心原因。
- 主要卖出触发原因统计（共 70 笔交易）：QQQ 跌破 MA150 占主导，VIX>30 次之。

### 输出文件
- HTML 报告：`output/ma150_vix30.0_20260710_200424.html`
- JSON 指标：`output/ma150_vix30.0_20260710_200424_metrics.json`

### 复现命令
```bash
source /root/vix/.venv/bin/activate && \
python scripts/run_ma_strategy.py \
    --ma 150 --vix 30.0 \
    --start 2006-06-21 --end 2026-07-06
```

### 数据来源
- ETF 历史价格：本地 `data/QQQ_History.csv`、`data/QLD_History.csv`、`data/TQQQ_History.csv`（由 `scripts/backtest.py` 通过 yfinance 下载并缓存）。
- VIX 历史数据：本地 `data/VIX_History.csv`。

### 备注
- 回测脚本：`scripts/run_ma_strategy.py`
- 生成时间：2026-07-10
- `run_ma_strategy.py` 未实现标的回退链；本次记录生成时，若 `data/TQQQ_History.csv` 尚未回填，上市前 TQQQ 列将缺失。`scripts/backtest.py` 才支持 TQQQ → QLD → QQQ 回退。

---

## 数据更新：TQQQ 回填（2026-07-10）

为与 QLD 时间轴对齐，通过 `scripts/backfill_tqqq.py` 对 TQQQ 进行了历史回填：

- **回填区间**：2006-06-21 ~ 2010-02-10
- **方法**：基于上市后 TQQQ 与 QQQ 的日收益线性回归 `R_tqqq = α + β × R_qqq` 合成 OHLC
- **回归系数**：α ≈ -0.00018，β ≈ 2.957，R² ≈ 0.998
- **锚点**：2010-02-11 真实 TQQQ 收盘价
- **影响**：后续 rerun `scripts/run_ma_strategy.py` 时，上述区间内策略将直接持有合成 TQQQ，不再降级为 QLD/QQQ。历史记录中的绩效数字（基于降级规则）可能与新 rerun 结果存在差异。

> 合成数据已写入 `data/TQQQ_History.csv`，元信息记录在 `data/etf_metadata.json` 的 `TQQQ` 节点中。
