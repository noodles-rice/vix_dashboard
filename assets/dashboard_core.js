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

/**
 * 底部 NDX / SPX 双 Y 轴的轴名称。
 * 用于配置坐标轴，并作为点击切换系列显示/隐藏时的识别标识。
 */
const AXIS_NAME_NDX = 'NASDAQ-100';
const AXIS_NAME_SPX = 'S&P 500';

/**
 * VIX 事件标注的日内最高价阈值。
 * 当 VIX 最高价超过该值时，对应的交易日会被纳入事件标注范围。
 */
const VIX_EVENT_HIGH_THRESHOLD = 35;

/**
 * VIX 日内最高价 > VIX_EVENT_HIGH_THRESHOLD 的历史事件标注。
 *
 * 数据覆盖 1990–2026 年，共 451 个交易日 VIX 最高价突破该阈值。
 * 此处按事件分组，取每组峰值日进行标注；主要事件显示文字标签，
 * 次要事件仅显示标记点，悬停仍可查看详情，避免图表过度拥挤。
 */
const VIX_EVENT_ANNOTATIONS = [
    {
        date: '1990-08-23',
        label: '海湾危机',
        showLabel: true,
        description: '伊拉克入侵科威特，海湾战争一触即发，油价飙升引发全球股市剧烈波动。'
    },
    {
        date: '1991-01-14',
        label: '海湾战争',
        showLabel: true,
        description: '联合国限期伊拉克撤军的前夕，“沙漠风暴”行动即将展开，市场高度紧张。'
    },
    {
        date: '1997-10-28',
        label: '亚洲金融危机',
        showLabel: true,
        description: '国际炒家攻击东南亚货币，港股暴跌并波及全球，恒生指数单日跌超 10%。'
    },
    {
        date: '1998-10-08',
        label: '俄罗斯违约 / LTCM',
        showLabel: true,
        description: '俄罗斯主权债务违约引发全球避险潮，高杠杆对冲基金 LTCM 濒临爆仓，美联储协调救助。'
    },
    {
        date: '2001-03-22',
        label: '互联网泡沫加速破裂',
        showLabel: false,
        description: '纳斯达克泡沫进入加速下跌阶段，科技股持续重挫，市场恐慌情绪升温。'
    },
    {
        date: '2001-09-21',
        label: '9/11 事件',
        showLabel: true,
        description: '美国遭遇恐怖袭击，美股停市四天后重开，航空、保险股暴跌，恐慌情绪达到顶峰。'
    },
    {
        date: '2002-07-24',
        label: '安然 / 世通丑闻',
        showLabel: true,
        description: '安然、世通等巨头财务造假曝光，投资者对公司财报和审计体系失去信心。'
    },
    {
        date: '2003-02-10',
        label: '伊拉克战争前紧张',
        showLabel: true,
        description: '伊拉克战争阴云笼罩，美欧分歧加剧，油价和地缘政治风险推升市场波动。'
    },
    {
        date: '2007-08-16',
        label: '次贷危机爆发',
        showLabel: true,
        description: '法国巴黎银行冻结旗下次贷基金，美国次贷违约潮浮出水面，信贷市场开始冻结。'
    },
    {
        date: '2008-01-22',
        label: '次贷危机恶化',
        showLabel: false,
        description: '全球信贷紧缩担忧加剧，主要央行紧急联手救市，市场对未来金融体系深感不安。'
    },
    {
        date: '2008-03-17',
        label: '贝尔斯登危机',
        showLabel: true,
        description: '华尔街投行贝尔斯登濒临破产，最终被摩根大通以极低价格收购，次贷危机全面升级。'
    },
    {
        date: '2008-07-16',
        label: '房利美房地美危机',
        showLabel: true,
        description: '美国两大房贷巨头房利美、房地美股价暴跌，政府救助方案出台前市场极度恐慌。'
    },
    {
        date: '2008-10-24',
        label: '全球金融危机',
        showLabel: true,
        description: '雷曼兄弟破产后信贷市场冻结，全球股市暴跌，VIX 当日盘中最高 89.53，为历史最高纪录。'
    },
    {
        date: '2010-05-21',
        label: '闪电崩盘 / 欧债',
        showLabel: true,
        description: '道指盘中瞬间暴跌近千点的“闪电崩盘”刚过去不久，叠加希腊债务危机引发欧元区解体担忧。'
    },
    {
        date: '2011-08-08',
        label: '美债降级 / 欧债危机',
        showLabel: true,
        description: '标普首次将美国主权信用评级从 AAA 下调至 AA+，同时欧债危机持续恶化。'
    },
    {
        date: '2015-08-24',
        label: '人民币贬值',
        showLabel: true,
        description: '中国央行引导人民币贬值，引发新兴市场货币和股市连锁抛售，全球风险资产遭遇重估。'
    },
    {
        date: '2018-02-06',
        label: 'Volmageddon',
        showLabel: true,
        description: '低波动率环境突然逆转，做空 VIX 的 ETN（XIV）被强制清盘，波动率空头踩踏引发“波动率末日”。'
    },
    {
        date: '2018-12-26',
        label: '圣诞暴跌',
        showLabel: false,
        description: '美联储缩表和加息预期引发年末流动性紧张，美股在圣诞前后出现剧烈调整。'
    },
    {
        date: '2020-03-18',
        label: '新冠疫情爆发',
        showLabel: true,
        description: 'WHO 宣布新冠全球大流行，欧美多国开始封城，股市在数周内急速崩盘，VIX 盘中突破 85。'
    },
    {
        date: '2020-09-04',
        label: '科技股回调',
        showLabel: false,
        description: '纳斯达克在高位出现快速回调，科技股估值与期权投机活动引发波动放大。'
    },
    {
        date: '2020-10-29',
        label: '美国大选 / 疫情',
        showLabel: true,
        description: '美国大选前政策不确定性高企，同时欧美疫情第三波爆发，市场波动显著放大。'
    },
    {
        date: '2021-01-29',
        label: 'GameStop 逼空',
        showLabel: false,
        description: '散户抱团逼空 GameStop 等股票，对冲基金被迫平仓，市场微观结构和波动率剧烈扰动。'
    },
    {
        date: '2021-12-03',
        label: 'Omicron 变异株',
        showLabel: true,
        description: '新冠 Omicron 变异株引发全球担忧，旅行限制和封控预期导致风险资产急跌。'
    },
    {
        date: '2022-01-24',
        label: '俄乌冲突 / 加息',
        showLabel: true,
        description: '俄乌边境紧张局势升级，同时美联储即将开启加息周期，地缘与货币政策双重压力。'
    },
    {
        date: '2022-05-02',
        label: '美联储加息 / 通胀',
        showLabel: false,
        description: '美国通胀高企，美联储开启激进加息，市场对经济增长和估值重估产生担忧。'
    },
    {
        date: '2022-06-13',
        label: '通胀 / 衰退担忧',
        showLabel: false,
        description: '美国 CPI 超预期爆表，市场担心美联储被迫更激进加息，经济衰退概率上升。'
    },
    {
        date: '2024-08-05',
        label: '日元套利平仓',
        showLabel: true,
        description: '日本央行意外加息，日元急升导致全球日元套利交易被迫平仓，日经指数单日大跌 12%。'
    },
    {
        date: '2025-04-07',
        label: '特朗普关税战',
        showLabel: true,
        description: '特朗普政府宣布对多国加征高额“对等关税”，引发全球贸易战和衰退担忧，市场剧烈波动。'
    },
    {
        date: '2026-03-09',
        label: '地缘 / 政策不确定性',
        showLabel: false,
        description: '特朗普关于国际局势的言论引发市场波动，叠加政策不确定性，风险资产短暂承压。'
    }
];

