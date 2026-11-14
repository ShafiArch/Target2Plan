// planEditor.js: "Edit selected" in Stage 4. The selected plans open one at a
// time on a canvas like the drawing canvas of Stage 1: rooms move and resize
// (snapping to each other's edges and centres, undo / redo, arrow keys, R to
// turn), and walls, doors, windows, furniture and the room and plan scores
// follow every change. Replace puts the edit into the plan, Save also saves
// the session, Export writes the plan as drawn, Discard drops the edit.
import { h, palette, pad2, fmt, toast, confirmAsk, FONT, ZONE_LABELS, downloadBlob } from "./common.js";
import { GeometryCanvas, MOD_KEY } from "./geometry.js";
import { paintFrame, originalSynthesis, FURNITURE_STYLES } from "./schematicPaint.js";
import { overlapsIn } from "../engine/layout.js";
import { applyEdit, currentLayout, originalLayout, previewEdit, sameLayout } from "./planEdit.js";
import { EXPORT_FORMATS, exportPlan } from "./planExport.js";

const SIDES = ["N", "E", "S", "W"];

const COARSE = () => window.matchMedia?.("(pointer: coarse)").matches;

class PlanCanvas extends GeometryCanvas {
  constructor(wrap, ed) {
    super(wrap, () => ed.changed(), { allowAdd: false, allowRemove: false, allowClipboard: false, minSide: 1.0 });
    this.ed = ed;
    this.fitMargin = 56;
    this.pending = 0;
    this.selKey = "";
  }
  // Pointer moves come faster than the screen refreshes: the plan is rebuilt
  // (walls, doors, windows, furniture, scores) and drawn once per frame.
  changed() { if (!this.pending) this.pending = requestAnimationFrame(() => this.flush()); }
  /** Rebuild and draw now (before an export reads the rebuilt plan). */
  flush() {
    if (this.pending) { cancelAnimationFrame(this.pending); this.pending = 0; }
    this.onChange?.(); this.draw();
  }
  destroy() { if (this.pending) cancelAnimationFrame(this.pending); this.pending = 0; super.destroy(); }
  hintText() {
    if (COARSE()) return "drag a room or an edge · pinch to zoom and pan";
    return `wheel to zoom · middle-drag or Space + drag to pan · drag a room or an edge · ${MOD_KEY}+Z undo · arrow keys nudge · R turns a room · Alt: no snapping`;
  }
  draw() {
    const key = [...this.selected].sort().join("|");
    if (key !== this.selKey) { this.selKey = key; this.ed.onSelect?.(); }
    const { ctx, w, h: H } = this.fitCanvasSafe();
    if (!ctx) return;
    const P = palette(), ed = this.ed;
    this.drawGrid(ctx, P, w, H, 0.3);
    if (ed.view) {
      const syn = ed.view.syn, frame = syn.frames[syn.frames.length - 1], [ox, oy] = this.origin();
      paintFrame(ctx, ed.s, syn, frame, w, H, P, {
        transform: { scale: this.pxPerM / syn.metre, ox, oy }, showEdges: ed.opt.edges, showWalls: true,
        furniture: ed.opt.furn, showLabels: true, fontScale: Math.max(0.8, Math.min(2.4, this.pxPerM / 30)),
      });
    }
    // rooms that overlap, the selection with its handles, its size
    const bad = new Set((ed.overlaps || []).flat());
    for (const r of this.rooms) {
      const [x, y, rw, rh] = this.rectPx(r), sel = this.selected.has(r.id);
      if (bad.has(r.id)) { ctx.save(); ctx.strokeStyle = P.bad; ctx.lineWidth = 2; ctx.setLineDash([5, 3]); ctx.strokeRect(x + 1, y + 1, rw - 2, rh - 2); ctx.restore(); }
      if (sel) { ctx.strokeStyle = P.accent; ctx.lineWidth = 2; ctx.strokeRect(x, y, rw, rh); }
    }
    const sel = this.selectedRooms();
    if (sel.length === 1) {
      const r = sel[0], [x, y, rw, rh] = this.rectPx(r);
      this.drawHandles(ctx, P, x, y, rw, rh);
      const t = `${r.w.toFixed(2)} × ${r.h.toFixed(2)} m · ${(r.w * r.h).toFixed(1)} m²`;
      ctx.font = `500 10px ${FONT}`; ctx.textAlign = "center"; ctx.textBaseline = "top";
      const tw = ctx.measureText(t).width + 10, ty = y + rh + 8;
      ctx.fillStyle = P.accent; ctx.beginPath(); ctx.rect(x + rw / 2 - tw / 2, ty, tw, 16); ctx.fill();
      ctx.fillStyle = "#fff"; ctx.fillText(t, x + rw / 2, ty + 3);
    }
    this.drawGuides(ctx, P);
    this.drawBox(ctx, P);
    // north
    ctx.font = `600 10px ${FONT}`; ctx.fillStyle = P.accent; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText("N ↑", 22, 16);
  }
  fitCanvasSafe() {
    const r = this.cv.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return {};
    const dpr = Math.min(window.devicePixelRatio || 1, 3), pw = Math.round(r.width * dpr), ph = Math.round(r.height * dpr);
    if (this.cv.width !== pw || this.cv.height !== ph) { this.cv.width = pw; this.cv.height = ph; }
    const ctx = this.cv.getContext("2d");
    ctx.setTransform(pw / r.width, 0, 0, ph / r.height, 0, 0);
    ctx.clearRect(0, 0, r.width, r.height);
    return { ctx, w: r.width, h: r.height };
  }
}

