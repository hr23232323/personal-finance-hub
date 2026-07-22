// Tiny vanilla dashboard. No frameworks, no CDN — fully local.
const $ = (s) => document.querySelector(s);
const money = (n) => (n < 0 ? "-" : "") + "$" + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

// ── charts (ECharts, themed to the Statement look) ──────────────────────────
const CHART = {
  ink: "#1C1E1A", inkSoft: "#5C6056", green: "#1E5C40", oxblood: "#9E3B2A",
  rule: "#D4D5C9", paper: "#ECEDE6", surface: "#FBFAF5",
  sans: 'system-ui, -apple-system, sans-serif',
  mono: 'ui-monospace, "SF Mono", Menlo, monospace',
  palette: ["#1E5C40", "#9E3B2A", "#A9781F", "#3E6B8B", "#6B4E7A", "#7A6A4F", "#4C7A5B", "#9a9c90"],
};
const charts = {};
function chart(id) {
  if (!charts[id]) charts[id] = echarts.init(document.getElementById(id), null, { renderer: "svg" });
  return charts[id];
}
window.addEventListener("resize", () => {
  Object.values(charts).forEach((c) => c.resize());
  miniCharts.forEach((c) => c.resize());
});

function renderDonut(cats) {
  chart("chartCategory").setOption({
    color: CHART.palette,
    tooltip: { trigger: "item", formatter: (p) => `${p.name}<br>${money(-p.value)} · ${p.percent}%` },
    legend: { type: "scroll", bottom: 0, textStyle: { color: CHART.inkSoft, fontFamily: CHART.sans } },
    series: [{
      type: "pie", radius: ["52%", "78%"], center: ["50%", "43%"],
      data: cats.map((c) => ({ name: c.category, value: c.spent })),
      label: { show: false }, labelLine: { show: false },
      itemStyle: { borderColor: CHART.surface, borderWidth: 2 },
    }],
  }, true);
}

function renderSankey(flow) {
  chart("chartFlow").setOption({
    tooltip: {
      trigger: "item",
      formatter: (p) => p.dataType === "edge"
        ? `${p.data.source} → ${p.data.target}<br>${money(p.data.value)}`
        : p.name,
    },
    series: [{
      type: "sankey", data: flow.nodes, links: flow.links,
      emphasis: { focus: "adjacency" }, nodeGap: 9, nodeWidth: 11,
      itemStyle: { color: CHART.green, borderColor: CHART.surface },
      lineStyle: { color: "gradient", opacity: 0.38, curveness: 0.5 },
      label: { color: CHART.ink, fontFamily: CHART.sans, fontSize: 11 },
    }],
  }, true);
}

function renderHeatmap(daily) {
  const el = document.getElementById("chartHeatmap");
  if (!daily.length) { if (charts.chartHeatmap) charts.chartHeatmap.clear(); return; }
  const first = daily[0][0], last = daily[daily.length - 1][0];
  const weeks = Math.ceil((new Date(last) - new Date(first)) / (7 * 864e5)) + 2;
  el.style.minWidth = Math.max(560, weeks * 17 + 60) + "px";
  const c = chart("chartHeatmap");
  c.resize();
  c.setOption({
    tooltip: { formatter: (p) => `${p.data[0]} · ${money(-p.data[1])}` },
    visualMap: {
      min: 0, max: Math.max(...daily.map((d) => d[1])), orient: "horizontal",
      left: "center", bottom: 0, inRange: { color: ["#E4E6DC", "#8FB39C", "#1E5C40"] },
      textStyle: { color: CHART.inkSoft, fontFamily: CHART.mono, fontSize: 10 },
    },
    calendar: {
      range: [first, last], cellSize: ["auto", 14], top: 12, bottom: 46, left: 26, right: 14,
      itemStyle: { color: CHART.surface, borderColor: CHART.paper, borderWidth: 2 },
      splitLine: { lineStyle: { color: CHART.rule } },
      dayLabel: { color: CHART.inkSoft, fontFamily: CHART.sans, fontSize: 9, firstDay: 1 },
      monthLabel: { color: CHART.inkSoft, fontFamily: CHART.sans, fontSize: 10 },
      yearLabel: { show: false },
    },
    series: [{ type: "heatmap", coordinateSystem: "calendar", data: daily }],
  }, true);
}

