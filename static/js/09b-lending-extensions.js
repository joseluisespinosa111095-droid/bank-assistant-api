// --- LENDING EXTENSIONS: RECEIPTS, STATEMENTS, CONTRACT & PAGARÉ, LOANS CSV & COLLECTIONS QUEUE ---

function formatLoanBorrowerPrintLabel(l) {
    if (!l || typeof l !== 'object') return '—';
    const isEs = (NuraI18n.currentLang === 'es');
    const fallbackId = isEs ? `Cliente #${l.customer_id}` : `Customer #${l.customer_id}`;
    const nameStr = (l.customer_name && String(l.customer_name).trim()) ? String(l.customer_name).trim() : '';
    const numStr = (l.customer_number && String(l.customer_number).trim()) ? String(l.customer_number).trim() : '';

    if (nameStr && numStr) {
        return `${nameStr} (${numStr})`;
    }
    return nameStr || numStr || fallbackId;
}

function printPaymentReceipt(btnElement) {
    if (typeof hasModule === 'function' && !hasModule('LENDING')) return;
    const tr = btnElement.closest('tr');
    const tds = tr ? tr.querySelectorAll('td') : null;
    if (!currentSelectedLoan || !tds || tds.length < 6) return;
    const l = currentSelectedLoan;
    const custLabel = formatLoanBorrowerPrintLabel(l);
    const phoneSuffix = (l.customer_phone && String(l.customer_phone).trim())
        ? ` • Tel: <strong>${escapeHtml(String(l.customer_phone).trim())}</strong>`
        : '';

    const content = `
        <div class="space-y-5">
            <div class="flex items-center justify-between bg-slate-50 border border-slate-300 rounded-lg p-4">
                <div>
                    <p class="text-[10px] font-bold uppercase tracking-wider text-slate-500">${escapeHtml(t('print.receipt.title'))}</p>
                    <h2 class="text-lg font-extrabold text-slate-900 mt-0.5">${escapeHtml(l.loan_number)} • ${escapeHtml(tEnum(l.product_name || 'Personal Loan'))}</h2>
                    <p class="text-xs text-slate-600 mt-0.5">${escapeHtml(t('print.receipt.borrower_id'))} <strong>${escapeHtml(custLabel)}</strong>${phoneSuffix} • ${escapeHtml(t('print.receipt.currency'))} <strong>MXN</strong></p>
                </div>
                <div class="text-right text-xs">
                    <p class="text-[10px] font-bold uppercase text-slate-500">${escapeHtml(t('print.receipt.status'))}</p>
                    <div class="mt-1">${tds[5].innerHTML}</div>
                </div>
            </div>

            <div class="grid grid-cols-2 gap-4 text-xs border border-slate-300 rounded-lg p-4">
                <div>
                    <p class="text-[10px] font-bold uppercase text-slate-500 mb-1">${escapeHtml(t('print.receipt.allocations'))}</p>
                    <div class="text-slate-900">${tds[0].innerHTML}</div>
                </div>
                <div>
                    <p class="text-[10px] font-bold uppercase text-slate-500 mb-1">${escapeHtml(t('print.receipt.date_method'))}</p>
                    <div class="text-slate-900">${tds[1].innerHTML}</div>
                </div>
            </div>

            <div>
                <p class="text-[10px] font-bold uppercase tracking-wider text-slate-600 mb-1.5">${escapeHtml(t('print.receipt.breakdown'))}</p>
                <table class="w-full text-left text-xs border border-slate-300">
                    <thead>
                        <tr class="bg-slate-100 text-[10px] font-bold uppercase text-slate-600">
                            <th class="p-2.5 border border-slate-300 text-right">${escapeHtml(t('print.receipt.int_applied'))}</th>
                            <th class="p-2.5 border border-slate-300 text-right">${escapeHtml(t('print.receipt.prin_applied'))}</th>
                            <th class="p-2.5 border border-slate-300 text-right">${escapeHtml(t('print.receipt.total_received'))}</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td class="p-2.5 border border-slate-300 text-right font-mono font-semibold">${escapeHtml(tds[2].innerText)}</td>
                            <td class="p-2.5 border border-slate-300 text-right font-mono font-semibold">${escapeHtml(tds[3].innerText)}</td>
                            <td class="p-2.5 border border-slate-300 text-right font-mono font-extrabold text-slate-900">${escapeHtml(tds[4].innerText)}</td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <div class="grid grid-cols-3 gap-3 text-xs bg-slate-50 border border-slate-300 rounded-lg p-3.5">
                <div>
                    <p class="text-[10px] font-bold uppercase text-slate-500">${escapeHtml(t('print.receipt.unpaid_prin'))}</p>
                    <p class="font-mono font-bold text-slate-900 mt-0.5">${NuraFinancialEngine.formatMXNFromCents(l.unpaid_principal_cents)}</p>
                </div>
                <div>
                    <p class="text-[10px] font-bold uppercase text-slate-500">${escapeHtml(t('print.receipt.rem_int'))}</p>
                    <p class="font-mono font-bold text-slate-900 mt-0.5">${NuraFinancialEngine.formatMXNFromCents(l.remaining_contractual_interest_cents)}</p>
                </div>
                <div>
                    <p class="text-[10px] font-bold uppercase text-slate-500">${escapeHtml(t('print.receipt.rem_total'))}</p>
                    <p class="font-mono font-extrabold text-slate-900 mt-0.5">${NuraFinancialEngine.formatMXNFromCents(l.remaining_contract_total_cents)}</p>
                </div>
            </div>

            <div class="pt-6 mt-4 border-t border-slate-300 flex items-end justify-between text-[10px] text-slate-500">
                <p class="max-w-md leading-relaxed">
                    ${escapeHtml(t('print.receipt.footer_note'))}
                </p>
                <div class="text-center w-48 border-t border-slate-400 pt-1 font-semibold text-slate-700">
                    ${escapeHtml(t('print.receipt.stamp'))}
                </div>
            </div>
        </div>
    `;
    executePrint(t('print.receipt.title'), l.loan_number, content);
}

