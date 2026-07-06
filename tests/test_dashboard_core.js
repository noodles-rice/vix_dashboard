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

function runTests() {
    const tests = [
        testEscapeHtml,
        testParseDate,
        testParseCSV,
        testParseCSVRequiresColumns,
        testComputeFullPercentile,
        testComputeFullPercentileWithDuplicates,
        testComputeRollingPercentile,
        testComputeRollingPercentileWithDuplicates,
        testPercentileWindows,
        testVIXThresholdsStructure,
        testGetVIXRegime,
        testGetVIXRegimeInvalidInputs,
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
