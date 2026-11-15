// planEditor.js: "Edit selected" in Stage 4 (and "Edit plan" in a plan's detail
// view). The plans open one at a time on a canvas like the drawing canvas of
// Stage 1: rooms move and resize (snapping to each other's edges and centres,
// undo / redo, arrow keys, R to turn), Ctrl+D turns the door between two
// selected rooms, and walls, doors, windows, furniture and the room and plan
// scores follow every change. Fill and Text switch the room colours (off: a
// line drawing) and the room labels. A floating panel, folded to one bar until
// it is opened, holds the selected room, the checks, the rooms and the Pareto
// front with this plan moving on it. Replace puts the edit into the plan,
// Previous and Next go through the plans, Save also saves the session, Export
// writes the plan as drawn, Discard drops the changes.
import { h, palette, pad2, fmt, toast, confirmAsk, fitCanvas, onResize, FONT, MONO, ZONE_LABELS, downloadBlob } from "./common.js";
import { GeometryCanvas, MOD_KEY } from "./geometry.js";
import { paintFrame, originalSynthesis, FURNITURE_STYLES } from "./schematicPaint.js";
import { overlapsIn } from "../engine/layout.js";
import { paretoFrontIndices, doorKey, doorEnd } from "../engine/schematic.js";
import { applyEdit, currentDoors, currentLayout, originalLayout, previewEdit, sameDoors, sameLayout } from "./planEdit.js";
import { EXPORT_FORMATS, exportPlan } from "./planExport.js";

const SIDES = ["N", "E", "S", "W"];
const COARSE = () => window.matchMedia?.("(pointer: coarse)").matches;
const PHONE = () => window.matchMedia?.("(max-width: 760px)").matches;
const LS = (k, d) => { try { const v = localStorage.getItem(`t2p.${k}`); return v == null ? d : JSON.parse(v); } catch { return d; } };
const SS = (k, v) => { try { localStorage.setItem(`t2p.${k}`, JSON.stringify(v)); } catch { /* storage unavailable */ } };
const roomName = (id) => String(id).replace(/_/g, " ");
const OPEN_FIT = 0.49;       // computers open the plan at about half the full fit, centred; phones fill the screen
const SIDE_MS = 150;         // while a room is dragged, the panel follows about seven times a second (the plan every frame)
const setText = (el, t) => { if (el.textContent !== t) el.textContent = t; };   // an unchanged text leaves the layout alone
const isDoorKey = (e) => (e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "d";

/** The door (or balcony door) between rooms a and b in a built plan, or null. */
function doorBetween(syn, a, b) {
  const key = doorKey(a, b);
  return syn.openings.find((o) => (o.kind === "door" || o.kind === "balcony_door") && o.rooms && doorKey(o.rooms[0], o.rooms[1]) === key) || null;
}
/** Where a door sits, in words: the end of its wall by compass side. */
function doorWhere(o) {
  const along = Math.abs(o.wall.y1 - o.wall.y2) < 1e-6 ? ["west", "east"] : ["north", "south"];
  return `${along[doorEnd(o)]} end`;
}

class PlanCanvas extends GeometryCanvas {
  constructor(wrap, ed) {
    super(wrap, () => ed.changed(), { allowAdd: false, allowRemove: false, allowClipboard: false, minSide: 1.0 });
    this.ed = ed;
    this.fitMargin = 56;
    this.pending = 0;
    this.selKey = "";
    this.doors = {};          // doors turned with Ctrl+D: {doorKey: {end, into}}
  }
  // the doors turned are part of each undo step
  snapshot() { return { ...super.snapshot(), doors: { ...this.doors } }; }
  restore(st) { super.restore(st); this.doors = { ...(st.doors || {}) }; }
  key(e) {
    if (isDoorKey(e)) { e.preventDefault(); e.stopPropagation(); this.ed.flipDoor?.(); return; }
    super.key(e);
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
  /** The part of the canvas the floating panel covers, as insets in px. */
  insets() { return this.ed.insets?.() || { l: 0, t: 0, r: 0, b: 0 }; }
  /** Fit the plan in the free part of the canvas at `f` of the full fit. */
  fit(f = 1) {
    const r = this.cv.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    const [x0, y0, x1, y1] = this.extent(), m = this.fitMargin, i = this.insets();
    const W = r.width - i.l - i.r, H = r.height - i.t - i.b;
    const s = Math.min((W - 2 * m) / Math.max(x1 - x0, 1), (H - 2 * m) / Math.max(y1 - y0, 1)) * f;
    this.pxPerM = Math.max(GeometryCanvas.MIN_PX_PER_M, Math.min(GeometryCanvas.MAX_PX_PER_M, s));
    this.autoCentre = true;
    this.centre();
    this.onZoom?.();
  }
  /** Centre the plan in the free part of the canvas at the present zoom. */
  centre() {
    const r = this.cv.getBoundingClientRect();
    if (!this.rooms.length || r.width <= 0) { this.draw(); return; }
    const s = this.pxPerM, [x0, y0, x1, y1] = this.extent(), i = this.insets();
    const cx = i.l + (r.width - i.l - i.r) / 2, cy = i.t + (r.height - i.t - i.b) / 2;
    this.pan = [cx - ((x0 + x1) / 2) * s - this.pad, cy - ((y0 + y1) / 2) * s - this.pad];
    this.draw();
  }
  hintText() {
    if (COARSE()) return "drag a room or an edge · pinch to zoom and pan";
    return `drag a room or an edge · arrow keys nudge · R turns a room · ${MOD_KEY}+D: door flip (select both rooms) · Alt: no snapping`;
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
        furniture: ed.opt.furn, fill: ed.opt.fill, showLabels: ed.opt.text, fontScale: Math.max(0.8, Math.min(2.4, this.pxPerM / 30)),
      });
    }
    // rooms that overlap, the selection with its handles, its size
    const bad = new Set((ed.overlaps || []).flat());
    for (const r of this.rooms) {
      const [x, y, rw, rh] = this.rectPx(r), sel = this.selected.has(r.id);
      if (bad.has(r.id)) { ctx.save(); ctx.strokeStyle = P.a("ink3", 0.9); ctx.lineWidth = 1.2; ctx.setLineDash([5, 3]); ctx.strokeRect(x + 1, y + 1, rw - 2, rh - 2); ctx.restore(); }
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
    if (!this.size) this.measure();               // the size is measured on resize, not every frame
    if (!this.size) return {};
    const [w, h] = this.size;
    const dpr = Math.min(window.devicePixelRatio || 1, 3), pw = Math.round(w * dpr), ph = Math.round(h * dpr);
    if (this.cv.width !== pw || this.cv.height !== ph) { this.cv.width = pw; this.cv.height = ph; }
    const ctx = this.cv.getContext("2d");
    ctx.setTransform(pw / w, 0, 0, ph / h, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx, w, h };
  }
}

