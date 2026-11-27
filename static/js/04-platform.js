        let platformNoticeTimer = null;

        function hidePlatformNotice() {
            if (platformNoticeTimer) {
                clearTimeout(platformNoticeTimer);
                platformNoticeTimer = null;
            }
            const banner = document.getElementById('platform-notice-banner');
            if (banner) banner.classList.add('hidden');
        }

        function showPlatformNotice(type, message) {
            const banner = document.getElementById('platform-notice-banner');
            const textEl = document.getElementById('platform-notice-text');
            if (!banner || !textEl) return;

            if (platformNoticeTimer) {
                clearTimeout(platformNoticeTimer);
                platformNoticeTimer = null;
            }

            const styles = {
                success: 'bg-emerald-50 border-emerald-200 text-emerald-800',
                error: 'bg-red-50 border-red-200 text-red-800',
                info: 'bg-blue-50 border-blue-200 text-blue-800',
                warning: 'bg-amber-50 border-amber-200 text-amber-800'
            };
            const tone = styles[type] || styles.info;

            banner.className = `mb-4 rounded-2xl border px-4 py-3 text-xs font-semibold flex items-center justify-between gap-3 shadow-sm no-print ${tone}`;
            textEl.innerText = String(message || '');
            banner.classList.remove('hidden');

            if (type === 'success' || type === 'info') {
                platformNoticeTimer = setTimeout(() => hidePlatformNotice(), 6000);
            }
        }

        async function platformFetch(url, options = {}) {
            const method = String(options.method || 'GET').toUpperCase();
            const isMutating = ['POST', 'PATCH', 'PUT', 'DELETE'].includes(method);
            const headers = Object.assign({ 'Accept': 'application/json' }, options.headers || {});

            let body = options.body;
            if (isMutating) {
                const csrfToken = getCookie('csrf_token');
                if (csrfToken && !headers['X-CSRF-Token']) {
                    headers['X-CSRF-Token'] = csrfToken;
                }
                if (body !== undefined && body !== null && !(body instanceof FormData)) {
                    if (typeof body !== 'string') {
                        body = JSON.stringify(body);
                    }
                    if (!headers['Content-Type']) {
                        headers['Content-Type'] = 'application/json';
                    }
                }
            }

            const fetchOptions = Object.assign({}, options, {
                method,
                credentials: 'same-origin',
                headers
            });
            if (body !== undefined) {
                fetchOptions.body = body;
            }

            let res;
            try {
                res = await fetch(url, fetchOptions);
            } catch (networkErr) {
                const err = new Error('Network error communicating with NURA Platform.');
                err.status = 0;
                err.detail = networkErr?.message || 'Network error';
                throw err;
            }

            let parsed = null;
            if (res.status !== 204) {
                const rawText = await res.text();
                if (rawText) {
                    try {
                        parsed = JSON.parse(rawText);
                    } catch (_) {
                        parsed = { detail: rawText };
                    }
                }
            }

            if (res.status === 401) {
                currentUser = null;
                mostrarAuthView();
                const err = new Error('Session expired. Please sign in again.');
                err.status = 401;
                err.detail = (parsed && parsed.detail) || 'Session expired';
                throw err;
            }

            if (!res.ok) {
                const detail = parsed ? (parsed.detail ?? parsed.message ?? parsed) : null;
                let message = '';
                if (typeof detail === 'string') {
                    message = detail;
                } else if (Array.isArray(detail)) {
                    message = detail.map(d => (d && d.msg ? d.msg : String(d))).join('; ');
                } else if (detail && typeof detail === 'object') {
                    message = JSON.stringify(detail);
                } else {
                    const fallbackByStatus = {
                        403: 'Access denied: platform superadmin privileges required.',
                        404: 'Requested platform resource was not found.',
                        409: 'Conflict: resource already exists or cannot be updated.',
                        422: 'Validation error: please review the submitted fields.',
                        500: 'Internal server error while processing platform request.'
                    };
                    message = fallbackByStatus[res.status] || `Request failed (${res.status})`;
                }
                const err = new Error(message);
                err.status = res.status;
                err.detail = detail;
                throw err;
            }

            return parsed;
        }

        function applyShellMode(platformMode, user) {
            hidePlatformNotice();

            const sideCommercial = document.getElementById('sidebar-commercial-nav');
            const sidePlatform = document.getElementById('sidebar-platform-nav');
            const topbarCommercial = document.getElementById('topbar-commercial-actions');
            const mobileCommercial = document.getElementById('mobile-commercial-nav');
            const mobilePlatform = document.getElementById('mobile-platform-nav');
            const floatingBtn = document.getElementById('floating-executive-btn');
            const sideSubtitle = document.getElementById('sidebar-brand-subtitle');
            const topbarContext = document.getElementById('topbar-context-label');
            const headerRoleBadge = document.getElementById('header-role-badge');
            const profileRoleBadge = document.getElementById('profile-role-badge');
            const profileTitle = document.getElementById('profile-section-title');
            const profileSubtitle = document.getElementById('profile-section-subtitle');
            const profileAdminHeader = document.getElementById('profile-admin-card-header');
            const teamBtn = document.getElementById('btn-team-management');
            const auditBtn = document.getElementById('btn-audit-logs');

            sideCommercial?.classList.toggle('hidden', platformMode);
            sidePlatform?.classList.toggle('hidden', !platformMode);
            topbarCommercial?.classList.toggle('hidden', platformMode);
            mobileCommercial?.classList.toggle('hidden', platformMode);
            mobilePlatform?.classList.toggle('hidden', !platformMode);
            floatingBtn?.classList.toggle('hidden', platformMode);

            if (platformMode) {
                const platformName = (user && user.tenant_name) ? user.tenant_name : 'NURA Platform';
                if (sideSubtitle) sideSubtitle.innerText = platformName;
                if (topbarContext) topbarContext.innerText = `${platformName} /`;
                if (headerRoleBadge) {
                    headerRoleBadge.innerText = 'SUPERADMIN';
                    headerRoleBadge.className = 'bg-indigo-50 text-indigo-700 border border-indigo-200 text-[9px] font-bold px-2 py-0.5 rounded-full uppercase';
                }
                if (profileRoleBadge) {
                    profileRoleBadge.innerText = 'SUPERADMIN';
                    profileRoleBadge.className = 'bg-indigo-50 text-indigo-700 border border-indigo-200/80 text-[10px] font-semibold px-2.5 py-1 rounded-full shrink-0';
                }
                if (profileTitle) profileTitle.innerText = (NuraI18n.currentLang === 'es') ? 'Cuenta y Seguridad' : 'Account & Security';
                if (profileSubtitle) profileSubtitle.innerText = (NuraI18n.currentLang === 'es') ? 'Administra tu información personal, idioma y contraseña de acceso' : 'Manage your personal details, language preferences, and account password';
                if (profileAdminHeader) profileAdminHeader.innerText = (NuraI18n.currentLang === 'es') ? 'Cuenta de Plataforma y Seguridad' : 'Platform Account & Security';
                teamBtn?.classList.add('hidden');
                auditBtn?.classList.add('hidden');
            } else {
                const orgName = (user && user.tenant_name) ? user.tenant_name : '';
                if (sideSubtitle) sideSubtitle.innerText = orgName || 'Credit Operations';
                if (topbarContext) topbarContext.innerText = orgName ? `${orgName} /` : 'NURA CRM /';
                if (headerRoleBadge) {
                    headerRoleBadge.innerText = user ? user.role : 'TEAM';
                    headerRoleBadge.className = 'bg-blue-50 text-blue-700 border border-blue-200 text-[9px] font-bold px-2 py-0.5 rounded-full uppercase';
                }
                if (profileRoleBadge) {
                    profileRoleBadge.innerText = user ? user.role : 'Active';
                    profileRoleBadge.className = 'bg-emerald-50 text-emerald-700 border border-emerald-200/60 text-[10px] font-semibold px-2.5 py-1 rounded-full shrink-0';
                }
                if (profileTitle) profileTitle.innerText = t('profile.title');
                if (profileSubtitle) profileSubtitle.innerText = t('profile.subtitle');
                if (profileAdminHeader) profileAdminHeader.innerText = t('profile.admin_header');

                const isOwnerAdmin = Boolean(user && user.role === 'OWNER_ADMIN');
                teamBtn?.classList.toggle('hidden', !isOwnerAdmin);
                auditBtn?.classList.toggle('hidden', !isOwnerAdmin);
            }
        }

        // Platform Console State (no credentials are ever stored in global state)
        const PLATFORM_SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
        let platformTenantsCache = [];
        let platformAuditLogsCache = [];
        let platformSelectedTenant = null;
        let platformPendingStatusTenantId = null;
        let platformSlugManuallyEdited = false;

        function resetShellMode() {
            hidePlatformNotice();
            platformTenantsCache = [];
            platformAuditLogsCache = [];
            platformSelectedTenant = null;
            platformPendingStatusTenantId = null;
            platformSlugManuallyEdited = false;
            closePlatformCreateOrgModal();
            closePlatformOrgDetailModal();
            closePlatformStatusConfirmModal();

            const auditEventEl = document.getElementById('plat-audit-event-type');
            const auditResEl = document.getElementById('plat-audit-resource-id');
            const auditLimitEl = document.getElementById('plat-audit-limit');
            const auditTbody = document.getElementById('plat-audit-tbody');
            const auditCountLabel = document.getElementById('plat-audit-count-label');
            if (auditEventEl) auditEventEl.value = '';
            if (auditResEl) auditResEl.value = '';
            if (auditLimitEl) auditLimitEl.value = '50';
            if (auditTbody) auditTbody.innerHTML = '<tr><td colspan="7" class="py-10 px-4 text-center text-slate-400">Loading platform audit logs...</td></tr>';
            if (auditCountLabel) auditCountLabel.innerText = 'Loading...';

            document.getElementById('sidebar-platform-nav')?.classList.add('hidden');
            document.getElementById('mobile-platform-nav')?.classList.add('hidden');
            document.getElementById('sidebar-commercial-nav')?.classList.remove('hidden');
            document.getElementById('topbar-commercial-actions')?.classList.remove('hidden');
            document.getElementById('mobile-commercial-nav')?.classList.remove('hidden');
            document.getElementById('floating-executive-btn')?.classList.remove('hidden');
            document.getElementById('btn-team-management')?.classList.add('hidden');
            document.getElementById('btn-audit-logs')?.classList.add('hidden');

            const sideSubtitle = document.getElementById('sidebar-brand-subtitle');
            const topbarContext = document.getElementById('topbar-context-label');
            const headerRoleBadge = document.getElementById('header-role-badge');
            const profileRoleBadge = document.getElementById('profile-role-badge');
            if (sideSubtitle) sideSubtitle.innerText = 'Credit Operations';
            if (topbarContext) topbarContext.innerText = 'NURA CRM /';
            if (headerRoleBadge) {
                headerRoleBadge.innerText = 'TEAM';
                headerRoleBadge.className = 'bg-blue-50 text-blue-700 border border-blue-200 text-[9px] font-bold px-2 py-0.5 rounded-full uppercase';
            }
            if (profileRoleBadge) {
                profileRoleBadge.innerText = 'Active';
                profileRoleBadge.className = 'bg-emerald-50 text-emerald-700 border border-emerald-200/60 text-[10px] font-semibold px-2.5 py-1 rounded-full shrink-0';
            }

            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.side-nav-item').forEach(el => el.classList.remove('active'));
        }

        /* =========================================================================
           2B. PLATFORM CONSOLE — ORGANIZATIONS, ONBOARDING, DETAIL & STATUS TOGGLE
           ========================================================================= */
        function generateSlugFromOrgName(rawName) {
            return String(rawName || '')
                .normalize('NFKD')
                .replace(/[\u0300-\u036f]/g, '')
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, '-')
                .replace(/^-+|-+$/g, '')
                .replace(/-{2,}/g, '-')
                .slice(0, 80);
        }

        function updatePlatformOrgsKPIs(tenants) {
            const list = Array.isArray(tenants) ? tenants : [];
            const total = list.length;
            const active = list.filter(item => Boolean(item.is_active)).length;
            const suspended = total - active;
            const totalUsers = list.reduce((acc, item) => acc + (Number(item.users_count) || 0), 0);

            const elTotal = document.getElementById('plat-kpi-total');
            const elActive = document.getElementById('plat-kpi-active');
            const elSuspended = document.getElementById('plat-kpi-suspended');
            const elUsers = document.getElementById('plat-kpi-users');
            if (elTotal) elTotal.innerText = String(total);
            if (elActive) elActive.innerText = String(active);
            if (elSuspended) elSuspended.innerText = String(suspended);
            if (elUsers) elUsers.innerText = String(totalUsers);
        }

        function renderPlatformOrganizations() {
            const tbody = document.getElementById('plat-orgs-tbody');
            const countLabel = document.getElementById('plat-orgs-count-label');
            if (!tbody) return;

            const search = (document.getElementById('plat-orgs-search')?.value || '').trim().toLowerCase();
            const statusFilter = document.getElementById('plat-orgs-status-filter')?.value || 'ALL';

            const filtered = platformTenantsCache.filter(item => {
                if (statusFilter === 'ACTIVE' && !item.is_active) return false;
                if (statusFilter === 'SUSPENDED' && item.is_active) return false;
                if (search) {
                    const haystack = `${item.name || ''} ${item.legal_name || ''} ${item.slug || ''}`.toLowerCase();
                    if (!haystack.includes(search)) return false;
                }
                return true;
            });

            if (countLabel) {
                countLabel.innerText = `Showing ${filtered.length} of ${platformTenantsCache.length} organizations`;
            }

            if (filtered.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="6" class="py-10 px-4 text-center">
                            <p class="text-xs font-bold text-slate-700">No organizations found</p>
                            <p class="text-[11px] text-slate-500 mt-0.5">Adjust the search or status filter, or onboard a new organization.</p>
                        </td>
                    </tr>
                `;
                return;
            }

            tbody.innerHTML = filtered.map(item => {
                const tenantId = Number(item.id);
                const isPlatformTenant = (item.slug === 'platform');
                const isActive = Boolean(item.is_active);
                const usersCount = Number(item.users_count) || 0;
                const customersCount = Number(item.customers_count) || 0;
                const loansCount = Number(item.loans_count) || 0;

                const statusBadge = isActive
                    ? '<span class="text-[10px] font-bold px-2.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">Active</span>'
                    : '<span class="text-[10px] font-bold px-2.5 py-0.5 rounded-full bg-red-50 text-red-700 border border-red-200">Suspended</span>';

                const systemBadge = isPlatformTenant
                    ? '<span class="text-[9px] font-extrabold uppercase tracking-wider px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-200">System</span>'
                    : '';

                const statusActionHtml = isPlatformTenant
                    ? '<span class="text-[10px] font-semibold text-slate-400 px-2 py-1">Protected</span>'
                    : (isActive
                        ? `<button type="button" onclick="openPlatformStatusConfirmModal(${tenantId})" class="py-1.5 px-2.5 rounded-lg bg-red-50 hover:bg-red-100 text-red-700 border border-red-200 text-[11px] font-bold transition">Suspend</button>`
                        : `<button type="button" onclick="openPlatformStatusConfirmModal(${tenantId})" class="py-1.5 px-2.5 rounded-lg bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200 text-[11px] font-bold transition">Activate</button>`
                    );

                const legalLine = item.legal_name
                    ? `<p class="text-[11px] text-slate-500 truncate max-w-[240px]">${escapeHtml(item.legal_name)}</p>`
                    : '';

                return `
                    <tr class="hover:bg-slate-50/80 transition">
                        <td class="py-3.5 px-4">
                            <p class="font-bold text-slate-900">${escapeHtml(item.name)}</p>
                            ${legalLine}
                        </td>
                        <td class="py-3.5 px-4">
                            <div class="flex items-center gap-1.5 flex-wrap">
                                <span class="font-mono text-[11px] font-semibold px-2 py-0.5 rounded-lg bg-slate-100 text-slate-700 border border-slate-200">${escapeHtml(item.slug)}</span>
                                ${systemBadge}
                            </div>
                        </td>
                        <td class="py-3.5 px-4">${statusBadge}</td>
                        <td class="py-3.5 px-4">
                            <div class="flex items-center gap-1.5 flex-wrap text-[11px] text-slate-600">
                                <span class="px-2 py-0.5 rounded-lg bg-slate-100 font-semibold">${usersCount} users</span>
                                <span class="px-2 py-0.5 rounded-lg bg-slate-100 font-semibold">${customersCount} customers</span>
                                <span class="px-2 py-0.5 rounded-lg bg-slate-100 font-semibold">${loansCount} loans</span>
                            </div>
                        </td>
                        <td class="py-3.5 px-4 text-[11px] text-slate-500 whitespace-nowrap">${escapeHtml(formatDateTimeShort(item.created_at))}</td>
                        <td class="py-3.5 px-4 text-right whitespace-nowrap">
                            <div class="inline-flex items-center gap-1.5">
                                <button type="button" onclick="openPlatformOrgDetailModal(${tenantId})" class="py-1.5 px-2.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-[11px] font-bold transition">View / Edit</button>
                                ${statusActionHtml}
                            </div>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        async function loadPlatformOrganizations() {
            if (!isPlatformSuperadmin(currentUser)) return;
            const tbody = document.getElementById('plat-orgs-tbody');
            const countLabel = document.getElementById('plat-orgs-count-label');
            const refreshBtn = document.getElementById('btn-platform-refresh-orgs');

            if (tbody && platformTenantsCache.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" class="py-10 px-4 text-center text-slate-400">Loading organizations...</td></tr>';
            }
            if (countLabel) countLabel.innerText = 'Loading...';
            if (refreshBtn) refreshBtn.disabled = true;

            try {
                const data = await platformFetch('/api/platform/tenants');
                platformTenantsCache = Array.isArray(data) ? data : [];
                updatePlatformOrgsKPIs(platformTenantsCache);
                renderPlatformOrganizations();
            } catch (err) {
                if (err.status !== 401) {
                    showPlatformNotice('error', err.message || 'Could not load organizations.');
                    if (tbody) {
                        tbody.innerHTML = `
                            <tr>
                                <td colspan="6" class="py-10 px-4 text-center space-y-2">
                                    <p class="text-xs font-bold text-red-700">Failed to load organizations</p>
                                    <p class="text-[11px] text-slate-500">${escapeHtml(err.message || 'Unexpected error')}</p>
                                    <button type="button" onclick="loadPlatformOrganizations()" class="mt-2 py-1.5 px-3 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-[11px] font-bold">Retry</button>
                                </td>
                            </tr>
                        `;
                    }
                    if (countLabel) countLabel.innerText = 'Error loading directory';
                }
            } finally {
                if (refreshBtn) refreshBtn.disabled = false;
            }
        }

        function clearPlatformCreatePasswordFields() {
            const pwInput = document.getElementById('plat-create-owner-password');
            const confirmInput = document.getElementById('plat-create-owner-password-confirm');
            const toggleBtn = document.getElementById('plat-create-pw-toggle');
            if (pwInput) {
                pwInput.value = '';
                pwInput.type = 'password';
            }
            if (confirmInput) {
                confirmInput.value = '';
                confirmInput.type = 'password';
            }
            if (toggleBtn) toggleBtn.innerText = 'Show';
        }

        function togglePlatformPasswordVisibility(inputId, btnEl) {
            const input = document.getElementById(inputId);
            if (!input) return;
            const reveal = (input.type === 'password');
            input.type = reveal ? 'text' : 'password';
            if (btnEl) btnEl.innerText = reveal ? 'Hide' : 'Show';
        }

        function handlePlatformOrgNameInput() {
            if (platformSlugManuallyEdited) return;
            const nameVal = document.getElementById('plat-create-name')?.value || '';
            const slugInput = document.getElementById('plat-create-slug');
            if (slugInput) {
                slugInput.value = generateSlugFromOrgName(nameVal);
            }
        }

        function handlePlatformOrgSlugManualInput() {
            const slugInput = document.getElementById('plat-create-slug');
            if (!slugInput) return;
            platformSlugManuallyEdited = slugInput.value.trim().length > 0;
        }

        function openPlatformCreateOrgModal() {
            if (!isPlatformSuperadmin(currentUser)) return;
            const form = document.getElementById('platform-create-org-form');
            if (form) form.reset();
            clearPlatformCreatePasswordFields();
            platformSlugManuallyEdited = false;

            const errBox = document.getElementById('plat-create-org-error');
            if (errBox) {
                errBox.innerText = '';
                errBox.classList.add('hidden');
            }
            const submitBtn = document.getElementById('plat-create-org-submit');
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerText = 'Create Organization';
            }
            document.getElementById('platform-create-org-modal')?.classList.remove('hidden');
        }

        function closePlatformCreateOrgModal() {
            clearPlatformCreatePasswordFields();
            document.getElementById('platform-create-org-modal')?.classList.add('hidden');
        }

        async function handlePlatformCreateOrgSubmit(e) {
            e.preventDefault();
            if (!isPlatformSuperadmin(currentUser)) return;

            const errBox = document.getElementById('plat-create-org-error');
            const submitBtn = document.getElementById('plat-create-org-submit');
            const showErr = (msg) => {
                if (errBox) {
                    errBox.innerText = msg;
                    errBox.classList.remove('hidden');
                }
            };
            if (errBox) errBox.classList.add('hidden');

            const name = (document.getElementById('plat-create-name')?.value || '').trim();
            const legalNameRaw = (document.getElementById('plat-create-legal-name')?.value || '').trim();
            const slug = (document.getElementById('plat-create-slug')?.value || '').trim().toLowerCase();
            const ownerFullNameRaw = (document.getElementById('plat-create-owner-name')?.value || '').trim();
            const ownerEmail = (document.getElementById('plat-create-owner-email')?.value || '').trim().toLowerCase();
            const ownerPassword = document.getElementById('plat-create-owner-password')?.value || '';
            const ownerPasswordConfirm = document.getElementById('plat-create-owner-password-confirm')?.value || '';

            const slugInput = document.getElementById('plat-create-slug');
            if (slugInput) slugInput.value = slug;

            if (name.length < 2) {
                showErr('Organization name must be at least 2 characters.');
                return;
            }
            if (!PLATFORM_SLUG_RE.test(slug) || slug.length < 2) {
                showErr("Invalid slug. Use lowercase letters, numbers, and hyphens only (e.g., 'acme-credit').");
                return;
            }
            if (!ownerEmail || !ownerEmail.includes('@')) {
                showErr('Please enter a valid email address for the initial OWNER_ADMIN.');
                return;
            }
            if (ownerPassword.trim().length < 8 || ownerPassword.length > 128) {
                showErr('Initial password must be between 8 and 128 characters.');
                return;
            }
            if (ownerPassword !== ownerPasswordConfirm) {
                showErr('Password confirmation does not match.');
                return;
            }

            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerText = 'Creating...';
            }

            try {
                const payload = {
                    name,
                    slug,
                    legal_name: legalNameRaw || null,
                    owner_email: ownerEmail,
                    owner_password: ownerPassword,
                    owner_full_name: ownerFullNameRaw || null
                };
                const created = await platformFetch('/api/platform/tenants', {
                    method: 'POST',
                    body: JSON.stringify(payload)
                });
                clearPlatformCreatePasswordFields();
                closePlatformCreateOrgModal();
                const createdSlug = created?.tenant?.slug || slug;
                const createdEmail = created?.owner_admin?.email || ownerEmail;
                showPlatformNotice('success', `Organization "${createdSlug}" created with initial OWNER_ADMIN ${createdEmail}.`);
                await loadPlatformOrganizations();
            } catch (err) {
                if (err.status !== 401) {
                    showErr(err.message || 'Could not create organization.');
                }
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerText = 'Create Organization';
                }
            }
        }

        function switchPlatformDetailTab(tabName) {
            const isOverview = (tabName !== 'users');
            const btnOverview = document.getElementById('plat-detail-tab-btn-overview');
            const btnUsers = document.getElementById('plat-detail-tab-btn-users');
            const paneOverview = document.getElementById('plat-detail-pane-overview');
            const paneUsers = document.getElementById('plat-detail-pane-users');

            if (btnOverview) {
                btnOverview.className = isOverview
                    ? 'px-3.5 py-1.5 rounded-xl text-xs font-bold bg-slate-900 text-white transition'
                    : 'px-3.5 py-1.5 rounded-xl text-xs font-bold bg-slate-100 text-slate-600 hover:bg-slate-200 transition';
            }
            if (btnUsers) {
                btnUsers.className = !isOverview
                    ? 'px-3.5 py-1.5 rounded-xl text-xs font-bold bg-slate-900 text-white transition'
                    : 'px-3.5 py-1.5 rounded-xl text-xs font-bold bg-slate-100 text-slate-600 hover:bg-slate-200 transition';
            }
            paneOverview?.classList.toggle('hidden', !isOverview);
            paneUsers?.classList.toggle('hidden', isOverview);
        }

        function renderPlatformOrgDetail(detail, users) {
            platformSelectedTenant = detail;
            const isPlatformTenant = (detail.slug === 'platform');
            const isActive = Boolean(detail.is_active);

            const titleEl = document.getElementById('plat-detail-title');
            const slugBadge = document.getElementById('plat-detail-slug-badge');
            const statusBadge = document.getElementById('plat-detail-status-badge');
            const systemBadge = document.getElementById('plat-detail-system-badge');
            const createdAtEl = document.getElementById('plat-detail-created-at');

            if (titleEl) titleEl.innerText = detail.name || 'Organization';
            if (slugBadge) slugBadge.innerText = detail.slug || '—';
            if (statusBadge) {
                statusBadge.innerText = isActive ? 'Active' : 'Suspended';
                statusBadge.className = isActive
                    ? 'text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200'
                    : 'text-[10px] font-bold px-2 py-0.5 rounded-full bg-red-50 text-red-700 border border-red-200';
            }
            systemBadge?.classList.toggle('hidden', !isPlatformTenant);
            if (createdAtEl) createdAtEl.innerText = `Created: ${formatDateTimeShort(detail.created_at)}`;

            const setVal = (id, val) => {
                const el = document.getElementById(id);
                if (el) el.innerText = String(val);
            };
            setVal('plat-detail-kpi-users', Number(detail.users_count) || 0);
            setVal('plat-detail-kpi-active-users', Number(detail.active_users_count) || 0);
            setVal('plat-detail-kpi-customers', Number(detail.customers_count) || 0);
            setVal('plat-detail-kpi-leads', Number(detail.leads_count) || 0);
            setVal('plat-detail-kpi-loans', Number(detail.loans_count) || 0);

            const slugInput = document.getElementById('plat-edit-slug-readonly');
            const nameInput = document.getElementById('plat-edit-name');
            const legalInput = document.getElementById('plat-edit-legal-name');
            if (slugInput) slugInput.value = detail.slug || '';
            if (nameInput) nameInput.value = detail.name || '';
            if (legalInput) legalInput.value = detail.legal_name || '';

            const editErr = document.getElementById('plat-edit-org-error');
            if (editErr) {
                editErr.innerText = '';
                editErr.classList.add('hidden');
            }

            const statusBtn = document.getElementById('plat-detail-status-action-btn');
            if (statusBtn) {
                if (isPlatformTenant) {
                    statusBtn.classList.add('hidden');
                } else {
                    statusBtn.classList.remove('hidden');
                    if (isActive) {
                        statusBtn.innerText = 'Suspend Organization';
                        statusBtn.className = 'py-2 px-3.5 rounded-xl text-xs font-bold border transition bg-red-50 hover:bg-red-100 text-red-700 border-red-200';
                    } else {
                        statusBtn.innerText = 'Activate Organization';
                        statusBtn.className = 'py-2 px-3.5 rounded-xl text-xs font-bold border transition bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border-emerald-200';
                    }
                }
            }

            const userList = Array.isArray(users) ? users : [];
            const tabCount = document.getElementById('plat-detail-users-tab-count');
            if (tabCount) tabCount.innerText = `(${userList.length})`;

            const usersTbody = document.getElementById('plat-detail-users-tbody');
            if (usersTbody) {
                if (userList.length === 0) {
                    usersTbody.innerHTML = '<tr><td colspan="4" class="py-8 px-3.5 text-center text-slate-400">No users registered in this organization.</td></tr>';
                } else {
                    usersTbody.innerHTML = userList.map(u => {
                        const uActive = Boolean(u.is_active);
                        const uStatus = uActive
                            ? '<span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">Active</span>'
                            : '<span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-red-50 text-red-700 border border-red-200">Inactive</span>';
                        return `
                            <tr class="hover:bg-slate-50/70">
                                <td class="py-2.5 px-3.5">
                                    <p class="font-bold text-slate-900">${escapeHtml(u.full_name || '—')}</p>
                                    <p class="text-[11px] text-slate-500">${escapeHtml(u.email)}</p>
                                </td>
                                <td class="py-2.5 px-3.5">
                                    <span class="font-mono text-[10px] font-bold px-2 py-0.5 rounded-lg bg-slate-100 text-slate-700 border border-slate-200">${escapeHtml(u.role)}</span>
                                </td>
                                <td class="py-2.5 px-3.5">${uStatus}</td>
                                <td class="py-2.5 px-3.5 text-[11px] text-slate-500 whitespace-nowrap">${escapeHtml(formatDateTimeShort(u.created_at))}</td>
                            </tr>
                        `;
                    }).join('');
                }
            }
        }

        async function openPlatformOrgDetailModal(tenantId) {
            if (!isPlatformSuperadmin(currentUser)) return;
            const idNum = Number(tenantId);
            if (!Number.isFinite(idNum) || idNum <= 0) return;

            const modal = document.getElementById('platform-org-detail-modal');
            const loadingEl = document.getElementById('plat-detail-loading');
            const errEl = document.getElementById('plat-detail-error');
            const bodyEl = document.getElementById('plat-detail-body');

            switchPlatformDetailTab('overview');
            if (errEl) {
                errEl.innerText = '';
                errEl.classList.add('hidden');
            }
            loadingEl?.classList.remove('hidden');
            bodyEl?.classList.add('hidden');
            modal?.classList.remove('hidden');

            try {
                const [detail, users] = await Promise.all([
                    platformFetch(`/api/platform/tenants/${idNum}`),
                    platformFetch(`/api/platform/tenants/${idNum}/users`)
                ]);
                renderPlatformOrgDetail(detail, users);
                bodyEl?.classList.remove('hidden');
            } catch (err) {
                if (err.status !== 401) {
                    if (errEl) {
                        errEl.innerText = err.message || 'Could not load organization details.';
                        errEl.classList.remove('hidden');
                    }
                    showPlatformNotice('error', err.message || 'Could not load organization details.');
                }
            } finally {
                loadingEl?.classList.add('hidden');
            }
        }

        function closePlatformOrgDetailModal() {
            platformSelectedTenant = null;
            document.getElementById('platform-org-detail-modal')?.classList.add('hidden');
        }

        async function handlePlatformEditOrgSubmit(e) {
            e.preventDefault();
            if (!isPlatformSuperadmin(currentUser) || !platformSelectedTenant) return;

            const tenantId = Number(platformSelectedTenant.id);
            const errBox = document.getElementById('plat-edit-org-error');
            const submitBtn = document.getElementById('plat-edit-org-submit');
            if (errBox) errBox.classList.add('hidden');

            const name = (document.getElementById('plat-edit-name')?.value || '').trim();
            const legalName = (document.getElementById('plat-edit-legal-name')?.value || '').trim();

            if (name.length < 2) {
                if (errBox) {
                    errBox.innerText = 'Organization name must be at least 2 characters.';
                    errBox.classList.remove('hidden');
                }
                return;
            }

            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerText = 'Saving...';
            }

            try {
                const updated = await platformFetch(`/api/platform/tenants/${tenantId}`, {
                    method: 'PATCH',
                    body: JSON.stringify({
                        name,
                        legal_name: legalName
                    })
                });
                const merged = Object.assign({}, platformSelectedTenant, updated);
                const [freshDetail, freshUsers] = await Promise.all([
                    platformFetch(`/api/platform/tenants/${tenantId}`),
                    platformFetch(`/api/platform/tenants/${tenantId}/users`)
                ]);
                renderPlatformOrgDetail(freshDetail || merged, freshUsers);
                showPlatformNotice('success', `Organization "${updated.slug}" settings updated.`);
                await loadPlatformOrganizations();
            } catch (err) {
                if (err.status !== 401 && errBox) {
                    errBox.innerText = err.message || 'Could not update organization settings.';
                    errBox.classList.remove('hidden');
                }
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerText = 'Save Changes';
                }
            }
        }

        function openPlatformStatusConfirmFromDetail() {
            if (!platformSelectedTenant || platformSelectedTenant.slug === 'platform') return;
            openPlatformStatusConfirmModal(Number(platformSelectedTenant.id));
        }

        function openPlatformStatusConfirmModal(tenantId) {
            if (!isPlatformSuperadmin(currentUser)) return;
            const idNum = Number(tenantId);
            const tenant = platformTenantsCache.find(t => Number(t.id) === idNum)
                || (platformSelectedTenant && Number(platformSelectedTenant.id) === idNum ? platformSelectedTenant : null);

            // Hard guard: never open suspend/activate confirmation for the system platform tenant
            if (!tenant || tenant.slug === 'platform') return;

            platformPendingStatusTenantId = idNum;
            const willSuspend = Boolean(tenant.is_active);

            const kickerEl = document.getElementById('plat-confirm-kicker');
            const titleEl = document.getElementById('plat-confirm-title');
            const orgNameEl = document.getElementById('plat-confirm-org-name');
            const orgSlugEl = document.getElementById('plat-confirm-org-slug');
            const msgBox = document.getElementById('plat-confirm-message-box');
            const errBox = document.getElementById('plat-confirm-status-error');
            const submitBtn = document.getElementById('plat-confirm-submit-btn');

            if (orgNameEl) orgNameEl.innerText = tenant.name || 'Organization';
            if (orgSlugEl) orgSlugEl.innerText = tenant.slug || '—';
            if (errBox) {
                errBox.innerText = '';
                errBox.classList.add('hidden');
            }

            if (willSuspend) {
                if (kickerEl) {
                    kickerEl.innerText = 'High-Impact Action';
                    kickerEl.className = 'text-[10px] font-extrabold uppercase tracking-wider px-2.5 py-0.5 rounded-full bg-red-50 text-red-700 border border-red-200';
                }
                if (titleEl) titleEl.innerText = 'Suspend Organization?';
                if (msgBox) {
                    msgBox.className = 'p-3.5 rounded-2xl border bg-amber-50 border-amber-200 text-amber-900 text-xs leading-relaxed';
                    msgBox.innerText = 'Suspending this organization will immediately revoke all active user sessions and block all users in this tenant from signing in until the organization is reactivated.';
                }
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerText = 'Suspend Organization';
                    submitBtn.className = 'py-2.5 px-4 rounded-xl text-xs font-bold text-white bg-red-600 hover:bg-red-700 shadow-sm transition';
                }
            } else {
                if (kickerEl) {
                    kickerEl.innerText = 'Restore Access';
                    kickerEl.className = 'text-[10px] font-extrabold uppercase tracking-wider px-2.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200';
                }
                if (titleEl) titleEl.innerText = 'Activate Organization?';
                if (msgBox) {
                    msgBox.className = 'p-3.5 rounded-2xl border bg-emerald-50 border-emerald-200 text-emerald-900 text-xs leading-relaxed';
                    msgBox.innerText = 'Reactivating this organization will allow its active users to sign in and resume commercial CRM operations immediately.';
                }
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerText = 'Activate Organization';
                    submitBtn.className = 'py-2.5 px-4 rounded-xl text-xs font-bold text-white bg-emerald-600 hover:bg-emerald-700 shadow-sm transition';
                }
            }

            document.getElementById('platform-status-confirm-modal')?.classList.remove('hidden');
        }

        function closePlatformStatusConfirmModal() {
            platformPendingStatusTenantId = null;
            document.getElementById('platform-status-confirm-modal')?.classList.add('hidden');
        }

        async function executePlatformStatusToggle() {
            if (!isPlatformSuperadmin(currentUser) || !platformPendingStatusTenantId) return;
            const idNum = Number(platformPendingStatusTenantId);
            const tenant = platformTenantsCache.find(t => Number(t.id) === idNum)
                || (platformSelectedTenant && Number(platformSelectedTenant.id) === idNum ? platformSelectedTenant : null);

            if (!tenant || tenant.slug === 'platform') {
                closePlatformStatusConfirmModal();
                return;
            }

            const targetActive = !Boolean(tenant.is_active);
            const errBox = document.getElementById('plat-confirm-status-error');
            const submitBtn = document.getElementById('plat-confirm-submit-btn');
            if (errBox) errBox.classList.add('hidden');

            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerText = targetActive ? 'Activating...' : 'Suspending...';
            }

            try {
                const updated = await platformFetch(`/api/platform/tenants/${idNum}`, {
                    method: 'PATCH',
                    body: JSON.stringify({ is_active: targetActive })
                });
                closePlatformStatusConfirmModal();
                const actionLabel = targetActive ? 'activated' : 'suspended (active sessions revoked)';
                showPlatformNotice('success', `Organization "${updated.slug}" has been ${actionLabel}.`);
                await loadPlatformOrganizations();

                const detailModal = document.getElementById('platform-org-detail-modal');
                if (detailModal && !detailModal.classList.contains('hidden') && platformSelectedTenant && Number(platformSelectedTenant.id) === idNum) {
                    await openPlatformOrgDetailModal(idNum);
                }
            } catch (err) {
                if (err.status !== 401 && errBox) {
                    errBox.innerText = err.message || 'Could not update organization status.';
                    errBox.classList.remove('hidden');
                }
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerText = targetActive ? 'Activate Organization' : 'Suspend Organization';
                }
            }
        }

        /* =========================================================================
           2C. PLATFORM CONSOLE — PLATFORM AUDIT LOGS (/api/platform/audit-logs)
           ========================================================================= */
        const PLATFORM_AUDIT_SENSITIVE_KEY_RE = /(password|secret|token|hash|cookie|authorization)/i;

        function formatPlatformAuditDetails(details) {
            if (!details || typeof details !== 'object' || Array.isArray(details)) {
                return '<span class="text-slate-400">—</span>';
            }
            const entries = Object.entries(details).filter(([k, v]) => {
                if (PLATFORM_AUDIT_SENSITIVE_KEY_RE.test(String(k))) return false;
                return v !== null && v !== undefined && String(v).trim() !== '';
            });
            if (entries.length === 0) {
                return '<span class="text-slate-400">—</span>';
            }
            const chips = entries.map(([k, v]) => {
                const rawVal = (typeof v === 'object') ? JSON.stringify(v) : String(v);
                return `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-slate-100 border border-slate-200/80 text-[10px] text-slate-700"><span class="font-bold text-slate-500">${escapeHtml(k)}:</span><span class="font-mono break-all">${escapeHtml(rawVal)}</span></span>`;
            }).join(' ');
            return `<div class="flex flex-wrap gap-1 max-w-[320px]">${chips}</div>`;
        }

        function renderPlatformAuditLogs(logs) {
            const tbody = document.getElementById('plat-audit-tbody');
            const countLabel = document.getElementById('plat-audit-count-label');
            if (!tbody) return;

            const list = Array.isArray(logs) ? logs : [];
            if (countLabel) {
                countLabel.innerText = `Showing ${list.length} platform audit event${list.length === 1 ? '' : 's'}`;
            }

            if (list.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="7" class="py-10 px-4 text-center">
                            <p class="text-xs font-bold text-slate-700">No platform audit events found</p>
                            <p class="text-[11px] text-slate-500 mt-0.5">Try clearing the event type or resource ID filters.</p>
                        </td>
                    </tr>
                `;
                return;
            }

            tbody.innerHTML = list.map(item => {
                const outcome = String(item.outcome || '').toUpperCase();
                let outcomeBadgeClass = 'bg-amber-50 text-amber-800 border-amber-200';
                if (outcome === 'SUCCESS') {
                    outcomeBadgeClass = 'bg-emerald-50 text-emerald-700 border-emerald-200';
                } else if (outcome === 'DENIED') {
                    outcomeBadgeClass = 'bg-red-50 text-red-700 border-red-200';
                }

                const actorRole = item.actor_role ? escapeHtml(item.actor_role) : 'SYSTEM';
                const actorIdText = (item.actor_user_id !== null && item.actor_user_id !== undefined)
                    ? `<span class="text-[10px] text-slate-400 font-mono">#${escapeHtml(String(item.actor_user_id))}</span>`
                    : '';

                const resType = item.resource_type ? escapeHtml(item.resource_type) : '—';
                const resId = (item.resource_id !== null && item.resource_id !== undefined && String(item.resource_id).trim() !== '')
                    ? `<p class="text-[10px] font-mono text-slate-500 truncate max-w-[180px]" title="${escapeHtml(item.resource_id)}">${escapeHtml(item.resource_id)}</p>`
                    : '';

                const ipDisplay = item.ip_address ? escapeHtml(item.ip_address) : '—';

                return `
                    <tr class="hover:bg-slate-50/80 transition align-top">
                        <td class="py-3 px-4 text-[11px] text-slate-600 whitespace-nowrap">${escapeHtml(formatDateTimeShort(item.created_at))}</td>
                        <td class="py-3 px-4 whitespace-nowrap">
                            <span class="font-mono text-[10px] font-bold px-2 py-0.5 rounded-lg bg-slate-100 text-slate-800 border border-slate-200">${escapeHtml(item.event_type || '—')}</span>
                        </td>
                        <td class="py-3 px-4 whitespace-nowrap">
                            <span class="text-[10px] font-bold px-2.5 py-0.5 rounded-full border ${outcomeBadgeClass}">${escapeHtml(outcome || '—')}</span>
                        </td>
                        <td class="py-3 px-4 whitespace-nowrap">
                            <div class="flex items-center gap-1.5">
                                <span class="text-[11px] font-semibold text-slate-800">${actorRole}</span>
                                ${actorIdText}
                            </div>
                        </td>
                        <td class="py-3 px-4">
                            <p class="text-[11px] font-semibold text-slate-700">${resType}</p>
                            ${resId}
                        </td>
                        <td class="py-3 px-4 font-mono text-[11px] text-slate-500 whitespace-nowrap">${ipDisplay}</td>
                        <td class="py-3 px-4">${formatPlatformAuditDetails(item.details)}</td>
                    </tr>
                `;
            }).join('');
        }

        async function loadPlatformAuditLogs() {
            if (!isPlatformSuperadmin(currentUser)) return;
            const tbody = document.getElementById('plat-audit-tbody');
            const countLabel = document.getElementById('plat-audit-count-label');
            const refreshBtn = document.getElementById('btn-platform-audit-refresh');
            const applyBtn = document.getElementById('btn-platform-audit-apply');

            const eventType = (document.getElementById('plat-audit-event-type')?.value || '').trim();
            const resourceId = (document.getElementById('plat-audit-resource-id')?.value || '').trim();
            const rawLimit = Number(document.getElementById('plat-audit-limit')?.value || 50);
            const safeLimit = [50, 100, 200].includes(rawLimit) ? rawLimit : 50;

            const params = new URLSearchParams({ limit: String(safeLimit) });
            if (eventType) params.set('event_type', eventType);
            if (resourceId) params.set('resource_id', resourceId);

            if (tbody) {
                tbody.innerHTML = '<tr><td colspan="7" class="py-10 px-4 text-center text-slate-400">Loading platform audit logs...</td></tr>';
            }
            if (countLabel) countLabel.innerText = 'Loading...';
            if (refreshBtn) refreshBtn.disabled = true;
            if (applyBtn) applyBtn.disabled = true;

            try {
                const data = await platformFetch(`/api/platform/audit-logs?${params.toString()}`);
                platformAuditLogsCache = Array.isArray(data) ? data : [];
                renderPlatformAuditLogs(platformAuditLogsCache);
            } catch (err) {
                if (err.status !== 401) {
                    showPlatformNotice('error', err.message || 'Could not load platform audit logs.');
                    if (tbody) {
                        tbody.innerHTML = `
                            <tr>
                                <td colspan="7" class="py-10 px-4 text-center space-y-2">
                                    <p class="text-xs font-bold text-red-700">Failed to load platform audit logs</p>
                                    <p class="text-[11px] text-slate-500">${escapeHtml(err.message || 'Unexpected error')}</p>
                                    <button type="button" onclick="loadPlatformAuditLogs()" class="mt-2 py-1.5 px-3 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-[11px] font-bold">Retry</button>
                                </td>
                            </tr>
                        `;
                    }
                    if (countLabel) countLabel.innerText = 'Error loading audit logs';
                }
            } finally {
                if (refreshBtn) refreshBtn.disabled = false;
                if (applyBtn) applyBtn.disabled = false;
            }
        }

        function handlePlatformAuditFilterSubmit(e) {
            e.preventDefault();
            loadPlatformAuditLogs();
        }

        function resetPlatformAuditFilters() {
            const eventEl = document.getElementById('plat-audit-event-type');
            const resEl = document.getElementById('plat-audit-resource-id');
            const limitEl = document.getElementById('plat-audit-limit');
            if (eventEl) eventEl.value = '';
            if (resEl) resEl.value = '';
            if (limitEl) limitEl.value = '50';
            loadPlatformAuditLogs();
        }