// 百分位图按 VIX 经济含义区间在历史数据中的固定百分位边界划分。
// VIX 经济含义阈值 13、20、30、40 在历史收盘价中的全历史百分位约为
// 17%、63%、92%、98%，四舍五入取整后据此把百分位图从左到右划分为
// 5 个具有经济含义的区段；区间左闭右开，最后一个区间闭合。
const PERCENTILE_PIECES = [
    { min: 0, max: 17, maxOpen: true, color: '#22c55e', label: '恐慌缺失' },
    { min: 17, max: 63, maxOpen: true, color: '#84cc16', label: '低波动常态' },
    { min: 63, max: 92, maxOpen: true, color: '#eab308', label: '市场担忧' },
    { min: 92, max: 98, maxOpen: true, color: '#f97316', label: '显著恐慌' },
    { min: 98, max: 100, color: '#ef4444', label: '极端危机' }
];

// 百分位图纵轴刻度与分隔线位置，取各区间端点
const PERCENTILE_PIECE_BOUNDARIES = [0, ...PERCENTILE_PIECES.map(p => p.max)];

/**
 * VIX 经济含义区间阈值。
 *
 * 这些阈值来自市场长期交易经验（13、20、30、40 为常见心理关口），
 * 用于把 VIX 收盘价划分为具有业务含义的 regime。
 * 区间左闭右开，最后一档闭合。
 */
const VIX_THRESHOLDS = [
    { min: 0, max: 13, maxOpen: true, label: '恐慌缺失', description: '市场过度乐观，波动率常被低估', color: '#22c55e' },
    { min: 13, max: 20, maxOpen: true, label: '低波动常态', description: '正常低波动环境，VIX 长期均值附近', color: '#84cc16' },
    { min: 20, max: 30, maxOpen: true, label: '市场担忧', description: '避险情绪升温，回调压力增大', color: '#eab308' },
    { min: 30, max: 40, maxOpen: true, label: '显著恐慌', description: '明显恐慌，流动性收缩，期权保护需求激增', color: '#f97316' },
    { min: 40, max: Infinity, label: '极端危机', description: '系统性危机或黑天鹅事件，通常伴随股市大跌', color: '#ef4444' }
];

/**
 * 根据 VIX 收盘价查找对应的经济含义区间（regime）。
 *
 * 对非法输入（非数字、NaN、负数、Infinity）返回 null，避免错误映射到极端区间。
 */
