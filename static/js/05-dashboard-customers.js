        /* =========================================================================
           4. STAGE 1 & 2 LIVE CUSTOMERS MODULE (DIRECTORY, PROFILE & NOTES TAB D)
           ========================================================================= */
        function debounceCustomerSearch() {
            clearTimeout(customerSearchTimer);
            customerSearchTimer = setTimeout(() => loadCustomers(1), 280);
        }

let isDashboardSummaryLoading = false;

function setDashboardPrivacyMetric(elementId, centsValue) {
    const el = document.getElementById(elementId);

    if (!el || !Number.isFinite(centsValue)) return;

    const formatted = NuraFinancialEngine.formatMXNFromCents(centsValue);
    el.setAttribute('data-real-value', formatted);
    el.innerText = isPrivacyOn ? '••••••••' : formatted;
}

async function loadDashboardSummary() {
    if (isDashboardSummaryLoading) return;

    isDashboardSummaryLoading = true;
    const errBox = document.getElementById('dashboard-summary-error');
    if (errBox) errBox.classList.add('hidden');

    try {
        // 1. Core CRM Dashboard Summary (always loaded for commercial tenants)
        const coreRes = await fetch('/api/dashboard/summary');

        if (coreRes.status === 401) {
            mostrarAuthView();
            return;
        }

        if (coreRes.status === 403) {
            throw new Error('Insufficient permissions to view dashboard metrics.');
        }

        if (!coreRes.ok) {
            throw new Error(`Dashboard request failed (HTTP ${coreRes.status}).`);
        }

        const coreData = await coreRes.json();
        if (!coreData || typeof coreData !== 'object' || Array.isArray(coreData)) {
            throw new Error('Invalid dashboard response.');
        }

        const setIntText = (id, val) => {
            const el = document.getElementById(id);
            if (el && Number.isSafeInteger(val) && val >= 0) el.innerText = String(val);
        };

        const coreCounts = (coreData.counts && typeof coreData.counts === 'object') ? coreData.counts : {};
        const callList = (coreData.call_list && typeof coreData.call_list === 'object') ? coreData.call_list : {};
        setIntText('dash-customers-count', coreCounts.customers);
        setIntText('dash-leads-count', coreCounts.open_leads);
        setIntText('dash-calls-today-count', callList.today ?? coreCounts.call_list_today);
        setIntText('dash-calls-overdue-count', callList.overdue ?? coreCounts.call_list_overdue);
        setIntText('dash-calls-upcoming-count', callList.upcoming ?? coreCounts.call_list_upcoming);

        const activityListEl = document.getElementById('dash-recent-activity-list');
        if (activityListEl) {
            const activity = Array.isArray(coreData.recent_activity) ? coreData.recent_activity : [];
            if (activity.length === 0) {
                activityListEl.innerHTML = '<p class="text-xs text-slate-400 py-4 text-center">No recent CRM activity recorded yet.</p>';
            } else {
                activityListEl.innerHTML = activity.map(item => `
                    <div class="p-2.5 rounded-xl bg-slate-50 border border-slate-200/70 flex items-start justify-between gap-2 text-xs">
                        <div class="min-w-0">
                            <div class="flex items-center gap-1.5 flex-wrap">
                                <span class="text-[10px] font-extrabold px-2 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-200">${escapeHtml(item.interaction_type || 'NOTE')}</span>
                                ${item.outcome ? `<span class="text-[10px] font-bold px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200">${escapeHtml(item.outcome)}</span>` : ''}
                            </div>
                            <p class="text-slate-700 mt-1 line-clamp-2">${escapeHtml(item.content || '')}</p>
                            <p class="text-[10px] text-slate-400 mt-0.5">${escapeHtml(item.created_by_name || 'Team')}</p>
                        </div>
                        <span class="text-[10px] text-slate-400 whitespace-nowrap shrink-0">${escapeHtml(formatDateTimeShort(item.created_at))}</span>
                    </div>
                `).join('');
            }
        }

        // 2. Lending Portfolio Dashboard Summary (ONLY when LENDING module is enabled)
        if (typeof hasModule === 'function' && hasModule('LENDING')) {
            const lendRes = await fetch('/api/loans/dashboard-summary');

            if (lendRes.status === 401) {
                mostrarAuthView();
                return;
            }
            if (!lendRes.ok) {
                throw new Error(`Lending dashboard request failed (HTTP ${lendRes.status}).`);
            }

            const data = await lendRes.json();
            if (!data || typeof data !== 'object' || Array.isArray(data)) {
                throw new Error('Invalid lending dashboard response.');
            }

            const amountFields = [
                ['active_loan_portfolio_cents', 'saldo-val'],
                ['due_today_cents', 'dash-due-today-val'],
                ['overdue_amount_cents', 'dash-overdue-val'],
                ['total_outstanding_principal_cents', 'dash-outstanding-principal-val'],
                ['total_amount_due_cents', 'dash-total-amount-due-val']
            ];

            for (const [field, elementId] of amountFields) {
                if (Number.isFinite(data[field])) {
                    setDashboardPrivacyMetric(elementId, data[field]);
                }
            }

            const collectedEl = document.getElementById('dash-collected-week-val');
            if (
                collectedEl &&
                data.collected_this_week_enabled === true &&
                Number.isFinite(data.collected_this_week_cents)
            ) {
                collectedEl.classList.add('privacy-metric', 'text-emerald-700');
                collectedEl.classList.remove('text-slate-400');
                setDashboardPrivacyMetric('dash-collected-week-val', data.collected_this_week_cents);
            } else if (collectedEl) {
                collectedEl.classList.remove('privacy-metric', 'text-emerald-700');
                collectedEl.classList.add('text-slate-400');
                collectedEl.removeAttribute('data-real-value');
                collectedEl.innerText = 'Pending Stage 4';
            }

            const counts = data.counts;
            if (counts && typeof counts === 'object' && !Array.isArray(counts)) {
                const activeLoans = counts.active_loans;
                const countEl = document.getElementById('dash-active-loans-count');
                const badgeEl = document.getElementById('dash-live-loans-badge');

                if (Number.isSafeInteger(activeLoans) && activeLoans >= 0) {
                    if (countEl) countEl.innerText = String(activeLoans);
                    if (badgeEl) {
                        badgeEl.innerText = `${activeLoans} Live ${activeLoans === 1 ? 'Loan' : 'Loans'}`;
                    }
                }

                setIntText('dash-current-loans-count', counts.current_loans);
                setIntText('dash-overdue-loans-count', counts.overdue_loans);
                setIntText('dash-overdue-loans-badge', counts.overdue_loans);
                setIntText('dash-paidoff-loans-count', counts.paid_off_loans);
                setIntText('dash-defaulted-loans-count', counts.defaulted_loans);
                setIntText('dash-due-today-inst-count', counts.due_today_installments);
                setIntText('dash-overdue-inst-count', counts.overdue_installments);
                setIntText('dash-collected-week-count', counts.collected_week_payments);

                const totalLive = (counts.current_loans || 0) + (counts.overdue_loans || 0) + (counts.defaulted_loans || 0);
                const hbCurrent = document.getElementById('dhb-current');
                const hbOverdue = document.getElementById('dhb-overdue');
                const hbDefaulted = document.getElementById('dhb-defaulted');

                if (totalLive > 0) {
                    if (hbCurrent) hbCurrent.style.width = `${(counts.current_loans / totalLive) * 100}%`;
                    if (hbOverdue) hbOverdue.style.width = `${(counts.overdue_loans / totalLive) * 100}%`;
                    if (hbDefaulted) hbDefaulted.style.width = `${(counts.defaulted_loans / totalLive) * 100}%`;
                } else {
                    if (hbCurrent) hbCurrent.style.width = '0%';
                    if (hbOverdue) hbOverdue.style.width = '0%';
                    if (hbDefaulted) hbDefaulted.style.width = '0%';
                }
            }
        }

        // 3. Broker Pipeline & Commissions Summary (ONLY when BROKER module is enabled)
        if (typeof hasModule === 'function' && hasModule('BROKER') && typeof loadBrokerDashboardSummary === 'function') {
            await loadBrokerDashboardSummary();
        }
    } catch (err) {
        console.error('Dashboard summary error:', err);

        if (errBox) {
            errBox.innerText = 'Unable to refresh dashboard metrics. Please try again.';
            errBox.classList.remove('hidden');
        }
    } finally {
        isDashboardSummaryLoading = false;
    }
}

                async function loadCustomers(page = 1) {
            currentCustomersPage = page;
            const search = document.getElementById('customer-search-input')?.value.trim() || '';
            const status = document.getElementById('customer-status-filter')?.value || 'ALL';
            const sort = document.getElementById('customer-sort-select')?.value || 'CREATED_DESC';
            const tbody = document.getElementById('customers-table-tbody');

            const params = new URLSearchParams({ page: String(page), page_size: '20', sort });
            if (search) params.set('search', search);
            if (status && status !== 'ALL') params.set('status', status);

            try {
                const res = await fetch(`/api/customers?${params.toString()}`);
                if (res.status === 401) {
                    mostrarAuthView();
                    return;
                }
                if (res.status === 403) {
                    throw new Error("Insufficient permissions to view customers.");
                }
                if (!res.ok) throw new Error("Could not load customers");
                const data = await res.json();
                currentCustomersTotal = data.total || 0;

                const dashCountEl = document.getElementById('dash-customers-count');
                if (dashCountEl) dashCountEl.innerText = String(currentCustomersTotal);

                tbody.innerHTML = '';
                if (!data.items || data.items.length === 0) {
                    tbody.innerHTML = `
                        <tr>
                            <td colspan="6" class="py-12 px-6 text-center">
                                <div class="max-w-md mx-auto space-y-2">
                                    <p class="text-sm font-bold text-slate-900">No customers found</p>
                                    <p class="text-xs text-slate-500">Click "+ New Customer" or convert a qualified Lead.</p>
                                </div>
                            </td>
                        </tr>
                    `;
                } else {
                    data.items.forEach(c => {
                        const tr = document.createElement('tr');
                        tr.className = 'hover:bg-slate-50/90 transition cursor-pointer';
                        tr.onclick = () => openCustomerDetail(c.id);

                        const statusBadgeClass = c.status === 'ACTIVE'
                            ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                            : (c.status === 'INACTIVE' ? 'bg-slate-100 text-slate-600 border-slate-200' : 'bg-amber-50 text-amber-800 border-amber-200');

                        const kycBadgeClass = c.verification_status === 'VERIFIED'
                            ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                            : (c.verification_status === 'PENDING_VERIFICATION' ? 'bg-amber-50 text-amber-800 border-amber-200' : 'bg-slate-100 text-slate-600 border-slate-200');

                        tr.innerHTML = `
                            <td class="py-3.5 px-4 font-mono font-bold text-blue-700">${escapeHtml(c.customer_number)}</td>
                            <td class="py-3.5 px-4">
                                <p class="font-bold text-slate-900">${escapeHtml(c.full_legal_name)}</p>
                                <p class="text-[10px] text-slate-500">${c.entity_type === 'BUSINESS' ? `Business • Rep: ${escapeHtml(c.legal_representative_name || 'N/A')}` : `Individual • ${escapeHtml(c.email || 'No email')}`}</p>
                            </td>
                            <td class="py-3.5 px-4 font-medium text-slate-700">${escapeHtml(c.phone_primary)}</td>
                            <td class="py-3.5 px-4">
                                <span class="text-[10px] font-bold px-2.5 py-0.5 rounded-full border ${statusBadgeClass}">${escapeHtml(c.status)}</span>
                            </td>
                            <td class="py-3.5 px-4">
                                <span class="text-[10px] font-bold px-2.5 py-0.5 rounded-full border ${kycBadgeClass}">${escapeHtml(c.verification_status || 'NOT_PROVIDED')}</span>
                            </td>
                            <td class="py-3.5 px-4 text-right">
                                <button type="button" class="text-xs font-bold text-blue-600 hover:underline">View Profile →</button>
                            </td>
                        `;
                        tbody.appendChild(tr);
                    });
                }

                const startIdx = currentCustomersTotal === 0 ? 0 : (page - 1) * data.page_size + 1;
                const endIdx = Math.min(page * data.page_size, currentCustomersTotal);
                document.getElementById('customers-pagination-info').innerText = `Showing ${startIdx}–${endIdx} of ${currentCustomersTotal} customers`;
                document.getElementById('cust-prev-btn').disabled = page <= 1;
                document.getElementById('cust-next-btn').disabled = endIdx >= currentCustomersTotal;
            } catch (err) {
                tbody.innerHTML = `<tr><td colspan="6" class="py-8 text-center text-red-600">${escapeHtml(err.message)}</td></tr>`;
            }
        }

        function changeCustomerPage(delta) {
            const next = currentCustomersPage + delta;
            if (next >= 1) loadCustomers(next);
        }

        function getLoanStatusBadgeStyle(status) {
            const styles = {
                DRAFT: 'bg-amber-50 text-amber-800 border-amber-200',
                APPROVED: 'bg-blue-50 text-blue-700 border-blue-200',
                ACTIVE: 'bg-emerald-50 text-emerald-700 border-emerald-200',
                PAID_OFF: 'bg-slate-100 text-slate-700 border-slate-200',
                CANCELLED: 'bg-red-50 text-red-700 border-red-200',
                DEFAULTED: 'bg-red-100 text-red-900 border-red-300'
            };
            return styles[status] || 'bg-slate-100 text-slate-700 border-slate-200';
        }

        async function openCustomerDetail(customerId) {
            try {
                const res = await fetch(`/api/customers/${customerId}`);
                if (res.status === 401) {
                    mostrarAuthView();
                    return;
                }
                if (res.status === 403) {
                    throw new Error("Insufficient permissions to view this customer profile.");
                }
                if (!res.ok) throw new Error("Could not load customer profile");
                const c = await res.json();
                currentSelectedCustomer = c;

                document.getElementById('detail-cust-number-crumb').innerText = c.customer_number;
                document.getElementById('detail-cust-title').innerText = c.full_legal_name;
                document.getElementById('detail-cust-entity-badge').innerText = c.entity_type;
                document.getElementById('detail-cust-status-badge').innerText = c.status;
                document.getElementById('detail-verification-badge').innerText = c.verification_status;
                // Llenar datos de la tira de resumen rápido
                const qsPhone = document.getElementById('cd-qs-phone');
                const qsRfc = document.getElementById('cd-qs-rfc');
                const qsBalance = document.getElementById('cd-qs-balance');
                
                if (qsPhone) qsPhone.innerText = c.phone_primary || 'No Phone';
                if (qsRfc) qsRfc.innerText = c.tax_id_rfc || 'No RFC';

                const hasLending = (typeof hasModule === 'function') && hasModule('LENDING');
                const hasBroker = (typeof hasModule === 'function') && hasModule('BROKER');
                document.getElementById('cd-qs-balance-sep')?.classList.toggle('module-hidden', !hasLending);
                document.getElementById('cd-qs-balance-wrap')?.classList.toggle('module-hidden', !hasLending);
                document.getElementById('cd-qs-balance-wrap')?.classList.toggle('hidden', !hasLending);
                document.getElementById('ctab-btn-summary')?.classList.toggle('module-hidden', !hasLending);
                document.getElementById('ctab-btn-summary')?.classList.toggle('hidden', !hasLending);
                document.getElementById('ctab-btn-loans')?.classList.toggle('module-hidden', !hasLending);
                document.getElementById('ctab-btn-loans')?.classList.toggle('hidden', !hasLending);
                if (!hasLending) {
                    document.getElementById('ctab-panel-summary')?.classList.add('hidden');
                    document.getElementById('ctab-panel-loans')?.classList.add('hidden');
                }
                document.getElementById('cd-qs-broker-sep')?.classList.toggle('module-hidden', !hasBroker);
                document.getElementById('cd-qs-broker-wrap')?.classList.toggle('module-hidden', !hasBroker);
                document.getElementById('cd-qs-broker-wrap')?.classList.toggle('hidden', !hasBroker);
                document.getElementById('ctab-btn-broker')?.classList.toggle('module-hidden', !hasBroker);
                document.getElementById('ctab-btn-broker')?.classList.toggle('hidden', !hasBroker);
                if (!hasBroker) {
                    document.getElementById('ctab-panel-broker')?.classList.add('hidden');
                }

                const valOrNP = v => (v && String(v).trim()) ? String(v) : 'Not Provided';
                document.getElementById('det-customer-number').innerText = c.customer_number;
                document.getElementById('det-full-name').innerText = c.full_legal_name;
                document.getElementById('det-dob').innerText = valOrNP(c.date_of_birth);
                document.getElementById('det-phone-primary').innerText = c.phone_primary;
                document.getElementById('det-phone-secondary').innerText = valOrNP(c.phone_secondary);
                document.getElementById('det-email').innerText = valOrNP(c.email);
                document.getElementById('det-address').innerText = valOrNP(c.address_full);
                document.getElementById('det-emp-status').innerText = valOrNP(c.employment_status);
                document.getElementById('det-employer').innerText = valOrNP(c.employer_or_business_name);
                document.getElementById('det-occupation').innerText = valOrNP(c.occupation_or_industry);
                document.getElementById('det-emp-address').innerText = valOrNP(c.employment_address);
                const sinceDate = c.customer_since ? c.customer_since.split('T')[0] : 'N/A';
                document.getElementById('det-since-rfc').innerText = `Since ${sinceDate} • RFC: ${valOrNP(c.tax_id_rfc)}`;

                const bizBlock = document.getElementById('det-business-block');
                if (c.entity_type === 'BUSINESS') {
                    bizBlock.classList.remove('hidden');
                    document.getElementById('det-trade-name').innerText = valOrNP(c.trade_name);
                    document.getElementById('det-legal-rep').innerText = valOrNP(c.legal_representative_name);
                    document.getElementById('det-corp-rfc').innerText = valOrNP(c.tax_id_rfc);
                } else {
                    bizBlock.classList.add('hidden');
                }

                // Populate Lending Tabs B & C ONLY when LENDING module is enabled (zero /api/loans calls for CORE-only)
                if (hasLending) {
                    let lendingSummary = null;
                    try {
                        const lendRes = await fetch(`/api/loans/customers/${customerId}/summary`);
                        if (lendRes.ok) {
                            lendingSummary = await lendRes.json();
                        }
                    } catch (_) {}

                    const cs = (lendingSummary && lendingSummary.credit_summary && typeof lendingSummary.credit_summary === 'object')
                        ? lendingSummary.credit_summary
                        : {};
                    const loansList = (lendingSummary && Array.isArray(lendingSummary.loans)) ? lendingSummary.loans : [];

                    if (qsBalance) {
                        const bal = Number(cs.current_outstanding_principal_cents) || 0;
                        qsBalance.innerText = NuraFinancialEngine.formatMXNFromCents(bal);
                        qsBalance.className = bal > 0 ? 'font-mono text-red-600 font-extrabold' : 'font-mono text-emerald-600 font-extrabold';
                    }

                    const outPrincipalEl = document.getElementById('csum-outstanding-principal');
                    const totalDueEl = document.getElementById('csum-total-due');
                    const overdueEl = document.getElementById('csum-overdue-amount');
                    const ratioEl = document.getElementById('csum-loans-ratio');
                    const nextDueEl = document.getElementById('csum-next-due-date');
                    const remInstEl = document.getElementById('csum-remaining-installments');
                    const lastPayEl = document.getElementById('csum-last-payment-date');

                    if (outPrincipalEl) {
                        outPrincipalEl.innerText = Number.isFinite(cs.current_outstanding_principal_cents)
                            ? NuraFinancialEngine.formatMXNFromCents(cs.current_outstanding_principal_cents)
                            : 'N/A';
                    }
                    if (totalDueEl) {
                        totalDueEl.innerText = Number.isFinite(cs.total_amount_due_cents)
                            ? NuraFinancialEngine.formatMXNFromCents(cs.total_amount_due_cents)
                            : 'N/A';
                    }
                    if (overdueEl) {
                        overdueEl.innerText = Number.isFinite(cs.overdue_amount_cents)
                            ? NuraFinancialEngine.formatMXNFromCents(cs.overdue_amount_cents)
                            : 'N/A';
                    }
                    if (ratioEl) {
                        const actCount = Number.isSafeInteger(cs.active_loans_count) ? cs.active_loans_count : 'N/A';
                        const totCount = Number.isSafeInteger(cs.total_loans_count) ? cs.total_loans_count : 'N/A';
                        ratioEl.innerText = `${actCount} / ${totCount}`;
                    }
                    if (nextDueEl) {
                        nextDueEl.innerText = cs.next_payment_due_date ? String(cs.next_payment_due_date) : 'None scheduled';
                    }
                    if (remInstEl) {
                        remInstEl.innerText = Number.isSafeInteger(cs.remaining_installments)
                            ? String(cs.remaining_installments)
                            : 'N/A';
                    }
                    if (lastPayEl) {
                        lastPayEl.innerText = cs.last_payment_date ? String(cs.last_payment_date) : '—';
                    }

                    const loansTabBtn = document.getElementById('ctab-btn-loans');
                    if (loansTabBtn) {
                        loansTabBtn.innerText = `C. Loans (${loansList.length})`;
                    }

                    const loansTbody = document.getElementById('customer-loans-tbody');
                    if (loansTbody) {
                        loansTbody.innerHTML = '';
                        if (loansList.length === 0) {
                            loansTbody.innerHTML = `
                                <tr>
                                    <td colspan="10" class="py-8 text-center text-slate-400">
                                        No loans associated with this customer yet.
                                    </td>
                                </tr>
                            `;
                        } else {
                            loansList.forEach(l => {
                                const tr = document.createElement('tr');
                                tr.className = 'hover:bg-slate-50/90 transition cursor-pointer';
                                tr.onclick = () => {
                                    switchTab('loans');
                                    openLoanDetailBlueprint();
                                    if (typeof openLoanDetail === 'function') {
                                        openLoanDetail(l.id);
                                    }
                                };

                                const badgeClass = getLoanStatusBadgeStyle(l.status);
                                const origPrincipal = Number.isFinite(l.principal_cents) ? NuraFinancialEngine.formatMXNFromCents(l.principal_cents) : 'N/A';
                                const unpaidPrincipal = Number.isFinite(l.unpaid_principal_cents) ? NuraFinancialEngine.formatMXNFromCents(l.unpaid_principal_cents) : 'N/A';
                                const remInterest = Number.isFinite(l.remaining_contractual_interest_cents) ? NuraFinancialEngine.formatMXNFromCents(l.remaining_contractual_interest_cents) : 'N/A';
                                const remTotal = Number.isFinite(l.remaining_contract_total_cents) ? NuraFinancialEngine.formatMXNFromCents(l.remaining_contract_total_cents) : 'N/A';
                                const payableToday = Number.isFinite(l.current_payable_amount_cents) ? NuraFinancialEngine.formatMXNFromCents(l.current_payable_amount_cents) : 'N/A';
                                const hasOverdue = Number.isFinite(l.overdue_amount_cents) && l.overdue_amount_cents > 0;

                                tr.innerHTML = `
                                    <td class="py-3 px-4 font-mono font-bold text-blue-700">${escapeHtml(l.loan_number)}</td>
                                    <td class="py-3 px-4 font-semibold text-slate-800">${escapeHtml(l.product_name)}</td>
                                    <td class="py-3 px-4">
                                        <span class="text-[10px] font-bold px-2.5 py-0.5 rounded-full border ${badgeClass}">${escapeHtml(l.status)}</span>
                                    </td>
                                    <td class="py-3 px-4 text-right font-mono text-slate-800">${escapeHtml(origPrincipal)}</td>
                                    <td class="py-3 px-4 text-right font-mono font-bold text-slate-900">${escapeHtml(unpaidPrincipal)}</td>
                                    <td class="py-3 px-4 text-right font-mono text-amber-700">${escapeHtml(remInterest)}</td>
                                    <td class="py-3 px-4 text-right font-mono font-bold text-blue-900">${escapeHtml(remTotal)}</td>
                                    <td class="py-3 px-4 text-right font-mono font-bold ${hasOverdue ? 'text-red-600' : 'text-slate-800'}">${escapeHtml(payableToday)}</td>
                                    <td class="py-3 px-4 font-mono text-slate-600">${escapeHtml(l.maturity_date || '—')}</td>
                                    <td class="py-3 px-4 text-right">
                                        <button type="button" class="text-xs font-bold text-blue-600 hover:underline">Inspect →</button>
                                    </td>
                                `;
                                loansTbody.appendChild(tr);
                            });
                        }
                    }
                }

                // Populate Broker Tab ONLY when BROKER module is enabled (zero /api/broker calls without BROKER)
                if (hasBroker && typeof loadCustomerBrokerSummary === 'function') {
                    await loadCustomerBrokerSummary(customerId);
                }

                document.getElementById('customers-master-view').classList.add('hidden');
                document.getElementById('customer-detail-view').classList.remove('hidden');
                switchCustomerProfileTab('personal');
                await loadCustomerInteractions(customerId);
            } catch (err) {
                alert(err.message);
            }
        }


        async function loadCustomerInteractions(customerId) {
            const listEl = document.getElementById('customer-interactions-list');
            listEl.innerHTML = '<p class="text-slate-400 py-4 text-center">Loading customer interactions...</p>';
            try {
                const res = await fetch(`/api/customers/${customerId}/interactions`);
                if (!res.ok) throw new Error("Failed to load interactions");
                const items = await res.json();
                listEl.innerHTML = '';
                if (!items || items.length === 0) {
                    listEl.innerHTML = '<p class="text-slate-400 py-6 text-center">No interactions or notes recorded for this customer yet.</p>';
                    return;
                }
                items.forEach(i => {
                    const div = document.createElement('div');
                    div.className = 'p-3.5 rounded-2xl bg-slate-50 border border-slate-200/70 space-y-1';
                    const inheritedBadge = i.lead_id
                        ? '<span class="text-[9px] font-bold px-2 py-0.5 rounded bg-purple-100 text-purple-800">Inherited from Lead</span>'
                        : '';
                    div.innerHTML = `
                        <div class="flex items-center justify-between gap-2 flex-wrap">
                            <div class="flex items-center gap-1.5">
                                <span class="text-[10px] font-extrabold px-2 py-0.5 rounded bg-blue-100 text-blue-800">${escapeHtml(i.interaction_type)}</span>
                                <span class="text-[10px] font-bold px-2 py-0.5 rounded bg-slate-200 text-slate-700">${escapeHtml(i.visibility)}</span>
                                ${i.outcome ? `<span class="text-[10px] font-bold px-2 py-0.5 rounded bg-emerald-100 text-emerald-800">${escapeHtml(i.outcome)}</span>` : ''}
                                ${inheritedBadge}
                            </div>
                            <span class="text-[10px] text-slate-400">${escapeHtml(formatDateTimeShort(i.created_at))}</span>
                        </div>
                        <p class="text-xs text-slate-800 whitespace-pre-wrap pt-1">${escapeHtml(i.content)}</p>
                        <p class="text-[10px] text-slate-500 pt-1">Recorded by <strong>${escapeHtml(i.created_by_name)}</strong> (${escapeHtml(i.created_by_role)})</p>
                    `;
                    listEl.appendChild(div);
                });
            } catch (err) {
                listEl.innerHTML = `<p class="text-red-600">${escapeHtml(err.message)}</p>`;
            }
        }

        async function handleCustomerInteractionSubmit(e) {
            e.preventDefault();
            if (!currentSelectedCustomer) return;
            const errBox = document.getElementById('cust-interaction-error');
            const btn = document.getElementById('ci-submit-btn');
            errBox.classList.add('hidden');
            btn.disabled = true;

            const payload = {
                interaction_type: document.getElementById('ci-type').value,
                direction: 'OUTBOUND',
                visibility: document.getElementById('ci-visibility').value,
                outcome: document.getElementById('ci-outcome').value || null,
                content: document.getElementById('ci-content').value.trim()
            };

            try {
                const res = await fetch(`/api/customers/${currentSelectedCustomer.id}/interactions`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCookie('csrf_token') },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || "Could not save interaction");
                document.getElementById('ci-content').value = '';
                document.getElementById('ci-outcome').value = '';
                await loadCustomerInteractions(currentSelectedCustomer.id);
            } catch (err) {
                errBox.innerText = err.message;
                errBox.classList.remove('hidden');
            } finally {
                btn.disabled = false;
            }
        }

        function closeCustomerProfileView() {
            document.getElementById('customer-detail-view').classList.add('hidden');
            document.getElementById('customers-master-view').classList.remove('hidden');
        }

        function toggleCustomerModalEntityFields() {
            const entityType = document.getElementById('cf-entity-type').value;
            const isBiz = entityType === 'BUSINESS';
            document.getElementById('cf-business-group').classList.toggle('hidden', !isBiz);
            document.getElementById('cf-legal-rep').required = isBiz;
            // Persona física: nombre, segundo nombre (opcional) y apellidos. Persona moral: razón social.
            document.getElementById('cf-individual-name-group').classList.toggle('hidden', isBiz);
            document.getElementById('cf-business-name-group').classList.toggle('hidden', !isBiz);
            document.getElementById('cf-first-name').required = !isBiz;
            document.getElementById('cf-last-name').required = !isBiz;
            document.getElementById('cf-full-name').required = isBiz;
        }

        function openCustomerFormModal(mode) {
            customerFormMode = mode;
            const errBox = document.getElementById('customer-form-error');
            errBox.classList.add('hidden');
            const form = document.getElementById('customer-form');
            form.reset();
            document.getElementById('cf-legacy-name-hint').classList.add('hidden');

            if (mode === 'edit' && currentSelectedCustomer) {
                const c = currentSelectedCustomer;
                document.getElementById('customer-modal-title').innerText = `Edit Customer ${c.customer_number}`;
                document.getElementById('cf-entity-type').value = c.entity_type || 'INDIVIDUAL';
                document.getElementById('cf-status').value = c.status || 'ACTIVE';
                document.getElementById('cf-verification').value = c.verification_status || 'PENDING_VERIFICATION';
                document.getElementById('cf-full-name').value = c.entity_type === 'BUSINESS' ? (c.full_legal_name || '') : '';
                document.getElementById('cf-first-name').value = c.first_name || '';
                document.getElementById('cf-middle-name').value = c.middle_name || '';
                document.getElementById('cf-last-name').value = c.last_name || '';
                // Clientes registrados antes de este cambio no tienen el nombre separado: se avisa para capturarlo.
                if (c.entity_type !== 'BUSINESS' && !c.first_name && !c.last_name && c.full_legal_name) {
                    const legacyHint = document.getElementById('cf-legacy-name-hint');
                    legacyHint.innerText = (NuraI18n.currentLang === 'es')
                        ? `Nombre actual registrado: ${c.full_legal_name}. Captúralo separado en nombre y apellidos.`
                        : `Name currently on file: ${c.full_legal_name}. Please enter it split into first and last name.`;
                    legacyHint.classList.remove('hidden');
                }
                document.getElementById('cf-legal-rep').value = c.legal_representative_name || '';
                document.getElementById('cf-trade-name').value = c.trade_name || '';
                document.getElementById('cf-phone-primary').value = c.phone_primary || '';
                document.getElementById('cf-phone-secondary').value = c.phone_secondary || '';
                document.getElementById('cf-email').value = c.email || '';
                document.getElementById('cf-rfc').value = c.tax_id_rfc || '';
                document.getElementById('cf-dob').value = c.date_of_birth || '';
                document.getElementById('cf-emp-status').value = c.employment_status || '';
                document.getElementById('cf-employer').value = c.employer_or_business_name || '';
                document.getElementById('cf-occupation').value = c.occupation_or_industry || '';
                document.getElementById('cf-address').value = c.address_full || '';
                document.getElementById('cf-emp-address').value = c.employment_address || '';
            } else {
                document.getElementById('customer-modal-title').innerText = 'Register New Customer';
            }

            toggleCustomerModalEntityFields();
            document.getElementById('customer-form-modal').classList.remove('hidden');
        }

        function closeCustomerFormModal() {
            document.getElementById('customer-form-modal').classList.add('hidden');
        }

        async function handleCustomerFormSubmit(e) {
            e.preventDefault();
            const errBox = document.getElementById('customer-form-error');
            const submitBtn = document.getElementById('cf-submit-btn');
            errBox.classList.add('hidden');
            submitBtn.disabled = true;

            const isBizEntity = document.getElementById('cf-entity-type').value === 'BUSINESS';
            const payload = {
                entity_type: document.getElementById('cf-entity-type').value,
                status: document.getElementById('cf-status').value,
                verification_status: document.getElementById('cf-verification').value,
                full_legal_name: isBizEntity ? (document.getElementById('cf-full-name').value.trim() || null) : null,
                first_name: isBizEntity ? null : document.getElementById('cf-first-name').value.trim(),
                middle_name: isBizEntity ? null : document.getElementById('cf-middle-name').value.trim(),
                last_name: isBizEntity ? null : document.getElementById('cf-last-name').value.trim(),
                legal_representative_name: document.getElementById('cf-legal-rep').value.trim() || null,
                trade_name: document.getElementById('cf-trade-name').value.trim() || null,
                phone_primary: document.getElementById('cf-phone-primary').value.trim(),
                phone_secondary: document.getElementById('cf-phone-secondary').value.trim() || null,
                email: document.getElementById('cf-email').value.trim() || null,
                tax_id_rfc: document.getElementById('cf-rfc').value.trim() || null,
                date_of_birth: document.getElementById('cf-dob').value || null,
                employment_status: document.getElementById('cf-emp-status').value.trim() || null,
                employer_or_business_name: document.getElementById('cf-employer').value.trim() || null,
                occupation_or_industry: document.getElementById('cf-occupation').value.trim() || null,
                address_full: document.getElementById('cf-address').value.trim() || null,
                employment_address: document.getElementById('cf-emp-address').value.trim() || null
            };

            const isEdit = (customerFormMode === 'edit' && currentSelectedCustomer);
            const url = isEdit ? `/api/customers/${currentSelectedCustomer.id}` : '/api/customers';
            const method = isEdit ? 'PATCH' : 'POST';

            try {
                const res = await fetch(url, {
                    method,
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCookie('csrf_token') },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!res.ok) {
                    const msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
                    throw new Error(msg || 'Failed to save customer');
                }
                closeCustomerFormModal();
                await loadCustomers(currentCustomersPage);
                switchTab('customers');
                await openCustomerDetail(data.id);
            } catch (err) {
                errBox.innerText = err.message;
                errBox.classList.remove('hidden');
            } finally {
                submitBtn.disabled = false;
            }
        }

        function switchCustomerProfileTab(tabKey) {
            const hasLending = (typeof hasModule === 'function') && hasModule('LENDING');
            const hasBroker = (typeof hasModule === 'function') && hasModule('BROKER');
            let effectiveKey = tabKey;
            if (!hasLending && (tabKey === 'summary' || tabKey === 'loans')) {
                effectiveKey = 'personal';
            }
            if (!hasBroker && tabKey === 'broker') {
                effectiveKey = 'personal';
            }
            const tabs = ['personal', 'summary', 'loans', 'broker', 'interactions', 'documents'];
            tabs.forEach(k => {
                const btn = document.getElementById('ctab-btn-' + k);
                const panel = document.getElementById('ctab-panel-' + k);
                if (!btn || !panel) return;
                const moduleCls = (k === 'summary' || k === 'loans')
                    ? 'lending-only-ui '
                    : (k === 'broker' ? 'broker-only-ui ' : '');
                if (!hasLending && (k === 'summary' || k === 'loans')) {
                    btn.classList.add('hidden', 'module-hidden');
                    panel.classList.add('hidden');
                    return;
                }
                if (!hasBroker && k === 'broker') {
                    btn.classList.add('hidden', 'module-hidden');
                    panel.classList.add('hidden');
                    return;
                }
                if (k === effectiveKey) {
                    btn.className = `${moduleCls}py-2 px-4 rounded-xl font-semibold bg-white text-slate-900 shadow-sm whitespace-nowrap`;
                    panel.classList.remove('hidden');
                } else {
                    btn.className = `${moduleCls}py-2 px-4 rounded-xl font-semibold text-slate-600 hover:text-slate-900 whitespace-nowrap`;
                    panel.classList.add('hidden');
                }
            });
        }
