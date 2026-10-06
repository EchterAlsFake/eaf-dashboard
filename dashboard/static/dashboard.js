(() => {
  const content = document.getElementById("content");
  const topbarStatusText = document.getElementById("topbar-status-text");
  const topbarStatusDot = document.querySelector("#topbar-status .pulse-indicator");
  const autoRefreshSelect = document.getElementById("auto-refresh");

  const colors = ["#68a6ff", "#4fda9b", "#c084fc", "#f9c74f", "#ff7b89", "#58d6d6", "#ff9f5a", "#a3e635"];
  const endpointNames = {
    "pornfetch.to": "Website", "www.pornfetch.to": "Website redirect",
    "api.pornfetch.to": "Public API", "downloads.pornfetch.to": "Downloads",
    "echteralsfake.me": "Website (legacy)", "www.echteralsfake.me": "Website redirect (legacy)",
    "docs.echteralsfake.me": "Documentation", "api.echteralsfake.me": "Public API (legacy)",
    "vplan.echteralsfake.me": "Substitution plan", "mcp.echteralsfake.me": "Public MCP",
    "licenses.pornfetch.to": "Licensing API", "ip.echteralsfake.me": "IP service",
  };

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const pretty = (key) => key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  const number = (value) => new Intl.NumberFormat().format(Number(value || 0));
  const money = (cents) => new Intl.NumberFormat(undefined, { style: "currency", currency: "EUR" }).format(Number(cents || 0) / 100);
  const dateTime = (value) => { const p = new Date(value); return !value || Number.isNaN(p.valueOf()) ? String(value || "—") : p.toLocaleString(); };
  const timeAgo = (seconds) => {
    if (seconds == null) return "Never";
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    return `${Math.floor(seconds / 3600)}h ago`;
  };
  const formatDuration = (seconds) => {
    if (!seconds) return "0s";
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const parts = [];
    if (d > 0) parts.push(`${d}d`);
    if (h > 0 || d > 0) parts.push(`${h}h`);
    parts.push(`${m}m`);
    return parts.join(" ");
  };

  let activeViewName = "overview";
  let refreshTimer = null;

  async function stepUp() {
    const password = prompt("Re-enter the dashboard password to authorize this action:");
    if (!password) throw new Error("Authorization cancelled");
    await eafAuth.json("/dashboard/api/auth/password", { method: "POST", body: JSON.stringify({ password }) });
    await eafAuth.assertion("/dashboard/api/auth/step-up/options", "/dashboard/api/auth/step-up/verify");
  }

  async function action(name, payload, confirmation) {
    await stepUp();
    if (prompt(`Type ${confirmation} to confirm:`) !== confirmation) throw new Error("Confirmation did not match");
    return eafAuth.json(`/dashboard/api/actions/${name}`, { method: "POST", body: JSON.stringify({ ...payload, confirmation }) });
  }

  function table(items, actions = "", filterId = "table-search") {
    if (!items.length) return "<p class='empty'>No records.</p>";
    const keys = Object.keys(items[0]);
    return `
      <div class="table-toolbar">
        <input type="search" id="${filterId}" class="search-input" placeholder="Search ${items.length} records…">
        <span class="record-count">${items.length} total entries</span>
      </div>
      <div class="table-wrap">
        <table id="${filterId}-table">
          <thead>
            <tr>
              ${keys.map((key) => `<th>${esc(pretty(key))}</th>`).join("")}
              ${actions ? "<th>Actions</th>" : ""}
            </tr>
          </thead>
          <tbody>
            ${items.map((item) => `
              <tr>
                ${keys.map((key) => {
                  const val = item[key];
                  if (key === "status" || key === "state") {
                    const cls = ["finished", "valid", "online", "up"].includes(String(val).toLowerCase()) ? "status-pill ok" : "status-pill warn";
                    return `<td><span class="${cls}">${esc(val)}</span></td>`;
                  }
                  return `<td>${esc(val)}</td>`;
                }).join("")}
                ${actions ? `<td>${actions.replaceAll("{id}", esc(item.id))}</td>` : ""}
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  function attachTableSearch(filterId = "table-search") {
    const input = document.getElementById(filterId);
    const tbl = document.getElementById(`${filterId}-table`);
    if (!input || !tbl) return;
    input.addEventListener("input", () => {
      const q = input.value.toLowerCase().trim();
      const rows = tbl.querySelectorAll("tbody tr");
      rows.forEach(r => {
        r.style.display = r.textContent.toLowerCase().includes(q) ? "" : "none";
      });
    });
  }

  function dateRange(days) {
    const result = [], now = new Date();
    for (let offset = days - 1; offset >= 0; offset -= 1) {
      result.push(new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - offset)).toISOString().slice(0, 10));
    }
    return result;
  }

  function lineChart(series, days, title, valueFormatter = number) {
    const width = 960, height = 300, pad = { left: 62, right: 18, top: 28, bottom: 42 };
    const values = series.flatMap((item) => days.map((day) => Number(item.values[day] || 0))), maximum = Math.max(1, ...values);
    const x = (index) => pad.left + (index * (width - pad.left - pad.right)) / Math.max(1, days.length - 1);
    const y = (value) => height - pad.bottom - (Number(value) / maximum) * (height - pad.top - pad.bottom);
    const grids = [0, .25, .5, .75, 1].map((part) => {
      const value = maximum * part, position = y(value);
      return `<line x1="${pad.left}" y1="${position}" x2="${width - pad.right}" y2="${position}" class="chart-grid"/><text x="${pad.left - 10}" y="${position + 4}" text-anchor="end" class="chart-label">${esc(valueFormatter(Math.round(value)))}</text>`;
    }).join("");
    const paths = series.map((item, index) => {
      const pts = days.map((day, dayIndex) => `${x(dayIndex)},${y(item.values[day] || 0)}`).join(" ");
      return `<polyline points="${pts}" fill="none" stroke="${colors[index % colors.length]}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><title>${esc(item.label)}</title></polyline>`;
    }).join("");
    const labelEvery = Math.max(1, Math.ceil(days.length / 7));
    const labels = days.map((day, index) => index % labelEvery === 0 || index === days.length - 1 ? `<text x="${x(index)}" y="${height - 14}" text-anchor="middle" class="chart-label">${esc(day.slice(5))}</text>` : "").join("");
    return `
      <div class="chart-panel">
        <div class="chart-heading">
          <h3>${esc(title)}</h3>
          <span>Peak ${esc(valueFormatter(maximum))}</span>
        </div>
        <svg class="line-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(title)}">
          <title>${esc(title)}</title>
          ${grids}${paths}${labels}
        </svg>
        <div class="legend">
          ${series.map((item, index) => `<span><i class="legend-dot color-${index % colors.length}"></i>${esc(item.label)}</span>`).join("")}
        </div>
      </div>
    `;
  }

  function distributionChart(totals, title, formatter = number) {
    const entries = Object.entries(totals).filter((entry) => entry[1] > 0).sort((left, right) => right[1] - left[1]), maximum = Math.max(1, ...entries.map((entry) => entry[1]));
    const rows = entries.map(([host, value], index) => {
      const barWidth = Math.max(2, (value / maximum) * 610);
      return `<g transform="translate(0 ${index * 42})"><text x="0" y="17" class="chart-label endpoint-label">${esc(endpointNames[host] || host)}</text><rect x="190" y="2" width="${barWidth}" height="22" rx="6" fill="${colors[index % colors.length]}"><title>${esc(host)}: ${esc(formatter(value))}</title></rect><text x="${202 + barWidth}" y="18" class="chart-value">${esc(formatter(value))}</text></g>`;
    }).join("");
    const height = Math.max(70, entries.length * 42 + 8);
    return `
      <div class="chart-panel">
        <div class="chart-heading">
          <h3>${esc(title)}</h3>
          <span>${esc(formatter(entries.reduce((sum, entry) => sum + entry[1], 0)))} total</span>
        </div>
        <svg class="bar-chart" viewBox="0 0 960 ${height}" role="img" aria-label="${esc(title)}">
          <title>${esc(title)}</title>
          ${rows || '<text x="20" y="35" class="chart-label">No data yet</text>'}
        </svg>
      </div>
    `;
  }

  function trafficModel(items, range) {
    const days = dateRange(range), requests = items.filter((item) => item.metric === "requests" && item.host !== "_other"), visitors = items.filter((item) => item.metric === "visitors");
    const totals = {}, hosts = {}, dailyTotal = {}, dailyVisitors = {};
    for (const item of requests) {
      totals[item.host] = (totals[item.host] || 0) + Number(item.value);
      hosts[item.host] ||= {};
      hosts[item.host][item.day] = (hosts[item.host][item.day] || 0) + Number(item.value);
      dailyTotal[item.day] = (dailyTotal[item.day] || 0) + Number(item.value);
    }
    for (const item of visitors) dailyVisitors[item.day] = (dailyVisitors[item.day] || 0) + Number(item.value);
    return {
      days,
      totals,
      totalRequests: Object.values(totals).reduce((sum, value) => sum + value, 0),
      totalVisitors: Object.values(dailyVisitors).reduce((sum, value) => sum + value, 0),
      endpointSeries: Object.entries(hosts).sort((left, right) => Object.values(right[1]).reduce((a, b) => a + b, 0) - Object.values(left[1]).reduce((a, b) => a + b, 0)).map(([host, values]) => ({ label: endpointNames[host] || host, values })),
      totalSeries: [{ label: "Requests", values: dailyTotal }, { label: "Approx. visitors", values: dailyVisitors }]
    };
  }

  function trafficMarkup(items, range, compact = false) {
    const model = trafficModel(items, range);
    const cards = `
      <div class="cards metric-cards">
        <article class="card"><span>Requests</span><strong>${number(model.totalRequests)}</strong><small>Across public endpoints</small></article>
        <article class="card"><span>Approx. visitors</span><strong>${number(model.totalVisitors)}</strong><small>Cookie-free daily deduplication</small></article>
        <article class="card"><span>Active endpoints</span><strong>${number(Object.keys(model.totals).length)}</strong><small>With requests in this period</small></article>
      </div>
    `;
    const total = lineChart(model.totalSeries, model.days, `Daily traffic · ${range} days`);
    return compact ? `${cards}${total}` : `${cards}<div class="chart-grid-layout">${total}${distributionChart(model.totals, "Requests by endpoint")}</div>${lineChart(model.endpointSeries, model.days, "Endpoint request trends")}`;
  }

  function renderVitals(vitals) {
    if (!vitals) return "";
    const cpu = vitals.cpu || {};
    const mem = vitals.memory || {};
    const disk = vitals.disk || {};
    const uptimeStr = formatDuration(vitals.uptime_seconds);

    return `
      <article class="vital-card">
        <div class="vital-header">
          <span class="vital-title">CPU Load</span>
          <span class="vital-badge">${cpu.percent || 0}%</span>
        </div>
        <div class="meter-bar"><div class="meter-fill" style="width: ${Math.min(100, cpu.percent || 0)}%"></div></div>
        <div class="vital-footer">
          <span>Load Avg: <strong>${cpu.load_1m || 0}</strong>, ${cpu.load_5m || 0}, ${cpu.load_15m || 0}</span>
          <small>${cpu.count || 1} Cores</small>
        </div>
      </article>

      <article class="vital-card">
        <div class="vital-header">
          <span class="vital-title">Memory (RAM)</span>
          <span class="vital-badge">${mem.percent || 0}%</span>
        </div>
        <div class="meter-bar"><div class="meter-fill color-green" style="width: ${Math.min(100, mem.percent || 0)}%"></div></div>
        <div class="vital-footer">
          <span><strong>${number(mem.used_mb)} MB</strong> / ${number(mem.total_mb)} MB</span>
          <small>${number(mem.avail_mb)} MB Free</small>
        </div>
      </article>

      <article class="vital-card">
        <div class="vital-header">
          <span class="vital-title">Disk Storage (/)</span>
          <span class="vital-badge">${disk.percent || 0}%</span>
        </div>
        <div class="meter-bar"><div class="meter-fill color-blue" style="width: ${Math.min(100, disk.percent || 0)}%"></div></div>
        <div class="vital-footer">
          <span><strong>${disk.used_gb} GB</strong> / ${disk.total_gb} GB</span>
          <small>${disk.free_gb} GB Avail</small>
        </div>
      </article>

      <article class="vital-card">
        <div class="vital-header">
          <span class="vital-title">System Uptime</span>
          <span class="vital-badge badge-active">RUNNING</span>
        </div>
        <div class="vital-uptime-text">${uptimeStr}</div>
        <div class="vital-footer">
          <span>Host: <strong>${esc(vitals.hostname || 'MSI-Origin')}</strong></span>
          <small>${esc(vitals.os || 'Arch Linux')} Kernel</small>
        </div>
      </article>
    `;
  }

  async function overview() {
    const data = await eafAuth.json("/dashboard/api/overview");
    content.innerHTML = document.getElementById("overview-template").innerHTML;

    // Update topbar status
    if (topbarStatusText) {
      const nodeCount = (data.nodes || []).length;
      topbarStatusText.textContent = `Fleet: ${nodeCount} Node${nodeCount === 1 ? '' : 's'} Online`;
    }

    // Host Vitals
    document.getElementById("vitals").innerHTML = renderVitals(data.vitals);

    // Application summary cards
    const finished = (data.payments || []).find((item) => item.status === "finished")?.count || 0;
    const authFailures = Object.values(data.security || {}).reduce((sum, value) => sum + Number(value || 0), 0);
    document.getElementById("summary").innerHTML = [
      ["Completed Checkouts", finished, "Valid purchase transactions"],
      ["Valid Licenses", data.licenses?.valid || 0, "Active Ed25519 offline licenses"],
      ["Connected Nodes", (data.nodes || []).length, "LAN servers & websites"],
      ["Error Reports", data.errors || 0, "PocketBase error logs"],
      ["School Feedback", data.feedback || 0, "Student/school submissions"],
      ["Auth Events · 24h", authFailures, "Security challenges & failed logins"],
    ].map(([label, value, sub]) => `
      <article class="card">
        <span>${label}</span>
        <strong>${number(value)}</strong>
        <small>${sub}</small>
      </article>
    `).join("");

    // Services grid with memory footprints
    document.getElementById("services").innerHTML = (data.services || []).map((service) => `
      <article class="service">
        <span class="dot ${service.ActiveState === "active" ? "ok" : "bad"}"></span>
        <div class="service-info">
          <strong>${esc(service.name)}</strong>
          <small>${esc(service.SubState || service.ActiveState)} · ${service.memory_mb ? `${service.memory_mb} MB` : 'Host'}</small>
        </div>
        ${service.name !== "dashboard" ? `<button class="service-action-btn" data-restart="${esc(service.name)}">Restart</button>` : ""}
      </article>
    `).join("");

    // Usage traffic chart
    document.getElementById("usage").innerHTML = trafficMarkup(data.usage || [], 30, true);

    // Action listeners
    document.querySelectorAll("[data-restart]").forEach((button) =>
      button.addEventListener("click", async () => {
        try {
          await action("restart_service", { service: button.dataset.restart }, button.dataset.restart);
          await overview();
        } catch (error) {
          alert(error.message);
        }
      })
    );

    document.getElementById("backup").addEventListener("click", async () => {
      try {
        const result = await action("backup", {}, "backup");
        alert(`Backup created successfully: ${result.result.backup}`);
      } catch (error) {
        alert(error.message);
      }
    });
  }

  async function nodesView() {
    const data = await eafAuth.json("/dashboard/api/nodes");
    const items = data.items || [];

    const originIp = "192.168.0.11";
    const routerIp = "192.168.0.1";

    content.innerHTML = `
      <section>
        <div class="section-head">
          <div>
            <p class="eyebrow">LAN MULTI-SERVER TOPOLOGY</p>
            <h2>Connected Servers & Websites</h2>
          </div>
          <div class="head-actions">
            <button id="add-node-btn" class="action-btn btn-primary">+ Add LAN Server</button>
          </div>
        </div>

        <p class="section-note">
          All servers are connected to the same local Ethernet router (<code>${routerIp}</code>).
          The centralized MSI Origin server aggregates health, hosted websites, and metrics from each node.
        </p>

        <!-- Visual Network Topology -->
        <div class="topology-panel">
          <div class="topology-title">Ethernet Router & Switch Network (192.168.0.0/24)</div>
          <div class="topology-graph">
            <div class="topo-node router-node">
              <span class="topo-icon">🌐</span>
              <strong>Compal CH7485E Router</strong>
              <small>${routerIp} (Gateway)</small>
            </div>
            <div class="topo-cable"></div>
            <div class="topo-hub">Gigabit Ethernet Interconnect</div>
            <div class="topo-branches">
              ${items.map(n => `
                <div class="topo-branch">
                  <div class="topo-cable-vert"></div>
                  <div class="topo-node ${n.role === 'gateway' ? 'origin-node' : 'child-node'}">
                    <span class="dot ${n.status === 'online' ? 'ok' : 'bad'}"></span>
                    <strong>${esc(n.name)}</strong>
                    <small>${esc(n.address)} · ${esc(pretty(n.role))}</small>
                  </div>
                </div>
              `).join("")}
            </div>
          </div>
        </div>

        <!-- Node Cards Grid -->
        <div class="section-head-sub">
          <h3>Registered Fleet Nodes</h3>
          <span class="sub-label">${items.length} server${items.length === 1 ? '' : 's'} registered</span>
        </div>

        <div class="node-grid">
          ${items.map(n => {
            const tel = n.telemetry || {};
            const isGateway = n.role === "gateway";
            const services = tel.services || [];
            return `
              <article class="node-card">
                <header class="node-header">
                  <div>
                    <span class="status-pill ${n.status === 'online' ? 'ok' : 'warn'}">${n.status.toUpperCase()}</span>
                    <h4>${esc(n.name)}</h4>
                    <span class="node-address">${esc(n.address)} · Role: ${esc(pretty(n.role))}</span>
                  </div>
                  <div class="node-header-actions">
                    <button class="probe-btn" data-probe="${esc(n.id)}" title="Probe server health">⚡ Probe</button>
                    ${!isGateway ? `<button class="danger-icon-btn" data-delete-node="${esc(n.id)}" title="Delete node">🗑</button>` : ''}
                  </div>
                </header>

                <div class="node-vitals-row">
                  <div class="node-vital">
                    <span>CPU</span>
                    <strong>${tel.cpu_percent != null ? `${tel.cpu_percent}%` : '—'}</strong>
                  </div>
                  <div class="node-vital">
                    <span>Memory</span>
                    <strong>${tel.memory_used_mb != null ? `${number(tel.memory_used_mb)} MB` : '—'}</strong>
                  </div>
                  <div class="node-vital">
                    <span>Disk</span>
                    <strong>${tel.disk_used_gb != null ? `${tel.disk_used_gb} GB` : '—'}</strong>
                  </div>
                  <div class="node-vital">
                    <span>Uptime</span>
                    <strong>${tel.uptime_seconds != null ? formatDuration(tel.uptime_seconds) : '—'}</strong>
                  </div>
                </div>

                <div class="node-services-section">
                  <div class="sub-label">Hosted Websites & Endpoints</div>
                  ${services.length > 0 ? `
                    <div class="node-service-list">
                      ${services.map(s => `
                        <div class="node-service-item">
                          <span class="service-tag">
                            <span class="mini-dot" style="background:${s.status === 'up' ? 'var(--green)' : 'var(--red)'}"></span>
                            ${esc(s.name)}
                          </span>
                          <span class="service-meta">${s.latency_ms ? `${s.latency_ms} ms` : ''} · ${s.status === 'up' ? 'Online' : 'Down'}</span>
                        </div>
                      `).join("")}
                    </div>
                  ` : `
                    <p class="empty-services">No website checks reported yet. Point the node agent to local URLs to monitor.</p>
                  `}
                </div>

                <footer class="node-footer">
                  <small>Last seen: ${timeAgo(n.last_seen_seconds_ago)}</small>
                  <code>ID: ${esc(n.id)}</code>
                </footer>
              </article>
            `;
          }).join("")}
        </div>

        <!-- Connection Guide Box -->
        <div class="detail-card connection-guide">
          <p class="eyebrow">HOW TO CONNECT ANOTHER SERVER ON THIS ETHERNET</p>
          <h3>One-Command Setup for Any LAN Server</h3>
          <p class="section-note">
            Run the turnkey Python agent on any other machine connected to the router. It gathers CPU, RAM, Disk, and local website health, pushing them to this dashboard every 15s.
          </p>
          <div class="code-box">
            <pre><code># 1. Download and run test on your remote server (no pip packages needed):
curl -s http://${originIp}:18010/dashboard/static/eaf-node-agent.py -o eaf-node-agent.py
python3 eaf-node-agent.py --dashboard-url http://${originIp}:18010 --test

# 2. Or install as a background systemd service:
sudo python3 eaf-node-agent.py --install-systemd --dashboard-url http://${originIp}:18010 --name "Server 2" --probe-service "Blog=http://localhost:3000"</code></pre>
          </div>
        </div>
      </section>
    `;

    // Modal triggers
    const modal = document.getElementById("node-modal");
    document.getElementById("add-node-btn").addEventListener("click", () => modal.showModal());
    document.getElementById("close-modal").addEventListener("click", () => modal.close());
    document.getElementById("cancel-node").addEventListener("click", () => modal.close());

    document.getElementById("node-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const node_id = document.getElementById("node-id-input").value.trim();
      const name = document.getElementById("node-name-input").value.trim();
      const address = document.getElementById("node-address-input").value.trim();
      const role = document.getElementById("node-role-input").value;
      const probe_url = document.getElementById("node-probe-input").value.trim() || null;

      try {
        await eafAuth.json("/dashboard/api/nodes", {
          method: "POST",
          body: JSON.stringify({ id: node_id, name, address, role, probe_url }),
        });
        modal.close();
        await nodesView();
      } catch (err) {
        alert("Failed to register server: " + err.message);
      }
    });

    // Probe buttons
    document.querySelectorAll("[data-probe]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.probe;
        btn.textContent = "⏳ Probing…";
        try {
          const res = await eafAuth.json(`/dashboard/api/nodes/${id}/probe`, { method: "POST", body: "{}" });
          alert(`Probe result for ${id}: ${res.result?.ok ? 'Online (Latency: ' + res.result.latency_ms + 'ms)' : 'Failed: ' + (res.result?.error || 'Unavailable')}`);
          await nodesView();
        } catch (err) {
          alert("Probe error: " + err.message);
        }
      });
    });

    // Delete buttons
    document.querySelectorAll("[data-delete-node]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.deleteNode;
        try {
          await action("delete_node", { id }, id);
          await nodesView();
        } catch (err) {
          alert(err.message);
        }
      });
    });
  }

  async function embedView() {
    const originUrl = window.location.origin;
    const lanUrl = "http://192.168.0.11:18010";

    content.innerHTML = `
      <section>
        <div class="section-head">
          <div>
            <p class="eyebrow">EMBED & FEDERATION SYSTEM</p>
            <h2>Website Embedding & Integrations</h2>
          </div>
        </div>

        <p class="section-note">
          Allow other websites to show live infrastructure health, server load, and service status.
          Supports lightweight <strong>Shadow-DOM Web Components</strong>, <strong>iframe embeds</strong>, and <strong>CORS REST APIs</strong>.
        </p>

        <!-- Live Playground -->
        <div class="embed-playground">
          <div class="playground-controls">
            <h3>Interactive Widget Configurator</h3>
            <div class="form-group">
              <label for="widget-type-select">Widget Type</label>
              <select id="widget-type-select">
                <option value="card" selected>Interactive Server Health Card</option>
                <option value="badge">Compact Operational Status Badge</option>
                <option value="iframe">Standalone iframe View</option>
              </select>
            </div>
            <div class="form-group">
              <label for="widget-source-select">Connection Source</label>
              <select id="widget-source-select">
                <option value="${originUrl}" selected>Public HTTPS (${originUrl})</option>
                <option value="${lanUrl}">LAN Gigabit Ethernet (${lanUrl})</option>
              </select>
            </div>

            <div class="sub-label">Generated Embed Snippet:</div>
            <div class="code-box">
              <pre><code id="generated-snippet"></code></pre>
            </div>
            <button id="copy-snippet-btn" class="action-btn btn-secondary">📋 Copy Snippet</button>
          </div>

          <div class="playground-preview">
            <h3>Live Interactive Preview</h3>
            <div class="preview-box" id="preview-container">
              <!-- Rendered via JS -->
            </div>
          </div>
        </div>

        <!-- API Integration Guide -->
        <div class="detail-card api-guide">
          <p class="eyebrow">CORS-ENABLED REST API FOR CUSTOM SITES</p>
          <h3>Fetch Real-Time Status via JavaScript from Any Site</h3>
          <p class="section-note">
            All public embed endpoints return permissive CORS headers (<code>Access-Control-Allow-Origin: *</code>) and sanitized public telemetry.
          </p>

          <div class="code-box">
            <pre><code>// Example: Fetch and render server status on any website (Next.js, React, HTML, WordPress):
fetch("${originUrl}/dashboard/api/embed/data")
  .then(res => res.json())
  .then(data => {
    console.log("Cluster Status:", data.cluster.status); // "operational"
    console.log("CPU Load:", data.host.cpu_percent + "%");
    console.log("Active Nodes:", data.cluster.nodes_online + "/" + data.cluster.nodes_total);
  });</code></pre>
          </div>
        </div>
      </section>
    `;

    const widgetSelect = document.getElementById("widget-type-select");
    const sourceSelect = document.getElementById("widget-source-select");
    const snippetCode = document.getElementById("generated-snippet");
    const previewContainer = document.getElementById("preview-container");
    const copyBtn = document.getElementById("copy-snippet-btn");

    function updatePlayground() {
      const type = widgetSelect.value;
      const src = sourceSelect.value;

      let code = "";
      if (type === "iframe") {
        code = `<iframe src="${src}/dashboard/embed/view?widget=card" width="380" height="230" style="border:none;border-radius:12px;overflow:hidden;" loading="lazy"></iframe>`;
        previewContainer.innerHTML = `<iframe src="/dashboard/embed/view?widget=card" width="380" height="230" style="border:none;border-radius:12px;overflow:hidden;"></iframe>`;
      } else {
        code = `<!-- EAF Server Status Widget -->\n<div data-eaf-widget="${type}" data-eaf-source="${src}"></div>\n<script src="${src}/dashboard/static/embed.js" defer></script>`;
        previewContainer.innerHTML = `<div data-eaf-widget="${type}" data-eaf-source="${window.location.origin}"></div>`;
        // Load embed.js if custom element not loaded
        if (!customElements.get("eaf-widget")) {
          const s = document.createElement("script");
          s.src = "/dashboard/static/embed.js";
          document.body.appendChild(s);
        } else {
          // Trigger mount
          const w = document.createElement("eaf-widget");
          w.setAttribute("data-widget", type);
          w.setAttribute("data-source", window.location.origin);
          previewContainer.innerHTML = "";
          previewContainer.appendChild(w);
        }
      }

      snippetCode.textContent = code;
    }

    widgetSelect.addEventListener("change", updatePlayground);
    sourceSelect.addEventListener("change", updatePlayground);
    copyBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(snippetCode.textContent);
      copyBtn.textContent = "✓ Copied to Clipboard!";
      setTimeout(() => { copyBtn.textContent = "📋 Copy Snippet"; }, 2000);
    });

    updatePlayground();
  }

  async function traffic(range = 30) {
    const data = await eafAuth.json(`/dashboard/api/usage?days=${range}`);
    content.innerHTML = `
      <section>
        <div class="section-head">
          <div><p class="eyebrow">PRIVACY-PRESERVING ANALYTICS</p><h2>Traffic</h2></div>
          <label class="range-control">Period
            <select id="traffic-range">
              <option value="7">7 days</option>
              <option value="30">30 days</option>
              <option value="90">90 days</option>
              <option value="365">1 year</option>
            </select>
          </label>
        </div>
        <p class="section-note">Request totals come from aggregate Caddy counters. Approximate visitors use daily, non-reversible duplicate checks without cookies or persistent IP addresses.</p>
        ${trafficMarkup(data.items || [], range)}
      </section>
    `;
    document.getElementById("traffic-range").value = String(range);
    document.getElementById("traffic-range").addEventListener("change", (e) => traffic(Number(e.target.value)).catch(showError));
  }

  let selectedRevenueMode = "production";
  function revenueChart(data, mode) {
    const days = dateRange(90), byDay = Object.fromEntries((data.daily || []).map((item) => [item.day, item]));
    if (mode === "sandbox") return lineChart([{ label: "Sandbox gross", values: Object.fromEntries(days.map((day) => [day, byDay[day]?.sandboxGrossCents || 0])) }], days, "Daily sandbox volume · 90 days", money);
    return lineChart([{ label: "Crypto gross", values: Object.fromEntries(days.map((day) => [day, byDay[day]?.cryptoGrossCents || 0])) }, { label: "Patreon gross", values: Object.fromEntries(days.map((day) => [day, byDay[day]?.patreonGrossCents || 0])) }], days, "Daily production revenue · 90 days", money);
  }

  async function revenue(mode = selectedRevenueMode) {
    selectedRevenueMode = mode === "sandbox" ? "sandbox" : "production";
    const data = await eafAuth.json("/dashboard/api/revenue"), all = data.totals.all, assumptions = data.patreonAssumptions;
    const period = data.totals, isSandbox = selectedRevenueMode === "sandbox";
    const productionCards = `<div class="cards revenue-cards"><article class="card accent-card"><span>Gross revenue</span><strong>${money(all.grossCents)}</strong><small>${number(all.sales)} production sales · all time</small></article><article class="card"><span>Today</span><strong>${money(period.today.grossCents)}</strong><small>${number(period.today.sales)} sales</small></article><article class="card"><span>Last 7 days</span><strong>${money(period.week.grossCents)}</strong><small>${number(period.week.sales)} sales</small></article><article class="card"><span>Average order</span><strong>${money(all.sales ? Math.round(all.grossCents / all.sales) : 0)}</strong><small>Across completed production sales</small></article><article class="card"><span>Crypto gross</span><strong>${money(all.cryptoGrossCents)}</strong><small>${number(all.cryptoSales)} completed production checkouts</small></article><article class="card"><span>Patreon gross</span><strong>${money(all.patreonGrossCents)}</strong><small>${number(all.patreonSales)} delivered one-time licenses</small></article><article class="card"><span>Patreon estimated net</span><strong>${money(all.patreonNetEstimateCents)}</strong><small>${money(assumptions.estimatedNetCents)} estimated per sale</small></article></div>`;
    const sandboxCards = `<div class="cards revenue-cards"><article class="card accent-card sandbox-card"><span>Sandbox volume</span><strong>${money(all.sandboxGrossCents)}</strong><small>${number(all.sandboxSales)} test sales · all time</small></article><article class="card"><span>Today</span><strong>${money(period.today.sandboxGrossCents)}</strong><small>${number(period.today.sandboxSales)} test sales</small></article><article class="card"><span>Last 7 days</span><strong>${money(period.week.sandboxGrossCents)}</strong><small>${number(period.week.sandboxSales)} test sales</small></article><article class="card"><span>Average test order</span><strong>${money(all.sandboxSales ? Math.round(all.sandboxGrossCents / all.sandboxSales) : 0)}</strong><small>Across completed sandbox checkouts</small></article><article class="card"><span>Completed test checkouts</span><strong>${number(all.sandboxSales)}</strong><small>Never included in production earnings</small></article></div>`;
    const productionDetails = `<div class="channel-grid"><article class="detail-card">${distributionChart({ Crypto: all.cryptoGrossCents, Patreon: all.patreonGrossCents }, "Production gross by source", money)}</article><article class="detail-card"><p class="eyebrow">PATREON ESTIMATE</p><h3>${money(assumptions.priceCents)} sale → ${money(assumptions.estimatedNetCents)} net</h3><dl class="fee-list"><div><dt>Platform fee</dt><dd>${assumptions.platformRatePercent}%</dd></div><div><dt>EUR processing</dt><dd>${assumptions.processingRatePercent}% + ${money(assumptions.processingFixedCents)}</dd></div><div><dt>Estimated fee per sale</dt><dd>${money(assumptions.estimatedFeeCents)}</dd></div></dl><p class="section-note">Estimate only; excludes ${esc(assumptions.excludes)}. Crypto provider/network fees are not stored, so crypto is shown as gross. <a href="https://support.patreon.com/hc/en-us/articles/11111747095181-Creator-fees-overview" target="_blank" rel="noopener noreferrer">Official Patreon fee schedule</a>.</p></article></div>`;
    const sandboxDetails = `<article class="detail-card"><p class="eyebrow">TEST ENVIRONMENT</p><h3>Sandbox activity stays isolated</h3><p class="section-note">Only completed checkouts marked <code>environment=sandbox</code> are shown here. They are excluded from every production revenue total and from the production trend.</p></article>`;
    content.innerHTML = `<section><div class="section-head"><div><p class="eyebrow">SALES ANALYTICS</p><h2>Revenue</h2></div><label class="range-control">Environment<select id="revenue-environment"><option value="production">Production</option><option value="sandbox">Sandbox</option></select></label></div><p class="section-note">${isSandbox ? "Completed sandbox transactions only. These figures are test volume, not earnings." : "Completed production crypto checkouts and delivered Patreon licenses. Sandbox transactions are excluded."}</p>${isSandbox ? sandboxCards : productionCards}${revenueChart(data, selectedRevenueMode)}${isSandbox ? sandboxDetails : productionDetails}</section>`;
    document.getElementById("revenue-environment").value = selectedRevenueMode;
    document.getElementById("revenue-environment").addEventListener("change", (event) => revenue(event.target.value).catch(showError));
  }

  function reportCards(items, kind) {
    if (!items.length) return "<p class='empty'>No reports.</p>";
    const isError = kind === "errors";
    return `
      <div class="table-toolbar">
        <input type="search" id="report-search" class="search-input" placeholder="Search reports…">
        <span class="record-count">${items.length} total entries</span>
      </div>
      <div class="report-grid" id="report-grid">
        ${items.map((item) => `
          <article class="report-card">
            <header>
              <div><span class="report-type">${isError ? "Error report" : "Feedback"}</span><time>${esc(dateTime(item.created || item.created_at))}</time></div>
              <button class="danger" data-delete="${isError ? "delete_error" : "delete_feedback"}" data-id="${esc(item.id)}">Delete</button>
            </header>
            <pre>${esc(item.message)}</pre>
            <footer><code>${esc(item.id)}</code>${isError && item.updated && item.updated !== item.created ? `<span>Updated ${esc(dateTime(item.updated))}</span>` : ""}</footer>
          </article>
        `).join("")}
      </div>
    `;
  }

  async function records(kind) {
    const data = await eafAuth.json(`/dashboard/api/${kind}`);
    const deletion = { errors: "delete_error", feedback: "delete_feedback", checklist: "delete_checklist" }[kind];
    const body = ["errors", "feedback"].includes(kind) ? reportCards(data.items || [], kind) : table(data.items || [], deletion ? `<button class="danger" data-delete="${deletion}" data-id="{id}">Delete</button>` : "");
    content.innerHTML = `<section><div class="section-head"><div><p class="eyebrow">RECORDS</p><h2>${esc(pretty(kind))}</h2></div></div>${body}</section>`;
    if (["errors", "feedback"].includes(kind)) {
      const search = document.getElementById("report-search");
      if (search) {
        search.addEventListener("input", () => {
          const q = search.value.toLowerCase().trim();
          document.querySelectorAll(".report-card").forEach(c => {
            c.style.display = c.textContent.toLowerCase().includes(q) ? "" : "none";
          });
        });
      }
    } else {
      attachTableSearch();
    }
    document.querySelectorAll("[data-delete]").forEach((button) => button.addEventListener("click", async () => { try { await action(button.dataset.delete, { id: button.dataset.id }, button.dataset.id); await records(kind); } catch (error) { alert(error.message); } }));
  }

  function showError(error) { content.innerHTML = `<p class="error">${esc(error.message)}</p>`; }

  const views = {
    overview,
    nodes: nodesView,
    embed: embedView,
    traffic,
    revenue,
  };

  function switchView(viewName) {
    activeViewName = viewName;
    document.querySelectorAll(".nav").forEach((item) => item.classList.remove("active"));
    const activeBtn = document.querySelector(`.nav[data-view='${viewName}']`);
    if (activeBtn) activeBtn.classList.add("active");
    const fn = views[viewName] || (() => records(viewName));
    fn().catch(showError);
  }

  document.querySelectorAll("button[data-view]").forEach((button) =>
    button.addEventListener("click", () => switchView(button.dataset.view))
  );

  document.getElementById("logout").addEventListener("click", async () => {
    await eafAuth.json("/dashboard/api/auth/logout", { method: "POST", body: "{}" });
    location.href = "/dashboard/login";
  });

  // Setup auto-refresh
  function setupRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    const secs = Number(autoRefreshSelect?.value || 0);
    if (secs > 0) {
      refreshTimer = setInterval(() => {
        const fn = views[activeViewName] || (() => records(activeViewName));
        fn().catch(console.error);
      }, secs * 1000);
    }
  }

  if (autoRefreshSelect) {
    autoRefreshSelect.addEventListener("change", setupRefresh);
    setupRefresh();
  }

  overview().catch(showError);
})();
