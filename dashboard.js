class VIXDashboard {
    constructor() {
        this.data = [];
        this.dates = [];
        this.closes = [];
        this.ohlc = [];
        this.flatDots = [];
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
        return VIXDashboardCore.PERCENTILE_PIECES;
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

    populatePercentileOptions() {
        const select = document.getElementById('percentileSelect');
        select.innerHTML = '';
        VIXDashboardCore.PERCENTILE_WINDOWS.forEach(w => {
            const option = document.createElement('option');
            option.value = w.value;
            option.textContent = w.label;
            select.appendChild(option);
        });
    }

    init() {
        if (typeof echarts === 'undefined') {
            this.showError('ECharts 库加载失败，请刷新页面重试');
            return;
        }

        this.populatePercentileOptions();

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

        this.chart.on('dataZoom', (params) => {
            const batch = params.batch && params.batch[0];
            if (batch) this.updateVisibleMaxMarkPoint(batch);
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

            this.data = VIXDashboardCore.parseCSV(text);
            this.dates = this.data.map(d => d.dateStr);
            this.closes = this.data.map(d => d.close);

            if (this.data.length === 0) {
                throw new Error('CSV 解析结果为空');
            }

            this.ohlc = this.data.map(d => [d.open, d.close, d.low, d.high]);
            this.flatDots = this.data
                .map((d, i) => (d.open === d.high && d.high === d.low && d.low === d.close) ? [i, d.close] : null)
                .filter(p => p !== null);

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
        return VIXDashboardCore.parseCSV(text);
    }

    parseDate(dateStr) {
        return VIXDashboardCore.parseDate(dateStr);
    }

    computeFullPercentile() {
        VIXDashboardCore.computeFullPercentile(this.data, this.closes);
    }

    computeRollingPercentile(window) {
        VIXDashboardCore.computeRollingPercentile(this.data, this.closes, window);
    }

    getPercentileKey() {
        const type = document.getElementById('percentileSelect').value;
        return type === 'full' ? 'percentileFull' : `percentile${type}`;
    }

    getPercentileLabel() {
        const type = document.getElementById('percentileSelect').value;
        const window = VIXDashboardCore.PERCENTILE_WINDOWS.find(w => String(w.value) === type);
        return window ? window.label : '历史百分位';
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
        const ohlc = this.ohlc;
        const flatDots = this.flatDots;
        const percentiles = this.data.map(d => d[percentileKey]);

        const currentOption = this.chart.getOption() || {};
        const currentDataZoom = currentOption.dataZoom && currentOption.dataZoom[0];
        const zoomState = currentDataZoom ? {
            start: currentDataZoom.start !== undefined ? currentDataZoom.start : 0,
            end: currentDataZoom.end !== undefined ? currentDataZoom.end : 100,
            ...(currentDataZoom.startValue !== undefined ? {
                startValue: currentDataZoom.startValue,
                endValue: currentDataZoom.endValue
            } : {})
        } : { start: 0, end: 100 };

        const c = this.colors;

        const option = {
            backgroundColor: 'transparent',
            animation: false,
            textStyle: {
                color: c.textMuted
            },
            title: [
                {
                    text: 'VIX 历史 K 线',
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
                    const date = VIXDashboardCore.escapeHtml(params[0].axisValue);
                    const candle = params.find(p => p.seriesName === 'VIX K线');
                    const idx = candle ? candle.dataIndex : params[0].dataIndex;
                    const d = this.data[idx];
                    const pct = params.find(p => p.seriesName === percentileLabel);
                    let html = `<div style="font-weight:700;margin-bottom:6px;">${date}</div>`;
                    if (d) {
                        const color = d.close >= d.open ? '#ef4444' : '#22c55e';
                        html += `<div style="color:${color};">开: <strong>${d.open.toFixed(2)}</strong> 高: <strong>${d.high.toFixed(2)}</strong> 低: <strong>${d.low.toFixed(2)}</strong> 收: <strong>${d.close.toFixed(2)}</strong></div>`;
                    }
                    if (pct) {
                        html += `<div style="color:${c.secondary};">${percentileLabel}: <strong>${parseFloat(pct.value).toFixed(1)}%</strong></div>`;
                    }
                    return html;
                }
            },
            legend: [
                {
                    data: ['VIX K线'],
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
                    boundaryGap: true,
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
                    ...zoomState,
                    zoomOnMouseWheel: true,
                    moveOnMouseMove: true,
                    moveOnMouseWheel: true
                },
                {
                    type: 'slider',
                    xAxisIndex: [0, 1],
                    ...zoomState,
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
                seriesIndex: 2,
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
                    name: 'VIX K线',
                    type: 'candlestick',
                    data: ohlc,
                    xAxisIndex: 0,
                    yAxisIndex: 0,
                    progressive: 2000,
                    itemStyle: {
                        color: '#ef4444',
                        color0: '#22c55e',
                        borderColor: '#ef4444',
                        borderColor0: '#22c55e'
                    },
                    markLine: {
                        silent: true,
                        symbol: 'none',
                        data: [
                            {
                                yAxis: VIXDashboardCore.VIX_MARK_LINE_LOW,
                                label: { formatter: String(VIXDashboardCore.VIX_MARK_LINE_LOW), color: c.textMuted },
                                lineStyle: { color: c.textSubtle, type: 'dashed' }
                            },
                            {
                                yAxis: VIXDashboardCore.VIX_MARK_LINE_HIGH,
                                label: { formatter: String(VIXDashboardCore.VIX_MARK_LINE_HIGH), color: c.secondary },
                                lineStyle: { color: c.secondary, type: 'dashed' }
                            }
                        ]
                    },
                },
                {
                    name: 'VIX 平线',
                    type: 'scatter',
                    data: flatDots,
                    xAxisIndex: 0,
                    yAxisIndex: 0,
                    symbol: 'circle',
                    symbolSize: 3,
                    itemStyle: { color: c.textMuted },
                    tooltip: { show: false },
                    emphasis: { scale: false }
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
                                yAxis: VIXDashboardCore.PERCENTILE_MARK_LINE_MEDIAN,
                                label: { formatter: '50%', color: c.textMuted },
                                lineStyle: { color: c.textSubtle, type: 'dashed' }
                            }
                        ]
                    }
                }
            ]
        };

        this.chart.setOption(option, true);
        this.updateVisibleMaxMarkPoint();
    }

    updateVisibleMaxMarkPoint(eventBatch) {
        if (!this.chart || this.data.length === 0) return;

        let startIdx = 0;
        let endIdx = this.data.length - 1;

        if (eventBatch && eventBatch.startValue !== undefined && eventBatch.endValue !== undefined) {
            startIdx = Math.floor(eventBatch.startValue);
            endIdx = Math.ceil(eventBatch.endValue);
        } else {
            const option = this.chart.getOption() || {};
            const dz = option.dataZoom && option.dataZoom[0];
            if (dz) {
                if (dz.startValue !== undefined && dz.endValue !== undefined) {
                    startIdx = Math.floor(dz.startValue);
                    endIdx = Math.ceil(dz.endValue);
                } else if (dz.start !== undefined && dz.end !== undefined) {
                    const n = this.data.length;
                    startIdx = Math.floor(n * dz.start / 100);
                    endIdx = Math.ceil(n * dz.end / 100) - 1;
                }
            }
        }

        startIdx = Math.max(0, Math.min(this.data.length - 1, startIdx));
        endIdx = Math.max(0, Math.min(this.data.length - 1, endIdx));
        if (startIdx > endIdx) {
            [startIdx, endIdx] = [endIdx, startIdx];
        }

        let maxClose = -Infinity;
        let maxIdx = startIdx;
        for (let i = startIdx; i <= endIdx; i++) {
            const v = this.closes[i];
            if (v > maxClose) {
                maxClose = v;
                maxIdx = i;
            }
        }

        this.chart.setOption({
            series: [{
                name: 'VIX K线',
                markPoint: {
                    data: [{
                        name: '窗口最高',
                        coord: [maxIdx, maxClose],
                        value: maxClose,
                        label: { color: '#fff', formatter: '{c}' },
                        itemStyle: { color: this.colors.danger }
                    }]
                }
            }]
        });
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

// Initialize dashboard when DOM is ready (browser only)
if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => {
        new VIXDashboard();
    });
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { VIXDashboard };
}
