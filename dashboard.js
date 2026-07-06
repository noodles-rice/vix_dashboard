const VIX_MARK_LINE_LOW = 20;
const VIX_MARK_LINE_HIGH = 30;
const PERCENTILE_MARK_LINE_MEDIAN = 50;

// 0-100% 按 20% 一档离散着色，从绿到红；左闭右开，最后一个区间闭合
const PERCENTILE_PIECES = [
    { min: 0, max: 20, maxOpen: true, color: '#22c55e', label: '极低' },
    { min: 20, max: 40, maxOpen: true, color: '#84cc16', label: '偏低' },
    { min: 40, max: 60, maxOpen: true, color: '#eab308', label: '中等' },
    { min: 60, max: 80, maxOpen: true, color: '#f97316', label: '偏高' },
    { min: 80, max: 100, color: '#ef4444', label: '极高' }
];

class VIXDashboard {
    constructor() {
        this.data = [];
        this.dates = [];
        this.closes = [];
        this.chart = null;
        this.computedWindows = new Set(['full']);
        this.resizeHandler = null;
        this.colors = this.loadColors();
        this.init();
    }

    loadColors() {
        const root = getComputedStyle(document.documentElement);
        const get = (name, fallback) => {
            const value = root.getPropertyValue(name).trim();
            return value || fallback;
        };
        return {
            bg: get('--color-bg', '#0f172a'),
            surface: get('--color-surface', '#1e293b'),
            border: get('--color-border', '#334155'),
            textPrimary: get('--color-text-primary', '#f8fafc'),
            textSecondary: get('--color-text-secondary', '#e2e8f0'),
            textMuted: get('--color-text-muted', '#94a3b8'),
            textSubtle: get('--color-text-subtle', '#64748b'),
            primary: get('--color-primary', '#38bdf8'),
            secondary: get('--color-secondary', '#fbbf24'),
            danger: get('--color-danger', '#ef4444'),
            error: get('--color-error', '#f87171')
        };
    }

    getPercentilePieces() {
        return PERCENTILE_PIECES;
    }

    getPercentilePiece(value) {
        return this.getPercentilePieces().find(p => {
            const aboveMin = value >= p.min;
            const belowMax = p.maxOpen ? value < p.max : value <= p.max;
            return aboveMin && belowMax;
        });
    }

    getPercentilePieceColor(value) {
        const piece = this.getPercentilePiece(value);
        return piece ? piece.color : this.colors.textMuted;
    }

    init() {
        if (typeof echarts === 'undefined') {
            this.showError('ECharts 库加载失败，请刷新页面重试');
            return;
        }

        const chartDom = document.getElementById('chart');
        this.chart = echarts.init(chartDom);
        this.bindEvents();
        this.loadData();
        this.loadUpdateInfo();

        this.resizeHandler = () => {
            if (this.chart) this.chart.resize();
        };
        window.addEventListener('resize', this.resizeHandler);

        window.addEventListener('beforeunload', () => {
            if (this.chart) {
                this.chart.dispose();
                this.chart = null;
            }
        });
    }

    bindEvents() {
        document.getElementById('percentileSelect').addEventListener('change', (e) => {
            const type = e.target.value;
            if (type !== 'full' && !this.computedWindows.has(type)) {
                this.computeRollingPercentile(parseInt(type, 10));
            }
            this.updateStats();
            this.updateChart();
        });

        document.getElementById('chartType').addEventListener('change', () => {
            this.updateChart();
        });

        document.getElementById('resetZoom').addEventListener('click', () => {
            this.chart.dispatchAction({
                type: 'dataZoom',
                start: 0,
                end: 100
            });
        });
    }

    async loadData() {
        this.showLoading('正在加载 VIX 历史数据...');
        try {
            const response = await fetch('VIX_History.csv');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            const text = await response.text();

            this.data = this.parseCSV(text);
            this.dates = this.data.map(d => d.dateStr);
            this.closes = this.data.map(d => d.close);

            if (this.data.length === 0) {
                throw new Error('CSV 解析结果为空');
            }

            this.hideLoading();
            this.showLoading('正在计算历史百分位...');
            // 使用 requestAnimationFrame + setTimeout 让加载提示先渲染，避免阻塞 UI
            requestAnimationFrame(() => {
                setTimeout(() => {
                    try {
                        this.computeFullPercentile();
                        this.hideLoading();
                        this.updateStats();
                        this.updateChart();
                    } catch (err) {
                        console.error('[VIX Dashboard] Compute error:', err);
                        this.showError('百分位计算失败：' + err.message);
                    }
                }, 50);
            });
        } catch (error) {
            console.error('[VIX Dashboard] Load error:', error);
            this.showError(error.message);
        }
    }

