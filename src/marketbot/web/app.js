"use strict";

/* All text from the API is inserted as plain text nodes, never parsed as HTML: token names,
   symbols and news headlines are written by strangers and must be treated as hostile. */

const view = document.getElementById("view");
const tabLinks = Array.from(document.querySelectorAll("#tabs a"));
let renderToken = 0;

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "style") {
      // The page CSP forbids style="" attributes, so apply declarations through the style API.
      for (const declaration of String(value).split(";")) {
        const at = declaration.indexOf(":");
        if (at > 0) node.style.setProperty(declaration.slice(0, at).trim(), declaration.slice(at + 1).trim());
      }
    }
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
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

const nf = (digits) => new Intl.NumberFormat("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
function usd(value, compact) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "n/a";
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
function signedUsd(value) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "n/a";
  return (value < 0 ? "-" : "+") + usd(Math.abs(value));
}
function pctText(value, digits = 1) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "n/a";
  return (value > 0 ? "+" : value < 0 ? "-" : "") + nf(digits).format(Math.abs(value)) + "%";
}
function pctCell(value, digits = 1) {
  const span = el("span", { text: pctText(value, digits) });
  if (value > 0) span.className = "pos";
  else if (value < 0) span.className = "neg";
  return span;
}
function qty(value) {
  if (!Number.isFinite(value)) return "n/a";
  if (value >= 1000) return nf(2).format(value);
  if (value >= 1) return nf(4).format(value);
  return value.toPrecision(4);
}
function age(hours) {
  if (hours === null || hours === undefined) return "n/a";
  if (hours < 1) return Math.round(hours * 60) + " min";
  if (hours < 48) return Math.round(hours) + " h";
  return nf(1).format(hours / 24) + " d";
}
function clockTime(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString();
}

/* ---- badges: always a glyph and a word, so colour is never the only signal ---- */
const RISK_BADGE = {
  CRITICAL: ["tone-critical", "✕", "Critical"],
  HIGH: ["tone-serious", "▲", "High"],
  UNVERIFIED: ["tone-warning", "?", "Unverified"],
  MEDIUM: ["tone-warning", "!", "Medium"],
  LOW: ["tone-good", "✓", "Low"],
};
const VERDICT_BADGE = {
  AVOID: ["tone-critical", "✕", "Avoid"],
  HIGH_RISK: ["tone-serious", "▲", "Hot but risky"],
  SKIP: ["tone-muted", "–", "Skip"],
  WATCH: ["tone-good", "●", "Worth watching"],
};
const PUMP_BADGE = {
  PUMP_STARTING: ["tone-good", "▲", "Pump starting"],
  BUILDING: ["tone-info", "◔", "Building"],
  PUMP_EXTENDED: ["tone-warning", "!", "Already extended"],
  DUMP_RISK: ["tone-serious", "▼", "Dump risk"],
  DUMPING: ["tone-critical", "▼", "Dumping"],
  QUIET: ["tone-muted", "–", "Quiet"],
};
function badge(table, key) {
  const [tone, glyph, label] = table[key] || ["tone-muted", "–", String(key)];
  return el("span", { class: "badge " + tone }, el("span", { class: "glyph", "aria-hidden": "true", text: glyph }), label);
}
function spine(table, key) {
  const tone = (table[key] || ["tone-muted"])[0].replace("tone-", "");
  const map = { critical: "var(--critical)", serious: "var(--serious)", warning: "var(--warning)", good: "var(--good)", info: "var(--accent)", muted: "transparent" };
  return map[tone];
}
function meter(value) {
  return el("span", { class: "meter" },
    el("span", { class: "meter-track", role: "img", "aria-label": "Heat " + value + " out of 100" },
      el("span", { class: "meter-fill", style: "width:" + Math.max(0, Math.min(100, value)) + "%" })),
    String(value));
}

