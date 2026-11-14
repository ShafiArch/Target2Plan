// store.js: the session state (one source of truth) and its change events.
// Field names follow the prototype's dataclasses so sessions read the same.

export const ZONED_TYPES = ["living", "kitchen", "bedroom", "bathroom"];
export const EXTRA_TYPES = ["balcony"];
export const ROOM_TYPES_UI = ["living", "bedroom", "kitchen", "bathroom", "balcony"];

export function defaultInputs() {
  return {
    room_counts: { living: 1, bedroom: 2, kitchen: 1, bathroom: 1, balcony: 1 },
    plan_geometry: { width: 12.0, height: 9.0, units: "m", px_per_m: 100.0 },
    site_conditions: {
      N: { view: "Vegetation", spl_dB: 55.0 }, S: { view: "Urban", spl_dB: 70.0 },
      E: { view: "No View", spl_dB: 65.0 }, W: { view: "Sky", spl_dB: 60.0 },
    },
    aspect_ratios: { living: 0.9, kitchen: 1.0, bedroom: 1.0, bathroom: 1.2, balcony: 2.5 },
    wwr: { living: 0.25, kitchen: 0.45, bedroom: 0.35, bathroom: 0.2, balcony: 0.6 },
    prefs: { target_qvci: 0.7, target_aqi: 0.6, targets_enabled: true, capacity_override: null, interior_bathrooms_only: true },
    params: { n_plans: 12, samples_n: 200, temperature: 0.8, zone_samples: 80 },
  };
}

const emptyStages = () => ({
  stage1: { candidates: [], selected_idx: null },
  stage2: { tables: [], selected_idxs: [], sample_pool: [], n_decodes: 0, error: null },
  stage3: { graphs: [], selected_idxs: [] },
  stage4: { schematics: [], selected_idxs: [] },
});

class Store {
  constructor() {
    this.listeners = new Map();
    this.inputs = defaultInputs();
    Object.assign(this, emptyStages());
    this.drawnRooms = null;                 // the Stage 1 drawing (metres), kept for sessions
    this.ui = { theme: "white" };
    this.epoch = 0;
  }

  on(evt, fn) { if (!this.listeners.has(evt)) this.listeners.set(evt, new Set()); this.listeners.get(evt).add(fn); return () => this.listeners.get(evt).delete(fn); }
  emit(...evts) { for (const e of [...evts, "state"]) for (const fn of this.listeners.get(e) || []) fn(); }

  inputsChanged() { this.emit("inputs"); }
  setStage1(candidates, selectedIdx = null) { this.stage1 = { candidates, selected_idx: selectedIdx }; this.emit("stage1"); }
  setStage1Selection(idx) { this.stage1.selected_idx = idx; this.emit("stage1"); }
  setStage2(tables, selected, pool, nDecodes, error = null) { this.stage2 = { tables, selected_idxs: selected, sample_pool: pool, n_decodes: nDecodes, error }; this.emit("stage2"); }
  setStage2Selection(idxs) { this.stage2.selected_idxs = [...new Set(idxs)].sort((a, b) => a - b); this.emit("stage2"); }
  setStage3(graphs, selected) { this.stage3 = { graphs, selected_idxs: selected }; this.emit("stage3"); }
  setStage3Selection(idxs) { this.stage3.selected_idxs = [...new Set(idxs)].sort((a, b) => a - b); this.emit("stage3"); }
  setStage4(schematics, selected) { this.stage4 = { schematics, selected_idxs: selected }; this.emit("stage4"); }
  setStage4Selection(idxs) { this.stage4.selected_idxs = [...new Set(idxs)].sort((a, b) => a - b); this.emit("stage4"); }

  selectedVariation() {
    const { candidates, selected_idx } = this.stage1;
    if (selected_idx == null) return null;
    return candidates.find((c) => Number(c.variation_idx) === selected_idx) || null;
  }

  statuses() {
    const st = (populated, selected) => (selected ? "done" : populated ? "pending" : "idle");
    const total = Object.values(this.inputs.room_counts).reduce((a, b) => a + Number(b || 0), 0);
    return {
      inputs: total > 0 ? "done" : "idle",
      area: st(this.stage1.candidates.length > 0, this.stage1.selected_idx != null),
      zones: st(this.stage2.tables.length > 0, this.stage2.selected_idxs.length > 0),
      connectivity: st(this.stage3.graphs.length > 0, this.stage3.selected_idxs.length > 0),
      schematic: st(this.stage4.schematics.length > 0, this.stage4.selected_idxs.length > 0),
    };
  }

  // A reset or a loaded session starts a new epoch: model runs that were
  // still in flight compare epochs and drop their stale results. Neither
  // emits "inputs", so nothing auto-regenerates over the restored stages.
  reset() {
    this.epoch++;
    this.inputs = defaultInputs();
    Object.assign(this, emptyStages());
    this.drawnRooms = null;
    this.emit("reset", "stage1", "stage2", "stage3", "stage4");
  }

  // ── sessions ──────────────────────────────────────────────────────
  toSession() {
    const strip = (v) => JSON.parse(JSON.stringify(v, (k, x) => (k.startsWith("_") ? undefined : x)));
    return {
      app: "Target2Plan", format: "target2plan-session", version: 1, saved: new Date().toISOString(),
      inputs: strip(this.inputs), drawn_rooms: strip(this.drawnRooms),
      stage1: strip(this.stage1), stage2: strip(this.stage2), stage3: strip(this.stage3),
      stage4: { generated: this.stage4.schematics.length > 0, selected_idxs: [...this.stage4.selected_idxs] },
      ui: { theme: this.ui.theme },
    };
  }

  /** Load a saved session. Returns the Stage 4 selection when Stage 4 should be
   *  rebuilt from Stages 2 and 3 (it is not stored), otherwise null. */
  fromSession(s) {
    if (!s || typeof s !== "object" || !s.inputs) throw new Error("Not a Target2Plan session file.");
    const d = defaultInputs();
    const inp = s.inputs;
    this.inputs = {
      ...d, ...inp,
      room_counts: { ...inp.room_counts },
      plan_geometry: { ...d.plan_geometry, ...inp.plan_geometry },
      site_conditions: { ...d.site_conditions, ...inp.site_conditions },
      aspect_ratios: { ...d.aspect_ratios, ...inp.aspect_ratios },
      wwr: { ...d.wwr, ...inp.wwr },
      prefs: { ...d.prefs, ...inp.prefs },
      params: { ...d.params, ...inp.params },
    };
    this.drawnRooms = Array.isArray(s.drawn_rooms) ? s.drawn_rooms : null;
    const e = emptyStages();
    this.stage1 = { ...e.stage1, ...(s.stage1 || {}) };
    this.stage2 = { ...e.stage2, ...(s.stage2 || {}) };
    this.stage3 = { ...e.stage3, ...(s.stage3 || {}) };
    this.stage4 = e.stage4;
    this.epoch++;
    this.emit("reset", "stage1", "stage2", "stage3", "stage4");
    return s.stage4 && s.stage4.generated ? s.stage4.selected_idxs || [] : null;
  }
}

export const store = new Store();
