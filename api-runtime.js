/**
 * ◈ Universal Fullstack Backend & API Runtime Interceptor
 * Integrated across all 74 CEO Portfolio Builds.
 * Provides live REST API routes, state persistence, and interactive DevTools Inspector.
 */

(function() {
  'use strict';

  var BASE = (typeof window !== 'undefined' && window.BASE) || 'http://localhost:8090';
  if (typeof window !== 'undefined') window.BASE = BASE;

  // Do not display DevTools inside iframe simulators
  const isInIframe = window.self !== window.top;

  // In-memory request log
  const networkLogs = [];

  // Local Database Engine
  const DB = {
    get: (key, fallback = []) => {
      try {
        const val = localStorage.getItem('ceo_db_' + key);
        return val ? JSON.parse(val) : fallback;
      } catch (e) { return fallback; }
    },
    set: (key, val) => {
      try {
        localStorage.setItem('ceo_db_' + key, JSON.stringify(val));
      } catch (e) {}
    }
  };

  // REAL backend only — mock responses removed. Every /api/ call goes to the
  // gateway; a minimal offline stub is returned only when the network fails.
  function logReal(method, url, status, request, response, duration) {
    networkLogs.unshift({
      id: Math.random().toString(36).substr(2, 6),
      method,
      url,
      status,
      duration,
      timestamp: new Date().toLocaleTimeString(),
      request: request,
      response: response
    });
    if (networkLogs.length > 50) networkLogs.pop();
    updateDevToolsUI();
  }

  // Pass-through fetch: real responses logged, offline fallback stub on failure
  const originalFetch = window.fetch;
  window.fetch = async function(input, init = {}) {
    const url = typeof input === 'string' ? input : (input.url || '');
    if (!url.includes('/api/')) return originalFetch(input, init);
    const method = ((init && init.method) || 'GET').toUpperCase();
    const t0 = performance.now();
    try {
      const res = await originalFetch(input, init);
      const dur = Math.max(1, Math.round(performance.now() - t0));
      res.clone().json().then(data => {
        logReal(method, url, res.status, (init && init.body) || {}, data, dur);
      }).catch(() => {});
      return res;
    } catch (e) {
      const dur = Math.max(1, Math.round(performance.now() - t0));
      const stub = { error: 'offline', message: 'Backend unreachable — offline fallback.', url };
      logReal(method, url, 503, (init && init.body) || {}, stub, dur);
      return new Response(JSON.stringify(stub), {
        status: 503,
        headers: { 'Content-Type': 'application/json' }
      });
    }
  };

  // Ping the REAL gateway on page load
  setTimeout(() => {
    window.fetch(BASE + '/api');
  }, 1000);

  // If in iframe, do not inject UI
  if (isInIframe) return;

  // Inject DevTools UI Elements
  function injectDevTools() {
    const css = `
      .ceo-devtools-btn {
        position: fixed;
        bottom: 24px;
        left: 24px;
        z-index: 9998;
        background: rgba(14, 18, 27, 0.9);
        border: 1px solid rgba(0, 229, 153, 0.4);
        color: #fff;
        padding: 8px 16px;
        border-radius: 9999px;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 12px;
        font-weight: 700;
        display: inline-flex;
        align-items: center;
        gap: 8px;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.8), 0 0 15px rgba(0, 229, 153, 0.2);
        cursor: pointer;
        backdrop-filter: blur(14px);
        transition: all 0.2s ease;
      }
      .ceo-devtools-btn:hover {
        transform: translateY(-2px);
        border-color: #00e599;
        box-shadow: 0 14px 40px rgba(0, 229, 153, 0.35);
      }
      .ceo-devtools-pulse {
        width: 8px; height: 8px; border-radius: 50%;
        background: #00e599; box-shadow: 0 0 8px #00e599;
        animation: ceoPulse 1.5s infinite;
      }
      @keyframes ceoPulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.3; transform: scale(0.8); }
      }

      /* Slide-Over Drawer */
      .ceo-drawer {
        position: fixed;
        bottom: 74px;
        left: 24px;
        width: 480px;
        max-width: calc(100vw - 48px);
        height: 520px;
        background: rgba(11, 15, 24, 0.96);
        border: 1px solid rgba(255, 255, 255, 0.12);
        border-radius: 18px;
        box-shadow: 0 25px 60px rgba(0, 0, 0, 0.9), 0 0 30px rgba(0, 229, 153, 0.15);
        backdrop-filter: blur(24px);
        z-index: 9999;
        display: none;
        flex-direction: column;
        overflow: hidden;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
      }
      .ceo-drawer.open { display: flex; animation: ceoDrawerIn 0.25s ease-out; }
      @keyframes ceoDrawerIn {
        from { opacity: 0; transform: translateY(12px) scale(0.98); }
        to { opacity: 1; transform: translateY(0) scale(1); }
      }

      .drawer-header {
        padding: 14px 18px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: rgba(255, 255, 255, 0.02);
      }
      .drawer-title {
        font-size: 13px;
        font-weight: 700;
        color: #fff;
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .drawer-close {
        background: none; border: none; color: #94a3b8;
        font-size: 18px; cursor: pointer; padding: 2px 6px;
      }
      .drawer-close:hover { color: #fff; }

      .drawer-tabs {
        display: flex;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        background: rgba(0, 0, 0, 0.2);
      }
      .drawer-tab {
        flex: 1;
        padding: 10px;
        background: none;
        border: none;
        color: #94a3b8;
        font-size: 11px;
        font-weight: 600;
        cursor: pointer;
        border-bottom: 2px solid transparent;
        transition: all 0.2s;
      }
      .drawer-tab.active {
        color: #00e599;
        border-bottom-color: #00e599;
        background: rgba(0, 229, 153, 0.05);
      }

      .drawer-body {
        flex: 1;
        overflow-y: auto;
        padding: 14px;
        font-size: 12px;
        color: #cbd5e1;
      }

      .net-table {
        width: 100%;
        border-collapse: collapse;
      }
      .net-table th {
        text-align: left;
        color: #64748b;
        font-size: 10px;
        text-transform: uppercase;
        padding-bottom: 8px;
      }
      .net-row {
        cursor: pointer;
        border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        transition: background 0.15s;
      }
      .net-row:hover { background: rgba(255, 255, 255, 0.04); }
      .net-row td { padding: 8px 4px; font-family: monospace; font-size: 11px; }
      .method-badge {
        padding: 2px 6px; border-radius: 4px; font-weight: 700; font-size: 10px;
      }
      .method-get { background: rgba(56, 189, 248, 0.15); color: #38bdf8; }
      .method-post { background: rgba(0, 229, 153, 0.15); color: #00e599; }
      .status-200 { color: #00e599; }

      .json-viewer {
        background: #06090e;
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 10px;
        padding: 12px;
        font-family: monospace;
        font-size: 11px;
        color: #38bdf8;
        max-height: 180px;
        overflow-y: auto;
        white-space: pre-wrap;
        margin-top: 10px;
      }

      .tester-form {
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      .tester-btn {
        padding: 9px 16px;
        background: #00e599;
        color: #070a0e;
        border: none;
        border-radius: 8px;
        font-weight: 700;
        cursor: pointer;
        font-size: 12px;
        transition: transform 0.15s;
      }
      .tester-btn:hover { transform: scale(1.02); }
    `;

    const style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);

    const btn = document.createElement('button');
    btn.className = 'ceo-devtools-btn';
    btn.innerHTML = `
      <span class="ceo-devtools-pulse"></span>
      <span>⚡ Backend API</span>
      <span style="font-size:10px;background:rgba(0,229,153,0.18);border:1px solid rgba(0,229,153,0.3);color:#00e599;padding:1px 6px;border-radius:99px;">REST 200</span>
    `;
    btn.onclick = toggleDevTools;
    document.body.appendChild(btn);

    const drawer = document.createElement('div');
    drawer.className = 'ceo-drawer';
    drawer.id = 'ceoDrawer';
    drawer.innerHTML = `
      <div class="drawer-header">
        <div class="drawer-title">
          <span>⚡ LIVE FULLSTACK REST API GATEWAY</span>
        </div>
        <button class="drawer-close" onclick="document.getElementById('ceoDrawer').classList.remove('open')">✕</button>
      </div>
      <div class="drawer-tabs">
        <button class="drawer-tab active" onclick="switchDrawerTab('network')">Network Log (<span id="netCount">0</span>)</button>
        <button class="drawer-tab" onclick="switchDrawerTab('database')">Database State</button>
        <button class="drawer-tab" onclick="switchDrawerTab('console')">API Console</button>
      </div>
      <div class="drawer-body" id="drawerBody"></div>
    `;
    document.body.appendChild(drawer);

    renderNetworkTab();
  }

  window.toggleDevTools = function() {
    const d = document.getElementById('ceoDrawer');
    if (d) d.classList.toggle('open');
  };

  let activeTab = 'network';
  window.switchDrawerTab = function(tab) {
    activeTab = tab;
    document.querySelectorAll('.drawer-tab').forEach(t => t.classList.remove('active'));
    event.target.classList.add('active');
    if (tab === 'network') renderNetworkTab();
    else if (tab === 'database') renderDatabaseTab();
    else if (tab === 'console') renderConsoleTab();
  };

  function renderNetworkTab() {
    const body = document.getElementById('drawerBody');
    if (!body) return;
    document.getElementById('netCount').textContent = networkLogs.length;

    if (networkLogs.length === 0) {
      body.innerHTML = '<p style="color:#64748b;text-align:center;padding:30px 0;">No API requests recorded yet.<br>Click interactive buttons on the page to trigger live REST endpoints.</p>';
      return;
    }

    let rows = networkLogs.map(l => `
      <tr class="net-row" onclick="viewLogDetail('${l.id}')">
        <td><span class="method-badge method-${l.method.toLowerCase()}">${l.method}</span></td>
        <td style="color:#f8fafc;max-width:180px;overflow:hidden;text-overflow:ellipsis;">${l.url}</td>
        <td class="status-200">${l.status}</td>
        <td style="color:#64748b;">${l.duration}ms</td>
      </tr>
    `).join('');

    body.innerHTML = `
      <table class="net-table">
        <thead>
          <tr><th>Method</th><th>Endpoint</th><th>Status</th><th>Time</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      <div id="logDetailArea"></div>
    `;
  }

  window.viewLogDetail = function(id) {
    const item = networkLogs.find(l => l.id === id);
    if (!item) return;
    const area = document.getElementById('logDetailArea');
    if (area) {
      area.innerHTML = `
        <div style="margin-top:14px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.1);">
          <div style="font-weight:700;color:#00e599;margin-bottom:4px;">HTTP Response Payload (${item.url}):</div>
          <div class="json-viewer">${JSON.stringify(item.response, null, 2)}</div>
        </div>
      `;
    }
  };

  function renderDatabaseTab() {
    const body = document.getElementById('drawerBody');
    if (!body) return;
    const orders = DB.get('orders', []);
    const leads = DB.get('leads', []);
    const token = DB.get('auth_token', 'No active session');

    body.innerHTML = `
      <div style="font-weight:700;color:#38bdf8;margin-bottom:6px;">Auth Session:</div>
      <div class="json-viewer">${JSON.stringify({ active_token: token }, null, 2)}</div>

      <div style="font-weight:700;color:#00e599;margin:12px 0 6px;">Orders Table (${orders.length} entries):</div>
      <div class="json-viewer">${JSON.stringify(orders.slice(0, 5), null, 2)}</div>

      <div style="font-weight:700;color:#f59e0b;margin:12px 0 6px;">Leads & Inquiries Table (${leads.length} entries):</div>
      <div class="json-viewer">${JSON.stringify(leads.slice(0, 5), null, 2)}</div>
    `;
  }

  function renderConsoleTab() {
    const body = document.getElementById('drawerBody');
    if (!body) return;
    body.innerHTML = `
      <div class="tester-form">
        <label style="color:#94a3b8;font-size:11px;">Select Live Endpoint to Dispatch:</label>
        <select id="testEndpoint" style="background:#080d14;border:1px solid #1e293b;color:#fff;padding:8px;border-radius:6px;font-family:monospace;font-size:11px;">
          <option value="GET /api">GET /api (Gateway Routes)</option>
          <option value="GET /api/projects">GET /api/projects (Portfolio Catalog)</option>
          <option value="GET /api/ai/prompts">GET /api/ai/prompts (AI Prompts)</option>
          <option value="POST /api/estimate">POST /api/estimate (Project Quote)</option>
        </select>
        <button class="tester-btn" onclick="executeTestApi()">⚡ Send HTTP Request</button>
        <div id="testOutputArea" style="display:none">
          <div style="font-weight:700;color:#00e599;margin-top:10px;">Response Received:</div>
          <div class="json-viewer" id="testOutputJson"></div>
        </div>
      </div>
    `;
  }

  window.executeTestApi = async function() {
    const select = document.getElementById('testEndpoint');
    const parts = select.value.split(' ');
    const method = parts[0];
    const endpoint = parts.slice(1).join(' ');
    const res = await window.fetch(BASE + endpoint, {
      method: method,
      headers: { 'Content-Type': 'application/json' },
      body: method === 'POST' ? JSON.stringify({ scope: 5, design: 7, integrations: 3 }) : undefined
    });
    const json = await res.json();
    document.getElementById('testOutputArea').style.display = 'block';
    document.getElementById('testOutputJson').textContent = JSON.stringify(json, null, 2);
  };

  function updateDevToolsUI() {
    if (activeTab === 'network') renderNetworkTab();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectDevTools);
  } else {
    injectDevTools();
  }

})();
