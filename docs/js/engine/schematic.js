// schematic.js: Stage 4 schematic data, packing and the recorded synthesis
// (models/schematic_mock.py + models/schematic_layout.py).
// Rooms slide into contact, walls are split into exterior / partition /
// railing, doors sit on connectivity edges, windows follow the planned WWR,
// and furniture blocks are placed against walls. Every step is recorded as a
// frame so the canvas can replay the synthesis.
import { pyHypot, pySum } from "./pymath.js";

export const WALL_EXT_M = 0.25, WALL_INT_M = 0.12, DOOR_W_M = 0.9, DOOR_OFF_M = 0.2, DOOR_CLEAR_M = 1.0;
export const WIN_FRAC_PER_WWR = 2.7 / 1.5, WIN_MIN_M = 0.6, WIN_MAX_FRAC = 0.85, EPS = 1e-3;
export const REFERENCE_BOX_SIZE = 600;
const PRIMARY_TYPES = new Set(["living", "bedroom", "kitchen"]);
const OUTDOOR_TYPES = new Set(["balcony"]);
const isOpenPair = (a, b) => (a === "living" && b === "kitchen") || (a === "kitchen" && b === "living");
const SIDE_NORMAL = { N: [0, -1], S: [0, 1], W: [-1, 0], E: [1, 0] };
const OPP = { N: "S", S: "N", E: "W", W: "E" };
const PRIVACY = { bathroom: 4, bedroom: 3, kitchen: 2, living: 1, balcony: 0 };
const DEFAULT_ASPECT = {
  living: 1.4, bedroom: 1.2, kitchen: 1.4, bathroom: 1.5, dining: 1.3, balcony: 2.2, study: 1.2,
  storage: 1.5, stair: 1.0, veranda: 2.0, garden: 1.5, parking: 2.0,
};

// lexicographic tuple compare + stable sort by key (Python sorted semantics)
function cmpKeys(a, b) {
  for (let i = 0; i < a.length; i++) { if (a[i] < b[i]) return -1; if (a[i] > b[i]) return 1; }
  return 0;
}
const sortBy = (arr, key) => arr.map((v, i) => [key(v), i, v]).sort((p, q) => cmpKeys(p[0], q[0]) || p[1] - q[1]).map((p) => p[2]);
const argmaxBy = (arr, f) => { let best = arr[0], bv = f(arr[0]); for (const x of arr) { const v = f(x); if (v > bv) { bv = v; best = x; } } return best; };
const rtype = (r) => r.room.room_type;

// ═══════════════════════════════════ schematicScale / packing ══════════
export function computeAreaScale(rooms, cw, ch, targetFill = 0.25) {
  if (!rooms.length) return 1;
  const total = pySum(rooms.map((r) => Math.max(1, Number(r.area || 0))));
  return total > 0 ? Math.sqrt((cw * ch * targetFill) / total) : 1;
}

export function packCircles(nodes, { w = 300, h = 300, pad = 4, iters = 300, gap = 0.6, step = 0.55, ox = 0, oy = 0 } = {}) {
  const n = nodes.length;
  for (let it = 0; it < iters; it++) {
    for (let i = 0; i < n; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < n; j++) {
        const b = nodes[j];
        const dx = b.cx - a.cx, dy = b.cy - a.cy;
        const dist = Math.sqrt(dx * dx + dy * dy) + 1e-9, minDist = a.r + b.r + gap;
        if (dist >= minDist) continue;
        const overlap = minDist - dist, ux = dx / dist, uy = dy / dist;
        const ap = !!a.pinned, bp = !!b.pinned;
        if (ap && bp) continue;
        if (ap) { b.cx += ux * overlap * step; b.cy += uy * overlap * step; }
        else if (bp) { a.cx -= ux * overlap * step; a.cy -= uy * overlap * step; }
        else { const half = overlap * 0.5 * step; a.cx -= ux * half; a.cy -= uy * half; b.cx += ux * half; b.cy += uy * half; }
      }
    }
    for (const nd of nodes) {
      if (nd.pinned) continue;
      nd.cx = Math.max(ox + pad + nd.r, Math.min(ox + w - pad - nd.r, nd.cx));
      nd.cy = Math.max(oy + pad + nd.r, Math.min(oy + h - pad - nd.r, nd.cy));
    }
  }
  return nodes;
}

export function resolveAllOverlaps(nodes, w, h, pad, ox = 0, oy = 0, maxIters = 300) {
  const n = nodes.length;
  for (let it = 0; it < maxIters; it++) {
    let any = false;
    for (let i = 0; i < n; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < n; j++) {
        const b = nodes[j];
        const dx = b.cx - a.cx, dy = b.cy - a.cy;
        const ovx = (a.w + b.w) / 2 - Math.abs(dx), ovy = (a.h + b.h) / 2 - Math.abs(dy);
        if (ovx <= 0.01 || ovy <= 0.01) continue;
        any = true;
        let px = 0, py = 0;
        if (ovx < ovy) px = dx >= 0 ? ovx : -ovx; else py = dy >= 0 ? ovy : -ovy;
        a.cx -= px * 0.5; a.cy -= py * 0.5; b.cx += px * 0.5; b.cy += py * 0.5;
      }
    }
    for (const nd of nodes) {
      nd.cx = Math.max(ox + pad + nd.w / 2, Math.min(ox + w - pad - nd.w / 2, nd.cx));
      nd.cy = Math.max(oy + pad + nd.h / 2, Math.min(oy + h - pad - nd.h / 2, nd.cy));
    }
    if (!any) return;
  }
}

export function packRectanglesWithConnectivity(nodes, edges, { w = 300, h = 300, pad = 4, cycles = 30, centroidPull = 0.05, springK = 0.3, ox = 0, oy = 0 } = {}) {
  const byId = new Map(nodes.map((n) => [n.room_id, n]));
  const pairs = [];
  for (const e of edges || []) {
    const a = byId.get(e.source), b = byId.get(e.target);
    if (a && b && a !== b) pairs.push([a, b]);
  }
  resolveAllOverlaps(nodes, w, h, pad, ox, oy);
  const nN = nodes.length;
  if (!nN) return nodes;
  for (let c = 0; c < cycles; c++) {
    const sx = pySum(nodes.map((n) => n.cx)) / nN, sy = pySum(nodes.map((n) => n.cy)) / nN;
    for (const n of nodes) { n.cx += (sx - n.cx) * centroidPull; n.cy += (sy - n.cy) * centroidPull; }
    for (const [a, b] of pairs) {
      const dx = b.cx - a.cx, dy = b.cy - a.cy, dist = Math.sqrt(dx * dx + dy * dy) + 1e-9;
      const ux = dx / dist, uy = dy / dist;
      const target = Math.abs(ux) * (a.w + b.w) / 2 + Math.abs(uy) * (a.h + b.h) / 2;
      const diff = dist - target;
      if (diff > 0) { const f = springK * diff; a.cx += ux * f; a.cy += uy * f; b.cx -= ux * f; b.cy -= uy * f; }
    }
    resolveAllOverlaps(nodes, w, h, pad, ox, oy);
  }
  return nodes;
}

