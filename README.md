# VIX 历史数据看板

基于 CBOE 官方 VIX 历史数据构建的交互式 Web 看板，支持滑动、缩放查看 VIX 全历史走势及对应历史百分位。

## 文件说明

| 文件 | 说明 |
|---|---|
| `data/VIX_History.csv` | CBOE 官方 VIX 日线数据（运行时下载） |
| `data/NDX_History.csv` | Yahoo Finance 纳斯达克100指数（^NDX）日线数据（运行时下载） |
| `data/SPX_History.csv` | Yahoo Finance 标普500指数（^GSPC）日线数据（运行时下载） |
| `data/ndx_pe.json` | 纳斯达克100 滚动市盈率（TTM）代理数据（由 `scripts/fetch_ndx_pe.py` 维护） |
| `data/last_update.json` | VIX 数据更新时间与状态记录（由 `scripts/start.py` 维护） |
| `data/ndx_last_update.json` | 纳斯达克100 数据更新状态记录 |
| `index.html` | 看板主页面 |
| `assets/dashboard.js` | 图表逻辑、交互事件、更新时间展示 |
| `assets/dashboard_core.js` | 可测试的纯函数核心：CSV 解析、日期解析、百分位计算 |
| `assets/style.css` | 页面样式 |
| `scripts/start.py` | 启动脚本：自动更新 VIX / 纳斯达克100 / NDX PE 数据并启动本地 HTTP 服务 |
| `scripts/fetch_ndx_pe.py` | 获取 QQQ 滚动市盈率并保存到 `data/ndx_pe.json` |
| `scripts/backtest.py` | VectorBT 回测脚本：VIX 驱动 QQQ/QLD/TQQQ 杠杆轮动 |
| `scripts/optimize.py` | VIX 阈值参数扫描脚本 |
| `tests/test_start.py` | `scripts/start.py` 的单元测试 |
| `tests/test_dashboard_core.js` | `assets/dashboard_core.js` 的单元测试 |

## 启动方式

由于浏览器安全策略，本地 CSV 文件需要通过 HTTP 服务器加载。本项目提供 `scripts/start.py` 启动脚本，在启动服务前会自动从 CBOE 拉取最新 VIX 数据、从 Yahoo Finance 拉取最新纳斯达克100数据，并更新本地 CSV：

首次使用前请安装依赖（务必使用项目虚拟环境）：

```bash
source /root/vix/.venv/bin/activate && pip install -r requirements.txt
```

然后运行：

```bash
source /root/vix/.venv/bin/activate && python3 scripts/start.py
```

默认监听 `8080` 端口，也可自定义端口：

```bash
source /root/vix/.venv/bin/activate && python3 scripts/start.py 9000
```

然后在浏览器中打开：

```
http://localhost:8080
```

> 注：若仅需手动启动 HTTP 服务而不更新数据，仍可运行 `python3 -m http.server 8080`，但看板上的“数据更新时间”将显示为“未记录”。
>
> 若未安装 `yfinance`，纳斯达克100 数据更新会被跳过，VIX 看板仍可正常使用。

## 功能

- **三图布局**：上方为 VIX 历史 K 线，中间为对应历史百分位，下方为纳斯达克100（NASDAQ-100）历史 K 线
- **联动缩放/滑动**：三个图表共用同一个缩放与滑动条，鼠标滚轮、双指捏合、底部滑块均可控制
- **纳斯达克100 纵轴切换**：支持普通坐标与对数坐标，便于观察长期 exponential 增长
- **历史百分位**：默认显示全历史百分位，可切换为滚动 1 年 / 5 年 / 10 年百分位
- **百分位显示方式**：支持折线或面积图
- **统计卡片**：展示最新日期、最新 VIX、最新百分位、历史最高/最低/均值、数据更新时间
- **历史事件标注**：VIX 历史 K 线上方标注了日内最高价 > 35 的重大事件（共 29 个），主要事件显示文字标签，次要事件显示黄色标记点，鼠标悬停可查看事件详情
- **交互提示**：鼠标悬停同时显示对应日期的 VIX 开高低收、百分位与纳斯达克100开高低收

