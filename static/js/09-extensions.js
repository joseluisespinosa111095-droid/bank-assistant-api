// --- CORE EXTENSIONS: KYC DOCUMENTS, SHARED PRINT/CSV UTILS & DYNAMIC I18N OBSERVER ---

// --- BLOQUE H: DOCUMENTOS KYC (UX REFINED) ---
function getDocumentTypeBadgeHtml(docType) {
    const map = {
        INE_ID: { label: 'INE / Official ID', cls: 'bg-blue-50 text-blue-700 border-blue-200' },
        PROOF_OF_ADDRESS: { label: 'Proof of Address', cls: 'bg-indigo-50 text-indigo-700 border-indigo-200' },
        BANK_STATEMENT: { label: 'Bank Statement', cls: 'bg-amber-50 text-amber-800 border-amber-200' },
        SIGNED_CONTRACT: { label: 'Signed Contract', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
        OTHER: { label: 'Other Document', cls: 'bg-slate-100 text-slate-700 border-slate-200' }
    };
    const item = map[docType] || { label: docType || 'Document', cls: 'bg-slate-100 text-slate-700 border-slate-200' };
    return `<span class="inline-block text-[10px] font-extrabold px-2.5 py-0.5 rounded-full border ${item.cls}">${escapeHtml(item.label)}</span>`;
}

function copyDocHashToClipboard(hashStr, btnEl) {
    if (!hashStr || !navigator.clipboard) return;
    navigator.clipboard.writeText(hashStr).then(() => {
        const prev = btnEl.innerText;
        btnEl.innerText = '✓ Hash Copied';
        setTimeout(() => { btnEl.innerText = prev; }, 1400);
    }).catch(() => {});
}

async function loadCustomerDocuments(customerId) {
    const tbody = document.getElementById('cust-docs-tbody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="5" class="py-6 text-center text-slate-400">Loading documents from PostgreSQL...</td></tr>';
    try {
        const res = await fetch(`/api/customers/${customerId}/documents`);
        if (!res.ok) throw new Error('Failed to load customer documents');
        const docs = await res.json();
        tbody.innerHTML = '';
        if (!Array.isArray(docs) || docs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="py-8 text-center text-slate-400">No KYC or contractual documents uploaded for this customer yet.</td></tr>';
            return;
        }
        docs.forEach(d => {
            const sizeKb = (Number(d.file_size_bytes || 0) / 1024).toFixed(1);
            const rawHash = String(d.file_hash || '');
            const shortHash = rawHash.length > 16
                ? `${rawHash.slice(0, 8)}…${rawHash.slice(-6)}`
                : (rawHash || 'N/A');

            const tr = document.createElement('tr');
            tr.className = 'hover:bg-slate-50/90 transition-colors';
            tr.innerHTML = `
                <td class="py-3 px-3.5">${getDocumentTypeBadgeHtml(d.document_type)}</td>
                <td class="py-3 px-3.5 font-semibold text-slate-800 max-w-[220px] truncate" title="${escapeHtml(d.original_filename)}">${escapeHtml(d.original_filename)}</td>
                <td class="py-3 px-3.5">
                    <div class="flex items-center gap-2">
                        <span class="font-mono font-bold text-slate-700 text-[11px]">${sizeKb} KB</span>
                        <button type="button" onclick="copyDocHashToClipboard('${escapeHtml(rawHash)}', this)" class="text-[9px] font-mono bg-slate-100 hover:bg-blue-50 text-slate-500 hover:text-blue-700 border border-slate-200 px-1.5 py-0.5 rounded transition" title="SHA-256: ${escapeHtml(rawHash)} (Click to copy)">
                            SHA: ${escapeHtml(shortHash)}
                        </button>
                    </div>
                </td>
                <td class="py-3 px-3.5">
                    <p class="font-semibold text-slate-800 text-[11px]">${escapeHtml(d.uploaded_by_name)}</p>
                    <p class="text-[10px] text-slate-400">${escapeHtml(formatDateTimeShort(d.created_at))}</p>
                </td>
                <td class="py-3 px-3.5 text-right space-x-1.5">
                    <button type="button" onclick="downloadDocument(${customerId}, ${d.id})" class="py-1 px-2.5 rounded-lg bg-blue-50 hover:bg-blue-100 text-blue-700 font-bold text-[11px] transition">View / Download</button>
                    <button type="button" onclick="deleteDocument(${customerId}, ${d.id})" class="py-1 px-2.5 rounded-lg bg-red-50 hover:bg-red-100 text-red-600 font-bold text-[11px] transition">Delete</button>
                </td>`;
            tbody.appendChild(tr);
        });
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5" class="py-4 text-center text-red-600">${escapeHtml(err.message)}</td></tr>`;
    }
}

async function handleDocumentUpload(e) {
    e.preventDefault();
    if (!currentSelectedCustomer) return;
    const btn = document.getElementById('doc-upload-btn');
    const errBox = document.getElementById('cust-doc-error');
    const fileInput = document.getElementById('doc-upload-file');
    errBox.classList.add('hidden');

    if (fileInput.files.length === 0) return;
    const file = fileInput.files[0];
    if (file.size > 10 * 1024 * 1024) {
        errBox.innerText = "File exceeds 10MB limit.";
        errBox.classList.remove('hidden');
        return;
    }

    const formData = new FormData();
    formData.append('document_type', document.getElementById('doc-upload-type').value);
    formData.append('file', file);

    btn.disabled = true;
    btn.innerText = 'Uploading...';
    try {
        const res = await fetch(`/api/customers/${currentSelectedCustomer.id}/documents`, {
            method: 'POST',
            headers: { 'X-CSRF-Token': getCookie('csrf_token') },
            body: formData
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Upload failed");
        document.getElementById('document-upload-form').reset();
        await loadCustomerDocuments(currentSelectedCustomer.id);
    } catch (err) {
        errBox.innerText = err.message;
        errBox.classList.remove('hidden');
    } finally {
        btn.disabled = false;
        btn.innerText = 'Upload to DB';
    }
}

function downloadDocument(customerId, docId) {
    window.open(`/api/customers/${customerId}/documents/${docId}/download`, '_blank');
}

async function deleteDocument(customerId, docId) {
    if (!confirm('Permanently delete this document from the customer dossier?')) return;
    try {
        const res = await fetch(`/api/customers/${customerId}/documents/${docId}`, {
            method: 'DELETE',
            headers: { 'X-CSRF-Token': getCookie('csrf_token') }
        });
        if (!res.ok) throw new Error('Delete failed');
        await loadCustomerDocuments(customerId);
    } catch (err) {
        alert(err.message);
    }
}

if (typeof window.openCustomerDetail === 'function') {
    const originalOpenCust = window.openCustomerDetail;
    window.openCustomerDetail = async function(id) {
        await originalOpenCust(id);
        await loadCustomerDocuments(id);
    };
}

// --- UTILIDADES COMPARTIDAS DE IMPRESIÓN Y EXPORTACIÓN CSV ---
function executePrint(title, metaId, contentHtml) {
    const appView = document.getElementById('app-view');
    appView.classList.add('no-print');

    document.getElementById('print-meta-date').innerText = new Date().toLocaleString(NuraI18n.getLocale(), {
        year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit'
    });
    document.getElementById('print-meta-id').innerText = metaId;
    document.getElementById('print-view-content').innerHTML = contentHtml;

    window.print();

    appView.classList.remove('no-print');
}

// --- EXPORTACIÓN A EXCEL / CSV (UTF-8 BOM CON PREVENCIÓN DE INYECCIÓN DE FÓRMULAS) ---
function escapeCsvCell(val) {
    if (val === null || val === undefined) return '""';
    let str = String(val).replace(/\r?\n/g, ' ').trim();
    if (/^[=+\-@]/.test(str) && !/^-[0-9]+(\.[0-9]+)?$/.test(str)) {
        str = "'" + str;
    }
    return `"${str.replace(/"/g, '""')}"`;
}

function triggerCsvDownload(filename, headersRow, dataRows) {
    const csvLines = [
        headersRow.map(escapeCsvCell).join(','),
        ...dataRows.map(row => row.map(escapeCsvCell).join(','))
    ];
    // \uFEFF guarantees Microsoft Excel opens UTF-8 accents and special characters properly
    const blob = new Blob(['\uFEFF' + csvLines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', filename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(() => URL.revokeObjectURL(url), 1500);
}

async function exportCustomersCSV() {
    const btn = document.getElementById('btn-export-customers-csv');
    const isEs = (NuraI18n.currentLang === 'es');
    const origText = btn ? btn.innerText : '';
    if (btn) {
        btn.disabled = true;
        btn.innerText = isEs ? 'Exportando...' : 'Exporting...';
    }

    try {
        const search = document.getElementById('customer-search-input')?.value.trim() || '';
        const status = document.getElementById('customer-status-filter')?.value || 'ALL';
        const sort = document.getElementById('customer-sort-select')?.value || 'CREATED_DESC';

        let allItems = [];
        let page = 1;
        while (page <= 20) {
            const params = new URLSearchParams({ page: String(page), page_size: '100', sort });
            if (search) params.set('search', search);
            if (status && status !== 'ALL') params.set('status', status);

            const res = await fetch(`/api/customers?${params.toString()}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            const items = Array.isArray(data.items) ? data.items : [];
            allItems = allItems.concat(items);
            if (allItems.length >= (data.total || 0) || items.length < 100) break;
            page++;
        }

        const headers = isEs
            ? [
                'No. Cliente', 'Nombre Legal / Razón Social', 'Tipo de Persona', 'Estatus',
                'Verificación KYC', 'Teléfono Principal', 'Teléfono Secundario', 'Correo Electrónico',
                'RFC', 'Cliente Desde'
              ]
            : [
                'Customer Number', 'Full Legal Name', 'Entity Type', 'Customer Status',
                'KYC Verification', 'Primary Phone', 'Secondary Phone', 'Email Address',
                'Tax ID (RFC)', 'Customer Since'
              ];

        const rows = allItems.map(c => [
            c.customer_number || '',
            c.full_legal_name || '',
            tEnum(c.entity_type || 'INDIVIDUAL'),
            tEnum(c.status || 'ACTIVE'),
            tEnum(c.verification_status || 'PENDING_VERIFICATION'),
            c.phone_primary || '',
            c.phone_secondary || '',
            c.email || '',
            c.tax_id_rfc || '',
            c.customer_since ? String(c.customer_since).slice(0, 10) : ''
        ]);

        const dateStr = getLocalTodayISO();
        const filePrefix = isEs ? 'NURA_Clientes' : 'NURA_Customers';
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

// --- DYNAMIC CONTENT & PATTERN TRANSLATOR (ZERO NETWORK OVERHEAD) ---
(function initNuraDynamicI18nObserver() {
    let isMutatingI18n = false;

    const dynamicPatternRules = [
        [/^Showing (\d+)–(\d+) of (\d+) leads$/i, (m) => `Mostrando ${m[1]}–${m[2]} de ${m[3]} prospectos`],
        [/^Showing (\d+) of (\d+) leads$/i, (m) => `Mostrando ${m[1]} de ${m[2]} prospectos`],
        [/^Showing (\d+)–(\d+) of (\d+) customers$/i, (m) => `Mostrando ${m[1]}–${m[2]} de ${m[3]} clientes`],
        [/^Showing (\d+) of (\d+) customers$/i, (m) => `Mostrando ${m[1]} de ${m[2]} clientes`],
        [/^Showing (\d+)–(\d+) of (\d+) loans$/i, (m) => `Mostrando ${m[1]}–${m[2]} de ${m[3]} préstamos`],
        [/^Showing (\d+) of (\d+) loans$/i, (m) => `Mostrando ${m[1]} de ${m[2]} préstamos`],
        [/^Showing (\d+)–(\d+) of (\d+) evaluated loans \((\d+) active total in DB — narrow search for definitive figures\)$/i, (m) => `Mostrando ${m[1]}–${m[2]} de ${m[3]} préstamos evaluados (${m[4]} activos totales en BD — acota la búsqueda)`],
        [/^(\d+) Live Loan$/i, (m) => `${m[1]} Préstamo Activo`],
        [/^(\d+) Live Loans$/i, (m) => `${m[1]} Préstamos Activos`],
        [/^C\. Loans \((\d+)\)$/i, (m) => `C. Préstamos (${m[1]})`],
        [/^(\d+) Posted \/ (\d+) Total$/i, (m) => `${m[1]} Aplicados / ${m[2]} Totales`],
        [/^(\d+) Payments$/i, (m) => `${m[1]} Pagos`],
        [/^(INDIVIDUAL|BUSINESS) • Priority: (LOW|MEDIUM|HIGH)$/i, (m) => `${tEnum(m[1].toUpperCase())} • Prioridad: ${tEnum(m[2].toUpperCase())}`],
        [/^PRIORITY: (LOW|MEDIUM|HIGH)$/i, (m) => `PRIORIDAD: ${tEnum(m[1].toUpperCase())}`],
        [/^Last: ([A-Z_]+)$/i, (m) => `Último: ${tEnum(m[1])}`],
        [/^BUSINESS \(Rep: (.+)\)$/i, (m) => `PERSONA MORAL (Rep: ${m[1]})`],
        [/^Business • Rep: (.+)$/i, (m) => `Persona Moral • Rep: ${m[1]}`],
        [/^Individual • No email$/i, () => `Persona Física • Sin correo`],
        [/^Individual • (.+)$/i, (m) => `Persona Física • ${m[1]}`],
        [/^Since ([^•]+) • RFC: (.+)$/i, (m) => `Desde ${m[1].trim()} • RFC: ${m[2].trim() === 'Not Provided' ? 'No Proporcionado' : m[2].trim()}`],
        [/^(.*?)• By (.+)$/i, (m) => `${m[1]}• Por ${m[2]}`],
        [/^Edit Lead (LD-\d+)$/i, (m) => `Editar Prospecto ${m[1]}`],
        [/^Edit Customer (CUST-\d+)$/i, (m) => `Editar Cliente ${m[1]}`],
        [/^Showing: (.+)$/i, (m) => {
            const map = {
                "All Loans": "Todos los Préstamos",
                "Active Loans (Unliquidated)": "Préstamos Activos (Sin Liquidar)",
                "Current / On-Time Loans (0 Overdue Installments)": "Préstamos al Corriente (0 Cuotas Vencidas)",
                "Overdue Loans (≥ 1 Past-Due Installment)": "Préstamos en Mora (≥ 1 Cuota Vencida)",
                "Draft Loans (Pending Approval)": "Préstamos en Borrador (Pendientes de Aprobación)",
                "Approved Loans (Pending Disbursement)": "Préstamos Aprobados (Pendientes de Desembolso)",
                "Paid Off / Closed Loans": "Préstamos Liquidados / Cerrados",
                "Cancelled Loans": "Préstamos Cancelados",
                "Defaulted Loans": "Préstamos en Cartera Vencida"
            };
            return `Mostrando: ${map[m[1].trim()] || m[1]}`;
        }],
        [/^([\d.]+)% \/ mo • (\d+) wks$/i, (m) => `${m[1]}% / mes • ${m[2]} sem`],
        [/^([\d.]+)% \/ mo$/i, (m) => `${m[1]}% / mes`],
        [/^([\d.]+)% \/ month$/i, (m) => `${m[1]}% / mes`],
        [/^(\d+) wks \((\d+) mo\)$/i, (m) => `${m[1]} sem (${m[2]} mes)`],
        [/^(\d+) weeks$/i, (m) => `${m[1]} semanas`],
        [/^(\d+) days$/i, (m) => `${m[1]} días`],
        [/^(\d+) weekly installments$/i, (m) => `${m[1]} cuotas semanales`],
        [/^(\d+) monthly periods$/i, (m) => `${m[1]} periodos mensuales`],
        [/^(\d+)d DPD$/i, (m) => `${m[1]}d Mora`],
        [/^(\d+)d DPD \((\d+) inst\.\)$/i, (m) => `${m[1]}d Mora (${m[2]} cuotas)`],
        [/^Customer #(.+)$/i, (m) => `Cliente #${m[1]}`],
        [/^User #(\d+)$/i, (m) => `Usuario #${m[1]}`],
        [/^(\d+) contractual installments listed(.*)$/i, (m) => `${m[1]} cuotas contractuales listadas${m[2]}`],
        [/^Latest recorded settlement timestamp: (.+)$/i, (m) => `Última marca de tiempo de liquidación: ${m[1]}`],
        [/^Inst: (.+)$/i, (m) => `Cuotas: ${m[1]}`],
        [/^Reversed: (.+)$/i, (m) => `Revertido: ${m[1]}`],
        [/^Method: ([^•]+) • Paid at: (.+)$/i, (m) => `Método: ${tEnum(m[1].trim())} • Pagado: ${m[2].trim()}`],
        [/^(INDIVIDUAL|BUSINESS) • Phone: (.+) • Active Loans: (\d+)$/i, (m) => `${tEnum(m[1].toUpperCase())} • Tel: ${m[2]} • Préstamos Activos: ${m[3]}`],
        [/^Phone: (.+) • Active Loans: (\d+) • Current Unpaid Principal: (.+)$/i, (m) => `Tel: ${m[1]} • Préstamos Activos: ${m[2]} • Capital Insoluto Actual: ${m[3]}`],
        [/^KYC: ([A-Z_]+)$/i, (m) => `KYC: ${tEnum(m[1])}`],
        [/^(\$[\d,]+\.\d{2} MXN) \/ wk$/i, (m) => `${m[1]} / sem`],
        [/^Projected Installments \((\d+) Weeks \/ (\d+) Interest Months\)$/i, (m) => `Cuotas Proyectadas (${m[1]} Semanas / ${m[2]} Meses de Interés)`],
        [/^Projected Installments \((\d+) Weeks\)$/i, (m) => `Cuotas Proyectadas (${m[1]} Semanas)`],
        [/^Show Summary View \(First 3 & Final\) ↑$/i, () => `Mostrar Vista Resumen (Primeras 3 y Final) ↑`],
        [/^Show All (\d+) Installments ↓$/i, (m) => `Mostrar las ${m[1]} Cuotas ↓`],
        [/^••• (\d+) intermediate weekly installments \(#4 to #(\d+)\) hidden in summary view — Click to expand all (\d+) installments •••$/i, (m) => `••• ${m[1]} cuotas semanales intermedias (#4 a #${m[2]}) ocultas en vista resumen — Haz clic para expandir las ${m[3]} cuotas •••`],
        [/^Last Interaction \((.+)\)$/i, (m) => `Última Interacción (${tEnum(m[1].trim())})`],
        [/^Rem\. Total: (.+)$/i, (m) => `Total Rest.: ${m[1]}`],
        [/^Actor ID: ([^•]+) • Role: ([^•]+) • (.+)$/i, (m) => `ID Actor: ${m[1].trim()} • Rol: ${m[2].trim()} • ${m[3].trim()}`]
    ];

    const dynamicExactEs = {
        "No leads found": "No se encontraron prospectos",
        "Click \"+ New Lead\" to register a prospect in PostgreSQL.": "Haz clic en \"+ Nuevo Prospecto\" para registrar un prospecto en PostgreSQL.",
        "No call scheduled": "Sin llamada programada",
        "Open →": "Abrir →",
        "Queue: Overdue Calls (Requires Immediate Follow-up)": "Cola: Llamadas Vencidas (Requiere Seguimiento Inmediato)",
        "Queue: Today's Scheduled Calls & Uncontacted New Leads": "Cola: Llamadas Programadas para Hoy y Prospectos Nuevos",
        "Queue: Upcoming Scheduled Calls": "Cola: Próximas Llamadas Programadas",
        "Loading call queue...": "Cargando cola de llamadas...",
        "No leads in this call queue": "No hay prospectos en esta cola de llamadas",
        "All scheduled calls for this bucket are clear.": "Todas las llamadas programadas para esta bandeja están al día.",
        "New Lead (First Contact Pending)": "Prospecto Nuevo (1er Contacto Pendiente)",
        "📞 Dial": "📞 Llamar",
        "Log Call": "Registrar Llamada",
        "Details →": "Detalles →",
        "None Scheduled": "Sin Programar",
        "Not Provided": "No Proporcionado",
        "Not available": "No disponible",
        "Not specified": "No especificado",
        "No summary notes provided.": "Sin notas de resumen registradas.",
        "No calls or notes logged yet. Click \"+ Add Entry\" to record your first interaction.": "Aún no hay llamadas ni notas registradas. Haz clic en \"+ Agregar Registro\" para capturar tu primera interacción.",
        "Logged by": "Registrado por",
        "No customers found": "No se encontraron clientes",
        "Click \"+ New Customer\" or convert a qualified Lead.": "Haz clic en \"+ Nuevo Cliente\" o convierte un Prospecto calificado.",
        "View Profile →": "Ver Perfil →",
        "No Phone": "Sin Teléfono",
        "No RFC": "Sin RFC",
        "Pending Stage 4": "Pendiente Etapa 4",
        "Inspect →": "Inspeccionar →",
        "Loading customer interactions...": "Cargando interacciones del cliente...",
        "No interactions or notes recorded for this customer yet.": "Aún no hay interacciones ni notas registradas para este cliente.",
        "Inherited from Lead": "Heredado de Prospecto",
        "Recorded by": "Registrado por",
        "Legal Entity Name (Razón Social) *": "Denominación o Razón Social *",
        "Loading documents from PostgreSQL...": "Cargando documentos desde PostgreSQL...",
        "No KYC or contractual documents uploaded for this customer yet.": "Aún no se han subido documentos KYC o contractuales para este cliente.",
        "View / Download": "Ver / Descargar",
        "✓ Hash Copied": "✓ Hash Copiado",
        "Partial sample:": "Muestra parcial:",
        "No loans found": "No se encontraron préstamos",
        "There are no loans matching the selected filter.": "No existen préstamos que coincidan con el filtro seleccionado.",
        "No loans in this category within the first 100 evaluated active records. Narrow your search to evaluate specific loans.": "No hay préstamos en esta categoría dentro de los primeros 100 registros activos evaluados. Acota tu búsqueda.",
        "No contractual notes recorded.": "Sin notas contractuales registradas.",
        "Loading amortization schedule...": "Cargando tabla de amortización...",
        "Loading schedule...": "Cargando calendario...",
        "Unable to load amortization schedule.": "No fue posible cargar la tabla de amortización.",
        "Schedule unavailable": "Calendario no disponible",
        "No full installment settlements recorded yet": "Aún no se registran cuotas liquidadas en su totalidad",
        "PARTIAL PAYMENT": "PAGO PARCIAL",
        "ACTIVE / DISBURSED": "ACTIVO / DESEMBOLSADO",
        "No approval notes recorded.": "Sin notas de aprobación registradas.",
        "Approval not yet granted.": "Aprobación aún no otorgada.",
        "No disbursement notes recorded.": "Sin notas de desembolso registradas.",
        "Disbursement not yet executed.": "Desembolso aún no ejecutado.",
        "Authorized Underwriter": "Suscriptor Autorizado",
        "See customer interactions log.": "Consultar bitácora de interacciones del cliente.",
        "Loading payment ledger...": "Cargando libro de pagos...",
        "↩ Reverse": "↩ Revertir",
        "Locked (LIFO)": "Bloqueado (LIFO)",
        "Reversed": "Revertido",
        "🖨️ Receipt": "🖨️ Recibo",
        "🖨️ Print Statement": "🖨️ Imprimir Estado de Cuenta",
        "Searching active customers in PostgreSQL...": "Buscando clientes activos en PostgreSQL...",
        "No ACTIVE customers found matching your search.": "No se encontraron clientes ACTIVOS que coincidan con tu búsqueda.",
        "Select": "Seleccionar",
        "Invalid Parameters": "Parámetros Inválidos",
        "Schedule Preview Unavailable (Check Input Ranges)": "Vista Previa No Disponible (Verifica los Rangos)",
        "Queue: All Actionable Delinquent & Due Loans": "Cola: Todos los Préstamos en Mora y que Vencen Hoy",
        "Queue: Active Overdue Loans (Immediate Collection Required)": "Cola: Préstamos Activos en Mora (Cobranza Inmediata)",
        "Queue: Installments Maturing Today": "Cola: Cuotas que Vencen el Día de Hoy",
        "Queue: Defaulted Portfolio (Cartera Vencida / Legal Recovery)": "Cola: Cartera Vencida / Recuperación Legal",
        "Loading collections queue from PostgreSQL...": "Cargando cola de cobranza desde PostgreSQL...",
        "No delinquent or due loans in this queue": "No hay préstamos vencidos ni exigibles en esta cola",
        "All scheduled installments for this filter are up to date.": "Todas las cuotas programadas para este filtro están al corriente.",
        "No collection calls logged yet.": "Aún no se han registrado llamadas de cobranza.",
        "📞 Log Call": "📞 Registrar Llamada",
        "💳 Payment": "💳 Abonar Pago",
        "Loading team...": "Cargando equipo...",
        "Loading security audit trail...": "Cargando bitácora de auditoría de seguridad...",
        "New Consultation": "Nueva Consulta",
        "Hello, I'm Nura. How can I help you with your leads, customers, or credit simulations today?": "Hola, soy Nura. ¿Cómo puedo ayudarte hoy con tus prospectos, clientes o simulaciones de crédito?"
    };

    function translateSingleDynamicStringToEs(norm) {
        if (dynamicExactEs[norm]) return dynamicExactEs[norm];
        if (NuraI18n.staticEnToEs && NuraI18n.staticEnToEs[norm]) return NuraI18n.staticEnToEs[norm];
        if (`enum.${norm}` in NuraI18n.dictionaries.es) return NuraI18n.dictionaries.es[`enum.${norm}`];

        const apiTranslated = NuraI18n.translateApiError(norm);
        if (apiTranslated !== norm) return apiTranslated;

        for (const [regex, replacer] of dynamicPatternRules) {
            const match = norm.match(regex);
            if (match) return replacer(match);
        }
        return null;
    }

    function processDynamicSubtree(rootEl) {
        if (!rootEl) return;
        const isEs = (NuraI18n.currentLang === 'es');
        const walker = document.createTreeWalker(rootEl, NodeFilter.SHOW_TEXT, {
            acceptNode(node) {
                const p = node.parentElement;
                if (!p) return NodeFilter.FILTER_REJECT;
                const tag = p.tagName;
                if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'CODE' || tag === 'TEXTAREA') {
                    return NodeFilter.FILTER_REJECT;
                }
                return NodeFilter.FILTER_ACCEPT;
            }
        });

        let node;
        while ((node = walker.nextNode())) {
            const raw = node.nodeValue || '';
            const norm = raw.replace(/\s+/g, ' ').trim();
            if (!norm) continue;

            if (node.__nuraDynOrigEn === undefined) {
                const candidateEs = translateSingleDynamicStringToEs(norm);
                if (candidateEs && candidateEs !== norm) {
                    node.__nuraDynOrigEn = raw;
                    node.__nuraDynNormEn = norm;
                }
            }

            if (node.__nuraDynOrigEn) {
                if (!isEs) {
                    if (node.nodeValue !== node.__nuraDynOrigEn) {
                        node.nodeValue = node.__nuraDynOrigEn;
                    }
                } else {
                    const targetEs = translateSingleDynamicStringToEs(node.__nuraDynNormEn);
                    if (targetEs) {
                        const lead = (node.__nuraDynOrigEn.match(/^\s*/) || [''])[0];
                        const trail = (node.__nuraDynOrigEn.match(/\s*$/) || [''])[0];
                        const fullEs = `${lead}${targetEs}${trail}`;
                        if (node.nodeValue !== fullEs) {
                            node.nodeValue = fullEs;
                        }
                    }
                }
            }
        }
    }

    window.rerenderCachedCrmViewsOnLangChange = function() {
        isMutatingI18n = true;
        try {
            const appView = document.getElementById('app-view');
            if (appView) processDynamicSubtree(appView);
        } finally {
            isMutatingI18n = false;
        }
    };

    window.addEventListener('DOMContentLoaded', () => {
        const appView = document.getElementById('app-view');
        if (!appView || typeof MutationObserver === 'undefined') return;

        const observer = new MutationObserver((mutations) => {
            if (isMutatingI18n || NuraI18n.currentLang !== 'es') return;
            isMutatingI18n = true;
            try {
                mutations.forEach(m => {
                    if (m.type === 'childList') {
                        m.addedNodes.forEach(n => {
                            if (n.nodeType === Node.ELEMENT_NODE) {
                                NuraI18n.applyStaticTranslations(n);
                                processDynamicSubtree(n);
                            } else if (n.nodeType === Node.TEXT_NODE && n.parentElement) {
                                processDynamicSubtree(n.parentElement);
                            }
                        });
                    } else if (m.type === 'characterData' && m.target && m.target.parentElement) {
                        processDynamicSubtree(m.target.parentElement);
                    }
                });
            } finally {
                isMutatingI18n = false;
            }
        });

        observer.observe(appView, {
            childList: true,
            subtree: true,
            characterData: true
        });
    });
})();
