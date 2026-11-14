// zones.js: Stage 2 plan set-up and zone tables (models/zone_model.py).
// Rooms in metres, score tables, capacity, allowed zones, reachable range,
// and the table/pool builder around the v13 network (ZoneNet in nn.js).
import * as S from "./scoring.js";
import { PyRandom } from "./rng.js";
import { pyRound } from "./pymath.js";

export const MAX_ROOMS = 14;
export const AQI_COLLAPSED_WIDTH = 0.05;
export const AQI_LIVE_WIDTH = 0.15;
export const VIEW_OPTIONS = ["Vegetation", "Sky", "Urban", "Balcony", "No View"];
export const UNIT_TO_M = { m: 1.0, ft: 0.3048 };

export const rnd = (x, d) => pyRound(Number(x), d);     // Python round(): ties to even

export function unitScaleToM(units, pxPerM = 100) {
  if (units === "px") return 1 / Math.max(Number(pxPerM), 1e-6);
  return UNIT_TO_M[units] ?? 1.0;
}

/** SiteConditions → scoring site (levels clipped to the corpus range 30 to 80 dB(A)). */
export function siteFromInputs(sc) {
  const views = {}, L = {};
  for (const d of S.DIRS) {
    const side = sc?.[d];
    views[d] = (side && side.view) || "No View";
    const lvl = side ? Number(side.spl_dB ?? 60) : 60;
    L[d] = Math.min(Math.max(lvl, S.L_RANGE[0]), S.L_RANGE[1]);
  }
  return { views, L };
}

/** Split a Stage 1 variation into [zoned rooms, extra rooms] in metres. */
export function roomsFromVariation(variation, inputs, frontDoorType = "living", edrDefault = 0.15) {
  const pg = inputs.plan_geometry;
  const scale = unitScaleToM(pg.units, pg.px_per_m ?? 100);
  const arDict = inputs.aspect_ratios || {}, wwrDict = inputs.wwr || {};
  const zoned = [], extra = [];
  for (const r of variation.rooms || []) {
    const rt = String(r.room_type || "other").toLowerCase();
    const areaUnits = Number(r.area);
    const areaM2 = Math.max(areaUnits * scale * scale, 0.5);
    const drawn = !!(r.w_m && r.h_m);
    let ar = drawn ? Number(r.h_m) / Number(r.w_m) : Number(arDict[rt] ?? r.aspect_ratio ?? 1.0) || 1.0;
    ar = Math.min(Math.max(ar, 0.2), 5.0);
    const w = Math.sqrt(areaM2 / ar), h = Math.sqrt(areaM2 * ar);
    const rec = {
      id: r.room_id, type: rt, area: areaM2, area_units: areaUnits, w, h, perim: 2 * (w + h),
      aspect_ratio: ar, drawn, wwr: Number(wwrDict[rt] ?? r.wwr ?? 0) || 0, edr: 0, has_front_door: false,
    };
    if (drawn) { rec.w_m = Number(r.w_m); rec.h_m = Number(r.h_m); }
    (S.ROOM_TYPES.includes(rt) ? zoned : extra).push(rec);
  }
  let fd = zoned.filter((r) => r.type === frontDoorType);
  if (!fd.length) fd = zoned;
  if (fd.length) {
    let big = fd[0];
    for (const r of fd) if (r.area > big.area) big = r;          // max(): first of equals
    big.has_front_door = true; big.edr = edrDefault;
  }
  return [zoned, extra];
}

export function capacityFor(inputs, nZoned) {
  const ov = inputs.prefs?.capacity_override;
  return S.capacity(nZoned, ov && Object.keys(ov).length ? ov : null);
}

