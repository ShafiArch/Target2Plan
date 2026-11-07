// schematicPaint.js: draws one synthesis frame (app/widgets/schematic_canvas.paint_frame):
// connectivity edges, room fills, double-line walls with jambs, windows, door
// leaves with swings, the entrance marker, furniture, labels, compass, caption.
import { roomColor, hexA, FONT } from "./common.js";
import { WALL_EXT_M, WALL_INT_M, synthesize, referenceBox } from "../engine/schematic.js";

/** Cached synthesis per schematic (alignment C, like the desktop's Stage 4). */
export function getSynthesis(s) {
  if (!s._syn) { const [bw, bh] = referenceBox(s); Object.defineProperty(s, "_syn", { value: synthesize(s, "C", bw, bh), enumerable: false, writable: true }); }
  return s._syn;
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

export function paintFrame(ctx, schematic, syn, frame, w, h, P, o = {}) {
  const { showEdges = true, showLabels = false, showWalls = true, showFurniture = true, showCaption = false, fontScale = 1, showCompass = true } = o;
  const [rx, ry, rw, rh] = syn.box;
  const margin = Math.max(8, Math.min(w, h) * 0.06);
  const availW = Math.max(1, w - 2 * margin), availH = Math.max(1, h - 2 * margin - (showCaption ? 12 * fontScale : 0));
  const scale = rw > 0 && rh > 0 ? Math.min(availW / rw, availH / rh) : 1;
  const bwPx = rw * scale, bhPx = rh * scale, bxPx = (w - bwPx) / 2, byPx = margin + (availH - bhPx) / 2;
  const ox = bxPx - rx * scale, oy = byPx - ry * scale;
  const TX = (x) => ox + x * scale, TY = (y) => oy + y * scale;
  const shapes = frame.shapes || [];
  const byId = new Map(shapes.map((s) => [s.room_id, s]));
  const wallsFrac = showWalls ? Number(frame.walls || 0) : 0;
  const nOpen = wallsFrac >= 1 ? Number(frame.openings || 0) : 0;
  const nFurn = showFurniture ? Number(frame.furniture || 0) : 0;
  const metre = syn.metre;
  ctx.lineCap = "butt"; ctx.lineJoin = "miter";

  // 1. connectivity edges, dashed, behind everything
  if (showEdges) {
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
      ctx.strokeStyle = P.ink2; ctx.lineWidth = Math.max(0.5, (0.6 * scale) / 2);
      for (const wl of rails) { ctx.beginPath(); ctx.moveTo(TX(wl.x1), TY(wl.y1)); ctx.lineTo(TX(wl.x2), TY(wl.y2)); ctx.stroke(); }
    }
    // 4. openings: windows (three thin lines), doors (leaf + swing), entrance marker
    for (const op of opens) {
      const [nx, ny] = op.wall.out;
      if (op.kind === "window") {
        ctx.strokeStyle = P.ink2; ctx.lineWidth = 0.5;
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
        ctx.strokeStyle = P.ink; ctx.lineWidth = Math.max(0.6, (0.8 * scale) / 2);
        ctx.beginPath(); ctx.moveTo(TX(hx), TY(hy)); ctx.lineTo(TX(hx + px * dw), TY(hy + py * dw)); ctx.stroke();
        const a0 = Math.atan2(py, px), a1 = Math.atan2(uy, ux);
        const delta = ((a1 - a0 + 3 * Math.PI) % (2 * Math.PI)) - Math.PI;
        ctx.strokeStyle = P.ink2; ctx.lineWidth = Math.max(0.4, (0.5 * scale) / 2);
        ctx.beginPath(); ctx.arc(TX(hx), TY(hy), dw * scale, a0, a0 + delta, delta < 0); ctx.stroke();
        if (op.kind === "entry") {
          const mx = (hx + fx) / 2, my = (hy + fy) / 2, s_ = 0.35 * metre;
          ctx.fillStyle = P.accent; ctx.beginPath();
          ctx.moveTo(TX(mx + nx * (tExt + 0.05 * metre)), TY(my + ny * (tExt + 0.05 * metre)));
          ctx.lineTo(TX(mx + nx * (tExt + s_) + ux * s_ * 0.6), TY(my + ny * (tExt + s_) + uy * s_ * 0.6));
          ctx.lineTo(TX(mx + nx * (tExt + s_) - ux * s_ * 0.6), TY(my + ny * (tExt + s_) - uy * s_ * 0.6));
          ctx.closePath(); ctx.fill();
        }
      }
    }
  }

  // 5. furniture: white at 50 % over the room colour, thin outline
  if (nFurn > 0) {
    ctx.strokeStyle = P.ink2; ctx.lineWidth = 0.5; ctx.fillStyle = "rgba(255,255,255,0.5)";
    for (const piece of syn.furniture.slice(0, nFurn)) for (const pr of piece.prims) {
      ctx.beginPath();
      if (pr[0] === "rect") { ctx.rect(TX(pr[1]), TY(pr[2]), pr[3] * scale, pr[4] * scale); ctx.fill(); ctx.stroke(); }
      else if (pr[0] === "circle") { ctx.arc(TX(pr[1]), TY(pr[2]), pr[3] * scale, 0, 2 * Math.PI); ctx.fill(); ctx.stroke(); }
      else { ctx.moveTo(TX(pr[1]), TY(pr[2])); ctx.lineTo(TX(pr[3]), TY(pr[4])); ctx.stroke(); }
    }
  }

  // 6. labels (detail view, exports)
  if (showLabels) {
    for (const s of shapes) {
      const room = s.room || {}, qv = room.quality_view_score, aq = room.acoustic_score;
      const hasScores = qv != null && aq != null && room.zone_assignment != null;
      const cx = TX(s.cx), cy = TY(s.cy);
      halo(ctx, s.room_id.replace(/_/g, " "), cx, cy + (hasScores ? -5 : 0) * fontScale, `600 ${9 * fontScale}px ${FONT}`);
      if (hasScores) halo(ctx, `QV ${Number(qv).toFixed(2)} · AQ ${Number(aq).toFixed(2)}`, cx, cy + 7 * fontScale, `${8 * fontScale}px ${FONT}`);
    }
  }

  // 7. compass + caption
  const fs = fontScale;
  if (showCompass) {
    ctx.font = `700 ${9 * fs}px ${FONT}`; ctx.fillStyle = P.accent;
    ctx.textAlign = "center"; ctx.textBaseline = "bottom"; ctx.fillText("N", bxPx + bwPx / 2, Math.max(9 * fs, byPx - 3 * fs));
    ctx.textBaseline = "top"; ctx.fillText("S", bxPx + bwPx / 2, Math.min(h - 11 * fs, byPx + bhPx + 2));
    ctx.textBaseline = "middle"; ctx.textAlign = "left"; ctx.fillText("E", Math.min(w - 9 * fs, bxPx + bwPx + 3), byPx + bhPx / 2);
    ctx.textAlign = "right"; ctx.fillText("W", Math.max(9 * fs, bxPx - 3), byPx + bhPx / 2);
  }
  if (showCaption && frame.label) {
    ctx.font = `${9 * fs}px ${FONT}`; ctx.fillStyle = P.ink3; ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.fillText(frame.label, 6 * fs, h - 8 * fs);
  }
  return { scale, ox, oy, box: [bxPx, byPx, bwPx, bhPx] };
}

function halo(ctx, str, x, y, font) {
  ctx.font = font; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.lineWidth = 2.5; ctx.strokeStyle = "rgba(0,0,0,0.5)"; ctx.lineJoin = "round"; ctx.strokeText(str, x, y);
  ctx.fillStyle = "#fff"; ctx.fillText(str, x, y);
  ctx.lineJoin = "miter";
}

function roundRect(ctx, x, y, w, h, r) {
  if (ctx.roundRect) { ctx.roundRect(x, y, w, h, r); return; }
  ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}
