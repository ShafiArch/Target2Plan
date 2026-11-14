// geometry.js: the "Draw room program" canvas of Stage 1
// (app/widgets/geometry_canvas.py), also the base of the plan editor. Rooms
// are dragged from the palette onto a dot grid, moved by dragging, resized by
// an edge or corner (0.1 m grid, 1 m minimum side). Sizes are metres inside;
// the display follows m² / ft².
// Editing works like a CAD tool: Ctrl or Shift + click and box selection,
// copy / cut / paste / duplicate, undo / redo, arrow keys nudge, R turns a
// room, and moved or resized edges snap to the edges and centres of the other
// rooms (guide lines show what lined up; hold Alt to place freely).
import { h, palette, roomColor, fitCanvas, onResize, FONT, hexA } from "./common.js";

export const FT_PER_M = 3.280839895;
export const SQFT_PER_M2 = FT_PER_M * FT_PER_M;
export const ROOM_LABELS = { living: "Living", bedroom: "Bedroom", kitchen: "Kitchen", bathroom: "Bathroom", balcony: "Balcony" };
export const DEFAULT_AREA_M2 = { living: 20, bedroom: 14, kitchen: 9, bathroom: 5, balcony: 5 };
const MIN_SIDE_M = 1.0, SNAP_M = 0.1, HANDLE_PX = 7, HANDLE_TOUCH_PX = 16, SNAP_PX = 8, PASTE_STEP_M = 0.5;
const COARSE = () => window.matchMedia?.("(pointer: coarse)").matches;
const MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");
export const MOD_KEY = MAC ? "⌘" : "Ctrl";
const snap = (v) => Math.round(v / SNAP_M) * SNAP_M;
const overlaps = ([x, y, w, hh], r) => !(x + w <= r.x || r.x + r.w <= x || y + hh <= r.y || r.y + r.h <= y);
const LS = (k, d) => { try { const v = localStorage.getItem(`t2p.${k}`); return v == null ? d : JSON.parse(v); } catch { return d; } };
const SS = (k, v) => { try { localStorage.setItem(`t2p.${k}`, JSON.stringify(v)); } catch { /* storage unavailable */ } };

/** Rooms copied with Ctrl+C or cut with Ctrl+X, shared by the drawing canvases. */
let CLIP = null;

// ── snapping ──────────────────────────────────────────────────────────
// Edges line up with edges (flush or abutting) and centres with centres.
const xKeys = (o) => [o.x, o.x + o.w, o.x + o.w / 2];
const yKeys = (o) => [o.y, o.y + o.h, o.y + o.h / 2];

/** Shift [dx, dy] (null when nothing is near) that lines the box [x0, y0, x1, y1]
 *  up with one of `others` within `tol`, plus guide lines for what lined up. */
export function alignMove(box, others, tol) {
  const [x0, y0, x1, y1] = box;
  const best = (mine, keysOf) => {
    let out = null;
    for (const o of others) {
      const k = keysOf(o);
      for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) {
        if ((i === 2) !== (j === 2)) continue;                       // centre to centre, edge to edge
        const d = k[j] - mine[i], cost = Math.abs(d) + (i === 2 ? tol * 0.25 : 0);
        if (Math.abs(d) <= tol && (!out || cost < out.cost - 1e-9)) out = { d, cost, v: k[j] };
      }
    }
    return out;
  };
  const bx = best([x0, x1, (x0 + x1) / 2], xKeys), by = best([y0, y1, (y0 + y1) / 2], yKeys);
  const guides = [];
  const span = (v, keysOf, lo, hi, axis) => {
    let a = lo, b = hi;
    for (const o of others) if (keysOf(o).some((k) => Math.abs(k - v) < 1e-6)) {
      if (axis === "x") { a = Math.min(a, o.y); b = Math.max(b, o.y + o.h); } else { a = Math.min(a, o.x); b = Math.max(b, o.x + o.w); }
    }
    guides.push({ axis, v, a, b });
  };
  if (bx) span(bx.v, xKeys, y0 + (by ? by.d : 0), y1 + (by ? by.d : 0), "x");
  if (by) span(by.v, yKeys, x0 + (bx ? bx.d : 0), x1 + (bx ? bx.d : 0), "y");
  return { dx: bx ? bx.d : null, dy: by ? by.d : null, guides };
}

/** The edge value `v` on `axis` ("x" for west / east edges) lined up with an edge
 *  of `others` within `tol`, or null; `lo`, `hi` is the edge's extent for the guide. */
