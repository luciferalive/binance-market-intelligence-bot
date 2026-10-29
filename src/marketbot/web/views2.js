"use strict";

/* Intelligence, trading, research and system pages. */

async function pageLoad(root, token, path, build, wait) {
  const holder = el("div", null, loading(wait || "Loading…"));
  root.append(holder);
  try {
    const d = await api(path);
    if (token !== renderToken) return;
    holder.replaceChildren(...[].concat(build(d)).filter(Boolean));
  } catch (err) { if (token === renderToken) holder.replaceChildren(errorBox(err)); }
}

function planned(title, why) {
  return (root) => root.append(sectionHead(title, "Planned. Not built in this version."),
    el("div", { class: "panel pad" }, el("p", { text: why }), el("p", { class: "meta", text: "It is listed so the navigation matches the plan. Nothing here is faked." })));
}

/* ---------- Rug Pull Detector ---------- */
function renderRugs(root, token) {
  root.append(sectionHead("Rug Pull Detector", "Newest pools sorted worst-first. Missing security data counts as risky. A low score means no flag was found, not that the token is safe."));
  return pageLoad(root, token, "/api/rugs?" + qs({ chain: S.chain }), (d) => [notices(d.warnings), scanTable(d.rows, { empty: "No pools to check right now." })],
    "Checking contracts on the newest pools…");
}

/* ---------- Smart Money ---------- */
async function renderSmart(root, token) {
  root.append(sectionHead("Smart Money", "Track wallets you trust and see what they trade. Only wallets you add are followed; profit history and clusters are not connected."));
  const holder = el("div", null, loading("Loading…"));
  root.append(holder);
  const draw = async () => {
    const d = await api("/api/wallets?" + qs({ chain: S.chain }));
    const addr = el("input", { type: "text", placeholder: "Wallet address", maxlength: "64", "aria-label": "Wallet address" });
    const label = el("input", { type: "text", placeholder: "Label (optional)", maxlength: "40", "aria-label": "Label" });
    const add = el("button", { class: "btn primary", type: "button", text: "Track wallet" });
    add.addEventListener("click", async () => {
      try { await post("/api/wallets/add", { chain: S.chain, address: addr.value.trim(), label: label.value }); draw(); } catch (err) { toast(err.message, true); }
    });
    holder.replaceChildren(
      panel("Add a wallet (" + S.chain + ")", el("div", { class: "row-actions" }, addr, label, add)),
      el("div", { class: "section" }, d.items.length ? dataTable({ rows: d.items, text: (r) => r.address + " " + r.label, columns: [
        { label: "Label", left: true, cell: (r) => el("span", { class: "sym", text: r.label || "unnamed" }) },
        { label: "Address", left: true, cell: (r) => el("span", { class: "mono", text: r.address }) },
        { label: "Added", sort: (r) => r.added_at, cell: (r) => el("span", { text: ago(r.added_at) }) },
        { label: "", cell: (r) => el("button", { class: "btn small", type: "button", text: "Remove", onclick: async () => { await post("/api/wallets/remove", { chain: S.chain, address: r.address }); draw(); } }) }] })
        : el("div", { class: "panel empty", text: "No wallets tracked. Add some, then run the Smart money scanner mode or open a token's Smart money tab." })),
      bullets(d.limits, "i"));
  };
  try { await draw(); } catch (err) { holder.replaceChildren(errorBox(err)); }
}

/* ---------- News Radar ---------- */
function renderNews(root) {
  root.append(sectionHead("News Radar", "Search crypto headlines by token or topic. Social media monitoring is not connected."));
  const out = el("div");
  root.append(searchForm("Token name, ticker or topic", async (q) => {
    out.replaceChildren(loading("Searching…"));
    try {
      const d = await api("/api/news?q=" + encodeURIComponent(q));
      out.replaceChildren(el("p", { class: "meta", text: d.summary.total ? d.summary.total + " headlines, tone " + d.summary.tone : "No headlines found." }),
        ...d.headlines.map((h) => el("div", { class: "news-item" }, el("div", null, (h.tags || []).map((t) => el("span", { class: "tag" + (t === "negative" ? " bad" : ""), text: t.replace("_", " ") })), safeLink(h.link, h.title) || h.title),
          el("div", { class: "sub-text", text: [h.source, h.published ? new Date(h.published).toLocaleDateString() : ""].filter(Boolean).join(", ") }))));
    } catch (err) { out.replaceChildren(errorBox(err)); }
  }), el("div", { class: "notice info", text: "Not connected: social posts, mention counts and influencer activity." }), out);
}

