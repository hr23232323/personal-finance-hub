// Tiny vanilla dashboard. No frameworks, no CDN — fully local.
const $ = (s) => document.querySelector(s);
const money = (n) => (n < 0 ? "-" : "") + "$" + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

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
    // Period only affects the Statement and Ledger views.
    const usesPeriod = t.dataset.tab === "dashboard" || t.dataset.tab === "transactions";
    $("#periodbar").style.display = usesPeriod ? "flex" : "none";
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
    $("#byCategory").innerHTML = $("#byMonth").innerHTML = $("#topMerchants").innerHTML = "";
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

  const cats = d.by_category;
  const maxCat = Math.max(1, ...cats.map((c) => c.spent));
  $("#byCategory").innerHTML = cats.length
    ? cats.map((c) => bar(c.category, c.spent, maxCat, "spend")).join("")
    : '<div class="empty">No spending in this period.</div>';

  const months = d.by_month;
  const maxM = Math.max(1, ...months.map((m) => Math.max(m.income, m.spending)));
  $("#byMonth").innerHTML = months.length
    ? months.map((m) =>
        `<div class="bar-row month-row"><span class="name">${m.month}</span>
          <div class="month-pair">
            <div class="bar-track"><div class="bar-fill income" style="width:${Math.round((m.income / maxM) * 100)}%"></div></div>
            <div class="bar-track"><div class="bar-fill spend" style="width:${Math.round((m.spending / maxM) * 100)}%"></div></div>
          </div></div>`
      ).join("")
    : '<div class="empty">No data yet.</div>';

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
