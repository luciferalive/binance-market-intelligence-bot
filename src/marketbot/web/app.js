"use strict";

/* Core: DOM helpers, formatting, API calls, shared widgets. Every piece of API text is inserted as
   a text node, never parsed as HTML: token names, symbols and headlines are written by strangers. */

const view = document.getElementById("view");
let renderToken = 0;
const S = { meta: null, chain: "solana", sessionToken: null };

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "style") {
      for (const declaration of String(value).split(";")) { // CSP forbids style="" attributes
        const at = declaration.indexOf(":");
        if (at > 0) node.style.setProperty(declaration.slice(0, at).trim(), declaration.slice(at + 1).trim());
      }
    } else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

function safeLink(url, label, extra) {
  if (typeof url !== "string" || !/^https:\/\//.test(url)) return null;
  return el("a", { href: url, target: "_blank", rel: "noopener noreferrer", ...(extra || {}), text: label });
}

/* ---------- formatting ---------- */
const nf = (digits) => new Intl.NumberFormat("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
const ok = (v) => v !== null && v !== undefined && Number.isFinite(v);
function usd(value, compact) {
  if (!ok(value)) return "n/a";
  const abs = Math.abs(value);
  if (compact && abs >= 1e9) return "$" + nf(2).format(value / 1e9) + "B";
  if (compact && abs >= 1e6) return "$" + nf(2).format(value / 1e6) + "M";
  if (compact && abs >= 1e3) return "$" + nf(1).format(value / 1e3) + "K";
  if (abs >= 1000) return "$" + nf(0).format(value);
  if (abs >= 1) return "$" + nf(2).format(value);
  if (abs === 0) return "$0";
  const digits = Math.min(10, Math.max(4, 2 - Math.floor(Math.log10(abs)) + 1));
  return "$" + value.toFixed(digits);
}
function signedUsd(value) { return !ok(value) ? "n/a" : (value < 0 ? "-" : "+") + usd(Math.abs(value)); }
function pctText(value, digits = 1) {
  if (!ok(value)) return "n/a";
  return (value > 0 ? "+" : value < 0 ? "-" : "") + nf(digits).format(Math.abs(value)) + "%";
}
function pctCell(value, digits = 1) {
  const span = el("span", { text: pctText(value, digits) });
  if (value > 0) span.className = "pos"; else if (value < 0) span.className = "neg";
  return span;
}
function qty(value) {
  if (!ok(value)) return "n/a";
  if (value >= 1000) return nf(2).format(value);
  if (value >= 1) return nf(4).format(value);
  return value.toPrecision(4);
}
function num(value, digits = 0) { return ok(value) ? nf(digits).format(value) : "n/a"; }
function ageMin(m) {
  if (!ok(m)) return "n/a";
  if (m < 60) return Math.round(m) + " min";
  if (m < 2880) return nf(1).format(m / 60) + " h";
  return nf(1).format(m / 1440) + " d";
}
function ageHours(h) { return ok(h) ? ageMin(h * 60) : "n/a"; }
function clockTime(value) {
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString();
}
function ago(ts) {
  if (!ok(ts)) return "n/a";
  const s = Math.max(0, Date.now() / 1000 - ts);
  return s < 90 ? Math.round(s) + "s ago" : s < 5400 ? Math.round(s / 60) + "m ago" : nf(1).format(s / 3600) + "h ago";
}
function shortAddr(a) { return a && a.length > 14 ? a.slice(0, 6) + "…" + a.slice(-4) : a || ""; }

/* ---------- badges (always a glyph and a word, never colour alone) ---------- */
const RISK_BADGE = {
  CRITICAL: ["tone-critical", "✕", "Critical"], HIGH: ["tone-serious", "▲", "High"],
  UNVERIFIED: ["tone-warning", "?", "Unverified"], MEDIUM: ["tone-warning", "!", "Medium"], LOW: ["tone-good", "✓", "Low"],
};
const LABEL_BADGE = {
  CANDIDATE: ["tone-good", "●", "Candidate"], WATCH: ["tone-info", "◔", "Watch"], SKIP: ["tone-muted", "–", "Skip"],
  "HIGH RISK": ["tone-serious", "▲", "High risk"], AVOID: ["tone-critical", "✕", "Avoid"],
};
const CONF_BADGE = {
  HIGH: ["tone-good", "▮▮▮", "High"], MEDIUM: ["tone-warning", "▮▮▯", "Medium"], LOW: ["tone-serious", "▮▯▯", "Low"], UNKNOWN: ["tone-muted", "▯▯▯", "Unknown"],
};
function badge(table, key) {
  const [tone, glyph, label] = table[key] || ["tone-muted", "–", String(key)];
  return el("span", { class: "badge " + tone }, el("span", { class: "glyph", "aria-hidden": "true", text: glyph }), label);
}
function tone(table, key) {
  const t = (table[key] || ["tone-muted"])[0].replace("tone-", "");
  return { critical: "var(--critical)", serious: "var(--serious)", warning: "var(--warning)", good: "var(--good)", info: "var(--accent)", muted: "transparent" }[t];
}
function meter(value, max = 100) {
  if (!ok(value)) return el("span", { class: "sub-text", text: "n/a" });
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return el("span", { class: "meter" },
    el("span", { class: "meter-track", role: "img", "aria-label": "Score " + value + " out of " + max },
      el("span", { class: "meter-fill", style: "width:" + pct + "%" })), String(value));
}
function freshness(ts, staleAfter = 300) {
  if (!ok(ts)) return el("span", { class: "fresh stale", text: "age unknown" });
  const stale = Date.now() / 1000 - ts > staleAfter;
  return el("span", { class: "fresh" + (stale ? " stale" : ""), text: (stale ? "stale: " : "updated ") + ago(ts) });
}

/* ---------- network ---------- */
async function readBody(response) {
  let body = null;
  try { body = await response.json(); } catch (err) { /* not JSON */ }
  if (!response.ok) {
    const error = new Error((body && body.error) || "Request failed (" + response.status + ")");
    error.unsafeKey = Boolean(body && body.unsafe_key);
    throw error;
  }
  return body;
}
async function api(path) {
  let response;
  try { response = await fetch(path, { headers: { Accept: "application/json" } }); }
  catch (err) { throw new Error("Could not reach the local server. Is it still running in your terminal?"); }
  return readBody(response);
}
async function sessionToken() {
  if (!S.sessionToken) S.sessionToken = (await api("/api/session")).token;
  return S.sessionToken;
}
async function post(path, body) {
  const token = await sessionToken();
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Desk-Token": token, Accept: "application/json" },
      body: JSON.stringify(body || {}),
    });
  } catch (err) { throw new Error("Could not reach the local server."); }
  return readBody(response);
}
function qs(params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== "" && v !== null && v !== undefined && v !== false) q.set(k, v === true ? "1" : v);
  return q.toString();
}

