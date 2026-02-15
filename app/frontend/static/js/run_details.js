(function() {
    function byId(id) {
        return document.getElementById(id);
    }

    function setText(id, value) {
        var el = byId(id);
        if (!el) {
            return;
        }
        el.textContent = (value === null || value === undefined) ? '-' : String(value);
    }

    function setHtml(id, html) {
        var el = byId(id);
        if (!el) {
            return;
        }
        el.innerHTML = html;
    }

    function escapeHtml(value) {
        var s = String(value === null || value === undefined ? '-' : value);
        return s
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function statusBadgeKey(status) {
        var raw = String(status === null || status === undefined ? '-' : status).trim().toLowerCase();
        var key = raw
            .replace(/_/g, '-')
            .replace(/[^a-z0-9-]+/g, '-')
            .replace(/^-+|-+$/g, '');
        return key.length > 0 ? key : 'unknown';
    }

    function statusBadgeHtml(status) {
        var key = statusBadgeKey(status);
        return '<span class="badge status-badge status-badge-' + key + '">' + escapeHtml(status) + '</span>';
    }

    function fmt4(value) {
        var n = Number(value);
        if (!Number.isFinite(n)) {
            return '-';
        }
        return n.toFixed(4);
    }

    function fmt2(value) {
        var n = Number(value);
        if (!Number.isFinite(n)) {
            return '-';
        }
        return n.toFixed(2);
    }

    function utcTs(ms) {
        var n = Number(ms);
        if (!Number.isFinite(n) || n <= 0) {
            return '-';
        }
        var d = new Date(n);
        function p2(v) { return String(v).padStart(2, '0'); }
        return d.getUTCFullYear() + '-' + p2(d.getUTCMonth() + 1) + '-' + p2(d.getUTCDate()) +
            ' ' + p2(d.getUTCHours()) + ':' + p2(d.getUTCMinutes()) + ':' + p2(d.getUTCSeconds());
    }

    function applyActionButtons(status, tokenId) {
        var stopWrap = byId('run-stop-wrap');
        var collectWrap = byId('run-collect-wrap');
        var active = status === 'initialized' || status === 'running' || status === 'stopping';
        if (stopWrap) {
            stopWrap.style.display = active ? '' : 'none';
        }

        var tokenNum = Number(tokenId);
        var hasToken = Number.isFinite(tokenNum) && tokenNum > 0;
        var canCollect = status === 'failed' && hasToken;
        if (collectWrap) {
            collectWrap.style.display = canCollect ? '' : 'none';
        }
    }

    function applyFailure(item) {
        var block = byId('failure-block');
        var reasonRow = byId('failure-reason-row');
        var rawRow = byId('failure-raw-row');
        var reason = byId('failure-reason');
        var raw = byId('failure-raw');
        if (!block || !reasonRow || !rawRow || !reason || !raw) {
            return;
        }

        var reasonVal = item.failure_reason || '';
        var rawVal = item.failure_error_raw || '';
        var hasReason = String(reasonVal).length > 0;
        var hasRaw = String(rawVal).length > 0;

        block.style.display = (hasReason || hasRaw) ? '' : 'none';
        reasonRow.style.display = hasReason ? '' : 'none';
        rawRow.style.display = hasRaw ? '' : 'none';
        reason.textContent = reasonVal;
        raw.textContent = rawVal;
    }

    function applyIterations(items) {
        var tbody = byId('iterations-body');
        if (!tbody || !Array.isArray(items)) {
            return;
        }
        tbody.innerHTML = '';
        for (var i = 0; i < items.length; i++) {
            var row = items[i];
            var tr = document.createElement('tr');
            function td(text) {
                var c = document.createElement('td');
                c.textContent = text;
                tr.appendChild(c);
            }
            td(String(row.iteration_no));
            var tdId = document.createElement('td');
            var a = document.createElement('a');
            a.href = '/iterations/' + String(row.id);
            a.textContent = String(row.id);
            tdId.appendChild(a);
            tr.appendChild(tdId);
            var tdStatus = document.createElement('td');
            tdStatus.innerHTML = statusBadgeHtml(row.status);
            tr.appendChild(tdStatus);
            td(utcTs(row.started_at_ms));
            td(utcTs(row.finished_at_ms));
            td(fmt2(row.runtime_sec));
            td(row.close_reason || '');
            td(fmt4(row.pnl_fees_quote));
            td(fmt4(row.pnl_fees_il_quote));
            td(fmt4(row.pnl_fees_il_gas_quote));
            td(fmt4(row.pnl_fees_il_gas_cex_quote));
            td(fmt4(row.apr_fees_pct));
            td(fmt4(row.apr_fees_il_pct));
            td(fmt4(row.apr_fees_il_gas_pct));
            td(fmt4(row.apr_fees_il_gas_cex_pct));
            tbody.appendChild(tr);
        }
    }

    function applyView(item) {
        if (!item || !item.position) {
            return;
        }
        var p = item.position;
        setHtml('run-status', statusBadgeHtml(p.status));
        setText('run-token-id', p.token_id);
        setText('run-symbol', p.symbol);
        setText('run-started', utcTs(p.first_started_at_ms));
        setText('run-runtime', p.runtime_dhm);
        setText('run-total-quote', fmt4(p.total_quote));
        setText('run-pnl-fees', fmt4(p.pnl_fees_quote));
        setText('run-pnl-fees-il', fmt4(p.pnl_fees_il_quote));
        setText('run-pnl-fees-il-gas', fmt4(p.pnl_fees_il_gas_quote));
        setText('run-pnl-fees-il-gas-cex', fmt4(p.pnl_fees_il_gas_cex_quote));
        setText('run-apr-fees', fmt4(p.apr_fees_pct));
        setText('run-apr-fees-il', fmt4(p.apr_fees_il_pct));
        setText('run-apr-fees-il-gas', fmt4(p.apr_fees_il_gas_pct));
        setText('run-apr-fees-il-gas-cex', fmt4(p.apr_fees_il_gas_cex_pct));
        applyActionButtons(String(p.status || ''), p.token_id);
        applyFailure(item);
        applyIterations(item.iterations || []);
    }

    function startPolling(runId) {
        var endpoint = '/api/runs/' + encodeURIComponent(runId) + '/details';
        function tick() {
            fetch(endpoint, { credentials: 'same-origin' })
                .then(function(resp) {
                    if (!resp.ok) {
                        return null;
                    }
                    return resp.json();
                })
                .then(function(payload) {
                    if (!payload || payload.ok !== true || !payload.item) {
                        return;
                    }
                    applyView(payload.item);
                })
                .catch(function() {});
        }
        tick();
        window.setInterval(tick, 3000);
    }

    document.addEventListener('DOMContentLoaded', function() {
        var root = byId('run-details-root');
        if (!root) {
            return;
        }
        var runId = root.getAttribute('data-run-id');
        if (!runId) {
            return;
        }
        startPolling(runId);
    });
})();