/* ---- shared fetch + states ---- */
async function api(path) {
  let response;
  try {
    response = await fetch(path, { headers: { Accept: "application/json" } });
  } catch (err) {
    throw new Error("Could not reach the dashboard server. Is it still running in your terminal?");
  }
  let body = null;
  try { body = await response.json(); } catch (err) { /* not JSON */ }
  if (!response.ok) {
    const error = new Error((body && body.error) || "Request failed (" + response.status + ")");
    error.unsafeKey = Boolean(body && body.unsafe_key);
    throw error;
  }
  return body;
}
function notices(list) {
  if (!list || !list.length) return null;
  return el("div", { class: "notice", role: "status" },
    el("strong", { text: "Some data is missing" }),
    el("ul", null, list.map((item) => el("li", { text: item }))));
}
function errorBox(err) {
  return el("div", { class: "notice error", role: "alert" }, err.message);
}
function loading(label) {
  return el("p", { class: "meta", role: "status", text: label });
}
function sectionHead(title, lede, controls) {
  return el("div", { class: "section-head" },
    el("div", null, el("h2", { text: title }), lede ? el("p", { class: "lede", text: lede }) : null),
    controls || null);
}

/* ---- portfolio ---- */
function setupSteps() {
  return el("div", { class: "panel pad" },
    el("h2", { text: "Connect your Binance account" }),
    el("p", { class: "lede", text: "The portfolio tab needs a read-only API key. It is off until you add one." }),
    el("ol", { class: "steps" },
      el("li", null, "In Binance, open Account > API Management and create a key. Turn on Enable Reading only. Leave trading, withdrawals and transfers off, and restrict the key to your IP address."),
      el("li", null, "Copy .env.example to .env in the project folder and fill in the two values:"),
      el("li", null, el("pre", { text: "BINANCE_API_KEY=your_read_only_key\nBINANCE_API_SECRET=your_secret" })),
      el("li", null, "Restart the dashboard. The .env file is ignored by git, so it never reaches GitHub."),
      el("li", null, "The dashboard checks the key when it loads and refuses to run if withdrawals are enabled.")));
}

function stat(label, value, sub) {
  return el("div", { class: "panel stat" }, el("div", { class: "label", text: label }), el("div", { class: "value", text: value }), sub ? el("div", { class: "sub", text: sub }) : null);
}

function holdingsTable(snapshot) {
  const hasPnl = snapshot.holdings.some((row) => "avg_cost" in row);
  const head = ["Asset", "Quantity", "Price", "Value", "Share", "24h"];
  if (hasPnl) head.push("Avg cost", "Unrealized PnL", "Realized PnL", "Cost data");
  const rows = snapshot.holdings.map((row) => {
    const cells = [
      el("td", null, el("span", { class: "sym", text: row.asset }), row.note ? el("span", { class: "sub-text", text: " " + row.note }) : null, row.is_dust ? el("span", { class: "sub-text", text: " dust" }) : null),
      el("td", { text: qty(row.quantity) }),
      el("td", { text: usd(row.price_usd) }),
      el("td", { text: usd(row.value_usd) }),
      el("td", { text: nf(1).format(row.weight_pct) + "%" }),
      el("td", null, row.change_24h_pct === null ? "n/a" : pctCell(row.change_24h_pct, 2)),
    ];
    if (hasPnl) {
      const known = row.unrealized_pnl !== null && row.unrealized_pnl !== undefined;
      cells.push(
        el("td", { text: row.avg_cost ? usd(row.avg_cost) : "n/a" }),
        el("td", null, known ? el("span", null, el("span", { class: row.unrealized_pnl >= 0 ? "pos" : "neg", text: signedUsd(row.unrealized_pnl) }), el("span", { class: "sub-text", text: row.unrealized_pnl_pct === null ? "" : " (" + pctText(row.unrealized_pnl_pct) + ")" })) : "n/a"),
        el("td", { text: row.realized_pnl === null || row.realized_pnl === undefined ? "n/a" : signedUsd(row.realized_pnl) }),
        el("td", { text: known ? nf(0).format(row.basis_coverage_pct) + "% covered" : "no trades found" }),
      );
    }
    return el("tr", null, cells);
  });
  return el("div", { class: "panel scroll" }, el("table", null,
    el("thead", null, el("tr", null, head.map((h) => el("th", { text: h })))),
    el("tbody", null, rows)));
}