function printLoanStatement() {
    if (typeof hasModule === 'function' && !hasModule('LENDING')) return;
    if (!currentSelectedLoan) return;
    const l = currentSelectedLoan;
    const custLabel = formatLoanBorrowerPrintLabel(l);
    const phoneSuffix = (l.customer_phone && String(l.customer_phone).trim())
        ? ` • Tel: <strong>${escapeHtml(String(l.customer_phone).trim())}</strong>`
        : '';

    const content = `
        <div class="space-y-5">
            <div class="flex items-center justify-between bg-slate-50 border border-slate-300 rounded-lg p-4">
                <div>
                    <p class="text-[10px] font-bold uppercase tracking-wider text-slate-500">${escapeHtml(t('print.stmt.title'))}</p>
                    <h2 class="text-lg font-extrabold text-slate-900 mt-0.5">${escapeHtml(l.loan_number)} — ${escapeHtml(tEnum(l.product_name || 'Personal Loan'))}</h2>
                    <p class="text-xs text-slate-600 mt-0.5">${escapeHtml(t('print.stmt.borrower'))} <strong>${escapeHtml(custLabel)}</strong>${phoneSuffix} • ${escapeHtml(t('print.stmt.start'))} <strong>${escapeHtml(l.start_date)}</strong> • ${escapeHtml(t('print.stmt.maturity'))} <strong>${escapeHtml(l.maturity_date)}</strong></p>
                </div>
                <div class="text-right text-xs">
                    <p class="text-[10px] font-bold uppercase text-slate-500">${escapeHtml(t('print.stmt.contract_status'))}</p>
                    <p class="text-sm font-extrabold font-mono text-slate-900 mt-0.5">${escapeHtml(tEnum(l.status))}</p>
                </div>
            </div>

            <div class="grid grid-cols-3 gap-3 text-xs border border-slate-300 rounded-lg p-4 bg-white">
                <div>
                    <p class="text-[10px] font-bold text-slate-500 uppercase">${escapeHtml(t('print.stmt.orig_prin'))}</p>
                    <p class="font-mono font-bold text-slate-900 mt-0.5">${NuraFinancialEngine.formatMXNFromCents(l.principal_cents)}</p>
                </div>
                <div>
                    <p class="text-[10px] font-bold text-slate-500 uppercase">${escapeHtml(t('print.stmt.total_repay'))}</p>
                    <p class="font-mono font-bold text-slate-900 mt-0.5">${NuraFinancialEngine.formatMXNFromCents(l.total_repayment_cents)}</p>
                </div>
                <div>
                    <p class="text-[10px] font-bold text-slate-500 uppercase">${escapeHtml(t('print.stmt.total_paid'))}</p>
                    <p class="font-mono font-extrabold text-emerald-700 mt-0.5">${NuraFinancialEngine.formatMXNFromCents(l.amount_paid_cents)}</p>
                </div>
                <div>
                    <p class="text-[10px] font-bold text-slate-500 uppercase">${escapeHtml(t('print.stmt.unpaid_prin'))}</p>
                    <p class="font-mono font-extrabold text-slate-900 mt-0.5">${NuraFinancialEngine.formatMXNFromCents(l.unpaid_principal_cents)}</p>
                </div>
                <div>
                    <p class="text-[10px] font-bold text-slate-500 uppercase">${escapeHtml(t('print.stmt.rem_total'))}</p>
                    <p class="font-mono font-extrabold text-blue-900 mt-0.5">${NuraFinancialEngine.formatMXNFromCents(l.remaining_contract_total_cents)}</p>
                </div>
                <div>
                    <p class="text-[10px] font-bold text-slate-500 uppercase">${escapeHtml(t('print.stmt.payable_today'))}</p>
                    <p class="font-mono font-extrabold text-red-700 mt-0.5">${NuraFinancialEngine.formatMXNFromCents(l.current_payable_amount_cents)}</p>
                </div>
            </div>

            <div>
                <h3 class="font-bold mb-2 text-xs uppercase tracking-wider text-slate-700">${escapeHtml(t('print.stmt.schedule_heading'))}</h3>
                <table class="w-full text-left text-xs border border-collapse">
                    ${document.getElementById('ld-schedule-tbody').parentElement.innerHTML}
                </table>
            </div>
        </div>
    `;
    executePrint(t('print.stmt.title'), l.loan_number, content);
}

