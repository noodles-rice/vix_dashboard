// 交易复盘记录表（独立页面逻辑）

class TradingJournal {
    constructor(containerId) {
        this.container = document.getElementById(containerId || 'journalTableContainer');
        this.JOURNAL_STORAGE_KEY = 'trading_journal';
        this.JOURNAL_SEED_URL = 'data/trading_journal.json';
        this.JOURNAL_FIELD_LABELS = {
            date: '操作时间',
            action: '操作',
            stockName: '标的名称',
            externalFactor: '外部因素',
            internalFactor: '内部因素',
            result: '成功/失败',
            pnl: '盈亏',
            analysis: '原因分析',
            improvement: '改进措施',
            notes: '备注/其他'
        };
        this.JOURNAL_FIELDS = ['date', 'action', 'stockName', 'externalFactor', 'internalFactor', 'result', 'pnl', 'analysis', 'improvement', 'notes'];
        this.JOURNAL_RESULT_SUCCESS_MARKERS = ['成功', '盈'];
        this.JOURNAL_RESULT_FAILURE_MARKERS = ['失败', '亏'];
        this.init();
    }

    async init() {
        if (!this.container) return;

        // 1. Try localStorage first
        let data = this._loadFromStorage();

        // 2. Fall back to seed JSON file
        if (!data) {
            try {
                const response = await fetch(this.JOURNAL_SEED_URL, { cache: 'no-store' });
                if (response.ok) {
                    const json = await response.json();
                    if (json && Array.isArray(json.records)) {
                        data = json.records;
                        this._saveToStorage(data);
                    }
                }
            } catch (err) {
                console.warn('[TradingJournal] Seed load failed:', err);
            }
        }

        // 3. Render
        if (!data || data.length === 0) {
            this.container.innerHTML = '<div class="journal-empty">暂无交易记录，点击「+ 添加记录」开始</div>';
            data = [];
        } else {
            this._renderTable(data);
        }

        // 4. Bind toolbar events
        this._bindToolbar();
    }

    _loadFromStorage() {
        try {
            const raw = localStorage.getItem(this.JOURNAL_STORAGE_KEY);
            if (raw) {
                const parsed = JSON.parse(raw);
                if (parsed && Array.isArray(parsed.records)) {
                    return parsed.records;
                }
            }
        } catch (e) { /* corrupted, ignore */ }
        return null;
    }

    _saveToStorage(records) {
        try {
            localStorage.setItem(this.JOURNAL_STORAGE_KEY, JSON.stringify({ records }));
        } catch (e) {
            console.warn('[TradingJournal] Failed to save to localStorage:', e);
        }
    }