    async loadUpdateInfo() {
        const elem = document.getElementById('statUpdateTime');
        try {
            const response = await fetch('last_update.json');
            if (!response.ok) {
                if (response.status === 404) {
                    elem.textContent = '未记录';
                    return;
                }
                throw new Error(`HTTP ${response.status}`);
            }
            const info = await response.json();
            const updatedAt = info.updatedAt ? this.formatDateTime(info.updatedAt) : '未知';
            elem.textContent = updatedAt;
            elem.title = `数据源: ${info.source || 'CBOE'}\n最新数据日期: ${info.latestDate || '未知'}\n状态: ${this.translateStatus(info.status)}`;
        } catch (error) {
            console.warn('[VIX Dashboard] Update info load failed:', error);
            elem.textContent = '未知';
        }
    }

    formatDateTime(isoString) {
        try {
            const date = new Date(isoString);
            if (isNaN(date.getTime())) return '未知';
            const options = {
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: false
            };
            return date.toLocaleString('zh-CN', options);
        } catch (e) {
            return '未知';
        }
    }

    translateStatus(status) {
        const map = {
            updated: '已更新',
            up_to_date: '已是最新',
            network_error: '网络错误',
            fetch_error: '获取失败',
            parse_error: '解析失败'
        };
        return map[status] || status;
    }

    showLoading(message) {
        if (this.chart) {
            this.chart.showLoading({
                text: message,
                color: this.colors.primary,
                textColor: this.colors.textSecondary,
                maskColor: 'rgba(15, 23, 42, 0.8)',
                zlevel: 0
            });
        } else {
            const container = document.createElement('div');
            container.className = 'loading';

            const spinner = document.createElement('div');
            spinner.className = 'spinner';

            const msg = document.createElement('div');
            msg.textContent = message;

            container.appendChild(spinner);
            container.appendChild(msg);
            this.setOverlay(container);
        }
    }

    hideLoading() {
        if (this.chart) {
            this.chart.hideLoading();
        } else {
            this.clearOverlay();
        }
    }

    showError(message) {
        const container = document.createElement('div');
        container.className = 'loading';

        const title = document.createElement('div');
        title.style.cssText = `color: ${this.colors.error}; margin-bottom: 8px; font-weight: 600;`;
        title.textContent = '数据加载失败';

        const msg = document.createElement('div');
        msg.style.cssText = 'font-size: 0.9rem; margin-bottom: 12px;';
        msg.textContent = message;

        const hint = document.createElement('div');
        hint.style.cssText = `font-size: 0.85rem; color: ${this.colors.textMuted};`;
        hint.appendChild(document.createTextNode('请确保通过本地服务器访问，例如：'));
        hint.appendChild(document.createElement('br'));
        const code = document.createElement('code');
        code.textContent = 'python3 -m http.server 8080';
        hint.appendChild(code);

        container.appendChild(title);
        container.appendChild(msg);
        container.appendChild(hint);
        this.setOverlay(container);
    }

    setOverlay(content) {
        const chartDom = document.getElementById('chart');
        this.clearOverlay();
        const overlay = document.createElement('div');
        overlay.id = 'chart-overlay';
        overlay.style.cssText = `position:absolute;top:0;left:0;right:0;bottom:0;z-index:100;display:flex;align-items:center;justify-content:center;background:${this.colors.surface};`;
        overlay.appendChild(content);
        chartDom.style.position = 'relative';
        chartDom.appendChild(overlay);
    }

    clearOverlay() {
        const overlay = document.getElementById('chart-overlay');
        if (overlay) overlay.remove();
    }