/* ---------- Trading terminal, positions, orders ---------- */
function summaryStats(p) {
  return el("div", { class: "stats" },
    stat("Mode", "PAPER", "Live trading is not implemented", "warn"), stat("Equity", usd(p.equity), pctText(p.return_pct, 2), p.return_pct >= 0 ? "pos" : "neg"),
    stat("Cash", usd(p.cash)), stat("Open value", usd(p.open_value), p.open_positions + " positions"),
    stat("Realized P&L", signedUsd(p.realized_pnl), p.trades + " closed, win rate " + (ok(p.win_rate) ? nf(0).format(p.win_rate) + "%" : "n/a")),
    stat("Emergency stop", p.halted ? "ACTIVE" : "Off", p.halted ? p.halt_reason : "Entries allowed", p.halted ? "neg" : ""));
}

function positionsTable(positions) {
  if (!positions.length) return el("div", { class: "panel empty", text: "No open paper positions." });
  return dataTable({
    rows: positions, id: (r) => r.key, text: (r) => r.symbol, defaultSort: 3,
    columns: [
      { label: "Token", left: true, cell: (r) => el("a", { href: "#investigate/" + r.chain + "/" + r.pool, text: r.symbol }) },
      { label: "Value", sort: (r) => r.value_usd, cell: (r) => el("span", { text: usd(r.value_usd) }) },
      { label: "Cost", sort: (r) => r.cost_usd, cell: (r) => el("span", { text: usd(r.cost_usd) }) },
      { label: "P&L", sort: (r) => r.pnl_usd, cell: (r) => el("span", { class: r.pnl_usd >= 0 ? "pos" : "neg", text: signedUsd(r.pnl_usd) + " (" + pctText(r.pnl_pct, 1) + ")" }) },
      { label: "Exit impact", sort: (r) => r.exit_impact_pct, cell: (r) => el("span", { text: ok(r.exit_impact_pct) ? nf(1).format(r.exit_impact_pct) + "%" : "unknown" }) },
      { label: "Rules", left: true, cell: (r) => el("span", { class: "sub-text", text: [r.tp_pct && "TP " + r.tp_pct + "%", r.sl_pct && "SL " + r.sl_pct + "%", r.trail_pct && "trail " + r.trail_pct + "%", r.max_hold_min && r.max_hold_min + "m max"].filter(Boolean).join(" · ") || "none" }) },
      { label: "Price age", cell: (r) => freshness(r.last_ts, 600) },
      { label: "", cell: (r) => el("button", { class: "btn small", type: "button", text: "Close (paper)", onclick: async () => {
        if (!(await confirmDialog("Close the paper position in " + r.symbol + "?", "Close"))) return;
        try { const x = await post("/api/paper/sell", { key: r.key }); toast(x.ok ? "Closed. P&L " + signedUsd(x.pnl) : x.problems.join(" ")); route(); } catch (err) { toast(err.message, true); }
      } }) },
    ],
  });
}

