// layout.js: a plan edited by hand (the plan editor of Stage 4).
// Rooms are rectangles in metres. Walls, doors, windows and furniture are
// rebuilt from them with the steps of the synthesis (schematic.js), and QVCI
// and AQI are measured on the plan as drawn: each room takes the zone of the
// façades it really touches (an exterior wall of at least EXPOSED_MIN_M on a
// side), then the journal equations of scoring.js apply as in Stage 2.
import { buildPlanParts, recordPlanParts, wwrForRooms, shapeSnapshot } from "./schematic.js";
import * as S from "./scoring.js";
import { siteFromInputs, rnd } from "./zones.js";

export const EXPOSED_MIN_M = 1.2;     // the shortest exterior wall that takes a window (as buildOpenings)
const ZONE_OF = { "N,W": 1, "N,": 2, "N,E": 3, ",W": 4, ",": 5, ",E": 6, "S,W": 7, "S,": 8, "S,E": 9 };

/** The finished synthesis as editable rooms in metres, the plan's top-left at (0, 0). */
export function layoutFromSynthesis(syn) {
  const M = syn.metre || 1, rects = syn.rects || [];
  if (!rects.length) return [];
  const x0 = Math.min(...rects.map((r) => r.cx - r.w / 2)), y0 = Math.min(...rects.map((r) => r.cy - r.h / 2));
  const q = (v) => Math.round(v * 1000) / 1000;
  return rects.map((r) => ({
    id: r.room_id, type: r.room.room_type,
    x: q((r.cx - r.w / 2 - x0) / M), y: q((r.cy - r.h / 2 - y0) / M), w: q(r.w / M), h: q(r.h / M),
  }));
}

/** Rooms in metres → rectangles in drawing units (`metre` units per metre). */
export function rectsFromLayout(rooms, roomById, metre) {
  return rooms.map((r) => ({ room: roomById.get(r.id), room_id: r.id, cx: (r.x + r.w / 2) * metre, cy: (r.y + r.h / 2) * metre, w: r.w * metre, h: r.h * metre }));
}

/** Exterior wall length per side (metres) of each room, from the built walls. */
export function exposures(walls, metre) {
  const out = new Map();
  for (const wl of walls) {
    if (wl.kind !== "ext") continue;
    const id = wl.rooms[0];
    if (!out.has(id)) out.set(id, { N: 0, S: 0, E: 0, W: 0 });
    out.get(id)[wl.side] += Math.hypot(wl.x2 - wl.x1, wl.y2 - wl.y1) / metre;
  }
  return out;
}

/** Zone 1 … 9 (NW … SE, 5 interior) from the exposed façades: the longer of
 *  north / south, the longer of east / west, each when at least EXPOSED_MIN_M. */
export function zoneFromExposure(e) {
  const ok = (d) => (e?.[d] || 0) >= EXPOSED_MIN_M;
  const ns = ok("N") && (!ok("S") || e.N >= e.S) ? "N" : ok("S") ? "S" : "";
  const ew = ok("W") && (!ok("E") || e.W >= e.E) ? "W" : ok("E") ? "E" : "";
  return ZONE_OF[`${ns},${ew}`];
}

/**
 * QVCI / AQI of a plan as drawn. `rooms` in metres ({id, type, x, y, w, h}),
 * `roomById` the plan's room records (WWR, area units), `walls` from the
 * rebuilt plan. Returns per-room records shaped like the Stage 2 table rows
 * and the plan's `computed` values.
 */