export function alignGroup(nodes, alignment, { w = 300, h = 300, pad = 4, ox = 0, oy = 0 } = {}) {
  if (!nodes.length) return nodes;
  const hw = (n) => ("w" in n ? n.w / 2 : Number(n.r || 0)), hh = (n) => ("h" in n ? n.h / 2 : Number(n.r || 0));
  const minX = Math.min(...nodes.map((n) => n.cx - hw(n))), maxX = Math.max(...nodes.map((n) => n.cx + hw(n)));
  const minY = Math.min(...nodes.map((n) => n.cy - hh(n))), maxY = Math.max(...nodes.map((n) => n.cy + hh(n)));
  let tx = ox + w / 2, ty = oy + h / 2;
  if (alignment === "N") ty = oy + pad + (maxY - minY) / 2;
  else if (alignment === "S") ty = oy + h - pad - (maxY - minY) / 2;
  else if (alignment === "E") tx = ox + w - pad - (maxX - minX) / 2;
  else if (alignment === "W") tx = ox + pad + (maxX - minX) / 2;
  const sx = tx - (minX + maxX) / 2, sy = ty - (minY + maxY) / 2;
  for (const n of nodes) { n.cx += sx; n.cy += sy; }
  return nodes;
}

// ═══════════════════════════════════ schematic records + Pareto ════════
export function makeSchematic(table, graph, aspectRatios = null) {
  const lookup = { ...DEFAULT_ASPECT };
  if (aspectRatios) for (const [k, v] of Object.entries(aspectRatios)) if (v && Number(v) > 0) lookup[k] = Number(v);
  const rooms = (table.rooms || []).map((r) => ({
    ...r,
    aspect_ratio: r.w_m && r.h_m ? Number(r.w_m) / Number(r.h_m) : lookup[r.room_type] ?? 1.2,
  }));
  return {
    plan_index: Number(table.table_index ?? 0), rooms, edges: graph ? [...(graph.edges_ordered || [])] : [],
    source_connectivity: graph || null, computed: { ...(table.computed || {}) },
  };
}

export function makeSchematics(tables, graphs, selectedIdxs, inputs) {
  if (!tables.length || !selectedIdxs.length) return [];
  const byT = new Map(tables.map((t) => [Number(t.table_index), t]));
  const byG = new Map((graphs || []).map((g) => [Number(g.meta.plan_index), g]));
  const out = [];
  for (const idx of selectedIdxs) {
    const t = byT.get(Number(idx));
    if (!t) continue;
    const s = makeSchematic(t, byG.get(Number(idx)), inputs.aspect_ratios);
    for (const k of ["targets", "reachable", "capacity"]) if (k in t) s[k] = t[k];
    out.push(s);
  }
  return out;
}

/** plan_index values on the Pareto-dominant frontier of (QVCI, AQI), sorted by QVCI. */
export function paretoFrontIndices(items) {
  const pts = items.map((s) => [Number(s.plan_index), Number(s.computed?.qvci ?? 0), Number(s.computed?.aqi ?? 0)]);
  const out = [];
  pts.forEach(([idx, q, a], i) => {
    const dominated = pts.some(([, qj, aj], j) => j !== i && qj >= q && aj >= a && (qj > q || aj > a));
    if (!dominated) out.push([idx, q, a]);
  });
  return sortBy(out, (p) => [p[1]]).map((p) => p[0]);
}

// ═══════════════════════════════════ geometry helpers ══════════════════
const bounds = (r) => [r.cx - r.w / 2, r.cy - r.h / 2, r.cx + r.w / 2, r.cy + r.h / 2];
const overlap1d = (a0, a1, b0, b1) => { const lo = Math.max(a0, b0), hi = Math.min(a1, b1); return hi - lo > EPS ? [lo, hi] : null; };
const rectHits = (a, b, tol = 0) => !(a[2] <= b[0] + tol || b[2] <= a[0] + tol || a[3] <= b[1] + tol || b[3] <= a[1] + tol);
export { bounds };

function slideLimit(r, others, axis, dir, tol) {
  const [x0, y0, x1, y1] = bounds(r);
  let best = Infinity;
  for (const o of others) {
    const [ox0, oy0, ox1, oy1] = bounds(o);
    let d;
    if (axis === "x") { if (!overlap1d(y0, y1, oy0, oy1)) continue; d = dir > 0 ? ox0 - x1 : x0 - ox1; }
    else { if (!overlap1d(x0, x1, ox0, ox1)) continue; d = dir > 0 ? oy0 - y1 : y0 - oy1; }
    if (d >= -tol) best = Math.min(best, Math.max(0, d));
  }
  return best;
}

export function compactRectangles(rects, passes = 8, tol = 0.5, record = null) {
  if (!rects.length) return;
  const xs = rects.flatMap((r) => [bounds(r)[0], bounds(r)[2]]), ys = rects.flatMap((r) => [bounds(r)[1], bounds(r)[3]]);
  const tcx = (Math.min(...xs) + Math.max(...xs)) / 2, tcy = (Math.min(...ys) + Math.max(...ys)) / 2;
  for (let p = 0; p < passes; p++) {
    let moved = 0;
    const order = sortBy(rects, (r) => [Math.abs(r.cx - tcx) + Math.abs(r.cy - tcy)]);
    for (const r of order) {
      const others = rects.filter((o) => o !== r);
      for (const axis of ["x", "y"]) {
        const c = axis === "x" ? r.cx : r.cy, t = axis === "x" ? tcx : tcy;
        if (Math.abs(t - c) < EPS) continue;
        const dir = t > c ? 1 : -1;
        const step = Math.min(slideLimit(r, others, axis, dir, tol), Math.abs(t - c));
        if (step > EPS) { if (axis === "x") r.cx += dir * step; else r.cy += dir * step; moved += step; }
      }
    }
    if (record) record();
    if (moved < tol) break;
  }
}

