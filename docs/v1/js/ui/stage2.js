// stage2.js: Zone Assignments. Cards from the zone model (3 x 3 grid, metrics,
// room table), multi-select, the design explorer (parallel coordinates) and
// PNG export of the selected cards.
import { h, clear, palette, roomColor, fitCanvas, onResize, emptyState, toast, fmt, pad2, debounce, offscreen, canvasBlob, downloadBlob, FONT, MONO, ZONE_LABELS } from "./common.js";
import { zoneCellBox, arrangeRoomsInCell } from "../engine/connectivity.js";
import { zipBlob } from "./exporters.js";
import { openZoneDetail } from "./detail.js";

export const AXES = [
  { key: "qvci", label: "QVCI", domain: [0, 1], fmt: (v) => v.toFixed(3), get: (t) => t.computed.qvci },
  { key: "aqi", label: "AQI", domain: [0, 1], fmt: (v) => v.toFixed(3), get: (t) => t.computed.aqi },
  { key: "d_target", label: "Δ target", domain: [0, 1], fmt: (v) => v.toFixed(3), get: (t) => Number(t.computed.d_target || 0) },
  { key: "avg_spl_dB", label: "Indoor dB(A)", domain: [20, 70], fmt: (v) => v.toFixed(0), get: (t) => t.computed.avg_spl_dB },
  { key: "zone_spread", label: "Zone spread", domain: [1, 9], fmt: (v) => String(Math.round(v)), get: (t) => t.computed.zone_spread },
  { key: "concentration", label: "Fullest zone", domain: [0, 1], fmt: (v) => v.toFixed(2), get: (t) => t.computed.concentration },
];

/** Letterboxed plan box inside (w, h) with the plan's aspect. */
export function planBox(table, w, hh) {
  const pw = Number(table.plan_width || 0), ph = Number(table.plan_height || 0);
  if (pw <= 0 || ph <= 0) return [0, 0, w, hh];
  const a = pw / ph;
  let bw, bh;
  if (a >= 1) { bw = w; bh = w / a; if (bh > hh) { bh = hh; bw = bh * a; } }
  else { bh = hh; bw = hh * a; if (bw > w) { bw = w; bh = bw / a; } }
  return [(w - bw) / 2, (hh - bh) / 2, bw, bh];
}

/** The card's 3 x 3 grid with room dots (zone_canvas.paintEvent). */
export function drawZoneGrid(ctx, table, w, hh, { showWeights = false, P = palette(), dot = 3 } = {}) {
  const [bx, by, bw, bh] = planBox(table, w, hh);
  ctx.save(); ctx.translate(bx, by);
  const weights = table.zone_weights || {};
  for (let z = 0; z < 9; z++) {
    const [cx, cy, cw, ch] = zoneCellBox(z, bw, bh);
    if (showWeights) { const wg = Number(weights[`zone_${z}`] || 0); ctx.strokeStyle = P.a("accent", 0.08 + wg * 0.92); ctx.lineWidth = 0.5 + wg; }
    else { ctx.strokeStyle = P.border; ctx.lineWidth = 0.5; }
    ctx.strokeRect(cx + 0.5, cy + 0.5, cw - 1, ch - 1);
  }
  const byZone = new Map();
  for (const r of table.rooms || []) { if (r.zone_assignment == null) continue; const z = Number(r.zone_assignment); if (!byZone.has(z)) byZone.set(z, []); byZone.get(z).push(r); }
  for (const [z, rooms] of byZone) {
    const [cx, cy, cw, ch] = zoneCellBox(z, bw, bh);
    arrangeRoomsInCell(rooms, cx, cy, cw, ch).forEach((p, k) => { ctx.beginPath(); ctx.arc(p.cx, p.cy, dot, 0, 2 * Math.PI); ctx.fillStyle = roomColor(rooms[k].room_type); ctx.fill(); });
  }
  ctx.restore();
}

const roomCells = (r) => {
  const weighted = r.scored ?? ["living", "kitchen", "bedroom"].includes(r.room_type);
  const qv = r.quality_view_score, aq = r.acoustic_score;
  return {
    name: r.room_id.replace(/_/g, " "),
    z: r.zone_assignment == null ? "-" : ZONE_LABELS[r.zone_assignment] ?? "?",
    qv: qv == null ? "-" : qv.toFixed(2), qvCls: qv == null ? "dim" : weighted && qv >= 0.5 ? "acc" : weighted ? "" : "dim",
    aq: aq == null ? "-" : aq.toFixed(2), aqCls: aq == null ? "dim" : weighted && r.acoustic_ok ? "acc" : weighted ? "" : "dim",
  };
};