/**
 * Open the editor on `plans` (Stage 4 schematics). `opt` holds the Stage 4 view
 * options (edges, furniture style); `onApply(s)` is called after a plan
 * changed; `saveSession()` saves the session file.
 */
export function openPlanEditor(plans, { store, opt, onApply, saveSession }) {
  if (!plans.length) return;
  const ed = { s: null, opt: { ...opt }, view: null, overlaps: [], changed: () => {}, onSelect: null };
  const drafts = new Map();          // plan_index -> {rooms, undo, redo, selected}
  let cur = -1;

  // ── header ─────────────────────────────────────────────────────────
  const idxEl = h("span.idx"), countEl = h("span.pe-count");
  const bPrev = h("button.icon-btn", { title: "Previous selected plan", on: { click: () => go(cur - 1) } }, "‹");
  const bNext = h("button.icon-btn", { title: "Next selected plan", on: { click: () => go(cur + 1) } }, "›");
  const switcher = h("span.pe-switch", bPrev, countEl, bNext);
  const vNow = h("b.pe-v"), aNow = h("b.pe-v"), refEl = h("span.pe-ref");
  const metrics = h("span.pe-metrics", h("span.pe-lab", "As drawn"), h("span.pe-k", "V"), vNow, h("span.pe-k", "A"), aNow, refEl);
  const bClose = h("button.icon-btn", { title: "Close (Esc)", on: { click: () => requestClose() } }, "✕");
  const head = h("div.pe-head", h("span.h", "EDIT PLAN"), idxEl, switcher, h("span.grow"), metrics, bClose);

  // ── tools ──────────────────────────────────────────────────────────
  const bUndo = h("button", { title: `Undo (${MOD_KEY}+Z)`, "aria-label": "Undo", on: { click: () => canvas.undo() } }, "↶");
  const bRedo = h("button", { title: `Redo (${MOD_KEY}+Shift+Z)`, "aria-label": "Redo", on: { click: () => canvas.redo() } }, "↷");
  const bSnap = h("button.snap", { title: "Snap edges and centres to the other rooms (hold Alt to place freely)", on: { click: () => { canvas.setSnap(!canvas.snapOn); syncSnap(); } } }, "Snap");
  const syncSnap = () => { bSnap.classList.toggle("on", canvas.snapOn); bSnap.setAttribute("aria-pressed", String(canvas.snapOn)); };
  const bReset = h("button.link-btn", { title: "Back to the plan as it was generated", on: { click: () => { canvas.record(); canvas.rooms = originalLayout(ed.s); canvas.selected = new Set(); canvas.changed(); } } }, "Reset to generated");
  const cEdges = h("button.chip" + (ed.opt.edges ? ".active" : ""), { title: "Dashed connectivity edges", on: { click: () => { ed.opt.edges = !ed.opt.edges; cEdges.classList.toggle("active", ed.opt.edges); canvas.draw(); } } }, "● Edges");
  const furnSel = h("select", { "aria-label": "Furniture style" }, ...FURNITURE_STYLES.map(([v, l]) => h("option", { value: v }, l)));
  furnSel.value = ed.opt.furn;
  furnSel.addEventListener("change", () => { ed.opt.furn = furnSel.value; canvas.draw(); });
  const zoomPct = h("button.zoom-pct", { title: "Back to 100 %", on: { click: () => canvas.setScale(GeometryCanvas.BASE_PX_PER_M) } }, "100%");
  const zoomCtl = h("div.zoom-ctl", h("button", { title: "Zoom out (-)", on: { click: () => canvas.zoomBy(1 / 1.25) } }, "−"), zoomPct, h("button", { title: "Zoom in (+)", on: { click: () => canvas.zoomBy(1.25) } }, "+"), h("button.fit", { title: "Fit the plan (0)", on: { click: () => canvas.fit() } }, "Fit"));
  const tools = h("div.pe-tools", h("div.zoom-ctl.edit-ctl", bUndo, bRedo, bSnap), bReset, h("span.tsep"), cEdges, h("label.chip.chip-select.active", "▦ Furniture", furnSel), h("span.grow"), zoomCtl);

  // ── body: canvas and the side panel ─────────────────────────────────
  const canvasWrap = h("div.pe-canvas");
  const selSec = h("div.detail-sec"), roomsSec = h("div.detail-sec"), checkSec = h("div.detail-sec");
  const side = h("div.pe-side", selSec, checkSec, roomsSec);
  const body = h("div.pe-body", canvasWrap, side);

  // ── footer ─────────────────────────────────────────────────────────
  const bDiscard = h("button.btn-ghost", { title: "Drop the changes to this plan", on: { click: () => discard() } }, "Discard");
  const bSave = h("button.btn-ghost", { title: "Put the changes into the plan and save the session file", on: { click: () => save() } }, "Save");
  const bExport = h("button.btn-ghost", { title: "Save the plan as drawn as JPG, PNG, DXF or PDF", on: { click: (e) => { e.stopPropagation(); toggleMenu(); } } }, "Export ▾");
  const menu = h("div.pe-menu.hidden", ...EXPORT_FORMATS.map(([fmtId, label, sub]) => h("button", { on: { click: () => { hideMenu(); doExport(fmtId); } } }, h("span", label), h("span.sub", sub))));
  const bReplace = h("button.btn-primary", { title: "Put the changes into the plan", on: { click: () => replace() } }, "Replace  →");
  const statusEl = h("span.pe-status");
  const foot = h("div.pe-foot", statusEl, h("span.grow"), bDiscard, bSave, h("span.pe-export", bExport, menu), bReplace);

  const panel = h("div.plan-editor", { role: "dialog", "aria-label": "Edit plan" }, head, tools, body, foot);
  document.body.append(panel);
  const canvas = new PlanCanvas(canvasWrap, ed);
  canvasWrap.geom = canvas;     // reachable from the page (tests, console)
  canvas.onZoom = () => { zoomPct.textContent = `${canvas.zoomPercent()}%`; };
  canvas.onHistory = () => { bUndo.disabled = !canvas.canUndo(); bRedo.disabled = !canvas.canRedo(); };
  syncSnap();

  // ── live rebuild ───────────────────────────────────────────────────
  let sideTimer = 0;
  ed.changed = () => {
    if (!ed.s) return;
    ed.view = previewEdit(ed.s, canvas.rooms, store.inputs);
    ed.overlaps = overlapsIn(canvas.rooms);
    const c = ed.view.scored.computed;
    vNow.textContent = fmt(c.qvci, 2); aNow.textContent = fmt(c.aqi, 2);
    cancelAnimationFrame(sideTimer);
    sideTimer = requestAnimationFrame(refreshSide);
  };
  ed.onSelect = () => { cancelAnimationFrame(sideTimer); sideTimer = requestAnimationFrame(refreshSide); };
  const dirty = () => !!ed.s && !sameLayout(canvas.rooms, currentLayout(ed.s));

  function refreshSide() {
    if (!ed.view) return;
    const s = ed.s, scored = ed.view.scored, byId = new Map(scored.rooms.map((r) => [r.room_id, r]));
    // the selected room: size inputs, zone and scores as drawn
    const sel = canvas.selectedRooms();
    selSec.replaceChildren(h("div.h", "SELECTED ROOM"));
    if (sel.length !== 1) selSec.append(h("div.hint", sel.length ? `${sel.length} rooms selected: drag to move them together, arrow keys nudge.` : "Click a room to select it; drag it or an edge or corner to change the plan."));
    else {
      const r = sel[0], rec = byId.get(r.id) || {};
      const num = (val, set) => {
        const inp = h("input", { type: "number", min: 1, max: 40, step: 0.05, value: val.toFixed(2) });
        inp.addEventListener("change", () => { const v = Number(inp.value); if (Number.isFinite(v) && v >= 1 && v <= 40) { canvas.record(); set(Math.round(v * 1000) / 1000); canvas.changed(); } else inp.value = val.toFixed(2); });
        return inp;
      };
      const exp = rec.exposure || {}, sides = SIDES.filter((d) => (exp[d] || 0) > 0.05);
      const zoneTxt = (z) => (z == null ? "-" : `${ZONE_LABELS[z]}`);
      selSec.append(...[
        h("div.pe-room-name", r.id.replace(/_/g, " ")),
        h("div.pe-size",
          h("label", h("span.pe-lab", "Width (E-W)"), h("span.pe-num", num(r.w, (v) => { r.w = v; }), h("span", "m"))),
          h("label", h("span.pe-lab", "Depth (N-S)"), h("span.pe-num", num(r.h, (v) => { r.h = v; }), h("span", "m")))),
        kv("Area", `${(r.w * r.h).toFixed(1)} m²`),
        rec.zoned ? kv("Zone as drawn", `${zoneTxt(rec.zone_assignment)}${rec.planned_zone != null && rec.planned_zone !== rec.zone_assignment ? `  (zone plan: ${zoneTxt(rec.planned_zone)})` : ""}`) : kv("Zone", "not zoned"),
        kv("Exterior walls", sides.length ? `${sides.map((d) => `${d} ${exp[d].toFixed(1)}`).join(" · ")} m` : "none"),
        rec.quality_view_score != null ? kv("View score QV", fmt(rec.quality_view_score, 3)) : null,
        rec.acoustic_score != null ? kv("Acoustic score AQ", fmt(rec.acoustic_score, 3)) : null,
        rec.est_avg_spl_dB != null ? kv("Indoor level", `${fmt(rec.est_avg_spl_dB, 1)} dB(A)  (guideline ${rec.guideline_dB})`) : null,
      ].filter(Boolean));
    }
    // checks: overlaps, connected rooms without a door, the entrance
    const notes = [];
    for (const [a, b] of ed.overlaps) notes.push(`${a.replace(/_/g, " ")} and ${b.replace(/_/g, " ")} overlap`);
    const doors = new Set(ed.view.syn.openings.filter((o) => o.kind !== "window" && o.rooms).map((o) => [...o.rooms].sort().join("|")));
    for (const e of s.edges || []) {
      if (String(e.source).startsWith("outside") || String(e.target).startsWith("outside")) continue;
      if (!canvas.rooms.some((r) => r.id === e.source) || !canvas.rooms.some((r) => r.id === e.target)) continue;
      if (!doors.has([e.source, e.target].sort().join("|"))) notes.push(`${e.source.replace(/_/g, " ")} and ${e.target.replace(/_/g, " ")} are connected but share no wall long enough for a door (about 1 m)`);
    }
    if (!ed.view.syn.openings.some((o) => o.kind === "entry")) notes.push("No entrance: the entrance room needs a free exterior wall for its door (about 1 m)");
    checkSec.replaceChildren(h("div.h", "CHECKS"), notes.length ? h("ul.pe-notes", ...notes.map((n) => h("li", n))) : h("div.hint.pe-ok", "✓ No overlaps; every connection has its door; the entrance is in place."));
    // every room: zone, QV, AQ as drawn
    const rows = scored.rooms.map((r) => h("tr.clickable" + (canvas.selected.has(r.room_id) ? ".sel" : ""), { on: { click: () => { canvas.selected = new Set([r.room_id]); canvas.draw(); refreshSide(); } } },
      h("td", r.room_id.replace(/_/g, " ")), h("td", fmt(r.area_m2, 1)),
      h("td", r.zone_assignment == null ? "-" : ZONE_LABELS[r.zone_assignment] + (r.planned_zone != null && r.planned_zone !== r.zone_assignment ? "*" : "")),
      h("td" + (r.quality_view_score == null ? ".dim" : r.scored && r.quality_view_score >= 0.5 ? ".acc" : ""), fmt(r.quality_view_score, 2)),
      h("td" + (r.acoustic_score == null ? ".dim" : r.scored && r.acoustic_ok ? ".acc" : ""), fmt(r.acoustic_score, 2))));
    roomsSec.replaceChildren(h("div.h", `ROOMS (${scored.rooms.length})`),
      h("table.detail-tbl", h("thead", h("tr", h("th", "Room"), h("th", "m²"), h("th", "Zone"), h("th", "QV"), h("th", "AQ"))), h("tbody", ...rows)),
      h("div.hint", "As drawn: each room takes the zone of the façades it touches (an exterior wall of at least 1.2 m). * differs from the zone plan."));
    const d = dirty();
    statusEl.textContent = d ? "Changed · not in the plan yet" : ed.s.edit ? "Edited plan" : "As generated";
    statusEl.classList.toggle("changed", d);
  }

  function kv(k, v) { return h("div.kv", h("span", k), h("span", v)); }

  // ── plans ──────────────────────────────────────────────────────────
  function stash() {
    if (cur < 0) return;
    drafts.set(plans[cur].plan_index, { rooms: canvas.rooms.map((r) => ({ ...r })), undo: canvas.undoStack, redo: canvas.redoStack, selected: [...canvas.selected] });
  }
  function load(i) {
    stash();
    cur = i;
    const s = plans[i], d = drafts.get(s.plan_index);
    ed.s = s;
    originalSynthesis(s);
    canvas.rooms = d ? d.rooms.map((r) => ({ ...r })) : currentLayout(s);
    canvas.undoStack = d ? d.undo : []; canvas.redoStack = d ? d.redo : [];
    canvas.selected = new Set(d ? d.selected : []);
    canvas.counter = {};
    idxEl.textContent = `#${pad2(s.plan_index)}`;
    countEl.textContent = `${i + 1} of ${plans.length}`;
    switcher.classList.toggle("hidden", plans.length < 2);
    bPrev.disabled = i === 0; bNext.disabled = i === plans.length - 1;
    const pc = (s.orig || s).computed || {}, tg = s.targets || {};
    refEl.replaceChildren(...[h("span", `Zone plan V ${fmt(pc.qvci, 2)} · A ${fmt(pc.aqi, 2)}`), tg.present ? h("span", `Target V ${fmt(tg.qvci, 2)} · A ${fmt(tg.aqi, 2)}`) : null].filter(Boolean));
    canvas.onHistory();
    ed.changed();
    requestAnimationFrame(() => { canvas.fit(); canvas.cv.focus(); });
  }
  function go(i) { if (i >= 0 && i < plans.length && i !== cur) load(i); }

  // ── actions ────────────────────────────────────────────────────────
  function applyCurrent() {
    const s = ed.s;
    applyEdit(s, canvas.rooms.map((r) => ({ ...r })), store.inputs);
    drafts.delete(s.plan_index);
    canvas.resetHistory();
    onApply?.(s);
    ed.changed();
    return s;
  }
  function nextOrClose() {
    drafts.delete(plans[cur].plan_index);
    const rest = plans.findIndex((p, k) => k > cur);
    if (rest >= 0) { cur = -1; load(rest); } else close();
  }
  function replace() {
    if (ed.overlaps.length) {
      confirmAsk("Some rooms overlap. Put the plan in as it is?").then((ok) => { if (ok) { const s = applyCurrent(); toast(`Plan #${pad2(s.plan_index)} replaced.`); nextOrClose(); } });
      return;
    }
    const s = applyCurrent();
    toast(s.edit ? `Plan #${pad2(s.plan_index)} replaced with the edited plan.` : `Plan #${pad2(s.plan_index)} is back as generated.`);
    nextOrClose();
  }
  async function save() {
    const s = applyCurrent();
    const saved = await saveSession?.();
    toast(saved === false ? `Plan #${pad2(s.plan_index)} replaced; the session file was not saved.` : `Plan #${pad2(s.plan_index)} replaced and the session saved.`);
  }
  async function discard() {
    if (dirty() && !(await confirmAsk(`Drop the changes to plan #${pad2(ed.s.plan_index)}?`))) return;
    nextOrClose();
  }
  async function doExport(fmtId) {
    canvas.flush();
    try {
      const { blob, name } = await exportPlan(fmtId, { s: ed.s, view: ed.view, rooms: canvas.rooms, opt: ed.opt, edited: dirty() || !!ed.s.edit });
      if (await downloadBlob(blob, name)) toast(`Saved ${name}.`);
    } catch (err) { toast(`Could not export: ${err.message || err}`); }
  }
  function toggleMenu() { menu.classList.toggle("hidden"); }
  function hideMenu() { menu.classList.add("hidden"); }
  const outside = (e) => { if (!menu.contains(e.target) && e.target !== bExport) hideMenu(); };

  async function requestClose() {
    stash();
    const pending = plans.filter((p) => { const d = drafts.get(p.plan_index); return d && !sameLayout(d.rooms, currentLayout(p)); });
    if (pending.length && !(await confirmAsk(`Drop the changes to ${pending.length === 1 ? `plan #${pad2(pending[0].plan_index)}` : `${pending.length} plans`}?`))) return;
    close();
  }
  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); if (!menu.classList.contains("hidden")) hideMenu(); else requestClose(); }
  };
  function close() {
    window.removeEventListener("keydown", onKey);
    window.removeEventListener("pointerdown", outside, true);
    cancelAnimationFrame(sideTimer);
    canvas.destroy();
    panel.remove();
  }
  window.addEventListener("keydown", onKey);
  window.addEventListener("pointerdown", outside, true);
  load(0);
  return { close, canvas };
}