export function contactPass(rects, edges, maxGap, tol, record = null) {
  const byId = new Map(rects.map((r) => [r.room_id, r]));
  let movedAny = false;
  for (const e of edges) {
    const a = byId.get(e.source), b = byId.get(e.target);
    if (!a || !b) continue;
    const [mover, anchor] = a.w * a.h <= b.w * b.h ? [a, b] : [b, a];
    const [ax0, ay0, ax1, ay1] = bounds(anchor), [mx0, my0, mx1, my1] = bounds(mover);
    const gx = Math.max(ax0 - mx1, mx0 - ax1), gy = Math.max(ay0 - my1, my0 - ay1);
    const others = rects.filter((o) => o !== mover);
    if (gx <= tol && gy <= tol) continue;
    if (gx > tol && gy <= tol && gx <= maxGap) {
      const dir = ax0 >= mx1 ? 1 : -1;
      if (slideLimit(mover, others, "x", dir, tol) >= gx - tol) { mover.cx += dir * gx; movedAny = true; }
    } else if (gy > tol && gx <= tol && gy <= maxGap) {
      const dir = ay0 >= my1 ? 1 : -1;
      if (slideLimit(mover, others, "y", dir, tol) >= gy - tol) { mover.cy += dir * gy; movedAny = true; }
    }
  }
  if (record && movedAny) record();
}

export function glueBalconies(rects, facades, tol) {
  const byId = new Map(rects.map((r) => [r.room_id, r]));
  const indoor = rects.filter((r) => !OUTDOOR_TYPES.has(rtype(r)));
  if (!indoor.length) return;
  const gcx = pySum(indoor.map((r) => r.cx)) / indoor.length, gcy = pySum(indoor.map((r) => r.cy)) / indoor.length;
  const placed = [];
  for (const b of rects) {
    if (!OUTDOOR_TYPES.has(rtype(b))) continue;
    const [aid, side0] = facades.get(b.room_id) || [null, null];
    const a = byId.get(aid);
    if (!a || !["N", "S", "E", "W"].includes(side0)) { placed.push(b); continue; }
    const [ax0, ay0, ax1, ay1] = bounds(a);
    const obstacles = [...indoor.filter((r) => r !== a), ...placed];
    const long_ = Math.max(b.w, b.h), short_ = Math.min(b.w, b.h);
    const dx = a.cx - gcx, dy = a.cy - gcy;
    const outward = { N: -dy, S: dy, W: -dx, E: dx };
    const sides = [side0, ...sortBy(["N", "S", "E", "W"].filter((sd) => sd !== side0), (sd) => [-outward[sd]])];
    const candidates = (side) => {
      const out = [];
      const horizontal = side === "N" || side === "S";
      const [bw, bh] = horizontal ? [long_, short_] : [short_, long_];
      if (horizontal) {
        const y0 = side === "N" ? ay0 - bh : ay1;
        const ends = sortBy([["W", ax0], ["E", ax1 - bw]], (e) => [-Math.abs(e[1] + bw / 2 - gcx)]);
        const xs = ends.map((e) => e[1]);
        const span = ax1 - ax0 - bw;
        if (span > 0) xs.push(...[0.25, 0.5, 0.75].map((f) => ax0 + span * f));
        for (const x0 of xs) out.push([[x0, y0, x0 + bw, y0 + bh], bw, bh]);
      } else {
        const x0 = side === "W" ? ax0 - bw : ax1;
        const ends = sortBy([["N", ay0], ["S", ay1 - bh]], (e) => [-Math.abs(e[1] + bh / 2 - gcy)]);
        const ysA = ends.map((e) => e[1]);
        const span = ay1 - ay0 - bh;
        if (span > 0) ysA.push(...[0.25, 0.5, 0.75].map((f) => ay0 + span * f));
        for (const y0 of ysA) out.push([[x0, y0, x0 + bw, y0 + bh], bw, bh]);
      }
      return out;
    };
    const pushNeeded = (c, side) => {
      const [nx, ny] = SIDE_NORMAL[side], step = Math.max(tol, short_ * 0.1);
      for (let d = 0; d <= long_ * 3; d += step) {
        const cc = [c[0] + nx * d, c[1] + ny * d, c[2] + nx * d, c[3] + ny * d];
        if (!obstacles.some((o) => rectHits(cc, bounds(o), tol))) return d;
      }
      return Infinity;
    };
    let best = null;                                     // [key, d, c, bw, bh, side]
    for (let rank = 0; rank < sides.length; rank++) {
      const side = sides[rank], horizontal = side === "N" || side === "S";
      for (const [c, bw, bh] of candidates(side)) {
        const touch = horizontal ? overlap1d(c[0], c[2], ax0, ax1) : overlap1d(c[1], c[3], ay0, ay1);
        if (!touch || touch[1] - touch[0] < Math.min(long_, horizontal ? ax1 - ax0 : ay1 - ay0) * 0.5) continue;
        const d = pushNeeded(c, side);
        if (d === Infinity) continue;
        const key = [d <= tol ? 0 : 1, d <= tol ? rank : 0, d, rank];
        if (!best || cmpKeys(key, best[0]) < 0) best = [key, d, c, bw, bh, side];
        if (d <= tol && rank === 0) break;
      }
      if (best && best[1] <= tol && best[5] === side0) break;
    }
    let c, bw, bh, side, d;
    if (!best) { [c, bw, bh] = candidates(side0)[0]; side = side0; d = 0; }
    else [, d, c, bw, bh, side] = best;
    const [nx, ny] = SIDE_NORMAL[side];
    c = [c[0] + nx * d, c[1] + ny * d, c[2] + nx * d, c[3] + ny * d];
    b.cx = (c[0] + c[2]) / 2; b.cy = (c[1] + c[3]) / 2; b.w = bw; b.h = bh;
    placed.push(b);
  }
}

export function detachedBalconies(rects, facades, metre, tol) {
  const byId = new Map(rects.map((r) => [r.room_id, r]));
  const need = (DOOR_W_M + 2 * DOOR_OFF_M) * metre;
  const out = new Set();
  for (const b of rects) {
    if (!OUTDOOR_TYPES.has(rtype(b))) continue;
    const [aid] = facades.get(b.room_id) || [null, null];
    const a = byId.get(aid);
    if (!a) continue;
    const [ax0, ay0, ax1, ay1] = bounds(a), [bx0, by0, bx1, by1] = bounds(b);
    let touching = false;
    if (Math.abs(by1 - ay0) <= tol || Math.abs(by0 - ay1) <= tol) { const ov = overlap1d(ax0, ax1, bx0, bx1); touching = !!ov && ov[1] - ov[0] >= need; }
    if (!touching && (Math.abs(bx1 - ax0) <= tol || Math.abs(bx0 - ax1) <= tol)) { const ov = overlap1d(ay0, ay1, by0, by1); touching = !!ov && ov[1] - ov[0] >= need; }
    if (!touching) out.add(b.room_id);
  }
  return out;
}