export function scoreLayout(rooms, roomById, walls, metre, inputs, targets = null) {
  const site = siteFromInputs(inputs.site_conditions);
  const exp = exposures(walls, metre);
  const zoned = [], extra = [];
  for (const r of rooms) {
    const rec = roomById.get(r.id) || {};
    const type = String(rec.room_type || r.type).toLowerCase();
    const w = r.w, hh = r.h, area = Math.max(w * hh, 0.5);
    const wwr = Number(rec.wwr ?? inputs.wwr?.[type] ?? 0) || 0;
    const item = { id: r.id, type, area, w, h: hh, perim: 2 * (w + hh), aspect_ratio: w > 0 ? hh / w : 1, wwr, edr: 0, has_front_door: false, rec, exposure: exp.get(r.id) || { N: 0, S: 0, E: 0, W: 0 } };
    (S.ROOM_TYPES.includes(type) ? zoned : extra).push(item);
  }
  // the front door: the largest living room (else the largest zoned room), as in Stage 2
  let fd = zoned.filter((r) => r.type === "living");
  if (!fd.length) fd = zoned;
  if (fd.length) { let big = fd[0]; for (const r of fd) if (r.area > big.area) big = r; big.has_front_door = true; big.edr = 0.15; }

  const zones1 = zoned.map((r) => zoneFromExposure(r.exposure));
  const out = [];
  let computed = { qvci: 0, aqi: 0, avg_spl_dB: 0, zone_spread: 0, concentration: 0, d_target: null, valid: true, as_drawn: true };
  if (zoned.length) {
    const T = S.tables(zoned, site), w8 = S.weights(zoned);
    const [q, a] = S.evaluate(zones1, T.A, T.Q, w8);
    const levels = [];
    zoned.forEach((r, i) => {
      const z1 = zones1[i], Lin = T.L[i][z1 - 1], qv = T.Q[i][z1 - 1], aq = T.A[i][z1 - 1];
      levels.push(Lin);
      out.push({
        ...r.rec, room_id: r.id, room_type: r.type, area_m2: rnd(r.area, 2), w_m: rnd(r.w, 3), h_m: rnd(r.h, 3), aspect_ratio: r.w > 0 ? r.w / r.h : 1,
        zone_assignment: z1 - 1, planned_zone: r.rec.zone_assignment ?? null, exposure: r.exposure,
        quality_view_score: rnd(qv, 4), quality_view_achieved: qv >= 0.5, acoustic_score: rnd(aq, 4), acoustic_ok: Lin <= S.GUIDELINE[r.type],
        est_avg_spl_dB: rnd(Lin, 1), guideline_dB: S.GUIDELINE[r.type], wwr: r.wwr, weight: rnd(w8[i], 4), scored: S.SCORED_TYPES.has(r.type), zoned: true,
      });
    });
    const zc = new Map();
    for (const z of zones1) zc.set(z, (zc.get(z) || 0) + 1);
    const tq = targets ? Number(targets.qvci) : null, ta = targets ? Number(targets.aqi) : null;
    computed = {
      qvci: rnd(q, 4), aqi: rnd(a, 4), avg_spl_dB: rnd(S.planLevelSpl(levels), 2),
      zone_spread: zc.size, concentration: rnd(Math.max(...zc.values()) / Math.max(zoned.length, 1), 4),
      d_target: targets && targets.present ? rnd(Math.abs(q - tq) + Math.abs(a - ta), 4) : null,
      valid: true, as_drawn: true,
    };
  }
  for (const r of extra) {
    out.push({
      ...r.rec, room_id: r.id, room_type: r.type, area_m2: rnd(r.area, 2), w_m: rnd(r.w, 3), h_m: rnd(r.h, 3), aspect_ratio: r.w > 0 ? r.w / r.h : 1,
      zone_assignment: null, planned_zone: null, exposure: r.exposure, quality_view_score: null, quality_view_achieved: null, acoustic_score: null,
      acoustic_ok: null, est_avg_spl_dB: null, guideline_dB: null, weight: 0, scored: false, zoned: false,
    });
  }
  return { rooms: out, computed };
}

/**
 * The plan rebuilt from edited rooms: a synthesis-like record (rects, walls,
 * openings, furniture, frames that replay walls, doors, windows and furniture)
 * plus the as-drawn scores. `base` is the plan's original synthesis (its
 * `metre` keeps the drawing scale of the other plans).
 */
export function buildLayout(schematic, rooms, base, inputs) {
  const metre = base.metre || 1;
  const records = new Map((schematic.orig?.rooms || schematic.rooms || []).map((r) => [r.room_id, r]));
  const present = new Set(rooms.map((r) => r.id));
  const edges = (schematic.edges || []).filter((e) => (present.has(e.source) || String(e.source).startsWith("outside")) && (present.has(e.target) || String(e.target).startsWith("outside")));
  let rects = rectsFromLayout(rooms, records, metre);
  const parts = buildPlanParts(rects, edges, metre, wwrForRooms(rects.map((r) => r.room || { room_id: r.room_id }), inputs.wwr));
  const scored = scoreLayout(rooms, records, parts.walls, metre, inputs, schematic.targets);
  // rooms carry their as-drawn scores (room labels, room cards)
  const byId = new Map(scored.rooms.map((r) => [r.room_id, r]));
  rects = rects.map((r) => ({ ...r, room: byId.get(r.room_id) || r.room }));
  const frames = [], phases = [];
  const push = (label, shapes, kind, { morph = 1, walls = 0, nOpen = 0, nFurn = 0, hold = 1 } = {}) => {
    const f = { label, shapes: shapeSnapshot(shapes, kind, morph), walls, openings: nOpen, furniture: nFurn };
    for (let k = 0; k < Math.max(1, hold); k++) frames.push(f);
  };
  const phase = (label) => phases.push([frames.length, label]);
  phase("Edited plan");
  push("Edited plan", rects, "rect", { hold: 6 });
  recordPlanParts(parts, rects, push, phase);
  push("Edited plan", rects, "rect", { walls: 1, nOpen: parts.openings.length, nFurn: parts.furniture.length, hold: 12 });
  const xs = rects.flatMap((r) => [r.cx - r.w / 2, r.cx + r.w / 2]), ys = rects.flatMap((r) => [r.cy - r.h / 2, r.cy + r.h / 2]);
  const box = rects.length ? [Math.min(...xs), Math.min(...ys), Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)] : [0, 0, 1, 1];
  return {
    syn: { metre, box, rects, circles: [], walls: parts.walls, openings: parts.openings, furniture: parts.furniture, removed: base.removed || [], frames, phases, edited: true },
    scored,
  };
}

/** Rooms of an edit that overlap (pairs of ids), for warnings in the editor. */
export function overlapsIn(rooms, tol = 0.01) {
  const out = [];
  for (let i = 0; i < rooms.length; i++) for (let j = i + 1; j < rooms.length; j++) {
    const a = rooms[i], b = rooms[j];
    if (a.x + a.w - tol > b.x && b.x + b.w - tol > a.x && a.y + a.h - tol > b.y && b.y + b.h - tol > a.y) out.push([a.id, b.id]);
  }
  return out;
}
