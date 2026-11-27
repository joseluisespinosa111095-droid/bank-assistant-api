        /* =========================================================================
           3. STAGE 2 LIVE LEADS & PIPELINE, CALL LIST, INTERACTIONS & CONVERSION
           ========================================================================= */
        function debounceLeadSearch() {
            clearTimeout(leadSearchTimer);
            leadSearchTimer = setTimeout(() => loadLeads(1), 280);
        }

        function getStageBadgeStyle(stage) {
            const styles = {
                NEW: 'bg-blue-50 text-blue-700 border-blue-200',
                CONTACT_ATTEMPTED: 'bg-amber-50 text-amber-800 border-amber-200',
                CONTACTED: 'bg-indigo-50 text-indigo-700 border-indigo-200',
                QUALIFIED: 'bg-purple-50 text-purple-700 border-purple-200',
                APPLICATION_STARTED: 'bg-cyan-50 text-cyan-800 border-cyan-200',
                UNDER_REVIEW: 'bg-amber-100 text-amber-900 border-amber-300',
                APPROVED: 'bg-emerald-50 text-emerald-700 border-emerald-200',
                CONVERTED: 'bg-emerald-100 text-emerald-900 border-emerald-300',
                NURTURE: 'bg-slate-100 text-slate-700 border-slate-200',
                LOST: 'bg-red-50 text-red-700 border-red-200'
            };
            return styles[stage] || 'bg-slate-100 text-slate-700 border-slate-200';
        }

        async function loadLeads(page = 1) {
            currentLeadsPage = page;
            const search = document.getElementById('lead-search-input')?.value.trim() || '';
            const stage = document.getElementById('lead-stage-filter')?.value || 'ALL';
            const product = document.getElementById('lead-product-filter')?.value || 'ALL';
            const tbody = document.getElementById('leads-table-tbody');

            const params = new URLSearchParams({ page: String(page), page_size: '20' });
            if (search) params.set('search', search);
            if (stage && stage !== 'ALL') params.set('stage', stage);
            if (product && product !== 'ALL') params.set('product_interest', product);

            try {
                const res = await fetch(`/api/leads?${params.toString()}`);
                if (!res.ok) throw new Error("Could not load leads");
                const data = await res.json();
                currentLeadsTotal = data.total || 0;

                // Update Dashboard & Call List Counters
                const counts = data.call_list_counts || { open_total: 0, overdue: 0, today: 0, upcoming: 0 };
                const dashLeadsEl = document.getElementById('dash-leads-count');
                const dashCallsEl = document.getElementById('dash-calls-today-count');
                if (dashLeadsEl) dashLeadsEl.innerText = String(counts.open_total);
                if (dashCallsEl) dashCallsEl.innerText = String(counts.today + counts.overdue);

                document.getElementById('calllist-overdue-count').innerText = String(counts.overdue);
                document.getElementById('calllist-today-count').innerText = String(counts.today);
                document.getElementById('calllist-upcoming-count').innerText = String(counts.upcoming);

                tbody.innerHTML = '';
                if (!data.items || data.items.length === 0) {
                    tbody.innerHTML = `
                        <tr>
                            <td colspan="8" class="py-12 px-6 text-center">
                                <div class="max-w-md mx-auto space-y-2">
                                    <p class="text-sm font-bold text-slate-900">No leads found</p>
                                    <p class="text-xs text-slate-500">Click "+ New Lead" to register a prospect in PostgreSQL.</p>
                                </div>
                            </td>
                        </tr>
                    `;
                } else {
                    data.items.forEach(l => {
                        const tr = document.createElement('tr');
                        tr.className = 'hover:bg-slate-50/90 transition cursor-pointer';
                        tr.onclick = () => openLeadDetail(l.id);

                        const badgeClass = getStageBadgeStyle(l.stage);
                        const followUpText = l.next_follow_up_at
                            ? formatDateTimeShort(l.next_follow_up_at)
                            : (l.last_interaction_outcome ? `Last: ${l.last_interaction_outcome}` : 'No call scheduled');

                        tr.innerHTML = `
                            <td class="py-3.5 px-4 font-mono font-bold text-blue-700">${escapeHtml(l.lead_number)}</td>
                            <td class="py-3.5 px-4">
                                <p class="font-bold text-slate-900">${escapeHtml(l.full_name)}</p>
                                <p class="text-[10px] text-slate-500">${escapeHtml(l.entity_type)} • Priority: ${escapeHtml(l.priority)}</p>
                            </td>
                            <td class="py-3.5 px-4">
                                <p class="font-medium text-slate-800">${escapeHtml(l.phone_primary)}</p>
                                <p class="text-[10px] text-slate-500">${escapeHtml(l.source)}</p>
                            </td>
                            <td class="py-3.5 px-4 font-semibold text-slate-700">${escapeHtml(l.product_interest)}</td>
                            <td class="py-3.5 px-4 text-right font-mono font-bold text-slate-900">${NuraFinancialEngine.formatMXNFromCents(l.requested_amount_cents)}</td>
                            <td class="py-3.5 px-4">
                                <span class="text-[10px] font-bold px-2.5 py-0.5 rounded-full border ${badgeClass}">${escapeHtml(l.stage)}</span>
                            </td>
                            <td class="py-3.5 px-4 text-[11px] text-slate-600">${escapeHtml(followUpText)}</td>
                            <td class="py-3.5 px-4 text-right">
                                <button type="button" class="text-xs font-bold text-blue-600 hover:underline">Open →</button>
                            </td>
                        `;
                        tbody.appendChild(tr);
                    });
                }

                const startIdx = currentLeadsTotal === 0 ? 0 : (page - 1) * data.page_size + 1;
                const endIdx = Math.min(page * data.page_size, currentLeadsTotal);
                document.getElementById('leads-pagination-info').innerText = `Showing ${startIdx}–${endIdx} of ${currentLeadsTotal} leads`;
                document.getElementById('lead-prev-btn').disabled = page <= 1;
                document.getElementById('lead-next-btn').disabled = endIdx >= currentLeadsTotal;
            } catch (err) {
                tbody.innerHTML = `<tr><td colspan="8" class="py-8 text-center text-red-600">${escapeHtml(err.message)}</td></tr>`;
            }
        }

        function changeLeadPage(delta) {
            const next = currentLeadsPage + delta;
            if (next >= 1) loadLeads(next);
        }

        async function setCallListBucketFilter(bucket) {
            activeCallListBucket = bucket;
            const labels = {
                OVERDUE: 'Queue: Overdue Calls (Requires Immediate Follow-up)',
                TODAY: "Queue: Today's Scheduled Calls & Uncontacted New Leads",
                UPCOMING: 'Queue: Upcoming Scheduled Calls'
            };
            document.getElementById('calllist-active-label').innerText = labels[bucket] || 'Call Queue';

            ['TODAY', 'OVERDUE', 'UPCOMING'].forEach(b => {
                const btn = document.getElementById('cl-btn-' + b);
                if (!btn) return;
                btn.className = (b === bucket)
                    ? 'px-3.5 py-1.5 rounded-xl font-semibold bg-slate-900 text-white'
                    : 'px-3.5 py-1.5 rounded-xl font-semibold bg-slate-100 text-slate-700 hover:bg-slate-200';
            });

            const container = document.getElementById('calllist-cards-container');
            container.innerHTML = '<p class="text-xs text-slate-400 col-span-2 py-6 text-center">Loading call queue...</p>';

            try {
                const res = await fetch(`/api/leads?call_bucket=${encodeURIComponent(bucket)}&page_size=50`);
                if (!res.ok) throw new Error("Failed to load call list");
                const data = await res.json();
                container.innerHTML = '';

                if (!data.items || data.items.length === 0) {
                    container.innerHTML = `
                        <div class="col-span-2 fintech-card p-8 bg-white text-center space-y-1.5">
                            <p class="text-sm font-bold text-slate-900">No leads in this call queue</p>
                            <p class="text-xs text-slate-500">All scheduled calls for this bucket are clear.</p>
                        </div>
                    `;
                    return;
                }

                data.items.forEach(l => {
                    const card = document.createElement('div');
                    card.className = 'fintech-card p-4 bg-white space-y-3 flex flex-col justify-between';
                    const followStr = l.next_follow_up_at ? formatDateTimeShort(l.next_follow_up_at) : 'New Lead (First Contact Pending)';
                    card.innerHTML = `
                        <div class="flex items-start justify-between gap-2">
                            <div>
                                <div class="flex items-center gap-1.5">
                                    <span class="font-mono text-[11px] font-bold text-blue-700">${escapeHtml(l.lead_number)}</span>
                                    <span class="text-[10px] font-bold px-2 py-0.5 rounded-full border ${getStageBadgeStyle(l.stage)}">${escapeHtml(l.stage)}</span>
                                </div>
                                <h4 class="text-sm font-bold text-slate-900 mt-1">${escapeHtml(l.full_name)}</h4>
                                <p class="text-xs text-slate-600 font-medium">${escapeHtml(l.phone_primary)} • ${escapeHtml(l.product_interest)}</p>
                            </div>
                            <div class="text-right">
                                <p class="text-xs font-extrabold font-mono text-slate-900">${NuraFinancialEngine.formatMXNFromCents(l.requested_amount_cents)}</p>
                                <p class="text-[10px] text-amber-700 font-semibold mt-0.5">${escapeHtml(followStr)}</p>
                            </div>
                        </div>
                        <div class="grid grid-cols-3 gap-2 pt-2 border-t border-slate-100 text-xs">
                            <a href="tel:${escapeHtml(l.phone_primary)}" class="py-2 px-3 rounded-xl bg-emerald-50 hover:bg-emerald-100 text-emerald-700 font-bold text-center">📞 Dial</a>
                            <button type="button" class="btn-quick-log py-2 px-3 rounded-xl bg-blue-50 hover:bg-blue-100 text-blue-700 font-bold">Log Call</button>
                            <button type="button" class="btn-open-lead py-2 px-3 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold">Details →</button>
                        </div>
                    `;
                    card.querySelector('.btn-quick-log').onclick = async () => {
                        await openLeadDetail(l.id);
                        openLeadInteractionModal();
                    };
                    card.querySelector('.btn-open-lead').onclick = () => openLeadDetail(l.id);
                    container.appendChild(card);
                });
            } catch (err) {
                container.innerHTML = `<p class="text-xs text-red-600 col-span-2">${escapeHtml(err.message)}</p>`;
            }
        }

        async function openLeadDetail(leadId) {
            try {
                const res = await fetch(`/api/leads/${leadId}`);
                if (!res.ok) throw new Error("Could not load lead detail");
                const l = await res.json();
                currentSelectedLead = l;

                document.getElementById('detail-lead-number-crumb').innerText = l.lead_number;
                document.getElementById('detail-lead-title').innerText = l.full_name;
                document.getElementById('detail-lead-stage-badge').innerText = l.stage;
                document.getElementById('detail-lead-stage-badge').className = `text-[10px] font-extrabold px-2.5 py-0.5 rounded-full border uppercase ${getStageBadgeStyle(l.stage)}`;
                document.getElementById('detail-lead-priority-badge').innerText = `PRIORITY: ${l.priority}`;

                document.getElementById('ldet-number').innerText = l.lead_number;
                document.getElementById('ldet-entity').innerText = l.entity_type === 'BUSINESS'
                    ? `BUSINESS (Rep: ${l.legal_representative_name || 'N/A'})`
                    : 'INDIVIDUAL';
                document.getElementById('ldet-product').innerText = l.product_interest;
                document.getElementById('ldet-amount').innerText = NuraFinancialEngine.formatMXNFromCents(l.requested_amount_cents);
                document.getElementById('ldet-phone').innerText = l.phone_primary;
                document.getElementById('ldet-email').innerText = l.email || 'Not Provided';
                document.getElementById('ldet-source').innerText = l.source;
                document.getElementById('ldet-followup').innerText = l.next_follow_up_at ? formatDateTimeShort(l.next_follow_up_at) : 'None Scheduled';
                document.getElementById('ldet-notes').innerText = l.summary_notes || 'No summary notes provided.';

                const isConverted = (l.stage === 'CONVERTED' && l.converted_customer_id);
                const canWrite = ['OWNER_ADMIN', 'SALES_AGENT', 'CREDIT_ADVISOR'].includes(currentUser?.role);
                document.getElementById('lead-converted-banner').classList.toggle('hidden', !isConverted);
                document.getElementById('btn-lead-convert').classList.toggle('hidden', isConverted || !canWrite);
                document.getElementById('btn-lead-change-stage').classList.toggle('hidden', isConverted || !canWrite);
                document.getElementById('btn-lead-edit').classList.toggle('hidden', isConverted || !canWrite);
                document.getElementById('btn-lead-log-call').classList.toggle('hidden', !canWrite);

                if (isConverted) {
                    document.getElementById('btn-view-converted-customer').onclick = () => {
                        switchTab('customers');
                        openCustomerDetail(l.converted_customer_id);
                    };
                }

                // Render Stage History
                const histEl = document.getElementById('ldet-stage-history');
                histEl.innerHTML = '';
                (l.stage_history || []).forEach(h => {
                    const div = document.createElement('div');
                    div.className = 'p-2.5 rounded-xl bg-slate-50 border border-slate-100';
                    div.innerHTML = `
                        <div class="flex justify-between items-center">
                            <span class="font-bold text-slate-800">${escapeHtml(h.from_stage || 'START')} → ${escapeHtml(h.to_stage)}</span>
                            <span class="text-[10px] text-slate-400">${escapeHtml(formatDateTimeShort(h.changed_at))}</span>
                        </div>
                        <p class="text-[11px] text-slate-600 mt-0.5">${escapeHtml(h.reason_or_note || '')} • By ${escapeHtml(h.changed_by_name)}</p>
                    `;
                    histEl.appendChild(div);
                });

                // Render Interactions
                const intEl = document.getElementById('ldet-interactions-list');
                intEl.innerHTML = '';
                if (!l.interactions || l.interactions.length === 0) {
                    intEl.innerHTML = '<p class="text-slate-400 py-6 text-center">No calls or notes logged yet. Click "+ Add Entry" to record your first interaction.</p>';
                } else {
                    l.interactions.forEach(i => {
                        const item = document.createElement('div');
                        item.className = 'p-3.5 rounded-2xl bg-slate-50 border border-slate-200/70 space-y-1';
                        item.innerHTML = `
                            <div class="flex items-center justify-between gap-2">
                                <div class="flex items-center gap-1.5">
                                    <span class="text-[10px] font-extrabold px-2 py-0.5 rounded bg-blue-100 text-blue-800">${escapeHtml(i.interaction_type)}</span>
                                    ${i.outcome ? `<span class="text-[10px] font-bold px-2 py-0.5 rounded bg-emerald-100 text-emerald-800">${escapeHtml(i.outcome)}</span>` : ''}
                                </div>
                                <span class="text-[10px] text-slate-400">${escapeHtml(formatDateTimeShort(i.created_at))}</span>
                            </div>
                            <p class="text-xs text-slate-800 whitespace-pre-wrap pt-1">${escapeHtml(i.content)}</p>
                            <p class="text-[10px] text-slate-500 pt-1">Logged by <strong>${escapeHtml(i.created_by_name)}</strong> (${escapeHtml(i.created_by_role)})</p>
                        `;
                        intEl.appendChild(item);
                    });
                }

                document.getElementById('leads-master-view').classList.add('hidden');
                document.getElementById('lead-detail-view').classList.remove('hidden');
            } catch (err) {
                alert(err.message);
            }
        }

        function closeLeadDetailView() {
            document.getElementById('lead-detail-view').classList.add('hidden');
            document.getElementById('leads-master-view').classList.remove('hidden');
        }

        function toggleLeadModalEntityFields() {
            const isBiz = document.getElementById('lf-entity-type').value === 'BUSINESS';
            document.getElementById('lf-business-group').classList.toggle('hidden', !isBiz);
        }

        function openLeadFormModal(mode) {
            leadFormMode = mode;
            document.getElementById('lead-form-error').classList.add('hidden');
            document.getElementById('lead-form').reset();

            if (mode === 'edit' && currentSelectedLead) {
                const l = currentSelectedLead;
                document.getElementById('lead-modal-title').innerText = `Edit Lead ${l.lead_number}`;
                document.getElementById('lf-entity-type').value = l.entity_type || 'INDIVIDUAL';
                document.getElementById('lf-source').value = l.source || 'DIRECT_OUTREACH';
                document.getElementById('lf-priority').value = l.priority || 'MEDIUM';
                document.getElementById('lf-full-name').value = l.full_name || '';
                document.getElementById('lf-legal-rep').value = l.legal_representative_name || '';
                document.getElementById('lf-trade-name').value = l.trade_name || '';
                document.getElementById('lf-phone').value = l.phone_primary || '';
                document.getElementById('lf-email').value = l.email || '';
                document.getElementById('lf-product').value = l.product_interest || 'Personal Loan';
                document.getElementById('lf-amount-pesos').value = ((l.requested_amount_cents || 0) / 100).toFixed(2);
                document.getElementById('lf-rfc').value = l.tax_id_rfc || '';
                document.getElementById('lf-notes').value = l.summary_notes || '';
                if (l.next_follow_up_at) {
                    document.getElementById('lf-followup').value = l.next_follow_up_at.slice(0, 16);
                }
            } else {
                document.getElementById('lead-modal-title').innerText = 'Register New Lead';
                document.getElementById('lf-amount-pesos').value = '50000';
            }

            toggleLeadModalEntityFields();
            document.getElementById('lead-form-modal').classList.remove('hidden');
        }

        function closeLeadFormModal() {
            document.getElementById('lead-form-modal').classList.add('hidden');
        }

        async function handleLeadFormSubmit(e) {
            e.preventDefault();
            const errBox = document.getElementById('lead-form-error');
            const btn = document.getElementById('lf-submit-btn');
            errBox.classList.add('hidden');
            btn.disabled = true;

            const payload = {
                entity_type: document.getElementById('lf-entity-type').value,
                source: document.getElementById('lf-source').value,
                priority: document.getElementById('lf-priority').value,
                full_name: document.getElementById('lf-full-name').value.trim(),
                legal_representative_name: document.getElementById('lf-legal-rep').value.trim() || null,
                trade_name: document.getElementById('lf-trade-name').value.trim() || null,
                phone_primary: document.getElementById('lf-phone').value.trim(),
                email: document.getElementById('lf-email').value.trim() || null,
                product_interest: document.getElementById('lf-product').value,
                requested_amount_pesos: parseFloat(document.getElementById('lf-amount-pesos').value || '0') || 0,
                next_follow_up_at: document.getElementById('lf-followup').value || null,
                tax_id_rfc: document.getElementById('lf-rfc').value.trim() || null,
                summary_notes: document.getElementById('lf-notes').value.trim() || null
            };

            const isEdit = (leadFormMode === 'edit' && currentSelectedLead);
            const url = isEdit ? `/api/leads/${currentSelectedLead.id}` : '/api/leads';
            const method = isEdit ? 'PATCH' : 'POST';

            try {
                const res = await fetch(url, {
                    method,
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCookie('csrf_token') },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
                closeLeadFormModal();
                await loadLeads(currentLeadsPage);
                switchTab('leads');
                await openLeadDetail(data.id);
            } catch (err) {
                errBox.innerText = err.message;
                errBox.classList.remove('hidden');
            } finally {
                btn.disabled = false;
            }
        }

        function openLeadStageModal() {
            if (!currentSelectedLead) return;
            document.getElementById('lead-stage-error').classList.add('hidden');
            document.getElementById('ls-new-stage').value = currentSelectedLead.stage === 'CONVERTED' ? 'APPROVED' : currentSelectedLead.stage;
            document.getElementById('ls-lost-reason').value = currentSelectedLead.lost_reason || '';
            document.getElementById('ls-note').value = '';
            toggleStageModalRequirements();
            document.getElementById('lead-stage-modal').classList.remove('hidden');
        }

        function closeLeadStageModal() {
            document.getElementById('lead-stage-modal').classList.add('hidden');
        }

        function toggleStageModalRequirements() {
            const st = document.getElementById('ls-new-stage').value;
            document.getElementById('ls-lost-group').classList.toggle('hidden', st !== 'LOST');
            document.getElementById('ls-followup-group').classList.toggle('hidden', st !== 'NURTURE');
        }

        async function handleLeadStageSubmit(e) {
            e.preventDefault();
            if (!currentSelectedLead) return;
            const errBox = document.getElementById('lead-stage-error');
            const btn = document.getElementById('ls-submit-btn');
            errBox.classList.add('hidden');
            btn.disabled = true;

            const newStage = document.getElementById('ls-new-stage').value;
            const payload = {
                stage: newStage,
                lost_reason: document.getElementById('ls-lost-reason').value.trim() || null,
                stage_change_note: document.getElementById('ls-note').value.trim() || null
            };
            const followVal = document.getElementById('ls-followup').value;
            if (followVal) payload.next_follow_up_at = followVal;

            try {
                const res = await fetch(`/api/leads/${currentSelectedLead.id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCookie('csrf_token') },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || "Failed to update stage");
                closeLeadStageModal();
                await loadLeads(currentLeadsPage);
                await openLeadDetail(currentSelectedLead.id);
            } catch (err) {
                errBox.innerText = err.message;
                errBox.classList.remove('hidden');
            } finally {
                btn.disabled = false;
            }
        }

        function openLeadInteractionModal() {
            if (!currentSelectedLead) return;
            document.getElementById('lead-int-error').classList.add('hidden');
            document.getElementById('li-type').value = 'CALL';
            document.getElementById('li-outcome').value = 'INTERESTED';
            document.getElementById('li-followup').value = '';
            document.getElementById('li-content').value = '';
            document.getElementById('lead-interaction-modal').classList.remove('hidden');
        }

        function closeLeadInteractionModal() {
            document.getElementById('lead-interaction-modal').classList.add('hidden');
        }

        async function handleLeadInteractionSubmit(e) {
            e.preventDefault();
            if (!currentSelectedLead) return;
            const errBox = document.getElementById('lead-int-error');
            const btn = document.getElementById('li-submit-btn');
            errBox.classList.add('hidden');
            btn.disabled = true;

            const payload = {
                interaction_type: document.getElementById('li-type').value,
                direction: 'OUTBOUND',
                visibility: 'INTERNAL',
                outcome: document.getElementById('li-outcome').value || null,
                content: document.getElementById('li-content').value.trim(),
                next_follow_up_at: document.getElementById('li-followup').value || null
            };

            try {
                const res = await fetch(`/api/leads/${currentSelectedLead.id}/interactions`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCookie('csrf_token') },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || "Could not log interaction");
                closeLeadInteractionModal();
                await loadLeads(currentLeadsPage);
                await openLeadDetail(currentSelectedLead.id);
            } catch (err) {
                errBox.innerText = err.message;
                errBox.classList.remove('hidden');
            } finally {
                btn.disabled = false;
            }
        }

        function toggleConvertModalFields() {
            const isBiz = document.getElementById('lc-entity-type').value === 'BUSINESS';
            document.getElementById('lc-business-group').classList.toggle('hidden', !isBiz);
            document.getElementById('lc-legal-rep').required = isBiz;
        }

        function openLeadConvertModal() {
            if (!currentSelectedLead) return;
            document.getElementById('lead-convert-error').classList.add('hidden');
            document.getElementById('lc-entity-type').value = currentSelectedLead.entity_type || 'INDIVIDUAL';
            document.getElementById('lc-legal-rep').value = currentSelectedLead.legal_representative_name || '';
            document.getElementById('lc-trade-name').value = currentSelectedLead.trade_name || '';
            document.getElementById('lc-rfc').value = currentSelectedLead.tax_id_rfc || '';
            document.getElementById('lc-address').value = '';
            document.getElementById('lc-notes').value = '';
            toggleConvertModalFields();
            document.getElementById('lead-convert-modal').classList.remove('hidden');
        }

        function closeLeadConvertModal() {
            document.getElementById('lead-convert-modal').classList.add('hidden');
        }

        async function handleLeadConvertSubmit(e) {
            e.preventDefault();
            if (!currentSelectedLead) return;
            const errBox = document.getElementById('lead-convert-error');
            const btn = document.getElementById('lc-submit-btn');
            errBox.classList.add('hidden');
            btn.disabled = true;

            const payload = {
                entity_type: document.getElementById('lc-entity-type').value,
                legal_representative_name: document.getElementById('lc-legal-rep').value.trim() || null,
                trade_name: document.getElementById('lc-trade-name').value.trim() || null,
                tax_id_rfc: document.getElementById('lc-rfc').value.trim() || null,
                address_full: document.getElementById('lc-address').value.trim() || null,
                notes: document.getElementById('lc-notes').value.trim() || null
            };

            try {
                const res = await fetch(`/api/leads/${currentSelectedLead.id}/convert`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCookie('csrf_token') },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || "Conversion failed");
                closeLeadConvertModal();
                await Promise.all([loadLeads(currentLeadsPage), loadCustomers(1)]);
                switchTab('customers');
                await openCustomerDetail(data.customer.id);
            } catch (err) {
                errBox.innerText = err.message;
                errBox.classList.remove('hidden');
            } finally {
                btn.disabled = false;
            }
        }

        function switchLeadsSubView(subView) {
            closeLeadDetailView();
            const isPipeline = subView !== 'call-list';
            document.getElementById('leads-pipeline-view').classList.toggle('hidden', !isPipeline);
            document.getElementById('leads-calllist-view').classList.toggle('hidden', isPipeline);

            const pipeBtn = document.getElementById('seg-pipeline-btn');
            const callBtn = document.getElementById('seg-calllist-btn');
            if (isPipeline) {
                pipeBtn.className = 'py-2 px-4 rounded-xl text-xs font-semibold transition-all bg-white text-slate-900 shadow-sm';
                callBtn.className = 'py-2 px-4 rounded-xl text-xs font-semibold transition-all text-slate-500 hover:text-slate-800';
                loadLeads(currentLeadsPage);
            } else {
                callBtn.className = 'py-2 px-4 rounded-xl text-xs font-semibold transition-all bg-white text-slate-900 shadow-sm';
                pipeBtn.className = 'py-2 px-4 rounded-xl text-xs font-semibold transition-all text-slate-500 hover:text-slate-800';
                setCallListBucketFilter(activeCallListBucket);
            }
        }