export function mountZones(body, { store, runtime, goto }) {
  let showWeights = false, busy = false, hasGenerated = false, reqSeq = 0, lastError = null;
  let cards = [];
  const scroll = h("div.scroll", { style: { padding: "12px 12px 8px" } });

  // ── design explorer ───────────────────────────────────────────────
  const brushes = Object.fromEntries(AXES.map((a) => [a.key, null]));
  let hover = null, brushDrag = null;
  const caret = h("span.sub", "▾");
  const exMeta = h("span.sub");
  const passLbl = h("span");
  const pcCanvas = h("canvas");
  const readout = h("div.pc-readout", "Hover a plan or polyline to see values · drag on an axis to filter");
  const explorer = h("div.explorer",
    h("div.explorer-head", { on: { click: () => { explorer.classList.toggle("collapsed"); caret.textContent = explorer.classList.contains("collapsed") ? "▸" : "▾"; drawPC(); } } }, caret, h("span.h", "DESIGN EXPLORER"), exMeta),
    h("div.explorer-body", h("div.pc-summary", passLbl, h("button", { on: { click: () => { for (const k in brushes) brushes[k] = null; drawPC(); } } }, "Clear filters")), h("div.pc-plot", pcCanvas), readout));
  const M = { T: 24, B: 28, L: 28, R: 28 };
  const axisXs = (w) => AXES.map((_, i) => M.L + (i * (w - M.L - M.R)) / (AXES.length - 1));
  const yFor = (v, a, hh) => { const t = Math.max(0, Math.min(1, (v - a.domain[0]) / Math.max(a.domain[1] - a.domain[0], 1e-9))); return hh - M.B - t * (hh - M.B - M.T); };
  const valAt = (y, a, hh) => { const t = Math.max(0, Math.min(1, (hh - M.B - y) / Math.max(hh - M.B - M.T, 1e-9))); return a.domain[0] + t * (a.domain[1] - a.domain[0]); };
  const passes = (t) => AXES.every((a) => { const b = brushes[a.key]; if (!b) return true; const v = a.get(t); return v >= b[0] && v <= b[1]; });

  function drawPC() {
    const tables = store.stage2.tables;
    if (!tables.length || explorer.classList.contains("collapsed")) return;
    const { ctx, w, h: H } = fitCanvas(pcCanvas), P = palette(), xs = axisXs(w);
    const sel = new Set(store.stage2.selected_idxs);
    AXES.forEach((a, i) => {
      const x = xs[i];
      ctx.strokeStyle = P.border; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x, M.T); ctx.lineTo(x, H - M.B); ctx.stroke();
      ctx.font = `500 ${w < 520 ? 8.5 : 10}px ${FONT}`; ctx.fillStyle = P.ink; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(a.label, Math.round(x), M.T - 12);
      ctx.font = `8px ${MONO}`; ctx.fillStyle = P.ink3;
      ctx.fillText(a.fmt(a.domain[1]), x, M.T - 1 + 0); ctx.fillText(a.fmt(a.domain[0]), x, H - M.B + 8);
      let y0 = null, y1 = null, alpha = 0.15;
      if (brushDrag && brushDrag.key === a.key) { y0 = Math.min(brushDrag.y0, brushDrag.y1); y1 = Math.max(brushDrag.y0, brushDrag.y1); alpha = 0.25; }
      else if (brushes[a.key]) { y0 = yFor(brushes[a.key][1], a, H); y1 = yFor(brushes[a.key][0], a, H); }
      if (y0 != null) { ctx.fillStyle = P.a("accent", alpha); ctx.strokeStyle = P.accent; ctx.fillRect(x - 5, y0, 10, y1 - y0); ctx.strokeRect(x - 5, y0, 10, y1 - y0); }
    });
    const path = (t) => AXES.map((a, i) => [xs[i], yFor(a.get(t), a, H)]);
    const line = (pts) => { ctx.beginPath(); pts.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.stroke(); };
    const fg = new Set(sel); if (hover != null) fg.add(hover);
    ctx.lineJoin = "round";
    for (const t of tables) if (!fg.has(t.table_index) && !passes(t)) { ctx.strokeStyle = P.a("border", 0.3); ctx.lineWidth = 1; line(path(t)); }
    for (const t of tables) if (!fg.has(t.table_index) && passes(t)) { ctx.strokeStyle = P.a("ink2", 0.5); ctx.lineWidth = 1.25; line(path(t)); }
    for (const t of tables) {
      if (!fg.has(t.table_index)) continue;
      ctx.strokeStyle = passes(t) ? P.accent : P.a("accent", 0.35); ctx.lineWidth = sel.has(t.table_index) ? 2.5 : 2; line(path(t));
      if (t.table_index === hover) for (const [x, y] of path(t)) { ctx.beginPath(); ctx.arc(x, y, 3.5, 0, 2 * Math.PI); ctx.fillStyle = P.accent; ctx.fill(); ctx.strokeStyle = P.surface; ctx.lineWidth = 1; ctx.stroke(); }
    }
    passLbl.textContent = `${tables.filter(passes).length} / ${tables.length} plans pass current filters`;
  }

  const segDist = (px, py, x1, y1, x2, y2) => { const dx = x2 - x1, dy = y2 - y1; if (!dx && !dy) return Math.hypot(px - x1, py - y1); const t = Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy))); return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)); };
  function lineAt(x, y) {
    const b = pcCanvas.getBoundingClientRect(), xs = axisXs(b.width);
    let best = null, bd = 6;
    for (const t of store.stage2.tables) for (let i = 0; i < AXES.length - 1; i++) {
      const d = segDist(x, y, xs[i], yFor(AXES[i].get(t), AXES[i], b.height), xs[i + 1], yFor(AXES[i + 1].get(t), AXES[i + 1], b.height));
      if (d < bd) { bd = d; best = t.table_index; break; }
    }
    return best;
  }
  const pcLocal = (e) => { const b = pcCanvas.getBoundingClientRect(); return [e.clientX - b.left, e.clientY - b.top, b]; };
  pcCanvas.addEventListener("pointerdown", (e) => {
    const [x, y, b] = pcLocal(e), xs = axisXs(b.width);
    const ai = xs.findIndex((ax) => Math.abs(x - ax) <= 10);
    if (ai >= 0 && y >= M.T && y <= b.height - M.B) { brushDrag = { key: AXES[ai].key, y0: y, y1: y }; pcCanvas.setPointerCapture(e.pointerId); drawPC(); return; }
    const idx = lineAt(x, y);
    if (idx != null) toggleSelect(idx);
  });
  pcCanvas.addEventListener("pointermove", (e) => {
    const [x, y, b] = pcLocal(e);
    if (brushDrag) { brushDrag.y1 = Math.max(M.T, Math.min(b.height - M.B, y)); drawPC(); return; }
    const idx = lineAt(x, y);
    if (idx !== hover) {
      hover = idx; drawPC();
      const t = store.stage2.tables.find((q) => q.table_index === idx);
      readout.innerHTML = t ? `<b>#${pad2(idx)}</b>&nbsp;&nbsp;` + AXES.map((a) => `${a.label}: <b>${a.fmt(a.get(t))}</b>`).join(" · ") : "Hover a plan or polyline to see values · drag on an axis to filter";
    }
  });
  pcCanvas.addEventListener("pointerup", () => {
    if (!brushDrag) return;
    const a = AXES.find((q) => q.key === brushDrag.key), H = pcCanvas.getBoundingClientRect().height;
    if (Math.abs(brushDrag.y1 - brushDrag.y0) < 3) brushes[a.key] = null;
    else { const v0 = valAt(Math.max(brushDrag.y0, brushDrag.y1), a, H), v1 = valAt(Math.min(brushDrag.y0, brushDrag.y1), a, H); brushes[a.key] = [v0, v1]; }
    brushDrag = null; drawPC();
  });
  pcCanvas.addEventListener("pointerleave", () => { if (hover != null && !brushDrag) { hover = null; drawPC(); readout.textContent = "Hover a plan or polyline to see values · drag on an axis to filter"; } });
  onResize(pcCanvas.parentElement, drawPC);

  // ── toolbar ───────────────────────────────────────────────────────
  const bRegen = h("button.link-btn", { on: { click: () => regenerate() } }, "Regenerate");
  const bWeights = h("button.chip", { on: { click: () => { showWeights = !showWeights; bWeights.classList.toggle("active", showWeights); cards.forEach((c) => c.redraw()); } } }, "● Zone weight");
  const bAll = h("button.chip", { on: { click: () => { const st = store.stage2; store.setStage2Selection(st.selected_idxs.length === st.tables.length ? [] : st.tables.map((t) => t.table_index)); } } }, "■ Select all");
  const bExport = h("button.chip", { title: "Save each selected card as a high-resolution transparent PNG", on: { click: exportSelected } }, "⤓ Export selected");
  const selLbl = h("span.sel-label");
  const errPill = h("span.err-pill.hidden");
  const bProceed = h("button.btn-primary", { on: { click: () => goto("connectivity") } }, "Proceed to Connectivity  →");
  body.append(scroll, explorer, h("div.toolbar", h("div.tools", bRegen, bWeights, bAll, bExport), h("div.grow"), errPill, h("div.grow"), selLbl, bProceed));

  function toggleSelect(idx) {
    const sel = new Set(store.stage2.selected_idxs);
    if (sel.has(idx)) sel.delete(idx); else sel.add(idx);
    store.setStage2Selection([...sel]);
  }

  async function regenerate() {
    const av = store.selectedVariation();
    if (!av) return;
    hasGenerated = true;
    const my = ++reqSeq, ep = store.epoch;
    busy = true; render();
    try {
      const ip = JSON.parse(JSON.stringify(store.inputs));
      const res = await runtime.run("zones", { inputs: ip, variation: av, opts: { nSamples: ip.params.zone_samples, temperature: ip.params.temperature } });
      if (my !== reqSeq || ep !== store.epoch) return;
      const n = ip.params.n_plans, display = res.tables.slice(0, n);
      display.forEach((t, i) => { t.table_index = i; t.plan_width = ip.plan_geometry.width; t.plan_height = ip.plan_geometry.height; });
      const keys = new Set(display.map((t) => `${t.computed.qvci.toFixed(6)}|${t.computed.aqi.toFixed(6)}`));
      const pool = res.pool.map((s) => ({ ...s, in_display: keys.has(`${Number(s.qvci).toFixed(6)}|${Number(s.aqi).toFixed(6)}`) }));
      lastError = null;
      busy = false;
      store.setStage2(display, display.map((t) => t.table_index), pool, res.n_decodes);
    } catch (err) {
      if (my !== reqSeq || ep !== store.epoch) return;
      lastError = err.message; busy = false;
      store.setStage2([], [], [], 0, err.message);
    }
  }
  const scheduleRegen = debounce(() => { if (hasGenerated && store.selectedVariation()) regenerate(); }, 350);

  function card(t, sel) {
    const cv = h("canvas");
    const c = t.computed;
    const metrics = h("div.metrics",
      h("span.ml", "Q"), h("span.mv.acc", fmt(c.qvci, 3)), h("span.ms"),
      h("span.ml", "A"), h("span.mv.acc", fmt(c.aqi, 3)), h("span.ms"),
      h("span.ml", "SPL"), h("span.mv", fmt(c.avg_spl_dB, 0)), h("span.ms", "dB(A)"),
      h("span.ml", "Z"), h("span.mv", String(c.zone_spread)), h("span.ms"),
      ...(c.d_target != null ? [h("span.ml", "Δ"), h("span.mv", fmt(c.d_target, 3)), h("span.ms")] : []));
    const tbody = h("tbody", ...(t.rooms || []).map((r) => {
      const q = roomCells(r);
      return h("tr", h("td", q.name), h("td.c.z", q.z), h("td.r" + (q.qvCls ? `.${q.qvCls}` : ""), q.qv), h("td.r" + (q.aqCls ? `.${q.aqCls}` : ""), q.aq));
    }));
    const tbl = h("table.rooms-tbl", h("colgroup", h("col"), h("col", { style: { width: "30px" } }), h("col", { style: { width: "36px" } }), h("col", { style: { width: "36px" } })),
      h("thead", h("tr", h("th", "ROOM"), h("th.c", "Z"), h("th.r", "QV"), h("th.r", "AQ"))), tbody);
    const el = h("div.zone-card" + (sel ? ".selected" : ""), { on: { click: () => toggleSelect(t.table_index), dblclick: () => openZoneDetail(t, store) } },
      h("div.card-head", h("span.idx", `#${pad2(t.table_index)}`), sel ? h("span.badge", "✓ SELECTED") : null),
      h("div.zone-top", cv, metrics), h("div.tbl-wrap", tbl));
    el.redraw = () => { const { ctx } = fitCanvas(cv, 96, 96); drawZoneGrid(ctx, t, 96, 96, { showWeights }); };
    requestAnimationFrame(el.redraw);
    return el;
  }

  function render() {
    clear(scroll); cards = [];
    const st1 = store.stage1, st2 = store.stage2;
    errPill.classList.toggle("hidden", !lastError);
    errPill.textContent = lastError ? "Zone model error" : ""; errPill.title = lastError || "";
    if (busy) { scroll.append(h("div.empty-state", h("span.busy", "Decoding zone assignments with the zone model…"))); explorer.classList.add("hidden"); refreshToolbar(); return; }
    if (st1.selected_idx == null) { scroll.append(emptyState("Waiting for area selection", "Complete Stage 1 (pick an area variation or use your drawn layout) to generate zone assignments.")); explorer.classList.add("hidden"); refreshToolbar(); return; }
    if (!st2.tables.length) {
      scroll.append(emptyState("Ready to generate zone assignments", lastError ? `The model could not run: ${lastError}` : `The zone model decodes ${store.inputs.params.zone_samples + 1} arrangements (one greedy, ${store.inputs.params.zone_samples} sampled at T = ${store.inputs.params.temperature}) and shows the ${store.inputs.params.n_plans} closest to the targets.`, "Generate zone assignments", () => regenerate()));
      explorer.classList.add("hidden"); refreshToolbar(); return;
    }
    const t0 = st2.tables[0], tg = t0.targets || {}, rr = t0.reachable || {};
    const parts = [tg.present ? `Targets QVCI <b>${fmt(tg.qvci, 2)}</b> · AQI <b>${fmt(tg.aqi, 2)}</b>` : "Unconditioned sampling (no targets)"];
    if (rr.qvci) parts.push(`reachable QVCI ${rr.qvci[0].toFixed(2)} - ${rr.qvci[1].toFixed(2)} · AQI ${rr.aqi[0].toFixed(2)} - ${rr.aqi[1].toFixed(2)}`);
    if (rr.aqi_collapsed) parts.push(`<span style="color:var(--accent)">AQI cannot be changed on this site</span>`);
    parts.push(`${st2.n_decodes || st2.sample_pool.length} decodes · ${st2.tables.length} shown${tg.present ? " · sorted by distance to target" : ""}`);
    scroll.append(h("div.range-strip", { html: parts.join(" · ") }));
    const sel = new Set(st2.selected_idxs);
    const grid = h("div.grid4");
    for (const t of st2.tables) { const c = card(t, sel.has(t.table_index)); cards.push(c); grid.append(c); }
    scroll.append(grid);
    explorer.classList.remove("hidden");
    exMeta.textContent = `· ${st2.tables.length} plans · ${AXES.length} metrics`;
    requestAnimationFrame(drawPC);
    refreshToolbar();
  }

  function refreshSelection() {
    const sel = new Set(store.stage2.selected_idxs);
    cards.forEach((c, i) => {
      const t = store.stage2.tables[i], on = sel.has(t.table_index);
      c.classList.toggle("selected", on);
      const head = c.querySelector(".card-head"), badge = head.querySelector(".badge");
      if (on && !badge) head.append(h("span.badge", "✓ SELECTED")); else if (!on && badge) badge.remove();
    });
    drawPC(); refreshToolbar();
  }

  function refreshToolbar() {
    const st = store.stage2, has = st.tables.length > 0, n = st.selected_idxs.length;
    bWeights.disabled = !has; bAll.disabled = !has; bExport.disabled = !(has && n > 0);
    bRegen.disabled = store.stage1.selected_idx == null || busy;
    selLbl.textContent = !has ? "" : n === 0 ? "Click plans to select" : n === 1 ? "1 plan selected" : `${n} plans selected`;
    bProceed.disabled = !has || n === 0;
    const all = has && n === st.tables.length;
    bAll.classList.toggle("active", all); bAll.textContent = all ? "■ Select none" : "■ Select all";
  }

  async function exportSelected() {
    const st = store.stage2, sel = st.tables.filter((t) => st.selected_idxs.includes(t.table_index));
    if (!sel.length) return toast("Select at least one card before exporting.");
    const files = [];
    for (const t of sel) {
      const cv = renderCardPNG(t, showWeights);
      files.push({ name: `zone_plan_${pad2(t.table_index)}.png`, blob: await canvasBlob(cv) });
    }
    const saved = files.length === 1 ? await downloadBlob(files[0].blob, files[0].name) : await downloadBlob(await zipBlob(files), "zone_cards.png.zip");
    if (saved) toast(`Saved ${files.length} card${files.length > 1 ? "s" : ""}.`);
  }

  let lastTables = null;
  store.on("stage2", () => { if (store.stage2.tables !== lastTables) { lastTables = store.stage2.tables; render(); } else refreshSelection(); });
  store.on("stage1", () => { if (!busy) render(); });
  store.on("inputs", scheduleRegen);
  store.on("theme", () => { cards.forEach((c) => c.redraw()); drawPC(); });
  store.on("reset", () => { scheduleRegen.cancel(); busy = false; hasGenerated = store.stage2.tables.length > 0; lastError = null; lastTables = store.stage2.tables; render(); });
  store.on("goto:zones", () => regenerate());
  render();
}