function allocation(snapshot) {
  const top = snapshot.holdings.filter((row) => row.weight_pct >= 0.5).slice(0, 12);
  const shown = top.reduce((sum, row) => sum + row.weight_pct, 0);
  const rows = top.map((row) => el("div", { class: "alloc-row" },
    el("span", { class: "sym", text: row.asset }),
    el("div", { class: "alloc-track", role: "img", "aria-label": row.asset + " " + nf(1).format(row.weight_pct) + " percent" },
      el("div", { class: "alloc-fill", style: "width:" + Math.min(100, row.weight_pct) + "%" })),
    el("span", { class: "num", text: nf(1).format(row.weight_pct) + "%  " + usd(row.value_usd) })));
  if (shown < 99.5) {
    const rest = 100 - shown;
    rows.push(el("div", { class: "alloc-row" }, el("span", { class: "sub-text", text: "Everything else" }),
      el("div", { class: "alloc-track" }, el("div", { class: "alloc-fill", style: "width:" + rest + "%;opacity:.45" })),
      el("span", { class: "num", text: nf(1).format(rest) + "%" })));
  }
  return el("div", { class: "panel alloc" }, rows);
}

async function renderPortfolio(root, token) {
  root.append(sectionHead("Portfolio", "Your Binance Spot balances, valued at current prices."));
  const holder = el("div", null, loading("Loading your balances…"));
  root.append(holder);
  let data;
  try { data = await api("/api/portfolio"); }
  catch (err) { if (token !== renderToken) return; holder.replaceChildren(errorBox(err)); return; }
  if (token !== renderToken) return;
  if (!data.configured) { holder.replaceChildren(setupSteps()); return; }

  const parts = [];
  if (data.warnings && data.warnings.length) parts.push(notices(data.warnings));
  for (const flag of data.flags || []) parts.push(el("div", { class: "notice", role: "status", text: flag + " One coin moving against you hurts a lot." }));
  const changeUsd = data.change_24h_usd;
  parts.push(el("div", { class: "stats" },
    stat("Total value", usd(data.total_usd)),
    stat("Last 24 hours", signedUsd(changeUsd), data.change_24h_pct === null ? "" : pctText(data.change_24h_pct, 2)),
    stat("In stablecoins", nf(1).format(data.stable_pct) + "%", "Cash you can redeploy"),
    stat("Positions", String(data.holdings.length), data.unvalued.length ? data.unvalued.length + " could not be priced" : "")));
  parts.push(el("div", { class: "section" }, el("h3", { text: "Allocation" }), el("div", { style: "height:8px" }), allocation(data)));
  parts.push(el("div", { class: "section" }, el("h3", { text: "Holdings" }), el("div", { style: "height:8px" }), holdingsTable(data)));
  if (data.pnl_note) parts.push(el("p", { class: "meta", text: data.pnl_note }));
  if (data.unvalued.length) parts.push(el("p", { class: "meta", text: "Not priced (no USDT, USDC or BTC market): " + data.unvalued.map((u) => u.asset).join(", ") }));
  parts.push(el("p", { class: "meta", text: "Updated " + clockTime(data.generated_at) + ". Refreshes when you reopen this tab." }));
  holder.replaceChildren(...parts.filter(Boolean));
}

/* ---- token rows with a risk breakdown ---- */
function riskList(flags) {
  if (!flags || !flags.length) return el("p", { class: "meta", text: "No flags raised." });
  const glyph = { critical: "✕", high: "▲", medium: "!", info: "i" };
  return el("ul", null, flags.map((flag) => el("li", { class: "sev-" + flag.severity },
    el("span", { class: "glyph", "aria-hidden": "true", text: glyph[flag.severity] || "•" }),
    el("span", { text: flag.message }))));
}

