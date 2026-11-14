// schematicPaint.js: draws one synthesis frame (app/widgets/schematic_canvas.paint_frame):
// connectivity edges, room fills, double-line walls with jambs, windows, door
// leaves with swings, the entrance marker, furniture, labels, compass, caption.
import { roomColor as themeRoomColor, hexA, FONT } from "./common.js";
import { WALL_EXT_M, WALL_INT_M, synthesize, referenceBox } from "../engine/schematic.js";

/** Furniture drawing styles for the Schematic toolbar (value, label). */
export const FURNITURE_STYLES = [
  ["line", "Line drawing"], ["line20", "Line drawing, 0.2"], ["line50", "Line drawing, 0.5"],
  ["fill20", "Fill 0.2"], ["fill50", "Fill 0.5"], ["none", "No furniture"],
];
export const DEFAULT_FURNITURE = "line50";

/** Cached synthesis per schematic (alignment C, like the prototype's Stage 4).
 *  An edited plan (see ui/planEdit.js) keeps its rebuilt plan here instead. */
export function getSynthesis(s) {
  if (!s._syn) Object.defineProperty(s, "_syn", { value: originalSynthesis(s), enumerable: false, writable: true, configurable: true });
  return s._syn;
}
/** The synthesis of the plan as generated, also for a plan that was edited since. */
export function originalSynthesis(s) {
  if (!s._origSyn) { const [bw, bh] = referenceBox(s); Object.defineProperty(s, "_origSyn", { value: synthesize(s, "C", bw, bh), enumerable: false, writable: true, configurable: true }); }
  return s._origSyn;
}

// ── framing ─────────────────────────────────────────────────────────
// Plans are framed by their own extent instead of the reference box, so they
// sit centred in the view. Every frame is framed by everything still to come
// (that frame up to the finished plan), which zooms in smoothly while the
// rooms compact and ends exactly on the finished plan.
const FRAME_PAD_M = 0.75;   // exterior wall (0.25 m) plus the entrance marker

function shapeExtent(s) {
  if (s.kind === "rect" && "w" in s) {
    const m = Number(s.morph ?? 1), r = s.r ?? Math.sqrt((s.w * s.h) / Math.PI);
    const ww = r * 2 + (s.w - r * 2) * m, hh = r * 2 + (s.h - r * 2) * m;
    return [s.cx - ww / 2, s.cy - hh / 2, s.cx + ww / 2, s.cy + hh / 2];
  }
  return [s.cx - s.r, s.cy - s.r, s.cx + s.r, s.cy + s.r];
}
const union = (a, b) => (!a ? b : !b ? a : [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])]);

/** {final, suffix[i]}: padded extents of the finished plan and of frames i..end. */
export function planBounds(syn) {
  if (syn._bounds) return syn._bounds;
  const n = syn.frames.length, pad = FRAME_PAD_M * syn.metre;
  const [bx, by, bw, bh] = syn.box;
  const padded = (b) => (b ? [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad] : [bx, by, bx + bw, by + bh]);
  const suffix = new Array(n);
  let acc = null;
  for (let i = n - 1; i >= 0; i--) {
    for (const s of syn.frames[i].shapes || []) acc = union(acc, shapeExtent(s));
    suffix[i] = padded(acc);
  }
  const out = { final: n ? suffix[n - 1] : padded(null), suffix };
  Object.defineProperty(syn, "_bounds", { value: out, enumerable: false });
  return out;
}

/** Sizes of the finished plans, so a set of cards shares one scale (the largest at which every plan fits). */
export function commonFit(schematics) {
  const dims = schematics.map((s) => { const b = planBounds(getSynthesis(s)).final; return [b[2] - b[0], b[3] - b[1]]; }).filter(([w, h]) => w > 0 && h > 0);
  return dims.length ? dims : null;
}

