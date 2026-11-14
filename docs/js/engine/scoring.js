// scoring.js: the journal (v13) scoring equations, capacity rule and reachable
// range: a port of models/zone_scoring.py plus the feature builders of
// models/zone_model.py. Zones are 1 … 9 here (1 NW … 5 interior … 9 SE).

import { pySum, npSum } from "./pymath.js";

export const H = 2.7, ALPHA = 0.15, STC_WALL = 45, STC_WIN = 32, STC_DOOR = 25;
export const INTERIOR_ATT = 51, L_IN_FLOOR = 20, HALF_WIDTH = 15, WWR_REF = 0.4;
export const L_RANGE = [30, 80];
export const GUIDELINE = { bedroom: 30, living: 35, kitchen: 40, bathroom: 45 };
export const VIEW_WEIGHTS = { Vegetation: 1.0, Sky: 0.9, Urban: 0.7, Balcony: 0.4, "No View": 0.0 };
export const ROOM_TYPES = ["living", "kitchen", "bedroom", "bathroom"];
export const SCORED_TYPES = new Set(["living", "kitchen", "bedroom"]);
export const TYPE_ORDER = { living: 0, kitchen: 1, bedroom: 2, bathroom: 3 };
export const DIRS = ["N", "S", "E", "W"];
export const ZONE_SIDES = { 1: ["N", "W"], 2: ["N"], 3: ["N", "E"], 4: ["W"], 5: [], 6: ["E"], 7: ["S", "W"], 8: ["S"], 9: ["S", "E"] };
export const ZONE_CLASS = { 1: "corner", 2: "cardinal", 3: "corner", 4: "cardinal", 5: "interior", 6: "cardinal", 7: "corner", 8: "cardinal", 9: "corner" };
export const CAP_RULE = { corner: [[7, 2], [99, 3]], cardinal: [[99, 3]], interior: [[7, 1], [99, 2]] };
export const INTERIOR_BATHROOMS_ONLY = true;

const TAU_WIN = 10 ** (-STC_WIN / 10), TAU_DOOR = 10 ** (-STC_DOOR / 10), TAU_WALL = 10 ** (-STC_WALL / 10);

export function indoorLevel(room, zone, site) {
  const sides = ZONE_SIDES[zone];
  if (!sides.length) return Math.max(pySum(DIRS.map((d) => site.L[d])) / 4 - INTERIOR_ATT, L_IN_FLOOR);
  const A = Math.max(ALPHA * (2 * room.area + room.perim * H), 1);
  const wwr = Math.min(Math.max(room.wwr, 0), 0.95);
  const edr = Math.min(Math.max(room.edr || 0, 0), 0.95);
  const sol = Math.max(1 - wwr - edr, 0);
  const tau = wwr * TAU_WIN + edr * TAU_DOOR + sol * TAU_WALL;
  const TL = -10 * Math.log10(Math.max(tau, 1e-12));
  let energy = 0;
  for (const d of sides) {
    const length = d === "N" || d === "S" ? room.w : room.h;
    const S = Math.max(length * H, 1);
    energy += 10 ** ((site.L[d] - TL + 10 * Math.log10(S / A)) / 10);
  }
  return energy > 1e-15 ? Math.max(10 * Math.log10(energy), L_IN_FLOOR) : L_IN_FLOOR;
}

export function scoreA(type, Lin) {
  const G = GUIDELINE[type];
  return Math.min(Math.max((G + HALF_WIDTH - Lin) / (2 * HALF_WIDTH), 0), 1);
}

export function zoneView(site) {
  const w = Object.fromEntries(DIRS.map((d) => [d, VIEW_WEIGHTS[site.views[d]] ?? 0]));
  return Array.from({ length: 9 }, (_, i) => ZONE_SIDES[i + 1].reduce((m, d) => Math.max(m, w[d]), 0));
}

export function zoneLevel(site) {
  return Array.from({ length: 9 }, (_, i) => {
    const s = ZONE_SIDES[i + 1];
    return s.length ? pySum(s.map((d) => site.L[d])) / s.length : pySum(DIRS.map((d) => site.L[d])) / 4;
  });
}

export function weights(rooms) {
  const w = rooms.map((r) => (SCORED_TYPES.has(r.type) ? r.area : 0));
  const s = npSum(w);
  return s > 0 ? w.map((v) => v / s) : w;
}

// n × 9 tables (row-major arrays of arrays)
export function tables(rooms, site) {
  const zv = zoneView(site);
  const L = rooms.map((r) => Array.from({ length: 9 }, (_, z) => indoorLevel(r, z + 1, site)));
  const A = rooms.map((r, i) => L[i].map((l) => scoreA(r.type, l)));
  const Q = rooms.map((r) => zv.map((v) => v * Math.min(1, r.wwr / WWR_REF)));
  return { A, Q, L, zv };
}

export function evaluate(zones1, A, Q, w) {
  return [npSum(zones1.map((z, i) => w[i] * Q[i][z - 1])), npSum(zones1.map((z, i) => w[i] * A[i][z - 1]))];
}

export const zoneAllowed = (type, z, ibo = INTERIOR_BATHROOMS_ONLY) => !(ibo && z === 5 && SCORED_TYPES.has(type));

export function capacity(nRooms, override = null) {
  const cap = {};
  for (const [cls, steps] of Object.entries(CAP_RULE)) {
    for (const [nMax, c] of steps) if (nRooms <= nMax) { cap[cls] = c; break; }
  }
  if (override) for (const cls of ["corner", "cardinal", "interior"]) if (override[cls] > 0) cap[cls] = override[cls] | 0;
  return cap;
}