function renderTerminal(root, token) {
  root.append(sectionHead("Trading Terminal", "Paper trading only. Fills include fees and modelled price impact. Real orders cannot be sent from this build."));
  root.append(searchForm("Token or pool address to open a paper ticket", (v) => goto("#investigate/" + S.chain + "/" + v)));
  return pageLoad(root, token, "/api/paper", (d) => [summaryStats(d.summary), el("div", { class: "section" }, panel("Open positions", positionsTable(d.positions))),
    el("div", { class: "notice info", text: d.summary.live_trading })]);
}
function renderPositions(root, token) {
  root.append(sectionHead("Open Positions", "Simulated positions, refreshed by the background watcher and when you open this page."));
  return pageLoad(root, token, "/api/paper", (d) => [summaryStats(d.summary), positionsTable(d.positions)]);
}
function renderOrders(root, token) {
  root.append(sectionHead("Order History", "Every simulated fill, including modelled fees and slippage."));
  return pageLoad(root, token, "/api/orders", (d) => d.items.length ? dataTable({ rows: d.items, text: (r) => r.symbol + " " + r.reason, defaultSort: 0, columns: [
    { label: "Time", left: true, sort: (r) => r.ts, cell: (r) => el("span", { text: new Date(r.ts * 1000).toLocaleString() }) },
    { label: "Token", left: true, cell: (r) => el("span", { class: "sym", text: r.symbol }) },
    { label: "Side", left: true, cell: (r) => el("span", { class: "badge " + (r.side === "buy" ? "tone-good" : "tone-warning"), text: r.side }) },
    { label: "USD", sort: (r) => r.usd, cell: (r) => el("span", { text: usd(r.usd) }) }, { label: "Fill price", cell: (r) => el("span", { text: usd(r.price) }) },
    { label: "Fee", cell: (r) => el("span", { text: usd(r.fee_usd) }) }, { label: "Slippage", sort: (r) => r.slippage_pct, cell: (r) => el("span", { text: nf(2).format(r.slippage_pct) + "%" }) },
    { label: "P&L", sort: (r) => r.realized_pnl, cell: (r) => r.realized_pnl === null ? el("span", { text: "" }) : el("span", { class: r.realized_pnl >= 0 ? "pos" : "neg", text: signedUsd(r.realized_pnl) }) },
    { label: "Reason", left: true, cell: (r) => el("span", { class: "sub-text", text: r.reason || "" }) }] })
    : el("div", { class: "panel empty", text: "No orders yet." }));
}

/* ---------- Risk Management (limits + emergency stop) ---------- */
const RISK_FIELDS = [["paper_capital_usd", "Paper capital $"], ["max_position_usd", "Max position $"], ["max_open_positions", "Max open positions"],
  ["max_slippage_pct", "Max modelled slippage %"], ["min_liquidity_multiple", "Min liquidity ÷ position (x)"], ["max_daily_loss_pct", "Daily loss limit %"],
  ["fee_pct", "Pool fee %"], ["network_fee_usd", "Network fee $"], ["max_price_age_s", "Max price age (s)"]];

function renderRisk(root, token) {
  root.append(sectionHead("Risk Management", "Limits enforced before every simulated entry. The emergency stop blocks new entries immediately; exits still work."));
  return pageLoad(root, token, "/api/settings", (d) => {
    const inputs = {};
    const form = el("div", { class: "filters" }, RISK_FIELDS.map(([k, t]) => { const i = el("input", { type: "number", step: "any" }); i.value = d.settings[k]; inputs[k] = i; return el("label", null, t, i); }));
    const save = el("button", { class: "btn primary", type: "button", text: "Save limits", onclick: async () => {
      const body = {}; for (const [k] of RISK_FIELDS) body[k] = Number(inputs[k].value);
      try { await post("/api/settings", body); toast("Limits saved."); } catch (err) { toast(err.message, true); }
    } });
    const stop = el("button", { class: "btn stop" + (d.settings.halted ? " on" : ""), type: "button", text: d.settings.halted ? "Resume entries" : "EMERGENCY STOP", onclick: async () => {
      await post(d.settings.halted ? "/api/resume" : "/api/halt", { reason: "manual emergency stop" }); await refreshMeta(); route();
    } });
    return [panel("Emergency stop", [el("p", { text: d.settings.halted ? "Stopped: " + d.settings.halt_reason : "Entries are allowed." }), stop]),
      el("div", { class: "section" }, panel("Limits", [form, el("div", { class: "row-actions" }, save), el("p", { class: "meta", text: "Each value has a safe range; out-of-range values are refused." })]))];
  });
}