function renderTrend(ot) {
  chart("chartTrend").setOption({
    color: CHART.palette,
    tooltip: { trigger: "axis" },
    legend: { type: "scroll", top: 0, textStyle: { color: CHART.inkSoft, fontFamily: CHART.sans } },
    grid: { left: 56, right: 16, top: 38, bottom: 26 },
    xAxis: {
      type: "category", data: ot.months, boundaryGap: false,
      axisLabel: { color: CHART.inkSoft, fontFamily: CHART.mono, fontSize: 10 },
      axisLine: { lineStyle: { color: CHART.rule } },
    },
    yAxis: {
      type: "value", splitLine: { lineStyle: { color: CHART.rule } },
      axisLabel: { color: CHART.inkSoft, fontFamily: CHART.mono, fontSize: 10, formatter: (v) => "$" + v },
    },
    series: ot.series.map((s) => ({
      name: s.name, type: "line", stack: "total", smooth: true, symbol: "none",
      areaStyle: { opacity: 0.5 }, data: s.data,
    })),
  }, true);
}

const TONE = { watch: "#9E3B2A", positive: "#1E5C40", neutral: "#5C6056" };
const TONE_FADE = { watch: "rgba(158,59,42,0.13)", positive: "rgba(30,92,64,0.13)", neutral: "rgba(92,96,86,0.11)" };
const miniCharts = [];

function renderMini(el, spec, tone) {
  const c = echarts.init(el, null, { renderer: "svg" });
  miniCharts.push(c);
  const base = { animation: false, tooltip: { trigger: "item" } };
  if (spec.kind === "trend") {
    const color = TONE[spec.tone] || TONE.neutral;
    c.setOption({ ...base, tooltip: { trigger: "axis", formatter: (p) => `${p[0].axisValue} · ${money(-p[0].data)}` },
      grid: { left: 2, right: 2, top: 6, bottom: 2 },
      xAxis: { type: "category", data: spec.months, show: false, boundaryGap: false },
      yAxis: { type: "value", show: false, min: 0 },
      series: [{ type: "line", data: spec.values, smooth: true, symbol: "none",
        lineStyle: { color, width: 1.8 }, areaStyle: { color: TONE_FADE[spec.tone] || TONE_FADE.neutral } }] });
  } else if (spec.kind === "months") {
    c.setOption({ ...base, tooltip: { trigger: "axis", formatter: (p) => `${p[0].axisValue} · ${money(-p[0].data)}` },
      grid: { left: 2, right: 2, top: 6, bottom: 2 },
      xAxis: { type: "category", data: spec.months, show: false },
      yAxis: { type: "value", show: false },
      series: [{ type: "bar", barCategoryGap: "28%",
        data: spec.values.map((v, i) => ({ value: v, itemStyle: { color: i === spec.highlight ? TONE.watch : "#c9cbbf" } })) }] });
  } else if (spec.kind === "hbars") {
    const items = spec.items.slice().reverse();
    c.setOption({ ...base, tooltip: { trigger: "item", formatter: (p) => `${p.name} · ${money(-p.value)}` },
      grid: { left: 74, right: 8, top: 2, bottom: 2 },
      xAxis: { type: "value", show: false },
      yAxis: { type: "category", data: items.map((i) => i.label),
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: CHART.inkSoft, fontFamily: CHART.sans, fontSize: 9.5, width: 66, overflow: "truncate" } },
      series: [{ type: "bar", barWidth: 8, data: items.map((i) => i.value), itemStyle: { color: TONE[tone] || TONE.neutral } }] });
  } else if (spec.kind === "compare") {
    c.setOption({ ...base, tooltip: { trigger: "item" },
      grid: { left: 4, right: 4, top: 8, bottom: 18 },
      xAxis: { type: "category", data: spec.items.map((i) => i.label),
        axisLine: { lineStyle: { color: CHART.rule } }, axisTick: { show: false },
        axisLabel: { color: CHART.inkSoft, fontFamily: CHART.sans, fontSize: 10 } },
      yAxis: { type: "value", show: false },
      series: [{ type: "bar", barWidth: 22,
        data: spec.items.map((i, idx) => ({ value: i.value, itemStyle: { color: idx === 0 ? "#c9cbbf" : (TONE[tone] || TONE.neutral) } })) }] });
  }
}

