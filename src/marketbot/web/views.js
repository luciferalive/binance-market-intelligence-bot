"use strict";

/* Discovery and investigation pages: Command Center, New Launches, Token Scanner, Watchlist,
   Narratives, Token Investigation. */

const MODES = [["fresh", "Fresh launches"], ["momentum", "Momentum"], ["smart", "Smart money"]];
const MODE_HELP = {
  fresh: "Brand-new pools ranked on liquidity, buyer breadth, early demand and contract safety. Caps keep very young pools from scoring high.",
  momentum: "Trending pools ranked on volume acceleration, buyer breadth, buy pressure and trend quality.",
  smart: "Ranks tokens the wallets you track are buying. Add wallets on the Smart Money page first.",
};

function poolLink(item) { return "#investigate/" + item.pool.chain + "/" + item.pool.pool_address; }

function scoreBreakdown(item) {
  const max = item.contributions.reduce((s, c) => s + (c.max || 0), 0);
  const rows = item.contributions.map((c) => barRow(c.name, c.points, c.max || 0, c.detail, c.points < 0));
  return el("div", null,
    el("h3", { text: "Why this score" }), el("div", { class: "meta", text: "Raw " + num(item.opportunity_raw) + " of about " + num(max) + ". Hover a bar for the reason." }),
    el("div", null, rows),
    item.caps.length ? bullets(item.caps.map((c) => "Capped at " + c.cap + ": " + c.reason), "⛔") : null,
    item.notes.length ? bullets(item.notes, "i") : null);
}

function riskFlags(flags) {
  if (!flags || !flags.length) return el("p", { class: "meta", text: "No flags raised from the data available." });
  const glyph = { critical: "✕", high: "▲", medium: "!", info: "i" };
  return el("ul", { class: "clean" }, flags.map((f) => el("li", { class: "sev-" + f.severity },
    el("span", { class: "glyph", "aria-hidden": "true", text: glyph[f.severity] || "•" }), el("span", { text: f.message }))));
}

function watchButton(item) {
  const b = el("button", { class: "btn small", type: "button", text: item.watched ? "★ Watching" : "☆ Watch" });
  b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      await post(item.watched ? "/api/watchlist/remove" : "/api/watchlist/add", { chain: item.pool.chain, id: item.pool.pool_address });
      item.watched = !item.watched; b.textContent = item.watched ? "★ Watching" : "☆ Watch";
    } catch (err) { toast(err.message, true); }
    b.disabled = false;
  });
  return b;
}

function scanDetail(item) {
  return el("div", { class: "grid g3" },
    el("div", null, scoreBreakdown(item)),
    el("div", null, el("h3", { text: "Risk: " + item.risk.summary }), riskFlags(item.risk.flags),
      el("h3", { text: "Data confidence" }), bullets(item.confidence.reasons.length ? item.confidence.reasons : ["No notes."])),
    el("div", null, el("h3", { text: "Pool" }), el("p", { class: "addr", text: "Token " + item.pool.token_address }),
      el("p", { class: "addr", text: "Pool " + item.pool.pool_address }),
      el("div", { class: "row-actions" }, el("a", { class: "btn small", href: poolLink(item), text: "Investigate →" }), watchButton(item))));
}

const SCAN_COLUMNS = [
  { label: "Token", sort: (r) => r.pool.symbol.toLowerCase().charCodeAt(0), left: true,
    cell: (r) => el("span", null, el("span", { class: "sym", text: r.pool.symbol }), el("span", { class: "sub-text", text: " " + (r.pool.name !== r.pool.symbol ? r.pool.name.slice(0, 22) : "") })) },
  { label: "Signal", left: true, sort: (r) => r.opportunity, cell: (r) => badge(LABEL_BADGE, r.label) },
  { label: "Score", sort: (r) => r.opportunity, cell: (r) => meter(r.opportunity) },
  { label: "Rug risk", left: true, sort: (r) => r.risk.score, cell: (r) => badge(RISK_BADGE, r.risk.level) },
  { label: "Data", left: true, cell: (r) => badge(CONF_BADGE, r.confidence.level) },
  { label: "Age", sort: (r) => -(r.pool.age_minutes ?? 1e12), cell: (r) => el("span", { text: ageMin(r.pool.age_minutes) }) },
  { label: "Price", cell: (r) => el("span", { text: usd(r.pool.price_usd) }) },
  { label: "Liquidity", sort: (r) => r.pool.liquidity_usd, cell: (r) => el("span", { text: usd(r.pool.liquidity_usd, true) }) },
  { label: "Vol 5m", sort: (r) => r.pool.vol_m5, cell: (r) => el("span", { text: usd(r.pool.vol_m5, true) }) },
  { label: "Accel", sort: (r) => r.accel_5m, cell: (r) => el("span", { text: ok(r.accel_5m) ? nf(1).format(r.accel_5m) + "x" : "n/a" }) },
  { label: "Buyers 5m", sort: (r) => r.pool.buyers_m5, cell: (r) => el("span", { text: num(r.pool.buyers_m5) }) },
  { label: "1h", sort: (r) => r.pool.chg_h1, cell: (r) => pctCell(r.pool.chg_h1) },
];