/* ---------- Journal and performance ---------- */
function renderJournal(root, token) {
  root.append(sectionHead("Trade Journal", "Automatic log of entries, exits, refusals and stops. Notes are not editable in this version."));
  return pageLoad(root, token, "/api/journal", (d) => d.items.length ? dataTable({ rows: d.items, text: (r) => r.event + " " + r.symbol, columns: [
    { label: "Time", left: true, sort: (r) => r.ts, cell: (r) => el("span", { text: new Date(r.ts * 1000).toLocaleString() }) },
    { label: "Event", left: true, cell: (r) => el("span", { class: "badge tone-muted", text: r.event.replace("_", " ") }) },
    { label: "Token", left: true, cell: (r) => el("span", { text: r.symbol || "" }) },
    { label: "Detail", left: true, cell: (r) => el("span", { class: "sub-text", text: Object.entries(r.detail || {}).map(([k, v]) => k + ": " + (typeof v === "number" ? nf(2).format(v) : Array.isArray(v) ? v.join("; ") : v)).join(" · ") }) }] })
    : el("div", { class: "panel empty", text: "Nothing logged yet." }));
}
function renderPerformance(root, token) {
  root.append(sectionHead("Performance Analytics", "Simulated results only."));
  return pageLoad(root, token, "/api/performance", (d) => [summaryStats(d.summary),
    el("div", { class: "stats section" }, stat("Wins / losses", d.wins + " / " + d.losses), stat("Average win", signedUsd(d.avg_win)), stat("Average loss", signedUsd(d.avg_loss)), stat("Max drawdown", signedUsd(d.max_drawdown), "of closed-trade P&L")),
    el("div", { class: "section" }, panel("Closed-trade P&L curve", lineChart(d.equity_curve.map((p) => ({ x: p.t, y: p.pnl })), { fmt: signedUsd, height: 180, empty: "Close some paper trades to see the curve." }))),
    el("p", { class: "meta", text: d.note })]);
}

/* ---------- Alerts ---------- */
function renderAlerts(root, token) {
  root.append(sectionHead("Alerts", "Critical alerts cannot be muted. Telegram or webhook delivery is configured in .env."));
  return pageLoad(root, token, "/api/alerts", (d) => {
    const test = el("button", { class: "btn", type: "button", text: "Send test alert", onclick: async () => {
      const r = await post("/api/alerts/test", {}); toast(r.ok ? "Test sent." : r.message || JSON.stringify(r.results || {}), !r.ok);
    } });
    const read = el("button", { class: "btn", type: "button", text: "Mark all read", onclick: async () => { await post("/api/alerts/read", {}); refreshMeta(); route(); } });
    return [el("div", { class: "stats" }, stat("Telegram", d.channels.telegram ? "Configured" : "Not set"), stat("Webhook", d.channels.webhook ? "Configured" : "Not set"),
      stat("Unread", String(d.counts.critical + d.counts.high + d.counts.info), d.counts.critical + " critical")),
      el("div", { class: "row-actions" }, test, read),
      d.items.length ? dataTable({ rows: d.items, text: (r) => r.title + " " + r.kind, columns: [
        { label: "Level", left: true, sort: (r) => r.ts, cell: (r) => el("span", { class: "badge " + ({ critical: "tone-critical", high: "tone-warning", info: "tone-info" }[r.level]), text: r.level }) },
        { label: "Alert", left: true, cell: (r) => el("span", null, el("strong", { text: r.title }), el("span", { class: "sub-text", text: "  " + (r.body || "") })) },
        { label: "Sent", left: true, cell: (r) => el("span", { class: "sub-text", text: r.sent_external ? "delivered" : "in app" }) },
        { label: "When", sort: (r) => r.ts, cell: (r) => el("span", { text: ago(r.ts) }) }] }) : el("div", { class: "panel empty", text: "No alerts yet." })];
  });
}

