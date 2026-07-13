// 四图布局常量（基于容器高度的百分比）
const CHART_TITLE_TOP_PCT = 1;
const CHART_GRID_TOP_PCT = 5;
const CHART_GRID_HEIGHT_PCT = 17;
const CHART_SECTION_GAP_PCT = 2;

// 图表交互与坐标轴常量
const GRID_LEFT_MARGIN = 80;
const ZOOM_DEBOUNCE_MS = 80;
const VIX_AXIS_MAX = 100;
const PERCENTILE_AXIS_MAX = 100;
const NDX_LOG_AXIS_PADDING = 1.05;

// SPX K线配色（橙/琥珀色系，与 NDX 红绿实心区分）
const SPX_UP_COLOR = '#f97316';
const SPX_DOWN_COLOR = '#fbbf24';

const CHART_LAYOUT = (() => {
    const stride = CHART_GRID_TOP_PCT + CHART_GRID_HEIGHT_PCT + CHART_SECTION_GAP_PCT - CHART_TITLE_TOP_PCT;
    return {
        titleTops: [0, 1, 2, 3].map(i => `${CHART_TITLE_TOP_PCT + i * stride}%`),
        gridTops: [0, 1, 2, 3].map(i => `${CHART_GRID_TOP_PCT + i * stride}%`),
        gridHeight: `${CHART_GRID_HEIGHT_PCT}%`
    };
})();