export class PlanSetup {
  constructor(inputs, variation) {
    const [zoned, extra] = roomsFromVariation(variation, inputs);
    this.rooms = S.roomOrder(zoned).map((j) => zoned[j]);
    this.extra = extra;
    this.n = this.rooms.length;
    this.site = siteFromInputs(inputs.site_conditions);
    this.types = this.rooms.map((r) => r.type);
    this.ibo = inputs.prefs?.interior_bathrooms_only ?? S.INTERIOR_BATHROOMS_ONLY;
    this.cap = capacityFor(inputs, this.n);
    this.capv = S.capacityVector(this.cap);
    this.allowed = this.types.map((t) => Array.from({ length: 9 }, (_, z) => S.zoneAllowed(t, z + 1, this.ibo)));
    if (this.n) {
      const T = S.tables(this.rooms, this.site);
      Object.assign(this, { A: T.A, Q: T.Q, L: T.L, zv: T.zv });
      this.w = S.weights(this.rooms);
      this.reach = S.reachable(this.A, this.Q, this.w, this.allowed, this.capv);
      this.reachFree = S.reachable(this.A, this.Q, this.w, this.allowed);
    } else {
      this.A = this.Q = this.L = []; this.zv = S.zoneView(this.site); this.w = [];
      this.reach = this.reachFree = { Q: [0, 0], A: [0, 0] };
    }
    this.zlevel = S.zoneLevel(this.site);
  }

  get aqiWidth() { return this.reach.A[1] - this.reach.A[0]; }
  get aqiCollapsed() { return this.aqiWidth < AQI_COLLAPSED_WIDTH; }

  reachableDict() {
    const r4 = (v) => rnd(v, 4);
    return {
      qvci: this.reach.Q.map(r4), aqi: this.reach.A.map(r4),
      qvci_free: this.reachFree.Q.map(r4), aqi_free: this.reachFree.A.map(r4),
      aqi_collapsed: this.aqiCollapsed, aqi_live: this.aqiWidth >= AQI_LIVE_WIDTH,
    };
  }

  zoneWeightDicts() {
    const zw = {}, aw = {}, lv = {};
    for (let z = 0; z < 9; z++) {
      zw[`zone_${z}`] = rnd(this.zv[z], 4);
      lv[`zone_${z}`] = rnd(this.zlevel[z], 1);
      aw[`zone_${z}`] = rnd(1 - (this.zlevel[z] - 30) / 50, 4);
    }
    return [zw, aw, lv];
  }

  valid(zones1) { return S.valid(zones1, this.cap, this.types, this.ibo); }

  /** Score one arrangement (zones 1-based, decode order) into a card table. */
  table(zones1, targets, present, decode = "sampled") {
    const [q, a] = S.evaluate(zones1, this.A, this.Q, this.w);
    const levels = [], rooms = [];
    this.rooms.forEach((r, i) => {
      const z1 = zones1[i], Lin = this.L[i][z1 - 1], qv = this.Q[i][z1 - 1], aq = this.A[i][z1 - 1];
      levels.push(Lin);
      rooms.push({
        room_id: r.id, room_type: r.type, area: r.area_units, area_m2: rnd(r.area, 2),
        zone_assignment: z1 - 1, quality_view_score: rnd(qv, 4), quality_view_achieved: qv >= 0.5,
        acoustic_score: rnd(aq, 4), acoustic_ok: Lin <= S.GUIDELINE[r.type], est_avg_spl_dB: rnd(Lin, 1),
        guideline_dB: S.GUIDELINE[r.type], wwr: r.wwr, aspect_ratio: r.aspect_ratio, weight: rnd(this.w[i], 4),
        scored: S.SCORED_TYPES.has(r.type), zoned: true,
        ...(r.drawn ? { w_m: rnd(r.w, 3), h_m: rnd(r.h, 3) } : {}),
      });
    });
    for (const r of this.extra) {
      rooms.push({
        room_id: r.id, room_type: r.type, area: r.area_units, area_m2: rnd(r.area, 2), zone_assignment: null,
        quality_view_score: null, quality_view_achieved: null, acoustic_score: null, acoustic_ok: null,
        est_avg_spl_dB: null, guideline_dB: null, wwr: r.wwr, aspect_ratio: r.aspect_ratio, weight: 0,
        scored: false, zoned: false, ...(r.drawn ? { w_m: rnd(r.w, 3), h_m: rnd(r.h, 3) } : {}),
      });
    }
    const zc = new Map();
    for (const z of zones1) zc.set(z, (zc.get(z) || 0) + 1);
    const [zw, aw, lv] = this.zoneWeightDicts();
    const tq = Number(targets[0]), ta = Number(targets[1]);
    return {
      table_index: 0,
      computed: {
        qvci: rnd(q, 4), aqi: rnd(a, 4), avg_spl_dB: rnd(S.planLevelSpl(levels), 2),
        zone_spread: zc.size, concentration: zc.size ? rnd(Math.max(...zc.values()) / Math.max(this.n, 1), 4) : 0,
        d_target: present ? rnd(Math.abs(q - tq) + Math.abs(a - ta), 4) : null, valid: this.valid(zones1),
      },
      targets: { qvci: tq, aqi: ta, present: !!present },
      reachable: this.reachableDict(),
      capacity: { ...this.cap },
      zone_weights: zw, acoustic_weights: aw, zone_levels_dB: lv,
      decode, model: "v13", rooms,
    };
  }
}