function scanTable(rows, opts) {
  return dataTable({
    rows, columns: SCAN_COLUMNS, defaultSort: 2, id: (r) => r.key, searchHint: "Search symbol, name or address",
    text: (r) => [r.pool.symbol, r.pool.name, r.pool.token_address, r.pool.pool_address].join(" "),
    spine: (r) => tone(LABEL_BADGE, r.label), expand: scanDetail, empty: opts && opts.empty || "No tokens match. Loosen the filters or wait for new pools.",
    toolbar: opts && opts.toolbar,
  });
}

/* ---------- Command Center ---------- */
async function renderCommand(root, token) {
  root.append(sectionHead("Command Center", "What is happening on " + S.chain + " right now, and what needs attention."));
  const holder = el("div", null, loading("Scanning new pools and trending pools…"));
  root.append(holder);
  let d;
  try { d = await api("/api/command?" + qs({ chain: S.chain })); } catch (err) { if (token === renderToken) holder.replaceChildren(errorBox(err)); return; }
  if (token !== renderToken) return;
  const p = d.paper;
  const mini = (rows, empty) => rows.length
    ? el("div", { class: "scroll" }, el("table", null, el("tbody", null, rows.map((r) => el("tr", { class: "row-main", style: "--spine:" + tone(LABEL_BADGE, r.label) },
      el("td", null, el("a", { href: poolLink(r), text: r.pool.symbol })), el("td", { class: "left" }, badge(LABEL_BADGE, r.label)),
      el("td", null, meter(r.opportunity)), el("td", { class: "left" }, badge(RISK_BADGE, r.risk.level)))))))
    : el("p", { class: "meta", text: empty });
  const themeRows = d.themes.map((t) => el("tr", null, el("td", { class: "left", text: t.theme }), el("td", { text: t.tokens + " tokens" }),
    el("td", { text: usd(t.vol_h1, true) + " /h" }), el("td", null, pctCell(t.avg_change_h1, 0)),
    el("td", { class: "left" }, el("span", { class: "badge " + (t.assessment.market_reaction === "ELEVATED" ? "tone-warning" : "tone-muted"), text: t.assessment.market_reaction }))));
  holder.replaceChildren(...[
    notices(d.warnings),
    el("div", { class: "stats" },
      stat("Paper equity", usd(p.equity), pctText(p.return_pct, 2) + " vs start", p.return_pct >= 0 ? "pos" : "neg"),
      stat("Open positions", String(p.open_positions), "Cash " + usd(p.cash)),
      stat("Realized today", signedUsd(p.realized_today)),
      stat("Alerts unread", String(d.alert_counts.critical + d.alert_counts.high + d.alert_counts.info), d.alert_counts.critical + " critical"),
      stat("Market data", d.freshness.market_data_age_s != null ? Math.round(d.freshness.market_data_age_s) + "s old" : "n/a", "cached up to 90s")),
    el("div", { class: "grid g2 section" },
      panel("Candidates", mini(d.candidates, "No candidate passed every gate. That is common. Most new pools fail on liquidity, buyers or contract risk."), el("a", { href: "#scanner", text: "Open scanner →" })),
      panel("Risky launches to avoid", mini(d.risky_launches, "No flagged launches in the current batch."), el("a", { href: "#rugs", text: "Rug detector →" }))),
    el("div", { class: "grid g2 section" },
      panel("Fresh launches (top score)", mini(d.fresh, "No new pools yet."), el("a", { href: "#launches", text: "All launches →" })),
      panel("Momentum (top score)", mini(d.momentum, "No trending pools yet."))),
    el("div", { class: "section" }, panel("Narratives by 1h volume", themeRows.length ? el("div", { class: "scroll" }, el("table", null, el("tbody", null, themeRows))) : el("p", { class: "meta", text: "No data." }), el("a", { href: "#narratives", text: "Narrative radar →" }))),
  ].filter(Boolean));
}