class VIXDashboard {
    constructor() {
        this.data = [];
        this.dates = [];
        this.closes = [];
        this.ohlc = [];
        this.flatDots = [];
        this.ndxData = [];
        this.ndxOhlc = [];
        this.ndxFlatDots = [];
        this.ndxError = null;
        this.spxData = [];
        this.spxOhlc = [];
        this.spxFlatDots = [];
        this.spxError = null;
        this.peData = [];
        this.peSeries = [];
        this.peError = null;
        this.ndxLogScale = true;
        this.ndxVisible = true;
        this.spxVisible = true;
        this.chart = null;
        this.computedWindows = new Set(['full']);
        this.eventAnnotations = [];
        this.resizeHandler = null;
        this.zoomUpdateTimer = null;
        this.datePickers = {};
        this.activeDateInputId = null;
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

    renderThresholdTable() {
        const container = document.getElementById('thresholdTable');
        if (!container) return;

        const thresholds = VIXDashboardCore.VIX_THRESHOLDS;
        const table = document.createElement('table');
        table.className = 'threshold-ref-table';

        const thead = document.createElement('thead');
        const headerRow = document.createElement('tr');
        ['区间', '经济含义', '说明'].forEach(text => {
            const th = document.createElement('th');
            th.textContent = text;
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        table.appendChild(thead);

        const tbody = document.createElement('tbody');
        thresholds.forEach((t, index) => {
            const isFirst = index === 0;
            const isLast = index === thresholds.length - 1;
            let rangeText;
            if (isFirst) {
                rangeText = `VIX < ${t.max}`;
            } else if (isLast) {
                rangeText = `VIX ≥ ${t.min}`;
            } else {
                rangeText = `${t.min} ≤ VIX < ${t.max}`;
            }
            const row = document.createElement('tr');

            const rangeCell = document.createElement('td');
            rangeCell.className = 'threshold-range';
            rangeCell.style.color = t.color;
            rangeCell.textContent = rangeText;
            row.appendChild(rangeCell);

            const labelCell = document.createElement('td');
            labelCell.className = 'threshold-label';
            labelCell.style.color = t.color;
            labelCell.textContent = t.label;
            row.appendChild(labelCell);

            const descCell = document.createElement('td');
            descCell.className = 'threshold-desc';
            descCell.textContent = t.description;
            row.appendChild(descCell);

            tbody.appendChild(row);
        });
        table.appendChild(tbody);
        container.appendChild(table);
    }

    init() {
        if (typeof echarts === 'undefined') {
            this.showError('ECharts 库加载失败，请刷新页面重试');
            return;
        }

        this.populatePercentileOptions();
        this.renderThresholdTable();

        const chartDom = document.getElementById('chart');
        this.chart = echarts.init(chartDom);
        this.bindEvents();
        this.loadData();
        this.loadUpdateInfo();
        this.loadNdxPE();

        this.resizeHandler = () => {
            if (this.chart) this.chart.resize();
        };
        window.addEventListener('resize', this.resizeHandler);

        window.addEventListener('beforeunload', () => {
            clearTimeout(this.zoomUpdateTimer);
            if (this.resizeHandler) {
                window.removeEventListener('resize', this.resizeHandler);
            }
            if (this.chart) {
                this.chart.dispose();
                this.chart = null;
            }
            Object.values(this.datePickers).forEach(dp => {
                if (dp && typeof dp.destroy === 'function') dp.destroy();
            });
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

        document.getElementById('indexScale').addEventListener('change', (e) => {
            this.ndxLogScale = e.target.value === 'log';
            this.updateChart();
        });

        document.getElementById('resetZoom').addEventListener('click', () => {
            this.chart.dispatchAction({
                type: 'dataZoom',
                start: 0,
                end: 100
            });
        });

        document.getElementById('startDate').addEventListener('change', () => {
            this.applyDateRange();
        });

        document.getElementById('endDate').addEventListener('change', () => {
            this.applyDateRange();
        });

        document.addEventListener('click', (e) => {
            const todayBtn = e.target.closest('.dp-today');
            if (!todayBtn) return;
            if (!this.data.length) return;

            const inputId = this.activeDateInputId;
            if (!inputId || !this.datePickers[inputId]) return;

            e.preventDefault();
            e.stopPropagation();

            const input = document.getElementById(inputId);
            const latest = VIXDashboardCore.formatISODate(this.data[this.data.length - 1].date);
            input.value = latest;
            input.dispatchEvent(new Event('change', { bubbles: true }));
            this.datePickers[inputId].close();
        }, true);

        this.chart.on('dataZoom', (params) => {
            const batch = (params.batch && params.batch[0]) || params;
            clearTimeout(this.zoomUpdateTimer);
            this.zoomUpdateTimer = setTimeout(() => this.updateVisibleRanges(batch), ZOOM_DEBOUNCE_MS);
        });

        this.chart.on('click', (params) => {
            this.handleAxisClick(params);
        });
    }

    handleAxisClick(params) {
        const result = VIXDashboardCore.resolveAxisToggle(
            params,
            VIXDashboardCore.AXIS_NAME_NDX,
            VIXDashboardCore.AXIS_NAME_SPX,
            { ndxVisible: this.ndxVisible, spxVisible: this.spxVisible }
        );

        if (result.changed) {
            this.ndxVisible = result.ndxVisible;
            this.spxVisible = result.spxVisible;
            this.updateChart();
        }
    }

    initDateInputs() {
        if (!this.data.length) return;
        const startInput = document.getElementById('startDate');
        const endInput = document.getElementById('endDate');
        if (!startInput || !endInput) return;

        const first = VIXDashboardCore.formatISODate(this.data[0].date);
        const last = VIXDashboardCore.formatISODate(this.data[this.data.length - 1].date);
        startInput.value = first;
        endInput.value = last;

        [startInput, endInput].forEach(input => {
            input.addEventListener('focus', () => {
                this.activeDateInputId = input.id;
            });
            input.addEventListener('input', () => {
                input.classList.remove('invalid');
            });
            input.addEventListener('blur', () => {
                this.validateDateInput(input);
            });
        });

        if (typeof TinyDatePicker === 'undefined') return;

        // 注意：tiny-date-picker 目前没有内置 ARIA 支持，属于可访问性降级。
        // 若未来需要满足高可访问性标准，应替换为带 ARIA 的日期选择组件。
        const toLocalMidnight = (utcDate) => {
            return new Date(utcDate.getUTCFullYear(), utcDate.getUTCMonth(), utcDate.getUTCDate());
        };

        const options = {
            mode: 'dp-below',
            lang: {
                days: ['日', '一', '二', '三', '四', '五', '六'],
                months: ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月'],
                today: '最新',
                clear: '清除',
                close: '关闭'
            },
            format(date) {
                if (!date) return '';
                const y = date.getFullYear();
                const m = String(date.getMonth() + 1).padStart(2, '0');
                const d = String(date.getDate()).padStart(2, '0');
                return `${y}-${m}-${d}`;
            },
            parse(str) {
                if (str instanceof Date) return str;
                return VIXDashboardCore.parseISODate(str);
            },
            min: toLocalMidnight(this.data[0].date),
            max: toLocalMidnight(this.data[this.data.length - 1].date)
        };

        if (this.datePickers.startDate) this.datePickers.startDate.destroy();
        if (this.datePickers.endDate) this.datePickers.endDate.destroy();

        this.datePickers.startDate = TinyDatePicker(startInput, options);
        this.datePickers.endDate = TinyDatePicker(endInput, options);
    }

    validateDateInput(input) {
        if (!input) return;
        const value = input.value.trim();
        if (!value || VIXDashboardCore.parseISODate(value)) {
            input.classList.remove('invalid');
        } else {
            input.classList.add('invalid');
        }
    }

    applyDateRange() {
        if (!this.data.length || !this.chart) return;
        const startInput = document.getElementById('startDate');
        const endInput = document.getElementById('endDate');
        if (!startInput || !endInput) return;

        let startDate = VIXDashboardCore.parseISODate(startInput.value) || this.data[0].date;
        let endDate = VIXDashboardCore.parseISODate(endInput.value) || this.data[this.data.length - 1].date;
        if (startDate > endDate) {
            [startDate, endDate] = [endDate, startDate];
        }

        const n = this.data.length;
        const startIdx = Math.max(0, Math.min(n - 1,
            VIXDashboardCore.lowerBound(this.data, startDate.getTime(),
                (a, b) => a.date.getTime() < b)));
        const endIdx = Math.max(0, Math.min(n - 1,
            VIXDashboardCore.lowerBound(this.data, endDate.getTime(),
                (a, b) => a.date.getTime() <= b) - 1));

        const [finalStartIdx, finalEndIdx] = startIdx <= endIdx
            ? [startIdx, endIdx]
            : [endIdx, startIdx];

        this.chart.dispatchAction({
            type: 'dataZoom',
            startValue: finalStartIdx,
            endValue: finalEndIdx
        });
    }

    updateDateInputs(startIdx, endIdx) {
        const startInput = document.getElementById('startDate');
        const endInput = document.getElementById('endDate');
        if (!startInput || !endInput || !this.data.length) return;

        const startDate = VIXDashboardCore.formatISODate(this.data[startIdx].date);
        const endDate = VIXDashboardCore.formatISODate(this.data[endIdx].date);
        if (startInput.value !== startDate) startInput.value = startDate;
        if (endInput.value !== endDate) endInput.value = endDate;
    }

    async loadData() {
        this.showLoading('正在加载 VIX / 指数历史数据...');
        try {
            const [vixResponse, ndxResponse, spxResponse, peResponse] = await Promise.all([
                fetch('data/VIX_History.csv', { cache: 'no-store' }),
                fetch('data/NDX_History.csv', { cache: 'no-store' }),
                fetch('data/SPX_History.csv', { cache: 'no-store' }),
                fetch('data/nasdaq100_pe_history.csv', { cache: 'no-store' })
            ]);

            if (!vixResponse.ok) {
                throw new Error(`VIX HTTP ${vixResponse.status}: ${vixResponse.statusText}`);
            }
            const vixText = await vixResponse.text();

            this.data = VIXDashboardCore.parseCSV(vixText);
            this.dates = this.data.map(d => d.dateStr);
            this.closes = this.data.map(d => d.close);

            if (this.data.length === 0) {
                throw new Error('VIX CSV 解析结果为空');
            }

            this.ohlc = this.data.map(d => [d.open, d.close, d.low, d.high]);
            this.flatDots = this.data
                .map((d, i) => (d.open === d.high && d.high === d.low && d.low === d.close) ? [i, d.close] : null)
                .filter(p => p !== null);

            if (ndxResponse.ok) {
                try {
                    const ndxText = await ndxResponse.text();
                    this.ndxData = VIXDashboardCore.parseCSV(ndxText);
                    this.alignNdxToVix();
                    this.hideWarning('ndx-warning');
                } catch (ndxErr) {
                    console.warn('[VIX Dashboard] NDX parse failed:', ndxErr);
                    this.ndxError = '纳斯达克100 数据解析失败：' + ndxErr.message;
                    this.showWarning('ndx-warning', this.ndxError);
                }
            } else {
                console.warn('[VIX Dashboard] NDX load failed:', ndxResponse.status);
                this.ndxError = `纳斯达克100 数据加载失败：HTTP ${ndxResponse.status}`;
                this.showWarning('ndx-warning', this.ndxError);
            }

            if (spxResponse.ok) {
                try {
                    const spxText = await spxResponse.text();
                    this.spxData = VIXDashboardCore.parseCSV(spxText);
                    this.alignSpxToVix();
                    this.hideWarning('spx-warning');
                } catch (spxErr) {
                    console.warn('[VIX Dashboard] SPX parse failed:', spxErr);
                    this.spxError = '标普500 数据解析失败：' + spxErr.message;
                    this.showWarning('spx-warning', this.spxError);
                }
            } else {
                console.warn('[VIX Dashboard] SPX load failed:', spxResponse.status);
                this.spxError = `标普500 数据加载失败：HTTP ${spxResponse.status}`;
                this.showWarning('spx-warning', this.spxError);
            }

            if (peResponse.ok) {
                try {
                    const peText = await peResponse.text();
                    this.peData = VIXDashboardCore.parsePEHistoryCSV(peText);
                    this.alignPEToVix();
                    this.hideWarning('pe-warning');
                } catch (peErr) {
                    console.warn('[VIX Dashboard] PE history parse failed:', peErr);
                    this.peError = 'NDX 滚动PE 历史数据解析失败：' + peErr.message;
                    this.showWarning('pe-warning', this.peError);
                }
            } else {
                console.warn('[VIX Dashboard] PE history load failed:', peResponse.status);
                this.peError = `NDX 滚动PE 历史数据加载失败：HTTP ${peResponse.status}`;
                this.showWarning('pe-warning', this.peError);
            }

            this.hideLoading();
            this.showLoading('正在计算历史百分位...');
            // 使用 requestAnimationFrame + setTimeout 让加载提示先渲染，避免阻塞 UI
            requestAnimationFrame(() => {
                setTimeout(() => {
                    try {
                        this.computeEventAnnotations();
                        this.computeFullPercentile();
                        this.hideLoading();
                        this.updateStats();
                        this.initDateInputs();
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

    alignIndexToVix(indexData) {
        const dateKey = d => d.date.toISOString().split('T')[0];
        const map = new Map();
        indexData.forEach(d => {
            map.set(dateKey(d), d);
        });

        const ohlc = this.data.map(d => {
            const item = map.get(dateKey(d));
            if (!item) return null;
            return [item.open, item.close, item.low, item.high];
        });

        const flatDots = this.data
            .map((d, i) => {
                const item = map.get(dateKey(d));
                if (!item) return null;
                if (item.open === item.high && item.high === item.low && item.low === item.close) {
                    return [i, item.close];
                }
                return null;
            })
            .filter(p => p !== null);

        const prevCloses = VIXDashboardCore.buildPreviousCloseArray(ohlc);

        return { ohlc, flatDots, prevCloses };
    }

    alignNdxToVix() {
        const aligned = this.alignIndexToVix(this.ndxData);
        this.ndxOhlc = aligned.ohlc;
        this.ndxFlatDots = aligned.flatDots;
        this.ndxPrevCloses = aligned.prevCloses;
    }

    alignSpxToVix() {
        const aligned = this.alignIndexToVix(this.spxData);
        this.spxOhlc = aligned.ohlc;
        this.spxFlatDots = aligned.flatDots;
        this.spxPrevCloses = aligned.prevCloses;
    }

    alignPEToVix() {
        this.peSeries = VIXDashboardCore.alignPEToVix(this.peData, this.data);
    }

    computeEventAnnotations() {
        this.eventAnnotations = [];
        if (!this.data.length) return;

        const dateToIndex = new Map();
        this.data.forEach((d, i) => {
            dateToIndex.set(d.date.toISOString().split('T')[0], i);
        });

        this.eventAnnotations = VIXDashboardCore.VIX_EVENT_ANNOTATIONS
            .map(evt => {
                const idx = dateToIndex.get(evt.date);
                if (idx === undefined) {
                    console.warn(`[VIX Dashboard] 事件标注日期未找到数据: ${evt.date}`);
                    return null;
                }
                const d = this.data[idx];
                return {
                    name: evt.label,
                    xAxis: idx,
                    yAxis: d.high,
                    value: d.high,
                    itemStyle: { color: '#f59e0b' },
                    label: {
                        show: evt.showLabel !== false,
                        position: 'top',
                        distance: 8,
                        color: '#fbbf24',
                        fontSize: 11,
                        fontWeight: 'bold',
                        formatter: '{b}'
                    },
                    tooltip: {
                        trigger: 'item',
                        backgroundColor: this.colors.surface,
                        borderColor: this.colors.border,
                        textStyle: { color: this.colors.textSecondary },
                        // 注意：evt.date/label/description 当前为硬编码常量，安全。
                        // 若未来改为外部配置，必须先转义再插入 HTML，防止 XSS。
                        formatter: `<div style="font-weight:700;margin-bottom:6px;">${evt.date} · ${evt.label}</div>` +
                            `<div style="max-width:280px;line-height:1.5;color:#e2e8f0;">${evt.description}</div>` +
                            `<div style="margin-top:6px;color:#94a3b8;">VIX 日内最高: <strong>${d.high.toFixed(2)}</strong></div>`
                    },
                    symbol: 'pin',
                    symbolSize: 28,
                    symbolRotate: 0
                };
            })
            .filter(a => a !== null);
    }

    async loadUpdateInfo() {
        const elem = document.getElementById('statDataUpdateTime');
        const sources = {
            vix: { url: 'data/last_update.json', label: 'VIX', defaultSource: 'CBOE' },
            ndx: { url: 'data/ndx_last_update.json', label: 'NDX', defaultSource: 'Yahoo Finance' },
            spx: { url: 'data/spx_last_update.json', label: 'SPX', defaultSource: 'Yahoo Finance' },
            ndx_pe: { url: 'data/ndx_pe_last_update.json', label: 'NDX PE', defaultSource: 'Yahoo Finance' }
        };

        const entries = await Promise.all(
            Object.entries(sources).map(async ([key, cfg]) => {
                try {
                    const response = await fetch(cfg.url, { cache: 'no-store' });
                    if (!response.ok) {
                        if (response.status === 404) {
                            return { key, label: cfg.label, text: '未记录', title: '' };
                        }
                        throw new Error(`HTTP ${response.status}`);
                    }
                    const info = await response.json();
                    const dateText = info.updatedAt ? this.formatDate(info.updatedAt) : '未知';
                    const title = `数据源: ${info.source || cfg.defaultSource}\n最新数据日期: ${info.latestDate || '未知'}\n状态: ${this.translateStatus(info.status)}`;
                    return { key, label: cfg.label, text: dateText, title };
                } catch (error) {
                    console.warn(`[VIX Dashboard] Update info load failed (${cfg.url}):`, error);
                    return { key, label: cfg.label, text: '未知', title: '' };
                }
            })
        );

        if (!elem) return;
        const parts = entries.map(e => `<div>${e.label} ${e.text}</div>`).join('');
        const titles = entries.map(e => e.title).filter(Boolean).join('\n\n');
        elem.innerHTML = `<div style="font-size:0.75rem;line-height:1.5;">${parts}</div>`;
        elem.title = titles;
    }

    async loadNdxPE() {
        const elem = document.getElementById('statNdxPE');
        if (!elem) return;

        try {
            const response = await fetch('data/ndx_pe.json', { cache: 'no-store' });
            if (!response.ok) {
                if (response.status === 404) {
                    elem.textContent = '未记录';
                    elem.title = '尚未生成 ndx_pe.json，请运行 scripts/fetch_ndx_pe.py 或 scripts/start.py';
                } else {
                    throw new Error(`HTTP ${response.status}`);
                }
                return;
            }
            const text = await response.text();
            const pe = VIXDashboardCore.parseNDXPE(text);
            if (!pe) {
                elem.textContent = '无效';
                elem.title = 'ndx_pe.json 内容解析失败';
                return;
            }
            elem.textContent = pe.trailingPE.toFixed(2);
            const lines = [
                `滚动 PE (TTM): ${pe.trailingPE.toFixed(2)}`,
                pe.forwardPE ? `前瞻 PE: ${pe.forwardPE.toFixed(2)}` : '前瞻 PE: 暂无（免费数据源未提供）',
                `数据来源: ${pe.source}`,
                pe.asOf ? `数据日期: ${pe.asOf}` : '',
                pe.fetchedAt ? `获取时间: ${this.formatDateTime(pe.fetchedAt)}` : ''
            ];
            elem.title = lines.filter(Boolean).join('\n');
        } catch (error) {
            console.warn('[VIX Dashboard] NDX PE load failed:', error);
            elem.textContent = '未知';
            elem.title = '加载失败：' + error.message;
        }
    }

    formatDate(isoString) {
        try {
            const date = new Date(isoString);
            if (isNaN(date.getTime())) return '未知';
            return VIXDashboardCore.formatISODate(date);
        } catch (e) {
            return '未知';
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

    showWarning(id, message) {
        const elem = document.getElementById(id);
        if (!elem) return;
        elem.textContent = message;
        elem.style.display = 'block';
    }

    hideWarning(id) {
        const elem = document.getElementById(id);
        if (!elem) return;
        elem.style.display = 'none';
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
        const regime = VIXDashboardCore.getVIXRegime(last.close);
        document.getElementById('statClose').textContent = last.close.toFixed(2);
        document.getElementById('statPercentile').textContent = percentile.toFixed(1) + '%' + (percentilePiece ? ' ' + percentilePiece.label : '');
        document.getElementById('statPercentile').style.color = percentilePiece ? percentilePiece.color : this.colors.textMuted;
        const regimeElem = document.getElementById('statRegime');
        regimeElem.textContent = regime ? regime.label : '未知';
        regimeElem.style.color = regime ? regime.color : this.colors.textMuted;
        regimeElem.title = regime ? regime.description : '无法判断当前 VIX 区间';
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
        const ndxOhlc = this.ndxOhlc;
        const ndxSeriesData = ndxOhlc.map(v => v === null ? '-' : v);
        const ndxFlatDots = this.ndxFlatDots;
        const spxOhlc = this.spxOhlc;
        const spxSeriesData = spxOhlc.map(v => v === null ? '-' : v);
        const spxFlatDots = this.spxFlatDots;
        const peSeries = this.peSeries;

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
                    top: CHART_LAYOUT.titleTops[0],
                    textStyle: {
                        color: c.textPrimary,
                        fontSize: 15,
                        fontWeight: 'normal'
                    }
                },
                {
                    text: percentileLabel,
                    left: 'center',
                    top: CHART_LAYOUT.titleTops[1],
                    textStyle: {
                        color: c.textPrimary,
                        fontSize: 15,
                        fontWeight: 'normal'
                    }
                },
                {
                    text: 'NASDAQ-100 / 标普500 历史 K 线',
                    left: 'center',
                    top: CHART_LAYOUT.titleTops[2],
                    textStyle: {
                        color: c.textPrimary,
                        fontSize: 15,
                        fontWeight: 'normal'
                    }
                },
                {
                    text: 'NASDAQ-100 滚动 PE (TTM)',
                    left: 'center',
                    top: CHART_LAYOUT.titleTops[3],
                    textStyle: {
                        color: c.textPrimary,
                        fontSize: 15,
                        fontWeight: 'normal'
                    }
                }
            ],
            axisPointer: {
                link: [{ xAxisIndex: 'all' }],
                lineStyle: {
                    color: c.textSubtle,
                    type: 'dashed'
                },
                label: {
                    backgroundColor: c.surface
                }
            },
            tooltip: {
                trigger: 'axis',
                axisPointer: {
                    type: 'line',
                    lineStyle: {
                        color: c.textSubtle,
                        type: 'dashed'
                    },
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
                    const ndxValues = this.ndxOhlc[idx];
                    const spxValues = this.spxOhlc[idx];

                    const formatIndexLine = (values, prevClose, upColor, downColor, label, decimals = 2) => {
                        const [o, cl, l, h] = values;
                        const color = prevClose !== null
                            ? (cl >= prevClose ? upColor : downColor)
                            : (cl >= o ? upColor : downColor);
                        const changePct = prevClose !== null ? ((cl - prevClose) / prevClose) * 100 : null;
                        const changeText = changePct !== null
                            ? ` 涨跌幅: <strong>${changePct >= 0 ? '+' : ''}${changePct.toFixed(decimals)}%</strong>`
                            : '';
                        return `<div style="color:${color};">${label} 开: <strong>${o.toFixed(decimals)}</strong> 高: <strong>${h.toFixed(decimals)}</strong> 低: <strong>${l.toFixed(decimals)}</strong> 收: <strong>${cl.toFixed(decimals)}</strong>${changeText}</div>`;
                    };

                    let html = `<div style="font-weight:700;margin-bottom:6px;">${date}</div>`;
                    if (d) {
                        const color = d.close >= d.open ? '#ef4444' : '#22c55e';
                        html += `<div style="color:${color};">VIX 开: <strong>${d.open.toFixed(2)}</strong> 高: <strong>${d.high.toFixed(2)}</strong> 低: <strong>${d.low.toFixed(2)}</strong> 收: <strong>${d.close.toFixed(2)}</strong></div>`;
                    }
                    if (pct) {
                        html += `<div style="color:${c.secondary};">${percentileLabel}: <strong>${parseFloat(pct.value).toFixed(1)}%</strong></div>`;
                    }
                    if (Array.isArray(ndxValues)) {
                        const ndxPrevClose = this.ndxPrevCloses ? this.ndxPrevCloses[idx] : null;
                        html += formatIndexLine(ndxValues, ndxPrevClose, '#ef4444', '#22c55e', 'NDX');
                    }
                    if (Array.isArray(spxValues)) {
                        const spxPrevClose = this.spxPrevCloses ? this.spxPrevCloses[idx] : null;
                        html += formatIndexLine(spxValues, spxPrevClose, SPX_UP_COLOR, SPX_DOWN_COLOR, 'SPX');
                    }
                    const peValue = this.peSeries[idx];
                    if (peValue !== null && peValue !== undefined) {
                        html += `<div style="color:${c.primary};">NDX 滚动 PE: <strong>${peValue.toFixed(2)}</strong></div>`;
                    }
                    return html;
                }
            },
            // 四个 grid 使用相同的 right 边距，确保时间轴长度一致。
            grid: [
                {
                    left: GRID_LEFT_MARGIN,
                    right: '8%',
                    top: CHART_LAYOUT.gridTops[0],
                    height: CHART_LAYOUT.gridHeight
                },
                {
                    left: GRID_LEFT_MARGIN,
                    right: '8%',
                    top: CHART_LAYOUT.gridTops[1],
                    height: CHART_LAYOUT.gridHeight
                },
                {
                    left: GRID_LEFT_MARGIN,
                    right: '8%',
                    top: CHART_LAYOUT.gridTops[2],
                    height: CHART_LAYOUT.gridHeight
                },
                {
                    left: GRID_LEFT_MARGIN,
                    right: '8%',
                    top: CHART_LAYOUT.gridTops[3],
                    height: CHART_LAYOUT.gridHeight
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
                    boundaryGap: true,
                    data: dates,
                    gridIndex: 1,
                    axisLine: { lineStyle: { color: c.textSubtle } },
                    axisLabel: { show: false }
                },
                {
                    type: 'category',
                    boundaryGap: true,
                    data: dates,
                    gridIndex: 2,
                    axisLine: { lineStyle: { color: c.textSubtle } },
                    axisLabel: { color: c.textMuted }
                },
                {
                    type: 'category',
                    boundaryGap: true,
                    data: dates,
                    gridIndex: 3,
                    axisLine: { lineStyle: { color: c.textSubtle } },
                    axisLabel: { show: false }
                }
            ],
            yAxis: [
                {
                    type: 'value',
                    name: 'VIX',
                    gridIndex: 0,
                    position: 'left',
                    min: 0,
                    max: VIX_AXIS_MAX,
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
                    max: PERCENTILE_AXIS_MAX,
                    axisLine: { show: true, lineStyle: { color: c.secondary } },
                    axisTick: { show: false },
                    axisLabel: { show: false },
                    splitLine: { show: false },
                    nameTextStyle: { color: c.secondary }
                },
                {
                    type: this.ndxLogScale ? 'log' : 'value',
                    name: VIXDashboardCore.AXIS_NAME_NDX,
                    gridIndex: 2,
                    position: 'left',
                    scale: true,
                    triggerEvent: true,
                    axisLine: { show: true, lineStyle: { color: this.ndxVisible ? c.primary : c.textMuted } },
                    axisLabel: { color: this.ndxVisible ? c.textMuted : c.textSubtle, formatter: value => Math.round(value).toString() },
                    splitLine: { lineStyle: { color: c.border, type: 'dashed' } },
                    nameTextStyle: { color: this.ndxVisible ? c.primary : c.textMuted },
                    logBase: 10
                },
                {
                    type: this.ndxLogScale ? 'log' : 'value',
                    name: VIXDashboardCore.AXIS_NAME_SPX,
                    gridIndex: 2,
                    position: 'right',
                    scale: true,
                    triggerEvent: true,
                    axisLine: { show: true, lineStyle: { color: this.spxVisible ? c.secondary : c.textMuted } },
                    axisLabel: { color: this.spxVisible ? c.textMuted : c.textSubtle, formatter: value => Math.round(value).toString() },
                    splitLine: { show: false },
                    nameTextStyle: { color: this.spxVisible ? c.secondary : c.textMuted },
                    logBase: 10
                },
                {
                    type: 'value',
                    name: 'PE',
                    gridIndex: 3,
                    position: 'left',
                    scale: true,
                    axisLine: { show: true, lineStyle: { color: c.primary } },
                    axisLabel: { color: c.textMuted },
                    splitLine: { lineStyle: { color: c.border, type: 'dashed' } },
                    nameTextStyle: { color: c.primary }
                }
            ],
            dataZoom: [
                {
                    type: 'inside',
                    xAxisIndex: [0, 1, 2, 3],
                    ...zoomState,
                    zoomOnMouseWheel: true,
                    moveOnMouseMove: true,
                    moveOnMouseWheel: true
                },
                {
                    type: 'slider',
                    xAxisIndex: [0, 1, 2, 3],
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
                        label: {
                            position: 'start',
                            formatter: params => params.value + '%',
                            color: c.textMuted
                        },
                        lineStyle: { color: c.border, type: 'dashed' },
                        data: VIXDashboardCore.PERCENTILE_PIECE_BOUNDARIES
                            .filter(value => value > 0 && value < 100)
                            .map(value => ({ yAxis: value }))
                    }
                },
                {
                    name: 'NASDAQ-100 K线',
                    type: 'candlestick',
                    data: this.ndxVisible ? ndxSeriesData : [],
                    xAxisIndex: 2,
                    yAxisIndex: 2,
                    itemStyle: {
                        color: '#ef4444',
                        color0: '#22c55e',
                        borderColor: '#ef4444',
                        borderColor0: '#22c55e'
                    }
                },
                {
                    name: 'NASDAQ-100 平线',
                    type: 'scatter',
                    data: this.ndxVisible ? ndxFlatDots : [],
                    xAxisIndex: 2,
                    yAxisIndex: 2,
                    symbol: 'circle',
                    symbolSize: 3,
                    itemStyle: { color: c.textMuted },
                    tooltip: { show: false },
                    emphasis: { scale: false }
                },
                {
                    name: 'S&P 500 K线',
                    type: 'candlestick',
                    data: this.spxVisible ? spxSeriesData : [],
                    xAxisIndex: 2,
                    yAxisIndex: 3,
                    itemStyle: {
                        color: 'rgba(249, 115, 22, 0.25)',
                        color0: 'rgba(251, 191, 36, 0.25)',
                        borderColor: SPX_UP_COLOR,
                        borderColor0: SPX_DOWN_COLOR,
                        borderWidth: 1
                    }
                },
                {
                    name: 'S&P 500 平线',
                    type: 'scatter',
                    data: this.spxVisible ? spxFlatDots : [],
                    xAxisIndex: 2,
                    yAxisIndex: 3,
                    symbol: 'circle',
                    symbolSize: 3,
                    itemStyle: { color: c.textMuted },
                    tooltip: { show: false },
                    emphasis: { scale: false }
                },
                {
                    name: 'NDX 滚动 PE',
                    type: 'line',
                    data: peSeries,
                    xAxisIndex: 3,
                    yAxisIndex: 4,
                    smooth: false,
                    symbol: 'none',
                    lineStyle: { width: 2, color: c.primary },
                    itemStyle: { color: c.primary }
                }
            ]
        };

        this.chart.setOption(option, true);
        this.updateVisibleRanges();
    }

    updateVisibleRanges(eventBatch) {
        if (!this.chart || this.data.length === 0) return;

        let startIdx = 0;
        let endIdx = this.data.length - 1;

        const valueToIndex = (value) => {
            if (typeof value === 'number') return Math.round(value);
            if (typeof value === 'string') return this.dates.indexOf(value);
            return -1;
        };

        const applyBatch = (batch) => {
            if (!batch) return false;
            const startValueIdx = valueToIndex(batch.startValue);
            const endValueIdx = valueToIndex(batch.endValue);
            if (startValueIdx >= 0 && endValueIdx >= 0) {
                startIdx = Math.min(startValueIdx, endValueIdx);
                endIdx = Math.max(startValueIdx, endValueIdx);
                return true;
            }
            if (typeof batch.start === 'number' && typeof batch.end === 'number') {
                const n = this.data.length;
                startIdx = Math.floor(n * batch.start / 100);
                endIdx = Math.ceil(n * batch.end / 100) - 1;
                return true;
            }
            return false;
        };

        if (!eventBatch || !applyBatch(eventBatch)) {
            const option = this.chart.getOption() || {};
            const zooms = option.dataZoom || [];
            for (const dz of zooms) {
                if (applyBatch(dz)) break;
            }
        }

        startIdx = Math.max(0, Math.min(this.data.length - 1, startIdx));
        endIdx = Math.max(0, Math.min(this.data.length - 1, endIdx));
        if (startIdx > endIdx) {
            [startIdx, endIdx] = [endIdx, startIdx];
        }

        this.updateDateInputs(startIdx, endIdx);

        // VIX 窗口真实最高价标记
        let maxHigh = -Infinity;
        let maxIdx = startIdx;
        for (let i = startIdx; i <= endIdx; i++) {
            const v = this.data[i].high;
            if (v > maxHigh) {
                maxHigh = v;
                maxIdx = i;
            }
        }

        const windowMaxPoint = {
            name: '窗口最高',
            coord: [maxIdx, maxHigh],
            value: maxHigh,
            label: { color: '#fff', formatter: '{c}' },
            itemStyle: { color: this.colors.danger }
        };

        const updateOption = {
            series: [{
                name: 'VIX K线',
                markPoint: {
                    animation: false,
                    symbolSize: 28,
                    data: [...this.eventAnnotations, windowMaxPoint]
                }
            }]
        };

        // NDX / SPX 对数坐标：手动收紧纵轴范围
        if (this.ndxLogScale) {
            let ndxMin = Infinity;
            let ndxMax = -Infinity;
            let spxMin = Infinity;
            let spxMax = -Infinity;
            for (let i = startIdx; i <= endIdx; i++) {
                const ndxCandle = this.ndxOhlc[i];
                if (ndxCandle) {
                    const [, , low, high] = ndxCandle;
                    if (low < ndxMin) ndxMin = low;
                    if (high > ndxMax) ndxMax = high;
                }
                const spxCandle = this.spxOhlc[i];
                if (spxCandle) {
                    const [, , low, high] = spxCandle;
                    if (low < spxMin) spxMin = low;
                    if (high > spxMax) spxMax = high;
                }
            }
            const makeLogRange = (min, max, label) => {
                if (min <= 0 || !Number.isFinite(min) || !Number.isFinite(max)) {
                    if (min <= 0 && Number.isFinite(min)) {
                        console.warn(`${label} 可见区间存在非正价格，无法收紧对数纵轴:`, min);
                    }
                    return {};
                }
                return { min: min / NDX_LOG_AXIS_PADDING, max: max * NDX_LOG_AXIS_PADDING };
            };
            const yAxis = [
                {},
                { min: 0, max: PERCENTILE_AXIS_MAX },
                makeLogRange(ndxMin, ndxMax, 'NDX'),
                makeLogRange(spxMin, spxMax, 'SPX'),
                {}
            ];
            updateOption.yAxis = yAxis;
        }

        this.chart.setOption(updateOption);
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