async function exportLoansCSV() {
    if (typeof hasModule === 'function' && !hasModule('LENDING')) return;
    const btn = document.getElementById('btn-export-loans-csv');
    const isEs = (NuraI18n.currentLang === 'es');
    const origText = btn ? btn.innerText : '';
    if (btn) {
        btn.disabled = true;
        btn.innerText = isEs ? 'Exportando...' : 'Exporting...';
    }

    try {
        const search = document.getElementById('loan-search-input')?.value.trim() || '';
        const filterKey = (currentLoanFilter || 'all').toLowerCase();
        const isDerived = (filterKey === 'current' || filterKey === 'overdue');

        let allItems = [];
        let page = 1;
        while (page <= 20) {
            const params = new URLSearchParams({ page: String(page), page_size: '100' });
            if (search) params.set('search', search);

            if (isDerived) {
                params.set('status', 'ACTIVE');
            } else if (filterKey !== 'all') {
                const statusMap = {
                    active: 'ACTIVE',
                    draft: 'DRAFT',
                    approved: 'APPROVED',
                    paidoff: 'PAID_OFF',
                    cancelled: 'CANCELLED',
                    defaulted: 'DEFAULTED'
                };
                if (statusMap[filterKey]) params.set('status', statusMap[filterKey]);
            }

            const res = await fetch(`/api/loans?${params.toString()}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            const items = Array.isArray(data.items) ? data.items : [];
            allItems = allItems.concat(items);
            if (allItems.length >= (data.total || 0) || items.length < 100) break;
            page++;
        }

        if (filterKey === 'current') {
            allItems = allItems.filter(l => l && l.status === 'ACTIVE' && Number(l.overdue_amount_cents) === 0);
        } else if (filterKey === 'overdue') {
            allItems = allItems.filter(l => l && l.status === 'ACTIVE' && Number(l.overdue_amount_cents) > 0);
        }

        const headers = isEs
            ? [
                'No. Préstamo', 'No. Cliente', 'Nombre del Cliente', 'Teléfono Cliente',
                'Producto', 'Estatus', 'Días de Mora (DPD)', 'Cuotas Vencidas',
                'Tasa Mensual (%)', 'Plazo (Semanas)', 'Capital Original (MXN)',
                'Capital Insoluto (MXN)', 'Interés Restante (MXN)', 'Total Contrato Restante (MXN)',
                'Vence Hoy (MXN)', 'Monto en Mora (MXN)', 'Exigible Hoy (MXN)',
                'Fecha Inicio', 'Primer Pago', 'Fecha Vencimiento'
              ]
            : [
                'Loan Number', 'Customer Number', 'Customer Name', 'Customer Phone',
                'Product', 'Status', 'Days Past Due (DPD)', 'Overdue Installments',
                'Monthly Rate (%)', 'Term (Weeks)', 'Original Principal (MXN)',
                'Unpaid Principal (MXN)', 'Remaining Interest (MXN)', 'Remaining Contract Total (MXN)',
                'Due Today (MXN)', 'Overdue Amount (MXN)', 'Current Payable Today (MXN)',
                'Start Date', 'First Payment Date', 'Maturity Date'
              ];

        const rows = allItems.map(l => [
            l.loan_number || '',
            l.customer_number || '',
            l.customer_name || '',
            l.customer_phone || '',
            tEnum(l.product_name || 'Personal Loan'),
            tEnum(l.status || 'DRAFT'),
            Number.isSafeInteger(l.days_past_due) ? l.days_past_due : 0,
            Number.isSafeInteger(l.overdue_installments_count) ? l.overdue_installments_count : 0,
            Number.isFinite(l.monthly_interest_rate_percent) ? l.monthly_interest_rate_percent.toFixed(2) : '0.00',
            l.term_installments_count || 0,
            ((Number(l.principal_cents) || 0) / 100).toFixed(2),
            ((Number(l.unpaid_principal_cents) || 0) / 100).toFixed(2),
            ((Number(l.remaining_contractual_interest_cents) || 0) / 100).toFixed(2),
            ((Number(l.remaining_contract_total_cents) || 0) / 100).toFixed(2),
            ((Number(l.due_today_cents) || 0) / 100).toFixed(2),
            ((Number(l.overdue_amount_cents) || 0) / 100).toFixed(2),
            ((Number(l.current_payable_amount_cents) || 0) / 100).toFixed(2),
            l.start_date || '',
            l.first_payment_date || '',
            l.maturity_date || ''
        ]);

        const dateStr = getLocalTodayISO();
        const filePrefix = isEs ? 'NURA_Prestamos' : 'NURA_Loans';
        triggerCsvDownload(`${filePrefix}_${dateStr}.csv`, headers, rows);
    } catch (err) {
        alert((isEs ? 'Error al exportar CSV: ' : 'CSV Export Error: ') + err.message);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerText = origText || (isEs ? '📥 Exportar CSV' : '📥 Export CSV');
        }
    }
}

// =====================================================================
// NURA CRM — CONTRATO DE CRÉDITO SIMPLE + ANEXO A + PAGARÉ MERCANTIL
// =====================================================================

function numberToSpanishWordsMXN(cents) {
    const raw = Number(cents);
    if (!Number.isFinite(raw) || raw < 0) return null;
    const totalCents = Math.round(raw);
    if (totalCents > 9999999999) return null; // > $99,999,999.99

    const pesos = Math.floor(totalCents / 100);
    const centavos = totalCents % 100;

    const UNITS = [
        '', 'UN', 'DOS', 'TRES', 'CUATRO', 'CINCO', 'SEIS', 'SIETE', 'OCHO', 'NUEVE',
        'DIEZ', 'ONCE', 'DOCE', 'TRECE', 'CATORCE', 'QUINCE', 'DIECISÉIS', 'DIECISIETE',
        'DIECIOCHO', 'DIECINUEVE', 'VEINTE', 'VEINTIÚN', 'VEINTIDÓS', 'VEINTITRÉS',
        'VEINTICUATRO', 'VEINTICINCO', 'VEINTISÉIS', 'VEINTISIETE', 'VEINTIOCHO', 'VEINTINUEVE'
    ];
    const TENS = ['', '', '', 'TREINTA', 'CUARENTA', 'CINCUENTA', 'SESENTA', 'SETENTA', 'OCHENTA', 'NOVENTA'];
    const HUNDREDS = [
        '', 'CIENTO', 'DOSCIENTOS', 'TRESCIENTOS', 'CUATROCIENTOS', 'QUINIENTOS',
        'SEISCIENTOS', 'SETECIENTOS', 'OCHOCIENTOS', 'NOVECIENTOS'
    ];

    function below1000(num) {
        if (num === 0) return '';
        if (num === 100) return 'CIEN';
        const h = Math.floor(num / 100);
        const rest = num % 100;
        const parts = [];
        if (h) parts.push(HUNDREDS[h]);
        if (rest) {
            if (rest < 30) {
                parts.push(UNITS[rest]);
            } else {
                const tens = Math.floor(rest / 10);
                const unit = rest % 10;
                parts.push(unit ? `${TENS[tens]} Y ${UNITS[unit]}` : TENS[tens]);
            }
        }
        return parts.join(' ');
    }

    const millions = Math.floor(pesos / 1000000);
    const thousands = Math.floor((pesos % 1000000) / 1000);
    const units = pesos % 1000;

    const parts = [];
    if (millions) parts.push(millions === 1 ? 'UN MILLÓN' : `${below1000(millions)} MILLONES`);
    if (thousands) parts.push(thousands === 1 ? 'MIL' : `${below1000(thousands)} MIL`);
    if (units) parts.push(below1000(units));

    let pesosWords;
    if (pesos === 0) {
        pesosWords = 'CERO PESOS';
    } else if (pesos === 1) {
        pesosWords = 'UN PESO';
    } else if (millions && !thousands && !units) {
        pesosWords = `${parts.join(' ')} DE PESOS`;
    } else {
        pesosWords = `${parts.join(' ')} PESOS`;
    }

    return `${pesosWords} ${String(centavos).padStart(2, '0')}/100 M.N.`;
}

function formatDateLongEs(isoDateStr) {
    const MONTHS = [
        'ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO',
        'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE'
    ];
    const FALLBACK = '____ DE ______________ DE ______';
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(isoDateStr ?? ''));
    if (!m) return FALLBACK;
    const year = Number(m[1]);
    const month = Number(m[2]);
    const day = Number(m[3]);
    const check = new Date(Date.UTC(year, month - 1, day));
    if (check.getUTCFullYear() !== year || check.getUTCMonth() !== month - 1 || check.getUTCDate() !== day) {
        return FALLBACK;
    }
    return `${day} DE ${MONTHS[month - 1]} DE ${year}`;
}

function calculateCustomerAge(dobStr) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(dobStr ?? ''));
    if (!m) return '____';
    const by = Number(m[1]);
    const bm = Number(m[2]);
    const bd = Number(m[3]);
    const now = new Date();
    let age = now.getFullYear() - by;
    const hadBirthday = (now.getMonth() + 1 > bm) || ((now.getMonth() + 1 === bm) && now.getDate() >= bd);
    if (!hadBirthday) age -= 1;
    return (age >= 0 && age < 130) ? String(age) : '____';
}

async function printLoanContractAndPagare() {
    if (typeof hasModule === 'function' && !hasModule('LENDING')) return;
    if (!currentSelectedLoan) return;
    const l = currentSelectedLoan;
    const isEs = (NuraI18n.currentLang === 'es');

    // ------------------------------------------------------------------
    // IDENTIDAD ACREDITANTE ESTRICTA (SIN FALLBACK A tenant_name / legal_name)
    // ------------------------------------------------------------------
    const configuredLenderName = (currentUser && typeof currentUser.lending_entity_name === 'string')
        ? currentUser.lending_entity_name.trim()
        : '';

    if (!configuredLenderName) {
        const missingMsg = isEs
            ? 'No se puede generar el Contrato y Pagaré: la razón social acreditante (lending_entity_name) no está configurada para esta organización. Solicita al SUPERADMIN configurarla en la Consola de Plataforma (Organizaciones → Módulos → LENDING).'
            : 'Cannot generate Contract & Pagaré: the legal lender entity (lending_entity_name) is not configured for this organization. Please ask the Platform SUPERADMIN to configure it in the Platform Console (Organizations → Modules → LENDING).';
        const errBox = document.getElementById('loan-detail-error');
        if (errBox) {
            errBox.innerText = missingMsg;
            errBox.classList.remove('hidden');
        }
        alert(missingMsg);
        return;
    }

    const LENDER_NAME = configuredLenderName;
    const LENDER_ENTITY_TYPE = "MORAL";
    const LENDER_ADDRESS = "____________________________________________________________________";
    const SIGNING_PLACE = "__________________________________________________";
    const DAILY_LATE_FEE_TEXT = "$200.00 MXN (DOSCIENTOS PESOS 00/100 M.N.)";

    const E = (v) => escapeHtml(String(v ?? ''));
    const BLANK_LINE = '______________________________';
    const BLANK_LONG = '____________________________________________________________';

    const scheduleTbody = document.getElementById('ld-schedule-tbody');
    const scheduleTable = scheduleTbody ? scheduleTbody.parentElement : null;
    if (!scheduleTable || scheduleTbody.querySelector('td[colspan]') || !scheduleTbody.querySelector('tr')) {
        alert(isEs
            ? 'El calendario de pagos aún no está disponible. Espera a que cargue la tabla de amortización e inténtalo de nuevo.'
            : 'The payment schedule is not available yet. Wait for the amortization table to load and try again.');
        return;
    }

    let cust = (currentSelectedCustomer && currentSelectedCustomer.id === l.customer_id) ? currentSelectedCustomer : null;
    if (!cust && Number.isSafeInteger(l.customer_id)) {
        try {
            const res = await fetch(`/api/customers/${l.customer_id}`);
            if (res.ok) cust = await res.json();
        } catch (_) {}
    }

    const borrowerName = cust?.full_legal_name || l.customer_name || `Customer #${l.customer_id}`;
    const borrowerRfc = cust?.tax_id_rfc ? E(cust.tax_id_rfc) : BLANK_LINE;
    const borrowerAddress = cust?.address_full ? E(cust.address_full) : BLANK_LONG;
    const legalRep = cust?.legal_representative_name || '';
    const isBusiness = (cust?.entity_type === 'BUSINESS');
    const age = calculateCustomerAge(cust?.date_of_birth);

    const principalFormatted = NuraFinancialEngine.formatMXNFromCents(l.principal_cents);
    const totalInterestFormatted = NuraFinancialEngine.formatMXNFromCents(l.total_interest_cents);
    const totalRepayFormatted = NuraFinancialEngine.formatMXNFromCents(l.total_repayment_cents);
    const regInstallmentFormatted = NuraFinancialEngine.formatMXNFromCents(l.regular_installment_cents);
    const finalInstallmentFormatted = NuraFinancialEngine.formatMXNFromCents(l.final_installment_cents);

    const principalWords = numberToSpanishWordsMXN(l.principal_cents);
    const totalRepayWords = numberToSpanishWordsMXN(l.total_repayment_cents);
    const regInstallmentWords = numberToSpanishWordsMXN(l.regular_installment_cents);

    const hasDistinctFinal = Number.isFinite(Number(l.final_installment_cents))
        && Number(l.final_installment_cents) > 0
        && Number(l.final_installment_cents) !== Number(l.regular_installment_cents);
    const finalInstallmentWords = hasDistinctFinal ? numberToSpanishWordsMXN(l.final_installment_cents) : '';

    if (principalWords === null || totalRepayWords === null || regInstallmentWords === null
        || (hasDistinctFinal && finalInstallmentWords === null)) {
        alert(isEs
            ? 'No se pudo generar el contrato: alguna cantidad del préstamo no es válida o excede el límite soportado.'
            : 'Contract could not be generated: a loan amount is invalid or exceeds the supported limit.');
        return;
    }

    const rateNum = Number(l.monthly_interest_rate_percent);
    const rateFormatted = Number.isFinite(rateNum) ? `${rateNum.toFixed(2)}%` : '0.00%';
    const baseCalculoTexto = (l.interest_calculation_method === 'FLAT_ORIGINAL_PRINCIPAL')
        ? 'CAPITAL ORIGINAL'
        : 'SALDOS INSOLUTOS';

    const startLong = formatDateLongEs(l.start_date);
    const firstPayLong = formatDateLongEs(l.first_payment_date);
    const maturityLong = formatDateLongEs(l.maturity_date);
    const productLabel = tEnum(l.product_name || 'Personal Loan');

    const lettered = (items) => items
        .map((txt, i) => `<p class="pl-4 mb-0.5 text-justify">${String.fromCharCode(97 + i)}) ${txt}</p>`)
        .join('');
    const clause = (ordinal, title, body) =>
        `<p class="mb-1.5 text-justify"><strong>${ordinal}. ${title}.</strong> ${body}</p>`;

    const lenderH = E(LENDER_NAME);
    const borrowerH = E(borrowerName);

    const declAcreditado = isBusiness
        ? [
            `Ser una persona <strong>MORAL</strong> denominada <strong>${borrowerH}</strong>, legalmente constituida conforme a las leyes mexicanas, con capacidad jurídica para obligarse y representada en este acto por <strong>${legalRep ? E(legalRep) : BLANK_LINE}</strong>, quien cuenta con facultades suficientes para obligarla.`,
            `Tener su domicilio en <strong>${borrowerAddress}</strong>.`,
            `Identificarse con RFC / Identificación oficial <strong>${borrowerRfc}</strong>.`,
            `Que los recursos derivados del crédito serán destinados a actividades lícitas / capital de trabajo (<strong>${E(productLabel)}</strong>).`,
            `Que la información y documentación proporcionada a “EL ACREDITANTE” es verdadera y completa.`
        ]
        : [
            `Llamarse <strong>${borrowerH}</strong>, ser de nacionalidad <strong>MEXICANA</strong>, tener <strong>${E(age)}</strong> años de edad y contar con capacidad legal para obligarse${legalRep ? `, actuando por conducto de su representante legal <strong>${E(legalRep)}</strong>` : ''}.`,
            `Tener su domicilio en <strong>${borrowerAddress}</strong>.`,
            `Identificarse con RFC / Identificación oficial <strong>${borrowerRfc}</strong>.`,
            `Que los recursos derivados del crédito serán destinados a actividades lícitas / capital de trabajo (<strong>${E(productLabel)}</strong>).`,
            `Que la información y documentación proporcionada a “EL ACREDITANTE” es verdadera y completa.`
        ];

    const declAcreditante = [
        `Ser una persona <strong>${E(LENDER_ENTITY_TYPE)}</strong>, legalmente constituida conforme a las leyes mexicanas, con capacidad jurídica para celebrar el presente contrato.`,
        `Señalar como domicilio para efectos del presente contrato el ubicado en <strong>${E(LENDER_ADDRESS)}</strong>.`,
        `Contar con facultades suficientes para celebrar el presente contrato.`
    ];

    const finalInstallmentClause = hasDistinctFinal
        ? ` (y una cuota final de ajuste de <strong>${E(finalInstallmentFormatted)} (${E(finalInstallmentWords)})</strong> en caso de diferencia por redondeo)`
        : '';

    const contractSection = `
        <section class="text-[11px] leading-relaxed text-slate-900">
            <div class="text-center mb-3">
                <h1 class="text-sm font-extrabold uppercase tracking-wide">CONTRATO DE CRÉDITO SIMPLE INDIVIDUAL</h1>
                <p class="text-[10px] font-mono text-slate-600 mt-0.5">Folio: <strong>${E(l.loan_number)}</strong></p>
            </div>

            <p class="mb-2 text-justify">
                Que celebran por una parte <strong>${lenderH}</strong>, a quien en lo sucesivo se le denominará <strong>“EL ACREDITANTE”</strong>, y por la otra <strong>${borrowerH}</strong>, a quien en lo sucesivo se le denominará <strong>“EL ACREDITADO”</strong>, conjuntamente denominados <strong>“LAS PARTES”</strong>, al tenor de las siguientes declaraciones y cláusulas:
            </p>

            <h2 class="text-center font-extrabold uppercase tracking-wider mt-3 mb-1.5">DECLARACIONES</h2>

            <p class="mb-0.5 font-bold">I. Declara “EL ACREDITANTE”:</p>
            ${lettered(declAcreditante)}

            <p class="mt-1.5 mb-0.5 font-bold">II. Declara “EL ACREDITADO”:</p>
            ${lettered(declAcreditado)}

            <h2 class="text-center font-extrabold uppercase tracking-wider mt-3 mb-1.5">CLÁUSULAS</h2>

            ${clause('PRIMERA', 'OBJETO DEL CONTRATO',
                `“EL ACREDITANTE” otorga a “EL ACREDITADO” un Crédito Simple Individual por la cantidad de capital principal de <strong>${E(principalFormatted)} (${E(principalWords)})</strong>. “EL ACREDITADO” se obliga a restituir dicho capital más los intereses ordinarios pactados en la Cláusula Quinta, sumando un importe total contractual a pagar de <strong>${E(totalRepayFormatted)} (${E(totalRepayWords)})</strong>.`)}

            ${clause('SEGUNDA', 'DISPOSICIÓN DEL CRÉDITO',
                `“EL ACREDITANTE” entregará el importe del crédito a “EL ACREDITADO” mediante transferencia electrónica de fondos (SPEI), depósito bancario, cheque o efectivo, según conste en el comprobante de desembolso que se integra al expediente del crédito.`)}

            ${clause('TERCERA', 'PLAZO',
                `El crédito tendrá un plazo de <strong>${E(l.assumed_interest_months)} meses (${E(l.term_installments_count)} amortizaciones semanales)</strong>, iniciando el día <strong>${E(startLong)}</strong> y concluyendo el día <strong>${E(maturityLong)}</strong>.`)}

            ${clause('CUARTA', 'FORMA DE PAGO',
                `“EL ACREDITADO” se obliga a realizar <strong>${E(l.term_installments_count)} pagos con periodicidad SEMANAL</strong> de <strong>${E(regInstallmentFormatted)} (${E(regInstallmentWords)})</strong> cada uno${finalInstallmentClause}, hasta cubrir la suma total de <strong>${E(totalRepayFormatted)}</strong>, de acuerdo con el calendario de pagos que forma parte integral del presente contrato como <strong>ANEXO A</strong>. El primer pago deberá realizarse el día <strong>${E(firstPayLong)}</strong> y los subsecuentes en las fechas señaladas en dicho calendario.`)}

            ${clause('QUINTA', 'INTERESES ORDINARIOS',
                `El crédito causará un interés ordinario del <strong>${E(rateFormatted)} MENSUAL</strong>, calculado sobre <strong>${E(baseCalculoTexto)}</strong>, reflejado en el calendario de pagos entregado a “EL ACREDITADO”.`)}

            ${clause('SEXTA', 'INTERESES MORATORIOS',
                `En caso de incumplimiento en cualquiera de los pagos establecidos, “EL ACREDITADO” cubrirá sobre las cantidades vencidas y no pagadas un interés moratorio a razón de <strong>${E(DAILY_LATE_FEE_TEXT)} DIARIOS</strong> durante todo el periodo de mora, sin perjuicio de las demás consecuencias contractuales y legales que correspondan.`)}

            ${clause('SÉPTIMA', 'PAGOS ANTICIPADOS',
                `“EL ACREDITADO” podrá realizar pagos anticipados, totales o parciales, conforme a las condiciones establecidas en el presente contrato y a las políticas de liquidación anticipada autorizadas por “EL ACREDITANTE”.`)}

            ${clause('OCTAVA', 'APLICACIÓN DE PAGOS',
                `Los pagos recibidos se aplicarán en el siguiente orden de prelación: 1) gastos y conceptos exigibles; 2) intereses moratorios vencidos; 3) intereses ordinarios vencidos; 4) capital vencido; y 5) capital insoluto.`)}

            ${clause('NOVENA', 'OBLIGACIONES DEL ACREDITADO',
                `“EL ACREDITADO” se obliga a: a) pagar oportunamente las cantidades establecidas; b) proporcionar información y documentación verdadera; c) informar por escrito cualquier cambio de domicilio, teléfono o datos de contacto; d) destinar el crédito al fin declarado; y e) cumplir las demás obligaciones de este contrato.`)}

            ${clause('DÉCIMA', 'VENCIMIENTO ANTICIPADO',
                `“EL ACREDITANTE” podrá declarar vencido anticipadamente el saldo total insoluto cuando ocurra cualquiera de los siguientes supuestos: a) falta de pago oportuno de <strong>1 (UNA)</strong> o más parcialidades semanales; b) proporcionar información o documentación falsa; c) incumplimiento de obligaciones esenciales del contrato.`)}

            ${clause('DÉCIMA PRIMERA', 'DOMICILIO Y NOTIFICACIONES',
                `Todos los avisos y notificaciones relacionados con este contrato se realizarán en los domicilios señalados por “LAS PARTES” en el apartado de declaraciones.`)}

            ${clause('DÉCIMA SEGUNDA', 'DATOS PERSONALES',
                `“EL ACREDITANTE” tratará los datos personales de “EL ACREDITADO” conforme a la legislación aplicable en materia de protección de datos personales.`)}

            ${clause('DÉCIMA TERCERA', 'JURISDICCIÓN',
                `Para lo relativo a la interpretación y cumplimiento de este contrato, “LAS PARTES” se sujetan a las leyes aplicables y a los tribunales competentes.`)}

            ${clause('DÉCIMA CUARTA', 'INTEGRIDAD DEL CONTRATO',
                `El presente contrato, junto con su Anexo A y el título de crédito (Pagaré) que lo respalda, constituye el acuerdo total entre “LAS PARTES” respecto de su objeto.`)}

            <div class="mt-3" style="break-inside: avoid;">
                <p class="mb-1 text-justify">
                    Leído que fue el presente contrato y enteradas “LAS PARTES” de su contenido y alcance legal, lo firman por duplicado en <strong>${E(SIGNING_PLACE)}</strong>, el día <strong>${E(startLong)}</strong>.
                </p>
                <div class="grid grid-cols-2 gap-10 pt-12 text-[11px]">
                    <div class="text-center border-t border-slate-900 pt-1.5">
                        <p class="font-extrabold uppercase">“EL ACREDITANTE”</p>
                        <p class="font-semibold mt-0.5">${lenderH}</p>
                    </div>
                    <div class="text-center border-t border-slate-900 pt-1.5">
                        <p class="font-extrabold uppercase">“EL ACREDITADO”</p>
                        <p class="font-semibold mt-0.5">${borrowerH}</p>
                    </div>
                </div>
            </div>
        </section>
    `;

    const pagareText = `
        Debo y pagaré incondicionalmente, por este <strong>PAGARÉ</strong>, a la orden de <strong>${lenderH}</strong>, en el domicilio ubicado en <strong>${E(LENDER_ADDRESS)}</strong> o en el lugar que su tenedor me indique por escrito, la cantidad de <strong>${E(totalRepayFormatted)} (${E(totalRepayWords)})</strong>, valor recibido a mi entera satisfacción. Dicha cantidad será cubierta mediante <strong>${E(l.term_installments_count)}</strong> pagos semanales sucesivos conforme al calendario de pagos del Anexo A, cuyo primer pago vence el día <strong>${E(firstPayLong)}</strong> y cuyo vencimiento final es el día <strong>${E(maturityLong)}</strong>.
        La falta de pago oportuno de cualquiera de las parcialidades facultará al tenedor para dar por vencido anticipadamente el saldo total insoluto de este título. Las cantidades vencidas y no pagadas causarán intereses moratorios a razón de <strong>${E(DAILY_LATE_FEE_TEXT)} DIARIOS</strong> desde la fecha de su vencimiento y hasta su total liquidación.
        El presente pagaré se suscribe en <strong>${E(SIGNING_PLACE)}</strong>, el día <strong>${E(startLong)}</strong>.
    `;

    const annexSection = `
        <section class="text-[11px] leading-relaxed text-slate-900" style="page-break-before: always;">
            <div class="text-center mb-3">
                <h2 class="text-sm font-extrabold uppercase tracking-wide">ANEXO A — CALENDARIO DE PAGOS (TABLA DE AMORTIZACIÓN)</h2>
                <p class="text-[10px] font-mono text-slate-600 mt-0.5">Contrato / Folio: <strong>${E(l.loan_number)}</strong> • Acreditado: <strong>${borrowerH}</strong></p>
            </div>

            <div class="border border-slate-400 rounded-lg p-3 mb-3">
                <p class="text-[10px] font-extrabold uppercase tracking-wider text-slate-600 border-b border-slate-300 pb-1 mb-2">Resumen de condiciones financieras pactadas (Moneda: Pesos Mexicanos MXN)</p>
                <div class="grid grid-cols-4 gap-2.5">
                    <div><span class="text-[9px] text-slate-500 uppercase block">Capital Original</span><strong class="font-mono">${E(principalFormatted)}</strong></div>
                    <div><span class="text-[9px] text-slate-500 uppercase block">Tasa Ordinaria Mensual</span><strong class="font-mono">${E(rateFormatted)} / mes</strong></div>
                    <div><span class="text-[9px] text-slate-500 uppercase block">Interés Total</span><strong class="font-mono">${E(totalInterestFormatted)}</strong></div>
                    <div><span class="text-[9px] text-slate-500 uppercase block">Total a Pagar</span><strong class="font-mono">${E(totalRepayFormatted)}</strong></div>
                    <div><span class="text-[9px] text-slate-500 uppercase block">Plazo Semanal</span><strong>${E(l.term_installments_count)} pagos (${E(l.assumed_interest_months)} meses)</strong></div>
                    <div><span class="text-[9px] text-slate-500 uppercase block">Cuota Regular</span><strong class="font-mono">${E(regInstallmentFormatted)}</strong></div>
                    <div><span class="text-[9px] text-slate-500 uppercase block">Base de Cálculo</span><strong>${E(baseCalculoTexto)}</strong></div>
                    <div><span class="text-[9px] text-slate-500 uppercase block">Fechas clave</span><strong class="font-mono text-[10px]">${E(l.start_date)} → ${E(l.first_payment_date)} → ${E(l.maturity_date)}</strong></div>
                </div>
            </div>

            <table class="w-full text-left text-[10px] border border-collapse mb-4">
                ${scheduleTable.innerHTML}
            </table>

            <div class="border-2 border-slate-900 rounded-lg p-4 space-y-3" style="break-inside: avoid;">
                <div class="flex items-center justify-between border-b border-slate-400 pb-2">
                    <div>
                        <h3 class="text-sm font-extrabold uppercase tracking-wider">PAGARÉ MERCANTIL INCONDICIONAL</h3>
                        <p class="text-[10px] text-slate-600 font-mono">Contrato / Folio: <strong>${E(l.loan_number)}</strong> • Fecha de suscripción: <strong>${E(startLong)}</strong></p>
                    </div>
                    <div class="text-right">
                        <span class="text-[10px] font-bold uppercase text-slate-600 block">BUENO POR:</span>
                        <span class="text-sm font-extrabold font-mono">${E(totalRepayFormatted)}</span>
                    </div>
                </div>

                <p class="text-[11px] leading-relaxed text-justify text-slate-900">${pagareText}</p>

                <div class="grid grid-cols-3 gap-6 pt-10 text-[10px]">
                    <div class="text-center border-t border-slate-900 pt-1.5">
                        <p class="font-extrabold uppercase">EL SUSCRIPTOR / DEUDOR PRINCIPAL</p>
                        <p class="font-semibold mt-0.5">${borrowerH}</p>
                        <p class="text-slate-600 mt-0.5">Firma autógrafa</p>
                    </div>
                    <div class="text-center border-t border-slate-900 pt-1.5">
                        <p class="font-extrabold uppercase">AVAL / OBLIGADO SOLIDARIO</p>
                        <p class="text-slate-600 mt-0.5">Nombre y firma autógrafa</p>
                        <p class="text-slate-600 mt-2">Domicilio: ______________________</p>
                    </div>
                    <div class="text-center border-t border-slate-900 pt-1.5">
                        <p class="font-extrabold uppercase">AUTORIZACIÓN Y CONTROL ${lenderH}</p>
                        <p class="text-slate-600 mt-0.5">Mesa de control y expediente</p>
                    </div>
                </div>
            </div>
        </section>
    `;

    const content = `<div>${contractSection}${annexSection}</div>`;

    executePrint(isEs ? 'Contrato y Pagaré' : 'Contract & Promissory Note', l.loan_number, content);
}

