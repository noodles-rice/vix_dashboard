# VIX 历史数据看板

基于 CBOE 官方 VIX 历史数据构建的交互式 Web 看板，支持滑动、缩放查看 VIX 全历史走势及对应历史百分位。

## 文件说明

| 文件 | 说明 |
|---|---|
| `VIX_History.csv` | CBOE 官方 VIX 日线数据（1990-01-02 至今） |
| `last_update.json` | 数据更新时间与状态记录（由 `start.py` 维护） |
| `index.html` | 看板主页面 |
| `dashboard.js` | 图表逻辑、交互事件、更新时间展示 |
| `dashboard_core.js` | 可测试的纯函数核心：CSV 解析、日期解析、百分位计算 |
| `style.css` | 页面样式 |
| `start.py` | 启动脚本：自动更新 VIX 数据并启动本地 HTTP 服务 |
| `test_start.py` | `start.py` 的单元测试 |
| `test_dashboard_core.js` | `dashboard_core.js` 的单元测试 |

## 启动方式

由于浏览器安全策略，本地 CSV 文件需要通过 HTTP 服务器加载。本项目提供 `start.py` 启动脚本，在启动服务前会自动从 CBOE 拉取最新 VIX 数据并更新本地 CSV：

```bash
python3 start.py
```

默认监听 `8080` 端口，也可自定义端口：

```bash
python3 start.py 9000
```

然后在浏览器中打开：

```
http://localhost:8080
```

> 注：若仅需手动启动 HTTP 服务而不更新数据，仍可运行 `python3 -m http.server 8080`，但看板上的“数据更新时间”将显示为“未记录”。

## 功能

- **双图布局**：上方为 VIX 历史 K 线，下方为对应历史百分位
- **联动缩放/滑动**：两个图表共用同一个缩放与滑动条，鼠标滚轮、双指捏合、底部滑块均可控制
- **历史百分位**：默认显示全历史百分位，可切换为滚动 1 年 / 5 年 / 10 年百分位
- **百分位显示方式**：支持折线或面积图
- **统计卡片**：展示最新日期、最新 VIX、最新百分位、历史最高/最低/均值、数据更新时间
- **交互提示**：鼠标悬停同时显示对应日期的 VIX 开高低收与百分位

## 数据来源

- CBOE 官方：`https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv`
- 数据字段：`DATE`, `OPEN`, `HIGH`, `LOW`, `CLOSE`