/* ---------- shared widgets ---------- */
function notices(list, title) {
  if (!list || !list.length) return null;
  return el("div", { class: "notice", role: "status" }, el("strong", { text: title || "Some data is missing" }),
    el("ul", null, list.map((item) => el("li", { text: item }))));
}
function errorBox(err) { return el("div", { class: "notice error", role: "alert" }, err.message); }
function loading(label) { return el("p", { class: "meta", role: "status", text: label }); }
function sectionHead(title, lede, controls) {
  return el("div", { class: "section-head" },
    el("div", null, el("h2", { text: title }), lede ? el("p", { class: "lede", text: lede }) : null), controls || null);
}
function panel(title, body, controls, cls) {
  return el("section", { class: "panel " + (cls || "") },
    title ? el("div", { class: "panel-head" }, el("h3", { text: title }), controls || null) : null,
    el("div", { class: "panel-body" }, body));
}
function stat(label, value, sub, valueClass) {
  return el("div", { class: "panel stat" }, el("div", { class: "label", text: label }),
    el("div", { class: "value " + (valueClass || ""), text: value }), sub ? el("div", { class: "sub", text: sub }) : null);
}
function kv(pairs) {
  return el("dl", { class: "kv" }, pairs.filter(Boolean).flatMap(([k, v]) => [el("dt", { text: k }), el("dd", null, v)]));
}
function bullets(items, glyph = "•") {
  return el("ul", { class: "clean" }, items.map((t) => el("li", null, el("span", { class: "glyph", "aria-hidden": "true", text: glyph }), el("span", { text: t }))));
}
function barRow(label, value, max, detail, negative) {
  const pct = max ? Math.max(0, Math.min(100, (Math.abs(value) / max) * 100)) : 0;
  return el("div", { class: "bar-row", title: detail || "" },
    el("span", { text: label }),
    el("div", { class: "bar-track" }, el("div", { class: "bar-fill" + (negative ? " neg" : ""), style: "width:" + pct + "%" })),
    el("span", { class: "num", text: (value > 0 && negative ? "-" : "") + num(Math.abs(value), 1) + (max ? "/" + max : "") }));
}
function toast(text, bad) {
  const box = document.getElementById("alerts-panel");
  const note = el("div", { class: "alert-item " + (bad ? "critical" : "info"), role: "status" }, el("div", { class: "t", text }));
  box.prepend(note);
  setTimeout(() => note.remove(), 6000);
}

