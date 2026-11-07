// stage0.js: Inputs (targets, reachable range, site, WWR, zone capacity,
// generation parameters, payload). Room counts, plan geometry and aspect
// ratios live in the generator strip of Stage 1, as in the desktop app.
import { h, section, sliderRow, roomSliderRow, numberInput, debounce } from "./common.js";
import { reachableRange, VIEW_OPTIONS } from "../engine/zones.js";
import { capacity } from "../engine/scoring.js";
import { defaultInputs, ZONED_TYPES } from "../store.js";

const DIRS = ["N", "S", "E", "W"];
const WWR_TYPES = ["living", "bedroom", "kitchen", "bathroom", "balcony"];

export function mountInputs(body, { store, goRoomProgram }) {
  const inp = () => store.inputs;
  const changed = () => { refreshPayload(); refreshReach(); refreshCapHint(); store.inputsChanged(); };

  // ── Targets ───────────────────────────────────────────────────────
  const chk = h("input", { type: "checkbox", checked: inp().prefs.targets_enabled });
  const rowQ = sliderRow("Target QVCI", "Quality View Coverage Index", inp().prefs.target_qvci, 0, 1, 0.01, (v) => { inp().prefs.target_qvci = v; changed(); });
  const rowA = sliderRow("Target AQI", "Acoustic Quality Index", inp().prefs.target_aqi, 0, 1, 0.01, (v) => { inp().prefs.target_aqi = v; changed(); });
  chk.addEventListener("change", () => { inp().prefs.targets_enabled = chk.checked; rowQ.setEnabled(chk.checked); rowA.setEnabled(chk.checked); changed(); });
  const reach = h("div.reach", "-");
  const future = ["Daylight Index", "Thermal Index"].map((n) => {
    const r = sliderRow(n, `${n} · future update`, 0.5, 0, 1, 0.01, () => {}, { disabled: true });
    r.title = "Planned conditioning index, not part of the v13 model.";
    return r;
  });
  const secTargets = section("TARGETS", "Requested plan-level QVCI · AQI (conditions the v13 model)", true,
    h("label.check", chk, "Condition on targets (off = unconditioned sampling)"),
    rowQ, rowA,
    h("div.field-label", { style: { marginTop: "6px" } }, "Reachable range"), reach,
    h("div.field-label", { style: { marginTop: "8px" } }, "Future updates"), ...future);

  // ── Site conditions ───────────────────────────────────────────────
  const siteRows = h("div.site-rows");
  const viewSel = {}, splIn = {};
  for (const d of DIRS) {
    const side = inp().site_conditions[d];
    const sel = h("select", ...VIEW_OPTIONS.map((v) => h("option", { value: v, selected: v === side.view }, v)));
    sel.addEventListener("change", () => { inp().site_conditions[d].view = sel.value; changed(); });
    const spl = numberInput(side.spl_dB, { min: 30, max: 80, step: 1, suffix: "dB(A)", onChange: (v) => { inp().site_conditions[d].spl_dB = v; changed(); } });
    spl.title = "Outdoor A-weighted level at this façade. The corpus covers 30 to 80 dB(A).";
    viewSel[d] = sel; splIn[d] = spl;
    siteRows.append(h("span.d", d), sel, spl);
  }
  const compass = h("div.compass",
    h("span.l", { style: { left: "45px", top: "4px" } }, "N"), h("div.sq"),
    h("span.l", { style: { left: "45px", top: "84px" } }, "S"),
    h("span.l", { style: { left: "2px", top: "44px" } }, "W"), h("span.l", { style: { left: "90px", top: "44px" } }, "E"),
    h("div.cap", "ORIENTATION"));
  const secSite = section("SITE CONDITIONS", "View category & outdoor level per façade (30 to 80 dB(A))", true, h("div.site-grid", compass, siteRows));

  // ── Planned WWR ───────────────────────────────────────────────────
  const wwrRows = {};
  const secWwr = section("PLANNED WINDOW-TO-WALL RATIO", "Per room type · full view credit at 0.40", false,
    h("div.hint", "Planned glazing fraction of the room's exterior wall, a design requirement applied to every façade of the zone the room is placed in. View credit rises linearly to 1 at WWR 0.40; more glazing also lets more outdoor noise in."),
    ...WWR_TYPES.map((t) => (wwrRows[t] = roomSliderRow(t, inp().wwr[t] ?? 0.15, 0, 1, 0.01, (v) => { inp().wwr[t] = v; changed(); }))));

  // ── Zone capacity ─────────────────────────────────────────────────
  const capHint = h("div.hint");
  const capChk = h("input", { type: "checkbox", checked: !!inp().prefs.capacity_override });
  const capSpins = {};
  const capGrid = h("div.cap-grid");
  for (const [cls, label] of [["corner", "Corner zones"], ["cardinal", "Edge zones"], ["interior", "Interior zone"]]) {
    capSpins[cls] = numberInput(2, { min: 1, max: 14, step: 1, width: 64, onChange: (v) => { if (inp().prefs.capacity_override) { inp().prefs.capacity_override[cls] = v; changed(); } } });
    capGrid.append(h("span.field-label", label), capSpins[cls]);
  }
  const autoCap = () => capacity(ZONED_TYPES.reduce((s, t) => s + Number(inp().room_counts[t] || 0), 0));
  const syncCap = () => {
    const ov = inp().prefs.capacity_override, auto = autoCap();
    for (const cls of Object.keys(capSpins)) { capSpins[cls].set(ov ? ov[cls] ?? auto[cls] : auto[cls]); capSpins[cls].input.disabled = !ov; }
    capChk.checked = !!ov;
  };
  capChk.addEventListener("change", () => {
    inp().prefs.capacity_override = capChk.checked ? { ...autoCap() } : null;
    syncCap(); changed();
  });
  const ibo = h("input", { type: "checkbox", checked: inp().prefs.interior_bathrooms_only });
  ibo.addEventListener("change", () => { inp().prefs.interior_bathrooms_only = ibo.checked; changed(); });
  const secCap = section("ZONE CAPACITY", "Rooms per zone · trained rule by plan size (advanced)", false,
    capHint, h("label.check", capChk, "Override the rule"), capGrid, h("label.check", ibo, "Habitable rooms never interior (bathrooms only)"),
    h("div.hint", "The model was trained with the rule; overrides are for exploration."));

  // ── Generation parameters ─────────────────────────────────────────
  const par = inp().params;
  const pPlans = numberInput(par.n_plans, { min: 1, max: 24, step: 1, onChange: (v) => { inp().params.n_plans = v; changed(); } });
  const pTemp = numberInput(par.temperature, { min: 0.1, max: 1.5, step: 0.05, onChange: (v) => { inp().params.temperature = v; changed(); } });
  pTemp.title = "Sampling temperature of the zone decoder. The paper's protocol used 0.8; lower is closer to the greedy decode, higher is more diverse.";
  const pSamp = numberInput(par.zone_samples, { min: 8, max: 400, step: 8, onChange: (v) => { inp().params.zone_samples = v; changed(); } });
  pSamp.title = "Sampled arrangements per request (plus one greedy decode). The paper reports best-of-12.";
  const pArea = numberInput(par.samples_n, { min: 20, max: 1000, step: 20, onChange: (v) => { inp().params.samples_n = v; changed(); } });
  const secGen = section("GENERATION PARAMETERS", "Plans shown · sampling pool · temperature", false,
    h("div.param-grid", h("span.field-label", "Num tables"), pPlans, h("span.field-label", "Temperature"), pTemp,
      h("span.field-label", "Samples"), pSamp, h("span.field-label", "Area samples"), pArea));

  // ── Payload (debug) ───────────────────────────────────────────────
  const payload = h("pre.payload");
  const secPay = section("ASSEMBLED PAYLOAD (DEBUG)", "What the models receive", false, payload);

  function refreshPayload() {
    const ip = inp(), pg = ip.plan_geometry;
    const scale = pg.units === "ft" ? 0.3048 : pg.units === "px" ? 1 / Math.max(pg.px_per_m, 1e-6) : 1;
    const zoned = ZONED_TYPES.reduce((s, t) => s + Number(ip.room_counts[t] || 0), 0);
    payload.textContent = JSON.stringify({
      model: "zone v13 (cGAT-AR, outcome-labelled corpus)",
      room_counts: ip.room_counts,
      plan_geometry: { ...pg, total_area: pg.width * pg.height, total_area_m2: Number((pg.width * pg.height * scale * scale).toFixed(2)) },
      site_conditions: Object.fromEntries(DIRS.map((d) => [d, { view: ip.site_conditions[d].view, L_dBA: ip.site_conditions[d].spl_dB }])),
      aspect_ratios: ip.aspect_ratios, wwr_planned: ip.wwr,
      targets: { qvci: ip.prefs.target_qvci, aqi: ip.prefs.target_aqi, enabled: ip.prefs.targets_enabled },
      capacity: { override: ip.prefs.capacity_override, rule_for_plan: capacity(zoned, ip.prefs.capacity_override), interior_bathrooms_only: ip.prefs.interior_bathrooms_only },
      params: ip.params,
    }, null, 2);
  }

  function refreshReach() {
    const av = store.selectedVariation();
    if (!av) { reach.textContent = "Pick an area variation in Stage 1; the range depends on the room areas."; return; }
    const rr = reachableRange(inp(), av);
    if (!rr) { reach.textContent = "No zoned rooms in the selected variation."; return; }
    const p = inp().prefs, [q0, q1] = rr.qvci, [a0, a1] = rr.aqi;
    const mark = (v, lo, hi) => (v >= lo - 1e-9 && v <= hi + 1e-9 ? "" : ` <span class="acc">· target outside</span>`);
    let html = `QVCI <b>${q0.toFixed(2)} - ${q1.toFixed(2)}</b>${mark(p.target_qvci, q0, q1)}<br>AQI <b>${a0.toFixed(2)} - ${a1.toFixed(2)}</b>${mark(p.target_aqi, a0, a1)}`;
    if (rr.aqi_collapsed) html += `<br><span class="acc">AQI cannot be changed on this site (reachable width ${(a1 - a0).toFixed(2)}): every façade gives about the same indoor level.</span>`;
    else if (!rr.aqi_live) html += `<br>AQI range is narrow (${(a1 - a0).toFixed(2)}); view will dominate.`;
    const c = rr.capacity;
    html += `<br><span style="opacity:.75">${rr.n_zoned} zoned rooms · capacity corner ${c.corner} · edge ${c.cardinal} · interior ${c.interior}</span>`;
    reach.innerHTML = html;
  }

  function refreshCapHint() {
    const n = ZONED_TYPES.reduce((s, t) => s + Number(inp().room_counts[t] || 0), 0), a = capacity(n);
    capHint.textContent = `Trained rule for ${n} zoned rooms: corner ≤ ${a.corner}, edge ≤ ${a.cardinal}, interior ≤ ${a.interior} (step at 8 rooms); never every room in one zone.`;
    if (!inp().prefs.capacity_override) syncCap();
  }

  // full re-sync from the store (reset / session load)
  function syncAll() {
    const ip = inp();
    chk.checked = ip.prefs.targets_enabled;
    rowQ.set(ip.prefs.target_qvci); rowA.set(ip.prefs.target_aqi);
    rowQ.setEnabled(ip.prefs.targets_enabled); rowA.setEnabled(ip.prefs.targets_enabled);
    for (const d of DIRS) { viewSel[d].value = ip.site_conditions[d].view; splIn[d].set(ip.site_conditions[d].spl_dB); }
    for (const t of WWR_TYPES) wwrRows[t].set(ip.wwr[t] ?? 0.15);
    ibo.checked = ip.prefs.interior_bathrooms_only;
    pPlans.set(ip.params.n_plans); pTemp.set(ip.params.temperature); pSamp.set(ip.params.zone_samples); pArea.set(ip.params.samples_n);
    syncCap(); refreshPayload(); refreshReach(); refreshCapHint();
  }

  const scroll = h("div.scroll", { style: { padding: "8px 14px" } }, secTargets, secSite, secWwr, secCap, secGen, secPay);
  const resetBtn = h("button.link-btn", { on: { click: () => {
    if (!confirm("Reset all inputs to defaults?")) return;
    store.inputs = defaultInputs();
    store.emit("inputsReset");
    store.inputsChanged();
  } } }, "Reset to defaults");
  const goBtn = h("button.btn-primary", { on: { click: goRoomProgram } }, "Room program  →");
  body.append(scroll, h("div.toolbar", resetBtn, h("div.grow"), goBtn));

  const refreshSoon = debounce(() => { refreshReach(); refreshPayload(); refreshCapHint(); }, 60);
  store.on("stage1", refreshReach);
  store.on("inputs", refreshSoon);
  store.on("reset", syncAll);
  store.on("inputsReset", syncAll);
  syncAll();
}