    parseCSV(text) {
        const lines = text.trim().split(/\r?\n/);
        if (lines.length < 2) {
            throw new Error('CSV 文件内容不足');
        }

        const headers = lines[0].split(',').map(h => h.trim().toUpperCase());
        const dateIdx = headers.indexOf('DATE');
        const closeIdx = headers.indexOf('CLOSE');
        const openIdx = headers.indexOf('OPEN');
        const highIdx = headers.indexOf('HIGH');
        const lowIdx = headers.indexOf('LOW');

        if (dateIdx === -1 || closeIdx === -1) {
            throw new Error('CSV 缺少必需的 DATE 或 CLOSE 列');
        }

        const data = [];
        for (let i = 1; i < lines.length; i++) {
            const cols = lines[i].split(',');
            if (cols.length < Math.max(dateIdx, closeIdx) + 1) continue;

            const dateStr = cols[dateIdx].trim();
            const date = this.parseDate(dateStr);
            if (!date || isNaN(date.getTime())) continue;

            const close = parseFloat(cols[closeIdx]);
            if (isNaN(close)) continue;

            data.push({
                date: date,
                dateStr: dateStr,
                open: openIdx !== -1 ? parseFloat(cols[openIdx]) : close,
                high: highIdx !== -1 ? parseFloat(cols[highIdx]) : close,
                low: lowIdx !== -1 ? parseFloat(cols[lowIdx]) : close,
                close: close
            });
        }

        return data.sort((a, b) => a.date - b.date);
    }

    parseDate(dateStr) {
        // CBOE format: MM/DD/YYYY
        const parts = dateStr.split('/');
        if (parts.length !== 3) return null;
        const month = parseInt(parts[0], 10) - 1;
        const day = parseInt(parts[1], 10);
        const year = parseInt(parts[2], 10);
        // 使用 UTC 避免用户本地时区导致日期偏移或夏令时边界问题
        return new Date(Date.UTC(year, month, day));
    }

    computeFullPercentile() {
        const n = this.data.length;
        const closes = this.closes;

        // 全历史百分位：采用 percentile rank 定义，范围 (0, 100]。
        // 最小值约为 100/n%，最大值为 100%，而非标准定义中的 [0, 100]。
        const sorted = closes.map((value, index) => ({ value, index }))
            .sort((a, b) => a.value - b.value || a.index - b.index);

        for (let rank = 0; rank < n; rank++) {
            const idx = sorted[rank].index;
            this.data[idx].percentileFull = ((rank + 1) / n) * 100;
        }
    }

    computeRollingPercentile(window) {
        const n = this.data.length;
        const key = `percentile${window}`;
        const closes = this.closes;

        // 使用排序窗口 + 二分查找优化，复杂度 O(n log window)。
        // 注意：前 window - 1 个数据点窗口未满，使用扩展窗口（已有全部数据）计算，
        // 因此早期数据实际为“自起始以来的累计百分位”。
        let sortedWindow = [];
        const percentileRankInSorted = (sortedArr, target) => {
            let left = 0;
            let right = sortedArr.length;
            while (left < right) {
                const mid = Math.floor((left + right) / 2);
                if (sortedArr[mid] <= target) {
                    left = mid + 1;
                } else {
                    right = mid;
                }
            }
            return left;
        };

        const insertSorted = (sortedArr, value) => {
            let left = 0;
            let right = sortedArr.length;
            while (left < right) {
                const mid = Math.floor((left + right) / 2);
                if (sortedArr[mid] <= value) {
                    left = mid + 1;
                } else {
                    right = mid;
                }
            }
            sortedArr.splice(left, 0, value);
        };

        const removeValue = (sortedArr, value) => {
            let left = 0;
            let right = sortedArr.length;
            while (left < right) {
                const mid = Math.floor((left + right) / 2);
                if (sortedArr[mid] < value) {
                    left = mid + 1;
                } else {
                    right = mid;
                }
            }
            // 找到第一个 >= value 的位置，然后线性查找精确匹配（处理重复值）
            for (let i = left; i < sortedArr.length; i++) {
                if (sortedArr[i] === value) {
                    sortedArr.splice(i, 1);
                    return;
                }
            }
        };

        for (let i = 0; i < n; i++) {
            const current = closes[i];
            insertSorted(sortedWindow, current);

            if (sortedWindow.length > window) {
                removeValue(sortedWindow, closes[i - window]);
            }

            const rank = percentileRankInSorted(sortedWindow, current);
            this.data[i][key] = (rank / sortedWindow.length) * 100;
        }

        this.computedWindows.add(String(window));
    }

    getPercentileKey() {
        const type = document.getElementById('percentileSelect').value;
        return type === 'full' ? 'percentileFull' : `percentile${type}`;
    }