/** The room under canvas point (x, y) for a transform returned by paintFrame. */
export function roomAt(frame, T, x, y) {
  if (!frame || !T) return null;
  const px = (x - T.ox) / T.scale, py = (y - T.oy) / T.scale;
  const shapes = frame.shapes || [];
  for (let i = shapes.length - 1; i >= 0; i--) {
    const s = shapes[i];
    if (s.kind === "rect" && "w" in s) {
      const [x0, y0, x1, y1] = shapeExtent(s);
      if (px >= x0 && px <= x1 && py >= y0 && py <= y1) return s.room || null;
    } else if ((px - s.cx) ** 2 + (py - s.cy) ** 2 <= s.r * s.r) return s.room || null;
  }
  return null;
}

function cornerFree(wl, syn, end) {
  const x = end === "start" ? wl.x1 : wl.x2, y = end === "start" ? wl.y1 : wl.y2, rid = wl.rooms[0];
  const horizontal = Math.abs(wl.y1 - wl.y2) < 1e-6;
  for (const o of syn.walls) {
    if (o === wl || o.rooms[0] !== rid) continue;
    if ((Math.abs(o.y1 - o.y2) < 1e-6) === horizontal) continue;
    if ((Math.abs(o.x1 - x) < 1e-3 && Math.abs(o.y1 - y) < 1e-3) || (Math.abs(o.x2 - x) < 1e-3 && Math.abs(o.y2 - y) < 1e-3)) return o.kind === "ext" ? 1 : 0;
  }
  return 0;
}

/** Paint one frame centred in a w x h view. `fitDims` (plan sizes from
 *  commonFit) gives several cards the same scale; without it the finished plan
 *  fills the view. */
