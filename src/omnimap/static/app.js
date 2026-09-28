// Omnichannel Event Mapper UI. Plain JS, no build step.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = async (path, body) => {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
};
const hash = new URLSearchParams(location.hash.slice(1));
const S = { state: null, order: hash.get("order"), scope: hash.get("scope") || "order", tz: hash.get("tz") || "utc", samples: [], ruleFile: "shopify" };
const SHIP_COLORS = ["var(--s0)", "var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"];
const pill = (ch) => `<span class="pill ${esc(ch || "none")}">${esc((S.state?.channels || {})[ch] || ch || "unknown")}</span>`;

// ---------- time formatting ----------
function fmt(inst, mode = S.tz) {
  if (!inst) return "—";
  const d = new Date(inst.utc);
  if (mode === "utc") return inst.utc.replace("T", " ").replace("Z", " UTC");
  if (mode === "local") return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  return inst.local.replace("T", " ") + (inst.source.startsWith("epoch") ? " (epoch)" : "");
}
const origLabel = (inst) => (inst ? (inst.source.startsWith("epoch") ? `sent as ${inst.source}` : `sent as ${inst.local}`) : "");

// ---------- tabs ----------
function showTab(name) {
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  if (name === "coverage") loadCoverage();
  if (name === "rules") loadRules();
}
$$("#tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));

// ---------- timeline tab ----------
async function refresh() {
  S.state = await api("/api/state");
  const { orders, sources, warnings } = S.state;
  $("#order-count").textContent = orders.length;
  $("#source-count").textContent = sources.length;
  if (!S.order || !orders.find((o) => o.key === S.order)) S.order = orders[0]?.key || null;
  $("#orders").innerHTML = orders.map((o) => `
    <div class="order-card ${o.key === S.order ? "active" : ""}" data-key="${esc(o.key)}">
      <div class="t"><span class="name">${esc(o.display_name)}</span>${pill(o.channel)}</div>
      <div class="meta">${o.has_header ? esc(fmt(o.placed_at, S.tz === "orig" ? "orig" : S.tz)) : '<span class="stub">order never received</span>'}</div>
      <div class="t meta"><span>${o.shipments} pkg · ${o.events} scans</span>
        ${o.warnings ? `<span class="badge err">${o.warnings} ⚠</span>` : '<span class="badge ok">clean</span>'}</div>
    </div>`).join("") || '<div class="empty">No orders. Paste a payload or reload the demo.</div>';
  $$(".order-card").forEach((c) => (c.onclick = () => selectOrder(c.dataset.key)));
  $("#sources").innerHTML = sources.map((s) => `<li>${pill(s.channel)} <span class="x">${esc(s.label.replace(/^(Shopify|Amazon|TikTok Shop) /, ""))}</span>
      ${s.records ? "" : s.errors.length ? ' <span class="stub">unmapped</span>' : ' <span class="hint">(informational)</span>'}</li>`).join("");
  renderWarnings();
  await renderOrder();
}

function selectOrder(key) {
  S.order = key;
  $$(".order-card").forEach((c) => c.classList.toggle("active", c.dataset.key === key));
  renderWarnings();
  renderOrder();
}

function renderWarnings() {
  const all = S.state.warnings;
  const list = S.scope === "order" ? all.filter((w) => w.order_key === S.order) : all;
  $("#warn-count").textContent = all.length;
  $$("#warn-scope button").forEach((b) => b.classList.toggle("active", b.dataset.scope === S.scope));
  $("#warnings").innerHTML = list.map(warnCard).join("") ||
    `<div class="allclear">✓ No warnings ${S.scope === "order" ? "for this order" : ""}.</div>`;
  $$("#warnings .warn").forEach((el) => (el.onclick = () => el.dataset.key && selectOrder(el.dataset.key)));
}
const warnCard = (w) => `<div class="warn ${esc(w.severity)}" data-key="${esc(w.order_key || "")}">
    <div class="r">${esc(w.title)} <span class="tag">${esc(w.rule)}</span></div>
    <div class="m">${esc(w.message)}</div>
    ${w.order_key ? `<div class="o">${esc(w.order_key)}${w.shipment_id ? " · package " + esc(w.shipment_id) : ""}</div>` : ""}</div>`;
$$("#warn-scope button").forEach((b) => (b.onclick = () => { S.scope = b.dataset.scope; renderWarnings(); }));

async function renderOrder() {
  const view = $("#order-view");
  if (!S.order) { view.innerHTML = '<div class="empty">No order selected.</div>'; return; }
  const { order: o, timeline } = await api("/api/order/" + encodeURIComponent(S.order));
  const color = {};
  o.shipments.forEach((s, i) => (color[s.shipment_id] = SHIP_COLORS[i % SHIP_COLORS.length]));
  const shipName = (id) => { const s = o.shipments.find((x) => x.shipment_id === id); return s ? (s.name || s.shipment_id) : id; };
  const c = o.customer || {};
  view.innerHTML = `
    <div class="ohead">
      <div><h3>${esc(o.display_name || o.external_id)}</h3>
        <div class="hint">${pill(o.channel)} order <code>${esc(o.external_id)}</code>
        ${o.has_header ? "" : ' · <span class="stub">order payload never received — only its shipments</span>'}</div></div>
      <div class="hint">status<br><b>${esc(o.status || "—")}</b></div>
    </div>
    <div class="kv">
      <div><b>Placed</b>${esc(fmt(o.placed_at))}</div>
      <div><b>Total</b>${esc(o.total ?? "—")} ${esc(o.currency || "")}</div>
      <div><b>Ship-to region</b>${esc([c.region, c.country].filter(Boolean).join(", ") || "—")} ${c.postal_prefix ? `<span class="raw">${esc(c.postal_prefix)}…</span>` : ""}</div>
      <div><b>Packages / scans</b>${o.shipments.length} / ${o.shipments.reduce((n, s) => n + s.events.length, 0) + o.unassigned_events.length}</div>
    </div>
    <table><thead><tr><th>SKU</th><th>Item</th><th class="num">Ordered</th><th class="num">Shipped</th><th class="num">Price</th></tr></thead><tbody>
      ${o.lines.map((l) => `<tr><td><code>${esc(l.sku || "—")}</code></td><td>${esc(l.title || "")}</td>
        <td class="num">${esc(l.quantity ?? "—")}</td>
        <td class="num ${l.quantity != null && l.shipped > l.quantity ? "over" : ""}">${l.shipped}</td>
        <td class="num">${esc(l.unit_price ?? (l.extended_price ? l.extended_price + " (line)" : "—"))} ${esc(l.currency || "")}</td></tr>`).join("")
        || '<tr><td colspan="5" class="hint">No order lines received.</td></tr>'}
    </tbody></table>
    <div class="ships">${o.shipments.map((s) => `
      <div class="ship" style="--c:${color[s.shipment_id]}">
        <div class="h">${esc(s.name || s.shipment_id)} · ${esc(s.carrier_name || "Unknown carrier")}</div>
        <div class="raw">carrier sent as "${esc(s.carrier_raw || "")}"${s.carrier ? " → " + esc(s.carrier) : " → not recognised"}</div>
        <div class="d">${s.tracking_number ? "tracking <code>" + esc(s.tracking_number) + "</code>" : '<span class="missing">no tracking number</span>'}</div>
        <div class="d">${s.lines.map((l) => `${esc(l.quantity)}× ${esc(l.sku || l.line_id)}`).join(", ")}</div>
      </div>`).join("")}</div>
    <div class="tzbar"><h2 style="margin:0">Unified timeline</h2>
      <div class="seg" id="tz">
        <button data-tz="utc">UTC</button><button data-tz="local">My time zone</button><button data-tz="orig">As sent</button>
      </div></div>
    <p class="hint" style="margin-top:0">Sorted by absolute instant after normalising every channel's timestamp
      (ISO offsets, "Z", epoch seconds, epoch ms) — not by the text of the timestamp.</p>
    <ol class="tl">${timeline.map((e) => `
      <li class="${e.type}" style="--c:${e.shipment_id ? color[e.shipment_id] : "#98a2b3"}">
        <div class="when">${esc(fmt(e.at))}<span class="orig">${S.tz === "orig" ? "" : esc(origLabel(e.at))}</span></div>
        <div class="what">${esc(e.title)}${e.status ? `<span class="st ${esc(e.status)}">${esc(e.status.replaceAll("_", " "))}</span>` : ""}
          ${e.shipment_id && e.type === "tracking" ? `<span class="tag">${esc(shipName(e.shipment_id))}</span>` : ""}
          ${e.channel && e.channel !== o.channel ? `<span class="tag">via ${esc(S.state.channels[e.channel] || e.channel)}</span>` : ""}</div>
        ${e.detail ? `<div class="det">${esc(e.detail)}</div>` : ""}
      </li>`).join("")}</ol>`;
  $$("#tz button").forEach((b) => {
    b.classList.toggle("active", b.dataset.tz === S.tz);
    b.onclick = () => { S.tz = b.dataset.tz; renderOrder(); };
  });
}

$("#btn-demo").onclick = async () => { await api("/api/reset", { demo: true }); S.order = null; refresh(); };
$("#btn-clear").onclick = async () => { await api("/api/reset", {}); S.order = null; refresh(); };

// ---------- try tab ----------
async function initTry() {
  S.samples = await api("/api/samples");
  $("#sample").innerHTML += S.samples.map((s, i) => `<option value="${i}">${esc(s.label)}</option>`).join("");
  $("#channel").innerHTML += Object.entries(S.state.channels).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("");
  $("#sample").onchange = () => {
    const s = S.samples[$("#sample").value];
    if (!s) return;
    $("#payload").value = s.text;
    $("#context").value = Object.keys(s.context || {}).length ? JSON.stringify(s.context) : "";
    preview();
  };
  $("#file").onchange = async () => {
    const f = $("#file").files[0];
    if (!f) return;
    $("#payload").value = await f.text();
    preview();
  };
  let t;
  $("#payload").oninput = $("#context").oninput = () => { clearTimeout(t); t = setTimeout(preview, 450); };
  $("#channel").onchange = preview;
  $("#btn-preview").onclick = preview;
  $("#btn-ingest").onclick = () => preview(true);
}

async function preview(commit = false) {
  const text = $("#payload").value;
  if (!text.trim()) return;
  const out = $("#try-result");
  try {
    const r = await api(commit ? "/api/ingest" : "/api/preview", { text, context: $("#context").value, channel: $("#channel").value });
    const res = r.result;
    out.classList.remove("empty");
    out.innerHTML = `
      <div class="detected">${pill(res.channel)} <b>${esc(res.payload_type || "not recognised")}</b>
        <span class="hint">→ ${res.records.length} canonical record(s)${r.committed ? " · added to workspace" : ""}</span></div>
      ${res.errors.length ? `<div class="errs">${res.errors.map(esc).join("<br>")}</div>` : ""}
      <h2 class="mt">Warnings for affected orders</h2>
      ${r.warnings.map(warnCard).join("") || '<div class="allclear">✓ No warnings.</div>'}
      <h2 class="mt">Canonical records</h2>
      ${res.records.map((rec, i) => `<details ${i < 2 ? "open" : ""}><summary>${esc(rec.kind)} <span class="hint">${esc(rec.data.external_id || rec.data.shipment_id || rec.data.tracking_number || rec.data.order_ref || "")}</span></summary>
        <pre class="code">${esc(JSON.stringify(rec.data, null, 2))}</pre></details>`).join("")}`;
    $("#try-status").textContent = commit ? "Added. See the Timeline tab." : "";
    if (commit) { if (r.orders[0]) S.order = r.orders[0]; refresh(); }
  } catch (e) {
    out.classList.remove("empty");
    out.innerHTML = `<div class="errs">${esc(e.message)}</div>`;
  }
}

// ---------- coverage ----------
async function loadCoverage() {
  const cov = await api("/api/coverage");
  let kind = "";
  const rows = cov.rows.map((r) => {
    const head = r.kind !== kind ? `<tr class="kind"><td colspan="${2 + cov.channels.length}">${esc(r.kind)}</td></tr>` : "";
    kind = r.kind;
    return head + `<tr><td><code>${esc(r.field)}</code></td><td class="desc">${esc(r.description)}</td>` +
      cov.channels.map((ch) => {
        const c = r.channels[ch];
        if (!c.declared) return '<td class="c no">—</td>';
        const rate = c.fill_rate;
        const cls = rate === null || rate === 1 ? "yes" : "part";
        return `<td class="c ${cls}" title="${esc(c.payload_types.join("\n"))}">✓ ${rate === null ? "" : Math.round(rate * 100) + "%"}</td>`;
      }).join("") + "</tr>";
  }).join("");
  $("#coverage").innerHTML = `<table class="cov"><thead><tr><th>Canonical field</th><th>Meaning</th>
    ${cov.channels.map((ch) => `<th class="c">${pill(ch)}<br><span class="hint">${cov.summary[ch].declared}/${cov.summary[ch].total} fields</span></th>`).join("")}</tr></thead>
    <tbody>${rows}</tbody></table>`;
}

// ---------- rules ----------
async function loadRules() {
  const files = await api("/api/mappings");
  $("#rule-files").innerHTML = Object.keys(files).map((k) => `<button data-f="${esc(k)}">${esc(k)}.yaml</button>`).join("");
  const show = (k) => {
    S.ruleFile = k;
    $("#rule-text").textContent = files[k];
    $$("#rule-files button").forEach((b) => b.classList.toggle("active", b.dataset.f === k));
  };
  $$("#rule-files button").forEach((b) => (b.onclick = () => show(b.dataset.f)));
  show(S.ruleFile);
  $("#rule-catalog").innerHTML = `<table><thead><tr><th>Rule</th><th>Severity</th><th>Meaning</th></tr></thead><tbody>
    ${S.state.rules.map((r) => `<tr><td><code>${esc(r.code)}</code></td><td>${esc(r.severity)}</td><td>${esc(r.title)}</td></tr>`).join("")}</tbody></table>`;
}

// ---------- boot ----------
(async () => {
  await refresh();
  await initTry();
  const tab = hash.get("tab");
  if (tab) showTab(tab);
  if (hash.get("sample")) {
    const i = S.samples.findIndex((s) => s.label === hash.get("sample"));
    if (i >= 0) { $("#sample").value = i; $("#sample").onchange(); }
  }
})();