/** Reachable QVCI / AQI for the current inputs and variation (null when nothing to score). */
export function reachableRange(inputs, variation) {
  try {
    if (!variation || !(variation.rooms || []).length) return null;
    const ps = new PlanSetup(inputs, variation);
    if (!ps.n) return null;
    return { ...ps.reachableDict(), n_zoned: ps.n, capacity: { ...ps.cap } };
  } catch (e) {
    return null;
  }
}

/**
 * generate_zone_tables: one greedy decode + nSamples sampled decodes.
 * Returns { tables (unique, sorted by distance to target), pool (one metric dict per decode) }.
 */
export function generateZoneTables(net, inputs, variation, { nSamples = 80, temperature = 0.8, seed = null } = {}) {
  if (!variation || !(variation.rooms || []).length) return { tables: [], pool: [] };
  const ps = new PlanSetup(inputs, variation);
  if (!ps.n) throw new Error("No zoned room types (living / kitchen / bedroom / bathroom) in this variation.");
  if (ps.n > MAX_ROOMS) throw new Error(`${ps.n} zoned rooms; the v13 model was trained up to ${MAX_ROOMS}.`);
  const prefs = inputs.prefs || {};
  const present = prefs.targets_enabled ?? true;
  const tq = Number(prefs.target_qvci ?? 0.7) || 0, ta = Number(prefs.target_aqi ?? 0.6) || 0;
  const { zv, zn } = S.siteContext(ps.site);
  const base = {
    feats: S.roomFeatures(ps.rooms), n: ps.n, zv, zn, tgt: [tq, ta], present,
    cap: ps.capv, allowed: ps.allowed.flat(), temperature,
  };
  const rng = new PyRandom(seed ?? Math.floor(Math.random() * 2 ** 31));
  const greedy = net.generate({ ...base, B: 1, greedy: true }).zones;
  const sampled = net.generate({ ...base, B: Math.max(nSamples | 0, 1), greedy: false, rng }).zones;
  const all = [...greedy, ...sampled];
  const tables = [], pool = [], seen = new Map();
  all.forEach((z, si) => {
    const zones1 = Array.from(z, (v) => v + 1), key = zones1.join(",");
    const cached = seen.get(key);
    if (cached) { pool.push({ ...cached }); return; }
    const t = ps.table(zones1, [tq, ta], present, si === 0 ? "greedy" : "sampled");
    const c = t.computed;
    const metrics = { qvci: c.qvci, aqi: c.aqi, concentration: c.concentration, d_target: c.d_target, valid: c.valid };
    seen.set(key, metrics); pool.push({ ...metrics }); tables.push(t);
  });
  if (present) tables.sort((a, b) => a.computed.d_target - b.computed.d_target || (a.decode !== "greedy") - (b.decode !== "greedy"));
  else tables.sort((a, b) => (a.decode !== "greedy") - (b.decode !== "greedy") || (b.computed.qvci + b.computed.aqi) - (a.computed.qvci + a.computed.aqi));
  tables.forEach((t, i) => { t.table_index = i; });
  return { tables, pool, n_decodes: all.length };
}