setTimeout(() => {
    const ldActionsContainer = document.getElementById('ld-lifecycle-actions');
    if (ldActionsContainer && !document.getElementById('btn-print-statement')) {
        const parent = ldActionsContainer.parentElement;

        const printContractBtn = document.createElement('button');
        printContractBtn.id = 'btn-print-contract';
        printContractBtn.type = 'button';
        printContractBtn.setAttribute('data-i18n', 'loans.detail.print_contract');
        printContractBtn.className = "py-2 px-3.5 rounded-xl bg-indigo-50 hover:bg-indigo-100 text-indigo-800 border border-indigo-200 font-semibold text-xs flex items-center gap-1.5 shadow-sm transition";
        printContractBtn.innerText = (NuraI18n.currentLang === 'es') ? '📄 Contrato y Pagaré' : '📄 Contract & Pagaré';
        printContractBtn.onclick = printLoanContractAndPagare;

        const printStmtBtn = document.createElement('button');
        printStmtBtn.id = 'btn-print-statement';
        printStmtBtn.type = 'button';
        printStmtBtn.setAttribute('data-i18n', 'loans.detail.print_statement');
        printStmtBtn.className = "py-2 px-3.5 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold text-xs flex items-center gap-1.5 shadow-sm transition";
        printStmtBtn.innerText = t('loans.detail.print_statement');
        printStmtBtn.onclick = printLoanStatement;

        parent.insertBefore(printStmtBtn, parent.firstChild);
        parent.insertBefore(printContractBtn, printStmtBtn);
    }
}, 500);

