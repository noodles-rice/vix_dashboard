/**
 * journal.js 的 Node.js 单元测试。
 *
 * 运行方式：node test_journal.js
 */

const assert = require('assert');

// Minimal DOM mocks for Node.js testing
class MockElement {
    constructor(tagName) {
        this.tagName = tagName;
        this.children = [];
        this.attributes = {};
        this.dataset = {};
        this._classList = new Set();
        this._textContent = '';
        this._innerHTML = '';
        this.parent = null;
        this._listeners = {};
    }

    get classList() {
        return {
            add: (c) => this._classList.add(c),
            remove: (c) => this._classList.delete(c),
            contains: (c) => this._classList.has(c),
        };
    }

    get className() {
        return Array.from(this._classList).join(' ');
    }

    set className(value) {
        this._classList = new Set(String(value).split(/\s+/).filter(Boolean));
    }

    get textContent() {
        return this._textContent;
    }

    set textContent(value) {
        this._textContent = String(value);
    }

    get innerText() {
        return this._textContent;
    }

    set innerText(value) {
        this._textContent = String(value);
    }

    get innerHTML() {
        return this._innerHTML;
    }

    set innerHTML(value) {
        this._innerHTML = String(value);
        this.children.forEach(child => { child.parent = null; });
        this.children = [];
    }

    get contentEditable() {
        return this.attributes['contenteditable'];
    }

    set contentEditable(value) {
        this.attributes['contenteditable'] = String(value);
    }

    setAttribute(key, value) {
        this.attributes[key] = String(value);
    }

    getAttribute(key) {
        return this.attributes[key];
    }

    appendChild(child) {
        if (child.parent) {
            child.parent.children = child.parent.children.filter(c => c !== child);
        }
        child.parent = this;
        this.children.push(child);
        return child;
    }

    remove() {
        if (this.parent) {
            this.parent.children = this.parent.children.filter(c => c !== this);
            this.parent = null;
        }
    }

    closest() {
        return null;
    }

    addEventListener(event, handler) {
        if (!this._listeners[event]) this._listeners[event] = [];
        this._listeners[event].push(handler);
    }

    scrollIntoView() {}
    focus() {}

    querySelectorAll(selector) {
        const results = [];
        const matches = (el) => {
            if (selector === 'tbody tr') {
                return el.tagName === 'tr' && el.parent && el.parent.tagName === 'tbody';
            }
            if (selector === 'td[contenteditable]') {
                return el.tagName === 'td' && el.attributes['contenteditable'] === 'true';
            }
            if (selector === '.journal-delete-row') {
                return el._classList.has('journal-delete-row');
            }
            if (selector === 'tbody tr:last-child') {
                const tbodies = this.children.filter(c => c.tagName === 'tbody');
                const tbody = tbodies[tbodies.length - 1];
                if (!tbody) return false;
                const trs = tbody.children.filter(c => c.tagName === 'tr');
                return el === trs[trs.length - 1];
            }
            return false;
        };
        const walk = (el) => {
            if (matches(el)) results.push(el);
            el.children.forEach(walk);
        };
        this.children.forEach(walk);
        return results;
    }
}

const FIELDS = ['date', 'action', 'stockName', 'externalFactor', 'internalFactor', 'result', 'analysis', 'improvement', 'notes'];

let currentContainer;
let currentStatus;
let currentAddBtn;
let currentSaveBtn;
let currentExportBtn;

function resetDom() {
    currentContainer = new MockElement('div');
    currentContainer.id = 'journalTableContainer';
    currentStatus = new MockElement('div');
    currentStatus.id = 'journalStatus';
    currentAddBtn = new MockElement('button');
    currentAddBtn.id = 'journalAddRow';
    currentSaveBtn = new MockElement('button');
    currentSaveBtn.id = 'journalSave';
    currentExportBtn = new MockElement('button');
    currentExportBtn.id = 'journalExport';
}

resetDom();

global.document = {
    getElementById: (id) => {
        if (id === 'journalTableContainer') return currentContainer;
        if (id === 'journalStatus') return currentStatus;
        if (id === 'journalAddRow') return currentAddBtn;
        if (id === 'journalSave') return currentSaveBtn;
        if (id === 'journalExport') return currentExportBtn;
        return null;
    },
    addEventListener: () => {},
    createElement: (tag) => new MockElement(tag),
};

const localStorageData = {};
global.localStorage = {
    getItem: (key) => (key in localStorageData ? localStorageData[key] : null),
    setItem: (key, value) => { localStorageData[key] = String(value); },
    removeItem: (key) => { delete localStorageData[key]; },
};

global.fetch = () => Promise.resolve({ ok: false });

const { TradingJournal } = require('../assets/journal.js');

function createTd(text, field, rowIdx) {
    const td = new MockElement('td');
    td.textContent = text;
    td.contentEditable = 'true';
    td.dataset.field = field;
    td.dataset.row = String(rowIdx);
    return td;
}