// ═══════════════════════════════════ walls ═════════════════════════════
function sideSegment(r, side) {
  const [x0, y0, x1, y1] = bounds(r);
  return { N: [x0, y0, x1, y0], S: [x0, y1, x1, y1], W: [x0, y0, x0, y1], E: [x1, y0, x1, y1] }[side];
}

export function buildWalls(rects, tol) {
  const walls = [];
  for (const a of rects) {
    for (const side of ["N", "S", "E", "W"]) {
      const [ax1, ay1, ax2, ay2] = sideSegment(a, side);
      const horizontal = side === "N" || side === "S";
      const [lo, hi] = horizontal ? [ax1, ax2] : [ay1, ay2];
      const cst = horizontal ? ay1 : ax1;
      const shared = [];
      for (const b of rects) {
        if (b === a) continue;
        const [bx1, by1, bx2, by2] = sideSegment(b, OPP[side]);
        if (Math.abs((horizontal ? by1 : bx1) - cst) > tol) continue;
        const ov = overlap1d(lo, hi, ...(horizontal ? [bx1, bx2] : [by1, by2]));
        if (ov) shared.push([ov[0], ov[1], b]);
      }
      const sh = sortBy(shared, (s) => [s[0]]);
      let cursor = lo;
      const pieces = [];
      for (const [s0, s1, b] of sh) {
        if (s0 - cursor > tol) pieces.push([cursor, s0, null]);
        pieces.push([Math.max(s0, cursor), s1, b]);
        cursor = Math.max(cursor, s1);
      }
      if (hi - cursor > tol) pieces.push([cursor, hi, null]);
      for (const [p0, p1, b] of pieces) {
        if (p1 - p0 <= tol) continue;
        const ta = rtype(a), tb = b ? rtype(b) : null;
        if (b && a.room_id > b.room_id && ta !== "balcony" && tb !== "balcony") continue;   // emitted from the other room
        const seg = horizontal ? [p0, cst, p1, cst] : [cst, p0, cst, p1];
        let kind = b ? "int" : "ext";
        if (b && (ta === "balcony" || tb === "balcony")) {
          kind = "ext";
          if (ta === "balcony") continue;                          // the indoor room emits the façade
        } else if (!b && ta === "balcony") kind = "rail";
        walls.push({ x1: seg[0], y1: seg[1], x2: seg[2], y2: seg[3], kind, rooms: [a.room_id, b ? b.room_id : null], side, out: SIDE_NORMAL[side], types: [ta, tb] });
      }
    }
  }
  return walls;
}

const segLen = (w) => pyHypot(w.x2 - w.x1, w.y2 - w.y1);
const subSegment = (w, t0, t1) => [w.x1 + (w.x2 - w.x1) * t0, w.y1 + (w.y2 - w.y1) * t0, w.x1 + (w.x2 - w.x1) * t1, w.y1 + (w.y2 - w.y1) * t1];

function doorOn(w, metre, intoRoom, toward, kind = "door") {
  const L = segLen(w), dw = DOOR_W_M * metre;
  let off = DOOR_OFF_M * metre;
  if (L < dw + 0.05 * metre) return null;
  if (L < dw + 2 * off) off = (L - dw) / 2;
  const d1 = pyHypot(w.x1 - toward[0], w.y1 - toward[1]), d2 = pyHypot(w.x2 - toward[0], w.y2 - toward[1]);
  let t0, t1, hingeT;
  if (d1 <= d2) { t0 = off / L; t1 = (off + dw) / L; hingeT = t0; }
  else { t0 = (L - off - dw) / L; t1 = (L - off) / L; hingeT = t1; }
  const [x1, y1, x2, y2] = subSegment(w, t0, t1), [hx, hy] = subSegment(w, hingeT, hingeT);
  return { kind, x1, y1, x2, y2, hinge: [hx, hy], into: intoRoom, wall: w, width: dw };
}

export function buildOpenings(rects, walls, edges, metre, wwrByRoom) {
  const byId = new Map(rects.map((r) => [r.room_id, r]));
  const openings = [], used = new Map();
  const gx = pySum(rects.map((r) => r.cx)) / Math.max(1, rects.length), gy = pySum(rects.map((r) => r.cy)) / Math.max(1, rects.length);
  const living = rects.find((r) => rtype(r) === "living") || null;
  const hub = living ? [living.cx, living.cy] : [gx, gy];
  const sharedWalls = (a, b) => walls.filter((w) => (w.rooms[0] === a && w.rooms[1] === b) || (w.rooms[0] === b && w.rooms[1] === a));
  const occupy = (w, o) => {
    const L = segLen(w) || 1;
    const t0 = pyHypot(o.x1 - w.x1, o.y1 - w.y1) / L, t1 = pyHypot(o.x2 - w.x1, o.y2 - w.y1) / L;
    if (!used.has(w)) used.set(w, []);
    used.get(w).push([Math.min(t0, t1), Math.max(t0, t1)]);
  };
  let entrance = null;
  for (const e of edges) {
    const a = e.source, b = e.target;
    if (a == null || b == null) continue;
    if (String(a).startsWith("outside") || String(b).startsWith("outside")) { entrance = String(a).startsWith("outside") ? b : a; continue; }
    if (!byId.has(a) || !byId.has(b)) continue;
    const ws = sharedWalls(a, b);
    if (!ws.length) continue;
    const w = argmaxBy(ws, segLen);
    const ta = rtype(byId.get(a)), tb = rtype(byId.get(b));
    if (isOpenPair(ta, tb)) {
      for (const ww of ws) {
        openings.push({ kind: "open", x1: ww.x1, y1: ww.y1, x2: ww.x2, y2: ww.y2, wall: ww, rooms: [a, b] });
        if (!used.has(ww)) used.set(ww, []);
        used.get(ww).push([0, 1]);
      }
      continue;
    }
    let into = (PRIVACY[ta] ?? 1) >= (PRIVACY[tb] ?? 1) ? a : b;
    const other = into === a ? b : a;
    const kind = ta === "balcony" || tb === "balcony" ? "balcony_door" : "door";
    if (kind === "balcony_door") into = ta !== "balcony" ? a : b;
    const oc = byId.get(other);
    const d = doorOn(w, metre, into, kind !== "balcony_door" ? [oc.cx, oc.cy] : hub, kind);
    if (d) { d.rooms = [a, b]; openings.push(d); occupy(w, d); }
  }
  if (entrance == null && living) entrance = living.room_id;
  if (byId.has(entrance)) {
    const cands = walls.filter((w) => w.kind === "ext" && w.rooms[0] === entrance && w.rooms[1] == null);
    cands.sort((p, q) => segLen(q) - segLen(p));
    for (const w of cands) {
      const d = doorOn(w, metre, entrance, hub, "entry");
      if (d) { d.rooms = [entrance, null]; openings.push(d); occupy(w, d); break; }
    }
  }
  for (const w of walls) {
    if (w.kind !== "ext" || w.rooms[1] != null) continue;
    const rid = w.rooms[0];
    if (rtype(byId.get(rid)) === "balcony") continue;
    const wwr = Number(wwrByRoom[rid] || 0);
    if (wwr <= 0) continue;
    const L = segLen(w), Lm = L / metre;
    if (Lm < 1.2) continue;
    const occ = sortBy(used.get(w) || [], (o) => o);
    const free = [];
    let cur = 0;
    for (const [o0, o1] of occ) { if (o0 - cur > 0) free.push([cur, o0]); cur = Math.max(cur, o1); }
    if (1 - cur > 0) free.push([cur, 1]);
    if (!free.length) continue;
    const [f0, f1] = argmaxBy(free, (f) => f[1] - f[0]);
    const freeM = (f1 - f0) * Lm;
    const winM = Math.min(WIN_FRAC_PER_WWR * wwr * Lm, WIN_MAX_FRAC * freeM);
    if (winM < WIN_MIN_M || freeM < WIN_MIN_M + 0.3) continue;
    const mid = (f0 + f1) / 2, half = winM / Lm / 2;
    const [x1, y1, x2, y2] = subSegment(w, mid - half, mid + half);
    openings.push({ kind: "window", x1, y1, x2, y2, wall: w, rooms: [rid, null], wwr });
  }
  return openings;
}

