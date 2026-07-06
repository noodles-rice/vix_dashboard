/**
 * VIX Dashboard 纯函数核心
 *
 * 与 DOM / ECharts 解耦，包含 CSV 解析、日期解析、百分位计算等可在
 * 浏览器与 Node.js 测试环境中复用的逻辑。
 */

const PERCENTILE_WINDOWS = [
    { value: 'full', label: '全历史百分位' },
    { value: 252, label: '滚动 1 年百分位 (252 交易日)' },
    { value: 1260, label: '滚动 5 年百分位 (1260 交易日)' },
    { value: 2520, label: '滚动 10 年百分位 (2520 交易日)' }
];

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

/**
 * 转义 HTML 特殊字符，防止 XSS。
 */
function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/**
 * 解析 CBOE 日期格式 MM/DD/YYYY 为 UTC 日期对象。
 */
function parseDate(dateStr) {
    const parts = dateStr.split('/');
    if (parts.length !== 3) return null;
    const month = parseInt(parts[0], 10) - 1;
    const day = parseInt(parts[1], 10);
    const year = parseInt(parts[2], 10);
    return new Date(Date.UTC(year, month, day));
}

/**
 * 解析 CBOE VIX CSV 文本，返回按日期升序排列的数据对象数组。
 */
function parseCSV(text) {
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
        const date = parseDate(dateStr);
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

/**
 * 二分查找：返回 sortedArr 中第一个满足 !predicate(sortedArr[i], value) 的索引。
 *
 * 常用于 lower_bound / upper_bound 变体：
 * - 插入位置（第一个 > value）：predicate = (a, b) => a <= b
 * - 删除位置（第一个 >= value）：predicate = (a, b) => a < b
 */
function lowerBound(sortedArr, value, predicate) {
    let left = 0;
    let right = sortedArr.length;
    while (left < right) {
        const mid = Math.floor((left + right) / 2);
        if (predicate(sortedArr[mid], value)) {
            left = mid + 1;
        } else {
            right = mid;
        }
    }
    return left;
}

/**
 * 计算全历史百分位。
 *
 * 采用 percentile rank 定义，范围 (0, 100]，最小值约为 100/n%，最大值为 100%。
 */
function computeFullPercentile(data, closes) {
    const n = data.length;
    const sorted = closes.map((value, index) => ({ value, index }))
        .sort((a, b) => a.value - b.value || a.index - b.index);

    for (let rank = 0; rank < n; rank++) {
        const idx = sorted[rank].index;
        data[idx].percentileFull = ((rank + 1) / n) * 100;
    }
    return data;
}

/**
 * 计算滚动百分位。
 *
 * 使用排序窗口 + 二分查找优化，复杂度 O(n log window)。
 * 前 window - 1 个数据点窗口未满，使用扩展窗口（自起始以来的累计数据）计算。
 */
function computeRollingPercentile(data, closes, window) {
    const n = data.length;
    const key = `percentile${window}`;

    let sortedWindow = [];
    for (let i = 0; i < n; i++) {
        const current = closes[i];
        const insertIdx = lowerBound(sortedWindow, current, (a, b) => a <= b);
        sortedWindow.splice(insertIdx, 0, current);

        if (sortedWindow.length > window) {
            const valueToRemove = closes[i - window];
            const removeIdx = lowerBound(sortedWindow, valueToRemove, (a, b) => a < b);
            // 由于存在重复值，找到第一个 >= valueToRemove 的位置后线性扫描精确匹配
            for (let j = removeIdx; j < sortedWindow.length; j++) {
                if (sortedWindow[j] === valueToRemove) {
                    sortedWindow.splice(j, 1);
                    break;
                }
            }
        }

        const rank = lowerBound(sortedWindow, current, (a, b) => a <= b);
        data[i][key] = (rank / sortedWindow.length) * 100;
    }

    return data;
}

const VIXDashboardCore = {
    PERCENTILE_WINDOWS,
    VIX_MARK_LINE_LOW,
    VIX_MARK_LINE_HIGH,
    PERCENTILE_MARK_LINE_MEDIAN,
    PERCENTILE_PIECES,
    escapeHtml,
    parseDate,
    parseCSV,
    lowerBound,
    computeFullPercentile,
    computeRollingPercentile
};

if (typeof module !== 'undefined' && module.exports) {
    module.exports = VIXDashboardCore;
}
