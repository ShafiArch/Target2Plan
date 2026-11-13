// stage4.js: Schematic Design. Plans synthesised from areas, zones and
// connectivity (rooms slide into contact, walls, doors, windows, furniture),
// replayed as an animation; the AQI x QVCI Pareto front; exports.
import { h, clear, palette, fitCanvas, onResize, emptyState, toast, pad2, offscreen, canvasBlob, downloadBlob, FONT, MONO, numberInput, DESKTOP } from "./common.js";
import { makeSchematics, paretoFrontIndices } from "../engine/schematic.js";
import { getSynthesis, paintFrame, commonFit, roomAt, FURNITURE_STYLES, DEFAULT_FURNITURE } from "./schematicPaint.js";
import { zipBlob, encodeGIF, encodeMP4 } from "./exporters.js";
import { openSchematicDetail } from "./detail.js";
import { showRoomPopup, closeRoomPopup } from "./roomPopup.js";

const LS = (k, d) => { try { const v = localStorage.getItem(`t2p.${k}`); return v == null ? d : JSON.parse(v); } catch { return d; } };
const SS = (k, v) => { try { localStorage.setItem(`t2p.${k}`, JSON.stringify(v)); } catch { /* ignore */ } };

export function mountSchematic(body, { store, goto }) {
  const opt = { edges: LS("schEdges", true), walls: LS("schWalls", true), furn: LS("schFurniture", DEFAULT_FURNITURE), planOnly: LS("schPlanOnly", false), anim: LS("schAnim", 25), fixed: LS("paretoFixed", false) };
  if (!FURNITURE_STYLES.some(([v]) => v === opt.furn)) opt.furn = DEFAULT_FURNITURE;
  let cards = [], lastSch = null, hoverIdx = null, fitDims = null;
  const grid = h("div.grid4");
  const gridWrap = h("div.schem-grid-wrap", grid);

  // ── Pareto ────────────────────────────────────────────────────────
  const pcv = h("canvas");
  const b01 = h("button", { on: { click: () => setFixed(true) } }, "0-1"), bScaled = h("button", { on: { click: () => setFixed(false) } }, "Scaled");
  const pareto = h("div.pareto",
    h("div.pareto-head", h("span.h", "AQI × QVCI PARETO FRONT"), h("span.sub", "· dominant designs highlighted"), h("span.grow"),
      h("div.mode-pill", b01, bScaled), h("button.btn-ghost", { on: { click: saveParetoImage } }, "Save image"), h("button.btn-ghost", { on: { click: saveParetoCSV } }, "Save CSV")),
    h("div.pareto-plot", pcv));
  function setFixed(v) { opt.fixed = v; SS("paretoFixed", v); b01.classList.toggle("active", v); bScaled.classList.toggle("active", !v); drawPareto(); }

  // ── toolbar (two rows) ────────────────────────────────────────────
  const chip = (label, key, title, store_) => {
    const b = h("button.chip" + (opt[key] ? ".active" : ""), { title, on: { click: () => { opt[key] = !opt[key]; SS(store_, opt[key]); b.classList.toggle("active", opt[key]); applyView(); } } }, label);
    return b;
  };
  const bRegen = h("button.link-btn", { on: { click: () => regenerate() } }, "Regenerate");
  const cEdges = chip("● Show edges", "edges", "Dashed connectivity edges behind the plan", "schEdges");
  const cWalls = chip("▭ Walls · doors · windows", "walls", "Draw double-line walls with doors and windows", "schWalls");
  // furniture style dropdown (placement after RGeoLib by Ramon Elias Weber, MIT License)
  const furnSel = h("select", { "aria-label": "Furniture style" }, ...FURNITURE_STYLES.map(([v, label]) => h("option", { value: v }, label)));
  furnSel.value = opt.furn;
  const cFurn = h("label.chip.chip-select" + (opt.furn !== "none" ? ".active" : ""), { title: "How furniture is drawn. Furniture placement after RGeoLib by Ramon Elias Weber (MIT License)." }, "▦ Furniture", furnSel);
  furnSel.addEventListener("change", () => { opt.furn = furnSel.value; SS("schFurniture", opt.furn); cFurn.classList.toggle("active", opt.furn !== "none"); applyView(); });
  // Pareto front panel toggle (on = accent red); off leaves only the plans
  const cPlan = h("button.chip" + (!opt.planOnly ? ".active" : ""), { title: "Show or hide the AQI × QVCI Pareto front below the plans", on: { click: () => { opt.planOnly = !opt.planOnly; SS("schPlanOnly", opt.planOnly); cPlan.classList.toggle("active", !opt.planOnly); applyView(); } } }, "◆ Pareto front");
  const animIn = numberInput(opt.anim, { min: 0, max: 400, step: 5, suffix: "ms", width: 84, onChange: (v) => { opt.anim = v; SS("schAnim", v); replayAll(); } });
  animIn.title = "Milliseconds per synthesis frame (0 = show the finished plan)";
  const bReplay = h("button.link-btn", { title: "Replay the schematic synthesis on every card", on: { click: () => replayAll() } }, "↻ Replay");
  const bAnim = h("button.link-btn", { title: "Export the synthesis animation of the selected plans (all plans when none is selected)", on: { click: exportAnimation } }, "Save GIF / MP4");
  const bAll = h("button.chip", { on: { click: () => { const st = store.stage4; store.setStage4Selection(st.selected_idxs.length === st.schematics.length ? [] : st.schematics.map((s) => s.plan_index)); } } }, "■ Select all");
  const bDom = h("button.chip", { on: { click: () => store.setStage4Selection(paretoFrontIndices(store.stage4.schematics)) } }, "◆ Select Pareto-dominant");
  const selLbl = h("span.sel-label");
  const bBatch = h("button.link-btn", { title: "Top plans by distance to the targets on one contact sheet", on: { click: saveBatch } }, "Save Batch");
  const bExport = h("button.btn-primary", { on: { click: exportSelected } }, "Export selected  →");
  const toolbar = h("div.toolbar.two-rows",
    h("div.row", h("div.tools", bRegen, h("span.tsep"), cEdges, cWalls, cFurn, cPlan)),
    h("div.row", h("div.tools", h("span.sub", "Anim speed"), animIn, bReplay, bAnim, h("span.tsep"), bAll, bDom), h("span.grow"), selLbl, bBatch, bExport));
  const bodyWrap = h("div.stage4-body", { style: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0 } }, gridWrap, pareto);
  body.append(bodyWrap, toolbar);

  function regenerate() {
    const st2 = store.stage2, st3 = store.stage3;
    if (!st3.graphs.length || !st3.selected_idxs.length) return;
    const sch = makeSchematics(st2.tables, st3.graphs, st3.selected_idxs, store.inputs);
    store.setStage4(sch, []);
  }

  function card(s, sel) {
    const cv = h("canvas");
    const c = s.computed || {};
    const el = h("div.plain-card.schem-card", { on: { click: () => toggle(s.plan_index), dblclick: () => { closeRoomPopup(); openSchematicDetail(s, store, opt); } } },
      h("div.plain-head", h("span.idx", `#${pad2(s.plan_index)}`), sel ? h("span.badge", "✓ SELECTED") : null),
      h("div.plain-body", { style: { padding: "4px" } }, cv),
      h("div.plain-foot.schem-foot", h("span", h("span.ml", "Q"), h("span.mv", Number(c.qvci ?? 0).toFixed(2))), h("span", h("span.ml", "A"), h("span.mv", Number(c.aqi ?? 0).toFixed(2))), h("span.grow"), h("span.ml", `${(s.rooms || []).length} rooms`)));
    let frameIdx = -1, timer = null, T = null, shown = null;
    el.draw = () => {
      const syn = getSynthesis(s);
      const { ctx, w, h: H } = fitCanvas(cv);
      if (!syn.frames.length) return;
      shown = frameIdx < 0 ? syn.frames[syn.frames.length - 1] : syn.frames[Math.min(frameIdx, syn.frames.length - 1)];
      T = paintFrame(ctx, s, syn, shown, w, H, palette(), { showEdges: opt.edges, showWalls: opt.walls, furniture: opt.furn, showCaption: frameIdx >= 0, showCompass: !opt.planOnly, fitDims });
    };
    // a click on a room opens its card (as in the desktop app); elsewhere it selects the plan
    cv.addEventListener("click", (e) => {
      const b = cv.getBoundingClientRect(), room = roomAt(shown, T, e.clientX - b.left, e.clientY - b.top);
      if (!room) return;
      e.stopPropagation();
      showRoomPopup(room, e.clientX, e.clientY, store.inputs);
    });
    cv.addEventListener("mousemove", (e) => {
      const b = cv.getBoundingClientRect();
      const tip = roomAt(shown, T, e.clientX - b.left, e.clientY - b.top) ? "Click for room details · double-click for the plan" : "";
      if (cv.title !== tip) cv.title = tip;
    });
    el.replay = () => {
      clearInterval(timer);
      const syn = getSynthesis(s);
      if (opt.anim <= 0 || !syn.frames.length) { frameIdx = -1; el.draw(); return; }
      frameIdx = 0; el.draw();
      timer = setInterval(() => { frameIdx += 1; if (frameIdx >= syn.frames.length - 1) { frameIdx = -1; clearInterval(timer); } el.draw(); }, opt.anim);
    };
    el.stop = () => clearInterval(timer);
    el.s = s;
    return el;
  }

  function render() {
    closeRoomPopup(); cards.forEach((c) => c.stop()); clear(grid); cards = [];
    const st3 = store.stage3, st4 = store.stage4;
    gridWrap.querySelectorAll(".empty-state").forEach((e) => e.remove());
    if (!st3.selected_idxs.length) { gridWrap.prepend(emptyState("Waiting for connectivity selection", "Complete Stage 3 (select one or more connectivity graphs) to synthesise schematic plans.")); pareto.classList.add("hidden"); refreshToolbar(); return; }
    if (!st4.schematics.length) {
      const n = st3.selected_idxs.length;
      gridWrap.prepend(emptyState("Ready to synthesise schematics", `Each of the ${n} selected graph${n !== 1 ? "s" : ""} becomes a plan: rooms sized by area slide into contact along the connectivity edges, then walls, doors, windows (from the planned WWR) and furniture are added.`, `Synthesise ${n} plan${n !== 1 ? "s" : ""}`, () => regenerate()));
      pareto.classList.add("hidden"); refreshToolbar(); return;
    }
    pareto.classList.remove("hidden");
    const sel = new Set(st4.selected_idxs);
    fitDims = commonFit(st4.schematics);
    for (const s of st4.schematics) { const c = card(s, sel.has(s.plan_index)); cards.push(c); grid.append(c); }
    sizeCards();
    requestAnimationFrame(() => { cards.forEach((c) => c.replay()); drawPareto(); });
    applyView(false);
    refreshToolbar();
  }

  // Card height follows the plans' proportions (at the shared scale), so the
  // plans fill the view without wide empty bands above and below them.
  function sizeCards() {
    if (!cards.length || !fitDims) return;
    const cw = cards[0].querySelector(".plain-body").clientWidth - 8;
    if (cw <= 0) return;
    const m = Math.max(6, cw * 0.035) + (opt.planOnly ? 0 : 13);
    const sw = Math.min(...fitDims.map(([W]) => (cw - 2 * m) / W));
    const need = sw * Math.max(...fitDims.map(([, H]) => H)) + 2 * m;
    grid.style.setProperty("--schem-canvas-h", `${Math.round(Math.max(0.55 * cw, Math.min(1.15 * cw, need)))}px`);
  }
  function applyView(redraw = true) {
    bodyWrap.classList.toggle("plan-only", opt.planOnly);
    sizeCards();
    if (redraw) { cards.forEach((c) => c.draw()); drawPareto(); }
  }
  function replayAll() { cards.forEach((c) => c.replay()); }
  function toggle(idx) {
    const sel = new Set(store.stage4.selected_idxs);
    if (sel.has(idx)) sel.delete(idx); else sel.add(idx);
    store.setStage4Selection([...sel]);
  }
  function refreshSelection() {
    const sel = new Set(store.stage4.selected_idxs);
    for (const c of cards) {
      const on = sel.has(c.s.plan_index), head = c.querySelector(".plain-head"), badge = head.querySelector(".badge");
      if (on && !badge) head.append(h("span.badge", "✓ SELECTED")); else if (!on && badge) badge.remove();
    }
    drawPareto(); refreshToolbar();
  }
  function refreshToolbar() {
    const st = store.stage4, has = st.schematics.length > 0, n = st.selected_idxs.length;
    for (const b of [bAll, bDom, cEdges, cWalls, cFurn, bReplay, bAnim, bBatch]) b.disabled = !has;
    bRegen.disabled = !store.stage3.selected_idxs.length;
    selLbl.textContent = !has ? "" : n === 0 ? "No plans selected" : n === 1 ? "1 plan selected" : `${n} plans selected`;
    bExport.disabled = !has || n === 0;
    const all = has && n === st.schematics.length;
    bAll.classList.toggle("active", all); bAll.textContent = all ? "■ Select none" : "■ Select all";
  }

  // ── Pareto drawing ────────────────────────────────────────────────
  const PM = { L: 60, R: 24, T: 18, B: 30 };
  function bounds() {
    if (opt.fixed) return [0, 1, 0, 1];
    const qs = [], as = [];
    for (const s of store.stage4.schematics) { qs.push(Number(s.computed?.qvci ?? 0)); as.push(Number(s.computed?.aqi ?? 0)); }
    for (const p of store.stage2.sample_pool) { qs.push(Number(p.qvci ?? 0)); as.push(Number(p.aqi ?? 0)); }
    if (!qs.length) return [0, 1, 0, 1];
    const fit = (v) => {
      let lo = Math.min(...v), hi = Math.max(...v);
      const pd = Math.max(0.02, (hi - lo) * 0.1);
      lo = Math.max(0, lo - pd); hi = Math.min(1, hi + pd);
      if (hi - lo < 0.1) { const c = (lo + hi) / 2; lo = Math.max(0, c - 0.05); hi = Math.min(1, c + 0.05); if (hi - lo < 0.1) { if (lo <= 0) hi = Math.min(1, lo + 0.1); else lo = Math.max(0, hi - 0.1); } }
      return [lo, hi];
    };
    return [...fit(qs), ...fit(as)];
  }
  const niceTicks = (lo, hi, target = 4) => {
    if (hi <= lo) return [lo];
    const raw = (hi - lo) / Math.max(1, target - 1), mag = 10 ** Math.floor(Math.log10(raw)), nm = raw / mag;
    const step = (nm < 1.5 ? 1 : nm < 3.5 ? 2 : nm < 7.5 ? 5 : 10) * mag;
    const out = [];
    for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + step * 1e-6; v += step) out.push(Number(v.toFixed(10)));
    return out;
  };
  function paretoGeom(w, H) {
    const aw = w - PM.L - PM.R, ah = H - PM.T - PM.B, side = Math.max(0, Math.min(aw, ah));
    return { px: PM.L + (aw - side) / 2, py: PM.T, side };
  }
  function drawParetoInto(ctx, w, H, P, k = 1) {
    const { px, py, side } = paretoGeom(w, H), [q0, q1, a0, a1] = bounds();
    const X = (q) => px + Math.max(0, Math.min(1, q1 <= q0 ? 0.5 : (q - q0) / (q1 - q0))) * side;
    const Y = (a) => py + side - Math.max(0, Math.min(1, a1 <= a0 ? 0.5 : (a - a0) / (a1 - a0))) * side;
    const qt = niceTicks(q0, q1), at = niceTicks(a0, a1);
    ctx.strokeStyle = P.a("border", 0.27); ctx.lineWidth = 0.5 * k;
    for (const v of qt) if (v > q0 + Math.max(1e-6, (q1 - q0) * 1e-3) && v < q1 - Math.max(1e-6, (q1 - q0) * 1e-3)) { ctx.beginPath(); ctx.moveTo(X(v), py); ctx.lineTo(X(v), py + side); ctx.stroke(); }
    for (const v of at) if (v > a0 + Math.max(1e-6, (a1 - a0) * 1e-3) && v < a1 - Math.max(1e-6, (a1 - a0) * 1e-3)) { ctx.beginPath(); ctx.moveTo(px, Y(v)); ctx.lineTo(px + side, Y(v)); ctx.stroke(); }
    const axis = P.ink2;
    ctx.strokeStyle = axis; ctx.lineWidth = 1.3 * k;
    ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(px, py + side); ctx.lineTo(px + side, py + side); ctx.stroke();
    ctx.lineWidth = 1 * k;
    for (const v of qt) { ctx.beginPath(); ctx.moveTo(X(v), py + side); ctx.lineTo(X(v), py + side + 4 * k); ctx.stroke(); }
    for (const v of at) { ctx.beginPath(); ctx.moveTo(px - 4 * k, Y(v)); ctx.lineTo(px, Y(v)); ctx.stroke(); }
    ctx.font = `600 ${10.5 * k}px ${FONT}`; ctx.fillStyle = P.ink; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText("QVCI →", px + side / 2, py + side + 21 * k);
    ctx.save(); ctx.translate(px - 48 * k, py + side / 2); ctx.rotate(-Math.PI / 2); ctx.fillText("AQI →", 0, 0); ctx.restore();
    ctx.font = `${9.5 * k}px ${MONO}`; ctx.fillStyle = P.ink2;
    for (const v of qt) { ctx.textAlign = "center"; ctx.fillText(v.toFixed(2), X(v), py + side + 9 * k); }
    for (const v of at) { ctx.textAlign = "right"; ctx.fillText(v.toFixed(2), px - 7 * k, Y(v)); }
    const sch = store.stage4.schematics, dom = paretoFrontIndices(sch), domSet = new Set(dom);
    const pts = dom.map((idx) => sch.find((s) => s.plan_index === idx)).filter(Boolean).map((s) => [X(s.computed.qvci), Y(s.computed.aqi)]);
    if (pts.length >= 2) { ctx.strokeStyle = P.accent; ctx.lineWidth = 1.8 * k; ctx.setLineDash([7 * k, 5 * k]); ctx.beginPath(); pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.stroke(); ctx.setLineDash([]); }
    ctx.fillStyle = "rgba(120,120,120,0.84)";
    store.stage2.sample_pool.forEach((p, i) => {
      const hh = Math.imul(i, 2654435761) >>> 0;
      const jx = -((hh & 0xffff) / 65535) * 10 * k, jy = (((hh >>> 16) & 0xffff) / 65535) * 10 * k, r = 1.8 * k;
      const x = Math.min(Math.max(X(Number(p.qvci)) + jx, px + r), px + side - r), y = Math.min(Math.max(Y(Number(p.aqi)) + jy, py + r), py + side - r);
      ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.fill();
    });
    const sel = new Set(store.stage4.selected_idxs);
    for (const s of sch) {
      const x = X(s.computed.qvci), y = Y(s.computed.aqi), isDom = domSet.has(s.plan_index), isSel = sel.has(s.plan_index), isHov = s.plan_index === hoverIdx;
      const r = (isHov ? 7 : isSel ? 5.5 : 3.5) * k;
      ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI);
      if (isDom) { ctx.fillStyle = P.accent; ctx.strokeStyle = P.surface; ctx.lineWidth = 1.5 * k; }
      else if (isSel) { ctx.fillStyle = P.a("accent", 0.55); ctx.strokeStyle = P.accent; ctx.lineWidth = 1.2 * k; }
      else { ctx.fillStyle = P.ink2; ctx.strokeStyle = P.surface; ctx.lineWidth = 1 * k; }
      ctx.fill(); ctx.stroke();
    }
    if (hoverIdx != null) {
      const s = sch.find((q) => q.plan_index === hoverIdx);
      if (s) {
        const x = X(s.computed.qvci), y = Y(s.computed.aqi), label = `#${pad2(hoverIdx)}   QVCI ${s.computed.qvci.toFixed(3)}   AQI ${s.computed.aqi.toFixed(3)}`;
        ctx.font = `${9.5 * k}px ${MONO}`;
        const tw = ctx.measureText(label).width + 10;
        let lx = x + 9; if (lx + tw > w - 6) lx = x - tw - 9;
        ctx.fillStyle = P.a("surface", 0.92); ctx.strokeStyle = P.accent; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.roundRect ? ctx.roundRect(lx, y - 14, tw, 16, 3) : ctx.rect(lx, y - 14, tw, 16); ctx.fill(); ctx.stroke();
        ctx.fillStyle = P.ink; ctx.textAlign = "center"; ctx.fillText(label, lx + tw / 2, y - 6);
      }
    }
    return { X, Y };
  }
  function drawPareto() {
    if (pareto.classList.contains("hidden") || opt.planOnly) return;
    const { ctx, w, h: H } = fitCanvas(pcv);
    drawParetoInto(ctx, w, H, palette());
  }
  pcv.addEventListener("pointermove", (e) => {
    const b = pcv.getBoundingClientRect(), mx = e.clientX - b.left, my = e.clientY - b.top;
    const { px, py, side } = paretoGeom(b.width, b.height), [q0, q1, a0, a1] = bounds();
    let best = null, bd = 9;
    for (const s of store.stage4.schematics) {
      const x = px + Math.max(0, Math.min(1, (s.computed.qvci - q0) / (q1 - q0))) * side, y = py + side - Math.max(0, Math.min(1, (s.computed.aqi - a0) / (a1 - a0))) * side;
      const d = Math.hypot(mx - x, my - y);
      if (d < bd) { bd = d; best = s.plan_index; }
    }
    if (best !== hoverIdx) { hoverIdx = best; pcv.style.cursor = best != null ? "pointer" : "default"; drawPareto(); }
  });
  pcv.addEventListener("pointerleave", () => { if (hoverIdx != null) { hoverIdx = null; drawPareto(); } });
  pcv.addEventListener("click", () => { if (hoverIdx != null) toggle(hoverIdx); });
  onResize(pcv.parentElement, drawPareto);
  onResize(gridWrap, () => { sizeCards(); cards.forEach((c) => c.draw()); });

  async function saveParetoImage() {
    const { cv, ctx } = offscreen(600, 600, 4), P = palette();
    ctx.fillStyle = P.surface; ctx.fillRect(0, 0, 600, 600);
    drawParetoInto(ctx, 600, 600, P);
    downloadBlob(await canvasBlob(cv), "pareto_front.png");
  }
  function saveParetoCSV() {
    const sch = store.stage4.schematics, dom = new Set(paretoFrontIndices(sch)), sel = new Set(store.stage4.selected_idxs);
    const rows = [["type", "index", "qvci", "aqi", "d_target", "avg_spl_dB", "zone_spread", "concentration", "pareto_dominant", "selected"]];
    for (const s of sch) { const c = s.computed; rows.push(["plan", s.plan_index, c.qvci, c.aqi, c.d_target ?? "", c.avg_spl_dB ?? "", c.zone_spread ?? "", c.concentration ?? "", dom.has(s.plan_index) ? 1 : 0, sel.has(s.plan_index) ? 1 : 0]); }
    store.stage2.sample_pool.forEach((p, i) => rows.push(["sample", i, p.qvci, p.aqi, p.d_target ?? "", "", "", p.concentration ?? "", "", ""]));
    downloadBlob(new Blob([rows.map((r) => r.join(",")).join("\n") + "\n"], { type: "text/csv" }), "pareto_front.csv");
  }

  function renderPlanPNG(s, size = 900, label = true) {
    const { cv, ctx } = offscreen(size, size + 40, 2), P = palette(), syn = getSynthesis(s);
    paintFrame(ctx, s, syn, syn.frames[syn.frames.length - 1], size, size, P, { showEdges: opt.edges, showWalls: opt.walls, furniture: opt.furn, showLabels: label, fontScale: size / 400 });
    const c = s.computed;
    ctx.font = `${13}px ${MONO}`; ctx.fillStyle = P.ink2; ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.fillText(`#${pad2(s.plan_index)}   QVCI ${Number(c.qvci).toFixed(3)}   AQI ${Number(c.aqi).toFixed(3)}   ${(s.rooms || []).length} rooms`, 16, size + 20);
    return cv;
  }
  async function exportSelected() {
    const st = store.stage4, sel = st.schematics.filter((s) => st.selected_idxs.includes(s.plan_index));
    if (!sel.length) return toast("Select at least one schematic card before exporting.");
    const files = [];
    for (const s of sel) files.push({ name: `schematic_${pad2(s.plan_index)}.png`, blob: await canvasBlob(renderPlanPNG(s)) });
    const saved = files.length === 1 ? await downloadBlob(files[0].blob, files[0].name) : await downloadBlob(await zipBlob(files), "schematics.png.zip");
    if (saved) toast(`Saved ${files.length} plan${files.length > 1 ? "s" : ""}.`);
  }
  async function saveBatch() {
    const st = store.stage4;
    if (!st.schematics.length) return toast("Synthesise schematics before saving a batch sheet.");
    const p = store.inputs.prefs, tq = p.target_qvci, ta = p.target_aqi;
    const ranked = [...st.schematics].sort((a, b) => (Math.abs(a.computed.qvci - tq) + Math.abs(a.computed.aqi - ta)) - (Math.abs(b.computed.qvci - tq) + Math.abs(b.computed.aqi - ta))).slice(0, 9);
    const cols = 3, cell = 340, head = 54, rows = Math.ceil(ranked.length / cols), W = cols * cell, H = head + rows * (cell + 22);
    const { cv, ctx } = offscreen(W, H, 2.5), P = palette();
    ctx.fillStyle = P.surface; ctx.fillRect(0, 0, W, H);
    ctx.font = `600 15px ${FONT}`; ctx.fillStyle = P.ink; ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.fillText("Target2Plan · schematic batch", 14, 18);
    ctx.font = `11px ${FONT}`; ctx.fillStyle = P.ink2;
    ctx.fillText(`Targets QVCI ${tq.toFixed(2)} · AQI ${ta.toFixed(2)} · top ${ranked.length} of ${st.schematics.length} by distance to target`, 14, 38);
    const sheetFit = commonFit(ranked);
    ranked.forEach((s, i) => {
      const x = (i % cols) * cell, y = head + Math.floor(i / cols) * (cell + 22), syn = getSynthesis(s);
      ctx.save(); ctx.translate(x, y); paintFrame(ctx, s, syn, syn.frames[syn.frames.length - 1], cell, cell, P, { showEdges: opt.edges, showWalls: opt.walls, furniture: opt.furn, fontScale: 1.05, fitDims: sheetFit }); ctx.restore();
      ctx.font = `11px ${MONO}`; ctx.fillStyle = P.ink2; ctx.textAlign = "center";
      ctx.fillText(`#${pad2(s.plan_index)}  Q ${s.computed.qvci.toFixed(2)}  A ${s.computed.aqi.toFixed(2)}`, x + cell / 2, y + cell + 8);
    });
    downloadBlob(await canvasBlob(cv), "schematic_batch.png");
  }
  async function exportAnimation() {
    const st = store.stage4;
    const list = st.selected_idxs.length ? st.schematics.filter((s) => st.selected_idxs.includes(s.plan_index)) : st.schematics;
    if (!list.length) return;
    const size = 900, ms = opt.anim > 0 ? opt.anim : 40, files = [];
    let mp4ok = true;
    for (const [k, s] of list.entries()) {
      const syn = getSynthesis(s), n = syn.frames.length, P = palette();
      const delays = syn.frames.map((_, i) => (i === n - 1 ? 2000 : ms));
      const drawFrame = (ctx, i) => {
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.fillStyle = P.surface; ctx.fillRect(0, 0, size, size);
        paintFrame(ctx, s, syn, syn.frames[i], size, size, P, { showEdges: opt.edges, showWalls: opt.walls, furniture: opt.furn, showLabels: true, showCaption: true, fontScale: Math.max(1, size / 400) });
      };
      toast(`Encoding plan #${pad2(s.plan_index)} (${k + 1} of ${list.length})…`, 60000);
      files.push({ name: `schematic_synthesis_${pad2(s.plan_index)}.gif`, blob: await encodeGIF(n, size, drawFrame, delays) });
      if (mp4ok) {
        try {
          const mp4 = await encodeMP4(n, size, drawFrame, delays);
          if (mp4) files.push({ name: `schematic_synthesis_${pad2(s.plan_index)}.mp4`, blob: mp4 }); else mp4ok = false;
        } catch { mp4ok = false; }
      }
    }
    const saved = files.length === 1 ? await downloadBlob(files[0].blob, files[0].name) : await downloadBlob(await zipBlob(files), "schematic_synthesis.zip");
    if (saved) toast(mp4ok ? `Saved ${list.length} animation${list.length > 1 ? "s" : ""} as GIF and MP4.` : `Saved ${list.length} GIF animation${list.length > 1 ? "s" : ""}; ${DESKTOP ? "MP4 encoding is not available on this computer" : "this browser cannot encode MP4"}.`);
  }

  store.on("stage4", () => { if (store.stage4.schematics !== lastSch) { lastSch = store.stage4.schematics; render(); } else refreshSelection(); });
  store.on("stage3", () => { if (!store.stage4.schematics.length) render(); else refreshToolbar(); });
  store.on("theme", () => { cards.forEach((c) => c.draw()); drawPareto(); });
  store.on("reset", () => { lastSch = store.stage4.schematics; render(); });
  store.on("goto:schematic", () => regenerate());
  setFixed(opt.fixed);
  render();
  return { rebuild: (selected) => { regenerate(); if (selected) store.setStage4Selection(selected); } };
}
