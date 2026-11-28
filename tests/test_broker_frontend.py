"""Automated modular Broker frontend visibility & zero-leakage tests (Entregable 5B — Bloque 4).

Covers:
1. Static structural assertions:
   - `.module-hidden { display: none !important; }` in static/css/app.css.
   - `.broker-only-ui` and `data-responsive-display="true"` on responsive Broker elements
     (#top-btn-new-broker-case, #cd-qs-broker-sep) in index.html.
   - `static/js/10-broker.js` loaded in index.html, strictly gated by `hasModule('BROKER')`,
     and contains ZERO `/api/loans` calls.
2. Real headless Chrome (CDP at desktop 1280x800):
   - Computed CSS visibility matrix across all 3 commercial profiles:
     * CORE-only: 0 `.broker-only-ui` and 0 `.lending-only-ui` elements visible (`display === 'none'`).
     * CORE + BROKER: `.broker-only-ui` visible (including responsive desktop elements) and 0 `.lending-only-ui` visible.
     * CORE + LENDING + BROKER: both `.broker-only-ui` and `.lending-only-ui` visible cleanly without collisions.
   - Network fetch spy across all 4 profiles (`CORE`, `CORE + LENDING`, `CORE + BROKER`, `CORE + LENDING + BROKER`):
     * 0 calls to `/api/broker/*` when `!hasModule('BROKER')`.
     * 0 calls to `/api/loans/*` when `!hasModule('LENDING')` (including `CORE + BROKER`).
     * Clean coexistence of both vertical summaries in `CORE + LENDING + BROKER`.
"""
import base64
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _find_chrome_binary() -> str | None:
    return next(
        (
            cand
            for cand in (
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                "/usr/bin/google-chrome",
                "/usr/bin/chromium",
                "/usr/bin/chromium-browser",
            )
            if os.path.exists(cand)
        ),
        None,
    )