/* ---------- Scanner (+ New Launches preset) ---------- */
const FILTER_DEFS = [
  ["age", "Age", "select", [["", "Any"], ["lt5", "< 5 min"], ["5_15", "5-15 min"], ["15_60", "15-60 min"], ["1_6h", "1-6 hours"], ["gt6h", "> 6 hours"]]],
  ["liq_min", "Min liquidity $", "number"], ["liq_max", "Max liquidity $", "number"],
  ["position_usd", "My position $", "number"], ["liq_pos_mult", "Liquidity ≥ N× position", "number"],
  ["vol5_min", "Min 5m volume $", "number"], ["buyers5_min", "Min unique buyers 5m", "number"],
  ["accel_min", "Min volume accel (x)", "number"], ["wallets_min", "Min active wallets", "number"],
  ["risk_max", "Max risk score (0-100)", "number"],
  ["conf_min", "Min data confidence", "select", [["", "Any"], ["low", "Low"], ["medium", "Medium"], ["high", "High"]]],
  ["top10_max", "Max top-10 holders %", "number"], ["deployer_max", "Max deployer holding %", "number"], ["chg_min", "Min price change %", "number"],
  ["excl_critical", "Exclude critical risk", "check"], ["excl_failed_sell", "Exclude failed sell test", "check"],
  ["breakout", "Breakout only", "check"], ["holders_up", "Holder growth rising", "check"], ["smart", "Tracked wallets buying", "check"],
  ["clusters", "Cluster-linked wallets", "unsupported"], ["social", "Social attention rising", "unsupported"],
];

function filterForm(values, onApply) {
  const inputs = {};
  const labels = FILTER_DEFS.map(([key, text, kind, opts]) => {
    let input;
    if (kind === "select") { input = el("select", null, opts.map(([v, t]) => el("option", { value: v, text: t }))); input.value = values[key] || ""; }
    else if (kind === "number") { input = el("input", { type: "number", step: "any", min: "0" }); input.value = values[key] || ""; }
    else { input = el("input", { type: "checkbox" }); input.checked = Boolean(values[key]); if (kind === "unsupported") input.disabled = true; }
    inputs[key] = input;
    const cls = kind === "unsupported" ? "check off" : kind === "check" ? "check" : "";
    return el("label", { class: cls, title: kind === "unsupported" ? "Not connected: needs data this build does not have." : "" },
      kind === "select" || kind === "number" ? [text, input] : [input, text + (kind === "unsupported" ? " (not connected)" : "")]);
  });
  const apply = el("button", { class: "btn primary", type: "button", text: "Apply filters" });
  const reset = el("button", { class: "btn", type: "button", text: "Reset" });
  const read = () => {
    const out = {};
    for (const [key, , kind] of FILTER_DEFS) { const i = inputs[key]; if (kind === "check") { if (i.checked) out[key] = "1"; } else if (i.value !== "") out[key] = i.value; }
    return out;
  };
  apply.addEventListener("click", () => onApply(read()));
  reset.addEventListener("click", () => onApply({}));
  return el("details", { class: "panel" }, el("summary", { class: "pad", text: "Filters" }), el("div", { class: "filters" }, labels), el("div", { class: "pad row-actions" }, apply, reset));
}