## VIX 经济含义区间

看板在统计卡片中显示“当前区间”，并在图表上方提供区间参考表。这些阈值来自市场长期交易经验（13、20、30、40 为常见心理关口），用于快速判断市场恐慌程度，**不是统计分位数**。

| 区间 | 经济含义 | 说明 |
|---|---|---|
| VIX < 13 | 恐慌缺失 | 市场过度乐观，波动率常被低估 |
| 13 ≤ VIX < 20 | 低波动常态 | 正常低波动环境，VIX 长期均值附近 |
| 20 ≤ VIX < 30 | 市场担忧 | 避险情绪升温，回调压力增大 |
| 30 ≤ VIX < 40 | 显著恐慌 | 明显恐慌，流动性收缩，期权保护需求激增 |
| VIX ≥ 40 | 极端危机 | 系统性危机或黑天鹅事件，通常伴随股市大跌 |

> 注意：这些阈值是市场经验值，会随市场环境和时间中枢变化，建议结合滚动分位数综合判断。

## 回测（VectorBT）

项目已集成基于 [VectorBT](https://vectorbt.dev/) 的回测脚本，策略为 **VIX 驱动 QQQ/QLD/TQQQ 杠杆轮动**（VIX 越高越激进，越低越保守）：

- VIX < low：半仓 QQQ（0.5 倍）
- low ≤ VIX < mid1：满仓 QQQ（1 倍）
- mid1 ≤ VIX ≤ mid2：半仓 QLD + 半仓 QQQ（1.5 倍）
- mid2 ≤ VIX ≤ high：满仓 QLD（2 倍）
- VIX > high：半仓 QLD + 半仓 TQQQ（2.5 倍）

标的缺失时按以下链条回退：TQQQ → QLD → QQQ → 空仓，QLD → QQQ → 空仓。

默认阈值：`13, 20, 30, 40`。

### 运行单次回测

```bash
source /root/vix/.venv/bin/activate && python scripts/backtest.py
```

结果（权益曲线 HTML + 绩效 JSON）会保存到 `output/` 目录。

### 自定义参数

```bash
source /root/vix/.venv/bin/activate && python scripts/backtest.py \
  --thresholds 15 25 35 45 \
  --cash 100000 \
  --fees 0.0005 \
  --slippage 0.0005
```

### VIX 阈值参数扫描

```bash
source /root/vix/.venv/bin/activate && python scripts/optimize.py --metric calmar
```

支持按 `total_return`、`annual_return`、`sharpe`、`calmar` 排序找出最佳阈值组合，完整结果保存为 `output/vix_threshold_scan_*.csv`。

## 数据来源

- VIX：CBOE 官方 `https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv`
- 纳斯达克100：Yahoo Finance `^NDX`（通过 `yfinance` 拉取）
- QQQ / QLD / TQQQ：Yahoo Finance（通过 `yfinance` 拉取）
- 数据字段：`DATE`, `OPEN`, `HIGH`, `LOW`, `CLOSE`

### TQQQ 历史数据回填说明

TQQQ 于 2010-02-11 上市。为与 QLD（2006-06-21 上市）时间轴对齐，项目中 `data/TQQQ_History.csv` 的 **2006-06-21 至 2010-02-10** 区间采用合成数据：

- 基于上市后 TQQQ 与 QQQ 的日收益线性回归 `R_tqqq = α + β × R_qqq` 生成（当前 α≈-0.00018，β≈2.957，R²≈0.998）
- 锚定 2010-02-11 真实 TQQQ 收盘价，向前递推合成 CLOSE
- OHLC 按 QQQ 当日价格形态缩放生成

回填脚本：`scripts/backfill_tqqq.py`

> 注意：
> - 2006-06-21 至 2010-02-10 的 TQQQ 数据为基于统计关系的合成数据，仅用于与 QLD 对齐的回测分析，不代表真实历史价格。
> - 由于 `data/*_History.csv` 与 `data/etf_metadata.json` 被 `.gitignore` 排除，新 clone 或清理数据后需先执行上述脚本，才能在看板/回测中使用回填后的 TQQQ 数据。
