// geometry.js: the "Draw room program" canvas of Stage 1
// (app/widgets/geometry_canvas.py). Rooms are dragged from the palette onto
// a dot grid, moved by dragging, resized by an edge or corner (0.1 m snap,
// 1 m minimum side). Sizes are metres inside; the display follows m² / ft².
import { h, palette, roomColor, fitCanvas, onResize, FONT, hexA } from "./common.js";

export const FT_PER_M = 3.280839895;
export const SQFT_PER_M2 = FT_PER_M * FT_PER_M;
export const ROOM_LABELS = { living: "Living", bedroom: "Bedroom", kitchen: "Kitchen", bathroom: "Bathroom", balcony: "Balcony" };
export const DEFAULT_AREA_M2 = { living: 20, bedroom: 14, kitchen: 9, bathroom: 5, balcony: 5 };
const MIN_SIDE_M = 1.0, SNAP_M = 0.1, HANDLE_PX = 7;
const snap = (v) => Math.round(v / SNAP_M) * SNAP_M;
const overlaps = ([x, y, w, hh], r) => !(x + w <= r.x || r.x + r.w <= x || y + hh <= r.y || r.y + r.h <= y);

export class GeometryCanvas {
  constructor(wrap, onChange) {
    this.wrap = wrap;
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
    this.cv.addEventListener("pointerdown", (e) => this.down(e));
    this.cv.addEventListener("pointermove", (e) => this.move(e));
    this.cv.addEventListener("pointerup", (e) => this.up(e));
    this.cv.addEventListener("pointercancel", (e) => this.up(e));
    this.cv.addEventListener("keydown", (e) => {
      if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); this.removeSelected(); }
    });
    onResize(wrap, () => { if (this.autoCentre) this.centre(); else this.draw(); });
  }

  changed() { this.onChange?.(); this.draw(); }

  setRooms(rooms) {
    this.rooms = rooms.map((r) => ({ ...r }));
    this.counter = {};
    for (const r of this.rooms) this.counter[r.type] = Math.max(this.counter[r.type] || 0, Number(r.id.split("_").pop()) + 1);
    this.selected = new Set(); this.autoCentre = true; this.centre(); this.changed();
  }
  setUnits(u) { if (u !== this.units) { this.units = u; this.draw(); } }
  setPlan(wm, hm) { this.planW = Math.max(1, wm); this.planH = Math.max(1, hm); this.draw(); }
  setScale(px) { this.pxPerM = Math.max(6, Math.min(120, px)); this.centre(); }

  loadSample() {
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
    const s = this.pxPerM;
    const x0 = Math.min(...this.rooms.map((q) => q.x)), x1 = Math.max(...this.rooms.map((q) => q.x + q.w));
    const y0 = Math.min(...this.rooms.map((q) => q.y)), y1 = Math.max(...this.rooms.map((q) => q.y + q.h));
    this.pan = [r.width / 2 - ((x0 + x1) / 2) * s - this.pad, r.height / 2 - ((y0 + y1) / 2) * s - this.pad];
    this.draw();
  }

  clear() { this.rooms = []; this.counter = {}; this.selected = new Set(); this.changed(); }
  removeSelected() { if (!this.selected.size) return; this.rooms = this.rooms.filter((r) => !this.selected.has(r.id)); this.selected = new Set(); this.changed(); }
  totalAreaM2() { return this.rooms.reduce((s, r) => s + r.w * r.h, 0); }
  counts() { const c = {}; for (const r of this.rooms) c[r.type] = (c[r.type] || 0) + 1; return c; }

  addRoom(type, at = null) {
    const side = Math.sqrt(DEFAULT_AREA_M2[type] ?? 10);
    const n = this.counter[type] || 0; this.counter[type] = n + 1;
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
    const room = { id: `${type}_${n}`, type, x: snap(at[0] - side / 2), y: snap(at[1] - side / 2), w: side, h: side };
    this.rooms.push(room);
    this.selected = new Set([room.id]);
    this.changed();
    return room;
  }

  origin() { return [this.pad + this.pan[0], this.pad + this.pan[1]]; }
  toPx(xm, ym) { const [ox, oy] = this.origin(); return [ox + xm * this.pxPerM, oy + ym * this.pxPerM]; }
  toM(px, py) { const [ox, oy] = this.origin(); return [(px - ox) / this.pxPerM, (py - oy) / this.pxPerM]; }
  rectPx(r) { const [x, y] = this.toPx(r.x, r.y); return [x, y, r.w * this.pxPerM, r.h * this.pxPerM]; }
  local(e) { const b = this.cv.getBoundingClientRect(); return [e.clientX - b.left, e.clientY - b.top]; }

  hit(px, py) {
    for (let i = this.rooms.length - 1; i >= 0; i--) {
      const r = this.rooms[i], [x, y, w, hh] = this.rectPx(r);
      if (px < x - HANDLE_PX || px > x + w + HANDLE_PX || py < y - HANDLE_PX || py > y + hh + HANDLE_PX) continue;
      const edges = new Set();
      if (Math.abs(px - x) <= HANDLE_PX) edges.add("W");
      if (Math.abs(px - (x + w)) <= HANDLE_PX) edges.add("E");
      if (Math.abs(py - y) <= HANDLE_PX) edges.add("N");
      if (Math.abs(py - (y + hh)) <= HANDLE_PX) edges.add("S");
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

  down(e) {
    if (e.button !== 0) return;
    this.cv.focus();
    this.cv.setPointerCapture(e.pointerId);
    const [px, py] = this.local(e);
    const [r, edges] = this.hit(px, py);
    if (!r) {
      if (!e.shiftKey) this.selected = new Set();
      this.drag = { mode: "box", start: [px, py] };
      this.box = [px, py, px, py];
      this.draw(); return;
    }
    if (edges.size && this.selected.size <= 1) {
      this.selected = new Set([r.id]);
      this.drag = { mode: "resize", edges, start: [px, py], orig: { ...r }, room: r };
    } else {
      if (e.shiftKey) { if (this.selected.has(r.id)) this.selected.delete(r.id); else this.selected.add(r.id); }
      else if (!this.selected.has(r.id)) this.selected = new Set([r.id]);
      const movers = this.rooms.filter((q) => this.selected.has(q.id));
      this.drag = { mode: "move", start: [px, py], origs: new Map(movers.map((q) => [q.id, [q.x, q.y]])), rooms: movers };
    }
    this.draw();
  }

  move(e) {
    const [px, py] = this.local(e);
    if (!this.drag) { const [r, edges] = this.hit(px, py); this.cv.style.cursor = r ? this.cursorFor(edges) : "default"; return; }
    const s = this.pxPerM, dx = (px - this.drag.start[0]) / s, dy = (py - this.drag.start[1]) / s;
    if (this.drag.mode === "box") { this.box = [this.drag.start[0], this.drag.start[1], px, py]; this.draw(); return; }
    this.autoCentre = false;
    if (this.drag.mode === "move") {
      for (const r of this.drag.rooms) { const [ox, oy] = this.drag.origs.get(r.id); r.x = snap(ox + dx); r.y = snap(oy + dy); }
    } else {
      const o = this.drag.orig, r = this.drag.room, ed = this.drag.edges;
      let x0 = o.x, y0 = o.y, x1 = o.x + o.w, y1 = o.y + o.h;
      if (ed.has("W")) x0 = Math.min(snap(o.x + dx), x1 - MIN_SIDE_M);
      if (ed.has("E")) x1 = Math.max(snap(o.x + o.w + dx), x0 + MIN_SIDE_M);
      if (ed.has("N")) y0 = Math.min(snap(o.y + dy), y1 - MIN_SIDE_M);
      if (ed.has("S")) y1 = Math.max(snap(o.y + o.h + dy), y0 + MIN_SIDE_M);
      Object.assign(r, { x: x0, y: y0, w: x1 - x0, h: y1 - y0 });
    }
    this.changed();
  }

  up() {
    if (!this.drag) return;
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
    this.drag = null;
    this.changed();
  }

  fmtArea(a) { return this.units === "m" ? `${a.toFixed(1)} m²` : `${(a * SQFT_PER_M2).toFixed(0)} ft²`; }
  fmtLen(v) { return this.units === "m" ? v.toFixed(1) : (v * FT_PER_M).toFixed(1); }

  draw() {
    const { ctx, w, h: H } = fitCanvas(this.cv);
    const P = palette(), s = this.pxPerM, [ox, oy] = this.origin();
    const stepM = this.units === "m" ? 1 : s / FT_PER_M >= 12 ? 1 / FT_PER_M : 5 / FT_PER_M;
    const sp = stepM * s;
    ctx.fillStyle = P.a("ink3", 0.55);
    const x0 = ox - Math.ceil(ox / sp) * sp, y0 = oy - Math.ceil(oy / sp) * sp;
    for (let x = x0; x <= w; x += sp) for (let y = y0; y <= H; y += sp) { ctx.beginPath(); ctx.arc(x, y, 1, 0, 2 * Math.PI); ctx.fill(); }
    ctx.font = `9px ${FONT}`; ctx.fillStyle = P.ink3; ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.fillText(`dots every ${this.units === "m" ? "1 m" : stepM < 1 ? "1 ft" : "5 ft"}`, 6, H - 10);
    if (!this.rooms.length) {
      ctx.font = `12px ${FONT}`; ctx.textAlign = "center"; ctx.fillText("Drop rooms here, then drag an edge or corner to size them", w / 2, H / 2);
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
      ctx.fillText(`${ROOM_LABELS[r.type] || r.type} ${r.id.split("_").pop()}`, x + rw / 2, y + 4);
      ctx.font = `9px ${FONT}`; ctx.textBaseline = "middle";
      const ar = r.h > 0 ? r.w / r.h : 1;
      const lines = [this.fmtArea(r.w * r.h), `${this.fmtLen(r.w)} × ${this.fmtLen(r.h)}`, `ar ${ar.toFixed(2)}`];
      lines.forEach((t, i) => ctx.fillText(t, x + rw / 2, y + rh / 2 + (i - 1) * 12));
      ctx.restore();
      if (sel && this.selected.size === 1) {
        ctx.fillStyle = "#fff"; ctx.strokeStyle = P.ink; ctx.lineWidth = 1;
        for (const [cx, cy] of [[x, y], [x + rw, y], [x, y + rh], [x + rw, y + rh], [x + rw / 2, y], [x + rw / 2, y + rh], [x, y + rh / 2], [x + rw, y + rh / 2]]) {
          ctx.fillRect(cx - 3, cy - 3, 6, 6); ctx.strokeRect(cx - 3, cy - 3, 6, 6);
        }
      }
    }
    if (this.box) {
      const [a, b, c, d] = this.box;
      ctx.setLineDash([4, 3]); ctx.strokeStyle = P.ink3; ctx.fillStyle = P.a("ink3", 0.08);
      ctx.fillRect(Math.min(a, c), Math.min(b, d), Math.abs(c - a), Math.abs(d - b));
      ctx.strokeRect(Math.min(a, c), Math.min(b, d), Math.abs(c - a), Math.abs(d - b));
      ctx.setLineDash([]);
    }
  }
}

function shade(hex, f) {
  const n = parseInt(hex.slice(1), 16);
  const c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => Math.round(v * f));
  return `rgb(${c.join(",")})`;
}

/** Palette chips: drag one onto the canvas, or click to place it automatically. */
export function roomPalette(geom) {
  const wrap = h("div.palette", h("span.sub", "Drag a room onto the canvas:"));
  for (const rt of ["living", "bedroom", "kitchen", "bathroom", "balcony"]) {
    const chip = h("span.room-chip", { title: `Drag onto the canvas to add a ${rt} (${DEFAULT_AREA_M2[rt]} m² square to start)` }, h("span.swatch", { "data-rt": rt, style: { background: roomColor(rt) } }), ROOM_LABELS[rt]);
    chip.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      const start = [e.clientX, e.clientY];
      let ghost = null;
      const mv = (ev) => {
        if (!ghost && Math.abs(ev.clientX - start[0]) + Math.abs(ev.clientY - start[1]) > 5) {
          ghost = chip.cloneNode(true);
          Object.assign(ghost.style, { position: "fixed", pointerEvents: "none", zIndex: 40, opacity: 0.85 });
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
