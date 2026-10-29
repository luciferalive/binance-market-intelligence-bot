"use strict";
/* Binance portfolio and Binance pump/dump watch, carried over from the first dashboard. */
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

const PUMP_BADGE = {
  PUMP_STARTING: ["tone-good", "▲", "Pump starting"],
  BUILDING: ["tone-info", "◔", "Building"],
  PUMP_EXTENDED: ["tone-warning", "!", "Already extended"],
  DUMP_RISK: ["tone-serious", "▼", "Dump risk"],
  DUMPING: ["tone-critical", "▼", "Dumping"],
  QUIET: ["tone-muted", "–", "Quiet"],
};
function spine(table, key) {
  const tone = (table[key] || ["tone-muted"])[0].replace("tone-", "");
  const map = { critical: "var(--critical)", serious: "var(--serious)", warning: "var(--warning)", good: "var(--good)", info: "var(--accent)", muted: "transparent" };
  return map[tone];
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