def _run_cdp_eval(js_expr: str) -> dict:
    chrome_bin = _find_chrome_binary()
    if not chrome_bin:
        pytest.skip("Headless Chrome/Chromium binary not available in this environment.")

    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(REPO_ROOT)))
    http_port = server.server_address[1]
    srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
    srv_thread.start()

    user_dir = tempfile.mkdtemp(prefix="nura_e5b_broker_ui_")
    sock_tmp = socket.socket()
    sock_tmp.bind(("127.0.0.1", 0))
    cdp_port = sock_tmp.getsockname()[1]
    sock_tmp.close()

    proc = subprocess.Popen(
        [
            chrome_bin,
            "--headless=new",
            "--disable-gpu",
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={user_dir}",
            "--window-size=1280,800",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        ws_url = None
        for _ in range(40):
            try:
                tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json", timeout=2).read().decode())
                for t in tabs:
                    if t.get("type") == "page":
                        ws_url = t["webSocketDebuggerUrl"]
                        break
                if ws_url:
                    break
            except Exception:
                time.sleep(0.15)
        assert ws_url, "Could not connect to headless Chrome CDP."

        host_port, ws_path = ws_url.replace("ws://", "").split("/", 1)
        ws_host, ws_port = host_port.split(":")
        ws_sock = socket.create_connection((ws_host, int(ws_port)), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode()
        ws_sock.sendall(
            (
                f"GET /{ws_path} HTTP/1.1\r\nHost: {host_port}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += ws_sock.recv(4096)
        assert b"101" in buf.splitlines()[0]
        rem = [buf.split(b"\r\n\r\n", 1)[1]]
        cid = [0]

        def _recv_exact(n: int) -> bytes:
            while len(rem[0]) < n:
                chunk = ws_sock.recv(65536)
                if not chunk:
                    raise EOFError("CDP socket closed")
                rem[0] += chunk
            out, rem[0] = rem[0][:n], rem[0][n:]
            return out

        def _cdp(method: str, params: dict | None = None) -> dict:
            cid[0] += 1
            data = json.dumps({"id": cid[0], "method": method, "params": params or {}}).encode("utf-8")
            mask = os.urandom(4)
            hdr = bytearray([0x81])
            if len(data) < 126:
                hdr.append(0x80 | len(data))
            elif len(data) < 65536:
                hdr.append(0x80 | 126)
                hdr.extend(struct.pack("!H", len(data)))
            else:
                hdr.append(0x80 | 127)
                hdr.extend(struct.pack("!Q", len(data)))
            hdr.extend(mask)
            ws_sock.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
            while True:
                b1, b2 = _recv_exact(2)
                opcode = b1 & 0x0F
                length = b2 & 0x7F
                if length == 126:
                    length = struct.unpack("!H", _recv_exact(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", _recv_exact(8))[0]
                payload = _recv_exact(length)
                if opcode == 1:
                    msg = json.loads(payload.decode("utf-8"))
                    if msg.get("id") == cid[0]:
                        return msg.get("result", {})

        _cdp("Page.enable")
        _cdp("Runtime.enable")
        _cdp("Page.navigate", {"url": f"http://127.0.0.1:{http_port}/index.html"})
        for _ in range(40):
            ready = _cdp(
                "Runtime.evaluate",
                {
                    "expression": "typeof applyShellMode === 'function' && typeof loadBrokerWorkspace === 'function'",
                    "returnByValue": True,
                },
            )
            if ready.get("result", {}).get("value") is True:
                break
            time.sleep(0.15)

        eval_res = _cdp(
            "Runtime.evaluate",
            {"expression": js_expr, "awaitPromise": True, "returnByValue": True},
        )
        ws_sock.close()
        assert "exceptionDetails" not in eval_res, f"JS exception in headless Chrome: {eval_res}"
        return eval_res["result"]["value"]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        server.shutdown()
        server.server_close()
        shutil.rmtree(user_dir, ignore_errors=True)


def test_01_broker_frontend_static_structure_and_zero_lending_coupling():
    app_css = (REPO_ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
    index_html = (REPO_ROOT / "index.html").read_text(encoding="utf-8")
    core_auth_js = (REPO_ROOT / "static" / "js" / "03-core-auth.js").read_text(encoding="utf-8")
    plat_js = (REPO_ROOT / "static" / "js" / "04-platform.js").read_text(encoding="utf-8")
    dash_js = (REPO_ROOT / "static" / "js" / "05-dashboard-customers.js").read_text(encoding="utf-8")
    broker_js = (REPO_ROOT / "static" / "js" / "10-broker.js").read_text(encoding="utf-8")

    # 1. CSS & responsive guards
    assert ".module-hidden { display: none !important; }" in app_css
    assert 'id="top-btn-new-broker-case" data-responsive-display="true"' in index_html
    assert 'id="cd-qs-broker-sep" data-responsive-display="true"' in index_html
    assert 'id="tab-broker"' in index_html
    assert 'src="/static/js/10-broker.js?v=' in index_html

    # 2. Shell toggles & navigation guards
    assert "el.classList.toggle('module-hidden', !hasBroker)" in plat_js
    assert "BROKER_TABS.has(tabId) && !hasModule('BROKER')" in core_auth_js
    assert "hasModule('BROKER') && typeof loadBrokerDashboardSummary === 'function'" in dash_js
    assert "hasBroker && typeof loadCustomerBrokerSummary === 'function'" in dash_js

    # 3. Modular 10-broker.js purity: 0 calls to /api/loans, strictly gated by hasModule('BROKER')
    assert "hasModule('BROKER')" in broker_js
    assert "/api/loans" not in broker_js
    assert "/api/broker/dashboard-summary" in broker_js
    assert "/api/broker/customers/${customerId}/summary" in broker_js
    assert "/api/broker/lenders" in broker_js
    assert "/api/broker/cases" in broker_js
    assert "/api/broker/commissions" in broker_js


def test_02_headless_chrome_visibility_matrix_and_zero_network_leakage():
    js_test = """
    (async () => {
        // Inject deterministic Tailwind responsive utility cascade (offline-safe)
        const style = document.createElement('style');
        style.textContent = `
            .hidden { display: none; }
            .flex { display: flex; }
            .grid { display: grid; }
            @media (min-width: 640px) { .sm\\\\:inline { display: inline; } }
            @media (min-width: 768px) { .md\\\\:flex { display: flex; } }
        `;
        document.head.insertBefore(style, document.head.firstChild);

        document.getElementById('auth-view')?.classList.add('hidden');
        document.getElementById('app-view')?.classList.remove('hidden');
        document.getElementById('tab-home')?.classList.add('active');
        document.getElementById('tab-customers')?.classList.add('active');
        document.getElementById('customers-master-view')?.classList.add('hidden');
        document.getElementById('customer-detail-view')?.classList.remove('hidden');

        const lendingIds = [
            'top-btn-simulator',
            'cd-qs-balance-sep',
            'cd-qs-balance-wrap',
            'side-btn-loans',
            'side-btn-simulator',
            'dash-btn-simulator',
            'privacy-btn',
            'dash-lending-kpis-grid',
            'dash-lending-distribution-card',
            'ctab-btn-summary',
            'ctab-btn-loans'
        ];
        const brokerIds = [
            'top-btn-new-broker-case',
            'cd-qs-broker-sep',
            'cd-qs-broker-wrap',
            'side-btn-broker',
            'dash-broker-section',
            'ctab-btn-broker'
        ];

        const probe = (ids) => {
            const res = {};
            for (const id of ids) {
                const el = document.getElementById(id);
                const cs = getComputedStyle(el);
                res[id] = { display: cs.display, classes: el.className };
            }
            return res;
        };

        // 1. Visibility Matrix across CORE-only, CORE+BROKER, and CORE+LENDING+BROKER
        const userCore = { role: 'OWNER_ADMIN', tenant_name: 'Core Co', tenant_slug: 'core-co', enabled_modules: ['CORE'] };
        applyShellMode(false, userCore);
        const visCore = { lending: probe(lendingIds), broker: probe(brokerIds) };

        const userBroker = { role: 'OWNER_ADMIN', tenant_name: 'Broker Co', tenant_slug: 'broker-co', enabled_modules: ['CORE', 'BROKER'] };
        applyShellMode(false, userBroker);
        const visBroker = { lending: probe(lendingIds), broker: probe(brokerIds) };

        const userHybrid = { role: 'OWNER_ADMIN', tenant_name: 'Hybrid Co', tenant_slug: 'hybrid-co', enabled_modules: ['CORE', 'LENDING', 'BROKER'] };
        applyShellMode(false, userHybrid);
        const visHybrid = { lending: probe(lendingIds), broker: probe(brokerIds) };

        // 2. Network Spy across all 4 profiles verifying 0 unauthorized vertical API calls
        const recordedUrls = [];
        window.fetch = async (url) => {
            const u = String(url);
            recordedUrls.push(u);
            let body = {};
            if (u.startsWith('/api/dashboard/summary')) {
                body = { counts: { customers: 1, open_leads: 0 }, call_list: { today: 0, overdue: 0, upcoming: 0 }, recent_activity: [] };
            } else if (u.startsWith('/api/loans/dashboard-summary')) {
                body = { active_loan_portfolio_cents: 0, due_today_cents: 0, overdue_amount_cents: 0, total_outstanding_principal_cents: 0, total_amount_due_cents: 0, collected_this_week_enabled: true, collected_this_week_cents: 0, counts: { active_loans: 0, current_loans: 0, overdue_loans: 0, paid_off_loans: 0, defaulted_loans: 0, due_today_installments: 0, overdue_installments: 0, collected_week_payments: 0 } };
            } else if (u.startsWith('/api/broker/dashboard-summary')) {
                body = { active_pipeline_cents: 65000000, funded_volume_cents: 120000000, active_lenders_count: 2, counts: { active_cases: 3, funded_cases: 4, offers_received_cases: 1 }, commissions: { total_earned_cents: 3050000, paid_cents: 550000, pending_cents: 1000000, invoiced_cents: 1500000 }, recent_cases: [] };
            } else if (u === '/api/customers/1') {
                body = { id: 1, customer_number: 'CUST-00001', full_legal_name: 'Test Customer', entity_type: 'INDIVIDUAL', status: 'ACTIVE', verification_status: 'VERIFIED', phone_primary: '5511001100' };
            } else if (u === '/api/customers/1/interactions') {
                body = [];
            } else if (u === '/api/loans/customers/1/summary') {
                body = { customer_id: 1, credit_summary: { current_outstanding_principal_cents: 0, total_amount_due_cents: 0, overdue_amount_cents: 0, active_loans_count: 0, total_loans_count: 0, remaining_installments: 0 }, loans: [] };
            } else if (u === '/api/broker/customers/1/summary') {
                body = { customer_id: 1, broker_summary: { total_cases: 1, active_cases: 1, total_funded_cents: 30000000, approved_offers: 1, total_submissions: 2, commissions: { total_earned_cents: 750000 } }, cases: [], commissions: [] };
            } else if (u.startsWith('/api/broker/lenders')) {
                body = [];
            } else if (u.startsWith('/api/broker/cases') || u.startsWith('/api/broker/commissions')) {
                body = { total: 0, page: 1, page_size: 20, items: [] };
            }
            return {
                ok: true,
                status: 200,
                json: async () => body,
                text: async () => JSON.stringify(body)
            };
        };

        async function runProfileFlow(userObj) {
            recordedUrls.length = 0;
            currentUser = userObj;
            applyShellMode(false, userObj);
            await loadDashboardSummary();
            await openCustomerDetail(1);
            switchTab('broker');
            switchTab('loans');
            await loadBrokerWorkspace();
            return recordedUrls.slice();
        }

        const netCore = await runProfileFlow(userCore);
        const netLending = await runProfileFlow({ role: 'OWNER_ADMIN', tenant_name: 'Lend Co', tenant_slug: 'lend-co', enabled_modules: ['CORE', 'LENDING'] });
        const netBroker = await runProfileFlow(userBroker);
        const netHybrid = await runProfileFlow(userHybrid);

        return { visCore, visBroker, visHybrid, netCore, netLending, netBroker, netHybrid };
    })()
    """
    out = _run_cdp_eval(js_test)

    # 1. CORE-only: 0 lending elements visible, 0 broker elements visible
    for eid, st in out["visCore"]["lending"].items():
        assert st["display"] == "none", f"CORE-only lending leak on #{eid}: {st}"
    for eid, st in out["visCore"]["broker"].items():
        assert st["display"] == "none", f"CORE-only broker leak on #{eid}: {st}"

    # 2. CORE + BROKER: 0 lending elements visible, ALL broker elements visible (including responsive desktop controls)
    for eid, st in out["visBroker"]["lending"].items():
        assert st["display"] == "none", f"CORE+BROKER lending leak on #{eid}: {st}"
    for eid, st in out["visBroker"]["broker"].items():
        assert st["display"] != "none", f"CORE+BROKER broker element unexpectedly hidden #{eid}: {st}"
    assert out["visBroker"]["broker"]["top-btn-new-broker-case"]["display"] == "flex"
    assert "hidden" in out["visBroker"]["broker"]["top-btn-new-broker-case"]["classes"].split()
    assert "module-hidden" not in out["visBroker"]["broker"]["top-btn-new-broker-case"]["classes"].split()

    # 3. CORE + LENDING + BROKER: BOTH lending AND broker elements visible simultaneously
    for eid, st in out["visHybrid"]["lending"].items():
        assert st["display"] != "none", f"HYBRID lending element unexpectedly hidden #{eid}: {st}"
    for eid, st in out["visHybrid"]["broker"].items():
        assert st["display"] != "none", f"HYBRID broker element unexpectedly hidden #{eid}: {st}"

    # 4. Network isolation assertions
    # CORE-only: 0 /api/broker/* and 0 /api/loans/*
    assert not any(u.startswith("/api/broker") for u in out["netCore"]), f"CORE-only called broker API: {out['netCore']}"
    assert not any(u.startswith("/api/loans") for u in out["netCore"]), f"CORE-only called loans API: {out['netCore']}"

    # CORE + LENDING: 0 /api/broker/*, calls /api/loans/*
    assert not any(u.startswith("/api/broker") for u in out["netLending"]), f"CORE+LENDING called broker API: {out['netLending']}"
    assert "/api/loans/dashboard-summary" in out["netLending"]
    assert "/api/loans/customers/1/summary" in out["netLending"]

    # CORE + BROKER: 0 /api/loans/*, calls /api/broker/*
    assert not any(u.startswith("/api/loans") for u in out["netBroker"]), f"CORE+BROKER called loans API: {out['netBroker']}"
    assert any(u.startswith("/api/broker/dashboard-summary") for u in out["netBroker"])
    assert "/api/broker/customers/1/summary" in out["netBroker"]

    # CORE + LENDING + BROKER: calls BOTH vertical summaries cleanly
    assert "/api/loans/dashboard-summary" in out["netHybrid"]
    assert "/api/loans/customers/1/summary" in out["netHybrid"]
    assert any(u.startswith("/api/broker/dashboard-summary") for u in out["netHybrid"])
    assert "/api/broker/customers/1/summary" in out["netHybrid"]