    getPercentileLabel() {
        const type = document.getElementById('percentileSelect').value;
        switch (type) {
            case 'full': return '全历史百分位';
            case '252': return '滚动 1 年百分位';
            case '1260': return '滚动 5 年百分位';
            case '2520': return '滚动 10 年百分位';
            default: return '历史百分位';
        }
    }

    updateStats() {
        if (this.data.length === 0) return;

        const last = this.data[this.data.length - 1];
        const closes = this.closes;
        const max = Math.max(...closes);
        const min = Math.min(...closes);
        const mean = closes.reduce((a, b) => a + b, 0) / closes.length;
        const percentileKey = this.getPercentileKey();
        const percentile = last[percentileKey] !== undefined ? last[percentileKey] : last.percentileFull;

        const percentilePiece = this.getPercentilePiece(percentile);
        document.getElementById('statDate').textContent = last.dateStr;
        document.getElementById('statClose').textContent = last.close.toFixed(2);
        document.getElementById('statPercentile').textContent = percentile.toFixed(1) + '%' + (percentilePiece ? ' ' + percentilePiece.label : '');
        document.getElementById('statPercentile').style.color = percentilePiece ? percentilePiece.color : this.colors.textMuted;
        document.getElementById('statMax').textContent = max.toFixed(2);
        document.getElementById('statMin').textContent = min.toFixed(2);
        document.getElementById('statMean').textContent = mean.toFixed(2);
    }

