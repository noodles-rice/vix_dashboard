/**
 * dashboard_core.js 的 Node.js 单元测试。
 *
 * 运行方式：node test_dashboard_core.js
 */

const assert = require('assert');
const core = require('../assets/dashboard_core.js');

function testEscapeHtml() {
    assert.strictEqual(core.escapeHtml('<script>alert(1)</script>'), '&lt;script&gt;alert(1)&lt;/script&gt;');
    assert.strictEqual(core.escapeHtml('a & b'), 'a &amp; b');
    assert.strictEqual(core.escapeHtml('"quoted"'), '&quot;quoted&quot;');
    assert.strictEqual(core.escapeHtml("it's"), 'it&#39;s');
    assert.strictEqual(core.escapeHtml(123), '123');
}

function testParseDate() {
    const d = core.parseDate('12/31/2020');
    assert.strictEqual(d.toISOString(), '2020-12-31T00:00:00.000Z');
    assert.strictEqual(core.parseDate('not-a-date'), null);
    assert.strictEqual(core.parseDate('01/02'), null);
}

function testParseISODate() {
    const d = core.parseISODate('2020-12-31');
    assert.strictEqual(d.toISOString(), '2020-12-31T00:00:00.000Z');
    assert.strictEqual(core.parseISODate(''), null);
    assert.strictEqual(core.parseISODate(null), null);
    assert.strictEqual(core.parseISODate('2020-13-01'), null);
    assert.strictEqual(core.parseISODate('not-a-date'), null);
}

function testFormatISODate() {
    assert.strictEqual(core.formatISODate(new Date(Date.UTC(2020, 11, 31))), '2020-12-31');
    assert.strictEqual(core.formatISODate(new Date(Date.UTC(1990, 0, 2))), '1990-01-02');
}

function testParseNDXPE() {
    const payload = JSON.stringify({
        forward_pe: null,
        trailing_pe: 30.0,
        source: 'QQQ via Yahoo Finance',
        as_of: '2024-06-15',
        fetched_at: '2024-06-15T12:00:00+00:00'
    });
    const result = core.parseNDXPE(payload);
    assert.strictEqual(result.trailingPE, 30.0);
    assert.strictEqual(result.forwardPE, null);
    assert.strictEqual(result.source, 'QQQ via Yahoo Finance');
    assert.strictEqual(result.asOf, '2024-06-15');
}

function testParseNDXPEWithForwardPE() {
    const payload = JSON.stringify({
        forward_pe: 25.5,
        trailing_pe: 30.0,
        source: 'QQQ via Yahoo Finance',
        as_of: '2024-06-15'
    });
    const result = core.parseNDXPE(payload);
    assert.strictEqual(result.trailingPE, 30.0);
    assert.strictEqual(result.forwardPE, 25.5);
}

function testParseNDXPEWithStringNumbers() {
    const payload = JSON.stringify({
        forward_pe: null,
        trailing_pe: '30.00',
        source: 'QQQ via Yahoo Finance',
        as_of: '2024-06-15'
    });
    const result = core.parseNDXPE(payload);
    assert.strictEqual(result.trailingPE, 30);
    assert.strictEqual(result.forwardPE, null);
}

function testParseNDXPEInvalidJSON() {
    assert.strictEqual(core.parseNDXPE('not-json'), null);
}

function testParseNDXPEInvalidTrailingPE() {
    assert.strictEqual(core.parseNDXPE(JSON.stringify({ trailing_pe: null })), null);
    assert.strictEqual(core.parseNDXPE(JSON.stringify({ trailing_pe: 0 })), null);
    assert.strictEqual(core.parseNDXPE(JSON.stringify({ trailing_pe: -1 })), null);
    assert.strictEqual(core.parseNDXPE(JSON.stringify({ trailing_pe: 'abc' })), null);
    assert.strictEqual(core.parseNDXPE(JSON.stringify({})), null);
}