function newsBlock(token) {
  const out = el("div", { class: "news-list" });
  const button = el("button", { class: "btn", type: "button", text: "Look for news and company interest" });
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Searching news…";
    try {
      const query = token.name && token.name.length > 2 ? token.name : token.symbol;
      const data = await api("/api/news?q=" + encodeURIComponent(query + " " + token.symbol));
      out.replaceChildren();
      const s = data.summary;
      out.append(el("p", { class: "meta", text: s.total ? s.total + " headlines in the last 7 days. Tone: " + s.tone + "." : "No headlines found in the last 7 days. For a new token, no coverage is normal and is not a good or bad sign by itself." }));
      for (const item of data.headlines) {
        const tags = (item.tags || []).map((tag) => el("span", { class: "tag" + (tag === "negative" ? " bad" : ""), text: tag.replace("_", " ") }));
        out.append(el("div", { class: "news-item" },
          el("div", null, tags, safeLink(item.link, item.title) || item.title),
          el("div", { class: "sub-text", text: [item.source, item.published ? new Date(item.published).toLocaleDateString() : ""].filter(Boolean).join(", ") })));
      }
      button.remove();
    } catch (err) {
      out.replaceChildren(errorBox(err));
      button.disabled = false;
      button.textContent = "Try the news search again";
    }
  });
  return el("div", null, button, out);
}

function tokenDetail(token) {
  const facts = [
    ["Liquidity", usd(token.liquidity_usd, true)], ["24h volume", usd(token.volume_24h, true)],
    ["Market cap", usd(token.market_cap || token.fdv, true)], ["Pair age", age(token.age_hours)],
    ["Buys / sells (1h)", token.buys_1h + " / " + token.sells_1h], ["Chain", token.chain + (token.dex ? " on " + token.dex : "")],
  ];
  const links = [safeLink(token.url, "Open chart on DexScreener")].filter(Boolean);
  return el("div", { class: "detail-grid" },
    el("div", null, el("h3", { text: "Why it is hot" }),
      el("ul", null, (token.heat_reasons.length ? token.heat_reasons : ["Nothing stands out."]).map((reason) => el("li", null, el("span", { class: "glyph", "aria-hidden": "true", text: "•" }), el("span", { text: reason }))))),
    el("div", null, el("h3", { text: "Risk checks: " + token.risk.summary }), riskList(token.risk.flags)),
    el("div", null, el("h3", { text: "Numbers" }),
      el("ul", null, facts.map(([k, v]) => el("li", null, el("span", { class: "glyph", "aria-hidden": "true", text: "•" }), el("span", { text: k + ": " + v })))),
      el("p", { class: "addr", text: "Contract: " + token.address }),
      el("div", { class: "actions" }, links, newsBlock(token))));
}

function tokenRows(tokens, columns) {
  const out = [];
  tokens.forEach((token, index) => {
    const id = "detail-" + index + "-" + Math.random().toString(36).slice(2, 7);
    const toggle = el("button", { class: "toggle", type: "button", "aria-expanded": "false", "aria-controls": id },
      el("span", { class: "caret", "aria-hidden": "true", text: "▶" }), token.symbol, el("span", { class: "sub-text", text: token.name && token.name !== token.symbol ? token.name : "" }));
    const main = el("tr", { class: "row-main", style: "--spine:" + spine(VERDICT_BADGE, token.verdict) }, el("td", null, toggle), columns.map((make) => make(token)));
    const detail = el("tr", { class: "row-detail", id, hidden: true }, el("td", { colspan: String(columns.length + 1) }, tokenDetail(token)));
    toggle.addEventListener("click", () => {
      const open = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!open));
      detail.hidden = open;
    });
    out.push(main, detail);
  });
  return out;
}

const TOKEN_COLUMNS = [
  (t) => el("td", { class: "left" }, badge(VERDICT_BADGE, t.verdict)),
  (t) => el("td", null, meter(t.heat_score)),
  (t) => el("td", { class: "left" }, badge(RISK_BADGE, t.risk.level)),
  (t) => el("td", { text: usd(t.price_usd) }),
  (t) => el("td", null, pctCell(t.change_1h)),
  (t) => el("td", null, pctCell(t.change_24h, 0)),
  (t) => el("td", { text: usd(t.liquidity_usd, true) }),
  (t) => el("td", { text: age(t.age_hours) }),
];
const TOKEN_HEAD = ["Token", "Verdict", "Heat", "Rug risk", "Price", "1h", "24h", "Liquidity", "Age"];