    updateChart() {
        if (this.data.length === 0) return;

        const percentileKey = this.getPercentileKey();
        const percentileLabel = this.getPercentileLabel();
        const chartType = document.getElementById('chartType').value;
        const dates = this.dates;
        const closes = this.closes;
        const percentiles = this.data.map(d => d[percentileKey]);
        const c = this.colors;

        const option = {
            backgroundColor: 'transparent',
            animation: false,
            textStyle: {
                color: c.textMuted
            },
            title: [
                {
                    text: 'VIX 历史收盘价',
                    left: 'center',
                    top: '2%',
                    textStyle: {
                        color: c.textPrimary,
                        fontSize: 15,
                        fontWeight: 'normal'
                    }
                },
                {
                    text: percentileLabel,
                    left: 'center',
                    top: '53%',
                    textStyle: {
                        color: c.textPrimary,
                        fontSize: 15,
                        fontWeight: 'normal'
                    }
                }
            ],
            tooltip: {
                trigger: 'axis',
                axisPointer: {
                    type: 'cross',
                    link: { xAxisIndex: 'all' },
                    label: {
                        backgroundColor: c.surface
                    }
                },
                backgroundColor: c.surface,
                borderColor: c.border,
                textStyle: {
                    color: c.textSecondary
                },
                formatter: (params) => {
                    const date = params[0].axisValue;
                    const close = params.find(p => p.seriesName === 'VIX 收盘价');
                    const pct = params.find(p => p.seriesName === percentileLabel);
                    let html = `<div style="font-weight:700;margin-bottom:6px;">${date}</div>`;
                    if (close) {
                        html += `<div style="color:${c.primary};">VIX 收盘价: <strong>${parseFloat(close.value).toFixed(2)}</strong></div>`;
                    }
                    if (pct) {
                        html += `<div style="color:${c.secondary};">${percentileLabel}: <strong>${parseFloat(pct.value).toFixed(1)}%</strong></div>`;
                    }
                    return html;
                }
            },
            legend: [
                {
                    data: ['VIX 收盘价'],
                    top: '6%',
                    textStyle: { color: c.textMuted }
                },
                {
                    data: [percentileLabel],
                    top: '57%',
                    textStyle: { color: c.textMuted }
                }
            ],
            grid: [
                {
                    left: '3%',
                    right: '4%',
                    top: '14%',
                    height: '37%',
                    containLabel: true
                },
                {
                    left: '3%',
                    right: '4%',
                    top: '62%',
                    height: '29%',
                    containLabel: true
                }
            ],
            xAxis: [
                {
                    type: 'category',
                    boundaryGap: false,
                    data: dates,
                    gridIndex: 0,
                    axisLine: { lineStyle: { color: c.textSubtle } },
                    axisLabel: { show: false },
                    axisTick: { show: false }
                },
                {
                    type: 'category',
                    boundaryGap: false,
                    data: dates,
                    gridIndex: 1,
                    axisLine: { lineStyle: { color: c.textSubtle } },
                    axisLabel: { color: c.textMuted }
                }
            ],
            yAxis: [
                {
                    type: 'value',
                    name: 'VIX',
                    gridIndex: 0,
                    position: 'left',
                    axisLine: { show: true, lineStyle: { color: c.primary } },
                    axisLabel: { color: c.textMuted },
                    splitLine: { lineStyle: { color: c.border, type: 'dashed' } },
                    nameTextStyle: { color: c.primary }
                },
                {
                    type: 'value',
                    name: '百分位',
                    gridIndex: 1,
                    position: 'left',
                    min: 0,
                    max: 100,
                    axisLine: { show: true, lineStyle: { color: c.secondary } },
                    axisLabel: { color: c.textMuted, formatter: '{value}%' },
                    splitLine: { lineStyle: { color: c.border, type: 'dashed' } },
                    nameTextStyle: { color: c.secondary }
                }
            ],
            dataZoom: [
                {
                    type: 'inside',
                    xAxisIndex: [0, 1],
                    start: 0,
                    end: 100,
                    zoomOnMouseWheel: true,
                    moveOnMouseMove: true,
                    moveOnMouseWheel: true
                },
                {
                    type: 'slider',
                    xAxisIndex: [0, 1],
                    start: 0,
                    end: 100,
                    bottom: '2%',
                    height: 24,
                    borderColor: c.border,
                    fillerColor: `${this.hexToRgba(c.primary, 0.2)}`,
                    handleStyle: { color: c.primary },
                    textStyle: { color: c.textMuted },
                    dataBackground: {
                        lineStyle: { color: c.textSubtle },
                        areaStyle: { color: c.surface }
                    }
                }
            ],
            visualMap: {
                type: 'piecewise',
                seriesIndex: 1,
                dimension: 1,
                show: false,
                pieces: this.getPercentilePieces().map(p => ({
                    min: p.min,
                    max: p.max,
                    maxOpen: p.maxOpen,
                    color: p.color
                }))
            },
            series: [
                {
                    name: 'VIX 收盘价',
                    type: 'line',
                    data: closes,
                    xAxisIndex: 0,
                    yAxisIndex: 0,
                    smooth: false,
                    symbol: 'none',
                    sampling: 'lttb',
                    progressive: 2000,
                    lineStyle: { color: c.primary, width: 1.5 },
                    itemStyle: { color: c.primary },
                    markLine: {
                        silent: true,
                        symbol: 'none',
                        data: [
                            {
                                yAxis: VIX_MARK_LINE_LOW,
                                label: { formatter: String(VIX_MARK_LINE_LOW), color: c.textMuted },
                                lineStyle: { color: c.textSubtle, type: 'dashed' }
                            },
                            {
                                yAxis: VIX_MARK_LINE_HIGH,
                                label: { formatter: String(VIX_MARK_LINE_HIGH), color: c.secondary },
                                lineStyle: { color: c.secondary, type: 'dashed' }
                            }
                        ]
                    },
                    markPoint: {
                        data: [
                            {
                                type: 'max',
                                name: '历史最高',
                                label: { color: '#fff', formatter: '{c}' },
                                itemStyle: { color: c.danger }
                            }
                        ]
                    }
                },
                {
                    name: percentileLabel,
                    type: 'line',
                    data: percentiles,
                    xAxisIndex: 1,
                    yAxisIndex: 1,
                    smooth: true,
                    symbol: 'none',
                    lineStyle: { width: 2 },
                    areaStyle: chartType === 'area' ? { opacity: 0.35 } : null,
                    markLine: {
                        silent: true,
                        symbol: 'none',
                        data: [
                            {
                                yAxis: PERCENTILE_MARK_LINE_MEDIAN,
                                label: { formatter: '50%', color: c.textMuted },
                                lineStyle: { color: c.textSubtle, type: 'dashed' }
                            }
                        ]
                    }
                }
            ]
        };

        this.chart.setOption(option, true);
    }

    hexToRgba(hex, alpha) {
        const clean = hex.replace('#', '');
        const bigint = parseInt(clean, 16);
        const r = (bigint >> 16) & 255;
        const g = (bigint >> 8) & 255;
        const b = bigint & 255;
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }
}

// Initialize dashboard when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new VIXDashboard();
});