function testParseNDXPEInvalidSource() {
    const base = { trailing_pe: 30.0 };
    assert.strictEqual(core.parseNDXPE(JSON.stringify({ ...base, source: null })).source, 'Unknown');
    assert.strictEqual(core.parseNDXPE(JSON.stringify({ ...base, source: 123 })).source, 'Unknown');
    assert.strictEqual(core.parseNDXPE(JSON.stringify({ ...base, source: '' })).source, 'Unknown');
    assert.strictEqual(core.parseNDXPE(JSON.stringify(base)).source, 'Unknown');
}

function testLowerBoundDateLookup() {
    const data = [
        { date: new Date(Date.UTC(2020, 0, 1)), close: 10 },
        { date: new Date(Date.UTC(2020, 0, 3)), close: 20 },
        { date: new Date(Date.UTC(2020, 0, 6)), close: 30 }
    ];

    // 查找第一个日期 >= 2020-01-02 的索引（应跳到 2020-01-03）
    const startIdx = core.lowerBound(data, new Date(Date.UTC(2020, 0, 2)).getTime(),
        (a, b) => a.date.getTime() < b);
    assert.strictEqual(startIdx, 1);

    // 查找最后一个日期 <= 2020-01-05 的索引（应回到 2020-01-03）
    const endIdx = core.lowerBound(data, new Date(Date.UTC(2020, 0, 5)).getTime(),
        (a, b) => a.date.getTime() <= b) - 1;
    assert.strictEqual(endIdx, 1);
}

function testParseCSV() {
    const csv = `DATE,OPEN,HIGH,LOW,CLOSE
01/03/2020,17,18,16,17.5
01/02/2020,16,17,15,16.5
BAD,1,2,3,4
01/04/2020,18,19,17,18.5`;

    const data = core.parseCSV(csv);
    assert.strictEqual(data.length, 3);
    assert.strictEqual(data[0].dateStr, '01/02/2020');
    assert.strictEqual(data[0].close, 16.5);
    assert.strictEqual(data[2].dateStr, '01/04/2020');
    assert.strictEqual(data[2].close, 18.5);
}

function testParseCSVRequiresColumns() {
    assert.throws(() => core.parseCSV('FOO,BAR\n1,2'), /CSV 缺少必需的 DATE 或 CLOSE 列/);
}

function testParsePEHistoryCSV() {
    const csv = `date,pe_ratio
2020-01-01,25.5
2020-02-01,26.3
bad-date,27.0
2020-03-01,not-a-number
2020-04-01,24.0`;

    const data = core.parsePEHistoryCSV(csv);
    assert.strictEqual(data.length, 3);
    assert.strictEqual(data[0].dateStr, '2020-01-01');
    assert.strictEqual(data[0].pe, 25.5);
    assert.strictEqual(data[1].dateStr, '2020-02-01');
    assert.strictEqual(data[2].dateStr, '2020-04-01');
    assert.strictEqual(data[2].pe, 24.0);
}

function testParsePEHistoryCSVRequiresColumns() {
    assert.throws(() => core.parsePEHistoryCSV('foo,bar\n1,2'), /PE 历史 CSV 缺少必需的 date 或 pe_ratio 列/);
}

function testAlignPEToVix() {
    const peData = [
        { dateStr: '2020-01-01', pe: 25.0 },
        { dateStr: '2020-02-01', pe: 26.0 },
        { dateStr: '2020-04-01', pe: 24.0 }
    ];
    const vixData = [
        { date: new Date(Date.UTC(2020, 0, 2)) },
        { date: new Date(Date.UTC(2020, 0, 15)) },
        { date: new Date(Date.UTC(2020, 1, 3)) },
        { date: new Date(Date.UTC(2020, 2, 10)) },
        { date: new Date(Date.UTC(2020, 3, 1)) }
    ];

    const aligned = core.alignPEToVix(peData, vixData);
    assert.deepStrictEqual(aligned, [25.0, 25.0, 26.0, 26.0, 24.0]);
}

