        /* =========================================================================
           4B. STAGE 3 LIVE LOANS MODULE (BOOK OF BUSINESS, FILTERS & PAGINATION)
           ========================================================================= */
        let currentLoansPage = 1;
        let currentLoansTotal = 0;
        let currentLoanFilter = 'all';
        let loanSearchTimer = null;
        let loansRequestSeq = 0;

        function debounceLoanSearch() {
            clearTimeout(loanSearchTimer);
            loanSearchTimer = setTimeout(() => loadLoans(1), 280);
        }

        function navigateToLoansFilter(filterKey) {
            switchTab('loans');
            closeLoanDetailBlueprint();
            setLoansFilterUI(filterKey);
        }

        function setLoansFilterUI(filterKey) {
            currentLoanFilter = (filterKey || 'all').toLowerCase();

            const labels = {
                all: 'All Loans',
                active: 'Active Loans (Unliquidated)',
                current: 'Current / On-Time Loans (0 Overdue Installments)',
                overdue: 'Overdue Loans (≥ 1 Past-Due Installment)',
                draft: 'Draft Loans (Pending Approval)',
                approved: 'Approved Loans (Pending Disbursement)',
                paidoff: 'Paid Off / Closed Loans',
                cancelled: 'Cancelled Loans',
                defaulted: 'Defaulted Loans'
            };

            const labelEl = document.getElementById('loans-active-filter-label');
            if (labelEl) {
                labelEl.innerText = `Showing: ${labels[currentLoanFilter] || 'All Loans'}`;
            }

            const selectEl = document.getElementById('loan-status-filter');
            if (selectEl && selectEl.value !== currentLoanFilter) {
                selectEl.value = currentLoanFilter;
            }

            if (typeof switchLoansSubView === 'function') {
                switchLoansSubView('book', false);
            }

            ['all', 'active', 'current', 'overdue', 'paidoff', 'defaulted'].forEach(k => {
                const pill = document.getElementById('lf-pill-' + k);
                if (!pill) return;
                if (k === currentLoanFilter) {
                    pill.className = 'px-3.5 py-1.5 rounded-xl font-semibold bg-slate-900 text-white';
                } else if (k === 'current') {
                    pill.className = 'px-3.5 py-1.5 rounded-xl font-semibold bg-emerald-50 text-emerald-700 hover:bg-emerald-100';
                } else if (k === 'overdue') {
                    pill.className = 'px-3.5 py-1.5 rounded-xl font-semibold bg-red-50 text-red-600 hover:bg-red-100';
                } else if (k === 'defaulted') {
                    pill.className = 'px-3.5 py-1.5 rounded-xl font-semibold bg-rose-50 text-rose-800 hover:bg-rose-100';
                } else {
                    pill.className = 'px-3.5 py-1.5 rounded-xl font-semibold bg-slate-100 text-slate-700 hover:bg-slate-200';
                }
            });

            loadLoans(1);
        }

        async function loadLoans(page = 1) {
            currentLoansPage = page;
            const thisRequestId = ++loansRequestSeq;
            const search = document.getElementById('loan-search-input')?.value.trim() || '';
            const tbody = document.getElementById('loans-table-tbody');
            const warningEl = document.getElementById('loans-derived-warning');
            if (!tbody) return;

            const isDerivedFilter = (currentLoanFilter === 'current' || currentLoanFilter === 'overdue');
            let isSampleTruncated = false;
            let totalActiveInBackend = 0;

            const params = new URLSearchParams();
            if (search) params.set('search', search);

            if (isDerivedFilter) {
                params.set('status', 'ACTIVE');
                params.set('page', '1');
                params.set('page_size', '100');
            } else {
                params.set('page', String(page));
                params.set('page_size', '20');
                if (currentLoanFilter !== 'all') {
                    const statusMap = {
                        active: 'ACTIVE',
                        draft: 'DRAFT',
                        approved: 'APPROVED',
                        paidoff: 'PAID_OFF',
                        cancelled: 'CANCELLED',
                        defaulted: 'DEFAULTED'
                    };
                    if (statusMap[currentLoanFilter]) {
                        params.set('status', statusMap[currentLoanFilter]);
                    }
                }
            }

            try {
                const res = await fetch(`/api/loans?${params.toString()}`);
                if (thisRequestId !== loansRequestSeq) return;

                if (res.status === 401) {
                    mostrarAuthView();
                    return;
                }
                if (res.status === 403) {
                    throw new Error("Insufficient permissions to view loans.");
                }
                if (!res.ok) throw new Error("Could not load loans from PostgreSQL.");

                const data = await res.json();
                if (thisRequestId !== loansRequestSeq) return;

                let items = Array.isArray(data.items) ? data.items : [];

                if (isDerivedFilter) {
                    totalActiveInBackend = Number.isSafeInteger(data.total) ? data.total : items.length;
                    isSampleTruncated = (totalActiveInBackend > 100);

                    if (warningEl) {
                        if (isSampleTruncated) {
                            const warnText = document.getElementById('loans-derived-warning-text');
                            if (warnText) {
                                warnText.innerText = `The active portfolio currently has ${totalActiveInBackend} loans matching this query. This view evaluates only the first 100 active records and is strictly partial. Figures shown are not definitive portfolio totals. Please narrow your query using the search bar to evaluate an exact set of 100 or fewer records.`;
                            }
                            warningEl.classList.remove('hidden');
                        } else {
                            warningEl.classList.add('hidden');
                        }
                    }

                    if (currentLoanFilter === 'current') {
                        items = items.filter(l =>
                            l &&
                            l.status === 'ACTIVE' &&
                            Number.isFinite(l.overdue_amount_cents) &&
                            l.overdue_amount_cents === 0
                        );
                    } else if (currentLoanFilter === 'overdue') {
                        items = items.filter(l =>
                            l &&
                            l.status === 'ACTIVE' &&
                            Number.isFinite(l.overdue_amount_cents) &&
                            l.overdue_amount_cents > 0
                        );
                    }

                    currentLoansTotal = items.length;
                    const pageSize = 20;
                    const startIdx = (page - 1) * pageSize;
                    items = items.slice(startIdx, startIdx + pageSize);
                } else {
                    if (warningEl) warningEl.classList.add('hidden');
                    currentLoansTotal = Number.isSafeInteger(data.total) ? data.total : items.length;
                }

                tbody.innerHTML = '';
                if (items.length === 0) {
                    const emptyMessage = (isDerivedFilter && isSampleTruncated)
                        ? 'No loans in this category within the first 100 evaluated active records. Narrow your search to evaluate specific loans.'
                        : 'There are no loans matching the selected filter.';

                    tbody.innerHTML = `
                        <tr>
                            <td colspan="9" class="py-12 px-6 text-center">
                                <div class="max-w-md mx-auto space-y-2">
                                    <p class="text-sm font-bold text-slate-900">No loans found</p>
                                    <p class="text-xs text-slate-500">${escapeHtml(emptyMessage)}</p>
                                </div>
                            </td>
                        </tr>
                    `;
                } else {
                    items.forEach(l => {
                        if (!l || typeof l !== 'object') return;

                        const hasValidLoanId = Number.isSafeInteger(l.id) && l.id > 0;
                        const hasValidCustomerId = Number.isSafeInteger(l.customer_id) && l.customer_id > 0;

                        const tr = document.createElement('tr');
                        tr.className = hasValidLoanId ? 'hover:bg-slate-50/90 transition cursor-pointer' : 'opacity-70';
                        if (hasValidLoanId) {
                            tr.onclick = () => openLoanDetail(l.id);
                        }

                        const badgeClass = getLoanStatusBadgeStyle(l.status);
                        const rateDisplay = Number.isFinite(l.monthly_interest_rate_percent)
                            ? `${l.monthly_interest_rate_percent.toFixed(2)}% / mo`
                            : '—';
                        const origPrincipal = Number.isFinite(l.principal_cents)
                            ? NuraFinancialEngine.formatMXNFromCents(l.principal_cents)
                            : 'N/A';
                        const unpaidPrincipal = Number.isFinite(l.unpaid_principal_cents)
                            ? NuraFinancialEngine.formatMXNFromCents(l.unpaid_principal_cents)
                            : 'N/A';
                        const payableToday = Number.isFinite(l.current_payable_amount_cents)
                            ? NuraFinancialEngine.formatMXNFromCents(l.current_payable_amount_cents)
                            : 'N/A';
                        const isStrictlyOverdue = Number.isFinite(l.overdue_amount_cents) && l.overdue_amount_cents > 0;

                        const custNameLabel = l.customer_name || `Customer #${l.customer_id}`;
                        const custNumLabel = l.customer_number || `CUST-${l.customer_id}`;
                        const customerCellHtml = hasValidCustomerId
                            ? `<button type="button" onclick="event.stopPropagation(); switchTab('customers'); openCustomerDetail(${l.customer_id});" class="text-left group">
                                   <p class="font-bold text-slate-900 group-hover:text-blue-600 group-hover:underline">${escapeHtml(custNameLabel)}</p>
                                   <p class="font-mono text-[10px] text-blue-700 font-semibold">${escapeHtml(custNumLabel)}</p>
                               </button>`
                            : `<span class="text-slate-400 font-mono">Customer #N/A</span>`;

                        const dpdVal = Number.isSafeInteger(l.days_past_due) ? l.days_past_due : 0;
                        const dpdBadgeHtml = dpdVal > 0
                            ? `<span class="ml-1 inline-block text-[9px] font-extrabold font-mono px-1.5 py-0.5 rounded bg-red-100 text-red-800 border border-red-200">${dpdVal}d DPD</span>`
                            : '';

                        const actionBtnHtml = hasValidLoanId
                            ? `<button type="button" class="text-xs font-bold text-blue-600 hover:underline">Inspect →</button>`
                            : `<span class="text-slate-400 text-xs">Invalid ID</span>`;

                        tr.innerHTML = `
                            <td class="py-3.5 px-4 font-mono font-bold text-blue-700">${escapeHtml(l.loan_number || '—')}</td>
                            <td class="py-3.5 px-4">${customerCellHtml}</td>
                            <td class="py-3.5 px-4">
                                <p class="font-bold text-slate-900">${escapeHtml(l.product_name || 'Loan')}</p>
                                <p class="text-[10px] text-slate-500">${escapeHtml(rateDisplay)} • ${escapeHtml(l.term_installments_count || 16)} wks</p>
                            </td>
                            <td class="py-3.5 px-4">
                                <span class="text-[10px] font-bold px-2.5 py-0.5 rounded-full border ${badgeClass}">${escapeHtml(l.status || 'UNKNOWN')}</span>
                                ${dpdBadgeHtml}
                            </td>
                            <td class="py-3.5 px-4 text-right font-mono text-slate-800">${escapeHtml(origPrincipal)}</td>
                            <td class="py-3.5 px-4 text-right font-mono font-bold text-slate-900">${escapeHtml(unpaidPrincipal)}</td>
                            <td class="py-3.5 px-4 text-right font-mono font-bold ${isStrictlyOverdue ? 'text-red-600' : 'text-slate-800'}">${escapeHtml(payableToday)}</td>
                            <td class="py-3.5 px-4 font-mono text-slate-600">${escapeHtml(l.maturity_date || '—')}</td>
                            <td class="py-3.5 px-4 text-right">${actionBtnHtml}</td>
                        `;
                        tbody.appendChild(tr);
                    });
                }

                const pageSize = 20;
                const startIdx = currentLoansTotal === 0 ? 0 : (page - 1) * pageSize + 1;
                const endIdx = Math.min(page * pageSize, currentLoansTotal);

                const infoEl = document.getElementById('loans-pagination-info');
                if (infoEl) {
                    if (isDerivedFilter && isSampleTruncated) {
                        infoEl.innerHTML = `<span class="text-amber-700 font-bold">Partial sample:</span> Showing ${startIdx}–${endIdx} of ${currentLoansTotal} evaluated loans (${totalActiveInBackend} active total in DB — narrow search for definitive figures)`;
                    } else {
                        infoEl.innerText = `Showing ${startIdx}–${endIdx} of ${currentLoansTotal} loans`;
                    }
                }

                const prevBtn = document.getElementById('loan-prev-btn');
                const nextBtn = document.getElementById('loan-next-btn');
                if (prevBtn) prevBtn.disabled = page <= 1;
                if (nextBtn) nextBtn.disabled = endIdx >= currentLoansTotal;
            } catch (err) {
                if (thisRequestId !== loansRequestSeq) return;
                tbody.innerHTML = `<tr><td colspan="9" class="py-8 text-center text-red-600">${escapeHtml(err.message)}</td></tr>`;
            }
        }

        function changeLoanPage(delta) {
            const next = currentLoansPage + delta;
            if (next >= 1) loadLoans(next);
        }

        let currentSelectedLoan = null;
        let loanDetailRequestSeq = 0;

        function closeLoanDetailView() {
            const detailView = document.getElementById('loan-detail-view');
            const masterView = document.getElementById('loans-master-view');
            const errBox = document.getElementById('loan-detail-error');
            const succBox = document.getElementById('loan-detail-success');
            if (errBox) errBox.classList.add('hidden');
            if (succBox) succBox.classList.add('hidden');
            if (detailView) detailView.classList.add('hidden');
            if (masterView) masterView.classList.remove('hidden');
        }

        function openLoanDetailBlueprint() {
            const masterView = document.getElementById('loans-master-view');
            const detailView = document.getElementById('loan-detail-view');
            if (masterView) masterView.classList.add('hidden');
            if (detailView) detailView.classList.remove('hidden');
        }

        function closeLoanDetailBlueprint() {
            closeLoanDetailView();
        }

        function resetLoanFinancialMetricsUI() {
            const metricIds = [
                'ld-metric-principal',
                'ld-metric-unpaid-principal',
                'ld-metric-rem-interest',
                'ld-metric-rem-total',
                'ld-metric-accrued-interest',
                'ld-metric-due-today',
                'ld-metric-overdue',
                'ld-metric-current-payable'
            ];
            metricIds.forEach(id => {
                const el = document.getElementById(id);
                if (!el) return;
                el.classList.remove('privacy-metric');
                el.removeAttribute('data-real-value');
                el.innerText = '—';
            });
        }

                function setLoanFinancialMetric(elementId, centsValue) {
            const el = document.getElementById(elementId);
            if (!el) return;

            if (Number.isSafeInteger(centsValue)) {
                const formatted = NuraFinancialEngine.formatMXNFromCents(centsValue);
                el.classList.add('privacy-metric');
                el.setAttribute('data-real-value', formatted);
                el.innerText = isPrivacyOn ? '••••••••' : formatted;
            } else {
                el.classList.remove('privacy-metric');
                el.removeAttribute('data-real-value');
                el.innerText = 'N/A';
            }
        }

        function formatScheduleDueDateRaw(isoDateStr) {
            if (typeof isoDateStr !== 'string') return 'N/A';
            const trimmed = isoDateStr.trim();
            if (!trimmed) return 'N/A';
            // Preserve exact YYYY-MM-DD contractual date string without Date() timezone conversion
            const match = trimmed.match(/^(\d{4}-\d{2}-\d{2})/);
            return match ? match[1] : escapeHtml(trimmed);
        }

        function renderScheduleMoneyCellHTML(centsValue, extraClasses = 'text-slate-800') {
            if (Number.isSafeInteger(centsValue) && centsValue >= 0) {
                const formatted = NuraFinancialEngine.formatMXNFromCents(centsValue);
                const visibleText = isPrivacyOn ? '••••••••' : formatted;
                return `<td class="py-2.5 px-3 text-right font-mono ${extraClasses}"><span class="privacy-metric" data-real-value="${escapeHtml(formatted)}">${escapeHtml(visibleText)}</span></td>`;
            }
            return `<td class="py-2.5 px-3 text-right font-mono text-slate-400">N/A</td>`;
        }

        function getInstallmentStatusBadgeHTML(inst) {
            const rawStatus = (inst && typeof inst.status === 'string' && inst.status.trim())
                ? inst.status.trim().toUpperCase()
                : 'N/A';

            const styleMap = {
                'SCHEDULED': 'bg-slate-100 text-slate-700 border-slate-200',
                'DUE_TODAY': 'bg-amber-100 text-amber-800 border-amber-200',
                'OVERDUE': 'bg-red-100 text-red-800 border-red-200',
                'PARTIALLY_PAID': 'bg-blue-100 text-blue-800 border-blue-200',
                'PAID': 'bg-emerald-100 text-emerald-800 border-emerald-200'
            };

            const badgeClass = styleMap[rawStatus] || 'bg-slate-100 text-slate-500 border-slate-200';
            let html = `<span class="inline-block text-[10px] font-extrabold px-2 py-0.5 rounded-full border uppercase ${badgeClass}">${escapeHtml(rawStatus)}</span>`;

            const resolvedPaidCents = inst
                ? (Number.isSafeInteger(inst.total_paid_cents) ? inst.total_paid_cents : inst.amount_paid_cents)
                : null;

            // Secondary partial payment indicator without overriding authoritative backend status
            const hasPartialPayment = inst
                && Number.isSafeInteger(resolvedPaidCents) && resolvedPaidCents > 0
                && Number.isSafeInteger(inst.remaining_amount_cents) && inst.remaining_amount_cents > 0;

            if (hasPartialPayment && rawStatus !== 'PARTIALLY_PAID') {
                html += `<div class="mt-1"><span class="inline-block text-[9px] font-bold px-1.5 py-0.2 rounded bg-blue-50 text-blue-700 border border-blue-200 uppercase">PARTIAL PAYMENT</span></div>`;
            }

            if (rawStatus === 'PAID' && inst && typeof inst.paid_at === 'string' && inst.paid_at.trim()) {
                html += `<div class="mt-0.5 text-[9px] text-slate-400 font-mono">${escapeHtml(formatDateTimeShort(inst.paid_at))}</div>`;
            }

            return html;
        }

        function resetLoanScheduleUI() {
            const tbody = document.getElementById('ld-schedule-tbody');
            const countLabel = document.getElementById('ld-schedule-count-label');
            const paidTimestamps = document.getElementById('ld-schedule-paid-timestamps');

            if (tbody) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="8" class="py-8 px-4 text-center text-slate-400">
                            Loading amortization schedule...
                        </td>
                    </tr>`;
            }
            if (countLabel) countLabel.innerText = 'Loading schedule...';
            if (paidTimestamps) paidTimestamps.innerText = '—';
        }

        function setLoanScheduleErrorUI() {
            const tbody = document.getElementById('ld-schedule-tbody');
            const countLabel = document.getElementById('ld-schedule-count-label');
            const paidTimestamps = document.getElementById('ld-schedule-paid-timestamps');

            if (tbody) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="8" class="py-8 px-4 text-center text-red-600 font-medium">
                            Unable to load amortization schedule.
                        </td>
                    </tr>`;
            }
            if (countLabel) countLabel.innerText = 'Schedule unavailable';
            if (paidTimestamps) paidTimestamps.innerText = '—';
        }

        function renderLoanScheduleTable(installmentsInput) {
            const tbody = document.getElementById('ld-schedule-tbody');
            const countLabel = document.getElementById('ld-schedule-count-label');
            const paidTimestamps = document.getElementById('ld-schedule-paid-timestamps');
            if (!tbody) return;

            if (!Array.isArray(installmentsInput)) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="8" class="py-8 px-4 text-center text-red-600 font-medium">
                            Invalid amortization schedule format received from server.
                        </td>
                    </tr>`;
                if (countLabel) countLabel.innerText = 'Schedule unavailable (invalid format)';
                if (paidTimestamps) paidTimestamps.innerText = 'N/A';
                return;
            }

            if (installmentsInput.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="8" class="py-8 px-4 text-center text-slate-500">
                            No amortization installments generated for this loan record.
                        </td>
                    </tr>`;
                if (countLabel) countLabel.innerText = '0 installments recorded';
                if (paidTimestamps) paidTimestamps.innerText = 'No settlement timestamps recorded';
                return;
            }

            let rowsHtml = '';
            let latestPaidAt = null;
            let latestPaidAtTimeMs = -Infinity;
            let firstDueDate = null;
            let lastDueDate = null;

            installmentsInput.forEach((inst, idx) => {
                if (!inst || typeof inst !== 'object') {
                    rowsHtml += `
                        <tr class="hover:bg-slate-50/80 transition-colors">
                            <td class="py-2.5 px-3 font-mono text-slate-400">N/A</td>
                            <td class="py-2.5 px-3 font-mono text-slate-400">N/A</td>
                            <td class="py-2.5 px-3 text-right font-mono text-slate-400">N/A</td>
                            <td class="py-2.5 px-3 text-right font-mono text-slate-400">N/A</td>
                            <td class="py-2.5 px-3 text-right font-mono text-slate-400">N/A</td>
                            <td class="py-2.5 px-3 text-right font-mono text-slate-400">N/A</td>
                            <td class="py-2.5 px-3 text-right font-mono text-slate-400">N/A</td>
                            <td class="py-2.5 px-3 text-center"><span class="text-[10px] font-bold text-slate-400">N/A</span></td>
                        </tr>`;
                    return;
                }

                const numDisplay = Number.isSafeInteger(inst.installment_number) && inst.installment_number > 0
                    ? `#${inst.installment_number}`
                    : 'N/A';
                const dueDateDisplay = formatScheduleDueDateRaw(inst.due_date);

                if (idx === 0 && dueDateDisplay !== 'N/A') firstDueDate = dueDateDisplay;
                if (idx === installmentsInput.length - 1 && dueDateDisplay !== 'N/A') lastDueDate = dueDateDisplay;

                if (typeof inst.paid_at === 'string' && inst.paid_at.trim()) {
                    const candidateStr = inst.paid_at.trim();
                    const parsedTimeMs = Date.parse(candidateStr);
                    if (Number.isFinite(parsedTimeMs) && parsedTimeMs > latestPaidAtTimeMs) {
                        latestPaidAtTimeMs = parsedTimeMs;
                        latestPaidAt = candidateStr;
                    }
                }

                const rawStatus = (typeof inst.status === 'string') ? inst.status.trim().toUpperCase() : '';
                const rowBgClass = rawStatus === 'OVERDUE'
                    ? 'bg-red-50/30 hover:bg-red-50/60'
                    : (rawStatus === 'DUE_TODAY' ? 'bg-amber-50/30 hover:bg-amber-50/60' : 'hover:bg-slate-50/80');

                const remClass = (Number.isSafeInteger(inst.remaining_amount_cents) && inst.remaining_amount_cents > 0)
                    ? (rawStatus === 'OVERDUE' ? 'font-bold text-red-700' : 'font-bold text-slate-900')
                    : 'text-slate-500';

                const resolvedTotalDueCents = Number.isSafeInteger(inst.total_due_cents)
                    ? inst.total_due_cents
                    : inst.scheduled_payment_cents;
                const resolvedTotalPaidCents = Number.isSafeInteger(inst.total_paid_cents)
                    ? inst.total_paid_cents
                    : inst.amount_paid_cents;

                rowsHtml += `
                    <tr class="${rowBgClass} transition-colors">
                        <td class="py-2.5 px-3 font-mono font-bold text-slate-700">${escapeHtml(numDisplay)}</td>
                        <td class="py-2.5 px-3 font-mono font-semibold text-slate-800">${dueDateDisplay}</td>
                        ${renderScheduleMoneyCellHTML(inst.principal_due_cents, 'text-slate-700')}
                        ${renderScheduleMoneyCellHTML(inst.interest_due_cents, 'text-slate-700')}
                        ${renderScheduleMoneyCellHTML(resolvedTotalDueCents, 'font-bold text-slate-900')}
                        ${renderScheduleMoneyCellHTML(resolvedTotalPaidCents, 'font-semibold text-emerald-700')}
                        ${renderScheduleMoneyCellHTML(inst.remaining_amount_cents, remClass)}
                        <td class="py-2.5 px-3 text-center align-middle">${getInstallmentStatusBadgeHTML(inst)}</td>
                    </tr>`;
            });

            tbody.innerHTML = rowsHtml;

            if (countLabel) {
                const rangeText = (firstDueDate && lastDueDate) ? ` (${firstDueDate} → ${lastDueDate})` : '';
                countLabel.innerText = `${installmentsInput.length} contractual installments listed${rangeText}`;
            }
            if (paidTimestamps) {
                paidTimestamps.innerText = latestPaidAt
                    ? `Latest recorded settlement timestamp: ${formatDateTimeShort(latestPaidAt)}`
                    : 'No full installment settlements recorded yet';
            }
        }

        async function openLoanDetailFull(loanId) {
            if (!Number.isSafeInteger(loanId) || loanId <= 0) return;
            const thisReqId = ++loanDetailRequestSeq;

            // Reset financial metrics and amortization schedule immediately to avoid stale data leakage
            resetLoanFinancialMetricsUI();
            resetLoanScheduleUI();

            const errBox = document.getElementById('loan-detail-error');
            if (errBox) errBox.classList.add('hidden');


            try {
                const res = await fetch(`/api/loans/${loanId}`);
                if (thisReqId !== loanDetailRequestSeq) return;

                if (res.status === 401) {
                    mostrarAuthView();
                    return;
                }
                if (res.status === 403) {
                    throw new Error("Insufficient permissions to inspect loan file.");
                }
                if (!res.ok) {
                    throw new Error(`Failed to load loan file (HTTP ${res.status}).`);
                }

                const data = await res.json();
                if (thisReqId !== loanDetailRequestSeq) return;
                if (!data || typeof data !== 'object') {
                    throw new Error("Invalid loan record structure received.");
                }

                currentSelectedLoan = data;

                // 1. Header & Navigation
                const crumbEl = document.getElementById('ld-number-crumb');
                const titleEl = document.getElementById('ld-title');
                const badgeEl = document.getElementById('ld-status-badge');
                const prodBadgeEl = document.getElementById('ld-product-badge');
                const custBtn = document.getElementById('ld-btn-customer');

                if (crumbEl) crumbEl.innerText = data.loan_number || 'LN-00000';
                if (titleEl) titleEl.innerText = `${data.loan_number || 'Loan'} — ${data.product_name || 'Credit File'}`;
                if (badgeEl) {
                    badgeEl.innerText = data.status || 'UNKNOWN';
                    badgeEl.className = `text-[10px] font-extrabold px-2.5 py-0.5 rounded-full border uppercase ${getLoanStatusBadgeStyle(data.status)}`;
                }
                if (prodBadgeEl) prodBadgeEl.innerText = data.product_name || 'Personal Loan';

                if (custBtn) {
                    if (Number.isSafeInteger(data.customer_id) && data.customer_id > 0) {
                        custBtn.onclick = () => {
                            switchTab('customers');
                            openCustomerDetail(data.customer_id);
                        };
                        custBtn.classList.remove('hidden');
                    } else {
                        custBtn.onclick = null;
                        custBtn.classList.add('hidden');
                    }
                }

                // 2. Section 1: Immutable Approved Contractual Terms
                const setVal = (id, val) => {
                    const el = document.getElementById(id);
                    if (el) el.innerText = (val !== null && val !== undefined && String(val).trim()) ? String(val) : 'Not available';
                };

                const prodVer = data.product_version ? ` (${data.product_version})` : '';
                setVal('ld-term-product', `${data.product_name || 'Personal Loan'}${prodVer}`);
                setVal('ld-term-rate', Number.isFinite(data.monthly_interest_rate_percent) ? `${data.monthly_interest_rate_percent.toFixed(2)}% / month` : 'Not available');
                setVal('ld-term-weeks', Number.isSafeInteger(data.term_installments_count) ? `${data.term_installments_count} weekly installments` : 'Not available');
                setVal('ld-term-months', Number.isSafeInteger(data.assumed_interest_months) ? `${data.assumed_interest_months} monthly periods` : 'Not available');
                setVal('ld-term-method', data.interest_calculation_method);
                setVal('ld-term-basis', data.interest_basis);
                setVal('ld-term-accrual', data.interest_accrual_convention);
                setVal('ld-term-rounding', data.rounding_policy);
                setVal('ld-term-start', data.start_date);
                setVal('ld-term-first-payment', data.first_payment_date);
                setVal('ld-term-maturity', data.maturity_date);
                setVal('ld-term-notes', data.notes || 'No contractual notes recorded.');

                // Populate Block C: 8-Metric Financial Accounting Summary (Backend Direct Values)
                setLoanFinancialMetric('ld-metric-principal', data.principal_cents);
                setLoanFinancialMetric('ld-metric-unpaid-principal', data.unpaid_principal_cents);
                setLoanFinancialMetric('ld-metric-rem-interest', data.remaining_contractual_interest_cents);
                setLoanFinancialMetric('ld-metric-rem-total', data.remaining_contract_total_cents);
                setLoanFinancialMetric('ld-metric-accrued-interest', data.accrued_interest_cents);
                setLoanFinancialMetric('ld-metric-due-today', data.due_today_cents);
                setLoanFinancialMetric('ld-metric-overdue', data.overdue_amount_cents);
                setLoanFinancialMetric('ld-metric-current-payable', data.current_payable_amount_cents);

                // Populate Block D: 16-Week Amortization Schedule & Payment Ledger
                renderLoanScheduleTable(data.installments);

                // 3. Section 2: Operational Lifecycle & Audit Traceability

                
                // 3A. Underwriting Approval
                const appStatusEl = document.getElementById('ld-app-status');
                const appDateEl = document.getElementById('ld-app-date');
                const appUserEl = document.getElementById('ld-app-user');
                const appNotesEl = document.getElementById('ld-app-notes');

                if (data.approved_at) {
                    if (appStatusEl) {
                        appStatusEl.innerText = 'APPROVED';
                        appStatusEl.className = 'text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800';
                    }
                    if (appDateEl) appDateEl.innerText = formatDateTimeShort(data.approved_at);
                    if (appUserEl) appUserEl.innerText = data.approved_by_user_id ? `User #${data.approved_by_user_id}` : 'Not available';
                    if (appNotesEl) appNotesEl.innerText = data.approval_notes || 'No approval notes recorded.';
                } else {
                    if (appStatusEl) {
                        appStatusEl.innerText = 'PENDING';
                        appStatusEl.className = 'text-[10px] font-bold px-2 py-0.5 rounded-full bg-slate-200 text-slate-600';
                    }
                    if (appDateEl) appDateEl.innerText = 'Not available';
                    if (appUserEl) appUserEl.innerText = 'Not available';
                    if (appNotesEl) appNotesEl.innerText = 'Approval not yet granted.';
                }

                // 3B. Bank Disbursement & Activation
                const disbStatusEl = document.getElementById('ld-disb-status');
                const disbDateEl = document.getElementById('ld-disb-date');
                const disbMethodEl = document.getElementById('ld-disb-method');
                const disbRefEl = document.getElementById('ld-disb-ref');
                const disbNotesEl = document.getElementById('ld-disb-notes');

                if (data.activated_at || data.disbursed_at) {
                    if (disbStatusEl) {
                        disbStatusEl.innerText = 'ACTIVE / DISBURSED';
                        disbStatusEl.className = 'text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800';
                    }
                    if (disbDateEl) disbDateEl.innerText = formatDateTimeShort(data.disbursed_at || data.activated_at);
                    if (disbMethodEl) disbMethodEl.innerText = data.disbursement_method || 'Not available';
                    if (disbRefEl) disbRefEl.innerText = data.disbursement_reference || 'Not available';
                    if (disbNotesEl) disbNotesEl.innerText = data.disbursement_notes || 'No disbursement notes recorded.';
                } else {
                    if (disbStatusEl) {
                        disbStatusEl.innerText = 'PENDING';
                        disbStatusEl.className = 'text-[10px] font-bold px-2 py-0.5 rounded-full bg-slate-200 text-slate-600';
                    }
                    if (disbDateEl) disbDateEl.innerText = 'Not available';
                    if (disbMethodEl) disbMethodEl.innerText = 'Not available';
                    if (disbRefEl) disbRefEl.innerText = 'Not available';
                    if (disbNotesEl) disbNotesEl.innerText = 'Disbursement not yet executed.';
                }

                // 3C. Cancellation
                const cancBox = document.getElementById('ld-canc-container');
                const cancDateEl = document.getElementById('ld-canc-date');
                const cancReasonEl = document.getElementById('ld-canc-reason');

                if (data.status === 'CANCELLED' || data.cancelled_at) {
                    if (cancBox) cancBox.classList.remove('hidden');
                    if (cancDateEl) cancDateEl.innerText = formatDateTimeShort(data.cancelled_at);
                    if (cancReasonEl) cancReasonEl.innerText = data.cancellation_reason || 'Not specified';
                } else {
                    if (cancBox) cancBox.classList.add('hidden');
                }

                // 3D. Defaulted (Cartera Vencida) Audit
                const defBox = document.getElementById('ld-default-container');
                const defDateEl = document.getElementById('ld-default-date');
                const defUserEl = document.getElementById('ld-default-user');
                const defReasonEl = document.getElementById('ld-default-reason');

                if (data.status === 'DEFAULTED') {
                    if (defBox) defBox.classList.remove('hidden');
                    if (defDateEl) defDateEl.innerText = formatDateTimeShort(data.defaulted_at || data.updated_at);
                    if (defUserEl) defUserEl.innerText = data.defaulted_by_user_id ? `User #${data.defaulted_by_user_id}` : 'Authorized Underwriter';
                    if (defReasonEl) defReasonEl.innerText = data.default_reason || 'See customer interactions log.';
                } else {
                    if (defBox) defBox.classList.add('hidden');
                }

                // Update Block F lifecycle action buttons based on status and RBAC
                if (typeof updateLoanLifecycleActionsUI === 'function') {
                    updateLoanLifecycleActionsUI(data);
                }

                // Display detail view
                openLoanDetailBlueprint();
                window.scrollTo({ top: 0, behavior: 'smooth' });
            } catch (err) {
                if (thisReqId !== loanDetailRequestSeq) return;
                setLoanScheduleErrorUI();
                if (errBox) {
                    errBox.innerText = `Error: ${err.message}`;
                    errBox.classList.remove('hidden');
                    openLoanDetailBlueprint();
                } else {
                    alert(err.message);
                }
            }
        }


        async function openLoanDetail(loanId) {
            if (!Number.isSafeInteger(loanId) || loanId <= 0) return;
            await openLoanDetailFull(loanId);
        }

        /* =========================================================================
           4C. STAGE 4 (BLOCK E): LOAN ORIGINATION CONTROLLER
           ========================================================================= */
        let selectedOriginationCustomer = null;
        let loanCustomerSearchTimer = null;
        let loanCustomerSearchSeq = 0;
        let currentOriginationIdempotencyKey = null;
        let isSubmittingLoanOrigination = false;

        function getLocalTodayISO() {
            const now = new Date();
            const year = now.getFullYear();
            const month = String(now.getMonth() + 1).padStart(2, '0');
            const day = String(now.getDate()).padStart(2, '0');
            return `${year}-${month}-${day}`;
        }

        function generateFreshLoanIdempotencyKey() {
            const bytes = new Uint8Array(12);
            if (window.crypto && typeof window.crypto.getRandomValues === 'function') {
                window.crypto.getRandomValues(bytes);
            } else {
                for (let i = 0; i < bytes.length; i++) {
                    bytes[i] = Math.floor(Math.random() * 256);
                }
            }
            const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('').toUpperCase();
            return `LO-${Date.now().toString(36).toUpperCase()}-${hex}`.slice(0, 64);
        }

        function invalidateLoanOriginationIdempotencyKey() {
            currentOriginationIdempotencyKey = null;
        }

        async function openLoanOriginationModal(preselectedCustomerId = null) {
            const errBox = document.getElementById('loan-origination-error');
            if (errBox) errBox.classList.add('hidden');

            const form = document.getElementById('loan-origination-form');
            if (form) form.reset();

            currentOriginationIdempotencyKey = null;
            isSubmittingLoanOrigination = false;

            const submitBtn = document.getElementById('lo-submit-btn');
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerText = 'Create Draft Loan →';
            }

            const todayISO = getLocalTodayISO();
            const firstPayISO = NuraFinancialEngine.addDaysISO(todayISO, 7);

            const startEl = document.getElementById('lo-start-date');
            const firstEl = document.getElementById('lo-first-payment-date');
            if (startEl) {
                startEl.min = todayISO;
                startEl.value = todayISO;
            }
            if (firstEl) {
                firstEl.min = todayISO;
                firstEl.value = firstPayISO;
            }

            document.getElementById('lo-product').value = 'Personal Loan';
            document.getElementById('lo-principal').value = '50000';
            document.getElementById('lo-rate').value = '10';
            document.getElementById('lo-term-weeks').value = '16';
            document.getElementById('lo-interest-months').value = '4';
            document.getElementById('lo-notes').value = '';
            document.getElementById('lo-confirm-check').checked = false;

            clearLoanOriginationCustomer(false);
            updateLoanOriginationPreview();

            document.getElementById('loan-origination-modal')?.classList.remove('hidden');

            if (Number.isSafeInteger(preselectedCustomerId) && preselectedCustomerId > 0) {
                if (currentSelectedCustomer && currentSelectedCustomer.id === preselectedCustomerId) {
                    selectLoanOriginationCustomer(currentSelectedCustomer);
                    if (currentSelectedCustomer.status !== 'ACTIVE') {
                        await searchLoanOriginationCustomers('');
                    }
                } else {
                    try {
                        const res = await fetch(`/api/customers/${preselectedCustomerId}`);
                        if (res.status === 401) {
                            closeLoanOriginationModal();
                            mostrarAuthView();
                            return;
                        }
                        if (res.ok) {
                            const cust = await res.json();
                            selectLoanOriginationCustomer(cust);
                            if (cust.status !== 'ACTIVE') {
                                await searchLoanOriginationCustomers('');
                            }
                        } else {
                            await searchLoanOriginationCustomers('');
                        }
                    } catch (_) {
                        await searchLoanOriginationCustomers('');
                    }
                }
            } else {
                await searchLoanOriginationCustomers('');
            }
        }

        function closeLoanOriginationModal() {
            document.getElementById('loan-origination-modal')?.classList.add('hidden');
        }

        function debounceLoanCustomerSearch() {
            clearTimeout(loanCustomerSearchTimer);
            loanCustomerSearchTimer = setTimeout(() => searchLoanOriginationCustomers(), 260);
        }

        async function searchLoanOriginationCustomers(overrideSearch = null) {
            const thisSeq = ++loanCustomerSearchSeq;
            const searchInput = document.getElementById('lo-customer-search');
            const query = (overrideSearch !== null ? overrideSearch : (searchInput?.value || '')).trim();
            const resultsBox = document.getElementById('lo-customer-results');
            if (!resultsBox) return;

            resultsBox.innerHTML = '<p class="py-4 px-3 text-center text-slate-400 text-xs">Searching active customers in PostgreSQL...</p>';

            const params = new URLSearchParams({
                page: '1',
                page_size: '25',
                status: 'ACTIVE',
                sort: 'CREATED_DESC'
            });
            if (query) params.set('search', query);

            try {
                const res = await fetch(`/api/customers?${params.toString()}`);
                if (thisSeq !== loanCustomerSearchSeq) return;
                if (res.status === 401) {
                    closeLoanOriginationModal();
                    mostrarAuthView();
                    return;
                }
                if (res.status === 403) {
                    throw new Error('Insufficient permissions to query active customers.');
                }
                if (!res.ok) throw new Error(`Could not load active customers (HTTP ${res.status}).`);

                const data = await res.json();
                if (thisSeq !== loanCustomerSearchSeq) return;

                const items = (Array.isArray(data.items) ? data.items : []).filter(c => c && c.status === 'ACTIVE');
                resultsBox.innerHTML = '';

                if (items.length === 0) {
                    resultsBox.innerHTML = '<p class="py-4 px-3 text-center text-slate-500 text-xs">No ACTIVE customers found matching your search.</p>';
                    return;
                }

                items.forEach(c => {
                    const row = document.createElement('div');
                    row.className = 'p-2.5 px-3 hover:bg-blue-50/70 transition cursor-pointer flex items-center justify-between gap-2';

                    row.innerHTML = `
                        <div class="min-w-0">
                            <div class="flex items-center gap-1.5 flex-wrap">
                                <span class="font-mono font-bold text-blue-700 text-[11px]">${escapeHtml(c.customer_number)}</span>
                                <span class="font-bold text-slate-900 text-xs truncate">${escapeHtml(c.full_legal_name)}</span>
                                <span class="text-[9px] font-bold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">ACTIVE</span>
                            </div>
                            <p class="text-[10px] text-slate-500 mt-0.5">${escapeHtml(c.entity_type)} • Phone: ${escapeHtml(c.phone_primary)} • Active Loans: ${Number.isSafeInteger(c.active_loans_count) ? c.active_loans_count : 0}</p>
                        </div>
                        <button type="button" class="py-1.5 px-3 rounded-lg text-[11px] font-bold shrink-0 bg-blue-600 text-white hover:bg-blue-700">
                            Select
                        </button>
                    `;

                    row.onclick = () => selectLoanOriginationCustomer(c);
                    resultsBox.appendChild(row);
                });
            } catch (err) {
                if (thisSeq !== loanCustomerSearchSeq) return;
                resultsBox.innerHTML = `<p class="py-4 px-3 text-center text-red-600 text-xs">${escapeHtml(err.message)}</p>`;
            }
        }

        function selectLoanOriginationCustomer(customerObj) {
            const errBox = document.getElementById('loan-origination-error');
            if (!customerObj || typeof customerObj !== 'object') return;

            invalidateLoanOriginationIdempotencyKey();

            if (customerObj.status !== 'ACTIVE') {
                if (errBox) {
                    errBox.innerText = `Cannot originate a loan for customer ${customerObj.customer_number || ''} (${customerObj.full_legal_name || ''}) because their status is ${customerObj.status}. Only ACTIVE customers are eligible.`;
                    errBox.classList.remove('hidden');
                }
                return;
            }

            if (errBox) errBox.classList.add('hidden');
            selectedOriginationCustomer = customerObj;

            document.getElementById('lo-sel-cust-number').innerText = customerObj.customer_number || `CUST-#${customerObj.id}`;
            document.getElementById('lo-sel-cust-entity').innerText = customerObj.entity_type || 'INDIVIDUAL';
            document.getElementById('lo-sel-cust-status').innerText = customerObj.status || 'ACTIVE';
            document.getElementById('lo-sel-cust-kyc').innerText = `KYC: ${customerObj.verification_status || 'PENDING_VERIFICATION'}`;
            document.getElementById('lo-sel-cust-name').innerText = customerObj.full_legal_name || 'Selected Borrower';

            const activeCount = Number.isSafeInteger(customerObj.active_loans_count) ? customerObj.active_loans_count : 0;
            const unpaidBal = Number.isFinite(customerObj.current_outstanding_balance_cents)
                ? NuraFinancialEngine.formatMXNFromCents(customerObj.current_outstanding_balance_cents)
                : '$0.00 MXN';
            document.getElementById('lo-sel-cust-meta').innerText =
                `Phone: ${customerObj.phone_primary || 'N/A'} • Active Loans: ${activeCount} • Current Unpaid Principal: ${unpaidBal}`;

            document.getElementById('lo-customer-picker-box')?.classList.add('hidden');
            document.getElementById('lo-selected-customer-card')?.classList.remove('hidden');

            const prodSelect = document.getElementById('lo-product');
            if (prodSelect && customerObj.entity_type === 'BUSINESS') {
                prodSelect.value = 'Business Loan';
            }
            onLoanOriginationParamChange();
        }

        function clearLoanOriginationCustomer(triggerSearch = true) {
            selectedOriginationCustomer = null;
            invalidateLoanOriginationIdempotencyKey();
            document.getElementById('lo-selected-customer-card')?.classList.add('hidden');
            document.getElementById('lo-customer-picker-box')?.classList.remove('hidden');
            if (triggerSearch) {
                searchLoanOriginationCustomers();
            }
        }

        let isOriginationScheduleExpanded = false;

        function toggleLoanOriginationScheduleExpand() {
            isOriginationScheduleExpanded = !isOriginationScheduleExpanded;
            updateLoanOriginationPreview();
        }

        function syncOriginationPresetHighlight(weeks, months) {
            const presets = [
                [4, 1], [8, 2], [12, 3], [16, 4], [26, 6], [52, 12]
            ];
            presets.forEach(([w, m]) => {
                const btn = document.getElementById(`lo-preset-${w}-${m}`);
                if (!btn) return;
                if (weeks === w && months === m) {
                    btn.className = 'lo-preset-btn py-1.5 px-2 rounded-lg border border-blue-400 bg-blue-50 text-[10px] font-extrabold text-blue-700 transition shadow-sm';
                } else {
                    btn.className = 'lo-preset-btn py-1.5 px-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-100 text-[10px] font-bold text-slate-700 transition';
                }
            });
        }

        function applyLoanOriginationTermPreset(weeks, months) {
            const weeksEl = document.getElementById('lo-term-weeks');
            const monthsEl = document.getElementById('lo-interest-months');
            if (weeksEl) weeksEl.value = String(weeks);
            if (monthsEl) monthsEl.value = String(months);
            onLoanOriginationParamChange();
        }

        function handleLoanOriginationStartDateChange() {
            const startEl = document.getElementById('lo-start-date');
            const firstEl = document.getElementById('lo-first-payment-date');
            if (startEl && startEl.value && firstEl) {
                firstEl.min = startEl.value;
                if (!firstEl.value || firstEl.value < startEl.value) {
                    firstEl.value = NuraFinancialEngine.addDaysISO(startEl.value, 7);
                }
            }
            onLoanOriginationParamChange();
        }

        function onLoanOriginationParamChange() {
            invalidateLoanOriginationIdempotencyKey();
            updateLoanOriginationPreview();
        }

        function renderOriginationScheduleRowHtml(row) {
            return `
                <tr class="hover:bg-slate-50/90 transition-colors">
                    <td class="py-2 px-3.5 font-bold text-slate-700">#${row.installmentNumber}</td>
                    <td class="py-2 px-3.5 text-slate-700">${escapeHtml(row.dueDate)}</td>
                    <td class="py-2 px-3.5 text-right text-slate-700">${NuraFinancialEngine.formatMXNFromCents(row.principalComponentCents)}</td>
                    <td class="py-2 px-3.5 text-right text-amber-700">${NuraFinancialEngine.formatMXNFromCents(row.interestComponentCents)}</td>
                    <td class="py-2 px-3.5 text-right font-bold text-slate-900">${NuraFinancialEngine.formatMXNFromCents(row.scheduledPaymentCents)}</td>
                </tr>`;
        }

        function updateLoanOriginationPreview() {
            const principalRaw = Number(document.getElementById('lo-principal')?.value);
            const rateRaw = Number(document.getElementById('lo-rate')?.value);
            const weeksRaw = Number(document.getElementById('lo-term-weeks')?.value);
            const monthsRaw = Number(document.getElementById('lo-interest-months')?.value);
            const startDateRaw = document.getElementById('lo-start-date')?.value || '';
            const firstDateRaw = document.getElementById('lo-first-payment-date')?.value || '';
            const tbody = document.getElementById('lo-preview-schedule-tbody');
            const toggleBtn = document.getElementById('lo-schedule-toggle-btn');

            syncOriginationPresetHighlight(weeksRaw, monthsRaw);

            const isPrincipalValid = Number.isFinite(principalRaw) && principalRaw > 0 && principalRaw <= 10000000;
            const isRateValid = Number.isFinite(rateRaw) && rateRaw >= 0 && rateRaw <= 100;
            const isWeeksValid = Number.isSafeInteger(weeksRaw) && weeksRaw >= 1 && weeksRaw <= 104;
            const isMonthsValid = Number.isSafeInteger(monthsRaw) && monthsRaw >= 1 && monthsRaw <= 36;
            const isDatesValid = /^\d{4}-\d{2}-\d{2}$/.test(firstDateRaw) && (!startDateRaw || firstDateRaw >= startDateRaw);

            if (!isPrincipalValid || !isRateValid || !isWeeksValid || !isMonthsValid || !isDatesValid) {
                document.getElementById('lo-prev-weekly-badge').innerText = 'Invalid Parameters';
                document.getElementById('lo-prev-principal').innerText = '—';
                document.getElementById('lo-prev-monthly-interest').innerText = '—';
                document.getElementById('lo-prev-total-interest').innerText = '—';
                document.getElementById('lo-prev-total-repayment').innerText = '—';
                document.getElementById('lo-prev-final-payment').innerText = '—';
                document.getElementById('lo-prev-maturity').innerText = '—';
                document.getElementById('lo-prev-table-title').innerText = 'Schedule Preview Unavailable (Check Input Ranges)';
                if (toggleBtn) toggleBtn.classList.add('hidden');
                if (tbody) {
                    tbody.innerHTML = `
                        <tr>
                            <td colspan="5" class="py-6 px-3 text-center text-amber-700 font-sans text-xs">
                                Enter valid parameters (Principal: $0.01–$10,000,000 MXN, Rate: 0%–100%, Term: 1–104 weeks, Months: 1–36, First Payment &ge; Start Date) to preview schedule.
                            </td>
                        </tr>`;
                }
                return;
            }

            const preview = NuraFinancialEngine.calculateFlatSchedule({
                principalPesos: principalRaw,
                monthlyRatePercent: rateRaw,
                assumedInterestMonths: monthsRaw,
                termWeeks: weeksRaw,
                firstPaymentDateISO: firstDateRaw
            });

            document.getElementById('lo-prev-weekly-badge').innerText =
                `${NuraFinancialEngine.formatMXNFromCents(preview.regularPaymentCents)} / wk`;
            document.getElementById('lo-prev-principal').innerText =
                NuraFinancialEngine.formatMXNFromCents(preview.principalCents);
            document.getElementById('lo-prev-monthly-interest').innerText =
                NuraFinancialEngine.formatMXNFromCents(preview.monthlyInterestCents);
            document.getElementById('lo-prev-total-interest').innerText =
                NuraFinancialEngine.formatMXNFromCents(preview.totalInterestCents);
            document.getElementById('lo-prev-total-repayment').innerText =
                NuraFinancialEngine.formatMXNFromCents(preview.totalRepaymentCents);
            document.getElementById('lo-prev-final-payment').innerText =
                NuraFinancialEngine.formatMXNFromCents(preview.finalPaymentCents);
            document.getElementById('lo-prev-maturity').innerText =
                preview.estimatedPayoffDate || '—';
            document.getElementById('lo-prev-table-title').innerText =
                `Projected Installments (${preview.installmentsCount} Weeks / ${preview.monthsCount} Interest Months)`;

            if (!tbody) return;

            const totalRows = preview.schedule.length;
            const canSummarize = totalRows > 5;

            if (toggleBtn) {
                if (canSummarize) {
                    toggleBtn.classList.remove('hidden');
                    toggleBtn.innerText = isOriginationScheduleExpanded
                        ? `Show Summary View (First 3 & Final) ↑`
                        : `Show All ${totalRows} Installments ↓`;
                } else {
                    toggleBtn.classList.add('hidden');
                }
            }

            let rowsHtml = '';
            if (!canSummarize || isOriginationScheduleExpanded) {
                preview.schedule.forEach(row => {
                    rowsHtml += renderOriginationScheduleRowHtml(row);
                });
            } else {
                const firstThree = preview.schedule.slice(0, 3);
                const finalRow = preview.schedule[totalRows - 1];
                const hiddenCount = totalRows - 4;

                firstThree.forEach(row => {
                    rowsHtml += renderOriginationScheduleRowHtml(row);
                });

                rowsHtml += `
                    <tr onclick="toggleLoanOriginationScheduleExpand()" class="bg-slate-50/90 hover:bg-blue-50/60 cursor-pointer transition-colors">
                        <td colspan="5" class="py-2 px-3.5 text-center font-sans text-[11px] font-semibold text-blue-600">
                            ••• ${hiddenCount} intermediate weekly installments (#4 to #${totalRows - 1}) hidden in summary view — Click to expand all ${totalRows} installments •••
                        </td>
                    </tr>`;

                rowsHtml += renderOriginationScheduleRowHtml(finalRow);
            }

            tbody.innerHTML = rowsHtml;
        }

        async function handleLoanOriginationSubmit(e) {
            e.preventDefault();
            if (isSubmittingLoanOrigination) return;

            const errBox = document.getElementById('loan-origination-error');
            const submitBtn = document.getElementById('lo-submit-btn');
            if (errBox) errBox.classList.add('hidden');

            const showValidationError = (msg) => {
                if (errBox) {
                    errBox.innerText = msg;
                    errBox.classList.remove('hidden');
                }
            };

            if (!selectedOriginationCustomer || !Number.isSafeInteger(selectedOriginationCustomer.id)) {
                showValidationError('Please select an ACTIVE customer before originating a loan.');
                return;
            }
            if (selectedOriginationCustomer.status !== 'ACTIVE') {
                showValidationError(`Customer ${selectedOriginationCustomer.customer_number} is not ACTIVE and cannot originate a loan.`);
                return;
            }

            const productName = document.getElementById('lo-product')?.value || '';
            if (!['Personal Loan', 'Business Loan'].includes(productName)) {
                showValidationError('Please select a valid credit product.');
                return;
            }

            const principalPesos = Number(document.getElementById('lo-principal')?.value);
            if (!Number.isFinite(principalPesos) || principalPesos <= 0 || principalPesos > 10000000) {
                showValidationError('Principal amount must be greater than $0.00 MXN and cannot exceed $10,000,000.00 MXN.');
                return;
            }
            const principalCents = Math.round(principalPesos * 100);
            if (!Number.isSafeInteger(principalCents) || principalCents <= 0 || principalCents > 1000000000) {
                showValidationError('Invalid principal amount in cents.');
                return;
            }

            const monthlyRatePercent = Number(document.getElementById('lo-rate')?.value);
            if (!Number.isFinite(monthlyRatePercent) || monthlyRatePercent < 0 || monthlyRatePercent > 100) {
                showValidationError('Monthly flat interest rate must be between 0% and 100%.');
                return;
            }

            const termWeeks = Number(document.getElementById('lo-term-weeks')?.value);
            if (!Number.isSafeInteger(termWeeks) || termWeeks < 1 || termWeeks > 104) {
                showValidationError('Loan term must be an integer between 1 and 104 weekly installments.');
                return;
            }

            const assumedMonths = Number(document.getElementById('lo-interest-months')?.value);
            if (!Number.isSafeInteger(assumedMonths) || assumedMonths < 1 || assumedMonths > 36) {
                showValidationError('Assumed interest months must be an integer between 1 and 36.');
                return;
            }

            const startDate = document.getElementById('lo-start-date')?.value || '';
            const firstPaymentDate = document.getElementById('lo-first-payment-date')?.value || '';
            const todayISO = getLocalTodayISO();

            if (!/^\d{4}-\d{2}-\d{2}$/.test(startDate) || !/^\d{4}-\d{2}-\d{2}$/.test(firstPaymentDate)) {
                showValidationError('Please provide valid Contract Start Date and First Payment Due Date (YYYY-MM-DD).');
                return;
            }
            if (startDate < todayISO) {
                showValidationError(`Contract Start Date (${startDate}) cannot be earlier than today (${todayISO}).`);
                return;
            }
            if (firstPaymentDate < startDate) {
                showValidationError('First Payment Due Date cannot be earlier than the Contract Start Date.');
                return;
            }

            const confirmChecked = document.getElementById('lo-confirm-check')?.checked;
            if (!confirmChecked) {
                showValidationError('Please check the confirmation box to verify the contractual terms.');
                return;
            }

            const notesVal = document.getElementById('lo-notes')?.value.trim() || null;

            if (!currentOriginationIdempotencyKey) {
                currentOriginationIdempotencyKey = generateFreshLoanIdempotencyKey();
            }

            const payload = {
                idempotency_key: currentOriginationIdempotencyKey,
                customer_id: selectedOriginationCustomer.id,
                source_lead_id: null,
                product_name: productName,
                principal_cents: principalCents,
                monthly_interest_rate_percent: monthlyRatePercent,
                assumed_interest_months: assumedMonths,
                term_installments_count: termWeeks,
                start_date: startDate,
                first_payment_date: firstPaymentDate,
                notes: notesVal
            };

            isSubmittingLoanOrigination = true;
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerText = 'Originating Draft...';
            }

            try {
                const res = await fetch('/api/loans', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': getCookie('csrf_token')
                    },
                    body: JSON.stringify(payload)
                });

                if (res.status === 401) {
                    closeLoanOriginationModal();
                    mostrarAuthView();
                    return;
                }

                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    const detailMsg = typeof data.detail === 'string'
                        ? data.detail
                        : (Array.isArray(data.detail) ? data.detail.map(d => d.msg).join('; ') : null);
                    throw new Error(detailMsg || `Loan origination failed (HTTP ${res.status}).`);
                }

                const createdLoan = data.loan || data;
                currentOriginationIdempotencyKey = null;
                closeLoanOriginationModal();

                await Promise.all([
                    loadLoans(1),
                    loadDashboardSummary(),
                    loadCustomers(currentCustomersPage)
                ]);

                switchTab('loans');
                if (createdLoan && Number.isSafeInteger(createdLoan.id)) {
                    await openLoanDetailFull(createdLoan.id);
                }
            } catch (err) {
                showValidationError(err.message);
            } finally {
                isSubmittingLoanOrigination = false;
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerText = 'Create Draft Loan →';
                }
            }
        }

        /* =========================================================================
           4D. STAGE 4 (BLOCK F): LOAN APPROVAL, DISBURSEMENT & CANCELLATION CONTROLLER
           ========================================================================= */
        let isSubmittingLoanLifecycle = false;
        const loanCustomerLabelCache = {};

        function canUserApproveOrCancelLoan() {
            const role = currentUser?.role || '';
            return ['OWNER_ADMIN', 'CREDIT_ADVISOR'].includes(role);
        }

        function canUserActivateLoan() {
            const role = currentUser?.role || '';
            return ['OWNER_ADMIN', 'CREDIT_ADVISOR', 'OPERATIONS_OFFICER'].includes(role);
        }

        function showLoanDetailSuccessBanner(message) {
            const box = document.getElementById('loan-detail-success');
            const textEl = document.getElementById('loan-detail-success-text');
            if (textEl) textEl.innerText = message || 'Loan updated successfully.';
            if (box) box.classList.remove('hidden');
        }

        function hideLoanDetailSuccessBanner() {
            const box = document.getElementById('loan-detail-success');
            if (box) box.classList.add('hidden');
        }

        let currentPaymentIdempotencyKey = null;
        let currentSelectedPaymentForReversal = null;
        let loanPaymentsRequestSeq = 0;

        function onLoanPaymentParamChange() {
            currentPaymentIdempotencyKey = null;
        }

        function updateLoanLifecycleActionsUI(loan) {
            const container = document.getElementById('ld-lifecycle-actions');
            const btnApprove = document.getElementById('ld-btn-approve');
            const btnActivate = document.getElementById('ld-btn-activate');
            const btnCancel = document.getElementById('ld-btn-cancel');
            const btnPayment = document.getElementById('ld-btn-payment');
            if (!container || !btnApprove || !btnActivate || !btnCancel) return;

            btnApprove.classList.add('hidden');
            btnActivate.classList.add('hidden');
            btnCancel.classList.add('hidden');
            if (btnPayment) btnPayment.classList.add('hidden');
            container.classList.add('hidden');

            if (!loan || typeof loan !== 'object') return;
            const status = String(loan.status || '').toUpperCase();

            let anyVisible = false;
            if (status === 'DRAFT') {
                if (canUserApproveOrCancelLoan()) {
                    btnApprove.classList.remove('hidden');
                    btnCancel.classList.remove('hidden');
                    anyVisible = true;
                }
            } else if (status === 'APPROVED') {
                if (canUserActivateLoan()) {
                    btnActivate.classList.remove('hidden');
                    anyVisible = true;
                }
                if (canUserApproveOrCancelLoan()) {
                    btnCancel.classList.remove('hidden');
                    anyVisible = true;
                }
            } else if (status === 'ACTIVE') {
                if (btnPayment && canUserActivateLoan()) {
                    btnPayment.classList.remove('hidden');
                    anyVisible = true;
                }
            }

            if (anyVisible) {
                container.classList.remove('hidden');
            }

            const paymentsBox = document.getElementById('loan-payments-container');
            if (paymentsBox) {
                if (['ACTIVE', 'PAID_OFF', 'DEFAULTED'].includes(status)) {
                    paymentsBox.classList.remove('hidden');
                    loadLoanPayments(loan.id);
                } else {
                    paymentsBox.classList.add('hidden');
                }
            }
        }

        async function loadLoanPayments(loanId) {
            if (!Number.isSafeInteger(loanId) || loanId <= 0) return;
            const thisSeq = ++loanPaymentsRequestSeq;
            const tbody = document.getElementById('ld-payments-tbody');
            const countBadge = document.getElementById('ld-payments-count-badge');
            if (!tbody) return;

            tbody.innerHTML = '<tr><td colspan="7" class="py-6 px-4 text-center text-slate-400">Loading payment ledger...</td></tr>';

            try {
                const res = await fetch(`/api/loans/${loanId}/payments`);
                if (thisSeq !== loanPaymentsRequestSeq) return;
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const payments = await res.json();
                if (thisSeq !== loanPaymentsRequestSeq) return;

                const list = Array.isArray(payments) ? payments : [];
                const postedCount = list.filter(p => p && p.status === 'POSTED').length;
                if (countBadge) {
                    countBadge.innerText = `${postedCount} Posted / ${list.length} Total`;
                }

                if (list.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="7" class="py-6 px-4 text-center text-slate-400">No payments recorded for this loan yet.</td></tr>';
                    return;
                }

                let rowsHtml = '';
                list.forEach(p => {
                    const isPosted = p.status === 'POSTED';
                    const badgeClass = isPosted
                        ? 'bg-emerald-100 text-emerald-800 border-emerald-200'
                        : 'bg-slate-200 text-slate-600 border-slate-300 line-through';

                    const allocs = Array.isArray(p.allocations) ? p.allocations : [];
                    const allocSummary = allocs.map(a => `#${a.installment_number} (${NuraFinancialEngine.formatMXNFromCents(a.total_allocated_cents)})`).join(', ');

                    const canReverse = isPosted && p.is_top_reversible === true && currentUser?.role === 'OWNER_ADMIN';
                    const actionHtml = canReverse
                        ? `<button type="button" data-pay-id="${p.id}" class="btn-reverse-payment py-1.5 px-3 rounded-lg bg-red-50 hover:bg-red-100 text-red-700 border border-red-200 font-bold text-[11px] transition">↩ Reverse</button>`
                        : (isPosted ? '<span class="text-[10px] text-slate-400 font-medium">Locked (LIFO)</span>' : '<span class="text-[10px] text-slate-400">Reversed</span>');

                    const reversalNoteHtml = (!isPosted && p.reversal_reason)
                        ? `<p class="text-[10px] text-red-600 mt-0.5">Reversed: ${escapeHtml(p.reversal_reason)} (${escapeHtml(formatDateTimeShort(p.reversed_at))})</p>`
                        : '';

                    rowsHtml += `
                        <tr class="${isPosted ? 'hover:bg-slate-50/80' : 'bg-slate-50/60 opacity-75'} transition-colors">
                            <td class="py-2.5 px-3">
                                <p class="font-mono font-bold text-slate-900">${escapeHtml(p.payment_number)}</p>
                                ${allocSummary ? `<p class="text-[10px] text-slate-500 mt-0.5">Inst: ${escapeHtml(allocSummary)}</p>` : ''}
                                ${p.notes ? `<p class="text-[10px] text-slate-500 italic mt-0.5">${escapeHtml(p.notes)}</p>` : ''}
                                ${reversalNoteHtml}
                            </td>
                            <td class="py-2.5 px-3">
                                <p class="font-semibold text-slate-800">${escapeHtml(formatDateTimeShort(p.paid_at))}</p>
                                <p class="text-[10px] font-mono text-slate-500">${escapeHtml(p.payment_method)}${p.payment_reference ? ` • Ref: ${escapeHtml(p.payment_reference)}` : ''}</p>
                            </td>
                            <td class="py-2.5 px-3 text-right font-mono text-amber-800">${NuraFinancialEngine.formatMXNFromCents(p.interest_applied_cents)}</td>
                            <td class="py-2.5 px-3 text-right font-mono text-blue-900">${NuraFinancialEngine.formatMXNFromCents(p.principal_applied_cents)}</td>
                            <td class="py-2.5 px-3 text-right font-mono font-extrabold text-emerald-800">${NuraFinancialEngine.formatMXNFromCents(p.amount_cents)}</td>
                            <td class="py-2.5 px-3 text-center">
                                <span class="text-[10px] font-extrabold px-2 py-0.5 rounded-full border uppercase ${badgeClass}">${escapeHtml(p.status)}</span>
                            </td>
                            <td class="py-2.5 px-3 text-right">${actionHtml}</td>
                        </tr>`;
                });

                tbody.innerHTML = rowsHtml;

                tbody.querySelectorAll('.btn-reverse-payment').forEach(btn => {
                    const pid = Number(btn.getAttribute('data-pay-id'));
                    const payObj = list.find(x => x.id === pid);
                    if (payObj) {
                        btn.onclick = () => openLoanReversalModal(payObj);
                    }
                });
            } catch (err) {
                if (thisSeq !== loanPaymentsRequestSeq) return;
                tbody.innerHTML = `<tr><td colspan="7" class="py-6 px-4 text-center text-red-600">Unable to load payment history (${escapeHtml(err.message)}).</td></tr>`;
            }
        }

        async function openLoanPaymentModal() {
            if (!currentSelectedLoan || currentSelectedLoan.status !== 'ACTIVE') return;
            if (!canUserActivateLoan()) return;

            const l = currentSelectedLoan;
            currentPaymentIdempotencyKey = null;

            const errBox = document.getElementById('loan-payment-error');
            if (errBox) errBox.classList.add('hidden');

            const form = document.getElementById('loan-payment-form');
            if (form) form.reset();

            const submitBtn = document.getElementById('lpay-submit-btn');
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerText = 'Post Payment →';
            }

            document.getElementById('lpay-loan-header').innerText = `${l.loan_number || 'Loan'} • ${l.product_name || 'Personal Loan'}`;
            document.getElementById('lpay-customer-badge').innerText = `Customer #${l.customer_id}`;

            const payableCents = Number.isSafeInteger(l.current_payable_amount_cents) ? l.current_payable_amount_cents : 0;
            const remTotalCents = Number.isSafeInteger(l.remaining_contract_total_cents) ? l.remaining_contract_total_cents : 0;
            const unpaidInsts = (Array.isArray(l.installments) ? l.installments : []).filter(i => i && i.remaining_amount_cents > 0);
            const nextInstCents = unpaidInsts.length > 0 ? unpaidInsts[0].remaining_amount_cents : (l.regular_installment_cents || 0);

            document.getElementById('lpay-current-payable').innerText = NuraFinancialEngine.formatMXNFromCents(payableCents);
            document.getElementById('lpay-next-installment').innerText = NuraFinancialEngine.formatMXNFromCents(nextInstCents);
            document.getElementById('lpay-rem-total').innerText = NuraFinancialEngine.formatMXNFromCents(remTotalCents);

            const defaultCents = payableCents > 0 ? payableCents : nextInstCents;
            document.getElementById('lpay-amount').value = (defaultCents / 100).toFixed(2);
            document.getElementById('lpay-method').value = 'SPEI_TRANSFER';
            document.getElementById('lpay-date').value = '';
            document.getElementById('lpay-reference').value = '';
            document.getElementById('lpay-notes').value = '';
            document.getElementById('lpay-confirm-check').checked = false;

            // Populate Early Payoff Quote Card (Point 1B)
            const earlyBox = document.getElementById('lpay-early-payoff-box');
            const earlyCheck = document.getElementById('lpay-early-rebate-check');
            if (earlyCheck) earlyCheck.checked = false;

            const isEs = (NuraI18n.currentLang === 'es');
            const rebateCents = Number.isSafeInteger(l.early_payoff_rebate_cents) ? l.early_payoff_rebate_cents : 0;
            const earlyAmountCents = Number.isSafeInteger(l.early_payoff_amount_cents) ? l.early_payoff_amount_cents : remTotalCents;
            const canAuthorizeRebate = ['OWNER_ADMIN', 'CREDIT_ADVISOR'].includes(currentUser?.role || '');

            if (earlyBox) {
                if (rebateCents > 0 && earlyAmountCents > 0 && canAuthorizeRebate) {
                    document.getElementById('lpay-early-title').innerText = isEs
                        ? '⚡ Liquidación Anticipada Hoy (Capital + Interés Devengado)'
                        : '⚡ Early Payoff Quote Today (Principal + Accrued Interest)';
                    document.getElementById('lpay-early-sub').innerHTML = isEs
                        ? `Condona intereses futuros no devengados (<span id="lpay-early-rebate" class="font-mono font-bold">-${escapeHtml(NuraFinancialEngine.formatMXNFromCents(rebateCents))}</span>)`
                        : `Forgives unearned future interest (<span id="lpay-early-rebate" class="font-mono font-bold">-${escapeHtml(NuraFinancialEngine.formatMXNFromCents(rebateCents))}</span>)`;
                    document.getElementById('lpay-early-amount').innerText = NuraFinancialEngine.formatMXNFromCents(earlyAmountCents);
                    document.getElementById('lpay-early-btn').innerText = isEs ? '⚡ Usar Liquidación Anticipada' : '⚡ Use Early Payoff';
                    document.getElementById('lpay-early-check-label').innerText = isEs
                        ? 'Autorizar condonación de intereses futuros no devengados y liquidar préstamo (PAID_OFF)'
                        : 'Authorize forgiveness of unearned future interest and settle loan as PAID_OFF';
                    earlyBox.classList.remove('hidden');
                } else {
                    earlyBox.classList.add('hidden');
                }
            }

            document.getElementById('loan-payment-modal')?.classList.remove('hidden');

            const resolvedCust = await resolveLoanCustomerDisplay(l);
            const badgeEl = document.getElementById('lpay-customer-badge');
            if (badgeEl) badgeEl.innerText = resolvedCust;
        }

        function handleEarlyRebateToggle(isChecked) {
            if (!currentSelectedLoan) return;
            const l = currentSelectedLoan;
            if (isChecked && Number.isSafeInteger(l.early_payoff_amount_cents) && l.early_payoff_amount_cents > 0) {
                document.getElementById('lpay-amount').value = (l.early_payoff_amount_cents / 100).toFixed(2);
            }
            onLoanPaymentParamChange();
        }

        function setPaymentQuickAmount(mode) {
            if (!currentSelectedLoan) return;
            const l = currentSelectedLoan;
            const earlyCheck = document.getElementById('lpay-early-rebate-check');
            let targetCents = 0;
            if (mode === 'payable') {
                targetCents = l.current_payable_amount_cents || 0;
                if (earlyCheck) earlyCheck.checked = false;
            } else if (mode === 'installment') {
                const unpaidInsts = (Array.isArray(l.installments) ? l.installments : []).filter(i => i && i.remaining_amount_cents > 0);
                targetCents = unpaidInsts.length > 0 ? unpaidInsts[0].remaining_amount_cents : (l.regular_installment_cents || 0);
                if (earlyCheck) earlyCheck.checked = false;
            } else if (mode === 'payoff') {
                targetCents = l.remaining_contract_total_cents || 0;
                if (earlyCheck) earlyCheck.checked = false;
            } else if (mode === 'early_payoff') {
                targetCents = l.early_payoff_amount_cents || 0;
                if (earlyCheck) earlyCheck.checked = true;
            }
            if (targetCents > 0) {
                document.getElementById('lpay-amount').value = (targetCents / 100).toFixed(2);
                onLoanPaymentParamChange();
            }
        }

        function closeLoanPaymentModal() {
            if (isSubmittingLoanLifecycle) return;
            document.getElementById('loan-payment-modal')?.classList.add('hidden');
        }

        async function handleLoanPaymentSubmit(e) {
            e.preventDefault();
            if (isSubmittingLoanLifecycle || !currentSelectedLoan) return;

            const loanId = currentSelectedLoan.id;
            const loanNumber = currentSelectedLoan.loan_number || `#${loanId}`;
            const errBox = document.getElementById('loan-payment-error');
            const submitBtn = document.getElementById('lpay-submit-btn');
            if (errBox) errBox.classList.add('hidden');

            const showError = (msg) => {
                if (errBox) {
                    errBox.innerText = msg;
                    errBox.classList.remove('hidden');
                }
            };

            const amountPesos = Number(document.getElementById('lpay-amount')?.value);
            if (!Number.isFinite(amountPesos) || amountPesos <= 0) {
                showError('Payment amount must be greater than $0.00 MXN.');
                return;
            }
            const amountCents = Math.round(amountPesos * 100);
            if (amountCents <= 0 || amountCents > (currentSelectedLoan.remaining_contract_total_cents || 0)) {
                showError(`Payment cannot exceed the remaining contract total (${NuraFinancialEngine.formatMXNFromCents(currentSelectedLoan.remaining_contract_total_cents)}).`);
                return;
            }

            const method = document.getElementById('lpay-method')?.value || '';
            const reference = (document.getElementById('lpay-reference')?.value || '').trim() || null;
            const paidAtRaw = (document.getElementById('lpay-date')?.value || '').trim() || null;
            const notesVal = (document.getElementById('lpay-notes')?.value || '').trim() || null;

            if (!document.getElementById('lpay-confirm-check')?.checked) {
                showError('Please check the confirmation box before posting this payment.');
                return;
            }

            if (!currentPaymentIdempotencyKey) {
                currentPaymentIdempotencyKey = generateFreshLoanIdempotencyKey();
            }

            const applyEarlyRebate = Boolean(document.getElementById('lpay-early-rebate-check')?.checked);
            if (applyEarlyRebate && Number.isSafeInteger(currentSelectedLoan.early_payoff_amount_cents)) {
                if (amountCents !== currentSelectedLoan.early_payoff_amount_cents) {
                    const isEs = (NuraI18n.currentLang === 'es');
                    showError(
                        isEs
                            ? `Para aplicar la condonación por liquidación anticipada, el monto debe ser exactamente ${NuraFinancialEngine.formatMXNFromCents(currentSelectedLoan.early_payoff_amount_cents)}.`
                            : `To apply the early payoff interest rebate, the payment amount must be exactly ${NuraFinancialEngine.formatMXNFromCents(currentSelectedLoan.early_payoff_amount_cents)}.`
                    );
                    return;
                }
            }

            const payload = {
                idempotency_key: currentPaymentIdempotencyKey,
                amount_cents: amountCents,
                payment_method: method,
                payment_reference: reference,
                paid_at: paidAtRaw,
                notes: notesVal,
                apply_early_payoff_rebate: applyEarlyRebate
            };

            isSubmittingLoanLifecycle = true;
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerText = 'Posting Payment...';
            }

            try {
                const res = await fetch(`/api/loans/${loanId}/payments`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': getCookie('csrf_token')
                    },
                    body: JSON.stringify(payload)
                });

                if (res.status === 401) {
                    isSubmittingLoanLifecycle = false;
                    document.getElementById('loan-payment-modal')?.classList.add('hidden');
                    mostrarAuthView();
                    return;
                }

                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    const detailMsg = typeof data.detail === 'string'
                        ? data.detail
                        : (Array.isArray(data.detail) ? data.detail.map(d => d.msg).join('; ') : null);
                    throw new Error(detailMsg || `Failed to record payment (HTTP ${res.status}).`);
                }

                currentPaymentIdempotencyKey = null;
                isSubmittingLoanLifecycle = false;
                document.getElementById('loan-payment-modal')?.classList.add('hidden');

                const pNum = data.payment?.payment_number || 'Receipt';
                await applyUpdatedLoanFromLifecycleAction(
                    loanId,
                    `✓ Payment ${pNum} (${NuraFinancialEngine.formatMXNFromCents(amountCents)}) posted to ${loanNumber}.`
                );
            } catch (err) {
                showError(err.message);
            } finally {
                isSubmittingLoanLifecycle = false;
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerText = 'Post Payment →';
                }
            }
        }

        function openLoanReversalModal(paymentObj) {
            if (!currentSelectedLoan || !paymentObj || paymentObj.status !== 'POSTED') return;
            if (currentUser?.role !== 'OWNER_ADMIN') return;

            currentSelectedPaymentForReversal = paymentObj;
            const errBox = document.getElementById('loan-reversal-error');
            if (errBox) errBox.classList.add('hidden');

            const form = document.getElementById('loan-reversal-form');
            if (form) form.reset();

            const submitBtn = document.getElementById('lrev-submit-btn');
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerText = 'Confirm Reversal';
            }

            document.getElementById('lrev-number').innerText = paymentObj.payment_number || `Payment #${paymentObj.id}`;
            document.getElementById('lrev-amount').innerText = NuraFinancialEngine.formatMXNFromCents(paymentObj.amount_cents);
            document.getElementById('lrev-meta').innerText = `Method: ${paymentObj.payment_method || '—'} • Paid at: ${formatDateTimeShort(paymentObj.paid_at)}`;
            document.getElementById('lrev-reason').value = '';
            document.getElementById('lrev-confirm-check').checked = false;

            document.getElementById('loan-reversal-modal')?.classList.remove('hidden');
        }

        function closeLoanReversalModal() {
            if (isSubmittingLoanLifecycle) return;
            document.getElementById('loan-reversal-modal')?.classList.add('hidden');
        }

        async function handleLoanReversalSubmit(e) {
            e.preventDefault();
            if (isSubmittingLoanLifecycle || !currentSelectedLoan || !currentSelectedPaymentForReversal) return;

            const loanId = currentSelectedLoan.id;
            const paymentId = currentSelectedPaymentForReversal.id;
            const payNumber = currentSelectedPaymentForReversal.payment_number || `#${paymentId}`;
            const errBox = document.getElementById('loan-reversal-error');
            const submitBtn = document.getElementById('lrev-submit-btn');
            if (errBox) errBox.classList.add('hidden');

            const showError = (msg) => {
                if (errBox) {
                    errBox.innerText = msg;
                    errBox.classList.remove('hidden');
                }
            };

            const reason = (document.getElementById('lrev-reason')?.value || '').trim();
            if (reason.length < 3 || reason.length > 255) {
                showError('Reversal reason must be between 3 and 255 characters.');
                return;
            }

            if (!document.getElementById('lrev-confirm-check')?.checked) {
                showError('Please check the confirmation box to authorize this reversal.');
                return;
            }

            isSubmittingLoanLifecycle = true;
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerText = 'Reversing Payment...';
            }

            try {
                const res = await fetch(`/api/loans/${loanId}/payments/${paymentId}/reverse`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': getCookie('csrf_token')
                    },
                    body: JSON.stringify({ reversal_reason: reason })
                });

                if (res.status === 401) {
                    isSubmittingLoanLifecycle = false;
                    document.getElementById('loan-reversal-modal')?.classList.add('hidden');
                    mostrarAuthView();
                    return;
                }

                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    const detailMsg = typeof data.detail === 'string'
                        ? data.detail
                        : (Array.isArray(data.detail) ? data.detail.map(d => d.msg).join('; ') : null);
                    throw new Error(detailMsg || `Failed to reverse payment (HTTP ${res.status}).`);
                }

                isSubmittingLoanLifecycle = false;
                document.getElementById('loan-reversal-modal')?.classList.add('hidden');
                await applyUpdatedLoanFromLifecycleAction(
                    loanId,
                    `✓ Payment ${payNumber} has been reversed and prior balances restored.`
                );
            } catch (err) {
                showError(err.message);
            } finally {
                isSubmittingLoanLifecycle = false;
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerText = 'Confirm Reversal';
                }
            }
        }

        async function resolveLoanCustomerDisplay(loan) {
            if (!loan || !Number.isSafeInteger(loan.customer_id)) return 'Customer #N/A';
            const cid = loan.customer_id;

            if (currentSelectedCustomer && currentSelectedCustomer.id === cid) {
                const label = `${currentSelectedCustomer.customer_number} • ${currentSelectedCustomer.full_legal_name}`;
                loanCustomerLabelCache[cid] = label;
                return label;
            }
            if (loanCustomerLabelCache[cid]) {
                return loanCustomerLabelCache[cid];
            }
            try {
                const res = await fetch(`/api/customers/${cid}`);
                if (res.ok) {
                    const c = await res.json();
                    if (c && c.customer_number && c.full_legal_name) {
                        const label = `${c.customer_number} • ${c.full_legal_name}`;
                        loanCustomerLabelCache[cid] = label;
                        return label;
                    }
                }
            } catch (_) {
                // Non-blocking fallback to numeric ID
            }
            return `Customer #${cid}`;
        }

        async function applyUpdatedLoanFromLifecycleAction(loanId, successMessage) {
            await Promise.all([
                openLoanDetailFull(loanId),
                loadLoans(currentLoansPage),
                loadDashboardSummary(),
                loadCustomers(currentCustomersPage)
            ]);
            showLoanDetailSuccessBanner(successMessage);
        }

        // --- 1. APPROVE LOAN MODAL & SUBMIT (DRAFT -> APPROVED) ---
        async function openLoanApproveModal() {
            if (!currentSelectedLoan || currentSelectedLoan.status !== 'DRAFT') return;
            if (!canUserApproveOrCancelLoan()) return;

            const l = currentSelectedLoan;
            const errBox = document.getElementById('loan-approve-error');
            if (errBox) errBox.classList.add('hidden');

            const form = document.getElementById('loan-approve-form');
            if (form) form.reset();

            const submitBtn = document.getElementById('la-submit-btn');
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerText = 'Confirm Approval →';
            }

            document.getElementById('la-loan-header').innerText = `${l.loan_number || 'Loan'} • ${l.product_name || 'Personal Loan'}`;
            document.getElementById('la-customer-badge').innerText = `Customer #${l.customer_id}`;
            document.getElementById('la-principal').innerText = Number.isSafeInteger(l.principal_cents)
                ? NuraFinancialEngine.formatMXNFromCents(l.principal_cents)
                : 'N/A';
            document.getElementById('la-rate').innerText = Number.isFinite(l.monthly_interest_rate_percent)
                ? `${l.monthly_interest_rate_percent.toFixed(2)}% / mo`
                : 'N/A';
            document.getElementById('la-term').innerText = `${l.term_installments_count || 16} wks (${l.assumed_interest_months || 4} mo)`;
            document.getElementById('la-total-interest').innerText = Number.isSafeInteger(l.total_interest_cents)
                ? NuraFinancialEngine.formatMXNFromCents(l.total_interest_cents)
                : 'N/A';
            document.getElementById('la-total-repayment').innerText = Number.isSafeInteger(l.total_repayment_cents)
                ? NuraFinancialEngine.formatMXNFromCents(l.total_repayment_cents)
                : 'N/A';
            document.getElementById('la-weekly-payment').innerText = Number.isSafeInteger(l.regular_installment_cents)
                ? NuraFinancialEngine.formatMXNFromCents(l.regular_installment_cents)
                : 'N/A';
            document.getElementById('la-start-date').innerText = l.start_date || '—';
            document.getElementById('la-first-payment').innerText = l.first_payment_date || '—';
            document.getElementById('la-maturity-date').innerText = l.maturity_date || '—';

            document.getElementById('loan-approve-modal')?.classList.remove('hidden');

            const resolvedCust = await resolveLoanCustomerDisplay(l);
            const badgeEl = document.getElementById('la-customer-badge');
            if (badgeEl) badgeEl.innerText = resolvedCust;
        }

        function closeLoanApproveModal() {
            if (isSubmittingLoanLifecycle) return;
            document.getElementById('loan-approve-modal')?.classList.add('hidden');
        }

        async function handleLoanApproveSubmit(e) {
            e.preventDefault();
            if (isSubmittingLoanLifecycle || !currentSelectedLoan) return;

            const loanId = currentSelectedLoan.id;
            const loanNumber = currentSelectedLoan.loan_number || `#${loanId}`;
            const errBox = document.getElementById('loan-approve-error');
            const submitBtn = document.getElementById('la-submit-btn');
            if (errBox) errBox.classList.add('hidden');

            const showError = (msg) => {
                if (errBox) {
                    errBox.innerText = msg;
                    errBox.classList.remove('hidden');
                }
            };

            if (!document.getElementById('la-confirm-check')?.checked) {
                showError('Please check the confirmation box before approving this loan.');
                return;
            }

            const notesVal = document.getElementById('la-approval-notes')?.value.trim() || null;

            isSubmittingLoanLifecycle = true;
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerText = 'Approving Loan...';
            }

            try {
                const res = await fetch(`/api/loans/${loanId}/approve`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': getCookie('csrf_token')
                    },
                    body: JSON.stringify({ approval_notes: notesVal })
                });

                if (res.status === 401) {
                    isSubmittingLoanLifecycle = false;
                    document.getElementById('loan-approve-modal')?.classList.add('hidden');
                    mostrarAuthView();
                    return;
                }

                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    const detailMsg = typeof data.detail === 'string'
                        ? data.detail
                        : (Array.isArray(data.detail) ? data.detail.map(d => d.msg).join('; ') : null);
                    throw new Error(detailMsg || `Failed to approve loan (HTTP ${res.status}).`);
                }

                isSubmittingLoanLifecycle = false;
                document.getElementById('loan-approve-modal')?.classList.add('hidden');
                await applyUpdatedLoanFromLifecycleAction(
                    loanId,
                    `✓ Loan ${loanNumber} has been approved and is now ready for disbursement.`
                );
            } catch (err) {
                showError(err.message);
            } finally {
                isSubmittingLoanLifecycle = false;
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerText = 'Confirm Approval →';
                }
            }
        }

        // --- 2. ACTIVATE & DISBURSE LOAN MODAL & SUBMIT (APPROVED -> ACTIVE) ---
        async function openLoanActivateModal() {
            if (!currentSelectedLoan || currentSelectedLoan.status !== 'APPROVED') return;
            if (!canUserActivateLoan()) return;

            const l = currentSelectedLoan;
            const errBox = document.getElementById('loan-activate-error');
            if (errBox) errBox.classList.add('hidden');

            const form = document.getElementById('loan-activate-form');
            if (form) form.reset();

            const submitBtn = document.getElementById('ldisb-submit-btn');
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerText = 'Activate & Record Disbursement →';
            }

            document.getElementById('ldisb-loan-header').innerText = `${l.loan_number || 'Loan'} • ${l.product_name || 'Personal Loan'}`;
            document.getElementById('ldisb-customer-badge').innerText = `Customer #${l.customer_id}`;
            document.getElementById('ldisb-principal').innerText = Number.isSafeInteger(l.principal_cents)
                ? NuraFinancialEngine.formatMXNFromCents(l.principal_cents)
                : 'N/A';
            document.getElementById('ldisb-rate').innerText = Number.isFinite(l.monthly_interest_rate_percent)
                ? `${l.monthly_interest_rate_percent.toFixed(2)}% / mo`
                : 'N/A';
            document.getElementById('ldisb-term').innerText = `${l.term_installments_count || 16} weeks`;
            document.getElementById('ldisb-total-repayment').innerText = Number.isSafeInteger(l.total_repayment_cents)
                ? NuraFinancialEngine.formatMXNFromCents(l.total_repayment_cents)
                : 'N/A';

            document.getElementById('ldisb-method').value = 'SPEI_TRANSFER';
            document.getElementById('ldisb-date').value = '';
            document.getElementById('ldisb-reference').value = '';
            document.getElementById('ldisb-notes').value = '';
            document.getElementById('ldisb-confirm-check').checked = false;

            document.getElementById('loan-activate-modal')?.classList.remove('hidden');

            const resolvedCust = await resolveLoanCustomerDisplay(l);
            const badgeEl = document.getElementById('ldisb-customer-badge');
            if (badgeEl) badgeEl.innerText = resolvedCust;
        }

        function closeLoanActivateModal() {
            if (isSubmittingLoanLifecycle) return;
            document.getElementById('loan-activate-modal')?.classList.add('hidden');
        }

        async function handleLoanActivateSubmit(e) {
            e.preventDefault();
            if (isSubmittingLoanLifecycle || !currentSelectedLoan) return;

            const loanId = currentSelectedLoan.id;
            const loanNumber = currentSelectedLoan.loan_number || `#${loanId}`;
            const errBox = document.getElementById('loan-activate-error');
            const submitBtn = document.getElementById('ldisb-submit-btn');
            if (errBox) errBox.classList.add('hidden');

            const showError = (msg) => {
                if (errBox) {
                    errBox.innerText = msg;
                    errBox.classList.remove('hidden');
                }
            };

            const method = document.getElementById('ldisb-method')?.value || '';
            if (!['SPEI_TRANSFER', 'BANK_DEPOSIT', 'CHECK', 'CASH_RECEIPT'].includes(method)) {
                showError('Please select a valid disbursement method.');
                return;
            }

            const reference = (document.getElementById('ldisb-reference')?.value || '').trim();
            if (reference.length < 3 || reference.length > 120) {
                showError('Disbursement reference must be between 3 and 120 characters.');
                return;
            }

            const disbursedAtRaw = (document.getElementById('ldisb-date')?.value || '').trim();
            if (disbursedAtRaw) {
                const parsedMs = Date.parse(disbursedAtRaw);
                if (!Number.isFinite(parsedMs)) {
                    showError('Invalid disbursement date/time format.');
                    return;
                }
                if (parsedMs > Date.now() + 5 * 60 * 1000) {
                    showError('Disbursement time cannot be in the future.');
                    return;
                }
            }

            const confirmDisbursement = Boolean(document.getElementById('ldisb-confirm-check')?.checked);
            if (!confirmDisbursement) {
                showError('Disbursement confirmation is required to activate a loan.');
                return;
            }

            const notesVal = (document.getElementById('ldisb-notes')?.value || '').trim() || null;

            const payload = {
                confirm_disbursement: true,
                disbursement_method: method,
                disbursement_reference: reference,
                disbursed_at: disbursedAtRaw || null,
                disbursement_notes: notesVal
            };

            isSubmittingLoanLifecycle = true;
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerText = 'Activating Loan...';
            }

            try {
                const res = await fetch(`/api/loans/${loanId}/activate`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': getCookie('csrf_token')
                    },
                    body: JSON.stringify(payload)
                });

                if (res.status === 401) {
                    isSubmittingLoanLifecycle = false;
                    document.getElementById('loan-activate-modal')?.classList.add('hidden');
                    mostrarAuthView();
                    return;
                }

                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    const detailMsg = typeof data.detail === 'string'
                        ? data.detail
                        : (Array.isArray(data.detail) ? data.detail.map(d => d.msg).join('; ') : null);
                    throw new Error(detailMsg || `Failed to activate loan (HTTP ${res.status}).`);
                }

                isSubmittingLoanLifecycle = false;
                document.getElementById('loan-activate-modal')?.classList.add('hidden');
                await applyUpdatedLoanFromLifecycleAction(
                    loanId,
                    `✓ Disbursement recorded (${method} • Ref: ${reference}). Loan ${loanNumber} is now ACTIVE.`
                );
            } catch (err) {
                showError(err.message);
            } finally {
                isSubmittingLoanLifecycle = false;
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerText = 'Activate & Record Disbursement →';
                }
            }
        }

        // --- 3. CANCEL LOAN MODAL & SUBMIT (DRAFT / APPROVED -> CANCELLED) ---
        async function openLoanCancelModal() {
            if (!currentSelectedLoan || !['DRAFT', 'APPROVED'].includes(currentSelectedLoan.status)) return;
            if (!canUserApproveOrCancelLoan()) return;

            const l = currentSelectedLoan;
            const errBox = document.getElementById('loan-cancel-error');
            if (errBox) errBox.classList.add('hidden');

            const form = document.getElementById('loan-cancel-form');
            if (form) form.reset();

            const submitBtn = document.getElementById('lcanc-submit-btn');
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerText = 'Confirm Cancellation';
            }

            document.getElementById('lcanc-loan-header').innerText = `${l.loan_number || 'Loan'} • ${l.product_name || 'Personal Loan'}`;
            document.getElementById('lcanc-status-badge').innerText = l.status || 'DRAFT';
            document.getElementById('lcanc-customer-label').innerText = `Customer #${l.customer_id}`;
            document.getElementById('lcanc-principal').innerText = Number.isSafeInteger(l.principal_cents)
                ? NuraFinancialEngine.formatMXNFromCents(l.principal_cents)
                : 'N/A';

            document.getElementById('lcanc-reason').value = '';
            document.getElementById('lcanc-confirm-check').checked = false;

            document.getElementById('loan-cancel-modal')?.classList.remove('hidden');

            const resolvedCust = await resolveLoanCustomerDisplay(l);
            const custEl = document.getElementById('lcanc-customer-label');
            if (custEl) custEl.innerText = resolvedCust;
        }

        function closeLoanCancelModal() {
            if (isSubmittingLoanLifecycle) return;
            document.getElementById('loan-cancel-modal')?.classList.add('hidden');
        }

        async function handleLoanCancelSubmit(e) {
            e.preventDefault();
            if (isSubmittingLoanLifecycle || !currentSelectedLoan) return;

            const loanId = currentSelectedLoan.id;
            const loanNumber = currentSelectedLoan.loan_number || `#${loanId}`;
            const errBox = document.getElementById('loan-cancel-error');
            const submitBtn = document.getElementById('lcanc-submit-btn');
            if (errBox) errBox.classList.add('hidden');

            const showError = (msg) => {
                if (errBox) {
                    errBox.innerText = msg;
                    errBox.classList.remove('hidden');
                }
            };

            const reason = (document.getElementById('lcanc-reason')?.value || '').trim();
            if (reason.length < 3 || reason.length > 255) {
                showError('Cancellation reason must be between 3 and 255 characters.');
                return;
            }

            if (!document.getElementById('lcanc-confirm-check')?.checked) {
                showError('Please check the confirmation box to cancel this loan.');
                return;
            }

            isSubmittingLoanLifecycle = true;
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerText = 'Cancelling Loan...';
            }

            try {
                const res = await fetch(`/api/loans/${loanId}/cancel`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': getCookie('csrf_token')
                    },
                    body: JSON.stringify({ cancellation_reason: reason })
                });

                if (res.status === 401) {
                    isSubmittingLoanLifecycle = false;
                    document.getElementById('loan-cancel-modal')?.classList.add('hidden');
                    mostrarAuthView();
                    return;
                }

                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    const detailMsg = typeof data.detail === 'string'
                        ? data.detail
                        : (Array.isArray(data.detail) ? data.detail.map(d => d.msg).join('; ') : null);
                    throw new Error(detailMsg || `Failed to cancel loan (HTTP ${res.status}).`);
                }

                isSubmittingLoanLifecycle = false;
                document.getElementById('loan-cancel-modal')?.classList.add('hidden');
                await applyUpdatedLoanFromLifecycleAction(
                    loanId,
                    `Loan ${loanNumber} has been marked as CANCELLED.`
                );
            } catch (err) {
                showError(err.message);
            } finally {
                isSubmittingLoanLifecycle = false;
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerText = 'Confirm Cancellation';
                }
            }
        }

        function openLoanDetailBlueprint() {
            document.getElementById('loans-master-view').classList.add('hidden');
            document.getElementById('loan-detail-view').classList.remove('hidden');
        }

        function closeLoanDetailBlueprint() {
            document.getElementById('loan-detail-view').classList.add('hidden');
            document.getElementById('loans-master-view').classList.remove('hidden');
        }

        /* =========================================================================
           6. LOAN SIMULATOR CONTROLLER & PRINT
           ========================================================================= */
        function generateSimulationId() {
            const rnd = Math.floor(1000 + Math.random() * 9000);
            const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');
            return `SIM-${today}-${rnd}`;
        }

        function initializeSimulatorDates() {
            const todayISO = new Date().toISOString().split('T')[0];
            const nextWeekISO = NuraFinancialEngine.addDaysISO(todayISO, 7);
            const startEl = document.getElementById('sim-start-date');
            const firstEl = document.getElementById('sim-first-payment-date');
            if (startEl) startEl.value = todayISO;
            if (firstEl) firstEl.value = nextWeekISO;
            currentSimulationId = generateSimulationId();
        }

        function handleStartDateChange() {
            const startVal = document.getElementById('sim-start-date').value;
            if (startVal) {
                document.getElementById('sim-first-payment-date').value = NuraFinancialEngine.addDaysISO(startVal, 7);
            }
            runLoanSimulation();
        }

        function renderComparisonScenariosTable() {
            const card = document.getElementById('sim-comparison-card');
            const tbody = document.getElementById('sim-comparison-tbody');
            if (!card || !tbody) return;
            if (!savedComparisonScenarios || savedComparisonScenarios.length === 0) {
                card.classList.add('hidden');
                tbody.innerHTML = '';
                return;
            }
            card.classList.remove('hidden');
            tbody.innerHTML = '';
            const isEs = (NuraI18n.currentLang === 'es');

            savedComparisonScenarios.forEach(s => {
                const tr = document.createElement('tr');
                const termLabel = isEs
                    ? `${s.installmentsCount} sem / ${s.monthsCount} mes`
                    : `${s.installmentsCount} wks / ${s.monthsCount} mo`;
                tr.innerHTML = `
                    <td class="py-2 px-3 font-bold text-slate-900">${escapeHtml(s.simulationId)} (${escapeHtml(tEnum(s.product))})</td>
                    <td class="py-2 px-3 text-right font-mono">${NuraFinancialEngine.formatMXNFromCents(s.principalCents)}</td>
                    <td class="py-2 px-3 text-right font-mono">${s.monthlyRatePercent.toFixed(2)}%</td>
                    <td class="py-2 px-3 text-center">${escapeHtml(termLabel)}</td>
                    <td class="py-2 px-3 text-right font-mono font-bold text-blue-700">${NuraFinancialEngine.formatMXNFromCents(s.regularPaymentCents)}</td>
                    <td class="py-2 px-3 text-right font-mono">${NuraFinancialEngine.formatMXNFromCents(s.totalInterestCents)}</td>
                    <td class="py-2 px-3 text-right font-mono font-bold">${NuraFinancialEngine.formatMXNFromCents(s.totalRepaymentCents)}</td>
                `;
                tbody.appendChild(tr);
            });
        }

        function runLoanSimulation() {
            const product = document.getElementById('sim-product')?.value || 'Personal Loan';
            const principalPesos = parseFloat(document.getElementById('sim-principal')?.value || '50000') || 0;
            const monthlyRatePercent = parseFloat(document.getElementById('sim-monthly-rate')?.value || '10') || 0;
            const termWeeks = parseInt(document.getElementById('sim-term-weeks')?.value || '16', 10) || 16;
            const assumedInterestMonths = parseInt(document.getElementById('sim-interest-months')?.value || '4', 10) || 4;
            const firstPaymentDateISO = document.getElementById('sim-first-payment-date')?.value || new Date().toISOString().split('T')[0];
            const methodVal = document.getElementById('sim-interest-method')?.value || 'FLAT_ORIGINAL_PRINCIPAL';

            const res = NuraFinancialEngine.calculateFlatSchedule({
                principalPesos,
                monthlyRatePercent,
                assumedInterestMonths,
                termWeeks,
                firstPaymentDateISO,
                interestCalculationMethod: methodVal
            });
            res.product = product;
            res.simulationId = currentSimulationId;
            currentSimulationResult = res;

            const isEs = (NuraI18n.currentLang === 'es');
            const perMoSuffix = isEs ? '/mes' : '/mo';
            const subtitleKey = (res.interestCalculationMethod === 'DECLINING_BALANCE')
                ? 'sim.sum.subtitle_declining'
                : 'sim.sum.subtitle_flat';

            document.getElementById('sim-badge-id').innerText = currentSimulationId;
            document.getElementById('print-sim-id').innerText = currentSimulationId;
            document.getElementById('print-sim-date').innerText = new Date().toLocaleString(NuraI18n.getLocale());
            document.getElementById('sim-summary-subtitle').innerText = t(subtitleKey, { product: tEnum(product) });
            document.getElementById('sim-regular-payment-pill').innerText = t('sim.sum.per_week', {
                amount: NuraFinancialEngine.formatMXNFromCents(res.regularPaymentCents)
            });

            document.getElementById('sum-principal').innerText = NuraFinancialEngine.formatMXNFromCents(res.principalCents);
            document.getElementById('sum-rate').innerText = `${res.monthlyRatePercent.toFixed(2)}% (${NuraFinancialEngine.formatMXNFromCents(res.monthlyInterestCents)}${perMoSuffix})`;
            document.getElementById('sum-months-count').innerText = String(res.monthsCount);
            document.getElementById('sum-total-interest').innerText = NuraFinancialEngine.formatMXNFromCents(res.totalInterestCents);
            document.getElementById('sum-total-repayment').innerText = NuraFinancialEngine.formatMXNFromCents(res.totalRepaymentCents);
            document.getElementById('sum-regular-payment').innerText = NuraFinancialEngine.formatMXNFromCents(res.regularPaymentCents);
            document.getElementById('sum-inst-count').innerText = String(res.installmentsCount);
            document.getElementById('sum-final-payment').innerText = NuraFinancialEngine.formatMXNFromCents(res.finalPaymentCents);
            document.getElementById('sum-term-freq').innerText = t('sim.sum.weekly_inst_fmt', { count: res.installmentsCount });
            document.getElementById('sum-first-date').innerText = res.firstPaymentDateISO;
            document.getElementById('sum-payoff-date').innerText = res.estimatedPayoffDate;
            document.getElementById('disc-months').innerText = String(res.monthsCount);
            document.getElementById('disc-weeks').innerText = String(res.installmentsCount);

            const tbody = document.getElementById('sim-schedule-tbody');
            const tfoot = document.getElementById('sim-schedule-tfoot');
            tbody.innerHTML = '';

            let sumPrincipal = 0;
            let sumInterest = 0;
            let sumPayment = 0;

            res.schedule.forEach(row => {
                sumPrincipal += row.principalComponentCents;
                sumInterest += row.interestComponentCents;
                sumPayment += row.scheduledPaymentCents;

                const tr = document.createElement('tr');
                tr.className = 'hover:bg-slate-50/80';
                tr.innerHTML = `
                    <td class="py-2.5 px-3 font-bold text-slate-900">#${row.installmentNumber}</td>
                    <td class="py-2.5 px-3 text-slate-600 font-mono">${row.dueDate}</td>
                    <td class="py-2.5 px-3 text-right font-mono">${NuraFinancialEngine.formatMXNFromCents(row.beginningPrincipalCents)}</td>
                    <td class="py-2.5 px-3 text-right font-mono text-slate-800">${NuraFinancialEngine.formatMXNFromCents(row.principalComponentCents)}</td>
                    <td class="py-2.5 px-3 text-right font-mono text-amber-700">${NuraFinancialEngine.formatMXNFromCents(row.interestComponentCents)}</td>
                    <td class="py-2.5 px-3 text-right font-mono font-bold text-slate-900">${NuraFinancialEngine.formatMXNFromCents(row.scheduledPaymentCents)}</td>
                    <td class="py-2.5 px-3 text-right font-mono text-slate-600">${NuraFinancialEngine.formatMXNFromCents(row.endingPrincipalCents)}</td>
                `;
                tbody.appendChild(tr);
            });

            tfoot.innerHTML = `
                <tr>
                    <td colspan="3" class="py-3 px-3 text-right uppercase tracking-wider text-[10px]">${escapeHtml(t('sim.table.tfoot_total', { count: res.installmentsCount }))}</td>
                    <td class="py-3 px-3 text-right font-mono">${NuraFinancialEngine.formatMXNFromCents(sumPrincipal)}</td>
                    <td class="py-3 px-3 text-right font-mono text-amber-300">${NuraFinancialEngine.formatMXNFromCents(sumInterest)}</td>
                    <td class="py-3 px-3 text-right font-mono text-emerald-300">${NuraFinancialEngine.formatMXNFromCents(sumPayment)}</td>
                    <td class="py-3 px-3 text-right font-mono">$0.00 MXN</td>
                </tr>
            `;

            renderComparisonScenariosTable();
        }

        function resetLoanSimulation() {
            document.getElementById('sim-product').value = 'Personal Loan';
            document.getElementById('sim-principal').value = '50000';
            document.getElementById('sim-monthly-rate').value = '10';
            document.getElementById('sim-term-weeks').value = '16';
            document.getElementById('sim-interest-months').value = '4';
            initializeSimulatorDates();
            runLoanSimulation();
        }

        function saveScenarioForComparison() {
            if (!currentSimulationResult) return;
            const snapshot = { ...currentSimulationResult, simulationId: generateSimulationId() };
            savedComparisonScenarios.unshift(snapshot);
            if (savedComparisonScenarios.length > 4) savedComparisonScenarios.pop();
            renderComparisonScenariosTable();
        }

        function clearScenarioComparisons() {
            savedComparisonScenarios = [];
            document.getElementById('sim-comparison-card').classList.add('hidden');
        }

        function printLoanSimulation() {
            window.print();
        }
