/* =========================================================================
   10. MODULAR CREDIT BROKER FRONTEND (ENTREGABLE 5B — BLOQUE 4)
   Strictly gated behind hasModule('BROKER'). Zero Lending API references.
   ========================================================================= */
(function () {
    'use strict';

    const brokerState = {
        subView: 'cases',
        casesPage: 1,
        casesTotal: 0,
        casesCache: [],
        selectedCase: null,
        lendersCache: [],
        commissionsPage: 1,
        commissionsTotal: 0,
        commissionsCache: [],
        lastDashboardSummary: null,
        lastCustomerSummary: null,
        editingLenderId: null,
        editingSubmissionId: null,
        editingCommissionId: null,
        caseSearchTimer: null,
        lenderSearchTimer: null,
    };

    function _isEs() {
        return typeof NuraI18n !== 'undefined' && NuraI18n.currentLang === 'es';
    }

    function _statusLabel(st) {
        if (!_isEs()) return String(st || '');
        const map = {
            DRAFT: 'BORRADOR',
            SUBMITTED: 'ENVIADO',
            OFFERS_RECEIVED: 'OFERTAS RECIBIDAS',
            FUNDED: 'FONDEADO',
            CLOSED_LOST: 'CERRADO PERDIDO',
            CANCELLED: 'CANCELADO',
            IN_UNDERWRITING: 'EN ANÁLISIS',
            APPROVED: 'APROBADO',
            SELECTED: 'SELECCIONADO',
            DECLINED: 'RECHAZADO',
            WITHDRAWN: 'RETIRADO',
            PENDING: 'PENDIENTE',
            INVOICED: 'FACTURADA',
            PAID: 'PAGADA',
            ACTIVE: 'ACTIVA',
            INACTIVE: 'INACTIVA',
        };
        return map[st] || String(st || '');
    }

    function _brokerEnabled() {
        return typeof hasModule === 'function' && hasModule('BROKER');
    }

    function _fmtCents(cents) {
        const val = Number.isFinite(Number(cents)) ? Number(cents) : 0;
        if (typeof NuraFinancialEngine !== 'undefined' && typeof NuraFinancialEngine.formatMXNFromCents === 'function') {
            return NuraFinancialEngine.formatMXNFromCents(val);
        }
        return `$${(val / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} MXN`;
    }

    function _fmtBps(bps) {
        if (bps === null || bps === undefined || bps === '') return '—';
        const n = Number(bps);
        if (!Number.isFinite(n)) return '—';
        return `${(n / 100).toFixed(2)}%`;
    }

    function _esc(str) {
        if (typeof escapeHtml === 'function') return escapeHtml(str);
        return String(str ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function _fmtDt(iso) {
        if (typeof formatDateTimeShort === 'function') return formatDateTimeShort(iso);
        return iso ? String(iso).slice(0, 16).replace('T', ' ') : '—';
    }

    function _generateIdemKey(prefix) {
        const rnd = Math.random().toString(36).slice(2, 10);
        return `${prefix}-${Date.now()}-${rnd}`.slice(0, 64);
    }

    function _caseStatusBadge(status) {
        const map = {
            DRAFT: 'bg-slate-100 text-slate-700 border-slate-200',
            SUBMITTED: 'bg-blue-50 text-blue-700 border-blue-200',
            OFFERS_RECEIVED: 'bg-indigo-50 text-indigo-700 border-indigo-200',
            FUNDED: 'bg-emerald-50 text-emerald-700 border-emerald-200',
            CLOSED_LOST: 'bg-amber-50 text-amber-800 border-amber-200',
            CANCELLED: 'bg-red-50 text-red-700 border-red-200',
        };
        return map[status] || 'bg-slate-100 text-slate-700 border-slate-200';
    }

    function _subStatusBadge(status) {
        const map = {
            DRAFT: 'bg-slate-100 text-slate-700 border-slate-200',
            SUBMITTED: 'bg-blue-50 text-blue-700 border-blue-200',
            IN_UNDERWRITING: 'bg-amber-50 text-amber-800 border-amber-200',
            APPROVED: 'bg-indigo-50 text-indigo-700 border-indigo-200',
            SELECTED: 'bg-emerald-50 text-emerald-700 border-emerald-200',
            DECLINED: 'bg-red-50 text-red-700 border-red-200',
            WITHDRAWN: 'bg-slate-100 text-slate-500 border-slate-200',
        };
        return map[status] || 'bg-slate-100 text-slate-700 border-slate-200';
    }

    function _commStatusBadge(status) {
        const map = {
            PENDING: 'bg-amber-50 text-amber-800 border-amber-200',
            INVOICED: 'bg-blue-50 text-blue-700 border-blue-200',
            PAID: 'bg-emerald-50 text-emerald-700 border-emerald-200',
            CANCELLED: 'bg-red-50 text-red-700 border-red-200',
        };
        return map[status] || 'bg-slate-100 text-slate-700 border-slate-200';
    }

    async function brokerFetch(url, options = {}) {
        if (!_brokerEnabled()) {
            const err = new Error('BROKER module is not enabled for this organization.');
            err.status = 403;
            throw err;
        }
        const method = String(options.method || 'GET').toUpperCase();
        const isMutating = ['POST', 'PATCH', 'PUT', 'DELETE'].includes(method);
        const headers = Object.assign({ Accept: 'application/json' }, options.headers || {});
        let body = options.body;

        if (isMutating) {
            const csrf = typeof getCookie === 'function' ? getCookie('csrf_token') : null;
            if (csrf && !headers['X-CSRF-Token']) {
                headers['X-CSRF-Token'] = csrf;
            }
            if (body !== undefined && body !== null && typeof body !== 'string') {
                body = JSON.stringify(body);
                if (!headers['Content-Type']) {
                    headers['Content-Type'] = 'application/json';
                }
            }
        }

        const res = await fetch(url, Object.assign({}, options, { method, headers, body, credentials: 'same-origin' }));
        if (res.status === 401) {
            if (typeof mostrarAuthView === 'function') mostrarAuthView();
            const err = new Error('Session expired. Please sign in again.');
            err.status = 401;
            throw err;
        }
        const raw = await res.text();
        let parsed = null;
        if (raw) {
            try {
                parsed = JSON.parse(raw);
            } catch (_) {
                parsed = { detail: raw };
            }
        }
        if (!res.ok) {
            const detail = parsed ? (parsed.detail ?? parsed.message ?? parsed) : null;
            const msg = typeof detail === 'string'
                ? detail
                : (Array.isArray(detail) ? detail.map(d => (d && d.msg ? d.msg : String(d))).join('; ') : `Request failed (${res.status})`);
            const err = new Error(msg);
            err.status = res.status;
            throw err;
        }
        return parsed;
    }

    function resetBrokerWorkspaceState() {
        brokerState.subView = 'cases';
        brokerState.casesPage = 1;
        brokerState.casesTotal = 0;
        brokerState.casesCache = [];
        brokerState.selectedCase = null;
        brokerState.lendersCache = [];
        brokerState.commissionsPage = 1;
        brokerState.commissionsTotal = 0;
        brokerState.commissionsCache = [];
        brokerState.editingLenderId = null;
        brokerState.editingSubmissionId = null;
        brokerState.editingCommissionId = null;

        ['broker-lender-modal', 'broker-case-modal', 'broker-submission-modal', 'broker-fund-modal', 'broker-commission-modal'].forEach(id => {
            document.getElementById(id)?.classList.add('hidden');
        });
        document.getElementById('broker-case-detail')?.classList.add('hidden');
        document.getElementById('broker-cases-master')?.classList.remove('hidden');
    }

    // -------------------------------------------------------------------------
    // 1. DASHBOARD SUMMARY (GET /api/broker/dashboard-summary)
    // -------------------------------------------------------------------------
    function renderBrokerDashboardSummary(data) {
        if (!data || typeof data !== 'object') return;
        const isEs = _isEs();
        const counts = data.counts || {};
        const comm = data.commissions || {};

        const setTxt = (id, text) => {
            const el = document.getElementById(id);
            if (el) el.innerText = String(text);
        };

        setTxt('dash-broker-pipeline-val', _fmtCents(data.active_pipeline_cents || 0));
        setTxt('dash-broker-active-cases-count', counts.active_cases ?? 0);
        setTxt('dash-broker-funded-val', _fmtCents(data.funded_volume_cents || 0));
        setTxt('dash-broker-funded-cases-count', counts.funded_cases ?? 0);
        setTxt('dash-broker-comm-earned-val', _fmtCents(comm.total_earned_cents || 0));
        setTxt('dash-broker-comm-paid-val', _fmtCents(comm.paid_cents || 0));
        setTxt('dash-broker-comm-pending-val', _fmtCents((comm.pending_cents || 0) + (comm.invoiced_cents || 0)));
        setTxt('dash-broker-active-lenders-val', data.active_lenders_count ?? counts.active_lenders ?? 0);
        setTxt('dash-broker-offers-count', counts.offers_received_cases ?? 0);
        const lCount = data.active_lenders_count ?? 0;
        setTxt(
            'dash-broker-lenders-badge',
            isEs
                ? `${lCount} ${lCount === 1 ? 'Financiera Activa' : 'Financieras Activas'}`
                : `${lCount} Active ${lCount === 1 ? 'Lender' : 'Lenders'}`
        );

        const recentEl = document.getElementById('dash-broker-recent-cases-list');
        if (recentEl) {
            const items = Array.isArray(data.recent_cases) ? data.recent_cases : [];
            if (items.length === 0) {
                recentEl.innerHTML = `<p class="text-xs text-slate-400 py-4 text-center col-span-2">${isEs ? 'Aún no hay expedientes de broker. Haz clic en "+ Caso Broker" para comenzar.' : 'No broker cases yet. Click "+ Broker Case" to start.'}</p>`;
            } else {
                recentEl.innerHTML = items.map(c => `
                    <button type="button" onclick="openBrokerCaseFromAnywhere(${Number(c.id)})" class="w-full p-2.5 rounded-xl bg-slate-50 hover:bg-indigo-50/50 border border-slate-200/70 flex items-center justify-between gap-2 text-left transition text-xs">
                        <div class="min-w-0">
                            <div class="flex items-center gap-1.5 flex-wrap">
                                <span class="font-mono font-bold text-indigo-700">${_esc(c.case_number)}</span>
                                <span class="text-[10px] font-bold px-2 py-0.5 rounded-full border ${_caseStatusBadge(c.status)}">${_esc(_statusLabel(c.status))}</span>
                            </div>
                            <p class="font-semibold text-slate-800 truncate mt-0.5">${_esc(c.customer_name || (isEs ? 'Cliente' : 'Customer'))} • ${_esc(c.product_type)}</p>
                        </div>
                        <div class="text-right shrink-0">
                            <p class="font-mono font-bold text-slate-900">${_esc(_fmtCents(c.funded_amount_cents ?? c.requested_amount_cents))}</p>
                            <p class="text-[10px] text-slate-400">${Number(c.submissions_count || 0)} ${isEs ? 'financieras' : 'lenders'}</p>
                        </div>
                    </button>
                `).join('');
            }
        }
    }

    async function loadBrokerDashboardSummary() {
        if (!_brokerEnabled()) return;
        const data = await brokerFetch('/api/broker/dashboard-summary?recent_limit=8');
        if (!data || typeof data !== 'object') return;
        brokerState.lastDashboardSummary = data;
        renderBrokerDashboardSummary(data);
    }

    // -------------------------------------------------------------------------
    // 2. CUSTOMER DETAIL SUMMARY (GET /api/broker/customers/{id}/summary)
    // -------------------------------------------------------------------------
    async function loadCustomerBrokerSummary(customerId) {
        if (!_brokerEnabled() || !customerId) return;
        let data = null;
        try {
            data = await brokerFetch(`/api/broker/customers/${customerId}/summary`);
        } catch (err) {
            console.error('Failed to load customer broker summary:', err);
            return;
        }
        const bs = (data && data.broker_summary) ? data.broker_summary : {};
        const comm = bs.commissions || {};
        const cases = Array.isArray(data?.cases) ? data.cases : [];

        const qsFunded = document.getElementById('cd-qs-broker-funded');
        if (qsFunded) qsFunded.innerText = _fmtCents(bs.total_funded_cents || 0);

        const tabBtn = document.getElementById('ctab-btn-broker');
        if (tabBtn) tabBtn.innerText = `Broker (${cases.length})`;

        const setTxt = (id, text) => {
            const el = document.getElementById(id);
            if (el) el.innerText = String(text);
        };
        setTxt('cbrok-active-cases', `${bs.active_cases ?? 0} / ${bs.total_cases ?? 0}`);
        setTxt('cbrok-funded-total', _fmtCents(bs.total_funded_cents || 0));
        setTxt('cbrok-offers-count', `${bs.approved_offers ?? 0} offers (${bs.total_submissions ?? 0} sent)`);
        setTxt('cbrok-commissions-earned', _fmtCents(comm.total_earned_cents || 0));

        const tbody = document.getElementById('customer-broker-cases-tbody');
        if (tbody) {
            if (cases.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" class="py-8 text-center text-slate-400">No broker cases associated with this customer yet.</td></tr>';
            } else {
                tbody.innerHTML = cases.map(c => `
                    <tr class="hover:bg-slate-50/90 transition cursor-pointer" onclick="openBrokerCaseFromAnywhere(${Number(c.id)})">
                        <td class="py-3 px-4 font-mono font-bold text-indigo-700">${_esc(c.case_number)}</td>
                        <td class="py-3 px-4 font-semibold text-slate-800">${_esc(c.product_type)}</td>
                        <td class="py-3 px-4"><span class="text-[10px] font-bold px-2.5 py-0.5 rounded-full border ${_caseStatusBadge(c.status)}">${_esc(c.status)}</span></td>
                        <td class="py-3 px-4 text-right font-mono text-slate-800">${_esc(_fmtCents(c.requested_amount_cents))}</td>
                        <td class="py-3 px-4 text-right font-mono font-bold text-emerald-700">${c.funded_amount_cents != null ? _esc(_fmtCents(c.funded_amount_cents)) : '—'}</td>
                        <td class="py-3 px-4 text-center font-semibold text-slate-700">${Number(c.submissions_count || 0)} (${Number(c.approved_offers_count || 0)} appr.)</td>
                        <td class="py-3 px-4 text-right"><button type="button" class="text-xs font-bold text-indigo-600 hover:underline">Open Case →</button></td>
                    </tr>
                `).join('');
            }
        }
    }

    // -------------------------------------------------------------------------
    // 3. BROKER WORKSPACE NAVIGATION & LOADERS
    // -------------------------------------------------------------------------
    function switchBrokerSubView(subView) {
        if (!_brokerEnabled()) return;
        brokerState.subView = subView;
        const views = ['cases', 'lenders', 'commissions', 'calendar'];
        views.forEach(v => {
            document.getElementById(`broker-${v}-view`)?.classList.toggle('hidden', v !== subView);
            const btn = document.getElementById(`seg-broker-${v}-btn`);
            if (btn) {
                btn.className = (v === subView)
                    ? 'py-2 px-3.5 rounded-xl text-xs font-semibold transition-all bg-white text-slate-900 shadow-sm'
                    : 'py-2 px-3.5 rounded-xl text-xs font-semibold transition-all text-slate-600 hover:text-slate-900';
            }
        });
        if (subView === 'cases') {
            loadBrokerCases(brokerState.casesPage);
        } else if (subView === 'lenders') {
            loadBrokerLenders();
        } else if (subView === 'commissions') {
            loadBrokerCommissions(brokerState.commissionsPage);
        } else if (subView === 'calendar' && typeof loadCrmCalendar === 'function') {
            loadCrmCalendar('broker-calendar-root');
        }
    }

    async function loadBrokerWorkspace() {
        if (!_brokerEnabled()) return;
        await Promise.all([
            loadBrokerLenders(),
            loadBrokerCases(brokerState.casesPage),
            loadBrokerCommissions(brokerState.commissionsPage),
        ]);
    }

    function debounceBrokerCaseSearch() {
        clearTimeout(brokerState.caseSearchTimer);
        brokerState.caseSearchTimer = setTimeout(() => loadBrokerCases(1), 280);
    }

    function debounceBrokerLenderSearch() {
        clearTimeout(brokerState.lenderSearchTimer);
        brokerState.lenderSearchTimer = setTimeout(() => loadBrokerLenders(), 280);
    }

    // --- CASES ---
    async function loadBrokerCases(page = 1) {
        if (!_brokerEnabled()) return;
        brokerState.casesPage = page;
        const tbody = document.getElementById('broker-cases-tbody');
        const status = document.getElementById('broker-case-status-filter')?.value || 'ALL';
        const search = (document.getElementById('broker-case-search-input')?.value || '').trim();
        const params = new URLSearchParams({ page: String(page), page_size: '20' });
        if (status && status !== 'ALL') params.set('status', status);
        if (search) params.set('search', search);

        try {
            const data = await brokerFetch(`/api/broker/cases?${params.toString()}`);
            const items = Array.isArray(data?.items) ? data.items : [];
            brokerState.casesCache = items;
            brokerState.casesTotal = Number(data?.total || 0);

            if (tbody) {
                if (items.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="7" class="py-10 text-center text-slate-400">No broker cases found. Click "+ New Broker Case" to originate a brokerage file.</td></tr>';
                } else {
                    tbody.innerHTML = items.map(c => `
                        <tr class="hover:bg-slate-50/90 transition cursor-pointer" onclick="openBrokerCaseDetail(${Number(c.id)})">
                            <td class="py-3.5 px-4 font-mono font-bold text-indigo-700">${_esc(c.case_number)}</td>
                            <td class="py-3.5 px-4">
                                <p class="font-bold text-slate-900">${_esc(c.customer_name || 'Customer')}</p>
                                <p class="text-[10px] font-mono text-slate-500">${_esc(c.customer_number || '')}</p>
                            </td>
                            <td class="py-3.5 px-4 font-semibold text-slate-800">${_esc(c.product_type)}</td>
                            <td class="py-3.5 px-4"><span class="text-[10px] font-bold px-2.5 py-0.5 rounded-full border ${_caseStatusBadge(c.status)}">${_esc(c.status)}</span></td>
                            <td class="py-3.5 px-4 text-right font-mono font-semibold text-slate-900">${_esc(_fmtCents(c.requested_amount_cents))}</td>
                            <td class="py-3.5 px-4 text-center text-slate-700 font-semibold">
                                ${Number(c.submissions_count || 0)} sent • ${Number(c.approved_offers_count || 0)} appr.
                                ${c.selected_lender_name ? `<div class="text-[10px] text-emerald-700 font-bold">✓ ${_esc(c.selected_lender_name)}</div>` : ''}
                            </td>
                            <td class="py-3.5 px-4 text-right"><button type="button" class="text-xs font-bold text-indigo-600 hover:underline">Manage →</button></td>
                        </tr>
                    `).join('');
                }
            }

            const startIdx = brokerState.casesTotal === 0 ? 0 : (page - 1) * (data.page_size || 20) + 1;
            const endIdx = Math.min(page * (data.page_size || 20), brokerState.casesTotal);
            const infoEl = document.getElementById('broker-cases-pagination-info');
            if (infoEl) infoEl.innerText = `Showing ${startIdx}–${endIdx} of ${brokerState.casesTotal} cases`;
            const prevBtn = document.getElementById('broker-case-prev-btn');
            const nextBtn = document.getElementById('broker-case-next-btn');
            if (prevBtn) prevBtn.disabled = page <= 1;
            if (nextBtn) nextBtn.disabled = endIdx >= brokerState.casesTotal;
        } catch (err) {
            if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="py-8 text-center text-red-600">${_esc(err.message)}</td></tr>`;
        }
    }

    function changeBrokerCasePage(delta) {
        const next = brokerState.casesPage + delta;
        if (next >= 1) loadBrokerCases(next);
    }

    async function openBrokerCaseFromAnywhere(caseId) {
        if (!_brokerEnabled()) return;
        if (typeof switchTab === 'function') switchTab('broker');
        switchBrokerSubView('cases');
        await openBrokerCaseDetail(caseId);
    }

    async function openBrokerCaseDetail(caseId) {
        if (!_brokerEnabled()) return;
        try {
            const detail = await brokerFetch(`/api/broker/cases/${caseId}`);
            brokerState.selectedCase = detail;
            renderBrokerCaseDetail(detail);
            document.getElementById('broker-cases-master')?.classList.add('hidden');
            document.getElementById('broker-case-detail')?.classList.remove('hidden');
        } catch (err) {
            alert(err.message);
        }
    }

    function closeBrokerCaseDetail() {
        brokerState.selectedCase = null;
        document.getElementById('broker-case-detail')?.classList.add('hidden');
        document.getElementById('broker-cases-master')?.classList.remove('hidden');
    }

    function renderBrokerCaseDetail(c) {
        const setTxt = (id, txt) => {
            const el = document.getElementById(id);
            if (el) el.innerText = String(txt);
        };
        setTxt('bcd-number-crumb', c.case_number);
        setTxt('bcd-title', `${c.case_number} — ${c.customer_name || 'Customer'}`);
        const badge = document.getElementById('bcd-status-badge');
        if (badge) {
            badge.innerText = c.status;
            badge.className = `text-[10px] font-extrabold px-2.5 py-0.5 rounded-full uppercase border ${_caseStatusBadge(c.status)}`;
        }
        setTxt('bcd-product', c.product_type || '—');
        setTxt('bcd-requested', _fmtCents(c.requested_amount_cents));
        setTxt('bcd-term', c.requested_term_months ? `${c.requested_term_months} months` : '—');
        setTxt('bcd-funded', c.funded_amount_cents != null ? _fmtCents(c.funded_amount_cents) : 'Pending funding');
        setTxt('bcd-purpose', c.purpose || 'Not specified');
        setTxt('bcd-ext-ref', c.external_loan_reference || '—');

        const isTerminal = ['FUNDED', 'CLOSED_LOST', 'CANCELLED'].includes(c.status);
        const role = currentUser?.role || '';
        const canAddSub = !isTerminal && ['OWNER_ADMIN', 'SALES_AGENT', 'CREDIT_ADVISOR'].includes(role);
        const canUpdateSub = !isTerminal && ['OWNER_ADMIN', 'CREDIT_ADVISOR', 'OPERATIONS_OFFICER'].includes(role);
        const canSelect = !isTerminal && ['OWNER_ADMIN', 'CREDIT_ADVISOR'].includes(role);
        const canFund = !isTerminal && Boolean(c.selected_submission_id) && ['OWNER_ADMIN', 'CREDIT_ADVISOR', 'OPERATIONS_OFFICER'].includes(role);

        document.getElementById('btn-bcd-add-sub')?.classList.toggle('hidden', !canAddSub);
        document.getElementById('btn-bcd-fund')?.classList.toggle('hidden', !canFund);
        document.getElementById('btn-bcd-close-lost')?.classList.toggle('hidden', !canAddSub);

        const subTbody = document.getElementById('bcd-submissions-tbody');
        const subs = Array.isArray(c.submissions) ? c.submissions : [];
        if (subTbody) {
            if (subs.length === 0) {
                subTbody.innerHTML = '<tr><td colspan="7" class="py-8 text-center text-slate-400">No lender submissions yet. Submit this case to one or more partner lenders.</td></tr>';
            } else {
                subTbody.innerHTML = subs.map(s => {
                    const offerAmt = s.offered_amount_cents != null ? _fmtCents(s.offered_amount_cents) : '—';
                    const offerRate = _fmtBps(s.offered_rate_bps);
                    const offerComm = _fmtBps(s.offered_commission_bps);
                    const isSelectable = canSelect && s.status === 'APPROVED';
                    const isEditable = canUpdateSub && !['DECLINED', 'WITHDRAWN'].includes(s.status);
                    return `
                        <tr class="hover:bg-slate-50/70">
                            <td class="py-3 px-4">
                                <p class="font-bold text-slate-900">${_esc(s.lender_name || 'Lender')}</p>
                                <p class="text-[10px] font-mono text-slate-500">${_esc(s.lender_code || '')} ${s.external_reference ? `• Ref: ${_esc(s.external_reference)}` : ''}</p>
                            </td>
                            <td class="py-3 px-4"><span class="text-[10px] font-bold px-2.5 py-0.5 rounded-full border ${_subStatusBadge(s.status)}">${_esc(s.status)}</span></td>
                            <td class="py-3 px-4 text-right font-mono font-bold text-slate-900">${_esc(offerAmt)}</td>
                            <td class="py-3 px-4 text-right font-mono text-slate-700">${_esc(offerRate)}</td>
                            <td class="py-3 px-4 text-center text-slate-700">${s.offered_term_months ? `${Number(s.offered_term_months)}m` : '—'}</td>
                            <td class="py-3 px-4 text-right font-mono text-indigo-700 font-semibold">${_esc(offerComm)}</td>
                            <td class="py-3 px-4 text-right whitespace-nowrap space-x-1.5">
                                ${isEditable ? `<button type="button" onclick="openBrokerSubmissionModal('edit', ${Number(s.id)})" class="py-1 px-2.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-[11px] font-bold">Update Offer</button>` : ''}
                                ${isSelectable ? `<button type="button" onclick="selectBrokerSubmissionOffer(${Number(s.id)})" class="py-1 px-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-[11px] font-bold">Select Winner</button>` : ''}
                            </td>
                        </tr>
                    `;
                }).join('');
            }
        }

        const commCard = document.getElementById('bcd-commission-card');
        if (commCard) {
            if (!c.commission) {
                commCard.classList.add('hidden');
            } else {
                const cm = c.commission;
                commCard.classList.remove('hidden');
                setTxt('bcd-comm-number', cm.commission_number);
                setTxt('bcd-comm-lender', cm.lender_name || '—');
                setTxt('bcd-comm-rate', _fmtBps(cm.commission_rate_bps));
                setTxt('bcd-comm-expected', _fmtCents(cm.expected_commission_cents));
                setTxt('bcd-comm-paid', _fmtCents(cm.paid_commission_cents || 0));
                const cmBadge = document.getElementById('bcd-comm-status-badge');
                if (cmBadge) {
                    cmBadge.innerText = cm.status;
                    cmBadge.className = `text-[10px] font-bold px-2.5 py-0.5 rounded-full border ${_commStatusBadge(cm.status)}`;
                }
                const canUpdateComm = ['OWNER_ADMIN', 'OPERATIONS_OFFICER'].includes(role) && !['PAID', 'CANCELLED'].includes(cm.status);
                const cmBtn = document.getElementById('btn-bcd-update-comm');
                if (cmBtn) {
                    cmBtn.classList.toggle('hidden', !canUpdateComm);
                    cmBtn.onclick = () => openBrokerCommissionModal(cm.id);
                }
            }
        }
    }

    // --- LENDERS CATALOG ---
    async function loadBrokerLenders() {
        if (!_brokerEnabled()) return;
        const tbody = document.getElementById('broker-lenders-tbody');
        const activeOnly = Boolean(document.getElementById('broker-lender-active-only')?.checked);
        const search = (document.getElementById('broker-lender-search-input')?.value || '').trim();
        const params = new URLSearchParams();
        if (activeOnly) params.set('active_only', 'true');
        if (search) params.set('search', search);

        try {
            const list = await brokerFetch(`/api/broker/lenders${params.toString() ? `?${params.toString()}` : ''}`);
            brokerState.lendersCache = Array.isArray(list) ? list : [];
            const canManage = ['OWNER_ADMIN', 'CREDIT_ADVISOR'].includes(currentUser?.role || '');

            if (tbody) {
                if (brokerState.lendersCache.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="6" class="py-10 text-center text-slate-400">No partner lenders registered yet. Add banks, SOFOMs, or fintech lenders.</td></tr>';
                } else {
                    tbody.innerHTML = brokerState.lendersCache.map(l => `
                        <tr class="hover:bg-slate-50/80">
                            <td class="py-3.5 px-4 font-mono font-bold text-indigo-700">${_esc(l.code)}</td>
                            <td class="py-3.5 px-4">
                                <p class="font-bold text-slate-900">${_esc(l.name)}</p>
                                ${l.contact_name ? `<p class="text-[10px] text-slate-500">${_esc(l.contact_name)} ${l.contact_email ? `• ${_esc(l.contact_email)}` : ''}</p>` : ''}
                            </td>
                            <td class="py-3.5 px-4 text-xs font-semibold text-slate-700">${_esc(l.lender_type)}</td>
                            <td class="py-3.5 px-4 text-right font-mono font-bold text-slate-900">${_esc(_fmtBps(l.default_commission_bps))} <span class="text-[10px] text-slate-400">(${Number(l.default_commission_bps || 0)} bps)</span></td>
                            <td class="py-3.5 px-4">
                                <span class="text-[10px] font-bold px-2.5 py-0.5 rounded-full border ${l.is_active ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-slate-100 text-slate-500 border-slate-200'}">${l.is_active ? 'ACTIVE' : 'INACTIVE'}</span>
                            </td>
                            <td class="py-3.5 px-4 text-right whitespace-nowrap">
                                ${canManage ? `<button type="button" onclick="openBrokerLenderModal('edit', ${Number(l.id)})" class="py-1 px-2.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-[11px] font-bold">Edit</button>` : '<span class="text-[10px] text-slate-400">Read-only</span>'}
                            </td>
                        </tr>
                    `).join('');
                }
            }
        } catch (err) {
            if (tbody) tbody.innerHTML = `<tr><td colspan="6" class="py-8 text-center text-red-600">${_esc(err.message)}</td></tr>`;
        }
    }

    // --- COMMISSIONS ---
    async function loadBrokerCommissions(page = 1) {
        if (!_brokerEnabled()) return;
        brokerState.commissionsPage = page;
        const tbody = document.getElementById('broker-commissions-tbody');
        const status = document.getElementById('broker-comm-status-filter')?.value || 'ALL';
        const params = new URLSearchParams({ page: String(page), page_size: '20' });
        if (status && status !== 'ALL') params.set('status', status);

        try {
            const data = await brokerFetch(`/api/broker/commissions?${params.toString()}`);
            const items = Array.isArray(data?.items) ? data.items : [];
            brokerState.commissionsCache = items;
            brokerState.commissionsTotal = Number(data?.total || 0);
            const canManage = ['OWNER_ADMIN', 'OPERATIONS_OFFICER'].includes(currentUser?.role || '');

            if (tbody) {
                if (items.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="8" class="py-10 text-center text-slate-400">No commissions recorded yet. Fund a broker case with a selected offer to generate a commission.</td></tr>';
                } else {
                    tbody.innerHTML = items.map(cm => {
                        const isEditable = canManage && !['PAID', 'CANCELLED'].includes(cm.status);
                        return `
                            <tr class="hover:bg-slate-50/80">
                                <td class="py-3.5 px-4 font-mono font-bold text-indigo-700">${_esc(cm.commission_number)}</td>
                                <td class="py-3.5 px-4 font-mono font-semibold text-slate-800">${_esc(cm.case_number || `#${cm.broker_case_id}`)}</td>
                                <td class="py-3.5 px-4 font-semibold text-slate-800">${_esc(cm.lender_name || '—')}</td>
                                <td class="py-3.5 px-4 text-right font-mono text-slate-800">${_esc(_fmtCents(cm.funded_amount_cents))}</td>
                                <td class="py-3.5 px-4 text-right font-mono text-slate-600">${_esc(_fmtBps(cm.commission_rate_bps))}</td>
                                <td class="py-3.5 px-4 text-right font-mono font-bold text-slate-900">${_esc(_fmtCents(cm.expected_commission_cents))}</td>
                                <td class="py-3.5 px-4">
                                    <span class="text-[10px] font-bold px-2.5 py-0.5 rounded-full border ${_commStatusBadge(cm.status)}">${_esc(cm.status)}</span>
                                    ${cm.payment_reference ? `<div class="text-[10px] font-mono text-slate-400 mt-0.5">${_esc(cm.payment_reference)}</div>` : ''}
                                </td>
                                <td class="py-3.5 px-4 text-right whitespace-nowrap">
                                    ${isEditable ? `<button type="button" onclick="openBrokerCommissionModal(${Number(cm.id)})" class="py-1 px-2.5 rounded-lg bg-indigo-50 hover:bg-indigo-100 text-indigo-700 text-[11px] font-bold">Update</button>` : '<span class="text-[10px] text-slate-400">—</span>'}
                                </td>
                            </tr>
                        `;
                    }).join('');
                }
            }
        } catch (err) {
            if (tbody) tbody.innerHTML = `<tr><td colspan="8" class="py-8 text-center text-red-600">${_esc(err.message)}</td></tr>`;
        }
    }

    // -------------------------------------------------------------------------
    // 4. MODALS & MUTATIONS (LENDERS, CASES, SUBMISSIONS, SELECT, FUND, COMMISSIONS)
    // -------------------------------------------------------------------------
    function openBrokerLenderModal(mode = 'create', lenderId = null) {
        if (!_brokerEnabled()) return;
        brokerState.editingLenderId = (mode === 'edit') ? lenderId : null;
        document.getElementById('broker-lender-form-error')?.classList.add('hidden');
        document.getElementById('broker-lender-form')?.reset();

        const codeEl = document.getElementById('blf-code');
        const activeWrap = document.getElementById('blf-active-wrap');
        if (mode === 'edit' && lenderId) {
            const l = brokerState.lendersCache.find(item => item.id === lenderId);
            if (!l) return;
            document.getElementById('broker-lender-modal-title').innerText = `Edit Partner Lender (${l.code})`;
            if (codeEl) { codeEl.value = l.code; codeEl.readOnly = true; }
            document.getElementById('blf-name').value = l.name || '';
            document.getElementById('blf-type').value = l.lender_type || 'BANK';
            document.getElementById('blf-bps').value = String(l.default_commission_bps ?? 0);
            document.getElementById('blf-contact-name').value = l.contact_name || '';
            document.getElementById('blf-contact-email').value = l.contact_email || '';
            document.getElementById('blf-contact-phone').value = l.contact_phone || '';
            document.getElementById('blf-notes').value = l.notes || '';
            document.getElementById('blf-is-active').checked = Boolean(l.is_active);
            activeWrap?.classList.remove('hidden');
        } else {
            document.getElementById('broker-lender-modal-title').innerText = 'Register Partner Lender';
            if (codeEl) { codeEl.value = ''; codeEl.readOnly = false; }
            document.getElementById('blf-bps').value = '250';
            document.getElementById('blf-is-active').checked = true;
            activeWrap?.classList.add('hidden');
        }
        document.getElementById('broker-lender-modal')?.classList.remove('hidden');
    }

    function closeBrokerLenderModal() {
        document.getElementById('broker-lender-modal')?.classList.add('hidden');
    }

    async function handleBrokerLenderSubmit(e) {
        e.preventDefault();
        if (!_brokerEnabled()) return;
        const errBox = document.getElementById('broker-lender-form-error');
        const btn = document.getElementById('blf-submit-btn');
        errBox?.classList.add('hidden');
        if (btn) btn.disabled = true;

        const payload = {
            name: document.getElementById('blf-name').value.trim(),
            lender_type: document.getElementById('blf-type').value,
            default_commission_bps: Number(document.getElementById('blf-bps').value || 0),
            contact_name: document.getElementById('blf-contact-name').value.trim() || null,
            contact_email: document.getElementById('blf-contact-email').value.trim() || null,
            contact_phone: document.getElementById('blf-contact-phone').value.trim() || null,
            notes: document.getElementById('blf-notes').value.trim() || null,
        };
        try {
            if (brokerState.editingLenderId) {
                payload.is_active = Boolean(document.getElementById('blf-is-active').checked);
                await brokerFetch(`/api/broker/lenders/${brokerState.editingLenderId}`, { method: 'PATCH', body: payload });
            } else {
                payload.code = document.getElementById('blf-code').value.trim();
                await brokerFetch('/api/broker/lenders', { method: 'POST', body: payload });
            }
            closeBrokerLenderModal();
            await loadBrokerLenders();
            await loadBrokerDashboardSummary();
        } catch (err) {
            if (errBox) { errBox.innerText = err.message; errBox.classList.remove('hidden'); }
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async function openBrokerCaseModal(preselectedCustomerId = null) {
        if (!_brokerEnabled()) return;
        document.getElementById('broker-case-form-error')?.classList.add('hidden');
        document.getElementById('broker-case-form')?.reset();
        const selectEl = document.getElementById('bcf-customer-id');
        if (selectEl) {
            selectEl.innerHTML = '<option value="">Loading customers...</option>';
            try {
                const res = await fetch('/api/customers?page=1&page_size=100&status=ACTIVE');
                const data = res.ok ? await res.json() : { items: [] };
                const items = Array.isArray(data.items) ? data.items : [];
                selectEl.innerHTML = '<option value="">— Select Active Customer —</option>' + items.map(c => (
                    `<option value="${Number(c.id)}">${_esc(c.customer_number)} — ${_esc(c.full_legal_name)}</option>`
                )).join('');
                if (preselectedCustomerId) {
                    selectEl.value = String(preselectedCustomerId);
                }
            } catch (_) {
                selectEl.innerHTML = '<option value="">Could not load customers</option>';
            }
        }
        document.getElementById('broker-case-modal')?.classList.remove('hidden');
    }

    function closeBrokerCaseModal() {
        document.getElementById('broker-case-modal')?.classList.add('hidden');
    }

    async function handleBrokerCaseSubmit(e) {
        e.preventDefault();
        if (!_brokerEnabled()) return;
        const errBox = document.getElementById('broker-case-form-error');
        const btn = document.getElementById('bcf-submit-btn');
        errBox?.classList.add('hidden');
        if (btn) btn.disabled = true;

        const customerId = Number(document.getElementById('bcf-customer-id').value);
        const leadRaw = document.getElementById('bcf-source-lead-id').value.trim();
        const termRaw = document.getElementById('bcf-term-months').value.trim();
        const payload = {
            idempotency_key: _generateIdemKey('brk-ui'),
            customer_id: customerId,
            source_lead_id: leadRaw ? Number(leadRaw) : null,
            product_type: document.getElementById('bcf-product-type').value.trim(),
            requested_amount_pesos: Number(document.getElementById('bcf-amount-pesos').value),
            requested_term_months: termRaw ? Number(termRaw) : null,
            purpose: document.getElementById('bcf-purpose').value.trim() || null,
            notes: document.getElementById('bcf-notes').value.trim() || null,
        };
        try {
            const res = await brokerFetch('/api/broker/cases', { method: 'POST', body: payload });
            closeBrokerCaseModal();
            await loadBrokerCases(1);
            await loadBrokerDashboardSummary();
            if (currentSelectedCustomer && currentSelectedCustomer.id === customerId) {
                await loadCustomerBrokerSummary(customerId);
            }
            if (res && res.case && res.case.id) {
                if (typeof switchTab === 'function') switchTab('broker');
                switchBrokerSubView('cases');
                await openBrokerCaseDetail(res.case.id);
            }
        } catch (err) {
            if (errBox) { errBox.innerText = err.message; errBox.classList.remove('hidden'); }
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async function closeSelectedBrokerCaseAsLost() {
        if (!_brokerEnabled() || !brokerState.selectedCase) return;
        const reason = prompt('Reason for closing case as CLOSED_LOST:');
        if (reason === null) return;
        try {
            const updated = await brokerFetch(`/api/broker/cases/${brokerState.selectedCase.id}`, {
                method: 'PATCH',
                body: { status: 'CLOSED_LOST', closed_reason: reason.trim() || 'Closed lost' },
            });
            brokerState.selectedCase = updated;
            renderBrokerCaseDetail(updated);
            await loadBrokerCases(brokerState.casesPage);
            await loadBrokerDashboardSummary();
        } catch (err) {
            alert(err.message);
        }
    }

    async function openBrokerSubmissionModal(mode = 'create', submissionId = null) {
        if (!_brokerEnabled() || !brokerState.selectedCase) return;
        brokerState.editingSubmissionId = (mode === 'edit') ? submissionId : null;
        document.getElementById('broker-sub-form-error')?.classList.add('hidden');
        document.getElementById('broker-sub-form')?.reset();

        if (brokerState.lendersCache.length === 0) {
            await loadBrokerLenders();
        }
        const lenderSelect = document.getElementById('bsf-lender-id');
        if (lenderSelect) {
            const activeLenders = brokerState.lendersCache.filter(l => l.is_active);
            lenderSelect.innerHTML = '<option value="">— Select Partner Lender —</option>' + activeLenders.map(l => (
                `<option value="${Number(l.id)}">${_esc(l.code)} — ${_esc(l.name)} (${_esc(_fmtBps(l.default_commission_bps))})</option>`
            )).join('');
        }

        const lenderWrap = document.getElementById('bsf-lender-wrap');
        if (mode === 'edit' && submissionId) {
            const s = (brokerState.selectedCase.submissions || []).find(item => item.id === submissionId);
            if (!s) return;
            document.getElementById('broker-sub-modal-title').innerText = `Update Offer — ${s.lender_name || 'Lender'}`;
            lenderWrap?.classList.add('hidden');
            if (lenderSelect) lenderSelect.required = false;
            document.getElementById('bsf-status').value = s.status || 'SUBMITTED';
            document.getElementById('bsf-ext-ref').value = s.external_reference || '';
            document.getElementById('bsf-amount').value = s.offered_amount_cents != null ? String(s.offered_amount_cents / 100) : '';
            document.getElementById('bsf-rate-bps').value = s.offered_rate_bps != null ? String(s.offered_rate_bps) : '';
            document.getElementById('bsf-term').value = s.offered_term_months != null ? String(s.offered_term_months) : '';
            document.getElementById('bsf-comm-bps').value = s.offered_commission_bps != null ? String(s.offered_commission_bps) : '';
            document.getElementById('bsf-notes').value = s.decision_notes || '';
        } else {
            document.getElementById('broker-sub-modal-title').innerText = 'Submit Case to Partner Lender';
            lenderWrap?.classList.remove('hidden');
            if (lenderSelect) lenderSelect.required = true;
            document.getElementById('bsf-status').value = 'SUBMITTED';
        }
        document.getElementById('broker-submission-modal')?.classList.remove('hidden');
    }

    function closeBrokerSubmissionModal() {
        document.getElementById('broker-submission-modal')?.classList.add('hidden');
    }

    async function handleBrokerSubmissionSubmit(e) {
        e.preventDefault();
        if (!_brokerEnabled() || !brokerState.selectedCase) return;
        const errBox = document.getElementById('broker-sub-form-error');
        const btn = document.getElementById('bsf-submit-btn');
        errBox?.classList.add('hidden');
        if (btn) btn.disabled = true;

        const amtRaw = document.getElementById('bsf-amount').value.trim();
        const rateRaw = document.getElementById('bsf-rate-bps').value.trim();
        const termRaw = document.getElementById('bsf-term').value.trim();
        const commRaw = document.getElementById('bsf-comm-bps').value.trim();
        const payload = {
            status: document.getElementById('bsf-status').value,
            external_reference: document.getElementById('bsf-ext-ref').value.trim() || null,
            offered_amount_pesos: amtRaw ? Number(amtRaw) : null,
            offered_rate_bps: rateRaw ? Number(rateRaw) : null,
            offered_term_months: termRaw ? Number(termRaw) : null,
            offered_commission_bps: commRaw ? Number(commRaw) : null,
            decision_notes: document.getElementById('bsf-notes').value.trim() || null,
        };
        try {
            const caseId = brokerState.selectedCase.id;
            if (brokerState.editingSubmissionId) {
                await brokerFetch(`/api/broker/cases/${caseId}/submissions/${brokerState.editingSubmissionId}`, {
                    method: 'PATCH',
                    body: payload,
                });
            } else {
                payload.lender_id = Number(document.getElementById('bsf-lender-id').value);
                await brokerFetch(`/api/broker/cases/${caseId}/submissions`, { method: 'POST', body: payload });
            }
            closeBrokerSubmissionModal();
            await openBrokerCaseDetail(caseId);
            await loadBrokerCases(brokerState.casesPage);
            await loadBrokerDashboardSummary();
        } catch (err) {
            if (errBox) { errBox.innerText = err.message; errBox.classList.remove('hidden'); }
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async function selectBrokerSubmissionOffer(submissionId) {
        if (!_brokerEnabled() || !brokerState.selectedCase) return;
        try {
            const caseId = brokerState.selectedCase.id;
            const updated = await brokerFetch(`/api/broker/cases/${caseId}/submissions/${submissionId}/select`, {
                method: 'POST',
                body: {},
            });
            brokerState.selectedCase = updated;
            renderBrokerCaseDetail(updated);
            await loadBrokerCases(brokerState.casesPage);
            await loadBrokerDashboardSummary();
        } catch (err) {
            alert(err.message);
        }
    }

    function openBrokerFundModal() {
        if (!_brokerEnabled() || !brokerState.selectedCase) return;
        const c = brokerState.selectedCase;
        const selected = (c.submissions || []).find(s => s.status === 'SELECTED');
        document.getElementById('broker-fund-form-error')?.classList.add('hidden');
        document.getElementById('broker-fund-form')?.reset();
        const defAmount = selected?.offered_amount_cents ?? c.requested_amount_cents;
        document.getElementById('bff-amount-pesos').value = defAmount ? String(defAmount / 100) : '';
        document.getElementById('bff-comm-bps').value = selected?.offered_commission_bps != null ? String(selected.offered_commission_bps) : '';
        document.getElementById('broker-fund-modal')?.classList.remove('hidden');
    }

    function closeBrokerFundModal() {
        document.getElementById('broker-fund-modal')?.classList.add('hidden');
    }

    async function handleBrokerFundSubmit(e) {
        e.preventDefault();
        if (!_brokerEnabled() || !brokerState.selectedCase) return;
        const errBox = document.getElementById('broker-fund-form-error');
        const btn = document.getElementById('bff-submit-btn');
        errBox?.classList.add('hidden');
        if (btn) btn.disabled = true;

        const amtRaw = document.getElementById('bff-amount-pesos').value.trim();
        const bpsRaw = document.getElementById('bff-comm-bps').value.trim();
        const payDateRaw = document.getElementById('bff-expected-pay-date').value.trim();
        const payload = {
            funded_amount_pesos: amtRaw ? Number(amtRaw) : null,
            commission_rate_bps: bpsRaw ? Number(bpsRaw) : null,
            external_loan_reference: document.getElementById('bff-ext-ref').value.trim() || null,
            expected_pay_date: payDateRaw || null,
            notes: document.getElementById('bff-notes').value.trim() || null,
        };
        try {
            const caseId = brokerState.selectedCase.id;
            const updated = await brokerFetch(`/api/broker/cases/${caseId}/fund`, { method: 'POST', body: payload });
            brokerState.selectedCase = updated;
            closeBrokerFundModal();
            renderBrokerCaseDetail(updated);
            await Promise.all([
                loadBrokerCases(brokerState.casesPage),
                loadBrokerCommissions(1),
                loadBrokerDashboardSummary(),
            ]);
        } catch (err) {
            if (errBox) { errBox.innerText = err.message; errBox.classList.remove('hidden'); }
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    function openBrokerCommissionModal(commissionId) {
        if (!_brokerEnabled()) return;
        brokerState.editingCommissionId = commissionId;
        const cm = brokerState.commissionsCache.find(item => item.id === commissionId)
            || (brokerState.selectedCase && brokerState.selectedCase.commission && brokerState.selectedCase.commission.id === commissionId
                ? brokerState.selectedCase.commission
                : null);
        document.getElementById('broker-comm-form-error')?.classList.add('hidden');
        document.getElementById('broker-comm-form')?.reset();
        if (cm) {
            document.getElementById('broker-comm-modal-title').innerText = `Update Commission ${cm.commission_number}`;
            document.getElementById('bcmf-status').value = cm.status === 'PENDING' ? 'INVOICED' : 'PAID';
            document.getElementById('bcmf-paid-pesos').value = String((cm.paid_commission_cents || cm.expected_commission_cents || 0) / 100);
            document.getElementById('bcmf-pay-ref').value = cm.payment_reference || '';
            document.getElementById('bcmf-notes').value = cm.notes || '';
        }
        document.getElementById('broker-commission-modal')?.classList.remove('hidden');
    }

    function closeBrokerCommissionModal() {
        document.getElementById('broker-commission-modal')?.classList.add('hidden');
    }

    async function handleBrokerCommissionSubmit(e) {
        e.preventDefault();
        if (!_brokerEnabled() || !brokerState.editingCommissionId) return;
        const errBox = document.getElementById('broker-comm-form-error');
        const btn = document.getElementById('bcmf-submit-btn');
        errBox?.classList.add('hidden');
        if (btn) btn.disabled = true;

        const status = document.getElementById('bcmf-status').value;
        const paidRaw = document.getElementById('bcmf-paid-pesos').value.trim();
        const payload = {
            status,
            payment_reference: document.getElementById('bcmf-pay-ref').value.trim() || null,
            notes: document.getElementById('bcmf-notes').value.trim() || null,
        };
        if (status === 'PAID' && paidRaw) {
            payload.paid_commission_pesos = Number(paidRaw);
        }
        try {
            await brokerFetch(`/api/broker/commissions/${brokerState.editingCommissionId}`, { method: 'PATCH', body: payload });
            closeBrokerCommissionModal();
            await Promise.all([
                loadBrokerCommissions(brokerState.commissionsPage),
                loadBrokerDashboardSummary(),
            ]);
            if (brokerState.selectedCase && brokerState.selectedCase.id) {
                await openBrokerCaseDetail(brokerState.selectedCase.id);
            }
        } catch (err) {
            if (errBox) { errBox.innerText = err.message; errBox.classList.remove('hidden'); }
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    function rerenderBrokerViewsOnLangChange() {
        if (!_brokerEnabled()) return;
        if (brokerState.lastDashboardSummary) {
            renderBrokerDashboardSummary(brokerState.lastDashboardSummary);
        }
        if (brokerState.selectedCase) {
            renderBrokerCaseDetail(brokerState.selectedCase);
        }
    }

    // Export public Broker UI handlers on window
    window.rerenderBrokerViewsOnLangChange = rerenderBrokerViewsOnLangChange;
    window.resetBrokerWorkspaceState = resetBrokerWorkspaceState;
    window.loadBrokerDashboardSummary = loadBrokerDashboardSummary;
    window.loadCustomerBrokerSummary = loadCustomerBrokerSummary;
    window.loadBrokerWorkspace = loadBrokerWorkspace;
    window.switchBrokerSubView = switchBrokerSubView;
    window.debounceBrokerCaseSearch = debounceBrokerCaseSearch;
    window.debounceBrokerLenderSearch = debounceBrokerLenderSearch;
    window.loadBrokerCases = loadBrokerCases;
    window.changeBrokerCasePage = changeBrokerCasePage;
    window.openBrokerCaseFromAnywhere = openBrokerCaseFromAnywhere;
    window.openBrokerCaseDetail = openBrokerCaseDetail;
    window.closeBrokerCaseDetail = closeBrokerCaseDetail;
    window.loadBrokerLenders = loadBrokerLenders;
    window.loadBrokerCommissions = loadBrokerCommissions;
    window.openBrokerLenderModal = openBrokerLenderModal;
    window.closeBrokerLenderModal = closeBrokerLenderModal;
    window.handleBrokerLenderSubmit = handleBrokerLenderSubmit;
    window.openBrokerCaseModal = openBrokerCaseModal;
    window.closeBrokerCaseModal = closeBrokerCaseModal;
    window.handleBrokerCaseSubmit = handleBrokerCaseSubmit;
    window.closeSelectedBrokerCaseAsLost = closeSelectedBrokerCaseAsLost;
    window.openBrokerSubmissionModal = openBrokerSubmissionModal;
    window.closeBrokerSubmissionModal = closeBrokerSubmissionModal;
    window.handleBrokerSubmissionSubmit = handleBrokerSubmissionSubmit;
    window.selectBrokerSubmissionOffer = selectBrokerSubmissionOffer;
    window.openBrokerFundModal = openBrokerFundModal;
    window.closeBrokerFundModal = closeBrokerFundModal;
    window.handleBrokerFundSubmit = handleBrokerFundSubmit;
    window.openBrokerCommissionModal = openBrokerCommissionModal;
    window.closeBrokerCommissionModal = closeBrokerCommissionModal;
    window.handleBrokerCommissionSubmit = handleBrokerCommissionSubmit;
})();