function testAlignPEToVixWithEmptyData() {
    assert.deepStrictEqual(core.alignPEToVix([], [{ dateStr: '01/01/2020' }]), [null]);
    assert.deepStrictEqual(core.alignPEToVix([{ dateStr: '2020-01-01', pe: 25.0 }], []), []);
    assert.deepStrictEqual(core.alignPEToVix(null, null), []);
}

function testComputeFullPercentile() {
    const data = [
        { close: 10 },
        { close: 30 },
        { close: 20 },
        { close: 40 }
    ];
    const closes = data.map(d => d.close);
    core.computeFullPercentile(data, closes);

    // 排序后: 10(25), 20(50), 30(75), 40(100)
    assert.strictEqual(data[0].percentileFull, 25);
    assert.strictEqual(data[1].percentileFull, 75);
    assert.strictEqual(data[2].percentileFull, 50);
    assert.strictEqual(data[3].percentileFull, 100);
}

function testComputeFullPercentileWithDuplicates() {
    const data = [{ close: 10 }, { close: 10 }, { close: 20 }];
    const closes = data.map(d => d.close);
    core.computeFullPercentile(data, closes);

    // 稳定排序：10(idx0), 10(idx1), 20(idx2) => 33.33, 66.67, 100
    assert.ok(Math.abs(data[0].percentileFull - 33.33) < 0.01);
    assert.ok(Math.abs(data[1].percentileFull - 66.67) < 0.01);
    assert.strictEqual(data[2].percentileFull, 100);
}

function testComputeRollingPercentile() {
    const data = [
        { close: 10 },
        { close: 20 },
        { close: 30 },
        { close: 40 },
        { close: 50 }
    ];
    const closes = data.map(d => d.close);
    core.computeRollingPercentile(data, closes, 3);

    // 前两个点是累计窗口；从 i=2 开始为固定 3 日窗口
    // i=0: [10] => rank 1/1 = 100%
    assert.strictEqual(data[0].percentile3, 100);
    // i=1: [10,20] => 20 rank 2/2 = 100%
    assert.strictEqual(data[1].percentile3, 100);
    // i=2: [10,20,30] => 30 rank 3/3 = 100%
    assert.strictEqual(data[2].percentile3, 100);
    // i=3: [20,30,40] => 40 rank 3/3 = 100%
    assert.strictEqual(data[3].percentile3, 100);
    // i=4: [30,40,50] => 50 rank 3/3 = 100%
    assert.strictEqual(data[4].percentile3, 100);
}

function testComputeRollingPercentileWithDuplicates() {
    const data = [
        { close: 10 },
        { close: 20 },
        { close: 20 },
        { close: 30 }
    ];
    const closes = data.map(d => d.close);
    core.computeRollingPercentile(data, closes, 3);

    // i=2: [10,20,20] => 20 的 rank = lower_bound(<=20) = 3 => 3/3 = 100%
    assert.strictEqual(data[2].percentile3, 100);
    // i=3: [20,20,30] => 30 rank = 3/3 = 100%
    assert.strictEqual(data[3].percentile3, 100);
}

function testPercentileWindows() {
    assert.strictEqual(core.PERCENTILE_WINDOWS.length, 4);
    assert.deepStrictEqual(core.PERCENTILE_WINDOWS.map(w => w.value), ['full', 252, 1260, 2520]);
}

function testVIXThresholdsStructure() {
    assert.ok(core.VIX_THRESHOLDS.length >= 4);
    assert.ok(core.VIX_THRESHOLDS.every(t => typeof t.label === 'string'));
    assert.ok(core.VIX_THRESHOLDS.every(t => typeof t.description === 'string'));
    assert.ok(core.VIX_THRESHOLDS.every(t => typeof t.color === 'string'));
    assert.strictEqual(core.VIX_THRESHOLDS[core.VIX_THRESHOLDS.length - 1].max, Infinity);
}

function testPercentilePieceBoundaries() {
    assert.deepStrictEqual(core.PERCENTILE_PIECE_BOUNDARIES, [0, 17, 63, 92, 98, 100]);
}