// ═══════════════════════════════════ furniture ═════════════════════════
const innerRect = (r, metre, inset = 0.08) => { const [x0, y0, x1, y1] = bounds(r), d = inset * metre; return [x0 + d, y0 + d, x1 - d, y1 - d]; };

function placeAgainst(inner, side, w, d, at) {
  const [ix0, iy0, ix1, iy1] = inner;
  if (side === "N") { const x = ix0 + (ix1 - ix0 - w) * at; return [x, iy0, x + w, iy0 + d]; }
  if (side === "S") { const x = ix0 + (ix1 - ix0 - w) * at; return [x, iy1 - d, x + w, iy1]; }
  if (side === "W") { const y = iy0 + (iy1 - iy0 - w) * at; return [ix0, y, ix0 + d, y + w]; }
  const y = iy0 + (iy1 - iy0 - w) * at;
  return [ix1 - d, y, ix1, y + w];
}

function fits(box, inner, blocked, placed) {
  if (box[0] < inner[0] - EPS || box[1] < inner[1] - EPS || box[2] > inner[2] + EPS || box[3] > inner[3] + EPS) return false;
  return !blocked.some((b) => rectHits(box, b)) && !placed.some((p) => rectHits(box, p));
}

function tryPlace(inner, sides, w, d, blocked, placed, ats = [0.5, 0.0, 1.0, 0.25, 0.75]) {
  for (const side of sides) for (const at of ats) {
    const box = placeAgainst(inner, side, w, d, at);
    if (fits(box, inner, blocked, placed)) return [side, box];
  }
  return [null, null];
}

function doorSides(r, openings, metre) {
  const rid = r.room_id, [x0, y0, x1, y1] = bounds(r), sides = [], blocked = [], depth = DOOR_CLEAR_M * metre;
  for (const o of openings) {
    if (o.kind === "window" || !(o.rooms || []).includes(rid)) continue;
    let side;
    if (Math.abs(o.y1 - o.y2) < EPS) {
      side = Math.abs(o.y1 - y0) < Math.abs(o.y1 - y1) ? "N" : "S";
      let lo = Math.min(o.x1, o.x2), hi = Math.max(o.x1, o.x2);
      if (o.kind !== "open") { lo -= depth * 0.5; hi += depth * 0.5; }
      blocked.push(side === "N" ? [lo, y0, hi, y0 + depth] : [lo, y1 - depth, hi, y1]);
    } else {
      side = Math.abs(o.x1 - x0) < Math.abs(o.x1 - x1) ? "W" : "E";
      let lo = Math.min(o.y1, o.y2), hi = Math.max(o.y1, o.y2);
      if (o.kind !== "open") { lo -= depth * 0.5; hi += depth * 0.5; }
      blocked.push(side === "W" ? [x0, lo, x0 + depth, hi] : [x1 - depth, lo, x1, hi]);
    }
    sides.push(side);
  }
  return [sides, blocked];
}

function orderedSides(prefOpp) {
  const order = ["S", "N", "W", "E"];
  const first = prefOpp.filter((s) => s in OPP).map((s) => OPP[s]);
  const rest = order.filter((s) => !first.includes(s) && !prefOpp.includes(s));
  return [...first, ...rest, ...prefOpp.filter((s) => order.includes(s))];
}

function nudgeBeside(box, side, w, d, at) {
  const [x0, y0, x1, y1] = box;
  if (side === "N" || side === "S") { const x = at === 0 ? x0 - w : x1, y = side === "N" ? y0 : y1 - d; return [x, y, x + w, y + d]; }
  const y = at === 0 ? y0 - w : y1, x = side === "W" ? x0 : x1 - d;
  return [x, y, x + d, y + w];
}

function inFront(box, side, w, d, gap) {
  const [x0, y0, x1, y1] = box, cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  if (side === "N") return [cx - w / 2, y1 + gap, cx + w / 2, y1 + gap + d];
  if (side === "S") return [cx - w / 2, y0 - gap - d, cx + w / 2, y0 - gap];
  if (side === "W") return [x1 + gap, cy - w / 2, x1 + gap + d, cy + w / 2];
  return [x0 - gap - d, cy - w / 2, x0 - gap, cy + w / 2];
}