export function alignEdge(v, axis, others, tol, lo, hi) {
  let out = null;
  for (const o of others) {
    const ks = axis === "x" ? [o.x, o.x + o.w] : [o.y, o.y + o.h];
    for (const k of ks) if (Math.abs(k - v) <= tol && (!out || Math.abs(k - v) < Math.abs(out.v - v) - 1e-9)) out = { v: k };
  }
  if (!out) return null;
  let a = lo, b = hi;
  for (const o of others) {
    const ks = axis === "x" ? [o.x, o.x + o.w] : [o.y, o.y + o.h];
    if (ks.some((k) => Math.abs(k - out.v) < 1e-6)) { if (axis === "x") { a = Math.min(a, o.y); b = Math.max(b, o.y + o.h); } else { a = Math.min(a, o.x); b = Math.max(b, o.x + o.w); } }
  }
  return { v: out.v, guide: { axis, v: out.v, a, b } };
}

export class GeometryCanvas {
  /** `options`: allowAdd / allowRemove / allowClipboard (default true), minSide (m). */
  constructor(wrap, onChange, options = {}) {
    this.wrap = wrap;
    this.opts = { allowAdd: true, allowRemove: true, allowClipboard: true, minSide: MIN_SIDE_M, ...options };
    this.cv = h("canvas", { tabindex: 0 });
    wrap.append(this.cv);
    this.onChange = onChange;
    this.rooms = [];
    this.units = "m";
    this.planW = 12; this.planH = 9;
    this.pxPerM = 28;
    this.pad = 24;
    this.selected = new Set();
    this.drag = null;
    this.pan = [0, 0];
    this.autoCentre = true;
    this.box = null;
    this.counter = {};
    this.spaceDown = false;
    this.onZoom = null;
    this.onHistory = null;      // called when undo / redo become (un)available
    this.touches = new Map();   // active touch points, for pinch zoom
    this.pinch = null;
    this.guides = [];           // snap guide lines shown while dragging
    this.snapOn = LS("snap", true);
    this.undoStack = []; this.redoStack = [];
    this.cv.addEventListener("pointerdown", (e) => this.down(e));
    this.cv.addEventListener("pointermove", (e) => this.move(e));
    this.cv.addEventListener("pointerup", (e) => this.up(e));
    this.cv.addEventListener("pointercancel", (e) => this.up(e));
    this.cv.addEventListener("auxclick", (e) => { if (e.button === 1) e.preventDefault(); });
    this.cv.addEventListener("mousedown", (e) => { if (e.button === 1) e.preventDefault(); });   // no autoscroll
    // wheel (or trackpad pinch) zooms about the pointer
    this.cv.addEventListener("wheel", (e) => {
      e.preventDefault();
      const [px, py] = this.local(e), d = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);
      this.zoomAt(Math.exp(-d * (e.ctrlKey ? 0.01 : 0.0018)), px, py);
    }, { passive: false });
    this.cv.addEventListener("keydown", (e) => this.key(e));
    this.cv.addEventListener("keyup", (e) => { if (e.key === " ") { this.spaceDown = false; if (!this.drag) this.cv.style.cursor = "default"; } });
    this.cv.addEventListener("blur", () => { this.spaceDown = false; });
    // Edit menu commands (the desktop app on macOS) arrive as clipboard events
    const clip = (fn) => (e) => { if (document.activeElement === this.cv && this.opts.allowClipboard) { e.preventDefault(); fn(); } };
    this.docListeners = [["copy", clip(() => this.copy())], ["cut", clip(() => this.cut())], ["paste", clip(() => this.paste())]];
    for (const [t, fn] of this.docListeners) document.addEventListener(t, fn);
    this.ro = onResize(wrap, () => { if (this.autoCentre) this.centre(); else this.draw(); });
  }

  /** Detach the document listeners and the resize observer (for canvases that close). */
  destroy() {
    for (const [t, fn] of this.docListeners) document.removeEventListener(t, fn);
    this.ro?.disconnect();
  }

  // ── keyboard ──────────────────────────────────────────────────────
  key(e) {
    const mod = e.ctrlKey || e.metaKey, k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    const done = () => { e.preventDefault(); e.stopPropagation(); };
    if (mod && k === "z") { done(); if (e.shiftKey) this.redo(); else this.undo(); return; }
    if (mod && k === "y") { done(); this.redo(); return; }
    if (mod && k === "a") { done(); this.selectAll(); return; }
    if (mod && this.opts.allowClipboard && (k === "c" || k === "x" || k === "v" || k === "d")) {
      done();
      if (k === "c") this.copy(); else if (k === "x") this.cut(); else if (k === "v") this.paste(); else this.duplicate();
      return;
    }
    if (mod) return;
    if ((e.key === "Delete" || e.key === "Backspace") && this.opts.allowRemove) { done(); this.removeSelected(); }
    else if (e.key.startsWith("Arrow") && this.selected.size) {
      done();
      const s = e.shiftKey ? 1 : SNAP_M;
      this.nudge(e.key === "ArrowLeft" ? -s : e.key === "ArrowRight" ? s : 0, e.key === "ArrowUp" ? -s : e.key === "ArrowDown" ? s : 0);
    } else if (k === "r" && this.selected.size) { done(); this.rotateSelected(); }
    else if (e.key === "+" || e.key === "=") { done(); this.zoomBy(1.25); }
    else if (e.key === "-" || e.key === "_") { done(); this.zoomBy(1 / 1.25); }
    else if (e.key === "0") { done(); this.fit(); }
    else if (e.key === " ") { e.preventDefault(); if (!this.spaceDown) { this.spaceDown = true; if (!this.drag) this.cv.style.cursor = "grab"; } }
  }

  // ── history (undo / redo) ─────────────────────────────────────────
  snapshot() { return { rooms: this.rooms.map((r) => ({ ...r })), selected: [...this.selected], counter: { ...this.counter } }; }
  restore(st) { this.rooms = st.rooms.map((r) => ({ ...r })); this.selected = new Set(st.selected.filter((id) => this.rooms.some((r) => r.id === id))); this.counter = { ...st.counter }; }
  /** Record the state before an edit (call it, then change the rooms). */
  record(st = this.snapshot()) {
    this.undoStack.push(st);
    if (this.undoStack.length > 200) this.undoStack.shift();
    this.redoStack = [];
    this.lastNudge = 0;
    this.onHistory?.();
  }
  resetHistory() { this.undoStack = []; this.redoStack = []; this.onHistory?.(); }
  canUndo() { return this.undoStack.length > 0; }
  canRedo() { return this.redoStack.length > 0; }
  undo() { if (!this.undoStack.length) return; this.redoStack.push(this.snapshot()); this.restore(this.undoStack.pop()); this.guides = []; this.onHistory?.(); this.changed(); }
  redo() { if (!this.redoStack.length) return; this.undoStack.push(this.snapshot()); this.restore(this.redoStack.pop()); this.guides = []; this.onHistory?.(); this.changed(); }
  setSnap(on) { this.snapOn = !!on; SS("snap", this.snapOn); }

  // ── zoom and pan ──────────────────────────────────────────────────
  // pxPerM is the zoom (28 px per metre = 100 %); pan moves the origin.
  static BASE_PX_PER_M = 28;
  static MIN_PX_PER_M = 6;
  static MAX_PX_PER_M = 160;
  zoomPercent() { return Math.round((this.pxPerM / GeometryCanvas.BASE_PX_PER_M) * 100); }
  /** Zoom by `factor`, keeping the point under canvas pixel (px, py) in place. */
  zoomAt(factor, px, py) {
    const s0 = this.pxPerM, s1 = Math.max(GeometryCanvas.MIN_PX_PER_M, Math.min(GeometryCanvas.MAX_PX_PER_M, s0 * factor));
    if (Math.abs(s1 - s0) < 1e-9) return;
    const [mx, my] = this.toM(px, py);
    this.pxPerM = s1;
    this.pan = [px - mx * s1 - this.pad, py - my * s1 - this.pad];
    this.autoCentre = false;
    this.draw();
    this.onZoom?.();
  }
  zoomBy(factor) { const r = this.cv.getBoundingClientRect(); this.zoomAt(factor, r.width / 2, r.height / 2); }
  /** Extent [x0, y0, x1, y1] to fit and centre (the rooms, or the plan outline when there are none). */
  extent() {
    if (!this.rooms.length) return [0, 0, this.planW, this.planH];
    return [Math.min(...this.rooms.map((q) => q.x)), Math.min(...this.rooms.map((q) => q.y)), Math.max(...this.rooms.map((q) => q.x + q.w)), Math.max(...this.rooms.map((q) => q.y + q.h))];
  }
  /** Fit every room (or the plan outline when there are none) in the view. */
  fit() {
    const r = this.cv.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    const [x0, y0, x1, y1] = this.extent();
    const m = this.fitMargin ?? 36, s = Math.min((r.width - 2 * m) / Math.max(x1 - x0, 1), (r.height - 2 * m) / Math.max(y1 - y0, 1));
    this.pxPerM = Math.max(GeometryCanvas.MIN_PX_PER_M, Math.min(GeometryCanvas.MAX_PX_PER_M, s));
    this.autoCentre = true;
    this.centre();
    this.onZoom?.();
  }

  changed() { this.onChange?.(); this.draw(); }

  setRooms(rooms) {
    this.rooms = rooms.map((r) => ({ ...r }));
    this.counter = {};
    for (const r of this.rooms) this.counter[r.type] = Math.max(this.counter[r.type] || 0, Number(String(r.id).split("_").pop()) + 1);
    this.selected = new Set(); this.autoCentre = true; this.resetHistory(); this.centre(); this.changed();
  }
  setUnits(u) { if (u !== this.units) { this.units = u; this.draw(); } }
  setPlan(wm, hm) { this.planW = Math.max(1, wm); this.planH = Math.max(1, hm); this.draw(); }
  setScale(px) { this.pxPerM = Math.max(GeometryCanvas.MIN_PX_PER_M, Math.min(GeometryCanvas.MAX_PX_PER_M, px)); this.centre(); this.onZoom?.(); }

  loadSample() {
    if (this.rooms.length) this.record();
    const sizes = [["living", 5.5, 4.0], ["bedroom", 4.0, 3.5], ["bedroom", 4.0, 3.5], ["kitchen", 3.0, 3.0], ["bathroom", 2.0, 2.5], ["balcony", 1.5, 3.0]];
    const base = Math.max(...sizes.map((s) => s[2]));
    let x = 0;
    this.rooms = []; this.counter = {};
    for (const [rt, w, hh] of sizes) {
      const n = this.counter[rt] || 0; this.counter[rt] = n + 1;
      this.rooms.push({ id: `${rt}_${n}`, type: rt, x, y: base - hh, w, h: hh });
      x += w + 0.5;
    }
    this.selected = new Set(); this.autoCentre = true; this.centre(); this.changed();
  }

  centre() {
    const r = this.cv.getBoundingClientRect();
    if (!this.rooms.length || r.width <= 0) { this.pan = [0, 0]; this.draw(); return; }
    const s = this.pxPerM, [x0, y0, x1, y1] = this.extent();
    this.pan = [r.width / 2 - ((x0 + x1) / 2) * s - this.pad, r.height / 2 - ((y0 + y1) / 2) * s - this.pad];
    this.draw();
  }

  clear() { if (!this.rooms.length) return; this.record(); this.rooms = []; this.counter = {}; this.selected = new Set(); this.changed(); }
  removeSelected() {
    if (!this.selected.size || !this.opts.allowRemove) return;
    this.record();
    this.rooms = this.rooms.filter((r) => !this.selected.has(r.id)); this.selected = new Set(); this.changed();
  }
  totalAreaM2() { return this.rooms.reduce((s, r) => s + r.w * r.h, 0); }
  counts() { const c = {}; for (const r of this.rooms) c[r.type] = (c[r.type] || 0) + 1; return c; }
  selectAll() { this.selected = new Set(this.rooms.map((r) => r.id)); this.draw(); }
  selectedRooms() { return this.rooms.filter((r) => this.selected.has(r.id)); }
  newId(type) { const n = this.counter[type] || 0; this.counter[type] = n + 1; return `${type}_${n}`; }

  addRoom(type, at = null) {
    if (!this.opts.allowAdd) return null;
    this.record();
    const side = Math.sqrt(DEFAULT_AREA_M2[type] ?? 10);
    if (!at) {
      const r = this.cv.getBoundingClientRect();
      const visW = Math.max(this.planW, (r.width - 2 * this.pad) / this.pxPerM), visH = Math.max(this.planH, (r.height - 2 * this.pad) / this.pxPerM);
      const [ox, oy] = this.toM(this.pad, this.pad);
      let x = 0.5, y = 0.5, placed = false;
      while (y + side <= visH && !placed) {
        x = 0.5;
        while (x + side <= visW) { if (!this.rooms.some((q) => overlaps([ox + x, oy + y, side, side], q))) { placed = true; break; } x += 0.5; }
        if (!placed) y += 0.5;
      }
      at = [ox + x + side / 2, oy + y + side / 2];
    }
    const room = { id: this.newId(type), type, x: snap(at[0] - side / 2), y: snap(at[1] - side / 2), w: side, h: side };
    this.rooms.push(room);
    this.selected = new Set([room.id]);
    this.changed();
    return room;
  }

  // ── clipboard ─────────────────────────────────────────────────────
  copy() {
    const sel = this.selectedRooms();
    if (!sel.length || !this.opts.allowClipboard) return false;
    CLIP = { rooms: sel.map(({ type, x, y, w, h: hh }) => ({ type, x, y, w, h: hh })), n: 0 };
    return true;
  }
  cut() { if (this.copy()) { this.record(); this.rooms = this.rooms.filter((r) => !this.selected.has(r.id)); this.selected = new Set(); this.changed(); } }
  /** Paste the copied rooms, each paste half a metre further down and right. */
  paste() {
    if (!CLIP || !CLIP.rooms.length || !this.opts.allowClipboard) return;
    CLIP.n += 1;
    this.placeCopies(CLIP.rooms, PASTE_STEP_M * CLIP.n);
  }
  duplicate() { const sel = this.selectedRooms(); if (sel.length && this.opts.allowClipboard) this.placeCopies(sel, PASTE_STEP_M); }
  placeCopies(src, off) {
    this.record();
    const made = src.map((r) => ({ id: this.newId(r.type), type: r.type, x: snap(r.x + off), y: snap(r.y + off), w: r.w, h: r.h }));
    this.rooms.push(...made);
    this.selected = new Set(made.map((r) => r.id));
    this.autoCentre = false;
    this.changed();
  }

  /** Move the selection by (dx, dy) metres; quick repeats count as one step for undo. */
  nudge(dx, dy) {
    const now = performance.now();
    if (!(this.lastNudge && now - this.lastNudge < 800)) this.record();
    this.lastNudge = now;
    for (const r of this.selectedRooms()) { r.x = Math.round((r.x + dx) * 1000) / 1000; r.y = Math.round((r.y + dy) * 1000) / 1000; }
    this.autoCentre = false;
    this.changed();
  }
  /** Turn the selected rooms a quarter turn about their centres (width and depth swap). */
  rotateSelected() {
    const sel = this.selectedRooms();
    if (!sel.length) return;
    this.record();
    for (const r of sel) { const cx = r.x + r.w / 2, cy = r.y + r.h / 2; [r.w, r.h] = [r.h, r.w]; r.x = snap(cx - r.w / 2); r.y = snap(cy - r.h / 2); }
    this.autoCentre = false;
    this.changed();
  }

  origin() { return [this.pad + this.pan[0], this.pad + this.pan[1]]; }
  toPx(xm, ym) { const [ox, oy] = this.origin(); return [ox + xm * this.pxPerM, oy + ym * this.pxPerM]; }
  toM(px, py) { const [ox, oy] = this.origin(); return [(px - ox) / this.pxPerM, (py - oy) / this.pxPerM]; }
  rectPx(r) { const [x, y] = this.toPx(r.x, r.y); return [x, y, r.w * this.pxPerM, r.h * this.pxPerM]; }
  local(e) { const b = this.cv.getBoundingClientRect(); return [e.clientX - b.left, e.clientY - b.top]; }

  hit(px, py, grip = HANDLE_PX) {
    for (let i = this.rooms.length - 1; i >= 0; i--) {
      const r = this.rooms[i], [x, y, w, hh] = this.rectPx(r);
      if (px < x - grip || px > x + w + grip || py < y - grip || py > y + hh + grip) continue;
      const edges = new Set();
      if (Math.abs(px - x) <= grip) edges.add("W");
      if (Math.abs(px - (x + w)) <= grip) edges.add("E");
      if (Math.abs(py - y) <= grip) edges.add("N");
      if (Math.abs(py - (y + hh)) <= grip) edges.add("S");
      if (edges.size || (px >= x && px <= x + w && py >= y && py <= y + hh)) return [r, edges];
    }
    return [null, new Set()];
  }

  cursorFor(edges) {
    const has = (s) => s.every((k) => edges.has(k)) && edges.size === s.length;
    if (has(["N", "W"]) || has(["S", "E"])) return "nwse-resize";
    if (has(["N", "E"]) || has(["S", "W"])) return "nesw-resize";
    if (edges.has("N") || edges.has("S")) return "ns-resize";
    if (edges.has("E") || edges.has("W")) return "ew-resize";
    return "move";
  }

  // ── touch: a second finger starts a pinch (zoom about the midpoint, pan with it)
  touchDown(e) {
    this.touches.set(e.pointerId, this.local(e));
    if (this.touches.size !== 2) return false;
    const [a, b] = [...this.touches.values()], mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    if (this.drag && this.drag.mode === "move") for (const r of this.drag.rooms) { const [ox, oy] = this.drag.origs.get(r.id); r.x = ox; r.y = oy; }
    if (this.drag && this.drag.mode === "resize") Object.assign(this.drag.room, this.drag.orig);
    this.drag = null; this.box = null; this.guides = [];
    this.pinch = { d0: Math.hypot(a[0] - b[0], a[1] - b[1]), anchor: this.toM(mid[0], mid[1]), s0: this.pxPerM };
    try { this.cv.setPointerCapture(e.pointerId); } catch { /* pointer already gone */ }
    this.draw();
    return true;
  }
  touchMove(e) {
    if (!this.touches.has(e.pointerId)) return false;
    this.touches.set(e.pointerId, this.local(e));
    if (!this.pinch || this.touches.size < 2) return !!this.pinch;
    const [a, b] = [...this.touches.values()], mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
    const s1 = Math.max(GeometryCanvas.MIN_PX_PER_M, Math.min(GeometryCanvas.MAX_PX_PER_M, this.pinch.s0 * (d / Math.max(this.pinch.d0, 1))));
    this.pxPerM = s1;
    this.pan = [mid[0] - this.pinch.anchor[0] * s1 - this.pad, mid[1] - this.pinch.anchor[1] * s1 - this.pad];
    this.autoCentre = false;
    this.draw(); this.onZoom?.();
    return true;
  }
  touchUp(e) {
    this.touches.delete(e.pointerId);
    if (!this.pinch) return false;
    if (this.touches.size < 2) { this.pinch = null; this.drag = null; }
    return true;
  }

  down(e) {
    if (e.pointerType === "touch" && this.touchDown(e)) return;
    // middle button, or Space + left button, pans the view
    if (e.button === 1 || (e.button === 0 && this.spaceDown)) {
      e.preventDefault();
      this.cv.focus();
      try { this.cv.setPointerCapture(e.pointerId); } catch { /* synthetic or finished pointer */ }
      this.drag = { mode: "pan", start: this.local(e), pan0: [...this.pan] };
      this.cv.style.cursor = "grabbing";
      return;
    }
    if (e.button !== 0) return;
    this.cv.focus();
    try { this.cv.setPointerCapture(e.pointerId); } catch { /* synthetic or finished pointer */ }
    const [px, py] = this.local(e), add = e.shiftKey || e.ctrlKey || e.metaKey;
    const [r, edges] = this.hit(px, py, e.pointerType === "touch" ? HANDLE_TOUCH_PX : HANDLE_PX);
    if (!r) {
      if (!add) this.selected = new Set();
      this.drag = { mode: "box", start: [px, py] };
      this.box = [px, py, px, py];
      this.draw(); return;
    }
    const before = this.snapshot();
    if (edges.size && this.selected.size <= 1 && !add) {
      this.selected = new Set([r.id]);
      this.drag = { mode: "resize", edges, start: [px, py], orig: { ...r }, room: r, before };
    } else {
      // a plain click on a room of a larger selection keeps the group for dragging,
      // and selects just that room if the pointer comes up without moving it
      const collapse = !add && this.selected.has(r.id) && this.selected.size > 1 ? r.id : null;
      if (add) { if (this.selected.has(r.id)) this.selected.delete(r.id); else this.selected.add(r.id); }
      else if (!this.selected.has(r.id)) this.selected = new Set([r.id]);
      const movers = this.selectedRooms();
      const box = movers.length ? [Math.min(...movers.map((q) => q.x)), Math.min(...movers.map((q) => q.y)), Math.max(...movers.map((q) => q.x + q.w)), Math.max(...movers.map((q) => q.y + q.h))] : null;
      this.drag = { mode: "move", start: [px, py], origs: new Map(movers.map((q) => [q.id, [q.x, q.y]])), rooms: movers, box, before, collapse };
    }
    this.draw();
  }

  move(e) {
    if (e.pointerType === "touch" && this.touchMove(e)) return;
    const [px, py] = this.local(e);
    if (!this.drag) {
      if (this.spaceDown) { this.cv.style.cursor = "grab"; return; }
      const [r, edges] = this.hit(px, py); this.cv.style.cursor = r ? this.cursorFor(edges) : "default"; return;
    }
    if (this.drag.mode === "pan") {
      this.pan = [this.drag.pan0[0] + px - this.drag.start[0], this.drag.pan0[1] + py - this.drag.start[1]];
      this.autoCentre = false;
      this.draw(); return;
    }
    const s = this.pxPerM, dx = (px - this.drag.start[0]) / s, dy = (py - this.drag.start[1]) / s;
    if (this.drag.mode === "box") { this.box = [this.drag.start[0], this.drag.start[1], px, py]; this.draw(); return; }
    this.autoCentre = false;
    const snapping = this.snapOn && !e.altKey, tol = SNAP_PX / s;
    this.guides = [];
    if (this.drag.mode === "move") {
      if (!this.drag.rooms.length) return;
      const [bx0, by0, bx1, by1] = this.drag.box;
      // the group's top-left corner follows the 0.1 m grid; edges and centres line up with the other rooms
      let mx = snap(bx0 + dx) - bx0, my = snap(by0 + dy) - by0;
      if (snapping) {
        const others = this.rooms.filter((q) => !this.selected.has(q.id));
        const a = alignMove([bx0 + dx, by0 + dy, bx1 + dx, by1 + dy], others, tol);
        if (a.dx != null) mx = dx + a.dx;
        if (a.dy != null) my = dy + a.dy;
        this.guides = a.guides;
      }
      for (const r of this.drag.rooms) { const [ox, oy] = this.drag.origs.get(r.id); r.x = ox + mx; r.y = oy + my; }
    } else {
      const o = this.drag.orig, r = this.drag.room, ed = this.drag.edges, min = this.opts.minSide;
      const others = snapping ? this.rooms.filter((q) => q !== r) : [];
      const place = (v, axis, lo, hi) => {
        if (snapping) { const a = alignEdge(v, axis, others, tol, lo, hi); if (a) { this.guides.push(a.guide); return a.v; } }
        return snap(v);
      };
      let x0 = o.x, y0 = o.y, x1 = o.x + o.w, y1 = o.y + o.h;
      if (ed.has("W")) x0 = Math.min(place(o.x + dx, "x", y0, y1), x1 - min);
      if (ed.has("E")) x1 = Math.max(place(o.x + o.w + dx, "x", y0, y1), x0 + min);
      if (ed.has("N")) y0 = Math.min(place(o.y + dy, "y", x0, x1), y1 - min);
      if (ed.has("S")) y1 = Math.max(place(o.y + o.h + dy, "y", x0, x1), y0 + min);
      Object.assign(r, { x: x0, y: y0, w: x1 - x0, h: y1 - y0 });
    }
    this.changed();
  }

  up(e) {
    if (e && e.pointerType === "touch" && this.touchUp(e)) return;
    if (!this.drag) return;
    if (this.drag.mode === "pan") { this.drag = null; this.cv.style.cursor = this.spaceDown ? "grab" : "default"; return; }
    if (this.drag.mode === "box" && this.box) {
      const [a, b, c, d] = this.box, bx0 = Math.min(a, c), bx1 = Math.max(a, c), by0 = Math.min(b, d), by1 = Math.max(b, d);
      if (bx1 - bx0 > 3 || by1 - by0 > 3) {
        for (const r of this.rooms) {
          const [x, y, w, hh] = this.rectPx(r);
          if (x < bx1 && x + w > bx0 && y < by1 && y + hh > by0) this.selected.add(r.id);
        }
      }
      this.box = null;
    }
    // a move or resize that changed something becomes one undo step
    const before = this.drag.before, moved = before && JSON.stringify(before.rooms) !== JSON.stringify(this.rooms);
    if (moved) this.record(before);
    else if (this.drag.collapse) this.selected = new Set([this.drag.collapse]);
    this.drag = null;
    this.guides = [];
    this.changed();
  }

  fmtArea(a) { return this.units === "m" ? `${a.toFixed(1)} m²` : `${(a * SQFT_PER_M2).toFixed(0)} ft²`; }
  fmtLen(v) { return this.units === "m" ? v.toFixed(1) : (v * FT_PER_M).toFixed(1); }

  hintText(stepTxt) {
    if (COARSE()) return `dots every ${stepTxt} · pinch to zoom and pan`;
    return `dots every ${stepTxt} · wheel to zoom · middle-drag or Space + drag to pan · ${MOD_KEY}+C / ${MOD_KEY}+V copy and paste · ${MOD_KEY}+Z undo · Alt: no snapping`;
  }

  /** Dot grid (1 m, or 5 m when zoomed far out; 1 ft / 5 ft) and the hint line. */
  drawGrid(ctx, P, w, H, alpha = 0.55) {
    const s = this.pxPerM, [ox, oy] = this.origin();
    const stepM = this.units === "m" ? (s >= 9 ? 1 : 5) : s / FT_PER_M >= 12 ? 1 / FT_PER_M : 5 / FT_PER_M;
    const sp = stepM * s;
    ctx.fillStyle = P.a("ink3", alpha);
    const x0 = ox - Math.ceil(ox / sp) * sp, y0 = oy - Math.ceil(oy / sp) * sp;
    for (let x = x0; x <= w; x += sp) for (let y = y0; y <= H; y += sp) { ctx.beginPath(); ctx.arc(x, y, 1, 0, 2 * Math.PI); ctx.fill(); }
    ctx.font = `9px ${FONT}`; ctx.fillStyle = P.ink3; ctx.textAlign = "left"; ctx.textBaseline = "middle";
    const stepTxt = this.units === "m" ? `${stepM} m` : stepM < 1 ? "1 ft" : "5 ft";
    ctx.fillText(this.hintText(stepTxt), 6, H - 10);
  }

  draw() {
    const { ctx, w, h: H } = fitCanvas(this.cv);
    const P = palette();
    this.drawGrid(ctx, P, w, H);
    if (!this.rooms.length) {
      ctx.font = `12px ${FONT}`; ctx.textAlign = "center"; ctx.fillText(COARSE() ? "Tap a room type above to add it, then drag an edge or corner to size it" : "Drop rooms here, then drag an edge or corner to size them", w / 2, H / 2);
    }
    for (const r of this.rooms) {
      const [x, y, rw, rh] = this.rectPx(r), col = roomColor(r.type), sel = this.selected.has(r.id);
      ctx.fillStyle = hexA(col, sel ? 0.75 : 0.55);
      ctx.fillRect(x, y, rw, rh);
      ctx.strokeStyle = sel ? P.ink : shade(col, 0.87);
      ctx.lineWidth = sel ? 1.4 : 1;
      ctx.strokeRect(x + 0.5, y + 0.5, rw - 1, rh - 1);
      ctx.save();
      ctx.beginPath(); ctx.rect(x, y, rw, rh); ctx.clip();
      ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "top";
      ctx.font = `500 10.5px ${FONT}`;
      ctx.fillText(`${ROOM_LABELS[r.type] || r.type} ${String(r.id).split("_").pop()}`, x + rw / 2, y + 4);
      ctx.font = `9px ${FONT}`; ctx.textBaseline = "middle";
      const ar = r.h > 0 ? r.w / r.h : 1;
      const lines = [this.fmtArea(r.w * r.h), `${this.fmtLen(r.w)} × ${this.fmtLen(r.h)}`, `ar ${ar.toFixed(2)}`];
      lines.forEach((t, i) => ctx.fillText(t, x + rw / 2, y + rh / 2 + (i - 1) * 12));
      ctx.restore();
      if (sel && this.selected.size === 1) this.drawHandles(ctx, P, x, y, rw, rh);
    }
    this.drawGuides(ctx, P);
    this.drawBox(ctx, P);
  }

  drawHandles(ctx, P, x, y, rw, rh) {
    ctx.fillStyle = "#fff"; ctx.strokeStyle = P.ink; ctx.lineWidth = 1;
    for (const [cx, cy] of [[x, y], [x + rw, y], [x, y + rh], [x + rw, y + rh], [x + rw / 2, y], [x + rw / 2, y + rh], [x, y + rh / 2], [x + rw, y + rh / 2]]) {
      ctx.fillRect(cx - 3, cy - 3, 6, 6); ctx.strokeRect(cx - 3, cy - 3, 6, 6);
    }
  }
  /** Dashed accent lines along the edges or centres that snapped together. */
  drawGuides(ctx, P) {
    if (!this.guides.length) return;
    ctx.save();
    ctx.strokeStyle = P.accent; ctx.lineWidth = 1; ctx.setLineDash([4, 3]);
    const ext = 0.4;
    for (const g of this.guides) {
      ctx.beginPath();
      if (g.axis === "x") { const [x, ya] = this.toPx(g.v, g.a - ext), [, yb] = this.toPx(g.v, g.b + ext); ctx.moveTo(Math.round(x) + 0.5, ya); ctx.lineTo(Math.round(x) + 0.5, yb); }
      else { const [xa, y] = this.toPx(g.a - ext, g.v), [xb] = this.toPx(g.b + ext, g.v); ctx.moveTo(xa, Math.round(y) + 0.5); ctx.lineTo(xb, Math.round(y) + 0.5); }
      ctx.stroke();
    }
    ctx.restore();
  }
  drawBox(ctx, P) {
    if (!this.box) return;
    const [a, b, c, d] = this.box;
    ctx.setLineDash([4, 3]); ctx.strokeStyle = P.ink3; ctx.fillStyle = P.a("ink3", 0.08);
    ctx.fillRect(Math.min(a, c), Math.min(b, d), Math.abs(c - a), Math.abs(d - b));
    ctx.strokeRect(Math.min(a, c), Math.min(b, d), Math.abs(c - a), Math.abs(d - b));
    ctx.setLineDash([]);
  }
}

