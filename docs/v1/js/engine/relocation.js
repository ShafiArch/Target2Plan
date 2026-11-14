// relocation.js: reconcile the zone table with the requested rooms and move
// secondary rooms next to the primary room they connect to
// (models/zone_relocation.py). Zones are 0-based here (Z1 = 0 ... Z9 = 8).

export const PRIMARY_TYPES = new Set(["living", "bedroom", "kitchen"]);
export const CENTRE_ZONE = 4;
export const ADJACENT = {
  0: [1, 3, 4], 1: [0, 2, 3, 4, 5], 2: [1, 4, 5], 3: [0, 1, 4, 6, 7], 4: [0, 1, 2, 3, 5, 6, 7, 8],
  5: [1, 2, 4, 7, 8], 6: [3, 4, 7], 7: [3, 4, 5, 6, 8], 8: [4, 5, 7],
};
export const BALCONY_ANCHOR_PRIORITY = { living: 0, bedroom: 1, kitchen: 2 };
export const OUTDOOR_TYPES = new Set(["balcony"]);
export const ZONE_FACADES = { 0: ["N", "W"], 1: ["N"], 2: ["N", "E"], 3: ["W"], 4: [], 5: ["E"], 6: ["S", "W"], 7: ["S"], 8: ["S", "E"] };
export const SIDE_ZONES = { N: [0, 1, 2], S: [6, 7, 8], W: [0, 3, 6], E: [2, 5, 8] };

export const zoneLabel = (z) => (z == null ? "-" : `Z${Number(z) + 1}`);

// tuple comparison for sort keys (numbers, booleans, strings)
function cmpKeys(a, b) {
  for (let i = 0; i < a.length; i++) {
    const x = typeof a[i] === "boolean" ? +a[i] : a[i], y = typeof b[i] === "boolean" ? +b[i] : b[i];
    if (x < y) return -1;
    if (x > y) return 1;
  }
  return 0;
}
const sortBy = (arr, key) => arr.map((v, i) => [key(v), i, v]).sort((p, q) => cmpKeys(p[0], q[0]) || p[1] - q[1]).map((p) => p[2]);
const isAnchorType = (t) => Object.hasOwn(BALCONY_ANCHOR_PRIORITY, String(t));
class Counter {                              // collections.Counter for zone indices
  constructor() { this.m = new Map(); }
  get(k) { return this.m.get(k) || 0; }
  add(k, d = 1) { this.m.set(k, this.get(k) + d); }
}

/** Returns [rooms, addedIds, removedIds]. */
export function reconcileRooms(rooms, roomCounts) {
  if (!roomCounts) return [[...rooms], [], []];
  const kept = [], removed = [], seen = {};
  for (const r of rooms) {
    const rt = String(r.room_type || "other").toLowerCase();
    const want = Number(roomCounts[rt] || 0);
    if ((seen[rt] || 0) < want) { kept.push(r); seen[rt] = (seen[rt] || 0) + 1; }
    else removed.push(r.room_id ?? `${rt}_?`);
  }
  const added = [];
  for (const [rt, w] of Object.entries(roomCounts)) {
    const want = Number(w || 0);
    while ((seen[rt] || 0) < want) {
      const rid = `${rt}_${seen[rt] || 0}`;
      kept.push({ room_id: rid, room_type: rt, zone_assignment: null });
      added.push(rid); seen[rt] = (seen[rt] || 0) + 1;
    }
  }
  return [kept, added, removed];
}

function anchorPrimary(rid, adj, primaryIds, fallback) {
  const seen = new Set([rid]);
  const q = [rid];
  while (q.length) {
    const cur = q.shift();
    for (const nb of adj[cur] || []) {
      if (primaryIds.has(nb)) return nb;
      if (!seen.has(nb) && !nb.startsWith("outside")) { seen.add(nb); q.push(nb); }
    }
  }
  return fallback;
}

function balconyAnchor(rid, adj, byId, taken) {
  let cands = (adj[rid] || []).filter((n) => byId[n] && isAnchorType(byId[n].room_type || "") && byId[n].zone_assignment != null);
  if (cands.length) {
    cands = sortBy(cands, (n) => [BALCONY_ANCHOR_PRIORITY[String(byId[n].room_type)], taken.has(n), n]);
    return [cands[0], false];
  }
  const entries = Object.entries(byId);
  let free = entries.filter(([n, r]) => ["living", "bedroom"].includes(String(r.room_type)) && r.zone_assignment != null && !taken.has(n)).map(([n]) => n);
  if (!free.length) free = entries.filter(([, r]) => ["living", "bedroom"].includes(String(r.room_type)) && r.zone_assignment != null).map(([n]) => n);
  if (!free.length) return [null, false];
  free = sortBy(free, (n) => [BALCONY_ANCHOR_PRIORITY[String(byId[n].room_type)], n]);
  return [free[0], true];
}

function balconyFacade(zone, occupancy, rng, used) {
  let sides = [...(ZONE_FACADES[zone] || [])], why;
  if (!sides.length) { sides = ["N", "S", "E", "W"]; why = "interior room, least crowded side of the plan"; }
  else if (sides.length === 1) return [sides[0], "only façade of this zone"];
  else why = "least crowded of the zone's two façades";
  let fresh = sides.filter((sd) => !used.has(`${zone}:${sd}`));
  if (!fresh.length) fresh = sides;
  const load = Object.fromEntries(fresh.map((sd) => [sd, SIDE_ZONES[sd].reduce((s, z) => s + occupancy.get(z), 0)]));
  const best = Math.min(...Object.values(load));
  const tied = fresh.filter((sd) => load[sd] === best);
  if (tied.length === 1) return [tied[0], why];
  return [rng.choice(tied), why + ", random tie"];
}

