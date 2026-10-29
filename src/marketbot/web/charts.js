"use strict";

/* Small canvas charts, no libraries. Candlesticks with a volume strip, and a line chart. */

const CHART_COLORS = { up: "#2fd596", down: "#ff5b6b", grid: "#232a33", text: "#8d97a6", line: "#6c8cff", vol: 0.35 };

function sizeCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 600, h = canvas.clientHeight || 300;
  canvas.width = Math.round(w * ratio); canvas.height = Math.round(h * ratio);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, w, h };
}
function priceText(v) {
  if (!Number.isFinite(v)) return "";
  if (v >= 1) return v.toFixed(v >= 100 ? 1 : 3);
  return v.toPrecision(3);
}

function candleChart(candles) {
  const canvas = el("canvas", { class: "chart", role: "img", "aria-label": "Candlestick chart with volume" });
  const readout = el("div", { class: "chart-read", text: "" });
  const wrap = el("div", { class: "chart-wrap" }, canvas, readout);
  let hover = -1;
  const pad = { l: 8, r: 58, t: 22, b: 18 };

  function draw() {
    const { ctx, w, h } = sizeCanvas(canvas);
    ctx.clearRect(0, 0, w, h);
    if (!candles.length) {
      ctx.fillStyle = CHART_COLORS.text; ctx.font = "13px sans-serif";
      ctx.fillText("No candle data for this pool yet.", 16, 30); return;
    }
    const volH = (h - pad.t - pad.b) * 0.2, plotH = h - pad.t - pad.b - volH - 6;
    const lo = Math.min(...candles.map((c) => c.l)), hi = Math.max(...candles.map((c) => c.h));
    const span = hi - lo || hi || 1, maxV = Math.max(...candles.map((c) => c.v)) || 1;
    const slot = (w - pad.l - pad.r) / candles.length, bodyW = Math.max(1, slot * 0.7);
    const y = (p) => pad.t + plotH - ((p - lo) / span) * plotH;
    ctx.font = "11px ui-monospace, monospace"; ctx.textBaseline = "middle";
    for (let i = 0; i <= 4; i++) {
      const p = lo + (span * i) / 4, yy = y(p);
      ctx.strokeStyle = CHART_COLORS.grid; ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(w - pad.r, yy); ctx.stroke();
      ctx.fillStyle = CHART_COLORS.text; ctx.fillText(priceText(p), w - pad.r + 6, yy);
    }
    candles.forEach((c, i) => {
      const x = pad.l + slot * i + slot / 2, up = c.c >= c.o, col = up ? CHART_COLORS.up : CHART_COLORS.down;
      ctx.strokeStyle = col; ctx.fillStyle = col;
      ctx.beginPath(); ctx.moveTo(x, y(c.h)); ctx.lineTo(x, y(c.l)); ctx.stroke();
      const top = y(Math.max(c.o, c.c)), bot = y(Math.min(c.o, c.c));
      ctx.fillRect(x - bodyW / 2, top, bodyW, Math.max(1, bot - top));
      ctx.globalAlpha = CHART_COLORS.vol;
      const vh = (c.v / maxV) * volH;
      ctx.fillRect(x - bodyW / 2, h - pad.b - vh, bodyW, vh);
      ctx.globalAlpha = 1;
    });
    if (hover >= 0) {
      const x = pad.l + slot * hover + slot / 2;
      ctx.strokeStyle = "#5f6977"; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, h - pad.b); ctx.stroke(); ctx.setLineDash([]);
    }
    ctx.fillStyle = CHART_COLORS.text; ctx.textBaseline = "alphabetic";
    ctx.fillText(new Date(candles[0].t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), pad.l, h - 4);
    const last = new Date(candles[candles.length - 1].t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    ctx.fillText(last, w - pad.r - 40, h - 4);
  }
  function show(i) {
    hover = i;
    const c = candles[i] || candles[candles.length - 1];
    if (c) readout.textContent = new Date(c.t * 1000).toLocaleString() + "  O " + priceText(c.o) + "  H " + priceText(c.h) + "  L " + priceText(c.l) + "  C " + priceText(c.c) + "  Vol " + usd(c.v, true);
    draw();
  }
  canvas.addEventListener("pointermove", (e) => {
    if (!candles.length) return;
    const r = canvas.getBoundingClientRect(), slot = (r.width - pad.l - pad.r) / candles.length;
    show(Math.max(0, Math.min(candles.length - 1, Math.floor((e.clientX - r.left - pad.l) / slot))));
  });
  canvas.addEventListener("pointerleave", () => { hover = -1; show(candles.length - 1); });
  const redraw = () => { if (canvas.isConnected) draw(); };
  window.addEventListener("resize", redraw);
  requestAnimationFrame(() => show(candles.length - 1));
  return wrap;
}

function lineChart(points, opts) {
  const canvas = el("canvas", { class: "chart", role: "img", "aria-label": (opts && opts.label) || "Line chart" });
  canvas.style.setProperty("height", ((opts && opts.height) || 140) + "px");
  const draw = () => {
    const { ctx, w, h } = sizeCanvas(canvas);
    ctx.clearRect(0, 0, w, h);
    if (points.length < 2) {
      ctx.fillStyle = CHART_COLORS.text; ctx.font = "12px sans-serif";
      ctx.fillText((opts && opts.empty) || "Not enough readings yet.", 12, 24); return;
    }
    const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const sx = (v) => 8 + ((v - x0) / (x1 - x0 || 1)) * (w - 70), sy = (v) => h - 14 - ((v - y0) / (y1 - y0 || 1)) * (h - 28);
    ctx.strokeStyle = CHART_COLORS.line; ctx.lineWidth = 1.5; ctx.beginPath();
    points.forEach((p, i) => (i ? ctx.lineTo(sx(p.x), sy(p.y)) : ctx.moveTo(sx(p.x), sy(p.y)))); ctx.stroke();
    ctx.fillStyle = CHART_COLORS.text; ctx.font = "11px ui-monospace, monospace";
    ctx.fillText((opts && opts.fmt ? opts.fmt(y1) : priceText(y1)), w - 58, 12);
    ctx.fillText((opts && opts.fmt ? opts.fmt(y0) : priceText(y0)), w - 58, h - 4);
  };
  window.addEventListener("resize", () => { if (canvas.isConnected) draw(); });
  requestAnimationFrame(draw);
  return canvas;
}