if (typeof window.loadLoanPayments === 'function') {
    const originalLoadPayments = window.loadLoanPayments;
    window.loadLoanPayments = async function(loanId) {
        if (typeof hasModule === 'function' && !hasModule('LENDING')) return;
        await originalLoadPayments(loanId);
        const tbody = document.getElementById('ld-payments-tbody');
        if (!tbody) return;
        const rows = tbody.querySelectorAll('tr');
        rows.forEach(row => {
            if (row.children.length === 7 && !row.querySelector('td[colspan]')) {
                const actionTd = row.children[6];
                if (actionTd.querySelector('.btn-print-receipt')) return;

                const printBtn = document.createElement('button');
                printBtn.type = 'button';
                printBtn.setAttribute('data-i18n', 'loans.payments.receipt_btn');
                printBtn.className = 'btn-print-receipt text-[10px] text-slate-700 hover:text-slate-900 ml-1.5 font-bold bg-slate-100 hover:bg-slate-200 border border-slate-200 px-2.5 py-1 rounded-lg transition';
                printBtn.title = (NuraI18n.currentLang === 'es') ? 'Imprimir Recibo Oficial' : 'Print Official Receipt';
                printBtn.innerText = t('loans.payments.receipt_btn');
                printBtn.addEventListener('click', function () {
                    printPaymentReceipt(this);
                });

                actionTd.appendChild(document.createTextNode(' '));
                actionTd.appendChild(printBtn);
            }
        });
    };
}