function testVIXEventAnnotations() {
    assert.ok(Array.isArray(core.VIX_EVENT_ANNOTATIONS));
    assert.ok(core.VIX_EVENT_ANNOTATIONS.length > 0);
    core.VIX_EVENT_ANNOTATIONS.forEach(a => {
        assert.ok(/^\d{4}-\d{2}-\d{2}$/.test(a.date), `日期格式错误: ${a.date}`);
        assert.ok(typeof a.label === 'string' && a.label.length > 0, `标注文本为空: ${a.date}`);
        assert.ok(typeof a.description === 'string' && a.description.length > 0, `事件描述为空: ${a.date}`);
        assert.ok(typeof a.showLabel === 'boolean', `showLabel 应为布尔值: ${a.date}`);
    });
}

function testVIXEventAnnotationsAgainstData() {
    const fs = require('fs');
    const path = require('path');
    const csvPath = path.join(__dirname, '..', 'data', 'VIX_History.csv');
    const csv = fs.readFileSync(csvPath, 'utf8');
    const data = core.parseCSV(csv);
    const dateToHigh = new Map();
    data.forEach(d => {
        dateToHigh.set(d.date.toISOString().split('T')[0], d.high);
    });

    core.VIX_EVENT_ANNOTATIONS.forEach(a => {
        const high = dateToHigh.get(a.date);
        assert.ok(high !== undefined, `标注日期不存在于 VIX_History.csv: ${a.date}`);
        assert.ok(
            high > core.VIX_EVENT_HIGH_THRESHOLD,
            `${a.date} 的 VIX 最高价 ${high} 未超过阈值 ${core.VIX_EVENT_HIGH_THRESHOLD}`
        );
    });

    const high35Days = data.filter(d => d.high > core.VIX_EVENT_HIGH_THRESHOLD).length;
    assert.strictEqual(high35Days, 451, `VIX 最高价 > ${core.VIX_EVENT_HIGH_THRESHOLD} 的交易日数量应为 451`);
}

function testGetVIXRegime() {
    assert.strictEqual(core.getVIXRegime(10).label, '恐慌缺失');
    assert.strictEqual(core.getVIXRegime(13).label, '低波动常态');
    assert.strictEqual(core.getVIXRegime(19.99).label, '低波动常态');
    assert.strictEqual(core.getVIXRegime(20).label, '市场担忧');
    assert.strictEqual(core.getVIXRegime(35).label, '显著恐慌');
    assert.strictEqual(core.getVIXRegime(40).label, '极端危机');
    assert.strictEqual(core.getVIXRegime(80).label, '极端危机');
}

function testGetVIXRegimeInvalidInputs() {
    assert.strictEqual(core.getVIXRegime(-1), null);
    assert.strictEqual(core.getVIXRegime(NaN), null);
    assert.strictEqual(core.getVIXRegime(Infinity), null);
    assert.strictEqual(core.getVIXRegime(undefined), null);
    assert.strictEqual(core.getVIXRegime('30'), null);
}

function testBuildPreviousCloseArray() {
    const ohlc = [
        [10, 11, 9, 12],
        [11, 12, 10, 13],
        [12, 10, 9, 13]
    ];
    const result = core.buildPreviousCloseArray(ohlc);
    assert.deepStrictEqual(result, [null, 11, 12]);
}

function testBuildPreviousCloseArrayWithNulls() {
    const ohlc = [
        null,
        [10, 11, 9, 12],
        null,
        [11, 13, 10, 14]
    ];
    const result = core.buildPreviousCloseArray(ohlc);
    // 缺失日自身没有前收盘价；下一有效日的前收盘价仍为 11
    assert.deepStrictEqual(result, [null, null, null, 11]);
}

function testBuildPreviousCloseArrayEmpty() {
    assert.deepStrictEqual(core.buildPreviousCloseArray([]), []);
    assert.deepStrictEqual(core.buildPreviousCloseArray(null), []);
    assert.deepStrictEqual(core.buildPreviousCloseArray('invalid'), []);
}