function drawPiece(name, side, box, m) {
  const [x0, y0, x1, y1] = box, w = x1 - x0, h = y1 - y0;
  let P = [["rect", x0, y0, w, h]];
  const horiz = side === "N" || side === "S";
  if (name === "bed") {
    if (side === "N") P.push(["rect", x0 + 0.1 * m, y0 + 0.1 * m, w / 2 - 0.15 * m, 0.4 * m], ["rect", x0 + w / 2 + 0.05 * m, y0 + 0.1 * m, w / 2 - 0.15 * m, 0.4 * m], ["line", x0, y0 + 0.75 * m, x1, y0 + 0.75 * m]);
    else if (side === "S") P.push(["rect", x0 + 0.1 * m, y1 - 0.5 * m, w / 2 - 0.15 * m, 0.4 * m], ["rect", x0 + w / 2 + 0.05 * m, y1 - 0.5 * m, w / 2 - 0.15 * m, 0.4 * m], ["line", x0, y1 - 0.75 * m, x1, y1 - 0.75 * m]);
    else if (side === "W") P.push(["rect", x0 + 0.1 * m, y0 + 0.1 * m, 0.4 * m, h / 2 - 0.15 * m], ["rect", x0 + 0.1 * m, y0 + h / 2 + 0.05 * m, 0.4 * m, h / 2 - 0.15 * m], ["line", x0 + 0.75 * m, y0, x0 + 0.75 * m, y1]);
    else P.push(["rect", x1 - 0.5 * m, y0 + 0.1 * m, 0.4 * m, h / 2 - 0.15 * m], ["rect", x1 - 0.5 * m, y0 + h / 2 + 0.05 * m, 0.4 * m, h / 2 - 0.15 * m], ["line", x1 - 0.75 * m, y0, x1 - 0.75 * m, y1]);
    if (w < 1.3 * m || h < 1.3 * m) P = [P[0], P[1], P[3]];
  } else if (name === "sofa") {
    const back = 0.25 * m;
    if (horiz) {
      const by = side === "N" ? y0 : y1 - back;
      P.push(["rect", x0, by, w, back], ["line", x0 + w / 2, y0 + (side === "N" ? back : 0), x0 + w / 2, y1 - (side === "N" ? 0 : back)]);
    } else {
      const bx = side === "W" ? x0 : x1 - back;
      P.push(["rect", bx, y0, back, h], ["line", x0 + (side === "W" ? back : 0), y0 + h / 2, x1 - (side === "W" ? 0 : back), y0 + h / 2]);
    }
  } else if (name === "dining") {
    const [tw, td] = w >= h ? [1.6 * m, 0.9 * m] : [0.9 * m, 1.6 * m];
    const tx = x0 + (w - tw) / 2, ty = y0 + (h - td) / 2, c = 0.42 * m;
    P = [["rect", tx, ty, tw, td]];
    for (let i = 0; i < 2; i++) {
      if (tw >= td) { const cx = tx + tw * (0.25 + 0.5 * i) - c / 2; P.push(["rect", cx, ty - c - 0.05 * m, c, c], ["rect", cx, ty + td + 0.05 * m, c, c]); }
      else { const cy = ty + td * (0.25 + 0.5 * i) - c / 2; P.push(["rect", tx - c - 0.05 * m, cy, c, c], ["rect", tx + tw + 0.05 * m, cy, c, c]); }
    }
  } else if (name === "counter") {
    if (horiz) {
      const sw = 0.5 * m, sd = 0.4 * m, hx = x0 + w * 0.72;
      P.push(["rect", x0 + w * 0.2 - sw / 2, y0 + (h - sd) / 2, sw, sd]);
      for (const dx of [-0.12, 0.12]) for (const dy of [-0.12, 0.12]) P.push(["circle", hx + dx * m, y0 + h / 2 + dy * m, 0.09 * m]);
    } else {
      const sw = 0.4 * m, sd = 0.5 * m, hy = y0 + h * 0.72;
      P.push(["rect", x0 + (w - sw) / 2, y0 + h * 0.2 - sd / 2, sw, sd]);
      for (const dx of [-0.12, 0.12]) for (const dy of [-0.12, 0.12]) P.push(["circle", x0 + w / 2 + dx * m, hy + dy * m, 0.09 * m]);
    }
  } else if (name === "fridge") P.push(["line", x0, y0, x1, y1], ["line", x0, y1, x1, y0]);
  else if (name === "wc") {
    if (horiz) {
      const cy = y0 + (side === "N" ? h * 0.35 : h * 0.65);
      P = [["rect", x0 + w * 0.15, side === "N" ? y0 : y1 - 0.25 * m, w * 0.7, 0.25 * m], ["circle", x0 + w / 2, cy + (side === "N" ? 0.1 * m : -0.1 * m), w * 0.42]];
    } else {
      const cx = x0 + (side === "W" ? w * 0.35 : w * 0.65);
      P = [["rect", side === "W" ? x0 : x1 - 0.25 * m, y0 + h * 0.15, 0.25 * m, h * 0.7], ["circle", cx + (side === "W" ? 0.1 * m : -0.1 * m), y0 + h / 2, h * 0.42]];
    }
  } else if (name === "basin") P.push(["circle", x0 + w / 2, y0 + h / 2, Math.min(w, h) * 0.32]);
  else if (name === "shower") P.push(["line", x0, y0, x1, y1], ["circle", x0 + w / 2, y0 + h / 2, 0.06 * m]);
  else if (name === "bathtub") P.push(["rect", x0 + 0.08 * m, y0 + 0.08 * m, w - 0.16 * m, h - 0.16 * m]);
  else if (name === "wardrobe") P.push(horiz ? ["line", x0, y0 + h / 2, x1, y0 + h / 2] : ["line", x0 + w / 2, y0, x0 + w / 2, y1]);
  else if (name === "bistro") P = [["circle", x0 + w / 2, y0 + h / 2, 0.25 * m], ["rect", x0 - 0.35 * m, y0 + h / 2 - 0.18 * m, 0.3 * m, 0.36 * m], ["rect", x1 + 0.05 * m, y0 + h / 2 - 0.18 * m, 0.3 * m, 0.36 * m]];
  return P;
}