function renderDiscoveries(list) {
  miniCharts.forEach((c) => c.dispose());
  miniCharts.length = 0;
  if (!list.length) {
    $("#discoveries").innerHTML =
      '<div class="empty">Not enough history yet — import more transactions to surface patterns.</div>';
    return;
  }
  $("#discoveries").innerHTML = list.map((d, i) => {
    const ev = d.evidence.length ? `
      <details class="disc-ev">
        <summary>See ${d.evidence_count} transaction${d.evidence_count === 1 ? "" : "s"}</summary>
        <table class="txtable"><tbody>
          ${d.evidence.map((t) => `<tr>
            <td>${t.date}</td><td>${t.payee || "—"}</td>
            <td><span class="cat">${t.category}</span></td>
            <td class="r ${t.amount >= 0 ? "amt-pos" : "amt-neg"}">${money(t.amount)}</td></tr>`).join("")}
        </tbody></table>
      </details>` : "";
    const viz = d.chart ? `<div class="disc-viz" id="mini-${i}"></div>` : "";
    return `<article class="disc disc-${d.tone}">
      <div class="disc-body"><h4>${d.title}</h4>
        <p class="disc-sum">${d.summary}</p>${ev}</div>
      ${viz}
    </article>`;
  }).join("");
  list.forEach((d, i) => {
    const el = document.getElementById(`mini-${i}`);
    if (el && d.chart) renderMini(el, d.chart, d.tone);
  });
}

const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

async function loadCoach() {
  const el = $("#coach");
  try {
    const c = await api("/api/coach");
    if (!c.available) {
      el.className = "coach note";
      el.innerHTML = "Add an <code>LLM_API_KEY</code> to your <code>.env</code> for a written read of "
        + "these findings. Everything below works without it.";
      return;
    }
    if (!c.read) {
      el.className = "coach note";
      el.textContent = "Couldn't reach the model" + (c.error ? `: ${c.error}` : ".");
      return;
    }
    el.className = "coach";
    el.innerHTML = c.read.split(/\n\s*\n/).map((p) => `<p>${esc(p.trim())}</p>`).join("")
      + `<p class="coach-note">Written by ${esc(c.model)} from the computed findings below — it
         narrates the numbers, it never invents them.</p>`;
  } catch (e) {
    el.className = "coach note";
    el.textContent = e.message;
  }
}

let insightsLoaded = false;
async function loadInsights() {
  const [disc, ins, ch] = await Promise.all([
    api("/api/discoveries"), api("/api/insights"), api("/api/charts"),
  ]);
  renderDiscoveries(disc);
  const activeMo = ins.recurring.filter((r) => r.active).reduce((s, r) => s + r.monthly_cost, 0);
  $("#recurringTotal").textContent = money(-activeMo) + "/mo active";
  $("#recurring").innerHTML = ins.recurring.length
    ? ins.recurring.map((r) => `
      <div class="rec ${r.active ? "" : "inactive"}">
        <div class="rec-main"><span class="rec-name">${r.payee}</span>
          <span class="cat">${r.category}</span>${r.active ? "" : '<span class="rec-flag">inactive</span>'}</div>
        <div class="rec-meta">${r.cadence} · ${r.occurrences}× · last ${r.last_charge}</div>
        <div class="rec-amt">${money(-r.monthly_cost)}<span>/mo</span></div>
      </div>`).join("")
    : '<div class="empty">No recurring charges detected yet.</div>';
  renderTrend(ch.over_time);
  loadCoach();  // slower LLM call — don't block the deterministic feed
}

// ── date range ───────────────────────────────────────────────────────────────
function range() {
  const days = parseInt($("#range").value, 10);
  if (!days) return { start: null, end: null };
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - days);
  const iso = (d) => d.toISOString().slice(0, 10);
  return { start: iso(start), end: iso(end) };
}
function qs(obj) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) if (v != null) p.set(k, v);
  return p.toString();
}

