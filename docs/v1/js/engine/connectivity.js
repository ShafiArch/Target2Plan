// connectivity.js: Stage 3 node list, AR edge decode and per-plan graph layout
// (models/connectivity_model.py + the zone-cell helpers of models/zone_mock.py).
import { PyRandom } from "./rng.js";
import { reconcileRooms, relocateSecondaryRooms, rewireBalconyEdges, zoneLabel, PRIMARY_TYPES } from "./relocation.js";

export const STAGE3_SIZE = 200;
export const PREFERRED_ORDER = [
  "living", "kitchen", "bedroom", "bathroom", "stair", "balcony", "veranda", "garden", "storage",
  "parking", "pool", "dining", "study", "office", "laundry",
];
export const ZONE_LABELS = { 0: "NW", 1: "N", 2: "NE", 3: "W", 4: "C", 5: "E", 6: "SW", 7: "S", 8: "SE" };

/** (x, y, w, h) of one 3 x 3 cell. */
export function zoneCellBox(zone, w, h, gap = 0) {
  const row = Math.floor(zone / 3), col = zone % 3, cw = w / 3, ch = h / 3;
  return [col * cw + gap, row * ch + gap, cw - 2 * gap, ch - 2 * gap];
}

/** n rooms inside a cell: 1 centred, 2 a pair, 3 or 4 two columns, 5+ three columns (85 % sub-grid). */
export function arrangeRoomsInCell(rooms, cellX, cellY, cellW, cellH) {
  const n = rooms.length;
  if (!n) return [];
  const cx = cellX + cellW / 2, cy = cellY + cellH / 2;
  if (n === 1) return [{ cx, cy }];
  const cols = n <= 2 ? n : n <= 4 ? 2 : 3, rows = Math.ceil(n / cols);
  const uw = cellW * 0.85, uh = cellH * 0.85, gx = cx - uw / 2, gy = cy - uh / 2, sw = uw / cols, sh = uh / rows;
  return rooms.map((_, i) => ({ cx: gx + sw * (i % cols) + sw / 2, cy: gy + sh * Math.floor(i / cols) + sh / 2 }));
}

/** Node ids / types in the training order; outside_0 is always last. */
export function makeNodes(typeVocab, roomTypes) {
  const counts = {};
  for (const rt of roomTypes) { const t = rt.toLowerCase(); counts[t] = (counts[t] || 0) + 1; }
  const inVocab = new Set(Object.keys(typeVocab));
  const ids = [], types = [], used = new Set();
  for (const t of PREFERRED_ORDER) {
    if (!inVocab.has(t)) continue;
    for (let i = 0; i < (counts[t] || 0); i++) { ids.push(`${t}_${i}`); types.push(t); }
    used.add(t);
  }
  for (const t of [...inVocab].sort()) {
    if (used.has(t) || t === "outside") continue;
    for (let i = 0; i < (counts[t] || 0); i++) { ids.push(`${t}_${i}`); types.push(t); }
  }
  ids.push("outside_0"); types.push("outside");
  return { ids, types };
}