/* Searchable, sortable table with expandable rows. columns: {label, cell(row), sort(row), left} */
function dataTable(opts) {
  const state = { key: opts.defaultSort ?? null, dir: -1, query: "", open: new Set() };
  const body = el("tbody");
  const count = el("span", { class: "meta" });
  const search = el("input", { type: "search", placeholder: opts.searchHint || "Search…", "aria-label": "Search table" });
  const draw = () => {
    let rows = opts.rows.slice();
    const q = state.query.toLowerCase();
    if (q) rows = rows.filter((r) => (opts.text ? opts.text(r) : JSON.stringify(r)).toLowerCase().includes(q));
    if (state.key !== null) {
      const get = opts.columns[state.key].sort;
      if (get) rows.sort((a, b) => {
        const x = get(a), y = get(b);
        if (!ok(x) && !ok(y)) return 0; if (!ok(x)) return 1; if (!ok(y)) return -1; // unknown sorts last
        return (x - y) * state.dir;
      });
    }
    count.textContent = rows.length + " of " + opts.rows.length;
    body.replaceChildren();
    if (!rows.length) {
      body.append(el("tr", null, el("td", { colspan: String(opts.columns.length), class: "empty", text: opts.empty || "Nothing matches." })));
      return;
    }
    for (const row of rows) {
      const id = opts.id ? opts.id(row) : null;
      const first = opts.expand
        ? (() => {
          const t = el("button", { class: "toggle", type: "button", "aria-expanded": String(state.open.has(id)) },
            el("span", { class: "caret", "aria-hidden": "true", text: "▶" }), opts.columns[0].cell(row));
          return t;
        })() : opts.columns[0].cell(row);
      const main = el("tr", { class: "row-main", style: opts.spine ? "--spine:" + opts.spine(row) : "" },
        el("td", { class: "left" }, first), opts.columns.slice(1).map((c) => el("td", { class: c.left ? "left" : "" }, c.cell(row))));
      body.append(main);
      if (opts.expand) {
        const detail = el("tr", { class: "row-detail", hidden: !state.open.has(id) }, el("td", { colspan: String(opts.columns.length) }));
        let built = false;
        const fill = () => { if (!built) { detail.firstChild.append(opts.expand(row)); built = true; } };
        if (state.open.has(id)) fill();
        first.addEventListener("click", () => {
          const open = !state.open.has(id);
          if (open) { state.open.add(id); fill(); } else state.open.delete(id);
          first.setAttribute("aria-expanded", String(open));
          detail.hidden = !open;
        });
        body.append(detail);
      }
    }
  };
  search.addEventListener("input", () => { state.query = search.value.trim(); draw(); });
  const head = el("tr", null, opts.columns.map((c, i) => {
    const th = el("th", { class: (c.left ? "left " : "") + (c.sort ? "" : "nosort"), "aria-sort": "none", text: c.label });
    if (c.sort) th.addEventListener("click", () => {
      state.dir = state.key === i ? -state.dir : -1; state.key = i;
      head.querySelectorAll("th").forEach((x) => x.setAttribute("aria-sort", "none"));
      th.setAttribute("aria-sort", state.dir < 0 ? "descending" : "ascending");
      draw();
    });
    return th;
  }));
  draw();
  return el("div", { class: "panel" },
    el("div", { class: "tbar" }, opts.title ? el("h3", { text: opts.title }) : null, search, count, opts.toolbar || null),
    el("div", { class: "scroll" }, el("table", null, el("thead", null, head), body)));
}