export function paintFrame(ctx, schematic, syn, frame, w, h, P, o = {}) {
  const { showEdges = true, showLabels = false, showWalls = true, showCaption = false, fontScale = 1, fitDims = null } = o;
  // o.transform {scale, ox, oy} places the plan (the plan editor's zoom and pan; exports to scale);
  // o.roomColor overrides the theme's room colours (exports)
  const showCompass = o.transform ? false : o.showCompass ?? true, roomColor = o.roomColor || themeRoomColor;
  // furniture style: line | line20 | line50 | fill20 | fill50 | none (see FURNITURE_STYLES)
  const furniture = o.furniture ?? (o.showFurniture === false ? "none" : DEFAULT_FURNITURE);
  const B = planBounds(syn);
  const view = B.suffix[Math.max(0, syn.frames.indexOf(frame))] || B.final;
  const edge = showCompass ? 13 * fontScale : 0;
  const margin = Math.max(6, Math.min(w, h) * 0.035) + edge;
  const availW = Math.max(1, w - 2 * margin), availH = Math.max(1, h - 2 * margin);
  const fit = (bw, bh) => Math.min(availW / Math.max(bw, 1e-9), availH / Math.max(bh, 1e-9));
  const sizes = fitDims && fitDims.length ? fitDims : [[B.final[2] - B.final[0], B.final[3] - B.final[1]]];
  let scale = Math.min(...sizes.map(([fw, fh]) => fit(fw, fh)), fit(view[2] - view[0], view[3] - view[1]));
  let ox = w / 2 - ((view[0] + view[2]) / 2) * scale, oy = h / 2 - ((view[1] + view[3]) / 2) * scale;
  if (o.transform) ({ scale, ox, oy } = o.transform);
  const TX = (x) => ox + x * scale, TY = (y) => oy + y * scale;
  const shapes = frame.shapes || [];
  const byId = new Map(shapes.map((s) => [s.room_id, s]));
  const wallsFrac = showWalls ? Number(frame.walls || 0) : 0;
  const nOpen = wallsFrac >= 1 ? Number(frame.openings || 0) : 0;
  const nFurn = furniture !== "none" ? Number(frame.furniture || 0) : 0;
  const metre = syn.metre;
  ctx.lineCap = "butt"; ctx.lineJoin = "miter";

  // 1. connectivity edges, dashed, behind everything
  if (showEdges) {
    ctx.layer?.("A-ANNO-EDGE");   // CAD layer of what follows (planExport.js records the drawing; a canvas has no layer())
    ctx.strokeStyle = P.a("ink3", 0.43); ctx.lineWidth = 0.7; ctx.setLineDash([1.4, 1.4]);
    for (const e of schematic.edges || []) {
      const a = byId.get(e.source), b = byId.get(e.target);
      if (!a || !b) continue;
      ctx.beginPath(); ctx.moveTo(TX(a.cx), TY(a.cy)); ctx.lineTo(TX(b.cx), TY(b.cy)); ctx.stroke();
    }
    ctx.setLineDash([]);
  }

  // 2. rooms (circle → rectangle morph)
  const fillAlpha = wallsFrac > 0 || nFurn > 0 ? 0.55 : 0.9;
  for (const s of shapes) {
    ctx.layer?.(`A-AREA-${String(s.room.room_type).toUpperCase()}`);
    ctx.fillStyle = hexA(roomColor(s.room.room_type), fillAlpha);
    ctx.strokeStyle = P.surface; ctx.lineWidth = 0.8;
    ctx.beginPath();
    if (s.kind === "rect" && "w" in s) {
      const m = Number(s.morph ?? 1), r = s.r ?? Math.sqrt((s.w * s.h) / Math.PI);
      const ww = r * 2 + (s.w - r * 2) * m, hh = r * 2 + (s.h - r * 2) * m;
      const rad = Math.max(0, Math.min(((1 - m) * Math.min(ww, hh)) / 2 * scale + 1 * m, (Math.min(ww, hh) * scale) / 2));
      roundRect(ctx, TX(s.cx - ww / 2), TY(s.cy - hh / 2), ww * scale, hh * scale, rad);
    } else ctx.arc(TX(s.cx), TY(s.cy), s.r * scale, 0, 2 * Math.PI);
    ctx.fill(); ctx.stroke();
  }

  // 3. walls: hollow bands, clipped where they run through another band
  if (wallsFrac > 0 && syn.walls.length) {
    const tExt = WALL_EXT_M * metre, tInt = WALL_INT_M * metre;
    const ext = syn.walls.filter((wl) => wl.kind === "ext"), inn = syn.walls.filter((wl) => wl.kind === "int"), rails = syn.walls.filter((wl) => wl.kind === "rail");
    const nExt = Math.round(ext.length * Math.min(1, wallsFrac * 2)), nInt = Math.round(inn.length * Math.max(0, wallsFrac * 2 - 1));
    const opens = syn.openings.slice(0, nOpen);
    const bandRect = (wl, thick, outward) => {
      const [nx, ny] = wl.out;
      const [a0, a1] = outward ? [0, thick] : [-thick / 2, thick / 2];
      let xs, ys;
      if (Math.abs(ny) > 0) { xs = [Math.min(wl.x1, wl.x2), Math.max(wl.x1, wl.x2)]; ys = [wl.y1 + ny * a0, wl.y1 + ny * a1].sort((p, q) => p - q); }
      else { xs = [wl.x1 + nx * a0, wl.x1 + nx * a1].sort((p, q) => p - q); ys = [Math.min(wl.y1, wl.y2), Math.max(wl.y1, wl.y2)]; }
      if (outward) {
        if (Math.abs(ny) > 0) xs = [xs[0] - thick * cornerFree(wl, syn, "start"), xs[1] + thick * cornerFree(wl, syn, "end")];
        else ys = [ys[0] - thick * cornerFree(wl, syn, "start"), ys[1] + thick * cornerFree(wl, syn, "end")];
      }
      return [xs[0], ys[0], xs[1], ys[1]];
    };
    const pieces = (wl, [x0, y0, x1, y1]) => {
      const horizontal = Math.abs(wl.out[1]) > 0;
      const [lo, hi] = horizontal ? [x0, x1] : [y0, y1];
      const gaps = opens.filter((op) => op.wall === wl).map((op) => (horizontal ? [Math.min(op.x1, op.x2), Math.max(op.x1, op.x2)] : [Math.min(op.y1, op.y2), Math.max(op.y1, op.y2)])).sort((p, q) => p[0] - q[0] || p[1] - q[1]);
      const out = [];
      let cur = lo;
      for (const [g0, g1] of gaps) { if (g0 - cur > 1e-6) out.push([cur, g0]); cur = Math.max(cur, g1); }
      if (hi - cur > 1e-6) out.push([cur, hi]);
      return [out.map(([a, b]) => (horizontal ? [a, y0, b, y1] : [x0, a, x1, b])), gaps];
    };
    const bands = [...ext.slice(0, nExt).map((wl) => [bandRect(wl, tExt, true), wl]), ...inn.slice(0, nInt).map((wl) => [bandRect(wl, tInt, false), wl])];
    const allRects = bands.map((b) => b[0]);
    const clipped = (x0, y0, x1, y1, horizontal, skip) => {
      const [lo, hi] = horizontal ? [x0, x1] : [y0, y1], cst = horizontal ? y0 : x0, eps = 0.05 * metre;
      const cover = [];
      allRects.forEach(([a, b, c, d], i) => {
        if (i === skip) return;
        if (horizontal) { if (b + eps < cst && cst < d - eps) cover.push([a, c]); }
        else if (a + eps < cst && cst < c - eps) cover.push([b, d]);
      });
      cover.sort((p, q) => p[0] - q[0] || p[1] - q[1]);
      let cur = lo;
      const segs = [];
      for (const [c0, c1] of cover) { if (c0 > cur) segs.push([cur, Math.min(c0, hi)]); cur = Math.max(cur, c1); if (cur >= hi) break; }
      if (cur < hi) segs.push([cur, hi]);
      for (const [a, b] of segs) {
        if (b - a <= 1e-6) continue;
        ctx.beginPath();
        if (horizontal) { ctx.moveTo(TX(a), TY(cst)); ctx.lineTo(TX(b), TY(cst)); } else { ctx.moveTo(TX(cst), TY(a)); ctx.lineTo(TX(cst), TY(b)); }
        ctx.stroke();
      }
    };
    ctx.layer?.("A-WALL");
    ctx.fillStyle = P.surface;
    const pieceLists = bands.map(([rect, wl]) => {
      const pl = pieces(wl, rect);
      for (const [a, b, c, d] of pl[0]) ctx.fillRect(TX(a), TY(b), (c - a) * scale, (d - b) * scale);
      return pl;
    });
    ctx.strokeStyle = P.ink; ctx.lineWidth = Math.max(0.6, (0.9 * scale) / 2);
    bands.forEach(([, wl], idx) => {
      const horizontal = Math.abs(wl.out[1]) > 0, [pcs, gaps] = pieceLists[idx];
      for (const [a, b, c, d] of pcs) {
        if (horizontal) {
          clipped(a, b, c, b, true, idx); clipped(a, d, c, d, true, idx);
          for (const g of gaps.flat()) if (Math.abs(g - a) < 1e-6 || Math.abs(g - c) < 1e-6) { ctx.beginPath(); ctx.moveTo(TX(g), TY(b)); ctx.lineTo(TX(g), TY(d)); ctx.stroke(); }
        } else {
          clipped(a, b, a, d, false, idx); clipped(c, b, c, d, false, idx);
          for (const g of gaps.flat()) if (Math.abs(g - b) < 1e-6 || Math.abs(g - d) < 1e-6) { ctx.beginPath(); ctx.moveTo(TX(a), TY(g)); ctx.lineTo(TX(c), TY(g)); ctx.stroke(); }
        }
      }
    });
    if (wallsFrac >= 0.5) {
      ctx.layer?.("A-WALL-RAIL");
      ctx.strokeStyle = P.ink2; ctx.lineWidth = Math.max(0.5, (0.6 * scale) / 2);
      for (const wl of rails) { ctx.beginPath(); ctx.moveTo(TX(wl.x1), TY(wl.y1)); ctx.lineTo(TX(wl.x2), TY(wl.y2)); ctx.stroke(); }
    }
    // 4. openings: windows (three hairlines), doors (leaf + swing), entrance marker.
    // Thin, lighter ink so the openings read as hairlines next to the walls.
    const pxm = metre * scale;
    const winW = Math.max(0.3, pxm * 0.005), leafW = Math.max(0.4, pxm * 0.008), swingW = Math.max(0.3, pxm * 0.005);
    for (const op of opens) {
      const [nx, ny] = op.wall.out;
      if (op.kind === "window") {
        ctx.layer?.("A-GLAZ");
        ctx.strokeStyle = P.a("ink2", 0.8); ctx.lineWidth = winW;
        for (const f of [0, 0.5, 1]) {
          const off = tExt * f;
          ctx.beginPath(); ctx.moveTo(TX(op.x1 + nx * off), TY(op.y1 + ny * off)); ctx.lineTo(TX(op.x2 + nx * off), TY(op.y2 + ny * off)); ctx.stroke();
        }
      } else if (op.kind === "door" || op.kind === "balcony_door" || op.kind === "entry") {
        const [hx, hy] = op.hinge;
        const atStart = Math.abs(op.x1 - hx) < 1e-6 && Math.abs(op.y1 - hy) < 1e-6;
        const [fx, fy] = atStart ? [op.x2, op.y2] : [op.x1, op.y1];
        const dw = op.width, into = byId.get(op.into);
        const [ix, iy] = into ? [into.cx, into.cy] : [hx - nx, hy - ny];
        const ux = (fx - hx) / dw, uy = (fy - hy) / dw;
        let px = -uy, py = ux;
        if ((ix - hx) * px + (iy - hy) * py < 0) { px = uy; py = -ux; }
        ctx.layer?.("A-DOOR");
        ctx.strokeStyle = P.ink2; ctx.lineWidth = leafW;
        ctx.beginPath(); ctx.moveTo(TX(hx), TY(hy)); ctx.lineTo(TX(hx + px * dw), TY(hy + py * dw)); ctx.stroke();
        const a0 = Math.atan2(py, px), a1 = Math.atan2(uy, ux);
        const delta = ((a1 - a0 + 3 * Math.PI) % (2 * Math.PI)) - Math.PI;
        ctx.strokeStyle = P.a("ink3", 0.85); ctx.lineWidth = swingW;
        ctx.beginPath(); ctx.arc(TX(hx), TY(hy), dw * scale, a0, a0 + delta, delta < 0); ctx.stroke();
        if (op.kind === "entry") {
          const mx = (hx + fx) / 2, my = (hy + fy) / 2, s_ = 0.35 * metre;
          ctx.layer?.("A-ENTRY");
          ctx.fillStyle = P.accent; ctx.beginPath();
          ctx.moveTo(TX(mx + nx * (tExt + 0.05 * metre)), TY(my + ny * (tExt + 0.05 * metre)));
          ctx.lineTo(TX(mx + nx * (tExt + s_) + ux * s_ * 0.6), TY(my + ny * (tExt + s_) + uy * s_ * 0.6));
          ctx.lineTo(TX(mx + nx * (tExt + s_) - ux * s_ * 0.6), TY(my + ny * (tExt + s_) - uy * s_ * 0.6));
          ctx.closePath(); ctx.fill();
        }
      }
    }
  }

  // 5. furniture (placement after RGeoLib, see engine/schematic.js), always in
  // white: a line drawing at full, 0.5 or 0.2 strength, or white blocks at
  // 0.2 / 0.5 over the room colour with a white outline
  if (nFurn > 0) {
    ctx.layer?.("A-FURN");
    const filled = furniture.startsWith("fill"), k = furniture.endsWith("20") ? 0.2 : furniture.endsWith("50") ? 0.5 : 1;
    const white = (a) => `rgba(255,255,255,${a})`, lw = Math.max(0.7, metre * scale * 0.014);
    if (filled) { ctx.fillStyle = white(k); ctx.strokeStyle = white(0.95); ctx.lineWidth = lw; }
    else { ctx.strokeStyle = white(k); ctx.lineWidth = lw; }
    for (const piece of syn.furniture.slice(0, nFurn)) for (const pr of piece.prims) {
      ctx.beginPath();
      if (pr[0] === "rect") { ctx.rect(TX(pr[1]), TY(pr[2]), pr[3] * scale, pr[4] * scale); if (filled) ctx.fill(); ctx.stroke(); }
      else if (pr[0] === "circle") { ctx.arc(TX(pr[1]), TY(pr[2]), pr[3] * scale, 0, 2 * Math.PI); if (filled) ctx.fill(); ctx.stroke(); }
      else { ctx.moveTo(TX(pr[1]), TY(pr[2])); ctx.lineTo(TX(pr[3]), TY(pr[4])); ctx.stroke(); }
    }
  }

  // 6. labels (detail view, exports): plain ink snapped to whole pixels, with
  // no halo and nothing behind the text, so the furniture stays in view.
  if (showLabels) {
    ctx.layer?.("A-ANNO-TEXT");
    const fs1 = `500 ${8.5 * fontScale}px ${FONT}`, fs2 = `400 ${7.5 * fontScale}px ${FONT}`, k = fontScale;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const s of shapes) {
      const room = s.room || {}, qv = room.quality_view_score, aq = room.acoustic_score;
      const hasScores = qv != null && aq != null && room.zone_assignment != null;
      const name = s.room_id.replace(/_/g, " "), sub = hasScores ? `QV ${Number(qv).toFixed(2)} · AQ ${Number(aq).toFixed(2)}` : null;
      const cx = Math.round(TX(s.cx)), cy = Math.round(TY(s.cy));
      ctx.font = fs1; ctx.fillStyle = P.ink; ctx.fillText(name, cx, cy + Math.round((sub ? -4.5 : 0) * k));
      if (sub) { ctx.font = fs2; ctx.fillStyle = P.ink2; ctx.fillText(sub, cx, cy + Math.round(5.5 * k)); }
    }
  }

  // 7. compass letters just outside the plan + caption
  const fs = fontScale;
  ctx.layer?.("A-ANNO-TEXT");
  if (showCompass) {
    const e = edge / 2, mx = Math.round(w / 2), my = Math.round(h / 2);
    const top = Math.max(e, TY(view[1]) - e), bottom = Math.min(h - e, TY(view[3]) + e);
    const left = Math.max(e, TX(view[0]) - e), right = Math.min(w - e, TX(view[2]) + e);
    ctx.font = `600 ${8 * fs}px ${FONT}`; ctx.fillStyle = P.accent; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText("N", mx, Math.round(top)); ctx.fillText("S", mx, Math.round(bottom));
    ctx.fillText("W", Math.round(left), my); ctx.fillText("E", Math.round(right), my);
  }
  if (showCaption && frame.label) {
    ctx.font = `400 ${8 * fs}px ${FONT}`; ctx.textAlign = "left"; ctx.textBaseline = "middle";
    const tw = ctx.measureText(frame.label).width, y = Math.round(h - 8 * fs);
    ctx.fillStyle = P.a("surface", 0.85); ctx.fillRect(4 * fs, y - 6 * fs, tw + 6 * fs, 12 * fs);
    ctx.fillStyle = P.ink3; ctx.fillText(frame.label, Math.round(7 * fs), y);
  }
  return { scale, ox, oy };
}

function roundRect(ctx, x, y, w, h, r) {
  if (ctx.roundRect) { ctx.roundRect(x, y, w, h, r); return; }
  ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}