function getVIXRegime(value) {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
        return null;
    }

    return VIX_THRESHOLDS.find(t => {
        const aboveMin = value >= t.min;
        const belowMax = t.maxOpen ? value < t.max : value <= t.max;
        return aboveMin && belowMax;
    }) || null;
}

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
 * 解析 ISO 日期格式 YYYY-MM-DD 为 UTC 日期对象。
 */
function parseISODate(isoStr) {
    if (!isoStr) return null;
    const parts = isoStr.split('-');
    if (parts.length !== 3) return null;
    const year = parseInt(parts[0], 10);
    const month = parseInt(parts[1], 10) - 1;
    const day = parseInt(parts[2], 10);
    const date = new Date(Date.UTC(year, month, day));
    if (isNaN(date.getTime())) return null;
    if (date.getUTCFullYear() !== year ||
        date.getUTCMonth() !== month ||
        date.getUTCDate() !== day) {
        return null;
    }
    return date;
}

/**
 * 将 UTC 日期对象格式化为 ISO 日期字符串 YYYY-MM-DD。
 */
function formatISODate(date) {
    return date.toISOString().slice(0, 10);
}

/**
 * 解析 ndx_pe.json，返回标准化后的 PE 数据对象。
 *
 * 当前 Yahoo Finance 对 QQQ 等 ETF 仅提供 trailingPE（TTM），因此以 trailingPE
 * 作为主要校验字段；forwardPE 若存在则一并返回，不存在时为 null。
 * 对非法输入（JSON 解析失败、trailingPE 缺失、非数字或不大于零）返回 null。
 */
function parseNDXPE(jsonText) {
    let payload;
    try {
        payload = JSON.parse(jsonText);
    } catch (e) {
        return null;
    }
    if (!payload || typeof payload !== 'object') {
        return null;
    }

    const trailingPE = payload.trailing_pe !== undefined && payload.trailing_pe !== null
        ? parseFloat(payload.trailing_pe)
        : null;
    if (!Number.isFinite(trailingPE) || trailingPE <= 0) {
        return null;
    }

    const forwardPE = payload.forward_pe !== undefined && payload.forward_pe !== null
        ? parseFloat(payload.forward_pe)
        : null;

    return {
        trailingPE: trailingPE,
        forwardPE: Number.isFinite(forwardPE) && forwardPE > 0 ? forwardPE : null,
        source: typeof payload.source === 'string' && payload.source ? payload.source : 'Unknown',
        asOf: payload.as_of || null,
        fetchedAt: payload.fetched_at || null,
        note: payload.note || null
    };
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

/**
 * 根据已对齐的指数 OHLC 数组，预计算每个交易日对应的“前一日收盘价”。
 *
 * 数组元素为 [open, close, low, high] 或 null（缺失日）。
 * 返回数组中第 i 项表示第 i 个交易日所对应的上一有效交易日的 close，
 * 若不存在则返回 null。
 */
function buildPreviousCloseArray(ohlc) {
    if (!Array.isArray(ohlc)) return [];
    const prevCloses = [];
    let lastClose = null;
    for (let i = 0; i < ohlc.length; i++) {
        const item = ohlc[i];
        if (Array.isArray(item)) {
            prevCloses.push(lastClose);
            lastClose = item[1];
        } else {
            prevCloses.push(null);
        }
    }
    return prevCloses;
}

/**
 * 根据 ECharts yAxis 点击事件参数，判断是否需要切换 NDX / SPX 的可见性。
 *
 * ECharts 点击轴名称时 `params.axisIndex` 为 null，但 `params.name` 会携带轴名称；
 * 点击轴标签时 `params.name` 为 null。这里优先按名称匹配，再按 axisIndex 兜底。
 */
function resolveAxisToggle(params, ndxName, spxName, visibility) {
    if (params.componentType !== 'yAxis') {
        return { ...visibility, changed: false };
    }

    if (params.name === ndxName || params.axisIndex === 2) {
        return {
            ndxVisible: !visibility.ndxVisible,
            spxVisible: visibility.spxVisible,
            changed: true
        };
    }

    if (params.name === spxName || params.axisIndex === 3) {
        return {
            ndxVisible: visibility.ndxVisible,
            spxVisible: !visibility.spxVisible,
            changed: true
        };
    }

    return { ...visibility, changed: false };
}

const VIXDashboardCore = {
    PERCENTILE_WINDOWS,
    VIX_MARK_LINE_LOW,
    VIX_MARK_LINE_HIGH,
    VIX_EVENT_HIGH_THRESHOLD,
    VIX_EVENT_ANNOTATIONS,
    PERCENTILE_PIECES,
    PERCENTILE_PIECE_BOUNDARIES,
    VIX_THRESHOLDS,
    AXIS_NAME_NDX,
    AXIS_NAME_SPX,
    getVIXRegime,
    escapeHtml,
    parseDate,
    parseISODate,
    formatISODate,
    parseNDXPE,
    parseCSV,
    lowerBound,
    computeFullPercentile,
    computeRollingPercentile,
    buildPreviousCloseArray,
    resolveAxisToggle
};

if (typeof module !== 'undefined' && module.exports) {
    module.exports = VIXDashboardCore;
}
