        /* =========================================================================
           2. APPLICATION STATE & AUTHENTICATION
           ========================================================================= */
        let currentConversationId = null;
        let currentUser = null;
        let isPrivacyOn = false;
        let currentSimulationId = 'SIM-2026-0001';
        let currentSimulationResult = null;
        let savedComparisonScenarios = [];

        // Customers State
        let currentCustomersPage = 1;
        let currentCustomersTotal = 0;
        let currentSelectedCustomer = null;
        let customerFormMode = 'create';
        let customerSearchTimer = null;

        // Leads & Call List State
        let currentLeadsPage = 1;
        let currentLeadsTotal = 0;
        let currentSelectedLead = null;
        let leadFormMode = 'create';
        let leadSearchTimer = null;
        let activeCallListBucket = 'TODAY';

        const PLATFORM_TABS = new Set(['platform-orgs', 'platform-audit', 'perfil']);
        const COMMERCIAL_TABS = new Set(['home', 'leads', 'customers', 'loans', 'simulator', 'broker', 'perfil', 'ia']);
        const LENDING_TABS = new Set(['loans', 'simulator']);
        const BROKER_TABS = new Set(['broker']);

        function isPlatformSuperadmin(user) {
            return Boolean(user && user.is_superadmin === true && user.tenant_slug === 'platform');
        }

        function getEnabledModules(user) {
            const target = user !== undefined ? user : currentUser;
            if (!target || !Array.isArray(target.enabled_modules)) return [];
            return target.enabled_modules;
        }

        function hasModule(moduleCode, user) {
            const code = String(moduleCode || '').trim().toUpperCase();
            return getEnabledModules(user).includes(code);
        }

        const SECTION_TITLES = {
            get home() { return t('section.home'); },
            get leads() { return t('section.leads'); },
            get customers() { return t('section.customers'); },
            get loans() { return t('section.loans'); },
            get simulator() { return t('section.simulator'); },
            get broker() { return (typeof NuraI18n !== 'undefined' && NuraI18n.currentLang === 'es') ? 'Broker de Crédito' : 'Credit Broker'; },
            get perfil() { return isPlatformSuperadmin(currentUser) ? t('section.platform-account') : t('section.perfil'); },
            get ia() { return t('section.ia'); },
            get 'platform-orgs'() { return t('section.platform-orgs'); },
            get 'platform-audit'() { return t('section.platform-audit'); }
        };

        function escapeHtml(str) {
            return String(str ?? '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        function formatDateTimeShort(isoStr) {
            if (!isoStr) return '—';
            try {
                const d = new Date(isoStr);
                return d.toLocaleString(NuraI18n.getLocale(), { month: 'short', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
            } catch (e) {
                return isoStr;
            }
        }

        function getCookie(name) {
            const value = `; ${document.cookie}`;
            const parts = value.split(`; ${name}=`);
            if (parts.length === 2) return parts.pop().split(';').shift();
            return null;
        }

        window.addEventListener('DOMContentLoaded', async () => {
            if (typeof initializeSimulatorDates === 'function') initializeSimulatorDates();
            if (typeof runLoanSimulation === 'function') runLoanSimulation();

            // Check if URL contains a password reset token parameter
            const urlParams = new URLSearchParams(window.location.search);
            const tokenParam = urlParams.get('reset_token');
            if (tokenParam && tokenParam.trim().length > 15) {
                document.getElementById('auth-view').classList.remove('hidden');
                document.getElementById('app-view').classList.add('hidden');
                showAuthSubView('reset-confirm');
                document.getElementById('reset-token-input').value = tokenParam.trim();
                // Clean URL query parameter without reloading page
                window.history.replaceState({}, document.title, window.location.pathname);
            } else {
                await checkAuthStatus();
            }
        });

        function showAuthSubView(viewName) {
            const loginForm = document.getElementById('auth-form');
            const forgotView = document.getElementById('auth-forgot-view');
            const resetView = document.getElementById('auth-reset-confirm-view');
            const errBox = document.getElementById('auth-error');
            if (errBox) errBox.classList.add('hidden');

            loginForm.classList.add('hidden');
            forgotView.classList.add('hidden');
            resetView.classList.add('hidden');

            if (viewName === 'login') {
                loginForm.classList.remove('hidden');
            } else if (viewName === 'forgot') {
                document.getElementById('forgot-form').reset();
                document.getElementById('forgot-alert-box').classList.add('hidden');
                forgotView.classList.remove('hidden');
            } else if (viewName === 'reset-confirm') {
                document.getElementById('reset-confirm-form').reset();
                document.getElementById('reset-confirm-error').classList.add('hidden');
                resetView.classList.remove('hidden');
            }
        }

        async function handlePasswordResetRequest(e) {
            e.preventDefault();
            const email = document.getElementById('forgot-email').value;
            const alertBox = document.getElementById('forgot-alert-box');
            const submitBtn = document.getElementById('forgot-submit-btn');
            alertBox.classList.add('hidden');
            submitBtn.disabled = true;

            try {
                const res = await fetch('/api/auth/password-reset/request', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email })
                });
                const data = await res.json();
                // Always displays identical generic message to prevent account enumeration
                alertBox.innerText = data.message || "If an active account is associated with that email address, password reset instructions have been sent.";
                alertBox.classList.remove('hidden');
                document.getElementById('forgot-form').reset();
            } catch (err) {
                alertBox.innerText = "If an active account is associated with that email address, password reset instructions have been sent.";
                alertBox.classList.remove('hidden');
            } finally {
                submitBtn.disabled = false;
            }
        }

        async function handlePasswordResetConfirm(e) {
            e.preventDefault();
            const token = document.getElementById('reset-token-input').value;
            const new_password = document.getElementById('reset-new-pw').value;
            const confirm_pw = document.getElementById('reset-confirm-pw').value;
            const errBox = document.getElementById('reset-confirm-error');
            const submitBtn = document.getElementById('reset-confirm-submit-btn');
            errBox.classList.add('hidden');

            if (new_password !== confirm_pw) {
                errBox.innerText = "New passwords do not match.";
                errBox.classList.remove('hidden');
                return;
            }
            if (new_password.length < 8) {
                errBox.innerText = "Password must be at least 8 characters long.";
                errBox.classList.remove('hidden');
                return;
            }

            submitBtn.disabled = true;
            try {
                const res = await fetch('/api/auth/password-reset/confirm', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token, new_password })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || "Failed to update password");
                
                alert(data.message || "Password updated successfully. Please sign in.");
                showAuthSubView('login');
            } catch (err) {
                errBox.innerText = err.message;
                errBox.classList.remove('hidden');
            } finally {
                submitBtn.disabled = false;
            }
        }

        async function checkAuthStatus() {
            try {
                const res = await fetch('/api/auth/me');
                if (res.ok) {
                    const data = await res.json();
                    currentUser = data;
                    const accountLang = (data.preferred_language === 'es') ? 'es' : 'en';
                    await NuraI18n.setLanguage(accountLang, { persist: false });

                    const initials = data.email.substring(0, 2).toUpperCase();
                    const displayName = data.full_name || data.email.split('@')[0].toUpperCase();

                    document.getElementById('user-email-display').innerText = data.email;
                    document.getElementById('user-initials').innerText = initials;

                    const sideInitials = document.getElementById('sidebar-user-initials');
                    const sideName = document.getElementById('sidebar-user-name');
                    const sideEmail = document.getElementById('sidebar-user-email');
                    if (sideInitials) sideInitials.innerText = initials;
                    if (sideName) sideName.innerText = displayName;
                    if (sideEmail) sideEmail.innerText = data.email;

                    document.getElementById('profile-user-initials').innerText = initials;
                    document.getElementById('profile-user-name').innerText = displayName;
                    document.getElementById('profile-user-email').innerText = data.email;

                    const platformMode = isPlatformSuperadmin(data);
                    applyShellMode(platformMode, data);

                    document.getElementById('auth-view').classList.add('hidden');
                    document.getElementById('app-view').classList.remove('hidden');

                    if (platformMode) {
                        // SUPERADMIN Platform Console: zero commercial API calls
                        switchTab('platform-orgs');
                    } else {
                        const canWriteOrigination = ['OWNER_ADMIN', 'SALES_AGENT', 'CREDIT_ADVISOR'].includes(data.role);
                        const canManageLenders = ['OWNER_ADMIN', 'CREDIT_ADVISOR'].includes(data.role);
                        const hasLending = hasModule('LENDING', data);
                        const hasBroker = hasModule('BROKER', data);
                        document.getElementById('btn-new-customer')?.classList.toggle('hidden', !canWriteOrigination);
                        document.getElementById('btn-edit-customer')?.classList.toggle('hidden', !canWriteOrigination);
                        document.getElementById('btn-new-lead')?.classList.toggle('hidden', !canWriteOrigination);
                        document.getElementById('top-btn-new-lead')?.classList.toggle('hidden', !canWriteOrigination);
                        document.getElementById('top-btn-new-customer')?.classList.toggle('hidden', !canWriteOrigination);
                        document.getElementById('btn-new-loan')?.classList.toggle('hidden', !(canWriteOrigination && hasLending));
                        document.getElementById('btn-customer-new-loan')?.classList.toggle('hidden', !(canWriteOrigination && hasLending));
                        document.getElementById('top-btn-new-broker-case')?.classList.toggle('module-hidden', !(canWriteOrigination && hasBroker));
                        document.getElementById('btn-new-broker-case')?.classList.toggle('hidden', !(canWriteOrigination && hasBroker));
                        document.getElementById('btn-customer-new-broker-case')?.classList.toggle('hidden', !(canWriteOrigination && hasBroker));
                        document.getElementById('btn-new-broker-lender')?.classList.toggle('hidden', !(canManageLenders && hasBroker));

                        switchTab('home');
                        const initialLoads = [loadDashboardSummary(), cargarConversaciones(), loadCustomers(1), loadLeads(1)];
                        if (hasLending && typeof loadLoans === 'function') {
                            initialLoads.push(loadLoans(1));
                        }
                        if (hasBroker && typeof loadBrokerWorkspace === 'function') {
                            initialLoads.push(loadBrokerWorkspace());
                        }
                        await Promise.all(initialLoads);
                    }
                } else {
                    mostrarAuthView();
                }
            } catch (e) {
                mostrarAuthView();
            }
        }

        function mostrarAuthView() {
            currentUser = null;
            resetShellMode();
            if (typeof NuraI18n !== 'undefined' && NuraI18n.currentLang !== 'en') {
                NuraI18n.setLanguage('en', { persist: false });
            }
            document.getElementById('app-view').classList.add('hidden');
            document.getElementById('auth-view').classList.remove('hidden');
            currentConversationId = null;
        }

        async function handleAuthSubmit(e) {
            e.preventDefault();
            const email = document.getElementById('auth-email').value;
            const password = document.getElementById('auth-password').value;
            const errBox = document.getElementById('auth-error');
            const submitBtn = document.getElementById('auth-submit-btn');
            errBox.classList.add('hidden');
            submitBtn.disabled = true;

            try {
                const res = await fetch('/api/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, password })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || "Authentication error");
                await checkAuthStatus();
            } catch (err) {
                errBox.innerText = err.message;
                errBox.classList.remove('hidden');
            } finally {
                submitBtn.disabled = false;
            }
        }

        async function cerrarSesion() {
            try {
                await fetch('/api/auth/logout', {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': getCookie('csrf_token') }
                });
            } catch (e) {
                console.error("Error signing out", e);
            } finally {
                currentUser = null;
                mostrarAuthView();
            }
        }

        /* =========================================================================
           5. NAVIGATION & SUB-VIEWS
           ========================================================================= */
        function switchTab(tabId) {
            const platformMode = isPlatformSuperadmin(currentUser);
            if (platformMode && !PLATFORM_TABS.has(tabId)) {
                return;
            }
            if (!platformMode && String(tabId).startsWith('platform-')) {
                return;
            }
            if (!platformMode && LENDING_TABS.has(tabId) && !hasModule('LENDING')) {
                return;
            }
            if (!platformMode && BROKER_TABS.has(tabId) && !hasModule('BROKER')) {
                return;
            }

            const targetTab = document.getElementById('tab-' + tabId);
            if (!targetTab) return;

            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.side-nav-item').forEach(el => el.classList.remove('active'));

            targetTab.classList.add('active');
            if (platformMode && tabId === 'perfil') {
                document.getElementById('btn-platform-account')?.classList.add('active');
                document.getElementById('side-btn-platform-account')?.classList.add('active');
            } else {
                document.getElementById('btn-' + tabId)?.classList.add('active');
                document.getElementById('side-btn-' + tabId)?.classList.add('active');
            }

            const titleEl = document.getElementById('topbar-section-title');
            if (titleEl && SECTION_TITLES[tabId]) {
                titleEl.innerText = SECTION_TITLES[tabId];
            }

            const floatingBtn = document.getElementById('floating-executive-btn');
            if (floatingBtn) {
                floatingBtn.classList.toggle('hidden', platformMode || tabId === 'ia');
            }
            if (platformMode && tabId === 'platform-orgs') {
                loadPlatformOrganizations();
            } else if (platformMode && tabId === 'platform-audit') {
                loadPlatformAuditLogs();
            } else if (!platformMode && tabId === 'broker' && hasModule('BROKER') && typeof loadBrokerWorkspace === 'function') {
                loadBrokerWorkspace();
            }
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }
