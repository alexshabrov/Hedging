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

    function statusBadgeClass(status) {
        var key = String(status === null || status === undefined ? '-' : status).toLowerCase();
        if (['running', 'active', 'ok', 'done', 'finished', 'success', 'completed', 'filled'].indexOf(key) >= 0) {
            return 'badge-success';
        }
        if (['initialized', 'initializing', 'created', 'new', 'pending', 'open'].indexOf(key) >= 0) {
            return 'badge-info';
        }
        if (['stopping', 'pausing', 'warning'].indexOf(key) >= 0) {
            return 'badge-warning';
        }
        if (['failed', 'error', 'rejected', 'cancelled', 'canceled', 'dead'].indexOf(key) >= 0) {
            return 'badge-danger';
        }
        if (['stopped', 'closed', 'inactive'].indexOf(key) >= 0) {
            return 'badge-secondary';
        }
        return 'badge-light';
    }

    function statusBadgeHtml(status) {
        return '<span class="badge ' + statusBadgeClass(status) + '">' + escapeHtml(status) + '</span>';
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

    function applyStopButton(status) {
        var wrap = byId('run-stop-wrap');
        if (!wrap) {
            return;
        }
        var active = status === 'initialized' || status === 'running' || status === 'stopping';
        wrap.style.display = active ? '' : 'none';
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
        applyStopButton(String(p.status || ''));
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