/** Round tick values between lo and hi (about n of them). */
function niceTicks(lo, hi, n = 4) {
  const span = hi - lo;
  if (!(span > 0)) return [lo];
  const raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= raw) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

/**
 * Open the editor on `plans` (Stage 4 schematics). `opt` holds the Stage 4 view
 * options (edges, furniture style; the editor adds fill and text, remembered);
 * `onApply(s)` is called after a plan changed; `saveSession()` saves the session file.
 */
export function openPlanEditor(plans, { store, opt, onApply, saveSession }) {
  if (!plans.length) return;
  const ed = { s: null, opt: { ...opt, fill: !!LS("peFill", true), text: !!LS("peText", true) }, view: null, overlaps: [], changed: () => {}, onSelect: null, insets: null, flipDoor: null };
  const drafts = new Map();          // plan_index -> {rooms, doors, undo, redo, selected}
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
  const bSnap = h("button.snap", { title: "Snap edges and centres to the other rooms (hold Alt to place freely)", on: { click: () => { canvas.setSnap(!canvas.snapOn); syncToggles(); } } }, "Snap");
  const bGrid = h("button.snap", { title: "Show or hide the dotted grid", on: { click: () => { canvas.setGrid(!canvas.gridOn); syncToggles(); } } }, "Grid");
  const syncToggles = () => {
    for (const [b, on] of [[bSnap, canvas.snapOn], [bGrid, canvas.gridOn]]) { b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); }
  };
  const bReset = h("button.link-btn", { title: "Back to the plan as it was generated", on: { click: () => { canvas.record(); canvas.rooms = originalLayout(ed.s); canvas.doors = {}; canvas.selected = new Set(); canvas.changed(); } } }, "Reset to generated");
  const cEdges = h("button.chip" + (ed.opt.edges ? ".active" : ""), { title: "Dashed connectivity edges", on: { click: () => { ed.opt.edges = !ed.opt.edges; cEdges.classList.toggle("active", ed.opt.edges); canvas.draw(); } } }, "● Edges");
  const furnSel = h("select", { "aria-label": "Furniture style" }, ...FURNITURE_STYLES.map(([v, l]) => h("option", { value: v }, l)));
  furnSel.value = ed.opt.furn;
  furnSel.addEventListener("change", () => { ed.opt.furn = furnSel.value; canvas.draw(); });
  // Fill off: a line drawing (walls, doors, windows, furniture; balconies outlined in light grey).
  // Text: the room names and scores. Both remembered; the exports follow them.
  const cFill = h("button.chip", { title: "Room colours on, or off for a line drawing: walls, doors, windows and furniture (exports follow)", on: { click: () => { ed.opt.fill = !ed.opt.fill; SS("peFill", ed.opt.fill); syncView(); canvas.draw(); } } });
  const cText = h("button.chip", { title: "Show or hide the room names and scores on the plan (exports follow)", on: { click: () => { ed.opt.text = !ed.opt.text; SS("peText", ed.opt.text); syncView(); canvas.draw(); } } }, "Aa Text");
  const syncView = () => {
    cFill.textContent = ed.opt.fill ? "■ Fill" : "□ Fill";
    for (const [c, on] of [[cFill, ed.opt.fill], [cText, ed.opt.text]]) { c.classList.toggle("active", on); c.setAttribute("aria-pressed", String(on)); }
  };
  syncView();
  const zoomPct = h("button.zoom-pct", { title: "Back to 100 %", on: { click: () => canvas.setScale(GeometryCanvas.BASE_PX_PER_M) } }, "100%");
  const zoomCtl = h("div.zoom-ctl", h("button", { title: "Zoom out (-)", on: { click: () => canvas.zoomBy(1 / 1.25) } }, "−"), zoomPct, h("button", { title: "Zoom in (+)", on: { click: () => canvas.zoomBy(1.25) } }, "+"), h("button.fit", { title: "Fit the plan (0)", on: { click: () => canvas.fit() } }, "Fit"));
  const tools = h("div.pe-tools", h("div.zoom-ctl.edit-ctl", bUndo, bRedo, bSnap, bGrid), bReset, h("span.tsep"), cEdges, h("label.chip.chip-select.active", "▦ Furniture", furnSel), cFill, cText, h("span.grow"), zoomCtl);

  // ── body: the canvas, and a panel floating over it ────────────────────
  // Folded, the panel is one bar (the selected room); the grey button opens it
  // (selected room, checks, rooms, Pareto front) and it stays open until folded.
  const canvasWrap = h("div.pe-canvas");
  const barName = h("span.pe-bar-name");
  const bFold = h("button.pe-fold", { on: { click: () => setOpen(!isOpen()) } });
  const bar = h("div.pe-bar", h("span.pe-lab", "Selected room"), barName, h("span.grow"), bFold);
  const selSec = h("div.detail-sec"), checkSec = h("div.detail-sec"), roomsSec = h("div.detail-sec");
  const fbody = h("div.pe-fbody", selSec, checkSec, roomsSec);
  const pcv = h("canvas"), plotEl = h("div.pe-pareto-plot", pcv);
  let plotSize = null;      // the chart's size, measured when it changes (folded: none)
  const paretoSec = h("div.pe-pareto.detail-sec",
    h("div.h", "PARETO FRONT"),
    plotEl,
    h("div.pe-legend", h("span.pe-key.me"), "this plan as drawn", h("span.pe-key.zp"), "zone plan", h("span.pe-key.tg"), "target", h("span.pe-key.ot"), "other plans"));
  const float = h("div.pe-float", bar, fbody, paretoSec);
  const body = h("div.pe-body", canvasWrap, float);
  const isOpen = () => float.classList.contains("open");
  ed.insets = () => {
    const c = canvasWrap.getBoundingClientRect(), f = float.getBoundingClientRect(), none = { l: 0, t: 0, r: 0, b: 0 };
    if (!f.width || !c.width) return none;
    if (PHONE()) return { ...none, b: Math.max(0, c.bottom - f.top + 8) };     // a sheet along the bottom
    return isOpen() ? { ...none, r: Math.max(0, c.right - f.left + 8) } : none; // a column on the right
  };

  // ── footer ─────────────────────────────────────────────────────────
  const bDiscard = h("button.btn-ghost", { title: `Drop the changes since the plan was last replaced (${MOD_KEY}+Z brings them back)`, on: { click: () => discard() } }, "Discard");
  const bSave = h("button.btn-ghost", { title: "Put the changes into the plan and save the session file", on: { click: () => save() } }, "Save");
  const bExport = h("button.btn-ghost", { title: "Save the plan as drawn as JPG, PNG, DXF or PDF", on: { click: (e) => { e.stopPropagation(); toggleMenu(); } } }, "Export ▾");
  const menu = h("div.pe-menu.hidden", ...EXPORT_FORMATS.map(([fmtId, label, sub]) => h("button", { on: { click: () => { hideMenu(); doExport(fmtId); } } }, h("span", label), h("span.sub", sub))));
  const bReplace = h("button.btn-primary", { title: "Put the changes into the plan", on: { click: () => replace() } }, "Replace");
  const bGoPrev = h("button.btn-ghost.pe-nav.pe-prev", { title: "Previous selected plan (changes you have not put in stay here as a draft)", on: { click: () => go(cur - 1) } }, "←  Previous");
  const bGoNext = h("button.btn-ghost.pe-nav.pe-next", { title: "Next selected plan (changes you have not put in stay here as a draft)", on: { click: () => go(cur + 1) } }, "Next  →");
  const statusEl = h("span.pe-status");
  const foot = h("div.pe-foot", statusEl, h("span.grow"), bDiscard, bSave, h("span.pe-export", bExport, menu), bReplace, bGoPrev, bGoNext);

  const panel = h("div.plan-editor", { role: "dialog", "aria-label": "Edit plan" }, head, tools, body, foot);
  document.body.append(panel);
  const canvas = new PlanCanvas(canvasWrap, ed);
  canvasWrap.geom = canvas;     // reachable from the page (tests, console)
  canvas.onZoom = () => { zoomPct.textContent = `${canvas.zoomPercent()}%`; };
  canvas.onHistory = () => { bUndo.disabled = !canvas.canUndo(); bRedo.disabled = !canvas.canRedo(); };
  syncToggles();

  function setOpen(on, remember = true) {
    float.classList.toggle("open", on);
    bFold.textContent = on ? "▴" : "▾";
    bFold.title = on ? "Fold the panel" : "Open the panel: selected room, checks, rooms, Pareto front";
    bFold.setAttribute("aria-expanded", String(on));
    if (remember) SS("peOpen", on);
    // an untouched view follows: on phones the sheet covers much of the plan, so it fits again
    if (canvas.autoCentre) { if (PHONE()) canvas.fit(1); else canvas.centre(); }
    refreshSide();
  }

  // ── live rebuild ───────────────────────────────────────────────────
  // The plan and V / A follow every frame; the panel's lists follow at most every
  // SIDE_MS while a room is dragged, and at once when it is let go.
  let sideRaf = 0, sideWait = 0, sideAt = 0;
  function queueSide() {
    clearTimeout(sideWait); cancelAnimationFrame(sideRaf);
    const run = () => { sideRaf = requestAnimationFrame(() => { sideAt = performance.now(); refreshSide(); }); };
    const wait = canvas.drag ? SIDE_MS - (performance.now() - sideAt) : 0;
    if (wait > 0) sideWait = setTimeout(run, wait); else run();
  }
  ed.changed = () => {
    if (!ed.s) return;
    ed.view = previewEdit(ed.s, canvas.rooms, store.inputs, canvas.doors, true);
    ed.overlaps = overlapsIn(canvas.rooms);
    const c = ed.view.scored.computed;
    setText(vNow, fmt(c.qvci, 2)); setText(aNow, fmt(c.aqi, 2));
    drawPareto();
    queueSide();
  };
  ed.onSelect = () => queueSide();
  const dirty = () => !!ed.s && (!sameLayout(canvas.rooms, currentLayout(ed.s)) || !sameDoors(canvas.doors, currentDoors(ed.s)));

  // ── Ctrl+D: the door between the two selected rooms goes round its four places,
  // each end of their shared wall opening into either room (one change per press)
  ed.flipDoor = () => {
    if (!ed.view) return;
    const sel = canvas.selectedRooms();
    if (sel.length !== 2) { toast(`Select the two rooms of a door (${MOD_KEY} + click), then ${MOD_KEY}+D.`); return; }
    const [a, b] = sel.map((r) => r.id).sort(), key = doorKey(a, b), A = roomName(a), B = roomName(b);
    const door = doorBetween(ed.view.syn, a, b);
    if (!door) {
      const linked = (ed.s.edges || []).some((e) => doorKey(e.source, e.target) === key);
      const open = ed.view.syn.openings.some((o) => o.kind === "open" && o.rooms && doorKey(o.rooms[0], o.rooms[1]) === key);
      toast(open ? `${A} and ${B} are open to each other: there is no door to flip.` : linked ? `${A} and ${B} share no wall long enough for a door.` : `${A} and ${B} are not connected, so there is no door between them.`);
      return;
    }
    const ring = [[0, a], [0, b], [1, b], [1, a]];
    const at = ring.findIndex(([e, r]) => e === doorEnd(door) && r === door.into);
    const [end, into] = ring[(at + 1) % ring.length];
    canvas.record();
    // a choice that matches where the synthesis would put the door anyway is not kept
    const rest = { ...canvas.doors };
    delete rest[key];
    const plain = doorBetween(previewEdit(ed.s, canvas.rooms, store.inputs, rest, true).syn, a, b);
    canvas.doors = plain && doorEnd(plain) === end && plain.into === into ? rest : { ...rest, [key]: { end, into } };
    canvas.changed();
  };

  function refreshSide() {
    if (!ed.view) return;
    const s = ed.s, scored = ed.view.scored, byId = new Map(scored.rooms.map((r) => [r.room_id, r]));
    const sel = canvas.selectedRooms();
    setText(barName, sel.length === 1 ? roomName(sel[0].id) : sel.length ? `${sel.length} rooms` : "none");
    barName.classList.toggle("none", !sel.length);
    const d = dirty();
    setText(statusEl, d ? "Changed · not in the plan yet" : ed.s.edit ? "Edited plan" : "As generated");
    statusEl.classList.toggle("changed", d);
    bReplace.disabled = bDiscard.disabled = !d;
    if (!isOpen()) return;          // folded: the bar is all there is
    // the selected room: size inputs, zone and scores as drawn
    selSec.replaceChildren();
    if (sel.length !== 1) {
      selSec.append(h("div.hint", sel.length ? `${sel.length} rooms selected: drag to move them together, arrow keys nudge.` : "Click a room to select it; drag it or an edge or corner to change the plan."));
      // two rooms with a door between them: where it is, and the button for Ctrl+D (touch screens have no keys)
      const door = sel.length === 2 ? doorBetween(ed.view.syn, sel[0].id, sel[1].id) : null;
      if (door) {
        selSec.append(h("div.pe-door",
          h("button.btn-ghost", { title: `Move or turn the door between these rooms (${MOD_KEY}+D)`, on: { click: () => { ed.flipDoor(); canvas.cv.focus({ preventScroll: true }); } } }, "Flip door"),
          h("span.hint", `Opens into ${roomName(door.into)}, ${doorWhere(door)} of the wall · ${MOD_KEY}+D`)));
      }
    } else {
      const r = sel[0], rec = byId.get(r.id) || {};
      const num = (val, set) => {
        const inp = h("input", { type: "number", min: 1, max: 40, step: 0.05, value: val.toFixed(2) });
        inp.addEventListener("change", () => { const v = Number(inp.value); if (Number.isFinite(v) && v >= 1 && v <= 40) { canvas.record(); set(Math.round(v * 1000) / 1000); canvas.changed(); } else inp.value = val.toFixed(2); });
        return inp;
      };
      const exp = rec.exposure || {}, sides = SIDES.filter((dd) => (exp[dd] || 0) > 0.05);
      const zoneTxt = (z) => (z == null ? "-" : `${ZONE_LABELS[z]}`);
      selSec.append(...[
        h("div.pe-size",
          h("label", h("span.pe-lab", "Width (E-W)"), h("span.pe-num", num(r.w, (v) => { r.w = v; }), h("span", "m"))),
          h("label", h("span.pe-lab", "Depth (N-S)"), h("span.pe-num", num(r.h, (v) => { r.h = v; }), h("span", "m")))),
        kv("Area", `${(r.w * r.h).toFixed(1)} m²`),
        rec.zoned ? kv("Zone as drawn", `${zoneTxt(rec.zone_assignment)}${rec.planned_zone != null && rec.planned_zone !== rec.zone_assignment ? `  (zone plan: ${zoneTxt(rec.planned_zone)})` : ""}`) : kv("Zone", "not zoned"),
        kv("Exterior walls", sides.length ? `${sides.map((dd) => `${dd} ${exp[dd].toFixed(1)}`).join(" · ")} m` : "none"),
        rec.quality_view_score != null ? kv("View score QV", fmt(rec.quality_view_score, 3)) : null,
        rec.acoustic_score != null ? kv("Acoustic score AQ", fmt(rec.acoustic_score, 3)) : null,
        rec.est_avg_spl_dB != null ? kv("Indoor level", `${fmt(rec.est_avg_spl_dB, 1)} dB(A)  (guideline ${rec.guideline_dB})`) : null,
      ].filter(Boolean));
    }
    // checks, short and quiet: overlaps, connected rooms without a door, the entrance
    const notes = [];
    for (const [a, b] of ed.overlaps) notes.push([`Overlap: ${roomName(a)} / ${roomName(b)}`, "These rooms overlap"]);
    const doors = new Set(ed.view.syn.openings.filter((o) => o.kind !== "window" && o.rooms).map((o) => [...o.rooms].sort().join("|")));
    for (const e of s.edges || []) {
      if (String(e.source).startsWith("outside") || String(e.target).startsWith("outside")) continue;
      if (!canvas.rooms.some((r) => r.id === e.source) || !canvas.rooms.some((r) => r.id === e.target)) continue;
      if (!doors.has([e.source, e.target].sort().join("|"))) notes.push([`No door: ${roomName(e.source)} / ${roomName(e.target)}`, "Connected rooms need a shared wall of about 1 m for a door"]);
    }
    if (!ed.view.syn.openings.some((o) => o.kind === "entry")) notes.push(["No entrance", "The entrance room needs a free exterior wall of about 1 m"]);
    checkSec.replaceChildren(h("div.h", "CHECKS"), notes.length ? h("ul.pe-notes", ...notes.map(([t, tip]) => h("li", { title: tip }, t))) : h("div.pe-ok", "No overlaps, a door on every connection, the entrance in place."));
    // every room: zone, QV, AQ as drawn
    const rows = scored.rooms.map((r) => h("tr.clickable" + (canvas.selected.has(r.room_id) ? ".sel" : ""), { on: { click: () => { canvas.selected = new Set([r.room_id]); canvas.draw(); refreshSide(); } } },
      h("td", roomName(r.room_id)), h("td", fmt(r.area_m2, 1)),
      h("td", r.zone_assignment == null ? "-" : ZONE_LABELS[r.zone_assignment] + (r.planned_zone != null && r.planned_zone !== r.zone_assignment ? "*" : "")),
      h("td" + (r.quality_view_score == null ? ".dim" : r.scored && r.quality_view_score >= 0.5 ? ".acc" : ""), fmt(r.quality_view_score, 2)),
      h("td" + (r.acoustic_score == null ? ".dim" : r.scored && r.acoustic_ok ? ".acc" : ""), fmt(r.acoustic_score, 2))));
    roomsSec.replaceChildren(h("div.h", `ROOMS (${scored.rooms.length})`),
      h("table.detail-tbl", h("thead", h("tr", h("th", "Room"), h("th", "m²"), h("th", "Zone"), h("th", "QV"), h("th", "AQ"))), h("tbody", ...rows)),
      h("div.hint", "As drawn: each room takes the zone of the façades it touches (an exterior wall of at least 1.2 m). * differs from the zone plan."));
    drawPareto();
  }

  function kv(k, v) { return h("div.kv", h("span", k), h("span", v)); }

  // ── Pareto front: the other plans and samples as in Stage 4, this plan where
  // its as-drawn values are now (moving with every change), its zone plan, the target
  function drawPareto() {
    if (!isOpen() || !ed.view || !plotSize) return;
    const { ctx, w, h: H } = fitCanvas(pcv, plotSize[0], plotSize[1]), P = palette();
    const live = ed.view.scored.computed, zp = (ed.s.orig || ed.s).computed || {}, tg = ed.s.targets || {};
    const pts = store.stage4.schematics.map((s) => (s === ed.s
      ? { plan_index: s.plan_index, computed: { qvci: Number(live.qvci), aqi: Number(live.aqi) }, me: true }
      : { plan_index: s.plan_index, computed: { qvci: Number(s.computed?.qvci ?? 0), aqi: Number(s.computed?.aqi ?? 0) } }));
    if (!pts.some((p) => p.me)) pts.push({ plan_index: ed.s.plan_index, computed: { qvci: Number(live.qvci), aqi: Number(live.aqi) }, me: true });
    const pool = store.stage2.sample_pool || [];
    const qs = [...pts.map((p) => p.computed.qvci), ...pool.map((p) => Number(p.qvci ?? 0)), Number(zp.qvci ?? 0)];
    const as = [...pts.map((p) => p.computed.aqi), ...pool.map((p) => Number(p.aqi ?? 0)), Number(zp.aqi ?? 0)];
    if (tg.present) { qs.push(Number(tg.qvci)); as.push(Number(tg.aqi)); }
    const span = (v) => {
      let lo = Math.min(...v), hi = Math.max(...v);
      const pd = Math.max(0.02, (hi - lo) * 0.1);
      lo = Math.max(0, lo - pd); hi = Math.min(1, hi + pd);
      if (hi - lo < 0.1) { const c = (lo + hi) / 2; lo = Math.max(0, c - 0.05); hi = Math.min(1, lo + 0.1); lo = Math.max(0, hi - 0.1); }
      return [lo, hi];
    };
    const [q0, q1] = span(qs), [a0, a1] = span(as);
    const L = 46, R = 10, T = 8, B = 24, pw = Math.max(10, w - L - R), ph = Math.max(10, H - T - B);
    const X = (q) => L + ((q - q0) / (q1 - q0)) * pw, Y = (a) => T + ph - ((a - a0) / (a1 - a0)) * ph;
    // grid, axes, ticks
    ctx.lineWidth = 0.5; ctx.strokeStyle = P.a("border", 0.6);
    const qt = niceTicks(q0, q1), at = niceTicks(a0, a1);
    ctx.beginPath();
    for (const v of qt) { ctx.moveTo(X(v), T); ctx.lineTo(X(v), T + ph); }
    for (const v of at) { ctx.moveTo(L, Y(v)); ctx.lineTo(L + pw, Y(v)); }
    ctx.stroke();
    ctx.strokeStyle = P.ink3; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(L, T); ctx.lineTo(L, T + ph); ctx.lineTo(L + pw, T + ph); ctx.stroke();
    ctx.font = `8.5px ${MONO}`; ctx.fillStyle = P.ink3; ctx.textBaseline = "middle";
    ctx.textAlign = "center"; for (const v of qt) ctx.fillText(v.toFixed(2), X(v), T + ph + 8);
    ctx.textAlign = "right"; for (const v of at) ctx.fillText(v.toFixed(2), L - 4, Y(v));
    ctx.font = `600 8.5px ${FONT}`; ctx.fillStyle = P.ink2;
    ctx.textAlign = "right"; ctx.fillText("QVCI →", L + pw, T + ph + 18);
    ctx.save(); ctx.translate(7, T + ph / 2); ctx.rotate(-Math.PI / 2); ctx.textAlign = "center"; ctx.fillText("AQI →", 0, 0); ctx.restore();
    ctx.save(); ctx.beginPath(); ctx.rect(L - 6, T - 6, pw + 12, ph + 12); ctx.clip();
    // the samples of Stage 2 behind everything
    const dots = (list, r) => { ctx.beginPath(); for (const [x, y] of list) { ctx.moveTo(x + r, y); ctx.arc(x, y, r, 0, 2 * Math.PI); } ctx.fill(); };
    ctx.fillStyle = P.a("ink3", 0.4);
    dots(pool.map((p) => [X(Number(p.qvci)), Y(Number(p.aqi))]), 1.3);
    // the front through the non-dominated plans, this plan as it is drawn now included
    const front = paretoFrontIndices(pts).map((idx) => pts.find((p) => p.plan_index === idx)).filter(Boolean);
    if (front.length >= 2) {
      ctx.strokeStyle = P.a("accent", 0.55); ctx.lineWidth = 1.2; ctx.setLineDash([4, 3]);
      ctx.beginPath(); front.forEach((p, i) => (i ? ctx.lineTo(X(p.computed.qvci), Y(p.computed.aqi)) : ctx.moveTo(X(p.computed.qvci), Y(p.computed.aqi)))); ctx.stroke();
      ctx.setLineDash([]);
    }
    const onFront = new Set(front.map((p) => p.plan_index)), others = pts.filter((p) => !p.me);
    ctx.fillStyle = P.ink3; dots(others.filter((p) => !onFront.has(p.plan_index)).map((p) => [X(p.computed.qvci), Y(p.computed.aqi)]), 2.6);
    ctx.fillStyle = P.a("accent", 0.6); dots(others.filter((p) => onFront.has(p.plan_index)).map((p) => [X(p.computed.qvci), Y(p.computed.aqi)]), 2.6);
    // the target
    if (tg.present) {
      const tx = X(Number(tg.qvci)), ty = Y(Number(tg.aqi));
      ctx.strokeStyle = P.ink2; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(tx - 5, ty); ctx.lineTo(tx + 5, ty); ctx.moveTo(tx, ty - 5); ctx.lineTo(tx, ty + 5); ctx.stroke();
    }
    // this plan: from its zone plan to where the drawing puts it now
    const zx = X(Number(zp.qvci ?? 0)), zy = Y(Number(zp.aqi ?? 0)), lx = X(Number(live.qvci)), ly = Y(Number(live.aqi));
    ctx.strokeStyle = P.a("accent", 0.7); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(zx, zy); ctx.lineTo(lx, ly); ctx.stroke();
    ctx.beginPath(); ctx.arc(zx, zy, 3.6, 0, 2 * Math.PI); ctx.fillStyle = P.surface; ctx.fill(); ctx.strokeStyle = P.accent; ctx.lineWidth = 1.2; ctx.stroke();
    ctx.beginPath(); ctx.arc(lx, ly, 4.8, 0, 2 * Math.PI); ctx.fillStyle = P.accent; ctx.fill(); ctx.strokeStyle = P.surface; ctx.lineWidth = 1.2; ctx.stroke();
    ctx.restore();
  }

  // ── plans ──────────────────────────────────────────────────────────
  function stash() {
    if (cur < 0) return;
    drafts.set(plans[cur].plan_index, { rooms: canvas.rooms.map((r) => ({ ...r })), doors: { ...canvas.doors }, undo: canvas.undoStack, redo: canvas.redoStack, selected: [...canvas.selected] });
  }
  function load(i) {
    stash();
    cur = i;
    const s = plans[i], d = drafts.get(s.plan_index);
    ed.s = s;
    originalSynthesis(s);
    canvas.rooms = d ? d.rooms.map((r) => ({ ...r })) : currentLayout(s);
    canvas.doors = d ? { ...d.doors } : currentDoors(s);
    canvas.undoStack = d ? d.undo : []; canvas.redoStack = d ? d.redo : [];
    canvas.selected = new Set(d ? d.selected : []);
    idxEl.textContent = `#${pad2(s.plan_index)}`;
    countEl.textContent = `${i + 1} of ${plans.length}`;
    for (const el of [switcher, bGoPrev, bGoNext]) el.classList.toggle("hidden", plans.length < 2);
    bPrev.disabled = bGoPrev.disabled = i === 0; bNext.disabled = bGoNext.disabled = i === plans.length - 1;
    const pc = (s.orig || s).computed || {}, tg = s.targets || {};
    refEl.replaceChildren(...[h("span", `Zone plan V ${fmt(pc.qvci, 2)} · A ${fmt(pc.aqi, 2)}`), tg.present ? h("span", `Target V ${fmt(tg.qvci, 2)} · A ${fmt(tg.aqi, 2)}`) : null].filter(Boolean));
    canvas.onHistory();
    ed.changed();
    requestAnimationFrame(() => { canvas.fit(PHONE() ? 1 : OPEN_FIT); canvas.cv.focus(); });
  }
  function go(i) { if (i >= 0 && i < plans.length && i !== cur) load(i); }

  // ── actions ────────────────────────────────────────────────────────
  function applyCurrent() {
    const s = ed.s;
    applyEdit(s, canvas.rooms.map((r) => ({ ...r })), store.inputs, canvas.doors);
    drafts.delete(s.plan_index);
    canvas.resetHistory();
    onApply?.(s);
    ed.changed();
    return s;
  }
  /** Put the changes into the plan and stay on it (Next goes on). */
  async function replace() {
    if (!dirty()) return;
    if (ed.overlaps.length && !(await confirmAsk("Some rooms overlap. Put the plan in as it is?"))) return;
    const s = applyCurrent();
    toast(s.edit ? `Plan #${pad2(s.plan_index)} replaced with the edited plan.` : `Plan #${pad2(s.plan_index)} is back as generated.`);
  }
  async function save() {
    if (dirty() && ed.overlaps.length && !(await confirmAsk("Some rooms overlap. Put the plan in as it is?"))) return;
    const s = applyCurrent();
    const saved = await saveSession?.();
    toast(saved === false ? `Plan #${pad2(s.plan_index)} replaced; the session file was not saved.` : `Plan #${pad2(s.plan_index)} replaced and the session saved.`);
  }
  /** Back to the plan as it stands (one undo step, so Ctrl+Z brings the changes back). */
  function discard() {
    if (!dirty()) return;
    canvas.record();
    canvas.rooms = currentLayout(ed.s);
    canvas.doors = currentDoors(ed.s);
    canvas.selected = new Set();
    drafts.delete(ed.s.plan_index);
    canvas.changed();
    toast(`Changes to plan #${pad2(ed.s.plan_index)} dropped (${MOD_KEY}+Z brings them back).`);
  }
  async function doExport(fmtId) {
    canvas.flush();
    try {
      const { blob, name } = await exportPlan(fmtId, { s: ed.s, view: ed.view, opt: ed.opt, edited: dirty() || !!ed.s.edit });
      if (await downloadBlob(blob, name)) toast(`Saved ${name}.`);
    } catch (err) { toast(`Could not export: ${err.message || err}`); }
  }
  function toggleMenu() { menu.classList.toggle("hidden"); }
  function hideMenu() { menu.classList.add("hidden"); }
  const outside = (e) => { if (!menu.contains(e.target) && e.target !== bExport) hideMenu(); };

  async function requestClose() {
    stash();
    const pending = plans.filter((p) => { const d = drafts.get(p.plan_index); return d && (!sameLayout(d.rooms, currentLayout(p)) || !sameDoors(d.doors, currentDoors(p))); });
    if (pending.length && !(await confirmAsk(`Drop the changes to ${pending.length === 1 ? `plan #${pad2(pending[0].plan_index)}` : `${pending.length} plans`}?`))) return;
    close();
  }
  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); if (!menu.classList.contains("hidden")) hideMenu(); else requestClose(); }
    // Ctrl+D, undo and redo also when a button of the editor has the focus (the canvas handles them itself)
    else if ((e.ctrlKey || e.metaKey) && !e.altKey && !/^(INPUT|TEXTAREA|SELECT)$/.test(e.target?.tagName || "")) {
      const k = e.key.toLowerCase();
      if (k === "d") { e.preventDefault(); ed.flipDoor(); }
      else if (k === "z" || k === "y") { e.preventDefault(); if (k === "y" || e.shiftKey) canvas.redo(); else canvas.undo(); }
    }
  };
  const plotRO = onResize(plotEl, () => { const r = pcv.getBoundingClientRect(); plotSize = r.width > 0 && r.height > 0 ? [r.width, r.height] : null; drawPareto(); });
  function close() {
    window.removeEventListener("keydown", onKey);
    window.removeEventListener("pointerdown", outside, true);
    plotRO.disconnect();
    clearTimeout(sideWait); cancelAnimationFrame(sideRaf);
    canvas.destroy();
    panel.remove();
  }
  window.addEventListener("keydown", onKey);
  window.addEventListener("pointerdown", outside, true);
  setOpen(!!LS("peOpen", false), false);
  load(0);
  return { close, canvas };
}