export const capacityVector = (cap) => Array.from({ length: 9 }, (_, i) => cap[ZONE_CLASS[i + 1]]);

export function valid(zones1, cap, types = null, ibo = INTERIOR_BATHROOMS_ONLY) {
  const c = new Array(10).fill(0);
  zones1.forEach((z) => c[z]++);
  if (zones1.length > 1 && Math.max(...c.slice(1)) === zones1.length) return false;
  if (types && types.some((t, i) => !zoneAllowed(t, zones1[i], ibo))) return false;
  for (let z = 1; z <= 9; z++) if (c[z] > cap[ZONE_CLASS[z]]) return false;
  return true;
}

// ── Hungarian algorithm (rectangular, rows ≤ cols): min Σ C[i][p(i)] ───
export function linearSumAssignment(C) {
  const n = C.length, m = C[0].length;
  if (n > m) throw new Error("rows must not exceed columns");
  const INF = Infinity;
  const u = new Float64Array(n + 1), v = new Float64Array(m + 1);
  const p = new Int32Array(m + 1), way = new Int32Array(m + 1);
  for (let i = 1; i <= n; i++) {
    p[0] = i;
    let j0 = 0;
    const minv = new Float64Array(m + 1).fill(INF);
    const used = new Uint8Array(m + 1);
    do {
      used[j0] = 1;
      const i0 = p[j0];
      let delta = INF, j1 = 0;
      for (let j = 1; j <= m; j++) {
        if (used[j]) continue;
        const cur = C[i0 - 1][j - 1] - u[i0] - v[j];
        if (cur < minv[j]) { minv[j] = cur; way[j] = j0; }
        if (minv[j] < delta) { delta = minv[j]; j1 = j; }
      }
      for (let j = 0; j <= m; j++) {
        if (used[j]) { u[p[j]] += delta; v[j] -= delta; } else minv[j] -= delta;
      }
      j0 = j1;
    } while (p[j0] !== 0);
    do { const j1 = way[j0]; p[j0] = p[j1]; j0 = j1; } while (j0);
  }
  const rowToCol = new Int32Array(n);
  for (let j = 1; j <= m; j++) if (p[j]) rowToCol[p[j] - 1] = j - 1;
  return rowToCol;
}

function assignmentBound(M, w, allowed, capv, maximize) {
  const n = M.length;
  let slots = [];
  for (let z = 0; z < 9; z++) for (let k = 0; k < capv[z]; k++) slots.push(z);
  if (slots.length < n) for (let z = 0; z < 9; z++) for (let k = 0; k < n; k++) slots.push(z);
  const C = M.map((row, i) => slots.map((z) => {
    const col = allowed[i][z] ? w[i] * row[z] : (maximize ? -1e6 : 1e6);
    return maximize ? -col : col;
  }));
  const col = linearSumAssignment(C);
  const terms = [];
  for (let i = 0; i < n; i++) { const z = slots[col[i]]; if (allowed[i][z]) terms.push(w[i] * M[i][z]); }
  return pySum(terms);
}

export function reachable(A, Q, w, allowed = null, capv = null) {
  const n = A.length;
  allowed = allowed || A.map(() => new Array(9).fill(true));
  if (!capv) {
    const lo = (M) => npSum(M.map((r, i) => w[i] * Math.min(...r.filter((_, z) => allowed[i][z]))));
    const hi = (M) => npSum(M.map((r, i) => w[i] * Math.max(...r.filter((_, z) => allowed[i][z]))));
    return { Q: [lo(Q), hi(Q)], A: [lo(A), hi(A)] };
  }
  return {
    Q: [assignmentBound(Q, w, allowed, capv, false), assignmentBound(Q, w, allowed, capv, true)],
    A: [assignmentBound(A, w, allowed, capv, false), assignmentBound(A, w, allowed, capv, true)],
  };
}

export const planLevelSpl = (levels) => {
  const v = levels.filter((x) => x != null);
  return v.length ? 10 * Math.log10(pySum(v.map((x) => 10 ** (x / 10))) / v.length) : 0;
};

// ── features for the zone model (models/zone_model.py) ─────────────────
export const roomOrder = (rooms) =>
  rooms.map((_, i) => i).sort((i, j) => TYPE_ORDER[rooms[i].type] - TYPE_ORDER[rooms[j].type] || rooms[j].area - rooms[i].area || i - j);

export function roomFeatures(rooms) {
  // float32, like the prototype's np.float32 feature matrix
  const tot = pySum(rooms.map((r) => r.area));
  const X = new Float32Array(rooms.length * 11);
  rooms.forEach((r, i) => {
    const o = i * 11, sq = Math.sqrt(Math.max(r.area, 0.5));
    X[o + TYPE_ORDER[r.type]] = 1;
    X[o + 4] = r.area / 50; X[o + 5] = r.area / Math.max(tot, 1); X[o + 6] = r.wwr;
    X[o + 7] = r.w / sq; X[o + 8] = r.h / sq; X[o + 9] = r.edr || 0; X[o + 10] = r.has_front_door ? 1 : 0;
  });
  return X;
}

export function siteContext(site) {
  const zv = zoneView(site).map(Math.fround);
  const zn = Array.from({ length: 9 }, (_, i) => {
    const s = ZONE_SIDES[i + 1];
    return Math.fround(s.length ? (npSum(s.map((d) => site.L[d])) / s.length - 30) / 50 : 0);
  });
  return { zv, zn };
}