function tokenTable(tokens) {
  return el("div", { class: "panel scroll" }, el("table", null,
    el("thead", null, el("tr", null, TOKEN_HEAD.map((h, i) => el("th", { class: i === 1 || i === 3 ? "left" : "", text: h })))),
    el("tbody", null, tokenRows(tokens, TOKEN_COLUMNS))));
}

/* ---- meme coins ---- */
function chipList(rows, empty) {
  if (!rows.length) return el("p", { class: "meta", text: empty });
  return el("div", { class: "chips" }, rows.map((row) => el("span", { class: "chip" },
    el("strong", { text: row.symbol }),
    el("span", { class: "sub-text", text: row.change_24h === null || row.change_24h === undefined ? "" : pctText(row.change_24h, 1) }),
    el("span", { class: "sub-text", text: row.on_binance ? "on Binance" : "not on Binance" }))));
}

async function renderMemes(root, token) {
  const refresh = el("button", { class: "btn", type: "button", text: "Refresh" });
  refresh.addEventListener("click", () => route());
  root.append(sectionHead("Meme coins", "Newly promoted tokens ranked by attention, with a rug pull check on each. Open a row to see why.", refresh));
  const holder = el("div", null, loading("Scanning DexScreener and checking contracts. This takes up to half a minute…"));
  root.append(holder);
  let data;
  try { data = await api("/api/memes"); }
  catch (err) { if (token !== renderToken) return; holder.replaceChildren(errorBox(err)); return; }
  if (token !== renderToken) return;
  const parts = [notices(data.warnings)];
  parts.push(el("div", { class: "notice", text: data.note }));
  parts.push(el("div", { class: "section" }, el("h3", { text: "New and promoted tokens" }), el("div", { style: "height:8px" }),
    data.tokens.length ? tokenTable(data.tokens) : el("div", { class: "panel empty", text: "No tokens passed the liquidity and volume filters right now. Refresh in a few minutes." })));
  parts.push(el("div", { class: "section" }, el("h3", { text: "Trending on CoinGecko" }), el("div", { style: "height:8px" }), chipList(data.trending, "CoinGecko trending is unavailable right now.")));
  parts.push(el("div", { class: "section" }, el("h3", { text: "Established meme coins by 24h volume" }), el("div", { style: "height:8px" }), chipList(data.established.slice(0, 20), "CoinGecko meme list is unavailable right now.")));
  holder.replaceChildren(...parts.filter(Boolean));
}

/* ---- pump / dump ---- */
async function renderPumps(root, token) {
  const refresh = el("button", { class: "btn", type: "button", text: "Refresh" });
  refresh.addEventListener("click", () => route());
  root.append(sectionHead("Pump and dump watch", "Binance Spot markets where volume and price are doing something unusual right now. This shows what is happening, not what happens next.", refresh));
  const holder = el("div", null, loading("Reading hourly candles for the most active markets…"));
  root.append(holder);
  let data;
  try { data = await api("/api/pumps"); }
  catch (err) { if (token !== renderToken) return; holder.replaceChildren(errorBox(err)); return; }
  if (token !== renderToken) return;

  const state = { memeOnly: false, quiet: false };
  const body = el("div");
  const draw = () => {
    const rows = data.rows.filter((r) => (state.quiet || r.label !== "QUIET") && (!state.memeOnly || r.is_meme));
    if (!rows.length) { body.replaceChildren(el("div", { class: "panel empty", text: "Nothing unusual with these filters. Markets are quiet, or try showing quiet markets." })); return; }
    const head = ["Market", "Signal", "Volume vs normal", "1h", "4h", "24h", "Buying share", "RSI", "Off peak", "Why"];
    const trs = rows.map((r) => el("tr", { class: "row-main", style: "--spine:" + spine(PUMP_BADGE, r.label) },
      el("td", null, el("span", { class: "sym", text: r.base }), el("span", { class: "sub-text", text: r.is_meme ? " meme" : "" })),
      el("td", { class: "left" }, badge(PUMP_BADGE, r.label)),
      el("td", { text: nf(1).format(r.rel_volume) + "x" }),
      el("td", null, pctCell(r.ret_1h_pct)), el("td", null, pctCell(r.ret_4h_pct)), el("td", null, pctCell(r.change_24h_pct, 1)),
      el("td", { text: nf(0).format(r.taker_buy_ratio * 100) + "%" }),
      el("td", { text: nf(0).format(r.rsi) }),
      el("td", { text: nf(1).format(r.drawdown_from_peak_pct) + "%" }),
      el("td", { class: "wrap left", text: r.reasons.join(". ") })));
    body.replaceChildren(el("div", { class: "panel scroll" }, el("table", null,
      el("thead", null, el("tr", null, head.map((h, i) => el("th", { class: i === 1 || i === 9 ? "left" : "", text: h })))),
      el("tbody", null, trs))));
  };
  const memeBox = el("input", { type: "checkbox", id: "f-meme" });
  const quietBox = el("input", { type: "checkbox", id: "f-quiet" });
  memeBox.addEventListener("change", () => { state.memeOnly = memeBox.checked; draw(); });
  quietBox.addEventListener("change", () => { state.quiet = quietBox.checked; draw(); });
  draw();
  holder.replaceChildren(...[
    notices(data.warnings),
    el("div", { class: "controls", style: "margin-bottom:12px" },
      el("label", { for: "f-meme" }, memeBox, "Meme coins only"),
      el("label", { for: "f-quiet" }, quietBox, "Show quiet markets"),
      el("span", { class: "meta", text: data.rows.length + " markets scanned" })),
    body,
    el("p", { class: "meta", style: "margin-top:10px", text: "Buying share is the part of the last 4 hours of volume that came from market buy orders. Off peak is the distance from the 24 hour high." }),
  ].filter(Boolean));
}