/** Replace every balcony's connections by one edge to its anchor room. Returns [edges, log]. */
export function rewireBalconyEdges(rooms, edges) {
  const byId = Object.fromEntries(rooms.map((r) => [r.room_id, r]));
  const adj = {};
  for (const [a, b] of edges) { (adj[a] ||= []).push(b); (adj[b] ||= []).push(a); }
  const balconies = rooms.filter((r) => OUTDOOR_TYPES.has(String(r.room_type))).map((r) => r.room_id);
  if (!balconies.length) return [[...edges], []];
  const out = [], log = [], keep = new Map(), taken = new Map();
  const hasGood = (bid) => (adj[bid] || []).some((n) => isAnchorType((byId[n] || {}).room_type));
  for (const bid of sortBy(balconies, (b) => [!hasGood(b), b])) {
    const [anchor, rewired] = balconyAnchor(bid, adj, byId, taken);
    if (anchor == null) continue;
    keep.set(bid, anchor); taken.set(anchor, bid);
    const dropped = (adj[bid] || []).filter((n) => n !== anchor);
    if (rewired || dropped.length) {
      log.push({
        room_id: bid, anchor, dropped,
        reason: rewired ? `${bid} was on ${dropped.join(", ") || "nothing"} → re-assigned to ${anchor}` : `${bid} kept on ${anchor}, dropped ${dropped.join(", ")}`,
      });
    }
  }
  for (const [a, b] of edges) if (!keep.has(a) && !keep.has(b)) out.push([a, b]);
  for (const [bid, anchor] of keep) out.push([anchor, bid]);
  return [out, log];
}

/** Final zone of every room: [{room_id, room_type, zone_start, zone, anchor, reason, facade?}] in input order. */
export function relocateSecondaryRooms(rooms, edges, rng) {
  const byId = Object.fromEntries(rooms.map((r) => [r.room_id, r]));
  const primaryIds = new Set(rooms.filter((r) => PRIMARY_TYPES.has(String(r.room_type || "").toLowerCase()) && r.zone_assignment != null).map((r) => r.room_id));
  const adj = {};
  for (const [a, b] of edges) { (adj[a] ||= []).push(b); (adj[b] ||= []).push(a); }
  const living = (rooms.find((r) => r.room_type === "living" && r.zone_assignment != null) || {}).room_id ?? null;
  const occupancy = new Counter();
  for (const r of rooms) if (r.zone_assignment != null) occupancy.add(Number(r.zone_assignment));
  const out = [], facadesUsed = new Set();
  const order = sortBy(rooms, (r) => [primaryIds.has(r.room_id), String(r.room_type) !== "bathroom", r.room_id]);
  for (const r of order) {
    const rid = r.room_id;
    const z0 = r.zone_assignment == null ? null : Number(r.zone_assignment);
    const rec = { room_id: rid, room_type: r.room_type, zone_start: z0, zone: z0, anchor: null, reason: "primary, fixed" };
    if (primaryIds.has(rid)) { out.push(rec); continue; }
    const isOutdoor = OUTDOOR_TYPES.has(String(r.room_type));
    const anchor = isOutdoor
      ? (adj[rid] || []).find((n) => byId[n] && isAnchorType(byId[n].room_type)) ?? null
      : anchorPrimary(rid, adj, primaryIds, living);
    rec.anchor = anchor;
    if (anchor == null || byId[anchor].zone_assignment == null) { rec.reason = "no connected primary room, kept"; out.push(rec); continue; }
    const za = Number(byId[anchor].zone_assignment);
    if (isOutdoor) {
      const [side, why] = balconyFacade(za, occupancy, rng, facadesUsed);
      facadesUsed.add(`${za}:${side}`);
      rec.zone = null; rec.facade = side;
      rec.reason = `balcony outside ${anchor} on its ${side} façade (${why})`;
      out.push(rec); continue;
    }
    const allowed = new Set([...ADJACENT[za], za]);
    if (z0 != null && allowed.has(z0)) { rec.reason = (z0 === za ? "same zone as " : "adjacent to ") + anchor; out.push(rec); continue; }
    const cands = [...ADJACENT[za]];
    const nonCentre = cands.filter((z) => z !== CENTRE_ZONE);
    const pool = nonCentre.length ? nonCentre : cands;
    const best = Math.min(...pool.map((z) => occupancy.get(z)));
    let choice, why;
    if (best > 0 && cands.includes(CENTRE_ZONE) && occupancy.get(CENTRE_ZONE) < best) {
      choice = CENTRE_ZONE; why = "all other adjacent zones crowded, centre Z5";
    } else {
      const tied = pool.filter((z) => occupancy.get(z) === best);
      choice = tied.length === 1 ? tied[0] : rng.choice(tied);
      why = tied.length === 1 ? "least crowded adjacent zone" : "random among equally crowded adjacent zones";
    }
    if (z0 != null) occupancy.add(z0, -1);
    occupancy.add(choice);
    rec.zone = choice;
    rec.reason = `moved ${zoneLabel(z0)} → ${zoneLabel(choice)} next to ${anchor} (${why})`;
    out.push(rec);
  }
  const pos = Object.fromEntries(rooms.map((r, i) => [r.room_id, i]));
  return out.sort((a, b) => pos[a.room_id] - pos[b.room_id]);
}
