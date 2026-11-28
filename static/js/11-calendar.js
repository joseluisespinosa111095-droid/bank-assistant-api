/* =========================================================================
   11. VISUAL CRM & BROKER ACTIVITY CALENDAR (CALLS vs IN-PERSON APPOINTMENTS)
   Displays scheduled person names inside each day cell with clear visual
   distinction between Phone Calls (CALL) and In-Person Appointments (MEETING).
   ========================================================================= */
(function () {
    'use strict';

    const nowInit = new Date();
    const calState = {
        year: nowInit.getFullYear(),
        month: nowInit.getMonth() + 1, // 1..12
        filter: 'ALL', // ALL | CALL | MEETING
        events: [],
        counts: { total: 0, calls: 0, meetings: 0 },
        mountedRoots: new Set(),
        loading: false,
    };

    const MONTHS_EN = [
        'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December',
    ];
    const MONTHS_ES = [
        'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
        'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
    ];
    const WEEKDAYS_EN = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const WEEKDAYS_ES = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom'];

    function _isEs() {
        return typeof NuraI18n !== 'undefined' && NuraI18n.currentLang === 'es';
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

    function _pad2(n) {
        return String(n).padStart(2, '0');
    }

    function _todayLocalIso() {
        const d = new Date();
        return `${d.getFullYear()}-${_pad2(d.getMonth() + 1)}-${_pad2(d.getDate())}`;
    }

    async function loadCrmCalendar(targetRootId = null) {
        if (targetRootId) calState.mountedRoots.add(targetRootId);
        calState.loading = true;
        renderAllMountedCalendars();

        try {
            const res = await fetch(`/api/leads/calendar?year=${calState.year}&month=${calState.month}`);
            if (res.status === 401) {
                if (typeof mostrarAuthView === 'function') mostrarAuthView();
                return;
            }
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            calState.events = Array.isArray(data.events) ? data.events : [];
            calState.counts = data.counts || { total: calState.events.length, calls: 0, meetings: 0 };
        } catch (err) {
            console.error('Calendar load error:', err);
            calState.events = [];
            calState.counts = { total: 0, calls: 0, meetings: 0 };
        } finally {
            calState.loading = false;
            renderAllMountedCalendars();
        }
    }

    function renderAllMountedCalendars() {
        const rootIds = calState.mountedRoots.size > 0
            ? Array.from(calState.mountedRoots)
            : ['broker-calendar-root', 'leads-calendar-root'];
        rootIds.forEach(id => {
            const el = document.getElementById(id);
            if (el) renderCalendarInto(el);
        });
    }

    function renderCalendarInto(rootEl) {
        const isEs = _isEs();
        const monthLabel = `${(isEs ? MONTHS_ES : MONTHS_EN)[calState.month - 1]} ${calState.year}`;
        const weekdays = isEs ? WEEKDAYS_ES : WEEKDAYS_EN;
        const todayIso = _todayLocalIso();

        const filteredEvents = calState.events.filter(ev => {
            if (calState.filter === 'CALL') return ev.activity_type === 'CALL';
            if (calState.filter === 'MEETING') return ev.activity_type === 'MEETING';
            return true;
        });

        const byDate = {};
        filteredEvents.forEach(ev => {
            if (!byDate[ev.date]) byDate[ev.date] = [];
            byDate[ev.date].push(ev);
        });

        const firstDayOfMonth = new Date(calState.year, calState.month - 1, 1);
        const daysInMonth = new Date(calState.year, calState.month, 0).getDate();
        // Monday = 0 .. Sunday = 6
        const leadingBlanks = (firstDayOfMonth.getDay() + 6) % 7;
        const totalCells = Math.ceil((leadingBlanks + daysInMonth) / 7) * 7;

        const cellsHtml = [];
        for (let idx = 0; idx < totalCells; idx++) {
            const dayNum = idx - leadingBlanks + 1;
            if (dayNum < 1 || dayNum > daysInMonth) {
                cellsHtml.push('<div class="min-h-[104px] bg-slate-50/70 border-b border-r border-slate-100 p-2"></div>');
                continue;
            }
            const dateIso = `${calState.year}-${_pad2(calState.month)}-${_pad2(dayNum)}`;
            const isToday = (dateIso === todayIso);
            const dayEvents = byDate[dateIso] || [];

            const itemsHtml = dayEvents.map(ev => {
                const isMeeting = ev.activity_type === 'MEETING';
                const pillCls = isMeeting
                    ? 'bg-emerald-50 hover:bg-emerald-100 text-emerald-900 border-emerald-200'
                    : 'bg-blue-50 hover:bg-blue-100 text-blue-900 border-blue-200';
                const icon = isMeeting ? '🤝' : '📞';
                const typeLabel = isMeeting
                    ? (isEs ? 'Cita en persona' : 'In-Person Appointment')
                    : (isEs ? 'Llamada' : 'Phone Call');
                const clickHandler = ev.lead_id
                    ? `openCalendarEventTarget('LEAD', ${Number(ev.lead_id)})`
                    : (ev.customer_id ? `openCalendarEventTarget('CUSTOMER', ${Number(ev.customer_id)})` : '');
                return `
                    <button type="button" onclick="${clickHandler}" data-cal-activity-type="${_esc(ev.activity_type)}" title="${_esc(typeLabel)} • ${_esc(ev.time)} — ${_esc(ev.person_name)} (${_esc(ev.record_number || '')})"
                        class="w-full text-left px-1.5 py-1 rounded-lg border text-[10px] leading-tight transition flex items-center gap-1 ${pillCls}">
                        <span class="shrink-0">${icon}</span>
                        <span class="font-bold truncate">${_esc(ev.person_name)}</span>
                        <span class="ml-auto font-mono text-[9px] opacity-75 shrink-0">${_esc(ev.time)}</span>
                    </button>
                `;
            }).join('');

            cellsHtml.push(`
                <div data-cal-date="${dateIso}" class="min-h-[104px] bg-white border-b border-r border-slate-200/70 p-1.5 flex flex-col gap-1 ${isToday ? 'ring-2 ring-inset ring-indigo-500/60 bg-indigo-50/20' : ''}">
                    <div class="flex items-center justify-between px-0.5">
                        <span class="text-[11px] font-extrabold ${isToday ? 'inline-flex items-center justify-center h-5 w-5 rounded-full bg-indigo-600 text-white' : 'text-slate-700'}">${dayNum}</span>
                        <button type="button" onclick="openCalendarScheduleModal('${dateIso}')" title="${isEs ? 'Programar actividad este día' : 'Schedule activity on this day'}" class="text-[10px] font-bold text-slate-400 hover:text-indigo-600 px-1 rounded">+</button>
                    </div>
                    <div class="space-y-1 overflow-y-auto max-h-[82px] pr-0.5">
                        ${itemsHtml}
                    </div>
                </div>
            `);
        }

        const pillCls = (active) => active
            ? 'px-3 py-1.5 rounded-xl text-[11px] font-bold bg-slate-900 text-white shadow-sm'
            : 'px-3 py-1.5 rounded-xl text-[11px] font-bold bg-slate-100 hover:bg-slate-200 text-slate-700';

        rootEl.innerHTML = `
            <div class="fintech-card p-4 bg-white space-y-4">
                <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-3 border-b border-slate-100 pb-3">
                    <div class="flex items-center gap-2 flex-wrap">
                        <button type="button" onclick="changeCrmCalendarMonth(-1)" class="py-1.5 px-3 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold text-xs">‹</button>
                        <h3 class="text-sm sm:text-base font-extrabold text-slate-900 min-w-[155px] text-center">${_esc(monthLabel)}</h3>
                        <button type="button" onclick="changeCrmCalendarMonth(1)" class="py-1.5 px-3 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold text-xs">›</button>
                        <button type="button" onclick="goCrmCalendarToday()" class="py-1.5 px-3 rounded-xl bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 font-semibold text-xs">${isEs ? 'Hoy' : 'Today'}</button>
                    </div>

                    <div class="flex flex-wrap items-center gap-2">
                        <div class="flex items-center gap-1.5">
                            <button type="button" onclick="setCrmCalendarFilter('ALL')" class="${pillCls(calState.filter === 'ALL')}">${isEs ? 'Todas' : 'All'} (${Number(calState.counts.total || 0)})</button>
                            <button type="button" onclick="setCrmCalendarFilter('CALL')" class="${pillCls(calState.filter === 'CALL')}">📞 ${isEs ? 'Llamadas' : 'Calls'} (${Number(calState.counts.calls || 0)})</button>
                            <button type="button" onclick="setCrmCalendarFilter('MEETING')" class="${pillCls(calState.filter === 'MEETING')}">🤝 ${isEs ? 'Citas en persona' : 'In-Person'} (${Number(calState.counts.meetings || 0)})</button>
                        </div>
                        <button type="button" onclick="openCalendarScheduleModal()" class="btn-primary py-1.5 px-3.5 rounded-xl text-xs shadow-sm">
                            ${isEs ? '+ Programar Llamada / Cita' : '+ Schedule Call / Appointment'}
                        </button>
                    </div>
                </div>

                <!-- Legend -->
                <div class="flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-600">
                    <div class="flex items-center gap-3 flex-wrap">
                        <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-blue-50 text-blue-800 border border-blue-200 font-bold">
                            <span>📞</span><span>${isEs ? 'Llamada programada' : 'Scheduled Call'}</span>
                        </span>
                        <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-800 border border-emerald-200 font-bold">
                            <span>🤝</span><span>${isEs ? 'Cita en persona' : 'In-Person Appointment'}</span>
                        </span>
                    </div>
                    <span class="text-[10px] text-slate-400">
                        ${calState.loading
                            ? (isEs ? 'Actualizando calendario...' : 'Refreshing calendar...')
                            : (isEs ? 'Haz clic en cualquier nombre para abrir su expediente' : 'Click any person name to open their record')}
                    </span>
                </div>

                <!-- Monthly Grid -->
                <div class="overflow-x-auto rounded-2xl border border-slate-200/90">
                    <div class="min-w-[720px]">
                        <div class="grid grid-cols-7 bg-slate-50 border-b border-slate-200">
                            ${weekdays.map(w => `<div class="py-2 px-2 text-center text-[10px] font-extrabold uppercase tracking-wider text-slate-500">${_esc(w)}</div>`).join('')}
                        </div>
                        <div class="grid grid-cols-7 border-l border-t border-slate-100">
                            ${cellsHtml.join('')}
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    function changeCrmCalendarMonth(delta) {
        let m = calState.month + delta;
        let y = calState.year;
        if (m < 1) { m = 12; y -= 1; }
        if (m > 12) { m = 1; y += 1; }
        calState.month = m;
        calState.year = y;
        loadCrmCalendar();
    }

    function goCrmCalendarToday() {
        const now = new Date();
        calState.year = now.getFullYear();
        calState.month = now.getMonth() + 1;
        loadCrmCalendar();
    }

    function setCrmCalendarFilter(filterVal) {
        calState.filter = filterVal;
        renderAllMountedCalendars();
    }

    async function openCalendarEventTarget(entityKind, id) {
        if (entityKind === 'LEAD' && typeof openLeadDetail === 'function') {
            if (typeof switchTab === 'function') switchTab('leads');
            await openLeadDetail(id);
        } else if (entityKind === 'CUSTOMER' && typeof openCustomerDetail === 'function') {
            if (typeof switchTab === 'function') switchTab('customers');
            await openCustomerDetail(id);
        }
    }

    async function openCalendarScheduleModal(prefillDateIso = null) {
        const isEs = _isEs();
        const modal = document.getElementById('calendar-schedule-modal');
        if (!modal) return;
        document.getElementById('cal-sched-error')?.classList.add('hidden');
        document.getElementById('cal-sched-form')?.reset();

        const dtInput = document.getElementById('csf-datetime');
        if (dtInput) {
            const baseDate = prefillDateIso || _todayLocalIso();
            dtInput.value = `${baseDate}T10:00`;
        }

        const selectEl = document.getElementById('csf-lead-id');
        if (selectEl) {
            selectEl.innerHTML = `<option value="">${isEs ? 'Cargando prospectos...' : 'Loading leads...'}</option>`;
            try {
                const res = await fetch('/api/leads?page=1&page_size=100');
                const data = res.ok ? await res.json() : { items: [] };
                const openLeads = (data.items || []).filter(l => !['CONVERTED', 'LOST'].includes(l.stage));
                selectEl.innerHTML = `<option value="">${isEs ? '— Selecciona un prospecto activo —' : '— Select an active lead —'}</option>`
                    + openLeads.map(l => `<option value="${Number(l.id)}">${_esc(l.lead_number)} — ${_esc(l.full_name)}</option>`).join('');
            } catch (_) {
                selectEl.innerHTML = `<option value="">${isEs ? 'No se pudieron cargar prospectos' : 'Could not load leads'}</option>`;
            }
        }
        modal.classList.remove('hidden');
    }

    function closeCalendarScheduleModal() {
        document.getElementById('calendar-schedule-modal')?.classList.add('hidden');
    }

    async function handleCalendarScheduleSubmit(e) {
        e.preventDefault();
        const isEs = _isEs();
        const errBox = document.getElementById('cal-sched-error');
        const btn = document.getElementById('csf-submit-btn');
        errBox?.classList.add('hidden');
        if (btn) btn.disabled = true;

        const leadId = Number(document.getElementById('csf-lead-id').value);
        const activityType = document.getElementById('csf-activity-type').value; // CALL | MEETING
        const dtVal = document.getElementById('csf-datetime').value;
        const notes = document.getElementById('csf-notes').value.trim();

        const payload = {
            interaction_type: activityType === 'MEETING' ? 'MEETING' : 'CALL',
            direction: 'OUTBOUND',
            visibility: 'INTERNAL',
            outcome: 'FOLLOW_UP_REQUIRED',
            content: notes || (activityType === 'MEETING'
                ? (isEs ? 'Cita en persona programada desde calendario' : 'In-person appointment scheduled from calendar')
                : (isEs ? 'Llamada programada desde calendario' : 'Phone call scheduled from calendar')),
            next_follow_up_at: dtVal,
            next_follow_up_type: activityType,
        };

        try {
            const res = await fetch(`/api/leads/${leadId}/interactions`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': typeof getCookie === 'function' ? getCookie('csrf_token') : '',
                },
                body: JSON.stringify(payload),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(typeof data.detail === 'string' ? data.detail : (isEs ? 'No se pudo programar la actividad' : 'Could not schedule activity'));
            }
            closeCalendarScheduleModal();
            if (dtVal && dtVal.length >= 7) {
                calState.year = Number(dtVal.slice(0, 4));
                calState.month = Number(dtVal.slice(5, 7));
            }
            await loadCrmCalendar();
            if (typeof loadLeads === 'function') loadLeads(currentLeadsPage || 1);
        } catch (err) {
            if (errBox) { errBox.innerText = err.message; errBox.classList.remove('hidden'); }
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    function rerenderCalendarOnLangChange() {
        renderAllMountedCalendars();
    }

    window.loadCrmCalendar = loadCrmCalendar;
    window.changeCrmCalendarMonth = changeCrmCalendarMonth;
    window.goCrmCalendarToday = goCrmCalendarToday;
    window.setCrmCalendarFilter = setCrmCalendarFilter;
    window.openCalendarEventTarget = openCalendarEventTarget;
    window.openCalendarScheduleModal = openCalendarScheduleModal;
    window.closeCalendarScheduleModal = closeCalendarScheduleModal;
    window.handleCalendarScheduleSubmit = handleCalendarScheduleSubmit;
    window.rerenderCalendarOnLangChange = rerenderCalendarOnLangChange;
})();