function buildContainerWithRecords(records) {
    currentContainer.innerHTML = '';
    const table = new MockElement('table');
    table.className = 'journal-table';
    const tbody = new MockElement('tbody');
    records.forEach((rec, rowIdx) => {
        const tr = new MockElement('tr');
        FIELDS.forEach((field) => {
            tr.appendChild(createTd(rec[field] ?? '', field, rowIdx));
        });
        tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    currentContainer.appendChild(table);
}

function testExports() {
    assert.strictEqual(typeof TradingJournal, 'function');
}

function testLoadAndSaveFromStorage() {
    resetDom();
    localStorage.removeItem('trading_journal');
    const journal = new TradingJournal();

    assert.strictEqual(journal._loadFromStorage(), null);

    journal._saveToStorage([
        { date: '2024-01-01', action: '买入', result: '成功' },
    ]);
    const loaded = journal._loadFromStorage();
    assert.ok(Array.isArray(loaded));
    assert.strictEqual(loaded.length, 1);
    assert.strictEqual(loaded[0].date, '2024-01-01');
    assert.strictEqual(loaded[0].action, '买入');
    assert.strictEqual(loaded[0].result, '成功');

    localStorage.removeItem('trading_journal');
}

async function testLoadFromSeedFile() {
    resetDom();
    localStorage.removeItem('trading_journal');

    const seedRecords = [
        { date: '2024-02-01', action: '卖出', result: '失败' },
    ];
    const originalFetch = global.fetch;
    global.fetch = (url, options) => {
        assert.strictEqual(url, 'data/trading_journal.json');
        assert.deepStrictEqual(options, { cache: 'no-store' });
        return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ records: seedRecords }),
        });
    };

    new TradingJournal();
    // Wait for async init() to finish
    await new Promise(resolve => setTimeout(resolve, 0));

    // Seed data should be saved to localStorage and rendered
    const stored = JSON.parse(localStorageData['trading_journal']);
    assert.deepStrictEqual(stored.records, seedRecords);

    const rows = currentContainer.querySelectorAll('tbody tr');
    assert.strictEqual(rows.length, 1);

    global.fetch = originalFetch;
    localStorage.removeItem('trading_journal');
}

function testReadTable() {
    resetDom();
    buildContainerWithRecords([
        { date: '2024-01-01', action: '买入', result: '成功' },
        { date: '2024-01-02', action: '卖出', result: '失败' },
    ]);

    const journal = new TradingJournal();
    const records = journal._readTable();
    assert.strictEqual(records.length, 2);
    assert.strictEqual(records[0].date, '2024-01-01');
    assert.strictEqual(records[0].action, '买入');
    assert.strictEqual(records[0].result, '成功');
    assert.strictEqual(records[1].date, '2024-01-02');
    assert.strictEqual(records[1].action, '卖出');
    assert.strictEqual(records[1].result, '失败');
}

function testUpdateResultCell() {
    resetDom();
    const journal = new TradingJournal();

    const successCell = createTd('成功', 'result', 0);
    journal._updateResultCell(successCell);
    assert.ok(successCell.classList.contains('cell-success'));
    assert.ok(!successCell.classList.contains('cell-fail'));

    const profitCell = createTd('盈', 'result', 1);
    journal._updateResultCell(profitCell);
    assert.ok(profitCell.classList.contains('cell-success'));

    const failCell = createTd('失败', 'result', 2);
    journal._updateResultCell(failCell);
    assert.ok(failCell.classList.contains('cell-fail'));

    const lossCell = createTd('亏', 'result', 3);
    journal._updateResultCell(lossCell);
    assert.ok(lossCell.classList.contains('cell-fail'));

    const neutralCell = createTd('持有', 'result', 4);
    journal._updateResultCell(neutralCell);
    assert.ok(!neutralCell.classList.contains('cell-success'));
    assert.ok(!neutralCell.classList.contains('cell-fail'));

    // Toggle from success to failure should remove old class
    successCell.textContent = '失败';
    journal._updateResultCell(successCell);
    assert.ok(!successCell.classList.contains('cell-success'));
    assert.ok(successCell.classList.contains('cell-fail'));
}

function testRenderTableResultColoring() {
    resetDom();
    const journal = new TradingJournal();
    journal._renderTable([
        { date: '2024-01-01', result: '成功' },
        { date: '2024-01-02', result: '亏' },
        { date: '2024-01-03', result: '观望' },
    ]);

    const rows = currentContainer.querySelectorAll('tbody tr');
    assert.strictEqual(rows.length, 3);

    const successRowCells = rows[0].querySelectorAll('td[contenteditable]');
    const successCell = successRowCells.find(td => td.dataset.field === 'result');
    assert.ok(successCell.classList.contains('cell-success'));

    const failRowCells = rows[1].querySelectorAll('td[contenteditable]');
    const failCell = failRowCells.find(td => td.dataset.field === 'result');
    assert.ok(failCell.classList.contains('cell-fail'));

    const neutralRowCells = rows[2].querySelectorAll('td[contenteditable]');
    const neutralCell = neutralRowCells.find(td => td.dataset.field === 'result');
    assert.ok(!neutralCell.classList.contains('cell-success'));
    assert.ok(!neutralCell.classList.contains('cell-fail'));
}

async function runTests() {
    const tests = [
        testExports,
        testLoadAndSaveFromStorage,
        testLoadFromSeedFile,
        testReadTable,
        testUpdateResultCell,
        testRenderTableResultColoring,
    ];

    let passed = 0;
    let failed = 0;
    for (const test of tests) {
        try {
            await test();
            console.log(`  ✓ ${test.name}`);
            passed++;
        } catch (err) {
            console.error(`  ✗ ${test.name}`);
            console.error(err.message);
            console.error(err.stack);
            failed++;
        }
    }

    console.log(`\n${passed} passed, ${failed} failed`);
    process.exit(failed > 0 ? 1 : 0);
}

runTests();
