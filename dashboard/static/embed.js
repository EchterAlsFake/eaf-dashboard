/**
 * EAF Dashboard Embeddable Widget Library
 * Lightweight, zero-dependency, Shadow-DOM isolated status widgets
 * for embedding server & website health into any external webpage.
 */
(() => {
  if (window.EAFEmbedLoaded) return;
  window.EAFEmbedLoaded = true;

  const currentScript = document.currentScript;
  const scriptBase = currentScript ? new URL(currentScript.src).origin : window.location.origin;

  const styles = `
    :host {
      display: inline-block;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 13px;
      line-height: 1.4;
      color: #edf3ff;
      --bg: #0d131f;
      --card: #131c2e;
      --border: #223049;
      --green: #10b981;
      --yellow: #f59e0b;
      --red: #ef4444;
      --accent: #3b82f6;
      --muted: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    
    /* BADGE WIDGET */
    .eaf-badge {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      padding: 5px 12px;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 999px;
      font-weight: 500;
      box-shadow: 0 2px 6px rgba(0,0,0,0.25);
      cursor: default;
      transition: border-color 0.2s;
    }
    .eaf-badge:hover { border-color: var(--accent); }
    .eaf-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--green);
      box-shadow: 0 0 8px rgba(16, 185, 129, 0.6);
      animation: eaf-pulse 2s infinite;
    }
    .eaf-dot.degraded { background: var(--yellow); box-shadow: 0 0 8px rgba(245, 158, 11, 0.6); }
    .eaf-dot.offline { background: var(--red); box-shadow: 0 0 8px rgba(239, 68, 68, 0.6); }
    @keyframes eaf-pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.6; transform: scale(0.95); }
    }
    .eaf-badge-text { font-size: 12px; font-weight: 600; }
    .eaf-badge-detail { color: var(--muted); font-size: 11px; margin-left: 2px; }

    /* CARD WIDGET */
    .eaf-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 16px;
      width: 100%;
      max-width: 380px;
      box-shadow: 0 4px 16px rgba(0,0,0,0.3);
    }
    .eaf-card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--border);
    }
    .eaf-card-title {
      font-weight: 700;
      font-size: 13px;
      letter-spacing: 0.02em;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .eaf-status-pill {
      font-size: 10px;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 10px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      background: rgba(16, 185, 129, 0.15);
      color: var(--green);
    }
    .eaf-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      margin-bottom: 12px;
    }
    .eaf-stat-box {
      background: rgba(255,255,255,0.025);
      border: 1px solid rgba(255,255,255,0.04);
      border-radius: 8px;
      padding: 8px;
      text-align: center;
    }
    .eaf-stat-label {
      font-size: 10px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 3px;
    }
    .eaf-stat-value {
      font-size: 15px;
      font-weight: 700;
      color: #fff;
    }
    .eaf-services {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .eaf-service-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 5px 8px;
      background: rgba(255,255,255,0.015);
      border-radius: 6px;
      font-size: 12px;
    }
    .eaf-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 10px;
      padding-top: 8px;
      border-top: 1px solid var(--border);
      font-size: 10px;
      color: var(--muted);
    }
  `;

  class EAFWidgetElement extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
    }

    connectedCallback() {
      this.widgetType = this.getAttribute("data-widget") || "card";
      this.sourceUrl = this.getAttribute("data-source") || scriptBase;
      this.renderLoading();
      this.fetchData();
      this.timer = setInterval(() => this.fetchData(), 20000);
    }

    disconnectedCallback() {
      if (this.timer) clearInterval(this.timer);
    }

    renderLoading() {
      this.shadowRoot.innerHTML = `
        <style>${styles}</style>
        <div class="eaf-badge">
          <span class="eaf-dot"></span>
          <span>Loading server status…</span>
        </div>
      `;
    }

    async fetchData() {
      try {
        const url = `${this.sourceUrl}/dashboard/api/embed/data`;
        const res = await fetch(url);
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        this.render(data);
      } catch (err) {
        this.renderError(err.message);
      }
    }

    renderError(msg) {
      this.shadowRoot.innerHTML = `
        <style>${styles}</style>
        <div class="eaf-badge" style="border-color: var(--red);">
          <span class="eaf-dot offline"></span>
          <span>Status unavailable (${msg})</span>
        </div>
      `;
    }

    render(data) {
      const clusterStatus = data.cluster?.status || "operational";
      const dotClass = clusterStatus === "operational" ? "" : clusterStatus === "degraded" ? "degraded" : "offline";
      const statusLabel = clusterStatus.toUpperCase();

      if (this.widgetType === "badge") {
        this.shadowRoot.innerHTML = `
          <style>${styles}</style>
          <div class="eaf-badge">
            <span class="eaf-dot ${dotClass}"></span>
            <span class="eaf-badge-text">Systems ${statusLabel}</span>
            <span class="eaf-badge-detail">· ${data.cluster?.nodes_online || 1} online</span>
          </div>
        `;
        return;
      }

      // Default card / fleet widget
      const nodesCount = `${data.cluster?.nodes_online || 1}/${data.cluster?.nodes_total || 1}`;
      const cpu = `${data.host?.cpu_percent || 0}%`;
      const mem = `${data.host?.memory_percent || 0}%`;
      const services = data.services || [];

      this.shadowRoot.innerHTML = `
        <style>${styles}</style>
        <div class="eaf-card">
          <div class="eaf-card-header">
            <div class="eaf-card-title">
              <span class="eaf-dot ${dotClass}"></span>
              <span>LAN Cluster Status</span>
            </div>
            <span class="eaf-status-pill">${statusLabel}</span>
          </div>
          <div class="eaf-grid">
            <div class="eaf-stat-box">
              <div class="eaf-stat-label">Nodes</div>
              <div class="eaf-stat-value">${nodesCount}</div>
            </div>
            <div class="eaf-stat-box">
              <div class="eaf-stat-label">CPU</div>
              <div class="eaf-stat-value">${cpu}</div>
            </div>
            <div class="eaf-stat-box">
              <div class="eaf-stat-label">RAM</div>
              <div class="eaf-stat-value">${mem}</div>
            </div>
          </div>
          <div class="eaf-services">
            ${services.map(s => `
              <div class="eaf-service-row">
                <span style="display:flex;align-items:center;gap:6px;color:var(--muted)">
                  <span style="width:6px;height:6px;border-radius:50%;background:${s.status === 'online' ? 'var(--green)' : 'var(--red)'}"></span>
                  ${s.name}
                </span>
                <span style="font-weight:600;font-size:11px;color:${s.status === 'online' ? 'var(--green)' : 'var(--red)'}">
                  ${s.status === 'online' ? 'Operational' : 'Down'}
                </span>
              </div>
            `).join("")}
          </div>
          <div class="eaf-footer">
            <span>MSI Origin · Ethernet LAN</span>
            <span>${new Date().toLocaleTimeString()}</span>
          </div>
        </div>
      `;
    }
  }

  if (!customElements.get("eaf-widget")) {
    customElements.define("eaf-widget", EAFWidgetElement);
  }

  // Auto-mount on elements with [data-eaf-widget]
  function mount() {
    document.querySelectorAll("[data-eaf-widget]").forEach(el => {
      if (el.dataset.eafMounted) return;
      el.dataset.eafMounted = "true";
      const widget = document.createElement("eaf-widget");
      widget.setAttribute("data-widget", el.getAttribute("data-eaf-widget") || "card");
      if (el.getAttribute("data-eaf-source")) {
        widget.setAttribute("data-source", el.getAttribute("data-eaf-source"));
      } else {
        widget.setAttribute("data-source", scriptBase);
      }
      el.appendChild(widget);
    });

    // Also support script tag inline replacement if script has data-widget
    if (currentScript && currentScript.dataset.widget && !currentScript.dataset.mounted) {
      currentScript.dataset.mounted = "true";
      const widget = document.createElement("eaf-widget");
      widget.setAttribute("data-widget", currentScript.dataset.widget);
      widget.setAttribute("data-source", scriptBase);
      currentScript.parentNode.insertBefore(widget, currentScript.nextSibling);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