function testResolveAxisToggleByName() {
    const visibility = { ndxVisible: true, spxVisible: true };
    const ndxResult = core.resolveAxisToggle(
        { componentType: 'yAxis', name: core.AXIS_NAME_NDX, axisIndex: null },
        core.AXIS_NAME_NDX,
        core.AXIS_NAME_SPX,
        visibility
    );
    assert.deepStrictEqual(ndxResult, { ndxVisible: false, spxVisible: true, changed: true });

    const spxResult = core.resolveAxisToggle(
        { componentType: 'yAxis', name: core.AXIS_NAME_SPX, axisIndex: null },
        core.AXIS_NAME_NDX,
        core.AXIS_NAME_SPX,
        visibility
    );
    assert.deepStrictEqual(spxResult, { ndxVisible: true, spxVisible: false, changed: true });
}

function testResolveAxisToggleByAxisIndex() {
    const visibility = { ndxVisible: false, spxVisible: true };
    const result = core.resolveAxisToggle(
        { componentType: 'yAxis', name: null, axisIndex: 2 },
        core.AXIS_NAME_NDX,
        core.AXIS_NAME_SPX,
        visibility
    );
    assert.deepStrictEqual(result, { ndxVisible: true, spxVisible: true, changed: true });
}

function testResolveAxisToggleIgnoresOtherComponents() {
    const visibility = { ndxVisible: true, spxVisible: true };
    const result = core.resolveAxisToggle(
        { componentType: 'series', name: 'some series' },
        core.AXIS_NAME_NDX,
        core.AXIS_NAME_SPX,
        visibility
    );
    assert.deepStrictEqual(result, { ndxVisible: true, spxVisible: true, changed: false });
}

function testResolveAxisToggleIgnoresOtherAxisNames() {
    const visibility = { ndxVisible: true, spxVisible: true };
    const result = core.resolveAxisToggle(
        { componentType: 'yAxis', name: 'VIX', axisIndex: 0 },
        core.AXIS_NAME_NDX,
        core.AXIS_NAME_SPX,
        visibility
    );
    assert.deepStrictEqual(result, { ndxVisible: true, spxVisible: true, changed: false });
}

function runTests() {
    const tests = [
        testEscapeHtml,
        testParseDate,
        testParseISODate,
        testFormatISODate,
        testParseNDXPE,
        testParseNDXPEWithForwardPE,
        testParseNDXPEWithStringNumbers,
        testParseNDXPEInvalidJSON,
        testParseNDXPEInvalidTrailingPE,
        testParseNDXPEInvalidSource,
        testLowerBoundDateLookup,
        testParseCSV,
        testParseCSVRequiresColumns,
        testParsePEHistoryCSV,
        testParsePEHistoryCSVRequiresColumns,
        testAlignPEToVix,
        testAlignPEToVixWithEmptyData,
        testComputeFullPercentile,
        testComputeFullPercentileWithDuplicates,
        testComputeRollingPercentile,
        testComputeRollingPercentileWithDuplicates,
        testPercentileWindows,
        testVIXThresholdsStructure,
        testPercentilePieceBoundaries,
        testVIXEventAnnotations,
        testVIXEventAnnotationsAgainstData,
        testGetVIXRegime,
        testGetVIXRegimeInvalidInputs,
        testBuildPreviousCloseArray,
        testBuildPreviousCloseArrayWithNulls,
        testBuildPreviousCloseArrayEmpty,
        testResolveAxisToggleByName,
        testResolveAxisToggleByAxisIndex,
        testResolveAxisToggleIgnoresOtherComponents,
        testResolveAxisToggleIgnoresOtherAxisNames,
    ];

    let passed = 0;
    let failed = 0;
    for (const test of tests) {
        try {
            test();
            console.log(`  ✓ ${test.name}`);
            passed++;
        } catch (err) {
            console.error(`  ✗ ${test.name}`);
            console.error(err.message);
            failed++;
        }
    }

    console.log(`\n${passed} passed, ${failed} failed`);
    process.exit(failed > 0 ? 1 : 0);
}

runTests();