export function furnishRoom(r, openings, metre) {
  const rt = rtype(r), inner = innerRect(r, metre);
  const iw = inner[2] - inner[0], ih = inner[3] - inner[1], m = metre;
  if (iw < 1.2 * m || ih < 1.2 * m) return [];
  const [ds, blocked] = doorSides(r, openings, metre);
  const sides = orderedSides(ds);
  const placed = [], pieces = [];
  const add = (name, side, box) => { placed.push(box); pieces.push({ name, side, box, prims: drawPiece(name, side, box, m) }); };
  if (rt === "bedroom") {
    const bw = Math.min(iw, ih) >= 2.8 * m ? 1.6 : 1.0;
    const [side, box] = tryPlace(inner, sides, bw * m, 2.0 * m, blocked, placed);
    if (box) {
      add("bed", side, box);
      for (const at of [0.0, 1.0]) { const nb = nudgeBeside(box, side, 0.5 * m, 0.5 * m, at); if (fits(nb, inner, blocked, placed)) add("nightstand", side, nb); }
    }
    const [s2, b2] = tryPlace(inner, sides.filter((s) => s !== side), Math.min(1.8 * m, Math.max(iw, ih) * 0.5), 0.6 * m, blocked, placed);
    if (b2) add("wardrobe", s2, b2);
  } else if (rt === "living") {
    const [side, box] = tryPlace(inner, sides, 2.2 * m, 0.9 * m, blocked, placed);
    if (box) {
      add("sofa", side, box);
      const ct = inFront(box, side, 1.2 * m, 0.6 * m, 0.45 * m);
      if (fits(ct, inner, blocked, placed)) add("coffee_table", side, ct);
      const [s3, tv] = tryPlace(inner, [OPP[side]], 1.6 * m, 0.45 * m, blocked, placed);
      if (tv) add("tv", s3, tv);
    }
    if ((iw * ih) / (m * m) >= 16.0) {
      const [s4, dt] = tryPlace(inner, sides, 1.6 * m + 1.0 * m, 0.9 * m + 1.0 * m, blocked, placed, [0.0, 1.0, 0.5]);
      if (dt) add("dining", s4, dt);
    }
  } else if (rt === "kitchen") {
    const longSides = iw >= ih ? ["N", "S"] : ["W", "E"];
    const pref = [...longSides.filter((s) => !ds.includes(s)), ...sides.filter((s) => !longSides.includes(s))];
    const length = (iw >= ih ? iw : ih) - 0.1 * m;
    let [side, box] = tryPlace(inner, pref, length, 0.6 * m, blocked, placed, [0.5]);
    if (!box) [side, box] = tryPlace(inner, pref, length * 0.6, 0.6 * m, blocked, placed);
    if (box) {
      add("counter", side, box);
      const [s5, fr] = tryPlace(inner, side === "N" || side === "S" ? ["W", "E"] : ["N", "S"], 0.7 * m, 0.7 * m, blocked, placed, [0.0, 1.0]);
      if (fr) add("fridge", s5, fr);
    }
  } else if (rt === "bathroom") {
    const [side, box] = tryPlace(inner, sides, 0.45 * m, 0.7 * m, blocked, placed, [0.0, 1.0, 0.5]);
    if (box) {
      add("wc", side, box);
      const bs = nudgeBeside(box, side, 0.6 * m, 0.45 * m, 1.0);
      if (fits(bs, inner, blocked, placed)) add("basin", side, bs);
      else { const [s6, b6] = tryPlace(inner, sides, 0.6 * m, 0.45 * m, blocked, placed); if (b6) add("basin", s6, b6); }
    }
    if (Math.max(iw, ih) >= 2.0 * m && (iw * ih) / (m * m) >= 4.5) {
      const [s7, tub] = tryPlace(inner, sides, 1.7 * m, 0.75 * m, blocked, placed, [0.0, 1.0, 0.5]);
      if (tub) add("bathtub", s7, tub);
      else { const [s8, shw] = tryPlace(inner, sides, 0.9 * m, 0.9 * m, blocked, placed, [0.0, 1.0]); if (shw) add("shower", s8, shw); }
    } else {
      const [s8, shw] = tryPlace(inner, sides, 0.9 * m, 0.9 * m, blocked, placed, [0.0, 1.0]);
      if (shw) add("shower", s8, shw);
    }
  } else if (rt === "balcony") {
    const [s9, tb] = tryPlace(inner, sides, 0.6 * m, 0.6 * m, blocked, placed, [0.5, 0.0, 1.0]);
    if (tb) add("bistro", s9, tb);
  }
  return pieces;
}

// ═══════════════════════════════════ synthesis ═════════════════════════
export function referenceBox(schematic) {
  const conn = schematic.source_connectivity || {};
  const pw = Number(conn.plan_width || 0), ph = Number(conn.plan_height || 0);
  if (pw > 0 && ph > 0) { const a = pw / ph; return a >= 1 ? [REFERENCE_BOX_SIZE, REFERENCE_BOX_SIZE / a] : [REFERENCE_BOX_SIZE * a, REFERENCE_BOX_SIZE]; }
  return [REFERENCE_BOX_SIZE, REFERENCE_BOX_SIZE];
}

const snapshot = (shapes, kind, morph = 1) => shapes.map((s) => {
  const d = { room_id: s.room_id, room: s.room, cx: s.cx, cy: s.cy, kind };
  if ("r" in s) d.r = s.r;
  if ("w" in s) { d.w = s.w; d.h = s.h; }
  d.morph = morph;
  return d;
});