// --- BLOQUE J: COBRANZA, SEGUIMIENTO DE MORA Y CARTERA VENCIDA (DEFAULTED) ---
let activeLoansSubView = 'book';
let activeCollectionsBucket = 'ALL';

function switchLoansSubView(subView, triggerLoad = true) {
    if (typeof hasModule === 'function' && !hasModule('LENDING')) return;
    activeLoansSubView = (subView === 'collections') ? 'collections' : 'book';
    const bookView = document.getElementById('loans-book-view');
    const colView = document.getElementById('loans-collections-view');
    const bookBtn = document.getElementById('seg-loans-book-btn');
    const colBtn = document.getElementById('seg-loans-col-btn');

    if (activeLoansSubView === 'collections') {
        bookView?.classList.add('hidden');
        colView?.classList.remove('hidden');
        if (colBtn) colBtn.className = 'py-2 px-3.5 rounded-xl text-xs font-semibold transition-all bg-white text-slate-900 shadow-sm';
        if (bookBtn) bookBtn.className = 'py-2 px-3.5 rounded-xl text-xs font-semibold transition-all text-slate-600 hover:text-slate-900';
        if (triggerLoad) loadCollectionsQueue(activeCollectionsBucket);
    } else {
        colView?.classList.add('hidden');
        bookView?.classList.remove('hidden');
        if (bookBtn) bookBtn.className = 'py-2 px-3.5 rounded-xl text-xs font-semibold transition-all bg-white text-slate-900 shadow-sm';
        if (colBtn) colBtn.className = 'py-2 px-3.5 rounded-xl text-xs font-semibold transition-all text-slate-600 hover:text-slate-900';
        if (triggerLoad && typeof loadLoans === 'function') loadLoans(currentLoansPage || 1);
    }
}

