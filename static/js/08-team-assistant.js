        /* =========================================================================
           7. PRIVACY, TEAM MANAGEMENT, AUDIT LOGS & NURA CHAT
           ========================================================================= */
        function togglePrivacy() {
            isPrivacyOn = !isPrivacyOn;
            document.querySelectorAll('.privacy-metric').forEach(el => {
                const realVal = el.getAttribute('data-real-value') || '$0.00 MXN';
                el.innerText = isPrivacyOn ? '••••••••' : realVal;
            });
            const privacyLabel = document.getElementById('privacy-label');
            if (privacyLabel) {
                privacyLabel.innerText = isPrivacyOn ? 'Show Figures' : 'Hide Figures';
            }
        }

        async function openTeamModal() {
            document.getElementById('team-form-msg').classList.add('hidden');
            document.getElementById('team-modal').classList.remove('hidden');
            await loadTeamMembers();
        }

        function closeTeamModal() {
            document.getElementById('team-modal').classList.add('hidden');
        }

        async function loadTeamMembers() {
            const listEl = document.getElementById('team-members-list');
            const isEs = (typeof NuraI18n !== 'undefined' && NuraI18n.currentLang === 'es');
            listEl.innerHTML = `<p class="text-slate-400">${isEs ? 'Cargando equipo...' : 'Loading team...'}</p>`;

            try {
                const res = await fetch('/api/admin/users');
                if (!res.ok) throw new Error(isEs ? 'No se pudo cargar la lista del equipo' : 'Failed to load team list');
                const users = await res.json();
                listEl.innerHTML = '';

                const rolesCatalog = [
                    'OWNER_ADMIN',
                    'SALES_AGENT',
                    'CREDIT_ADVISOR',
                    'OPERATIONS_OFFICER'
                ];

                users.forEach(u => {
                    const isSelf = (currentUser && u.id === currentUser.id);
                    const isActive = Boolean(u.is_active);
                    const row = document.createElement('div');
                    row.className = `p-3 rounded-2xl border space-y-2.5 transition ${isActive ? 'bg-slate-50 border-slate-200/80' : 'bg-slate-100/70 border-slate-200 opacity-75'}`;

                    const statusBadge = isActive
                        ? `<span class="text-[9px] font-extrabold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">${isEs ? 'ACTIVO' : 'ACTIVE'}</span>`
                        : `<span class="text-[9px] font-extrabold px-2 py-0.5 rounded-full bg-red-50 text-red-700 border border-red-200">${isEs ? 'INACTIVO' : 'INACTIVE'}</span>`;

                    const selfBadge = isSelf
                        ? `<span class="text-[9px] font-bold px-2 py-0.5 rounded-full bg-slate-200 text-slate-700">${isEs ? 'Tú' : 'You'}</span>`
                        : '';

                    const roleOptionsHtml = rolesCatalog.map(r => {
                        const selected = (u.role === r) ? 'selected' : '';
                        const label = (typeof tEnum === 'function') ? tEnum(r) : r;
                        return `<option value="${r}" ${selected}>${escapeHtml(label)}</option>`;
                    }).join('');

                    const controlsHtml = isSelf
                        ? `<div class="pt-1 border-t border-slate-200/60 flex items-center justify-between text-[10px] text-slate-500">
                               <span>${escapeHtml((typeof tEnum === 'function') ? tEnum(u.role) : u.role)}</span>
                               <span class="font-semibold text-slate-400">${isEs ? 'Cuenta actual protegida' : 'Current account protected'}</span>
                           </div>`
                        : `<div class="pt-2 border-t border-slate-200/60 flex items-center gap-2">
                               <select data-user-id="${u.id}" class="team-role-select flex-1 fintech-input bg-white rounded-xl px-2.5 py-1.5 text-[11px] font-semibold">
                                   ${roleOptionsHtml}
                               </select>
                               <button type="button" data-user-id="${u.id}" class="btn-toggle-active py-1.5 px-3 rounded-xl text-[11px] font-bold border transition shrink-0 ${isActive ? 'bg-red-50 hover:bg-red-100 text-red-700 border-red-200' : 'bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border-emerald-200'}">
                                   ${isActive ? (isEs ? 'Desactivar' : 'Deactivate') : (isEs ? 'Reactivar' : 'Reactivate')}
                               </button>
                           </div>`;

                    row.innerHTML = `
                        <div class="flex items-start justify-between gap-2">
                            <div class="min-w-0 pr-2">
                                <p class="font-bold text-slate-900 truncate text-xs">${escapeHtml(u.full_name || u.email)}</p>
                                <p class="text-[10px] text-slate-500 truncate">${escapeHtml(u.email)}</p>
                            </div>
                            <div class="flex items-center gap-1 shrink-0">
                                ${selfBadge}
                                ${statusBadge}
                            </div>
                        </div>
                        ${controlsHtml}
                    `;

                    if (!isSelf) {
                        const selectEl = row.querySelector('.team-role-select');
                        const toggleBtn = row.querySelector('.btn-toggle-active');

                        if (selectEl) {
                            selectEl.onchange = async () => {
                                const newRole = selectEl.value;
                                if (newRole === u.role) return;
                                const confirmMsg = isEs
                                    ? `¿Cambiar el rol de ${u.full_name || u.email} a ${newRole}?`
                                    : `Change role for ${u.full_name || u.email} to ${newRole}?`;
                                if (!confirm(confirmMsg)) {
                                    selectEl.value = u.role;
                                    return;
                                }
                                await updateTeamMemberAccess(u.id, { role: newRole });
                            };
                        }

                        if (toggleBtn) {
                            toggleBtn.onclick = async () => {
                                const nextActive = !isActive;
                                const confirmMsg = nextActive
                                    ? (isEs ? `¿Reactivar el acceso de ${u.full_name || u.email}?` : `Reactivate access for ${u.full_name || u.email}?`)
                                    : (isEs ? `¿Desactivar a ${u.full_name || u.email}? Su sesión se cerrará de inmediato.` : `Deactivate ${u.full_name || u.email}? Their active sessions will be closed immediately.`);
                                if (!confirm(confirmMsg)) return;
                                await updateTeamMemberAccess(u.id, { is_active: nextActive });
                            };
                        }
                    }

                    listEl.appendChild(row);
                });
            } catch (err) {
                listEl.innerHTML = `<p class="text-red-600">${escapeHtml(err.message)}</p>`;
            }
        }

        async function updateTeamMemberAccess(targetUserId, payload) {
            const msgBox = document.getElementById('team-form-msg');
            const isEs = (typeof NuraI18n !== 'undefined' && NuraI18n.currentLang === 'es');
            if (msgBox) msgBox.classList.add('hidden');

            try {
                const res = await fetch(`/api/admin/users/${targetUserId}`, {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': getCookie('csrf_token')
                    },
                    body: JSON.stringify(payload)
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    const rawDetail = typeof data.detail === 'string' ? data.detail : 'Could not update user.';
                    throw new Error(typeof translateApiError === 'function' ? translateApiError(rawDetail) : rawDetail);
                }

                if (msgBox) {
                    msgBox.className = 'text-xs p-2.5 rounded-xl bg-emerald-50 border border-emerald-200 text-emerald-800 font-semibold';
                    msgBox.innerText = isEs
                        ? '✓ Permisos del usuario actualizados correctamente.'
                        : '✓ Team member access updated successfully.';
                    msgBox.classList.remove('hidden');
                }
                await loadTeamMembers();
            } catch (err) {
                if (msgBox) {
                    msgBox.className = 'text-xs p-2.5 rounded-xl bg-red-50 border border-red-200 text-red-700 font-medium';
                    msgBox.innerText = err.message;
                    msgBox.classList.remove('hidden');
                }
                await loadTeamMembers();
            }
        }

        async function handleCreateTeamMember(e) {
            e.preventDefault();
            const full_name = document.getElementById('new-member-name').value.trim();
            const email = document.getElementById('new-member-email').value.trim();
            const password = document.getElementById('new-member-password').value;
            const role = document.getElementById('new-member-role').value;
            const msgBox = document.getElementById('team-form-msg');
            const btn = document.getElementById('create-member-btn');

            msgBox.classList.add('hidden');
            btn.disabled = true;

            try {
                const res = await fetch('/api/auth/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCookie('csrf_token') },
                    body: JSON.stringify({ email, password, full_name, role })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || "Could not create member");

                document.getElementById('create-member-form').reset();
                msgBox.className = 'text-xs p-2 rounded-lg bg-emerald-50 text-emerald-700 font-medium';
                msgBox.innerText = `Created ${data.email} (${data.role})`;
                msgBox.classList.remove('hidden');
                await loadTeamMembers();
            } catch (err) {
                msgBox.className = 'text-xs p-2 rounded-lg bg-red-50 text-red-700 font-medium';
                msgBox.innerText = err.message;
                msgBox.classList.remove('hidden');
            } finally {
                btn.disabled = false;
            }
        }

        async function openAuditLogsModal() {
            const modal = document.getElementById('audit-modal');
            const listEl = document.getElementById('audit-logs-list');
            modal.classList.remove('hidden');
            listEl.innerHTML = '<p class="text-slate-400">Loading security audit trail...</p>';
            try {
                const res = await fetch('/api/admin/audit-logs?limit=40');
                if (!res.ok) throw new Error("Failed to load audit logs");
                const logs = await res.json();
                listEl.innerHTML = '';
                logs.forEach(l => {
                    const item = document.createElement('div');
                    item.className = 'p-3 rounded-xl bg-slate-50 border border-slate-200/80 flex items-center justify-between gap-2';
                    item.innerHTML = `
                        <div class="min-w-0">
                            <p class="font-bold text-slate-900">${escapeHtml(l.event_type)} <span class="text-[10px] font-normal text-slate-500">(${escapeHtml(l.resource_type)}: ${escapeHtml(l.resource_id || 'N/A')})</span></p>
                            <p class="text-[10px] text-slate-500 truncate">Actor ID: ${escapeHtml(l.actor_user_id || 'System')} • Role: ${escapeHtml(l.actor_role || 'N/A')} • ${escapeHtml(l.created_at)}</p>
                        </div>
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded-full ${l.outcome === 'SUCCESS' ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-600'} shrink-0">${escapeHtml(l.outcome)}</span>
                    `;
                    listEl.appendChild(item);
                });
            } catch (err) {
                listEl.innerHTML = `<p class="text-red-600">${escapeHtml(err.message)}</p>`;
            }
        }

        function closeAuditLogsModal() {
            document.getElementById('audit-modal').classList.add('hidden');
        }

        async function cargarConversaciones() {
            try {
                const res = await fetch('/api/conversations');
                if (res.status === 401 || res.status === 403) {
                    mostrarAuthView();
                    return;
                }
                if (res.ok) {
                    const convs = await res.json();
                    const bar = document.getElementById('conversations-bar');
                    bar.innerHTML = '';
                    if (convs.length > 0) {
                        convs.forEach(c => {
                            const btn = document.createElement('button');
                            btn.className = `whitespace-nowrap lg:whitespace-normal text-left px-3 py-2 rounded-xl font-medium transition ${c.id === currentConversationId ? 'bg-blue-600 text-white' : 'bg-slate-100 hover:bg-slate-200 text-slate-700'}`;
                            btn.innerText = c.title;
                            btn.onclick = () => seleccionarConversacion(c.id);
                            bar.appendChild(btn);
                        });
                        if (!currentConversationId) currentConversationId = convs[0].id;
                        await cargarMensajes(currentConversationId);
                    } else {
                        await nuevaConversacion();
                    }
                }
            } catch (e) { console.error("Error loading conversations", e); }
        }

        async function seleccionarConversacion(id) {
            currentConversationId = id;
            await cargarConversaciones();
        }

        async function nuevaConversacion() {
            try {
                const res = await fetch('/api/conversations', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCookie('csrf_token') },
                    body: JSON.stringify({ title: 'New Consultation' })
                });
                if (res.ok) {
                    const conv = await res.json();
                    currentConversationId = conv.id;
                    await cargarConversaciones();
                }
            } catch (e) { console.error("Error creating conversation", e); }
        }

        async function eliminarConversacionActual() {
            if (!currentConversationId || !confirm("Are you sure you want to delete this conversation history?")) return;
            try {
                const res = await fetch(`/api/conversations/${currentConversationId}`, {
                    method: 'DELETE',
                    headers: { 'X-CSRF-Token': getCookie('csrf_token') }
                });
                if (res.ok) { currentConversationId = null; await cargarConversaciones(); }
            } catch (e) { console.error("Error deleting", e); }
        }

        async function cargarMensajes(convId) {
            try {
                const res = await fetch(`/api/conversations/${convId}/messages`);
                if (res.ok) {
                    const messages = await res.json();
                    const container = document.getElementById('chat-messages');
                    container.innerHTML = '';
                    if (messages.length === 0) {
                        const welcome = document.createElement('div');
                        welcome.className = 'chat-msg-executive p-3.5 mr-12 text-[13px] leading-relaxed self-start max-w-[85%]';
                        welcome.innerHTML = '<p class="font-bold text-slate-900 mb-1">Hello, I\'m Nura. How can I help you with your leads, customers, or credit simulations today?</p>';
                        container.appendChild(welcome);
                    } else {
                        messages.forEach(msg => {
                            const div = document.createElement('div');
                            div.className = msg.role === 'user' ? 'chat-msg-user p-3.5 ml-12 text-[13px] leading-relaxed self-end max-w-[85%]' : 'chat-msg-executive p-3.5 mr-12 text-[13px] leading-relaxed whitespace-pre-wrap self-start max-w-[85%]';
                            div.innerText = msg.content;
                            container.appendChild(div);
                        });
                    }
                    container.scrollTop = container.scrollHeight;
                }
            } catch (e) { console.error("Error loading messages", e); }
        }

        function handleKeyPress(e) { if (e.key === 'Enter') enviarMensajeChat(); }

        async function enviarMensajeChat() {
            const input = document.getElementById('chat-input');
            const texto = input.value.trim();
            if (!texto) return;

            input.value = '';
            const sendBtn = document.getElementById('send-chat-btn');
            sendBtn.disabled = true;

            const container = document.getElementById('chat-messages');
            const userDiv = document.createElement('div');
            userDiv.className = 'chat-msg-user p-3.5 ml-12 text-[13px] leading-relaxed self-end max-w-[85%]';
            userDiv.innerText = texto;
            container.appendChild(userDiv);

            const typingId = 'typing-' + Date.now();
            const typingDiv = document.createElement('div');
            typingDiv.id = typingId;
            typingDiv.className = 'chat-msg-executive p-3.5 mr-12 self-start max-w-[85%] flex items-center h-[46px] mt-2';
            typingDiv.innerHTML = '<div class="typing-indicator flex items-center h-full"><span></span><span></span><span></span></div>';
            container.appendChild(typingDiv);
            container.scrollTop = container.scrollHeight;

            try {
                const res = await fetch('/chat-ejecutivo', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCookie('csrf_token') },
                    body: JSON.stringify({ mensaje: texto, conversation_id: currentConversationId })
                });
                if (res.status === 401 || res.status === 403) {
                    mostrarAuthView();
                    return;
                }
                if (!res.ok) throw new Error(`Error (${res.status})`);
                const data = await res.json();
                currentConversationId = data.conversation_id;
                await cargarConversaciones();
            } catch (err) {
                document.getElementById(typingId)?.remove();
                alert("Connection error: " + err.message);
            } finally {
                sendBtn.disabled = false;
            }
        }

        window.currentOpenProfileSection = null;

        function openProfileSection(section) {
            window.currentOpenProfileSection = section;
            const modal = document.getElementById('profile-detail-modal');
            const card = document.getElementById('profile-detail-card');
            const titleEl = document.getElementById('profile-modal-title');
            const bodyEl = document.getElementById('profile-modal-body');
            const closeBtn = document.querySelector('#profile-modal-footer button');

            const email = currentUser?.email || t('common.not_available');
            const displayName = currentUser?.full_name || email.split('@')[0].toUpperCase();
            const roleName = currentUser?.role || 'Standard';
            const activeLang = NuraI18n.currentLang;

            if (closeBtn) {
                closeBtn.innerText = t('common.close');
            }

            if (section === 'personal') {
                titleEl.innerText = t('profile.personal.title');
                bodyEl.innerHTML = `
                    <div class="space-y-2.5">
                        <div class="p-3 rounded-xl bg-slate-50 border border-slate-100">
                            <p class="text-[10px] font-semibold text-slate-400 uppercase">${escapeHtml(t('profile.personal.email'))}</p>
                            <p class="text-xs font-bold text-slate-900 mt-0.5">${escapeHtml(email)}</p>
                        </div>
                        <div class="p-3 rounded-xl bg-slate-50 border border-slate-100">
                            <p class="text-[10px] font-semibold text-slate-400 uppercase">${escapeHtml(t('profile.personal.name'))}</p>
                            <p class="text-xs font-bold text-slate-900 mt-0.5">${escapeHtml(displayName)}</p>
                        </div>
                        <div class="p-3 rounded-xl bg-slate-50 border border-slate-100">
                            <p class="text-[10px] font-semibold text-slate-400 uppercase">${escapeHtml(t('profile.personal.role'))}</p>
                            <p class="text-xs font-bold text-blue-700 mt-0.5">${escapeHtml(tEnum(roleName))}</p>
                        </div>

                        <div class="pt-3 mt-3 border-t border-slate-200/80 space-y-2">
                            <div>
                                <p class="text-[10px] font-extrabold text-slate-700 uppercase tracking-wider">${escapeHtml(t('profile.lang.section_title'))}</p>
                                <p class="text-[11px] text-slate-500 mt-0.5">${escapeHtml(t('profile.lang.section_subtitle'))}</p>
                            </div>
                            <div class="grid grid-cols-2 gap-2 pt-1">
                                <button type="button" onclick="NuraI18n.setLanguage('en', { persist: true })"
                                    class="py-2.5 px-3 rounded-xl text-xs font-bold border transition flex items-center justify-center gap-1.5 ${activeLang === 'en' ? 'bg-blue-600 text-white border-blue-600 shadow-sm' : 'bg-slate-50 hover:bg-slate-100 text-slate-700 border-slate-200'}">
                                    <span>English</span>
                                    ${activeLang === 'en' ? '<span class="text-[10px]">✓</span>' : ''}
                                </button>
                                <button type="button" onclick="NuraI18n.setLanguage('es', { persist: true })"
                                    class="py-2.5 px-3 rounded-xl text-xs font-bold border transition flex items-center justify-center gap-1.5 ${activeLang === 'es' ? 'bg-blue-600 text-white border-blue-600 shadow-sm' : 'bg-slate-50 hover:bg-slate-100 text-slate-700 border-slate-200'}">
                                    <span>Español</span>
                                    ${activeLang === 'es' ? '<span class="text-[10px]">✓</span>' : ''}
                                </button>
                            </div>
                            <p id="profile-lang-status" class="hidden text-[11px] font-semibold text-emerald-700 pt-0.5"></p>
                        </div>
                    </div>
                `;
            } else {
                const isEs = (NuraI18n.currentLang === 'es');
                titleEl.innerText = t('profile.security.title');
                bodyEl.innerHTML = `
                    <div class="space-y-4">
                        <p class="text-xs text-slate-600 leading-relaxed">${escapeHtml(t('profile.security.modal_body'))}</p>

                        <form id="self-password-form" onsubmit="handleSelfPasswordChange(event)" class="pt-3 border-t border-slate-200/80 space-y-3 text-xs">
                            <div>
                                <p class="text-[10px] font-extrabold text-slate-700 uppercase tracking-wider">
                                    ${isEs ? 'Cambiar Contraseña de la Cuenta' : 'Change Account Password'}
                                </p>
                                <p class="text-[11px] text-slate-500 mt-0.5">
                                    ${isEs ? 'Tu sesión actual permanecerá activa y se cerrarán sesiones en otros dispositivos.' : 'Your current session stays active while other devices are signed out.'}
                                </p>
                            </div>

                            <div id="self-pw-msg" class="hidden p-2.5 rounded-xl text-xs font-medium"></div>

                            <div>
                                <label class="block text-[10px] font-bold text-slate-500 uppercase mb-1">
                                    ${isEs ? 'Contraseña Actual *' : 'Current Password *'}
                                </label>
                                <input type="password" id="self-pw-current" required class="w-full fintech-input rounded-xl p-2.5 text-xs" placeholder="••••••••">
                            </div>

                            <div>
                                <label class="block text-[10px] font-bold text-slate-500 uppercase mb-1">
                                    ${isEs ? 'Nueva Contraseña *' : 'New Password *'}
                                </label>
                                <input type="password" id="self-pw-new" required minlength="8" class="w-full fintech-input rounded-xl p-2.5 text-xs" placeholder="${isEs ? 'Mínimo 8 caracteres' : 'Minimum 8 characters'}">
                            </div>

                            <div>
                                <label class="block text-[10px] font-bold text-slate-500 uppercase mb-1">
                                    ${isEs ? 'Confirmar Nueva Contraseña *' : 'Confirm New Password *'}
                                </label>
                                <input type="password" id="self-pw-confirm" required minlength="8" class="w-full fintech-input rounded-xl p-2.5 text-xs" placeholder="${isEs ? 'Repite la nueva contraseña' : 'Re-enter new password'}">
                            </div>

                            <button type="submit" id="self-pw-submit-btn" class="w-full btn-primary py-2.5 rounded-xl text-xs shadow-sm">
                                ${isEs ? 'Actualizar Contraseña' : 'Update Password'}
                            </button>
                        </form>
                    </div>
                `;
            }

            if (modal.classList.contains('hidden')) {
                modal.classList.remove('hidden');
                setTimeout(() => {
                    modal.classList.remove('opacity-0');
                    card.classList.remove('scale-95', 'translate-y-4');
                }, 10);
            }
        }

        async function handleSelfPasswordChange(e) {
            e.preventDefault();
            const isEs = (NuraI18n.currentLang === 'es');
            const current_password = document.getElementById('self-pw-current')?.value || '';
            const new_password = document.getElementById('self-pw-new')?.value || '';
            const confirm_password = document.getElementById('self-pw-confirm')?.value || '';
            const msgBox = document.getElementById('self-pw-msg');
            const submitBtn = document.getElementById('self-pw-submit-btn');

            const showMsg = (text, isError = true) => {
                if (!msgBox) return;
                msgBox.className = isError
                    ? 'p-2.5 rounded-xl bg-red-50 border border-red-200 text-red-700 text-xs font-medium'
                    : 'p-2.5 rounded-xl bg-emerald-50 border border-emerald-200 text-emerald-800 text-xs font-semibold';
                msgBox.innerText = text;
                msgBox.classList.remove('hidden');
            };

            if (new_password !== confirm_password) {
                showMsg(isEs ? 'Las contraseñas nuevas no coinciden.' : 'New passwords do not match.', true);
                return;
            }
            if (new_password.trim().length < 8) {
                showMsg(isEs ? 'La nueva contraseña debe tener al menos 8 caracteres.' : 'New password must be at least 8 characters long.', true);
                return;
            }
            if (new_password === current_password) {
                showMsg(isEs ? 'La nueva contraseña debe ser diferente a la actual.' : 'New password must be different from your current password.', true);
                return;
            }

            if (msgBox) msgBox.classList.add('hidden');
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerText = isEs ? 'Actualizando...' : 'Updating...';
            }

            try {
                const res = await fetch('/api/auth/me/password', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': getCookie('csrf_token')
                    },
                    body: JSON.stringify({ current_password, new_password })
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    const rawErr = typeof data.detail === 'string' ? data.detail : 'Could not update password.';
                    const errMapEs = {
                        "Current password is incorrect.": "La contraseña actual es incorrecta.",
                        "New password must be different from your current password.": "La nueva contraseña debe ser diferente a la actual.",
                        "Password must be at least 8 characters long.": "La contraseña debe tener al menos 8 caracteres.",
                        "Could not update password.": "No fue posible actualizar la contraseña."
                    };
                    const finalErr = isEs ? (errMapEs[rawErr] || translateApiError(rawErr)) : rawErr;
                    throw new Error(finalErr);
                }

                document.getElementById('self-password-form')?.reset();
                showMsg(
                    isEs ? '✓ Contraseña actualizada con éxito.' : '✓ Password updated successfully.',
                    false
                );
            } catch (err) {
                showMsg(err.message, true);
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerText = isEs ? 'Actualizar Contraseña' : 'Update Password';
                }
            }
        }

        function closeProfileSection() {
            window.currentOpenProfileSection = null;
            const modal = document.getElementById('profile-detail-modal');
            const card = document.getElementById('profile-detail-card');
            modal.classList.add('opacity-0');
            card.classList.add('scale-95', 'translate-y-4');
            setTimeout(() => modal.classList.add('hidden'), 200);
        }

        function mostrarModalProximamente(accion) {
            document.getElementById('info-modal-action').innerText = accion;
            const modal = document.getElementById('info-modal');
            const content = document.getElementById('info-modal-content');
            modal.classList.remove('hidden');
            setTimeout(() => {
                modal.classList.remove('opacity-0');
                content.classList.remove('scale-95');
            }, 10);
        }

        function cerrarModalProximamente() {
            const modal = document.getElementById('info-modal');
            const content = document.getElementById('info-modal-content');
            modal.classList.add('opacity-0');
            content.classList.add('scale-95');
            setTimeout(() => modal.classList.add('hidden'), 200);
        }