export function shade(hex, f) {
  const n = parseInt(hex.slice(1), 16);
  const c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => Math.round(v * f));
  return `rgb(${c.join(",")})`;
}

/** Palette chips: drag one onto the canvas, or click to place it automatically. */
export function roomPalette(geom) {
  const wrap = h("div.palette", h("span.sub", COARSE() ? "Tap or drag" : "Drag a room onto the canvas:"));
  for (const rt of ["living", "bedroom", "kitchen", "bathroom", "balcony"]) {
    const chip = h("span.room-chip", { title: `Drag onto the canvas to add a ${rt} (${DEFAULT_AREA_M2[rt]} m² square to start)` }, h("span.swatch", { "data-rt": rt, style: { background: roomColor(rt) } }), ROOM_LABELS[rt]);
    chip.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      const start = [e.clientX, e.clientY];
      let ghost = null;
      const mv = (ev) => {
        if (!ghost && Math.abs(ev.clientX - start[0]) + Math.abs(ev.clientY - start[1]) > 5) {
          ghost = chip.cloneNode(true);
          Object.assign(ghost.style, { position: "fixed", pointerEvents: "none", zIndex: 70, opacity: 0.85 });   // above the full screen drawing
          document.body.append(ghost);
        }
        if (ghost) { ghost.style.left = `${ev.clientX - 20}px`; ghost.style.top = `${ev.clientY - 10}px`; }
      };
      const upH = (ev) => {
        window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", upH);
        if (ghost) {
          ghost.remove();
          const b = geom.cv.getBoundingClientRect();
          if (ev.clientX >= b.left && ev.clientX <= b.right && ev.clientY >= b.top && ev.clientY <= b.bottom) {
            geom.addRoom(rt, geom.toM(ev.clientX - b.left, ev.clientY - b.top));
            geom.cv.focus();
          }
        } else geom.addRoom(rt);
      };
      window.addEventListener("pointermove", mv); window.addEventListener("pointerup", upH);
    });
    wrap.append(chip);
  }
  return wrap;
}