    async _saveToFile(records) {
        try {
            const resp = await fetch('data/trading_journal.json', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ records })
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            return true;
        } catch (e) {
            console.warn('[TradingJournal] Failed to save to file:', e);
            return false;
        }
    }

    _renderTable(records) {
        const table = document.createElement('table');
        table.className = 'journal-table';

        // THEAD — two-level header
        const thead = document.createElement('thead');

        const hr1 = document.createElement('tr');
        [
            ['操作时间', 1], ['操作', 1], ['操作原因', 3], ['事后回溯', 4], ['其他', 1], ['', 1]
        ].forEach(([text, span]) => {
            const th = document.createElement('th');
            th.textContent = text;
            if (span > 1) th.colSpan = span;
            hr1.appendChild(th);
        });
        thead.appendChild(hr1);

        const hr2 = document.createElement('tr');
        ['', '', '标的名称', '外部因素', '内部因素', '成功/失败', '盈亏', '原因分析', '改进措施', '', ''].forEach(text => {
            const th = document.createElement('th');
            th.textContent = text;
            hr2.appendChild(th);
        });
        thead.appendChild(hr2);
        table.appendChild(thead);

        // TBODY
        const tbody = document.createElement('tbody');
        const fields = this.JOURNAL_FIELDS;

        records.forEach((rec, rowIdx) => {
            const tr = document.createElement('tr');

            fields.forEach((field) => {
                const td = document.createElement('td');
                td.textContent = rec[field] ?? '';
                td.contentEditable = 'true';
                td.dataset.field = field;
                td.dataset.row = rowIdx;
                td.setAttribute('role', 'textbox');
                td.setAttribute('aria-label', this.JOURNAL_FIELD_LABELS[field] || field);

                // Color-code result column
                if (field === 'result') {
                    const v = (rec[field] ?? '').trim();
                    if (this.JOURNAL_RESULT_SUCCESS_MARKERS.some(m => v === m || v.startsWith(m))) {
                        td.classList.add('cell-success');
                    } else if (this.JOURNAL_RESULT_FAILURE_MARKERS.some(m => v === m || v.startsWith(m))) {
                        td.classList.add('cell-fail');
                    }
                }

                tr.appendChild(td);
            });

            // Delete button cell
            const delTd = document.createElement('td');
            delTd.className = 'journal-col-delete';
            const delBtn = document.createElement('button');
            delBtn.className = 'journal-delete-row';
            delBtn.textContent = '×';
            delBtn.title = '删除此行';
            delBtn.setAttribute('aria-label', `删除第 ${rowIdx + 1} 行`);
            delBtn.dataset.row = rowIdx;
            delTd.appendChild(delBtn);
            tr.appendChild(delTd);

            tbody.appendChild(tr);
        });

        table.appendChild(tbody);
        this.container.innerHTML = '';
        this.container.appendChild(table);
    }

    _readTable() {
        const records = [];
        const rows = this.container.querySelectorAll('tbody tr');
        const fields = this.JOURNAL_FIELDS;

        rows.forEach(row => {
            const rec = {};
            const cells = row.querySelectorAll('td[contenteditable]');
            cells.forEach((td, i) => {
                if (i < fields.length) {
                    rec[fields[i]] = td.textContent.trim();
                }
            });
            records.push(rec);
        });

        return records;
    }

    _updateResultCell(td) {
        const v = td.textContent.trim();
        td.classList.remove('cell-success', 'cell-fail');
        if (this.JOURNAL_RESULT_SUCCESS_MARKERS.some(m => v === m || v.startsWith(m))) {
            td.classList.add('cell-success');
        } else if (this.JOURNAL_RESULT_FAILURE_MARKERS.some(m => v === m || v.startsWith(m))) {
            td.classList.add('cell-fail');
        }
    }

    _showStatus(msg, isGood) {
        const el = document.getElementById('journalStatus');
        if (!el) return;
        el.textContent = msg;
        el.className = 'journal-status' + (isGood ? ' saved' : '');
        clearTimeout(this._statusTimer);
        if (isGood) {
            this._statusTimer = setTimeout(() => {
                el.textContent = '';
                el.className = 'journal-status';
            }, 2000);
        }
    }

    _bindToolbar() {
        // Prevent duplicate bindings (container persists across re-renders)
        if (this.container.dataset.journalBound === '1') return;
        this.container.dataset.journalBound = '1';

        const self = this;

        // --- Cell edit → auto-save ---
        this.container.addEventListener('blur', e => {
            const td = e.target;
            if (td && td.contentEditable === 'true' && td.closest('.journal-table')) {
                const newRecords = self._readTable();
                self._saveToStorage(newRecords);

                // Update result cell coloring
                if (td.dataset.field === 'result') {
                    self._updateResultCell(td);
                }

                self._showStatus('已保存', true);
            }
        }, true);

        // --- Delete row ---
        this.container.addEventListener('click', e => {
            const btn = e.target.closest('.journal-delete-row');
            if (!btn) return;
            const rowIdx = parseInt(btn.dataset.row, 10);
            if (isNaN(rowIdx)) return;

            const rows = this.container.querySelectorAll('tbody tr');
            if (rows.length <= 1) {
                // Last row: clear instead of delete
                rows[0].querySelectorAll('td[contenteditable]').forEach(td => {
                    td.textContent = '';
                    td.classList.remove('cell-success', 'cell-fail');
                });
            } else {
                rows[rowIdx].remove();
                // Re-index remaining rows
                this.container.querySelectorAll('tbody tr').forEach((tr, i) => {
                    tr.querySelectorAll('td[contenteditable]').forEach(td => { td.dataset.row = i; });
                    const delBtn = tr.querySelector('.journal-delete-row');
                    if (delBtn) {
                        delBtn.dataset.row = i;
                        delBtn.setAttribute('aria-label', `删除第 ${i + 1} 行`);
                    }
                });
            }

            const newRecords = self._readTable();
            self._saveToStorage(newRecords);
            self._showStatus('已保存', true);
        });

        // --- Toolbar buttons ---
        const addBtn = document.getElementById('journalAddRow');
        const saveBtn = document.getElementById('journalSave');
        const exportBtn = document.getElementById('journalExport');

        if (addBtn) {
            addBtn.onclick = () => {
                const records = self._readTable();
                const empty = Object.fromEntries(self.JOURNAL_FIELDS.map(f => [f, '']));
                records.push(empty);
                self._saveToStorage(records);
                self._renderTable(records);
                self._showStatus('已添加空行', true);

                // Scroll to new row and focus first cell
                const lastRow = this.container.querySelector('tbody tr:last-child');
                if (lastRow) {
                    lastRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    const firstCell = lastRow.querySelector('td[contenteditable]');
                    if (firstCell) firstCell.focus();
                }
            };
        }

        if (saveBtn) {
            saveBtn.onclick = async () => {
                const records = self._readTable();
                self._saveToStorage(records);
                const ok = await self._saveToFile(records);
                self._showStatus(ok ? '已保存到文件' : '保存失败，请检查服务器状态或文件权限', ok);
            };
        }

        if (exportBtn) {
            exportBtn.onclick = () => {
                const records = self._readTable();
                const json = JSON.stringify({ records }, null, 2);
                const blob = new Blob([json], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'trading_journal_' + new Date().toISOString().slice(0, 10) + '.json';
                a.click();
                URL.revokeObjectURL(url);
                self._showStatus('已导出', true);
            };
        }
    }
}

// Initialize journal when DOM is ready (browser only)
if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => {
        new TradingJournal();
    });
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { TradingJournal };
}
