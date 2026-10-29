"use strict";

/* Shell: sidebar, status bar, alerts panel, router. Runs last. */

const ROUTES = {
  command: renderCommand, launches: renderLaunches, scanner: renderScanner, watchlist: renderWatchlist, narratives: renderNarratives,
  investigate: renderInvestigate, rugs: renderRugs, smart: renderSmart, news: renderNews,
  terminal: renderTerminal, positions: renderPositions, orders: renderOrders, risk: renderRisk,
  journal: renderJournal, performance: renderPerformance, alerts: renderAlerts, sources: renderSources, health: renderHealth,
  settings: renderSettings, portfolio: renderPortfolio, pumps: renderPumps,
  ...Object.fromEntries(Object.entries(PLANNED).map(([k, [t, why]]) => [k, planned(t, why)])),
};

function parseHash() {
  const [name, ...rest] = (location.hash || "#command").slice(1).split("/");
  return { name: ROUTES[name] ? name : "command", params: rest.map((x) => { try { return decodeURIComponent(x); } catch (e) { return ""; } }) };
}

function renderNav() {
  const nav = document.getElementById("nav");
  const current = parseHash().name;
  nav.replaceChildren();
  const unread = S.meta ? S.meta.alert_counts.critical + S.meta.alert_counts.high : 0;
  for (const group of S.meta.nav) {
    nav.append(el("div", { class: "nav-sec", text: group.section }));
    for (const item of group.items) {
      const link = el("a", { class: "nav-link" + (item.ready ? "" : " planned"), href: "#" + item.id, "aria-current": item.id === current ? "page" : null },
        el("span", { text: item.label }),
        item.ready ? (item.id === "alerts" && unread ? el("span", { class: "nav-count", text: String(unread) }) : null) : el("span", { class: "nav-tag", text: "planned" }));
      nav.append(link);
    }
  }
  const pf = el("a", { class: "nav-link", href: "#pumps", "aria-current": current === "pumps" ? "page" : null }, el("span", { text: "Binance Pump Watch" }));
  nav.append(pf);
}

function renderStatusBar() {
  const m = S.meta, bar = document.getElementById("statusbar");
  const chain = el("select", { class: "sel", "aria-label": "Chain" }, m.chains.map((c) => el("option", { value: c.id, text: c.name })));
  chain.value = S.chain;
  chain.addEventListener("change", async () => { S.chain = chain.value; try { await post("/api/settings", { chain: S.chain }); } catch (e) { toast(e.message, true); } route(); });
  const worst = m.sources.some((s) => s.errors > 0 && s.ok === 0);
  const idle = m.sources.length === 0;
  const stop = el("button", { class: "btn stop" + (m.halted ? " on" : ""), type: "button", text: m.halted ? "STOPPED · resume" : "EMERGENCY STOP" });
  stop.addEventListener("click", async () => {
    if (!m.halted && !(await confirmDialog("Block all new paper entries now? Exits still work.", "Stop entries"))) return;
    try { await post(m.halted ? "/api/resume" : "/api/halt", { reason: "manual emergency stop" }); await refreshMeta(); } catch (e) { toast(e.message, true); }
  });
  bar.replaceChildren(
    chain,
    el("span", { class: "pill paper", title: "Real trading is not implemented" }, el("span", { class: "dot" }), "PAPER MODE"),
    el("span", { class: "pill " + (worst ? "bad" : idle ? "" : "ok"), title: "Data source status" }, el("span", { class: "dot" }), worst ? "Data source down" : idle ? "Idle" : "Sources OK"),
    el("span", { class: "pill" }, "Alerts: " + (m.alert_channels.telegram || m.alert_channels.webhook ? "external on" : "in-app only")),
    el("span", { class: "spacer" }), stop);
}

function renderAlertsPanel(items) {
  const box = document.getElementById("alerts-panel");
  box.replaceChildren(el("h3", { text: "Live alerts" }),
    ...(items.length ? items.slice(0, 12).map((a) => el("div", { class: "alert-item " + a.level },
      el("div", { class: "t", text: a.title }), a.body ? el("div", { class: "b", text: a.body }) : null, el("div", { class: "w", text: a.level + " · " + ago(a.ts) })))
      : [el("p", { class: "meta", text: "Nothing yet. Critical flags on watched tokens and held positions show up here." })]));
}

async function refreshMeta() {
  S.meta = await api("/api/meta");
  S.chain = S.meta.chain;
  renderNav(); renderStatusBar();
  try { renderAlertsPanel((await api("/api/alerts")).items); } catch (e) { /* panel is optional */ }
}

async function route() {
  const { name, params } = parseHash();
  const token = ++renderToken;
  renderNav();
  view.replaceChildren();
  view.scrollTop = 0;
  try { await ROUTES[name](view, token, params); } catch (err) { if (token === renderToken) view.replaceChildren(errorBox(err)); }
}

(async function boot() {
  try { await refreshMeta(); } catch (err) { view.replaceChildren(errorBox(err)); return; }
  window.addEventListener("hashchange", route);
  setInterval(() => { refreshMeta().catch(() => {}); }, 60000);
  route();
})();