/* ---------- Sources, health, settings ---------- */
function renderSources(root, token) {
  root.append(sectionHead("Data Sources", "Which data each chain really has. Unavailable means not connected, never estimated."));
  return pageLoad(root, token, "/api/capabilities", (d) => {
    const names = Object.keys(d.labels);
    return [el("div", { class: "panel scroll" }, el("table", null,
      el("thead", null, el("tr", null, el("th", { class: "left", text: "Capability" }), d.chains.map((c) => el("th", { text: c.name })))),
      el("tbody", null, names.map((n) => el("tr", null, el("td", { class: "left", text: d.labels[n] }), d.chains.map((c) => el("td", null, el("span", { class: "badge " + (c.capabilities[n] ? "tone-good" : "tone-muted"), text: c.capabilities[n] ? "Yes" : "No" })))))))),
      bullets(d.notes, "i")];
  });
}
function renderHealth(root, token) {
  root.append(sectionHead("System Health", "Source status, rate limiting and the background watcher."));
  return pageLoad(root, token, "/api/health", (d) => [
    el("div", { class: "stats" }, stat("Status", d.status, "", d.status === "OK" ? "pos" : "warn"), stat("Uptime", nf(0).format(d.uptime_s / 60) + " min"),
      stat("Watcher", d.watcher.running ? "Running" : d.watcher.enabled ? "Enabled, idle" : "Off", d.watcher.at ? "last run " + ago(d.watcher.at) + (d.watcher.error ? " (" + d.watcher.error + ")" : "") : "not run yet"),
      stat("Snapshots stored", String(d.database.snapshots), d.database.path)),
    el("div", { class: "section" }, d.sources.length ? dataTable({ title: "Sources", rows: d.sources, text: (r) => r.name, columns: [
      { label: "Source", left: true, cell: (r) => el("span", { class: "sym", text: r.name }) }, { label: "OK", cell: (r) => el("span", { text: String(r.ok) }) },
      { label: "Errors", sort: (r) => r.errors, cell: (r) => el("span", { class: r.errors ? "warn" : "", text: String(r.errors) }) },
      { label: "Avg latency", cell: (r) => el("span", { text: ok(r.avg_latency_ms) ? Math.round(r.avg_latency_ms) + " ms" : "n/a" }) },
      { label: "Last OK", cell: (r) => freshness(r.last_ok_ts || null, 900) }, { label: "Last error", left: true, cell: (r) => el("span", { class: "sub-text", text: r.last_error || "" }) }] })
      : el("div", { class: "panel empty", text: "No requests yet." })),
    bullets(d.notes, "i")]);
}
function renderSettings(root, token) {
  root.append(sectionHead("Settings", "Switches that are safe to change here. Keys and tokens live in .env and are never shown."));
  return pageLoad(root, token, "/api/settings", (d) => {
    const watcher = el("input", { type: "checkbox" }); watcher.checked = d.settings.watcher_enabled;
    const interval = el("input", { type: "number", min: "60", value: String(d.settings.watcher_interval_s) });
    const level = el("select", null, ["info", "high", "critical"].map((v) => el("option", { value: v, text: v }))); level.value = d.settings.alert_telegram_min_level;
    const save = el("button", { class: "btn primary", type: "button", text: "Save", onclick: async () => {
      try { await post("/api/settings", { watcher_enabled: watcher.checked, watcher_interval_s: Number(interval.value), alert_telegram_min_level: level.value }); toast("Saved."); } catch (err) { toast(err.message, true); }
    } });
    return [panel("Background watcher and alerts", [el("div", { class: "filters" },
      el("label", { class: "check" }, watcher, "Re-scan in the background and raise alerts"), el("label", null, "Scan interval (seconds, min 60)", interval), el("label", null, "Send to Telegram/webhook from level", level)),
      el("div", { class: "row-actions" }, save), el("p", { class: "meta", text: "Critical alerts are always sent when a channel is configured." })])];
  });
}

const PLANNED = {
  clusters: ["Wallet Clusters", "Needs funding-flow data between wallets. No provider is connected, and guessing clusters would produce false accusations."],
  lab: ["Strategy Lab", "Rule building and replay need stored history. This app records snapshots from now on, so there is nothing to test against yet."],
  backtest: ["Backtesting", "Needs historical pool and holder data this build does not have. The original CLI backtester covers Binance spot candles only."],
};