// ── tabs ─────────────────────────────────────────────────────────────────────
document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    document.querySelectorAll(".panel").forEach((p) => p.classList.add("hidden"));
    $("#tab-" + t.dataset.tab).classList.remove("hidden");
    const tab = t.dataset.tab;
    // Period only affects the Statement and Ledger views.
    $("#periodbar").style.display = (tab === "dashboard" || tab === "transactions") ? "flex" : "none";
    if (tab === "insights" && !insightsLoaded) {
      insightsLoaded = true;
      loadInsights().catch(console.error);
    }
    // ECharts can't size a hidden container; resize once this tab is visible.
    setTimeout(() => Object.values(charts).forEach((c) => c.resize()), 0);
  })
);
$("#range").addEventListener("change", refresh);

// ── status ───────────────────────────────────────────────────────────────────
async function loadStatus() {
  const s = await api("/api/status");
  $("#status").innerHTML =
    `<span class="dot ${s.llm_configured ? "on" : "off"}"></span>Advisor ${s.llm_configured ? "ready (" + s.llm_model + ")" : "off — add LLM key"}<br>` +
    `<span class="dot ${s.simplefin_connected ? "on" : "off"}"></span>SimpleFIN ${s.simplefin_connected ? "connected" : "not connected"}`;
  $("#chatPrivacy").textContent = s.llm_configured
    ? "Heads up: your question and the relevant transactions are sent to " + s.llm_model + " to answer."
    : "The advisor is off. Add LLM_API_KEY to your .env to enable it.";
  $("#sfStatus").innerHTML = s.simplefin_connected
    ? '<span class="pill green">connected</span>'
    : '<span class="muted">Not connected yet — paste a setup token below.</span>';
}

// ── dashboard ────────────────────────────────────────────────────────────────
function bar(name, value, max, cls) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return `<div class="bar-row"><span class="name" title="${name}">${name}</span>
    <div class="bar-track"><div class="bar-fill ${cls || ""}" style="width:${pct}%"></div></div>
    <span class="amt">${money(value)}</span></div>`;
}

async function loadDashboard() {
  const { start, end } = range();
  const d = await api("/api/summary?" + qs({ start, end }));
  const s = d.summary;
  const periodLabel = $("#range").selectedOptions[0].textContent;

  if (!s.count) {
    $("#summary").innerHTML =
      `<div class="empty-hero">No transactions yet. Open <b>Import &amp; sync</b> to add your first account.</div>`;
    $("#topMerchants").innerHTML = "";
    ["chartCategory", "chartFlow", "chartHeatmap"].forEach((id) => charts[id] && charts[id].clear());
    return;
  }

  const read = s.income > 0
    ? `You kept ${money(s.net)} of the ${money(s.income)} that came in — ${money(-s.spending)} went out.`
    : `${money(-s.spending)} went out this period, with nothing recorded coming in.`;
  $("#summary").innerHTML = `
    <p class="eyebrow">Statement summary · ${periodLabel}</p>
    <div class="net-fig ${s.net >= 0 ? "pos" : "neg"}">${money(s.net)}</div>
    <div class="net-label">Net for the period</div>
    <p class="read">${read}</p>
    <div class="tallies">
      <div class="tally"><span class="t-label">Money in</span><span class="t-val pos">${money(s.income)}</span></div>
      <div class="tally"><span class="t-label">Money out</span><span class="t-val neg">${money(s.spending)}</span></div>
      <div class="tally"><span class="t-label">Transactions</span><span class="t-val">${s.count}</span></div>
    </div>`;

  renderDonut(d.by_category);
  const c = await api("/api/charts?" + qs({ start, end }));
  renderSankey(c.flow);
  renderHeatmap(c.daily);

  const mer = d.top_merchants;
  const maxMer = Math.max(1, ...mer.map((m) => m.spent));
  $("#topMerchants").innerHTML = mer.length
    ? mer.map((m) => bar(m.payee + ` (${m.visits}×)`, m.spent, maxMer, "spend")).join("")
    : '<div class="empty">No merchants yet.</div>';
}