/** A transparent PNG of one zone card at 3x (fixed 240 x 320 design size). */
export function renderCardPNG(t, showWeights = false) {
  const W = 240, rows = (t.rooms || []).length, H = 176 + rows * 16;
  const { cv, ctx } = offscreen(W, H, 3), P = palette();
  ctx.font = `11px ${MONO}`; ctx.fillStyle = P.ink2; ctx.textBaseline = "middle"; ctx.textAlign = "left";
  ctx.fillText(`#${pad2(t.table_index)}`, 10, 14);
  ctx.save(); ctx.translate(10, 28); drawZoneGrid(ctx, t, 104, 104, { showWeights, P, dot: 3.4 }); ctx.restore();
  const c = t.computed, mx = 128;
  const rowsM = [["Q", fmt(c.qvci, 3), true, ""], ["A", fmt(c.aqi, 3), true, ""], ["SPL", fmt(c.avg_spl_dB, 0), false, "dB(A)"], ["Z", String(c.zone_spread), false, ""]];
  if (c.d_target != null) rowsM.push(["Δ", fmt(c.d_target, 3), false, ""]);
  rowsM.forEach(([l, v, acc, suf], i) => {
    const y = 36 + i * 20;
    ctx.font = `600 9px ${FONT}`; ctx.fillStyle = P.ink3; ctx.textAlign = "left"; ctx.fillText(l, mx, y);
    ctx.font = `600 11px ${MONO}`; ctx.fillStyle = acc ? P.accent : P.ink; ctx.textAlign = "right"; ctx.fillText(v, mx + 70, y);
    if (suf) { ctx.font = `9px ${FONT}`; ctx.fillStyle = P.ink3; ctx.textAlign = "left"; ctx.fillText(suf, mx + 74, y); }
  });
  const ty = 146, cz = W - 10 - 36 - 36 - 30, cq = W - 10 - 36, ca = W - 10;
  ctx.font = `600 9px ${FONT}`; ctx.fillStyle = P.ink3;
  ctx.textAlign = "left"; ctx.fillText("ROOM", 10, ty); ctx.textAlign = "center"; ctx.fillText("Z", cz + 15, ty); ctx.textAlign = "right"; ctx.fillText("QV", cq, ty); ctx.fillText("AQ", ca, ty);
  ctx.strokeStyle = P.border; ctx.beginPath(); ctx.moveTo(10, ty + 8); ctx.lineTo(W - 10, ty + 8); ctx.stroke();
  (t.rooms || []).forEach((r, i) => {
    const y = ty + 20 + i * 16, q = roomCells(r);
    ctx.font = `10.5px ${MONO}`;
    ctx.fillStyle = P.ink; ctx.textAlign = "left"; ctx.fillText(q.name, 10, y);
    ctx.fillStyle = P.ink2; ctx.textAlign = "center"; ctx.fillText(q.z, cz + 15, y);
    ctx.textAlign = "right";
    ctx.fillStyle = q.qvCls === "acc" ? P.accent : q.qvCls === "dim" ? P.ink3 : P.ink; ctx.fillText(q.qv, cq, y);
    ctx.fillStyle = q.aqCls === "acc" ? P.accent : q.aqCls === "dim" ? P.ink3 : P.ink; ctx.fillText(q.aq, ca, y);
  });
  return cv;
}