/** Full synthesis for one plan; frames[frames.length - 1] is the finished plan. */
export function synthesize(schematic, alignment, boxW, boxH, unitsPerM = 1.0, wwrByType = null) {
  const syn = { metre: 1, box: [0, 0, 600, 600], rects: [], circles: [], walls: [], openings: [], furniture: [], removed: [], frames: [], phases: [] };
  const rooms = schematic.rooms || [];
  if (!rooms.length) return syn;
  const conn = schematic.source_connectivity || {};
  const s3size = Number(conn.canvas_size || 200);
  const s3 = new Map((conn.nodes || []).filter((n) => !n.is_direction).map((n) => [n.id, n]));
  let edges = [...(schematic.edges || [])];
  const facades = new Map((conn.nodes || []).filter((n) => n.facade && n.anchor).map((n) => [n.id, [n.anchor, n.facade]]));

  const areaScale = computeAreaScale(rooms, boxW, boxH, 0.40);
  const ratios = rooms.filter((r) => r.area_m2 && Number(r.area_m2) > 0 && Number(r.area || 0) > 0).map((r) => Math.sqrt(Number(r.area) / Number(r.area_m2)));
  if (ratios.length) unitsPerM = pySum(ratios) / ratios.length;
  const metre = areaScale * unitsPerM;
  syn.metre = metre; syn.box = [0, 0, boxW, boxH];
  const frames = [], phases = [];
  const push = (label, shapes, kind, { morph = 1, walls = 0, nOpen = 0, nFurn = 0, hold = 1 } = {}) => {
    const f = { label, shapes: snapshot(shapes, kind, morph), walls, openings: nOpen, furniture: nFurn };
    for (let k = 0; k < Math.max(1, hold); k++) frames.push(f);
  };
  const phase = (label) => phases.push([frames.length, label]);

  // 0. bubbles at the connectivity positions
  const circles = [];
  const rect = conn.plan_rect;
  for (const r of rooms) {
    const src = s3.get(r.room_id);
    let bx, by;
    if (src) {
      let u, v;
      if (rect && rect.length === 4 && rect[2] > 0 && rect[3] > 0) { u = (Number(src.x) - rect[0]) / rect[2]; v = (Number(src.y) - rect[1]) / rect[3]; }
      else { u = Number(src.x) / s3size; v = Number(src.y) / s3size; }
      bx = u * boxW; by = v * boxH;
    } else { bx = boxW / 2; by = boxH / 2; }
    circles.push({ room: r, room_id: r.room_id, cx: bx, cy: by, r: Math.sqrt(Math.max(1, Number(r.area || 0)) / Math.PI) * areaScale, pinned: PRIMARY_TYPES.has(r.room_type) });
  }
  phase("Rooms at their connectivity positions");
  push("Rooms at their connectivity positions", circles, "circle", { hold: 6 });

  // 1. bubble packing (300 iterations, recorded every 15)
  phase("Bubble packing, resolving overlaps");
  for (let k = 0; k < 20; k++) {
    packCircles(circles, { w: boxW, h: boxH, pad: 4, iters: 15, gap: 0.6, step: 0.55 });
    push("Bubble packing, resolving overlaps", circles, "circle");
  }
  syn.circles = circles.map((c) => ({ ...c }));

  // 2. circles to rectangles by aspect ratio
  let rects = circles.map((c) => {
    const ar = Math.max(0.3, Math.min(3.0, Number(c.room.aspect_ratio ?? 1.2)));
    const area = Math.PI * c.r * c.r, rh = Math.sqrt(area / ar), rw = ar * rh;
    return { room: c.room, room_id: c.room_id, cx: c.cx, cy: c.cy, w: rw, h: rh, r: c.r };
  });
  glueBalconies(rects, facades, 0.5);
  phase("Rectangles from area and aspect ratio");
  for (let k = 1; k <= 6; k++) push("Rectangles from area and aspect ratio", rects, "rect", { morph: k / 6 });

  let indoor = rects.filter((r) => !OUTDOOR_TYPES.has(rtype(r)));

  // 3. spring packing along the connectivity edges
  phase("Spring packing along connectivity edges");
  for (let k = 0; k < 30; k++) {
    packRectanglesWithConnectivity(indoor, edges, { w: boxW, h: boxH, pad: 4, cycles: 1, centroidPull: 0.05, springK: 0.3 });
    glueBalconies(rects, facades, 0.5);
    push("Spring packing along connectivity edges", rects, "rect");
  }

  // 4. compaction, contact, detached balconies
  const rec = (label) => () => { glueBalconies(rects, facades, 0.5); push(label, rects, "rect"); };
  phase("Compaction, rooms slide until they share walls");
  compactRectangles(indoor, 8, 0.5, rec("Compaction, rooms slide until they share walls"));
  resolveAllOverlaps(indoor, boxW, boxH, 4);
  phase("Contact, connected rooms close small gaps");
  contactPass(indoor, edges, 1.5 * metre, 0.5, rec("Contact, connected rooms close small gaps"));
  glueBalconies(rects, facades, 0.5);
  const removed = detachedBalconies(rects, facades, metre, 0.5);
  if (removed.size) {
    rects = rects.filter((r) => !removed.has(r.room_id));
    edges = edges.filter((e) => !removed.has(e.source) && !removed.has(e.target));
    indoor = rects.filter((r) => !OUTDOOR_TYPES.has(rtype(r)));
    phase("Detached balcony removed");
    push("Detached balcony removed: " + [...removed].sort().join(", "), rects, "rect", { hold: 6 });
  }
  syn.removed = [...removed].sort();

  // 5. alignment of the group in the plan box
  if (alignment !== "C") {
    const before = rects.map((r) => [r.cx, r.cy]);
    alignGroup(rects, alignment, { w: boxW, h: boxH, pad: 4 });
    const after = rects.map((r) => [r.cx, r.cy]);
    phase(`Align group to ${alignment}`);
    for (let k = 1; k <= 5; k++) {
      const t = k / 5;
      push(`Align group to ${alignment}`, rects.map((r, i) => ({ ...r, cx: before[i][0] + (after[i][0] - before[i][0]) * t, cy: before[i][1] + (after[i][1] - before[i][1]) * t })), "rect");
    }
  }
  syn.rects = rects;

  // 6. walls
  const tol = Math.max(0.75, 0.03 * metre);
  const walls = buildWalls(rects, tol);
  syn.walls = walls;
  phase("Walls, exterior then partitions");
  for (let k = 1; k <= 10; k++) push("Walls, exterior then partitions", rects, "rect", { walls: k / 10 });

  // 7. openings
  const wwrByRoom = {};
  for (const r of rooms) {
    let v = r.wwr;
    if (v == null && wwrByType) v = wwrByType[r.room_type] ?? 0;
    wwrByRoom[r.room_id] = Number(v || 0);
  }
  const openings = buildOpenings(rects, walls, edges, metre, wwrByRoom);
  const order = { open: 0, door: 1, balcony_door: 1, entry: 2, window: 3 };
  const sortedOpen = sortBy(openings, (o) => [order[o.kind] ?? 9]);
  syn.openings = sortedOpen;
  phase("Doors from connectivity, windows from WWR");
  for (let k = 1; k <= sortedOpen.length; k++) push("Doors from connectivity, windows from WWR", rects, "rect", { walls: 1, nOpen: k });

  // 8. furniture
  const furniture = [];
  for (const r of rects) for (const piece of furnishRoom(r, sortedOpen, metre)) { piece.room_id = r.room_id; furniture.push(piece); }
  syn.furniture = furniture;
  phase("Furniture blocks");
  for (let k = 1; k <= furniture.length; k++) push("Furniture blocks", rects, "rect", { walls: 1, nOpen: sortedOpen.length, nFurn: k });
  const finalLabel = "Schematic plan" + (syn.removed.length ? ` · removed ${syn.removed.join(", ")}` : "");
  push(finalLabel, rects, "rect", { walls: 1, nOpen: sortedOpen.length, nFurn: furniture.length, hold: 12 });
  syn.frames = frames; syn.phases = phases;
  return syn;
}