/** Positions for one zone table, using the canonical edge set. */
export function buildGraph(table, nodeIds, nodeTypes, edgesIJ, canvasSize, planWidth, planHeight, roomCounts) {
  const planIndex = Number(table.table_index ?? 0);
  const [rooms, added, removed] = reconcileRooms(table.rooms || [], roomCounts);

  const inset = Math.max(8, canvasSize * 0.05), avail = canvasSize - 2 * inset;
  const aspect = planWidth > 0 && planHeight > 0 ? planWidth / planHeight : 1;
  const rectW = aspect >= 1 ? avail : avail * aspect, rectH = aspect >= 1 ? avail / aspect : avail;
  const rectX = (canvasSize - rectW) / 2, rectY = (canvasSize - rectH) / 2;
  const centreX = rectX + rectW / 2, centreY = rectY + rectH / 2;

  let edgePairs = edgesIJ.map(([i, j]) => [nodeIds[i], nodeIds[j]]);
  const rew = rewireBalconyEdges(rooms, edgePairs);
  edgePairs = rew[0];
  const rewired = rew[1];
  const decisions = relocateSecondaryRooms(rooms, edgePairs, new PyRandom(planIndex));
  const zoneStart = {}, zoneFinal = {};
  for (const d of decisions) { zoneStart[d.room_id] = d.zone_start; zoneFinal[d.room_id] = d.zone; }

  const zonePositions = (zoneOf) => {
    const byZone = new Map();
    for (const r of rooms) {
      const z = zoneOf[r.room_id];
      if (z == null) continue;
      if (!byZone.has(z)) byZone.set(z, []);
      byZone.get(z).push(r);
    }
    const pos = {};
    for (const [zone, zr] of byZone) {
      let [cx, cy, cw, ch] = zoneCellBox(zone, rectW, rectH);
      cx += rectX; cy += rectY;
      arrangeRoomsInCell(zr, cx, cy, cw, ch).forEach((p, k) => {
        pos[zr[k].room_id] = [Math.min(Math.max(p.cx, cx + cw * 0.12), cx + cw * 0.88), Math.min(Math.max(p.cy, cy + ch * 0.12), cy + ch * 0.88)];
      });
    }
    return pos;
  };
  const startPos = zonePositions(zoneStart), finalPos = zonePositions(zoneFinal);

  // balconies hang just outside the plan on a façade of their room
  const off = Math.max(4, inset * 0.6);
  const facadeOf = {}, anchorOf = {};
  for (const d of decisions) { if (d.facade) facadeOf[d.room_id] = d.facade; anchorOf[d.room_id] = d.anchor; }
  const perFacade = new Map();
  for (const [rid, side] of Object.entries(facadeOf)) {
    const key = `${anchorOf[rid] || ""}\u0000${side}`;
    if (!perFacade.has(key)) perFacade.set(key, { aid: anchorOf[rid] || "", side, rids: [] });
    perFacade.get(key).rids.push(rid);
  }
  for (const { aid, side, rids } of perFacade.values()) {
    const [ax, ay] = finalPos[aid] || [centreX, centreY];
    const za = zoneFinal[aid];
    let [cx, cy, cw, ch] = zoneCellBox(za != null ? Number(za) : 4, rectW, rectH);
    cx += rectX; cy += rectY;
    const n = rids.length;
    [...rids].sort().forEach((rid, k) => {
      const t = (k + 1) / (n + 1);
      let x, y;
      if (side === "N" || side === "S") { x = n > 1 ? cx + cw * t : ax; y = side === "N" ? rectY - off : rectY + rectH + off; }
      else { y = n > 1 ? cy + ch * t : ay; x = side === "W" ? rectX - off : rectX + rectW + off; }
      finalPos[rid] = [x, y];
    });
  }
  for (const nid of nodeIds) {
    if (nid.startsWith("outside")) continue;
    if (!(nid in finalPos)) finalPos[nid] = [centreX, centreY];
    if (!(nid in startPos)) startPos[nid] = [centreX, centreY];
  }

  // outside node: just beyond the plan edge nearest the room it connects to
  const ent = edgePairs.find(([a, b]) => a.startsWith("outside") || b.startsWith("outside"));
  const entry = ent ? (ent[0].startsWith("outside") ? ent[1] : ent[0]) : null;
  const [ex, ey] = entry ? (finalPos[entry] || [rectX, centreY]) : [rectX, centreY];
  const dist = { W: ex - rectX, E: rectX + rectW - ex, N: ey - rectY, S: rectY + rectH - ey };
  let side_ = "W";
  for (const k of ["W", "E", "N", "S"]) if (dist[k] < dist[side_]) side_ = k;
  let ox = ex, oy = ey;
  if (side_ === "W") ox = rectX - off; else if (side_ === "E") ox = rectX + rectW + off;
  else if (side_ === "N") oy = rectY - off; else oy = rectY + rectH + off;
  for (const nid of nodeIds) if (nid.startsWith("outside")) { finalPos[nid] = [ox, oy]; startPos[nid] = [ox, oy]; }

  const nodes = nodeIds.map((nid, k) => {
    const nt = nodeTypes[k];
    return {
      id: nid, type: nt, is_direction: false, is_primary: PRIMARY_TYPES.has(nt),
      x: finalPos[nid][0], y: finalPos[nid][1], x_start: startPos[nid][0], y_start: startPos[nid][1],
      zone_start: zoneStart[nid] ?? null, zone: zoneFinal[nid] ?? null, facade: facadeOf[nid] ?? null,
      anchor: nid in facadeOf ? anchorOf[nid] ?? null : null,
      relocated: (zoneStart[nid] ?? null) !== (zoneFinal[nid] ?? null) || nid in facadeOf,
    };
  });
  const relocations = decisions
    .filter((d) => d.zone_start !== d.zone || d.facade)
    .map((d) => ({ ...d, label: d.facade ? `${d.room_id}: outside ${d.anchor} (${d.facade})` : `${d.room_id}: ${zoneLabel(d.zone_start)} → ${zoneLabel(d.zone)}` }));
  for (const rw of rewired) relocations.push({ room_id: rw.room_id, reason: rw.reason, label: rw.reason });
  return {
    meta: { plan_index: planIndex }, canvas_size: canvasSize, plan_width: Number(planWidth), plan_height: Number(planHeight),
    plan_rect: [rectX, rectY, rectW, rectH], nodes,
    edges_ordered: edgePairs.map(([a, b]) => ({ source: a, target: b })), exposure_edges: [],
    relocations, decisions, rooms_added: added, rooms_removed: removed, balcony_rewired: rewired,
  };
}

/**
 * Run the edge model once (on the first selected table's rooms) and lay the
 * same edges out on every selected table.
 */
export function generateConnectivityGraphs(net, zoneTables, selectedIdxs, { planWidth = 1500, planHeight = 2000, roomCounts = null, sample = true, threshold = 0.5, seed = 42, canvasSize = STAGE3_SIZE } = {}) {
  if (!zoneTables.length || !selectedIdxs.length) return [];
  const byIdx = new Map(zoneTables.map((t) => [Number(t.table_index), t]));
  const first = selectedIdxs.map((i) => byIdx.get(Number(i))).find(Boolean);
  if (!first) return [];
  let firstRooms = first.rooms || [];
  if (roomCounts) firstRooms = reconcileRooms(firstRooms, roomCounts)[0];
  if (!firstRooms.length) return [];
  const { ids, types } = makeNodes(net.vocab, firstRooms.map((r) => String(r.room_type || "other").toLowerCase()));
  const typeIdx = types.map((t) => net.vocab[t] ?? net.vocab.outside);
  const { edges } = net.generate(typeIdx, { sample, threshold, rng: new PyRandom(seed) });
  const out = [];
  for (const idx of selectedIdxs) {
    const t = byIdx.get(Number(idx));
    if (!t || !(t.rooms || []).length) continue;
    out.push(buildGraph(t, ids, types, edges, canvasSize, planWidth, planHeight, roomCounts));
  }
  return out;
}
