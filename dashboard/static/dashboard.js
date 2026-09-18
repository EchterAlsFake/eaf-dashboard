(() => {
  const content = document.getElementById("content");
  const colors = ["#68a6ff", "#4fda9b", "#c084fc", "#f9c74f", "#ff7b89", "#58d6d6", "#ff9f5a", "#a3e635"];
  const endpointNames = {
    "echteralsfake.me": "Website", "www.echteralsfake.me": "Website redirect",
    "docs.echteralsfake.me": "Documentation", "api.echteralsfake.me": "Public API",
    "vplan.echteralsfake.me": "Substitution plan", "mcp.echteralsfake.me": "Public MCP",
    "licenses.echteralsfake.me": "Licensing API", "ip.echteralsfake.me": "IP service",
  };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  const pretty = (key) => key.replace(/_/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
  const number = (value) => new Intl.NumberFormat().format(Number(value || 0));
  const money = (cents) => new Intl.NumberFormat(undefined, { style: "currency", currency: "EUR" }).format(Number(cents || 0) / 100);
  const dateTime = (value) => { const parsed = new Date(value); return !value || Number.isNaN(parsed.valueOf()) ? String(value || "—") : parsed.toLocaleString(); };

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
  function table(items, actions = "") {
    if (!items.length) return "<p class='empty'>No records.</p>";
    const keys = Object.keys(items[0]);
    return `<div class="table-wrap"><table><thead><tr>${keys.map((key) => `<th>${esc(pretty(key))}</th>`).join("")}${actions ? "<th>Actions</th>" : ""}</tr></thead><tbody>${items.map((item) => `<tr>${keys.map((key) => `<td>${esc(item[key])}</td>`).join("")}${actions ? `<td>${actions.replaceAll("{id}", esc(item.id))}</td>` : ""}</tr>`).join("")}</tbody></table></div>`;
  }
  function dateRange(days) {
    const result = [], now = new Date();
    for (let offset = days - 1; offset >= 0; offset -= 1) result.push(new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - offset)).toISOString().slice(0, 10));
    return result;
  }
  function lineChart(series, days, title, valueFormatter = number) {
    const width = 960, height = 300, pad = { left: 62, right: 18, top: 28, bottom: 42 };
    const values = series.flatMap((item) => days.map((day) => Number(item.values[day] || 0))), maximum = Math.max(1, ...values);
    const x = (index) => pad.left + (index * (width - pad.left - pad.right)) / Math.max(1, days.length - 1);
    const y = (value) => height - pad.bottom - (Number(value) / maximum) * (height - pad.top - pad.bottom);
    const grids = [0, .25, .5, .75, 1].map((part) => { const value = maximum * part, position = y(value); return `<line x1="${pad.left}" y1="${position}" x2="${width - pad.right}" y2="${position}" class="chart-grid"/><text x="${pad.left - 10}" y="${position + 4}" text-anchor="end" class="chart-label">${esc(valueFormatter(Math.round(value)))}</text>`; }).join("");
    const paths = series.map((item, index) => `<polyline points="${days.map((day, dayIndex) => `${x(dayIndex)},${y(item.values[day] || 0)}`).join(" ")}" fill="none" stroke="${colors[index % colors.length]}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><title>${esc(item.label)}</title></polyline>`).join("");
    const labelEvery = Math.max(1, Math.ceil(days.length / 7));
    const labels = days.map((day, index) => index % labelEvery === 0 || index === days.length - 1 ? `<text x="${x(index)}" y="${height - 14}" text-anchor="middle" class="chart-label">${esc(day.slice(5))}</text>` : "").join("");
    return `<div class="chart-panel"><div class="chart-heading"><h3>${esc(title)}</h3><span>Peak ${esc(valueFormatter(maximum))}</span></div><svg class="line-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(title)}"><title>${esc(title)}</title>${grids}${paths}${labels}</svg><div class="legend">${series.map((item, index) => `<span><i class="legend-dot color-${index % colors.length}"></i>${esc(item.label)}</span>`).join("")}</div></div>`;
  }
  function distributionChart(totals, title, formatter = number) {
    const entries = Object.entries(totals).filter((entry) => entry[1] > 0).sort((left, right) => right[1] - left[1]), maximum = Math.max(1, ...entries.map((entry) => entry[1]));
    const rows = entries.map(([host, value], index) => { const barWidth = Math.max(2, (value / maximum) * 610); return `<g transform="translate(0 ${index * 42})"><text x="0" y="17" class="chart-label endpoint-label">${esc(endpointNames[host] || host)}</text><rect x="190" y="2" width="${barWidth}" height="22" rx="6" fill="${colors[index % colors.length]}"><title>${esc(host)}: ${esc(formatter(value))}</title></rect><text x="${202 + barWidth}" y="18" class="chart-value">${esc(formatter(value))}</text></g>`; }).join("");
    const height = Math.max(70, entries.length * 42 + 8);
    return `<div class="chart-panel"><div class="chart-heading"><h3>${esc(title)}</h3><span>${esc(formatter(entries.reduce((sum, entry) => sum + entry[1], 0)))} total</span></div><svg class="bar-chart" viewBox="0 0 960 ${height}" role="img" aria-label="${esc(title)}"><title>${esc(title)}</title>${rows || '<text x="20" y="35" class="chart-label">No data yet</text>'}</svg></div>`;
  }
  function trafficModel(items, range) {
    const days = dateRange(range), requests = items.filter((item) => item.metric === "requests" && item.host !== "_other"), visitors = items.filter((item) => item.metric === "visitors");
    const totals = {}, hosts = {}, dailyTotal = {}, dailyVisitors = {};
    for (const item of requests) { totals[item.host] = (totals[item.host] || 0) + Number(item.value); hosts[item.host] ||= {}; hosts[item.host][item.day] = (hosts[item.host][item.day] || 0) + Number(item.value); dailyTotal[item.day] = (dailyTotal[item.day] || 0) + Number(item.value); }
    for (const item of visitors) dailyVisitors[item.day] = (dailyVisitors[item.day] || 0) + Number(item.value);
    return { days, totals, totalRequests: Object.values(totals).reduce((sum, value) => sum + value, 0), totalVisitors: Object.values(dailyVisitors).reduce((sum, value) => sum + value, 0), endpointSeries: Object.entries(hosts).sort((left, right) => Object.values(right[1]).reduce((a, b) => a + b, 0) - Object.values(left[1]).reduce((a, b) => a + b, 0)).map(([host, values]) => ({ label: endpointNames[host] || host, values })), totalSeries: [{ label: "Requests", values: dailyTotal }, { label: "Approx. visitors", values: dailyVisitors }] };
  }
  function trafficMarkup(items, range, compact = false) {
    const model = trafficModel(items, range);
    const cards = `<div class="cards metric-cards"><article class="card"><span>Requests</span><strong>${number(model.totalRequests)}</strong><small>Across public endpoints</small></article><article class="card"><span>Approx. visitors</span><strong>${number(model.totalVisitors)}</strong><small>Cookie-free daily deduplication</small></article><article class="card"><span>Active endpoints</span><strong>${number(Object.keys(model.totals).length)}</strong><small>With requests in this period</small></article></div>`;
    const total = lineChart(model.totalSeries, model.days, `Daily traffic · ${range} days`);
    return compact ? `${cards}${total}` : `${cards}<div class="chart-grid-layout">${total}${distributionChart(model.totals, "Requests by endpoint")}</div>${lineChart(model.endpointSeries, model.days, "Endpoint request trends")}`;
  }
  async function overview() {
    const data = await eafAuth.json("/dashboard/api/overview");
    content.innerHTML = document.getElementById("overview-template").innerHTML;
    const finished = (data.payments || []).find((item) => item.status === "finished")?.count || 0;
    const authFailures = Object.values(data.security || {}).reduce((sum, value) => sum + Number(value || 0), 0);
    document.getElementById("summary").innerHTML = [["Completed checkouts", finished], ["Valid licenses", data.licenses?.valid || 0], ["Error reports", data.errors || 0], ["School feedback", data.feedback || 0], ["Authentication failures · 24h", authFailures]].map(([label, value]) => `<article class="card"><span>${label}</span><strong>${number(value)}</strong></article>`).join("");
    document.getElementById("services").innerHTML = (data.services || []).map((service) => `<article class="service"><span class="dot ${service.ActiveState === "active" ? "ok" : "bad"}"></span><div><strong>${esc(service.name)}</strong><small>${esc(service.SubState || service.ActiveState)}</small></div>${service.name !== "dashboard" ? `<button data-restart="${esc(service.name)}">Restart</button>` : ""}</article>`).join("");
    document.getElementById("usage").innerHTML = trafficMarkup(data.usage || [], 30, true);
    document.querySelectorAll("[data-restart]").forEach((button) => button.addEventListener("click", async () => { try { await action("restart_service", { service: button.dataset.restart }, button.dataset.restart); await overview(); } catch (error) { alert(error.message); } }));
    document.getElementById("backup").addEventListener("click", async () => { try { const result = await action("backup", {}, "backup"); alert(`Backup created: ${result.result.backup}`); } catch (error) { alert(error.message); } });
  }
  async function traffic(range = 30) {
    const data = await eafAuth.json(`/dashboard/api/usage?days=${range}`);
    content.innerHTML = `<section><div class="section-head"><div><p class="eyebrow">PRIVACY-PRESERVING ANALYTICS</p><h2>Traffic</h2></div><label class="range-control">Period<select id="traffic-range"><option value="7">7 days</option><option value="30">30 days</option><option value="90">90 days</option><option value="365">1 year</option></select></label></div><p class="section-note">Request totals come from aggregate Caddy counters. Approximate visitors use daily, non-reversible duplicate checks without cookies or persistent IP addresses.</p>${trafficMarkup(data.items || [], range)}</section>`;
    document.getElementById("traffic-range").value = String(range);
    document.getElementById("traffic-range").addEventListener("change", (event) => traffic(Number(event.target.value)).catch(showError));
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
    return `<div class="report-grid">${items.map((item) => `<article class="report-card"><header><div><span class="report-type">${isError ? "Error report" : "Feedback"}</span><time>${esc(dateTime(item.created || item.created_at))}</time></div><button class="danger" data-delete="${isError ? "delete_error" : "delete_feedback"}" data-id="${esc(item.id)}">Delete</button></header><pre>${esc(item.message)}</pre><footer><code>${esc(item.id)}</code>${isError && item.updated && item.updated !== item.created ? `<span>Updated ${esc(dateTime(item.updated))}</span>` : ""}</footer></article>`).join("")}</div>`;
  }
  async function records(kind) {
    const data = await eafAuth.json(`/dashboard/api/${kind}`), deletion = { errors: "delete_error", feedback: "delete_feedback", checklist: "delete_checklist" }[kind];
    const body = ["errors", "feedback"].includes(kind) ? reportCards(data.items || [], kind) : table(data.items || [], deletion ? `<button class="danger" data-delete="${deletion}" data-id="{id}">Delete</button>` : "");
    content.innerHTML = `<section><div class="section-head"><div><p class="eyebrow">RECORDS</p><h2>${esc(pretty(kind))}</h2></div></div>${body}</section>`;
    document.querySelectorAll("[data-delete]").forEach((button) => button.addEventListener("click", async () => { try { await action(button.dataset.delete, { id: button.dataset.id }, button.dataset.id); await records(kind); } catch (error) { alert(error.message); } }));
  }
  function showError(error) { content.innerHTML = `<p class="error">${esc(error.message)}</p>`; }
  document.querySelectorAll("button[data-view]").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll(".nav").forEach((item) => item.classList.remove("active")); button.classList.add("active");
    const views = { overview, traffic, revenue }; (views[button.dataset.view] || (() => records(button.dataset.view)))().catch(showError);
  }));
  document.getElementById("logout").addEventListener("click", async () => { await eafAuth.json("/dashboard/api/auth/logout", { method: "POST", body: "{}" }); location.href = "/dashboard/login"; });
  overview().catch(showError);
})();