// ── transactions ─────────────────────────────────────────────────────────────
let txTimer;
$("#txSearch").addEventListener("input", () => {
  clearTimeout(txTimer);
  txTimer = setTimeout(loadTransactions, 250);
});
async function loadTransactions() {
  const { start, end } = range();
  const rows = await api("/api/transactions?" + qs({ start, end, search: $("#txSearch").value, limit: 300 }));
  $("#txbody").innerHTML = rows.length
    ? rows.map((t) =>
        `<tr><td>${t.date}</td><td>${t.payee || "—"}</td><td><span class="cat">${t.category}</span></td>
         <td>${t.account}</td><td class="r ${t.amount >= 0 ? "amt-pos" : "amt-neg"}">${money(t.amount)}</td></tr>`
      ).join("")
    : '<tr><td colspan="5" class="empty">No transactions. Import a file or sync to get started.</td></tr>';
}

// ── connect & import ─────────────────────────────────────────────────────────
async function loadAccounts() {
  const accts = await api("/api/accounts");
  $("#importAccount").innerHTML = accts.length
    ? accts.map((a) => `<option value="${a.id}">${a.name} (${a.type})</option>`).join("")
    : '<option value="">— create an account first —</option>';
}
$("#createAcct").addEventListener("click", async () => {
  const name = $("#newAcctName").value.trim();
  if (!name) return;
  await api("/api/accounts", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, type: $("#newAcctType").value }),
  });
  $("#newAcctName").value = "";
  await loadAccounts();
});
$("#importBtn").addEventListener("click", async () => {
  const f = $("#importFile").files[0];
  const acct = $("#importAccount").value;
  const out = $("#importResult");
  if (!f || !acct) { out.className = "result err"; out.textContent = "Pick an account and a file."; return; }
  out.className = "result"; out.textContent = "Importing…";
  const fd = new FormData();
  fd.append("file", f);
  fd.append("account_id", acct);
  try {
    const r = await api("/api/import", { method: "POST", body: fd });
    out.className = "result ok";
    out.textContent = `Imported ${r.inserted} new (${r.skipped_duplicates} duplicates skipped).`;
    refresh();
  } catch (e) { out.className = "result err"; out.textContent = e.message; }
});

$("#sfConnect").addEventListener("click", async () => {
  const token = $("#sfToken").value.trim();
  const out = $("#sfResult");
  if (!token) return;
  out.className = "result"; out.textContent = "Connecting…";
  try {
    await api("/api/simplefin/connect", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ setup_token: token }),
    });
    $("#sfToken").value = "";
    out.className = "result ok"; out.textContent = "Connected. Hit “Sync now”.";
    loadStatus();
  } catch (e) { out.className = "result err"; out.textContent = e.message; }
});
$("#sfSync").addEventListener("click", async () => {
  const out = $("#sfResult");
  out.className = "result"; out.textContent = "Syncing…";
  try {
    const r = await api("/api/simplefin/sync", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    out.className = "result ok"; out.textContent = `Synced ${r.accounts} account(s), ${r.inserted} new transactions.`;
    loadAccounts(); refresh();
  } catch (e) { out.className = "result err"; out.textContent = e.message; }
});

// ── chat ─────────────────────────────────────────────────────────────────────
const chatHistory = [];
function addMsg(text, who) {
  const el = document.createElement("div");
  el.className = "msg " + who;
  el.textContent = text;
  $("#chatlog").appendChild(el);
  $("#chatlog").scrollTop = $("#chatlog").scrollHeight;
  return el;
}
async function sendChat() {
  const text = $("#chatInput").value.trim();
  if (!text) return;
  $("#chatInput").value = "";
  addMsg(text, "user");
  const thinking = addMsg("Thinking…", "bot thinking");
  try {
    const r = await api("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history: chatHistory }),
    });
    thinking.remove();
    addMsg(r.reply, "bot");
    chatHistory.push({ role: "user", content: text }, { role: "assistant", content: r.reply });
  } catch (e) {
    thinking.className = "msg bot"; thinking.textContent = "⚠ " + e.message;
  }
}
$("#chatSend").addEventListener("click", sendChat);
$("#chatInput").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });

// ── refresh ──────────────────────────────────────────────────────────────────
function refresh() {
  loadDashboard().catch((e) => console.error(e));
  loadTransactions().catch((e) => console.error(e));
}
loadStatus();
loadAccounts();
refresh();