function refreshLoansOrCollectionsView() {
    if (typeof hasModule === 'function' && !hasModule('LENDING')) return;
    if (activeLoansSubView === 'collections') {
        loadCollectionsQueue(activeCollectionsBucket);
    } else if (typeof loadLoans === 'function') {
        loadLoans(1);
    }
}

function openCollectionsQueueFromDashboard() {
    if (typeof hasModule === 'function' && !hasModule('LENDING')) return;
    switchTab('loans');
    if (typeof closeLoanDetailView === 'function') closeLoanDetailView();
    switchLoansSubView('collections', true);
}

async function loadCollectionsQueue(bucket = 'ALL') {
    if (typeof hasModule === 'function' && !hasModule('LENDING')) return;
    activeCollectionsBucket = bucket;
    const cardsBox = document.getElementById('collections-queue-cards');
    if (!cardsBox) return;

    const bucketLabels = {
        ALL: 'Queue: All Actionable Delinquent & Due Loans',
        OVERDUE: 'Queue: Active Overdue Loans (Immediate Collection Required)',
        DUE_TODAY: 'Queue: Installments Maturing Today',
        DEFAULTED: 'Queue: Defaulted Portfolio (Cartera Vencida / Legal Recovery)'
    };
    const lblEl = document.getElementById('cq-active-bucket-label');
    if (lblEl) lblEl.innerText = bucketLabels[bucket] || bucketLabels.ALL;

    ['ALL', 'OVERDUE', 'DUE_TODAY', 'DEFAULTED'].forEach(k => {
        const pill = document.getElementById('cq-pill-' + k);
        if (!pill) return;
        if (k === bucket) {
            pill.className = 'px-3.5 py-1.5 rounded-xl font-semibold bg-slate-900 text-white';
        } else if (k === 'OVERDUE') {
            pill.className = 'px-3.5 py-1.5 rounded-xl font-semibold bg-red-50 text-red-700 hover:bg-red-100';
        } else if (k === 'DUE_TODAY') {
            pill.className = 'px-3.5 py-1.5 rounded-xl font-semibold bg-amber-50 text-amber-800 hover:bg-amber-100';
        } else if (k === 'DEFAULTED') {
            pill.className = 'px-3.5 py-1.5 rounded-xl font-semibold bg-rose-50 text-rose-800 hover:bg-rose-100';
        } else {
            pill.className = 'px-3.5 py-1.5 rounded-xl font-semibold bg-slate-100 text-slate-700 hover:bg-slate-200';
        }
    });

    cardsBox.innerHTML = '<p class="col-span-2 py-8 text-center text-xs text-slate-400">Loading collections queue from PostgreSQL...</p>';

    try {
        const res = await fetch(`/api/collections/queue?bucket=${encodeURIComponent(bucket)}`);
        if (res.status === 401) {
            mostrarAuthView();
            return;
        }
        if (!res.ok) throw new Error('Could not load collections queue.');
        const data = await res.json();

        const counts = data.counts || {};
        const totals = data.totals || {};
        document.getElementById('cq-count-overdue').innerText = String(counts.overdue_active || 0);
        document.getElementById('cq-count-today').innerText = String(counts.due_today_active || 0);
        document.getElementById('cq-count-defaulted').innerText = String(counts.defaulted || 0);
        document.getElementById('cq-sum-overdue').innerText = NuraFinancialEngine.formatMXNFromCents(totals.overdue_active_cents || 0);
        document.getElementById('cq-sum-defaulted').innerText = NuraFinancialEngine.formatMXNFromCents(totals.defaulted_remaining_cents || 0);

        const items = Array.isArray(data.items) ? data.items : [];
        cardsBox.innerHTML = '';

        if (items.length === 0) {
            cardsBox.innerHTML = `
                <div class="col-span-2 fintech-card p-8 bg-white text-center space-y-1.5">
                    <p class="text-sm font-bold text-slate-900">No delinquent or due loans in this queue</p>
                    <p class="text-xs text-slate-500">All scheduled installments for this filter are up to date.</p>
                </div>`;
            return;
        }

        items.forEach(item => {
            const card = document.createElement('div');
            card.className = 'fintech-card p-4 bg-white space-y-3 flex flex-col justify-between border-l-4 ' +
                (item.status === 'DEFAULTED' ? 'border-l-rose-700' : (item.overdue_amount_cents > 0 ? 'border-l-red-600' : 'border-l-amber-500'));

            const dpdBadge = item.days_past_due > 0
                ? `<span class="text-[10px] font-extrabold font-mono px-2 py-0.5 rounded-full bg-red-100 text-red-800 border border-red-200">${item.days_past_due}d DPD (${item.overdue_installments_count} inst.)</span>`
                : `<span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-amber-100 text-amber-800">Due Today</span>`;

            const lastNoteHtml = item.last_collection_note
                ? `<div class="p-2.5 rounded-xl bg-slate-50 border border-slate-200/70 text-[11px] text-slate-600">
                       <p class="font-bold text-slate-800 text-[10px] uppercase">Last Interaction (${escapeHtml(item.last_collection_outcome || 'NOTE')})</p>
                       <p class="truncate mt-0.5">${escapeHtml(item.last_collection_note)}</p>
                   </div>`
                : `<p class="text-[11px] text-slate-400 italic">No collection calls logged yet.</p>`;

            card.innerHTML = `
                <div class="space-y-2.5">
                    <div class="flex items-start justify-between gap-2">
                        <div>
                            <div class="flex items-center gap-1.5 flex-wrap">
                                <span class="font-mono font-extrabold text-blue-700 text-xs">${escapeHtml(item.loan_number)}</span>
                                <span class="text-[10px] font-bold px-2 py-0.5 rounded-full border ${getLoanStatusBadgeStyle(item.status)}">${escapeHtml(item.status)}</span>
                                ${dpdBadge}
                            </div>
                            <h4 class="text-sm font-extrabold text-slate-900 mt-1">${escapeHtml(item.customer_name)}</h4>
                            <p class="text-xs text-slate-500 font-medium">${escapeHtml(item.customer_number)} &bull; 📞 ${escapeHtml(item.customer_phone || 'N/A')}</p>
                        </div>
                        <div class="text-right">
                            <p class="text-[10px] font-bold text-red-600 uppercase">Current Payable</p>
                            <p class="text-sm font-extrabold font-mono text-red-600">${NuraFinancialEngine.formatMXNFromCents(item.current_payable_amount_cents)}</p>
                            <p class="text-[10px] text-slate-500 font-mono">Rem. Total: ${NuraFinancialEngine.formatMXNFromCents(item.remaining_contract_total_cents)}</p>
                        </div>
                    </div>
                    ${lastNoteHtml}
                </div>
                <div class="grid grid-cols-3 gap-2 pt-2.5 border-t border-slate-100 text-xs">
                    <button type="button" class="btn-cq-log py-2 px-3 rounded-xl bg-blue-50 hover:bg-blue-100 text-blue-700 font-bold transition">📞 Log Call</button>
                    <button type="button" class="btn-cq-pay py-2 px-3 rounded-xl ${item.status === 'ACTIVE' ? 'bg-emerald-50 hover:bg-emerald-100 text-emerald-700 font-bold' : 'bg-slate-100 text-slate-400 cursor-not-allowed'}" ${item.status !== 'ACTIVE' ? 'disabled' : ''}>💳 Payment</button>
                    <button type="button" class="btn-cq-open py-2 px-3 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold transition">Inspect →</button>
                </div>
            `;

            card.querySelector('.btn-cq-log').onclick = async () => {
                await openLoanDetailFull(item.id);
                openLoanCollectionModal();
            };
            const payBtn = card.querySelector('.btn-cq-pay');
            if (item.status === 'ACTIVE') {
                payBtn.onclick = async () => {
                    await openLoanDetailFull(item.id);
                    openLoanPaymentModal();
                };
            }
            card.querySelector('.btn-cq-open').onclick = () => openLoanDetailFull(item.id);

            cardsBox.appendChild(card);
        });
    } catch (err) {
        cardsBox.innerHTML = `<p class="col-span-2 py-6 text-center text-xs text-red-600">${escapeHtml(err.message)}</p>`;
    }
}