function scannerPage(preset) {
  return async function render(root, token) {
    const state = { mode: preset ? "fresh" : "fresh", filters: preset ? { age: "lt5" } : {} };
    if (preset) state.filters = {};
    root.append(sectionHead(preset ? "New Launches" : "Token Scanner",
      preset ? "Newest pools on " + S.chain + ", scored. Brand-new pools are capped so they cannot look like sure things."
        : "Find tokens by mode and filter. Unknown data fails a filter you set; it is never treated as passing."));
    const body = el("div");
    const modeBar = el("div", { class: "seg", role: "group", "aria-label": "Scan mode" });
    const run = async () => {
      const mine = ++renderToken; token = mine;
      body.replaceChildren(loading("Scanning, scoring and checking contracts. This can take 20-40 seconds on the first run…"));
      try {
        const d = await api("/api/scan?" + qs({ chain: S.chain, mode: state.mode, ...state.filters }));
        if (mine !== renderToken) return;
        const parts = [notices(d.warnings)];
        const un = Object.values(d.unsupported_filters || {});
        if (un.length) parts.push(notices(un, "Filters not applied (not connected)"));
        parts.push(el("div", { class: "meta" }, "Fetched " + d.counts.fetched + " · after market filters " + d.counts.after_market_filters + " · scored " + d.counts.scored + " · shown " + d.counts.returned + " · market data " + Math.round(d.freshness.market_data_age_s || 0) + "s old"));
        parts.push(scanTable(d.rows));
        body.replaceChildren(...parts.filter(Boolean));
      } catch (err) { if (mine === renderToken) body.replaceChildren(errorBox(err)); }
    };
    if (!preset) {
      for (const [id, label] of MODES) {
        const b = el("button", { type: "button", "aria-pressed": String(id === state.mode), text: label });
        b.addEventListener("click", () => { state.mode = id; modeBar.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", "false")); b.setAttribute("aria-pressed", "true"); help.textContent = MODE_HELP[id]; run(); });
        modeBar.append(b);
      }
    }
    const help = el("p", { class: "meta", text: preset ? "" : MODE_HELP[state.mode] });
    const form = filterForm(state.filters, (f) => { state.filters = f; run(); });
    root.append(...(preset ? [form] : [modeBar, help, form]), el("div", { class: "section" }, body));
    run();
  };
}
const renderLaunches = scannerPage(true);
const renderScanner = scannerPage(false);

/* ---------- Watchlist ---------- */
async function renderWatchlist(root, token) {
  root.append(sectionHead("Watchlist", "Tokens you starred. Risk changes on these raise alerts."));
  const holder = el("div", null, loading("Loading…"));
  root.append(holder);
  try {
    const d = await api("/api/watchlist");
    if (token !== renderToken) return;
    if (!d.items.length) { holder.replaceChildren(el("div", { class: "panel empty", text: "Nothing watched yet. Star a token in the scanner or on its investigation page." })); return; }
    holder.replaceChildren(dataTable({
      rows: d.items, id: (r) => r.key, text: (r) => r.symbol + " " + r.name,
      columns: [
        { label: "Token", left: true, sort: (r) => r.added_at, cell: (r) => el("a", { href: "#investigate/" + r.chain + "/" + r.pool, text: r.symbol || shortAddr(r.token) }) },
        { label: "Name", left: true, cell: (r) => el("span", { class: "sub-text", text: r.name }) },
        { label: "Chain", left: true, cell: (r) => el("span", { text: r.chain }) },
        { label: "Added", sort: (r) => r.added_at, cell: (r) => el("span", { text: ago(r.added_at) }) },
        { label: "", cell: (r) => el("button", { class: "btn small", type: "button", text: "Remove", onclick: async () => { await post("/api/watchlist/remove", { chain: r.chain, id: r.pool }); route(); } }) },
      ],
    }));
  } catch (err) { if (token === renderToken) holder.replaceChildren(errorBox(err)); }
}

/* ---------- Narratives ---------- */
async function renderNarratives(root, token) {
  root.append(sectionHead("Trending Narratives", "Themes grouped from token names and the trading activity under each. Mentions and sentiment are not connected."));
  const holder = el("div", null, loading("Grouping tokens by theme…"));
  root.append(holder);
  try {
    const d = await api("/api/narratives?" + qs({ chain: S.chain }));
    if (token !== renderToken) return;
    holder.replaceChildren(...[notices(d.warnings), el("div", { class: "notice info", text: d.note }),
      dataTable({
        rows: d.themes, id: (r) => r.theme, text: (r) => r.theme, defaultSort: 3,
        columns: [
          { label: "Theme", left: true, sort: (r) => r.tokens, cell: (r) => el("span", { class: "sym", text: r.theme }) },
          { label: "Tokens", sort: (r) => r.tokens, cell: (r) => el("span", { text: String(r.tokens) }) },
          { label: "New (6h)", sort: (r) => r.new_launches, cell: (r) => el("span", { text: String(r.new_launches) }) },
          { label: "1h volume", sort: (r) => r.vol_h1, cell: (r) => el("span", { text: usd(r.vol_h1, true) }) },
          { label: "Volume change", sort: (r) => r.volume_growth_pct, cell: (r) => pctCell(r.volume_growth_pct, 0) },
          { label: "Avg 1h price", sort: (r) => r.avg_change_h1, cell: (r) => pctCell(r.avg_change_h1, 0) },
          { label: "Market reaction", left: true, cell: (r) => el("span", { class: "badge " + (r.assessment.market_reaction === "ELEVATED" ? "tone-warning" : "tone-muted"), text: r.assessment.market_reaction }) },
          { label: "Action", left: true, cell: (r) => el("span", { class: "badge tone-muted", text: r.assessment.action }) },
        ],
        expand: (r) => el("div", { class: "grid g2" },
          el("div", null, el("h3", { text: "Reading" }), bullets(r.assessment.notes), el("p", { class: "meta", text: r.assessment.social })),
          el("div", null, el("h3", { text: "Top tokens by volume" }), el("ul", { class: "clean" }, r.top.map((t) => el("li", null, el("a", { href: "#investigate/" + t.key.replace(":", "/"), text: t.symbol }), el("span", { class: "sub-text", text: " " + usd(t.vol_h1, true) + " /h" })))))),
      })].filter(Boolean));
  } catch (err) { if (token === renderToken) holder.replaceChildren(errorBox(err)); }
}

/* ---------- Token Investigation ---------- */
const INV_TABS = [["overview", "Overview"], ["security", "Security"], ["holders", "Holders"], ["smart", "Smart money"],
  ["liquidity", "Liquidity"], ["news", "News and social"], ["ai", "AI investigation"]];

function searchForm(placeholder, onGo) {
  const input = el("input", { type: "text", class: "wide", placeholder, autocomplete: "off", spellcheck: "false", maxlength: "64", "aria-label": placeholder });
  const form = el("form", { class: "search" }, input, el("button", { class: "btn primary", type: "submit", text: "Investigate" }));
  form.addEventListener("submit", (e) => { e.preventDefault(); if (input.value.trim()) onGo(input.value.trim()); });
  return form;
}

async function renderInvestigate(root, token, params) {
  const [chain, id] = params;
  root.append(sectionHead("Token Investigation", "Paste a token or pool address on " + S.chain + " for the full picture."));
  root.append(searchForm("Token or pool address", (v) => goto("#investigate/" + S.chain + "/" + v)));
  if (!id) { root.append(el("div", { class: "panel empty", text: "Enter an address, or open a token from the scanner." })); return; }
  if (chain && chain !== S.chain) { S.chain = chain; renderStatusBar(); }
  const holder = el("div", null, loading("Collecting market data, contract checks and holders…"));
  root.append(holder);
  let d;
  try { d = await api("/api/investigate?" + qs({ chain: S.chain, id, sections: "overview,security,holders,liquidity,ai,chart", mode: "fresh" })); }
  catch (err) { if (token === renderToken) holder.replaceChildren(errorBox(err)); return; }
  if (token !== renderToken) return;
  const cache = { overview: d.overview, security: d.security, holders: d.holders, liquidity: d.liquidity, ai: d.ai, chart: d.chart };
  const o = d.overview, pool = o.pool;
  const pane = el("div");
  const lazy = async (name) => {
    if (!cache[name]) { pane.replaceChildren(loading("Loading…")); const x = await api("/api/investigate?" + qs({ chain: S.chain, id, sections: name })); cache[name] = x[name]; }
    return cache[name];
  };
  const show = async (tab) => {
    try {
      const data = await lazy(tab === "overview" ? "overview" : tab);
      pane.replaceChildren(INV_RENDER[tab](data, d, id, cache));
    } catch (err) { pane.replaceChildren(errorBox(err)); }
  };
  holder.replaceChildren(...[
    notices(d.warnings),
    el("div", { class: "hero" },
      el("h2", { text: pool.symbol }), el("span", { class: "sub-text", text: pool.name }),
      el("span", { class: "price", text: usd(pool.price_usd) }), pctCell(pool.chg_h1), el("span", { class: "sub-text", text: "1h" }),
      badge(LABEL_BADGE, o.label), badge(RISK_BADGE, o.risk.level), badge(CONF_BADGE, o.confidence.level), meter(o.opportunity),
      freshness(d.fetched_at), watchButton({ pool, watched: false, key: d.key })),
    tabs(INV_TABS, "overview", show), pane].filter(Boolean));
  show("overview");
}

const INV_RENDER = {
  overview(o, d, id, cache) {
    const pool = o.pool;
    const chartBox = el("div", null);
    const drawChart = (c) => chartBox.replaceChildren(...[candleChart(c.candles), c.error ? el("p", { class: "meta", text: "Chart: " + c.error }) : null].filter(Boolean));
    drawChart(cache.chart || { candles: [] });
    const tfBar = el("div", { class: "seg", role: "group", "aria-label": "Timeframe" });
    for (const tf of ["1m", "5m", "15m", "1h", "4h"]) {
      const b = el("button", { type: "button", "aria-pressed": String(tf === "5m"), text: tf });
      b.addEventListener("click", async () => {
        tfBar.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", "false")); b.setAttribute("aria-pressed", "true");
        try { const x = await api("/api/investigate?" + qs({ chain: S.chain, id, sections: "chart", tf })); drawChart(x.chart); } catch (err) { chartBox.replaceChildren(errorBox(err)); }
      });
      tfBar.append(b);
    }
    const stats = kv([
      ["Liquidity", el("span", { text: usd(pool.liquidity_usd) })], ["FDV", el("span", { text: usd(pool.fdv_usd, true) })],
      ["Market cap", el("span", { text: usd(pool.market_cap_usd, true) })], ["Age", el("span", { text: ageMin(pool.age_minutes) })],
      ["Volume 5m / 1h / 24h", el("span", { text: [pool.vol_m5, pool.vol_h1, pool.vol_h24].map((v) => usd(v, true)).join(" / ") })],
      ["Buyers / sellers 1h", el("span", { text: num(pool.buyers_h1) + " / " + num(pool.sellers_h1) })],
      ["Buys / sells 1h", el("span", { text: num(pool.buys_h1) + " / " + num(pool.sells_h1) })],
      ["Change 5m / 1h / 6h / 24h", el("span", { text: [pool.chg_m5, pool.chg_h1, pool.chg_h6, pool.chg_h24].map((v) => pctText(v, 0)).join(" / ") })],
      ["Est. impact of $100 / $1,000", el("span", { text: pctText(o.impact_100, 2).replace("+", "") + " / " + pctText(o.impact_1000, 2).replace("+", "") })],
      ["Cross-checked on DexScreener", el("span", { text: o.dexscreener_corroborated ? "Yes" : "No" })],
    ]);
    const links = [safeLink(o.explorer && o.explorer + pool.token_address, "Explorer"), safeLink("https://www.geckoterminal.com/" + S.chain + "/pools/" + pool.pool_address, "GeckoTerminal")].filter(Boolean);
    return el("div", null,
      splitPane(panel("Price and volume", [el("div", { class: "row-actions" }, tfBar), chartBox]),
        el("div", { class: "grid" }, panel("Snapshot", stats), paperTicket(d.key, pool), el("div", { class: "row-actions" }, links)), "inv"),
      o.notes.length ? el("div", { class: "section" }, bullets(o.notes, "i")) : null,
      o.caps.length ? el("div", { class: "section" }, bullets(o.caps.map((c) => "Score capped at " + c.cap + ": " + c.reason), "⛔")) : null);
  },

  security(s) {
    const f = s.fields || {};
    const flag = (v, bad) => v === null || v === undefined ? el("span", { class: "badge tone-muted", text: "Unknown" }) : el("span", { class: "badge " + ((v ? bad : !bad) ? "tone-critical" : "tone-good"), text: v ? "Yes" : "No" });
    const rows = s.chain_name === "Solana" ? [
      ["Mint authority active", flag(f.mint_authority_active, true)], ["Freeze authority active", flag(f.freeze_authority_active, true)],
      ["Closable", flag(f.closable, true)], ["Balance mutable by authority", flag(f.balance_mutable, true)],
      ["Transfer hook", flag(f.transfer_hook, true)], ["Non-transferable", flag(f.non_transferable, true)], ["Metadata mutable", flag(f.metadata_mutable, true)],
    ] : [
      ["Contract verified", flag(f.contract_verified, false)], ["Proxy contract", flag(f.proxy, true)], ["Mintable", flag(f.mintable, true)],
      ["Hidden owner", flag(f.hidden_owner, true)], ["Buy tax", el("span", { text: ok(f.buy_tax_pct) ? nf(1).format(f.buy_tax_pct) + "%" : "Unknown" })],
      ["Sell tax", el("span", { text: ok(f.sell_tax_pct) ? nf(1).format(f.sell_tax_pct) + "%" : "Unknown" })],
    ];
    const sim = f.sell_simulation || {};
    rows.push(["Sell simulation", el("span", { class: "badge " + (sim.available ? (sim.passed ? "tone-good" : "tone-critical") : "tone-muted"), text: sim.available ? (sim.passed ? "Passed" : "Failed") : "Not available" })]);
    rows.push(["Top-10 holders", el("span", { text: ok(f.top10_pct) ? nf(1).format(f.top10_pct) + "%" : "Unknown" })]);
    rows.push(["LP locked / burned", el("span", { text: ok(f.lp_locked_pct) ? nf(0).format(f.lp_locked_pct) + "%" : "Unknown" })]);
    return el("div", { class: "grid g2" },
      panel("Contract checks (" + s.chain_name + ")", [s.available ? null : el("div", { class: "notice error", text: "No security data came back. Unknown is treated as risky, never as safe." }), kv(rows),
        sim.note ? el("p", { class: "meta", text: sim.note }) : null, el("p", { class: "meta", text: "Source: " + s.provider + ". Checks differ by chain because the underlying contracts differ." })]),
      panel("Risk: " + s.risk.summary, [badge(RISK_BADGE, s.risk.level), riskFlags(s.risk.flags)]));
  },

  holders(h) {
    if (!h.available) return el("div", { class: "notice", text: "No holder data came back for this token." });
    return el("div", { class: "grid" },
      el("div", { class: "stats" }, stat("Top-10 share", ok(h.top10_pct) ? nf(1).format(h.top10_pct) + "%" : "n/a", "excluding pool, burn and locked where labelled"),
        stat("Holders", num(h.holder_count)), stat("Holder growth", pctText(h.growth_pct, 1), "needs time to build")),
      dataTable({
        rows: h.rows, text: (r) => r.address + " " + (r.label || ""), searchHint: "Search address or label",
        columns: [
          { label: "Address", left: true, cell: (r) => el("span", { class: "mono", text: shortAddr(r.address) }) },
          { label: "Share", sort: (r) => r.percent, cell: (r) => el("span", { text: ok(r.percent) ? nf(2).format(r.percent) + "%" : "n/a" }) },
          { label: "Type", left: true, cell: (r) => el("span", { class: "badge tone-muted", text: r.label || "wallet" }) },
        ],
      }), el("p", { class: "meta", text: h.note }));
  },

  smart(s) {
    if (!s || !s.available) return el("div", { class: "notice", text: (s && s.reason) || "Trades are not available." });
    return el("div", { class: "grid" },
      el("div", { class: "stats" }, stat("State", s.state), stat("Tracked buyers", String(s.tracked_buyers), s.tracked_count + " wallets tracked"),
        stat("Tracked net flow", signedUsd(s.net_usd), "buy " + usd(s.buy_usd) + " / sell " + usd(s.sell_usd)),
        stat("Large trades (≥$2k)", String(s.large_count), "buys " + usd(s.large_buy_usd, true) + " / sells " + usd(s.large_sell_usd, true))),
      panel("Tracked wallet trades (" + s.window + ")", s.tracked_trades.length ? dataTable({ rows: s.tracked_trades, columns: [
        { label: "Wallet", left: true, cell: (r) => el("span", null, el("span", { class: "sym", text: r.label || "" }), el("span", { class: "mono", text: " " + shortAddr(r.wallet) })) },
        { label: "Side", left: true, cell: (r) => el("span", { class: "badge " + (r.kind === "buy" ? "tone-good" : "tone-critical"), text: r.kind }) },
        { label: "USD", cell: (r) => el("span", { text: usd(r.usd) }) }, { label: "Time", cell: (r) => el("span", { text: r.time ? new Date(r.time).toLocaleTimeString() : "" }) }] }) : el("p", { class: "meta", text: "No tracked wallet traded in the window." })),
      bullets(s.limits, "i"));
  },

  liquidity(l) {
    const hist = (l.history || []).map((p) => ({ x: p.t, y: p.liquidity }));
    return el("div", { class: "grid g2" },
      panel("Pool depth", [kv([["Liquidity", el("span", { text: usd(l.liquidity_usd) })], ["FDV / liquidity", el("span", { text: ok(l.fdv_to_liquidity) ? nf(1).format(l.fdv_to_liquidity) + "x" : "n/a" })],
        ["24h turnover", el("span", { text: ok(l.turnover_24h) ? nf(1).format(l.turnover_24h) + "x liquidity" : "n/a" })], ["LP locked / burned", el("span", { text: ok(l.lp_locked_pct) ? nf(0).format(l.lp_locked_pct) + "%" : "unknown" })]]),
        el("h3", { text: "Liquidity over time" }), lineChart(hist, { fmt: (v) => usd(v, true), label: "Liquidity history", empty: "Not enough readings yet." }),
        el("p", { class: "meta", text: ok(l.history_change_pct) ? "Change since first reading: " + pctText(l.history_change_pct, 0) : l.history_note })]),
      panel("Estimated cost to exit", [el("table", null, el("tbody", null, l.exit_table.map((r) => el("tr", null, el("td", { class: "left", text: "Sell " + usd(r.usd) }), el("td", { class: ok(r.impact_pct) && r.impact_pct > 5 ? "neg" : "", text: ok(r.impact_pct) ? nf(2).format(r.impact_pct) + "% price impact" : "unknown" }))))),
        el("p", { class: "meta", text: l.exit_note })]));
  },

  news(n) {
    return el("div", { class: "grid" },
      n.available ? el("div", { class: "meta", text: n.summary.total ? n.summary.total + " headlines, tone " + n.summary.tone + "." : "No headlines found. Normal for a new token." }) : el("div", { class: "notice", text: n.reason }),
      ...(n.headlines || []).map((item) => el("div", { class: "news-item" },
        el("div", null, (item.tags || []).map((t) => el("span", { class: "tag" + (t === "negative" ? " bad" : ""), text: t.replace("_", " ") })), safeLink(item.link, item.title) || item.title),
        el("div", { class: "sub-text", text: [item.source, item.published ? new Date(item.published).toLocaleDateString() : ""].filter(Boolean).join(", ") }))),
      el("div", { class: "notice info", text: n.social }), n.note ? el("p", { class: "meta", text: n.note }) : null);
  },

  ai(a) {
    return el("div", { class: "ai-card" },
      el("h3", { text: "AI investigation (rule-based)" }),
      el("p", null, el("strong", { text: a.verdict }), el("span", { class: "sub-text", text: "  Score " + num(a.score) + ", data confidence " + a.confidence })),
      el("div", { class: "grid g2" },
        el("div", null, el("h3", { text: "Why it was selected" }), bullets(a.why_selected, "▲"), el("h3", { text: "Risks" }), bullets(a.risks, "!")),
        el("div", null, el("h3", { text: "What would change the view" }), bullets(a.invalidation, "↯"), el("h3", { text: "What is unknown" }), bullets(a.unknown, "?"))),
      el("h3", { text: "Indicators that contributed" }), a.indicators.map((c) => barRow(c.name, c.points, c.max, c.detail, c.points < 0)),
      el("p", { class: "meta", text: "Sources: " + a.sources.join(", ") + ". Updated " + clockTime(a.updated_at) + "." }), el("p", { class: "meta", text: a.disclaimer }));
  },
};

/* Paper-trade ticket: simulated only. */
function paperTicket(key, pool) {
  const usdIn = el("input", { type: "number", min: "1", step: "any", value: "100", "aria-label": "Position size in USD" });
  const tp = el("input", { type: "number", placeholder: "TP %", min: "1", "aria-label": "Take profit percent" });
  const sl = el("input", { type: "number", placeholder: "SL %", min: "1", "aria-label": "Stop loss percent" });
  const tr = el("input", { type: "number", placeholder: "Trail %", min: "1", "aria-label": "Trailing stop percent" });
  const out = el("div");
  const btn = el("button", { class: "btn primary", type: "button", text: "Paper buy" });
  btn.addEventListener("click", async () => {
    if (!(await confirmDialog("Simulated buy of $" + usdIn.value + " of " + pool.symbol + ". No real money moves.", "Run paper buy"))) return;
    btn.disabled = true;
    try {
      const r = await post("/api/paper/buy", { chain: pool.chain, pool: pool.pool_address, usd: Number(usdIn.value), tp_pct: tp.value, sl_pct: sl.value, trail_pct: tr.value, reason: "manual from investigation" });
      out.replaceChildren(r.ok ? el("div", { class: "notice info", text: "Paper position opened at " + usd(r.fill_price) + " with " + nf(2).format(r.slippage_pct) + "% modelled slippage." })
        : el("div", { class: "notice error" }, el("strong", { text: "Refused" }), bullets(r.problems, "✕")));
      refreshMeta();
    } catch (err) { out.replaceChildren(errorBox(err)); }
    btn.disabled = false;
  });
  return panel("Paper trade ticket", [el("p", { class: "meta", text: "Simulated fills with fees and price impact. Limits from Risk Management apply." }),
    el("div", { class: "row-actions" }, usdIn, tp, sl, tr, btn), out], null, "ai-ticket");
}