/* ---- single token check ---- */
async function renderCheck(root, token) {
  root.append(sectionHead("Check a token", "Paste a contract address or type a name or ticker. You get the rug pull checks, liquidity and a reason list before you put money in."));
  const input = el("input", { type: "text", id: "q", placeholder: "Contract address, token name or ticker", autocomplete: "off", spellcheck: "false", maxlength: "100", "aria-label": "Token to check" });
  const go = el("button", { class: "btn primary", type: "submit", text: "Check token" });
  const results = el("div");
  const form = el("form", { class: "search" }, input, go);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const q = input.value.trim();
    if (!q) { results.replaceChildren(el("div", { class: "notice", text: "Type a contract address, name or ticker first." })); input.focus(); return; }
    go.disabled = true;
    results.replaceChildren(loading("Checking " + q + "…"));
    try {
      const data = await api("/api/token?q=" + encodeURIComponent(q));
      if (!data.tokens.length) { results.replaceChildren(...[notices(data.warnings), el("div", { class: "panel empty", text: "No trading pair found for that search. A token with no pair cannot be bought, and a very new one may not be indexed yet. Check the spelling or paste the contract address." })].filter(Boolean)); return; }
      results.replaceChildren(...[notices(data.warnings), ...data.tokens.map((t) => el("div", { class: "panel result" },
        el("div", { class: "result-head" }, el("h3", { text: t.symbol + (t.name ? "  " + t.name : "") }), badge(VERDICT_BADGE, t.verdict), badge(RISK_BADGE, t.risk.level), el("span", { class: "meta", text: "Heat " + t.heat_score + "  Price " + usd(t.price_usd) }), el("span", { class: "meta", text: "1h " + pctText(t.change_1h) + "  24h " + pctText(t.change_24h, 0) })),
        el("div", { class: "pad" }, tokenDetail(t))))].filter(Boolean));
    } catch (err) { results.replaceChildren(errorBox(err)); }
    finally { go.disabled = false; }
  });
  root.append(form, results);
  input.focus();
}

/* ---- router ---- */
const ROUTES = { portfolio: renderPortfolio, memes: renderMemes, pumps: renderPumps, check: renderCheck };
async function route() {
  const name = (location.hash || "#memes").slice(1);
  const render = ROUTES[name] || renderMemes;
  const active = ROUTES[name] ? name : "memes";
  tabLinks.forEach((link) => {
    if (link.dataset.tab === active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  const token = ++renderToken;
  view.replaceChildren();
  await render(view, token);
}
window.addEventListener("hashchange", route);
route();