function openLoanCollectionModal() {
    if (!currentSelectedLoan) return;
    const l = currentSelectedLoan;
    document.getElementById('loan-collection-error')?.classList.add('hidden');
    document.getElementById('loan-collection-form')?.reset();

    document.getElementById('lcol-loan-header').innerText = `${l.loan_number} • ${l.product_name || 'Loan'}`;
    document.getElementById('lcol-customer-header').innerText = `${l.customer_name || 'Customer #' + l.customer_id} • Phone: ${l.customer_phone || 'N/A'}`;
    document.getElementById('lcol-payable').innerText = NuraFinancialEngine.formatMXNFromCents(l.current_payable_amount_cents || 0);

    document.getElementById('loan-collection-modal')?.classList.remove('hidden');
}

function closeLoanCollectionModal() {
    document.getElementById('loan-collection-modal')?.classList.add('hidden');
}

async function handleLoanCollectionSubmit(e) {
    e.preventDefault();
    if (!currentSelectedLoan) return;
    const errBox = document.getElementById('loan-collection-error');
    const submitBtn = document.getElementById('lcol-submit-btn');
    errBox?.classList.add('hidden');
    submitBtn.disabled = true;

    const promisedRaw = parseFloat(document.getElementById('lcol-promised').value || '0');
    const payload = {
        interaction_type: document.getElementById('lcol-type').value,
        outcome: document.getElementById('lcol-outcome').value,
        promised_amount_pesos: (Number.isFinite(promisedRaw) && promisedRaw > 0) ? promisedRaw : null,
        next_follow_up_at: document.getElementById('lcol-followup').value || null,
        notes: document.getElementById('lcol-notes').value.trim()
    };

    try {
        const res = await fetch(`/api/loans/${currentSelectedLoan.id}/collections-log`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': getCookie('csrf_token')
            },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Could not record collection log.');

        closeLoanCollectionModal();
        const succBox = document.getElementById('loan-detail-success');
        const succText = document.getElementById('loan-detail-success-text');
        if (succBox && succText) {
            succText.innerText = `Collection log recorded in customer dossier (${data.interaction_type} • ${data.outcome}).`;
            succBox.classList.remove('hidden');
        }
    } catch (err) {
        if (errBox) {
            errBox.innerText = err.message;
            errBox.classList.remove('hidden');
        }
    } finally {
        submitBtn.disabled = false;
    }
}

function openLoanDefaultModal() {
    if (!currentSelectedLoan) return;
    const l = currentSelectedLoan;
    document.getElementById('loan-default-error')?.classList.add('hidden');
    document.getElementById('loan-default-form')?.reset();

    document.getElementById('ldef-loan-header').innerText = `${l.loan_number} (${l.product_name || 'Loan'})`;
    document.getElementById('ldef-customer-label').innerText = l.customer_name || `Customer #${l.customer_id}`;
    document.getElementById('ldef-principal').innerText = NuraFinancialEngine.formatMXNFromCents(l.unpaid_principal_cents || 0);
    document.getElementById('ldef-rem-total').innerText = NuraFinancialEngine.formatMXNFromCents(l.remaining_contract_total_cents || 0);
    document.getElementById('ldef-overdue').innerText = NuraFinancialEngine.formatMXNFromCents(l.overdue_amount_cents || 0);
    document.getElementById('ldef-dpd').innerText = `${l.days_past_due || 0} days`;

    document.getElementById('loan-default-modal')?.classList.remove('hidden');
}

function closeLoanDefaultModal() {
    document.getElementById('loan-default-modal')?.classList.add('hidden');
}

async function handleLoanDefaultSubmit(e) {
    e.preventDefault();
    if (!currentSelectedLoan) return;
    const errBox = document.getElementById('loan-default-error');
    const submitBtn = document.getElementById('ldef-submit-btn');
    errBox?.classList.add('hidden');
    submitBtn.disabled = true;

    const payload = {
        confirm_default: document.getElementById('ldef-confirm-check').checked,
        default_reason: document.getElementById('ldef-reason').value.trim()
    };

    try {
        const res = await fetch(`/api/loans/${currentSelectedLoan.id}/default`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': getCookie('csrf_token')
            },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Could not transition loan to DEFAULTED.');

        closeLoanDefaultModal();
        await Promise.all([
            openLoanDetailFull(currentSelectedLoan.id),
            loadLoans(currentLoansPage || 1),
            loadDashboardSummary()
        ]);

        const succBox = document.getElementById('loan-detail-success');
        const succText = document.getElementById('loan-detail-success-text');
        if (succBox && succText) {
            succText.innerText = `Loan ${data.loan_number} has been transitioned to DEFAULTED and logged in the audit trail.`;
            succBox.classList.remove('hidden');
        }
    } catch (err) {
        if (errBox) {
            errBox.innerText = err.message;
            errBox.classList.remove('hidden');
        }
    } finally {
        submitBtn.disabled = false;
    }
}

if (typeof window.updateLoanLifecycleActionsUI === 'function') {
    const _origUpdateLifecycleUI = window.updateLoanLifecycleActionsUI;
    window.updateLoanLifecycleActionsUI = function(loanData) {
        _origUpdateLifecycleUI(loanData);
        const colBtn = document.getElementById('ld-btn-collection');
        const defBtn = document.getElementById('ld-btn-default');
        const actionsWrap = document.getElementById('ld-lifecycle-actions');
        const role = currentUser?.role || '';

        const canLogCollection = (
            loanData &&
            ['ACTIVE', 'DEFAULTED'].includes(loanData.status) &&
            ['OWNER_ADMIN', 'CREDIT_ADVISOR', 'OPERATIONS_OFFICER'].includes(role)
        );
        const canMarkDefault = (
            loanData &&
            loanData.status === 'ACTIVE' &&
            Number(loanData.remaining_contract_total_cents) > 0 &&
            ['OWNER_ADMIN', 'CREDIT_ADVISOR'].includes(role)
        );

        if (colBtn) colBtn.classList.toggle('hidden', !canLogCollection);
        if (defBtn) defBtn.classList.toggle('hidden', !canMarkDefault);
        if ((canLogCollection || canMarkDefault) && actionsWrap) {
            actionsWrap.classList.remove('hidden');
        }
    };
}