/* Two panes with a draggable divider; width is remembered per key. */
function splitPane(a, b, key) {
  const wrap = el("div", { class: "split" });
  const gutter = el("div", { class: "gutter", role: "separator", "aria-orientation": "vertical", tabindex: "0", "aria-label": "Resize panels" });
  const pa = el("div", { class: "pane-a" }, a), pb = el("div", { class: "pane-b" }, b);
  wrap.append(pa, gutter, pb);
  try { const saved = localStorage.getItem("split:" + key); if (saved) wrap.style.setProperty("--split", saved); } catch (e) { /* storage blocked */ }
  const set = (pct) => {
    const v = Math.max(30, Math.min(80, pct)) + "%";
    wrap.style.setProperty("--split", v);
    try { localStorage.setItem("split:" + key, v); } catch (e) { /* ignore */ }
    window.dispatchEvent(new Event("resize"));
  };
  gutter.addEventListener("pointerdown", (e) => {
    gutter.setPointerCapture(e.pointerId);
    const move = (ev) => { const r = wrap.getBoundingClientRect(); set(((ev.clientX - r.left) / r.width) * 100); };
    const up = () => { gutter.removeEventListener("pointermove", move); gutter.removeEventListener("pointerup", up); };
    gutter.addEventListener("pointermove", move); gutter.addEventListener("pointerup", up);
  });
  gutter.addEventListener("keydown", (e) => {
    const cur = parseFloat(wrap.style.getPropertyValue("--split")) || 62;
    if (e.key === "ArrowLeft") set(cur - 3); if (e.key === "ArrowRight") set(cur + 3);
  });
  return wrap;
}

function tabs(items, initial, onChange) {
  const bar = el("div", { class: "tabs", role: "tablist" });
  const buttons = items.map(([id, label]) => {
    const b = el("button", { class: "tab", type: "button", role: "tab", "aria-selected": String(id === initial), text: label });
    b.addEventListener("click", () => { buttons.forEach((x) => x.setAttribute("aria-selected", "false")); b.setAttribute("aria-selected", "true"); onChange(id); });
    return b;
  });
  bar.append(...buttons);
  return bar;
}

function confirmDialog(text, yes) {
  return new Promise((resolve) => {
    const d = el("dialog", null, el("p", { text }),
      el("div", { class: "row-actions" },
        el("button", { class: "btn", type: "button", text: "Cancel", onclick: () => { d.close(); resolve(false); } }),
        el("button", { class: "btn primary", type: "button", text: yes || "Confirm", onclick: () => { d.close(); resolve(true); } })));
    d.addEventListener("close", () => { d.remove(); resolve(false); });
    document.body.append(d); d.showModal();
  });
}

function goto(hash) { location.hash = hash; }
