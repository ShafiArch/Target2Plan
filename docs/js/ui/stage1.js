// stage1.js: Area Distribution. Two modes, as in the prototype:
//   Draw room program (default): rooms drawn on a dot grid become the variation.
//   Generate area distributions (optional): the area model samples variations from the
//   room counts and the plan area (room counts, plan geometry and aspect
//   ratios sit in a strip above the cards).
import { h, clear, section, roomSliderRow, numberInput, palette, roomColor, fitCanvas, onResize, emptyState, toast, FONT, pad2, hexA, DESKTOP } from "./common.js";
import { GeometryCanvas, roomPalette, SQFT_PER_M2, MOD_KEY } from "./geometry.js";
import { packCirclesNatural } from "../engine/area.js";
import { unitScaleToM } from "../engine/zones.js";
import { pyRound } from "../engine/pymath.js";
import { ZONED_TYPES, ROOM_TYPES_UI } from "../store.js";

const SHORT = { living: "liv", bedroom: "bed", kitchen: "kit", bathroom: "bat", dining: "din", balcony: "bal", storage: "sto", study: "stu" };
// full screen icons: four corners pointing out (enter) and in (exit)
const FS_ICON = (d) => `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
const FS_ENTER = FS_ICON("M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4");
const FS_EXIT = FS_ICON("M6 2v4H2M10 2v4h4M14 10h-4v4M2 10h4v4");
const LS = (k, d) => { try { const v = localStorage.getItem(`t2p.${k}`); return v == null ? d : JSON.parse(v); } catch { return d; } };
const SS = (k, v) => { try { localStorage.setItem(`t2p.${k}`, JSON.stringify(v)); } catch { /* storage unavailable */ } };

export function mountArea(body, { store, runtime, goto }) {
  let mode = LS("areaMode", "draw");
  const prefs = { labels: LS("areaLabels", true), fill: LS("areaFill", false), useSeed: LS("areaUseSeed", false), seed: LS("areaSeed", 42), n: LS("areaN", 12), div: LS("areaDiv", 2.5), method: LS("areaMethod", "Farthest") };
  let busy = false;

  // ── mode bar ──────────────────────────────────────────────────────
  // the buttons carry a long label (desktop) and a short one (phone); CSS shows one of them
  const bDraw = h("button", { title: "Draw the rooms and their sizes yourself", on: { click: () => setMode("draw") } }, h("span.long", "Draw room program"), h("span.short", "Draw"));
  const bGen = h("button", { title: "Optional: let the trained area model sample the room areas from the room counts and the plan area. Drawing the rooms works without it.", on: { click: () => setMode("generate") } }, h("span.long", "Generate area distributions (optional)"), h("span.short", "Generate (optional)"));
  const modeHint = h("span.hint");
  const genTop = h("button.btn-primary", { title: "Sample area variations from the room counts and the plan area", on: { click: () => regenerate() } }, "Generate");
  const modeBar = h("div.mode-bar", h("div.mode-pill", bDraw, bGen), modeHint, genTop);

  // ── generator strip: room counts, plan geometry, aspect ratios ────
  const counterRows = {};
  const roomTotal = h("span");
  const counts = section("ROOM COUNTS", "", true);
  counts.querySelector(".st")?.remove();
  counts.querySelector(".section-head > div").append(h("div.st", roomTotal));
  for (const rt of ROOM_TYPES_UI) {
    const inp = h("input", { type: "number", min: 0, max: 99, value: store.inputs.room_counts[rt] ?? 0 });
    const set = (v) => { const n = Math.max(0, Math.min(99, Math.round(Number(v) || 0))); inp.value = n; store.inputs.room_counts[rt] = n; refreshTotals(); store.inputsChanged(); };
    inp.addEventListener("change", () => set(inp.value));
    counterRows[rt] = inp;
    counts.body.append(h("div.counter", h("span.swatch", { "data-rt": rt, style: { background: roomColor(rt) } }), h("span.field-label", rt), h("button", { on: { click: () => set(Number(inp.value) - 1) } }, "−"), inp, h("button", { on: { click: () => set(Number(inp.value) + 1) } }, "+")));
  }
  counts.body.append(h("div.hint", { title: "Balconies are a façade view category rather than a zoned room: they are not placed by the zone model." }, "Balcony joins the plan at the connectivity stage"));

  const pg = () => store.inputs.plan_geometry;
  const unitBtns = {};
  const unitPill = h("div.mode-pill", ...["px", "m", "ft"].map((u) => (unitBtns[u] = h("button", { on: { click: () => { pg().units = u; syncPlan(); store.inputsChanged(); } } }, u))));
  const spW = numberInput(pg().width, { min: 1, max: 100000, step: 0.1, width: 110, onChange: (v) => { pg().width = v; syncPlan(); store.inputsChanged(); } });
  const spH = numberInput(pg().height, { min: 1, max: 100000, step: 0.1, width: 110, onChange: (v) => { pg().height = v; syncPlan(); store.inputsChanged(); } });
  const spPx = numberInput(pg().px_per_m, { min: 1, max: 10000, step: 1, width: 110, onChange: (v) => { pg().px_per_m = v; syncPlan(); store.inputsChanged(); } });
  const pxRow = h("div.param-grid", h("span.field-label", "Pixels per metre"), spPx);
  const totalLbl = h("span.mono");
  const plan = section("PLAN GEOMETRY", "", true,
    h("div.param-grid", h("span.field-label", "Units"), unitPill, h("span.field-label", "Width (E-W)"), spW, h("span.field-label", "Height (N-S)"), spH), pxRow,
    h("div.param-grid", h("span.field-label", "Total area"), totalLbl));
  const arRows = {};
  const aspect = section("ASPECT RATIOS", "Shape per room type (N-S ÷ E-W)", true,
    h("div.hint", "AR > 1 = taller (N-S) than wide (E-W). Applies to every room of that type; sets the façade lengths the acoustic model sees."),
    ...ROOM_TYPES_UI.map((t) => (arRows[t] = roomSliderRow(t, store.inputs.aspect_ratios[t] ?? 1, 0.5, 3, 0.05, (v) => { store.inputs.aspect_ratios[t] = v; store.inputsChanged(); }))));
  const genInputs = h("div.gen-inputs", counts, plan, aspect);

  function refreshTotals() {
    const total = Object.values(store.inputs.room_counts).reduce((a, b) => a + Number(b || 0), 0);
    roomTotal.textContent = `${total} rooms total`;
  }
  function syncPlan() {
    for (const [u, b] of Object.entries(unitBtns)) b.classList.toggle("active", pg().units === u);
    spW.set(pg().width); spH.set(pg().height); spPx.set(pg().px_per_m);
    pxRow.classList.toggle("hidden", pg().units !== "px");
    const u = pg().units;
    totalLbl.textContent = `${(pg().width * pg().height).toLocaleString(undefined, { maximumFractionDigits: 1 })}  ${u}²`;
    geom.setPlan(pg().width * unitScaleToM(u, pg().px_per_m), pg().height * unitScaleToM(u, pg().px_per_m));
  }

  // ── draw panel ────────────────────────────────────────────────────
  const geomWrap = h("div.geom-wrap");
  const drawInfo = h("div.draw-info", "No rooms yet.");
  const geom = new GeometryCanvas(geomWrap, () => { store.drawnRooms = geom.rooms.map((r) => ({ ...r })); refreshDrawInfo(); });
  geomWrap.geom = geom;   // reachable from the page (tests, console)
  const um = h("button", { on: { click: () => setDrawUnits("m") } }, "m²"), uf = h("button", { on: { click: () => setDrawUnits("ft") } }, "ft²");
  // zoom: buttons here, wheel or pinch on the canvas, keys + / - / 0 when it has focus
  const zoomPct = h("button.zoom-pct", { title: "Back to 100 %", on: { click: () => geom.setScale(GeometryCanvas.BASE_PX_PER_M) } }, "100%");
  const zoomCtl = h("div.zoom-ctl", { role: "group", "aria-label": "Canvas zoom" },
    h("button", { title: "Zoom out (-)", "aria-label": "Zoom out", on: { click: () => geom.zoomBy(1 / 1.25) } }, "−"),
    zoomPct,
    h("button", { title: "Zoom in (+)", "aria-label": "Zoom in", on: { click: () => geom.zoomBy(1.25) } }, "+"),
    h("button.fit", { title: "Fit the rooms in view (0)", on: { click: () => geom.fit() } }, "Fit"),
    h("button.zoom-fs", { title: "Full screen (Esc to leave)", "aria-label": "Full screen", html: FS_ENTER, on: { click: () => setFull(true) } }));
  geom.onZoom = () => { zoomPct.textContent = `${geom.zoomPercent()}%`; };
  // undo / redo and snapping (the keyboard shortcuts work on the canvas too)
  const bUndo = h("button", { title: `Undo (${MOD_KEY}+Z)`, "aria-label": "Undo", disabled: true, on: { click: () => geom.undo() } }, "↶");
  const bRedo = h("button", { title: `Redo (${MOD_KEY}+Shift+Z)`, "aria-label": "Redo", disabled: true, on: { click: () => geom.redo() } }, "↷");
  const bSnap = h("button.snap" + (geom.snapOn ? ".on" : ""), { title: "Snap edges and centres to the other rooms (hold Alt to place freely)", "aria-pressed": String(geom.snapOn), on: { click: () => { geom.setSnap(!geom.snapOn); bSnap.classList.toggle("on", geom.snapOn); bSnap.setAttribute("aria-pressed", String(geom.snapOn)); } } }, "Snap");
  const editCtl = h("div.zoom-ctl.edit-ctl", { role: "group", "aria-label": "Edit" }, bUndo, bRedo, bSnap);
  geom.onHistory = () => { bUndo.disabled = !geom.canUndo(); bRedo.disabled = !geom.canRedo(); };
  // units and zoom sit in the draw panel; on phones they move up next to the mode pill
  const drawCtl = h("div.draw-ctl", editCtl, h("div.mode-pill", um, uf), zoomCtl);
  const bMin = h("button.btn-ghost.fs-min", { title: "Back to the normal view (Esc)", html: `${FS_EXIT}<span class="long">Exit full screen</span><span class="short">Exit</span>`, on: { click: () => setFull(false) } });
  const drawTop = h("div.draw-top", roomPalette(geom), drawCtl, bMin);
  const drawPanel = h("div.draw-panel", drawTop, geomWrap, drawInfo);
  const phone = window.matchMedia ? window.matchMedia("(max-width: 760px)") : null;
  function placeDrawCtl() { if (phone && phone.matches) modeBar.append(drawCtl); else drawTop.insertBefore(drawCtl, bMin); }
  placeDrawCtl();
  phone?.addEventListener("change", placeDrawCtl);
  // Full screen drawing: the canvas fills the window with the room chips, zoom and
  // Exit. On a computer the screen goes full screen as well (the browser's
  // Fullscreen API, or the desktop app's window); on a phone the canvas fills the page.
  let screenFull = false;   // true while the drawing holds the browser or the window in full screen
  const escKey = (e) => { if (e.key === "Escape") { e.preventDefault(); setFull(false); } };
  function setFull(on) {
    if (drawPanel.classList.contains("fullscreen") === on) return;
    drawPanel.classList.toggle("fullscreen", on);
    if (on) { window.addEventListener("keydown", escKey); if (!(phone && phone.matches)) enterScreen(); }
    else { window.removeEventListener("keydown", escKey); leaveScreen(); }
    requestAnimationFrame(() => geom.centre());
  }
  function enterScreen() {
    if (DESKTOP) { screenFull = true; DESKTOP.window.getCurrentWindow().setFullscreen(true).catch(() => { screenFull = false; }); return; }
    const el = document.documentElement, req = el.requestFullscreen || el.webkitRequestFullscreen;
    if (!req) return;   // the canvas still fills the page
    try { screenFull = true; Promise.resolve(req.call(el)).catch(() => { screenFull = false; }); } catch { screenFull = false; }
  }
  function leaveScreen() {
    if (!screenFull) return;
    screenFull = false;
    if (DESKTOP) { DESKTOP.window.getCurrentWindow().setFullscreen(false).catch(() => {}); return; }
    const exit = document.exitFullscreen || document.webkitExitFullscreen;
    if ((document.fullscreenElement || document.webkitFullscreenElement) && exit) Promise.resolve(exit.call(document)).catch(() => {});
  }
  // leaving the browser's full screen some other way (Esc, F11, the browser's own button) ends the drawing view too
  const fsChange = () => { if (screenFull && !(document.fullscreenElement || document.webkitFullscreenElement)) { screenFull = false; setFull(false); } };
  document.addEventListener("fullscreenchange", fsChange);
  document.addEventListener("webkitfullscreenchange", fsChange);
  function setDrawUnits(u) { geom.setUnits(u); um.classList.toggle("active", u === "m"); uf.classList.toggle("active", u === "ft"); refreshDrawInfo(); }

  function refreshDrawInfo() {
    const rooms = geom.rooms;
    if (!rooms.length) { drawInfo.textContent = "No rooms yet."; bUse.disabled = true; return; }
    const c = geom.counts(), total = geom.totalAreaM2(), u = geom.units;
    const sc = unitScaleToM(pg().units, pg().px_per_m), planM2 = pg().width * pg().height * sc * sc;
    const fa = (a) => (u === "m" ? `${a.toFixed(1)} m²` : `${(a * SQFT_PER_M2).toFixed(0)} ft²`);
    const parts = ROOM_TYPES_UI.filter((t) => c[t]).map((t) => `${c[t]} ${t}`);
    drawInfo.textContent = `${rooms.length} rooms · ${parts.join(", ")} · drawn area ${fa(total)} (plan ${fa(planM2)})`;
    bUse.disabled = !(c.living || c.bedroom || c.kitchen);
  }

  function useDrawnLayout() {
    const rooms = geom.rooms;
    if (!rooms.length) return;
    const units = pg().units, scale = unitScaleToM(units, pg().px_per_m);
    let totalUnits = 0;
    const out = rooms.map((r) => {
      const areaM2 = r.w * r.h, areaUnits = areaM2 / (scale * scale);
      totalUnits += areaUnits;
      return { room_id: r.id, room_type: r.type, area: areaUnits, area_m2: pyRound(areaM2, 2), w_m: pyRound(r.w, 3), h_m: pyRound(r.h, 3), aspect_ratio: r.w > 0 ? r.h / r.w : 1, radius: Math.sqrt(Math.max(areaUnits, 1e-6) / Math.PI) };
    });
    packCirclesNatural(out, 42);
    const c = geom.counts();
    const share = {};
    for (const r of out) share[r.room_type] = (share[r.room_type] || 0) + r.area;
    if (totalUnits > 0) for (const k of Object.keys(share)) share[k] /= totalUnits;
    const variation = { variation_idx: 0, rooms: out, total_area: totalUnits, units, area_share_by_type: share, counts_by_type: { ...c }, drawn: true };
    const totalM2 = rooms.reduce((s, r) => s + r.w * r.h, 0);
    const asp = pg().height > 0 ? pg().width / pg().height : 4 / 3;
    pg().width = pyRound(Math.sqrt(totalM2 * asp) / scale, 1); pg().height = pyRound(Math.sqrt(totalM2 / asp) / scale, 1);
    for (const rt of ROOM_TYPES_UI) { store.inputs.room_counts[rt] = Number(c[rt] || 0); counterRows[rt].value = store.inputs.room_counts[rt]; }
    refreshTotals(); syncPlan();
    store.inputsChanged();
    store.setStage1([variation], 0);
    toast("Layout set as the area distribution. Proceed to Zones to place it.");
  }

  // ── generate content ──────────────────────────────────────────────
  const genScroll = h("div.scroll", { style: { padding: "12px 12px 8px" } });

  async function regenerate() {
    const c = store.inputs.room_counts;
    const total = Object.values(c).reduce((a, b) => a + Number(b || 0), 0);
    const zoned = ZONED_TYPES.reduce((s, t) => s + Number(c[t] || 0), 0);
    if (total <= 0) return toast("Add at least one room before generating.");
    if (zoned <= 0) return toast("Add at least one living, kitchen, bedroom or bathroom; balconies alone cannot be zoned.");
    if (zoned > 14) return toast("The zone model was trained on plans with up to 14 zoned rooms.");
    busy = true; renderGen();
    const ep = store.epoch;
    try {
      const seed = prefs.useSeed ? Number(prefs.seed) : Math.floor(Math.random() * 2 ** 31);
      const vars = await runtime.run("area", { counts: { ...c }, totalArea: pg().width * pg().height, opts: { nVariations: prefs.n, nSamples: store.inputs.params.samples_n, diversity: prefs.div, seed, method: prefs.method } });
      if (ep !== store.epoch) return;
      for (const v of vars) { v.units = pg().units; v.counts_by_type = { ...c }; }
      store.setStage1(vars, null);
    } catch (err) {
      toast(`Area model failed: ${err.message}`);
    } finally { busy = false; renderGen(); }
  }

  function areaCard(v, sel) {
    const cv = h("canvas");
    const parts = [];
    const counts = v.counts_by_type || {};
    for (const rt of ["living", "bedroom", "kitchen", "bathroom", "balcony"]) if (counts[rt]) parts.push(`${counts[rt]} ${SHORT[rt] || rt.slice(0, 3)}`);
    const card = h("div.area-card" + (sel === v.variation_idx ? ".selected" : sel != null ? ".dimmed" : ""),
      { on: { click: () => store.setStage1Selection(store.stage1.selected_idx === v.variation_idx ? null : v.variation_idx) } },
      h("div.card-head", h("span.idx", `#${pad2(v.variation_idx)}`), h("span.sum", parts.join(" · ")), sel === v.variation_idx ? h("span.badge", "✓ SELECTED") : null), cv);
    requestAnimationFrame(() => drawBubbles(cv, v));
    card.redraw = () => drawBubbles(cv, v);
    return card;
  }

  function drawBubbles(cv, v) {
    const { ctx, w, h: H } = fitCanvas(cv);
    const P = palette();
    const act = v.rooms.filter((r) => r.radius > 0 && r.pos_natural);
    if (!act.length) return;
    const minX = Math.min(...act.map((r) => r.pos_natural[0] - r.radius)), maxX = Math.max(...act.map((r) => r.pos_natural[0] + r.radius));
    const minY = Math.min(...act.map((r) => r.pos_natural[1] - r.radius)), maxY = Math.max(...act.map((r) => r.pos_natural[1] + r.radius));
    const pad = 8, sc = Math.min((w - 2 * pad) / Math.max(maxX - minX, 1e-6), (H - 2 * pad) / Math.max(maxY - minY, 1e-6));
    const bcx = (minX + maxX) / 2, bcy = (minY + maxY) / 2;
    for (const r of act) {
      const cx = (r.pos_natural[0] - bcx) * sc + w / 2, cy = (r.pos_natural[1] - bcy) * sc + H / 2, rad = r.radius * sc;
      const col = roomColor(r.room_type);
      ctx.beginPath();
      if (prefs.fill) { ctx.arc(cx, cy, rad, 0, 2 * Math.PI); ctx.fillStyle = hexA(col, 0.85); ctx.fill(); }
      else { ctx.arc(cx, cy, Math.max(0.5, rad - 1.5), 0, 2 * Math.PI); ctx.strokeStyle = col; ctx.lineWidth = 1.2; ctx.stroke(); }
      if (!prefs.labels || rad < 10) continue;
      const label = r.room_type[0].toUpperCase() + r.room_type.slice(1);
      ctx.font = `${prefs.fill ? "500 " : ""}9px ${FONT}`;
      if (ctx.measureText(label).width > 2 * rad - 4) continue;
      ctx.fillStyle = prefs.fill ? "#fff" : P.ink3; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      const areaTxt = `${Math.round(r.area).toLocaleString()} ${v.units || "m"}²`;
      ctx.font = `8px ${FONT}`;
      const withArea = rad >= 16 && ctx.measureText(areaTxt).width <= 2 * rad - 4;
      ctx.font = `${prefs.fill ? "500 " : ""}9px ${FONT}`;
      ctx.fillText(label, Math.round(cx), Math.round(withArea ? cy - 5 : cy));
      if (withArea) { ctx.font = `8px ${FONT}`; ctx.fillText(areaTxt, Math.round(cx), Math.round(cy + 6)); }
    }
  }

  function comparison(cands, sel) {
    const cv = h("canvas");
    const types = [...new Set(cands.flatMap((v) => v.rooms.map((r) => r.room_type)))].sort((a, b) => ROOM_TYPES_UI.indexOf(a) - ROOM_TYPES_UI.indexOf(b));
    const share = (v, t) => v.rooms.filter((r) => r.room_type === t).reduce((s, r) => s + r.area, 0) / Math.max(v.total_area, 1e-9);
    const L = 70, R = 8, T = 18;
    const layout = () => {
      const w = cv.getBoundingClientRect().width;
      return { cw: (w - L - R) / cands.length };
    };
    const draw = () => {
      const { ctx, w, h: H } = fitCanvas(cv), P = palette();
      const { cw } = layout(), rh = (H - T - 4) / Math.max(types.length, 1);
      cands.forEach((v, i) => {
        if (sel === v.variation_idx) { ctx.fillStyle = P.a("accent_soft", 0.8); ctx.fillRect(L + i * cw, 0, cw, H); }
        ctx.font = `9px ${MONOF}`; ctx.fillStyle = sel === v.variation_idx ? P.accent : P.ink3; ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(`#${pad2(v.variation_idx)}`, L + i * cw + cw / 2, 8);
      });
      types.forEach((t, k) => {
        const yc = T + k * rh + rh / 2, maxS = Math.max(...cands.map((v) => share(v, t)), 1e-9);
        ctx.font = `10px ${FONT}`; ctx.fillStyle = P.ink2; ctx.textAlign = "right"; ctx.textBaseline = "middle";
        ctx.fillText(t[0].toUpperCase() + t.slice(1), L - 8, yc);
        cands.forEach((v, i) => {
          const s = share(v, t), bw = Math.max(1, (cw - 8) * (s / maxS)), bh = Math.min(10, rh - 4);
          ctx.fillStyle = sel != null && sel !== v.variation_idx ? hexA(roomColor(t), 0.3) : roomColor(t);
          ctx.fillRect(L + i * cw + 4, yc - bh / 2, bw, bh);
        });
      });
    };
    cv.addEventListener("click", (e) => {
      const b = cv.getBoundingClientRect(), { cw } = layout(), i = Math.floor((e.clientX - b.left - L) / cw);
      if (i >= 0 && i < cands.length) { const vi = cands[i].variation_idx; store.setStage1Selection(store.stage1.selected_idx === vi ? null : vi); }
    });
    requestAnimationFrame(draw);
    cv.redraw = draw;
    cv.style.height = `${Math.max(90, 26 + types.length * 22)}px`;
    return cv;
  }
  const MONOF = getComputedStyle(document.documentElement).getPropertyValue("--mono");

  let cards = [], comp = null;
  function renderGen() {
    clear(genScroll); cards = []; comp = null;
    const { candidates, selected_idx: sel } = store.stage1;
    if (busy) { genScroll.append(h("div.empty-state", h("span.busy", "Sampling area variations…"))); return; }
    const isDrawn = candidates.length === 1 && candidates[0].drawn;
    if (!candidates.length || isDrawn) {
      genScroll.append(emptyState("Generate area distributions (optional)",
        `Sample ${prefs.n} area variations from the inputs you've set up. Each variation distributes the ${Object.values(store.inputs.room_counts).reduce((a, b) => a + Number(b || 0), 0)} rooms across the plan area using slightly different proportions. This step is optional: you can draw the rooms instead.`,
        `Generate ${prefs.n} variations`, () => regenerate()));
      return;
    }
    const grid = h("div.grid4");
    for (const v of candidates) { const c = areaCard(v, sel); cards.push(c); grid.append(c); }
    comp = comparison(candidates, sel);
    genScroll.append(grid, h("div.comparison",
      h("div.comparison-head", h("span.caret.sub", "▾"), h("span.field-label", "Area share comparison"), h("span.sub", `· ${candidates.length} variations · total ${Math.round(candidates[0].total_area).toLocaleString()} ${pg().units}² each`), h("span.grow", { style: { flex: 1 } }), h("span.sub", "Click a column to select")), comp));
  }
  onResize(genScroll, () => { cards.forEach((c) => c.redraw()); comp?.redraw(); });

  // ── toolbar ───────────────────────────────────────────────────────
  const chipText = h("button.chip" + (prefs.labels ? ".active" : ""), { title: "Show room names inside each circle", on: { click: () => { prefs.labels = !prefs.labels; SS("areaLabels", prefs.labels); chipText.classList.toggle("active", prefs.labels); cards.forEach((c) => c.redraw()); } } }, "Text");
  const chipFill = h("button.chip" + (prefs.fill ? ".active" : ""), { title: "Solid coloured fill vs outline only", on: { click: () => { prefs.fill = !prefs.fill; SS("areaFill", prefs.fill); chipFill.classList.toggle("active", prefs.fill); cards.forEach((c) => c.redraw()); } } }, "Fill");
  const seedIn = numberInput(prefs.seed, { min: 0, max: 999999999, step: 1, width: 96, onChange: (v) => { prefs.seed = v; SS("areaSeed", v); } });
  seedIn.input.disabled = !prefs.useSeed;
  const chipSeed = h("button.chip" + (prefs.useSeed ? ".active" : ""), { title: "When on, reuse the seed value for reproducible generations", on: { click: () => { prefs.useSeed = !prefs.useSeed; SS("areaUseSeed", prefs.useSeed); chipSeed.classList.toggle("active", prefs.useSeed); seedIn.input.disabled = !prefs.useSeed; } } }, "Seed");
  const nIn = numberInput(prefs.n, { min: 4, max: 24, step: 1, width: 64, onChange: (v) => { prefs.n = v; SS("areaN", v); if (!store.stage1.candidates.length) renderGen(); } });
  const divIn = numberInput(prefs.div, { min: 0.1, max: 5, step: 0.25, width: 74, onChange: (v) => { prefs.div = v; SS("areaDiv", v); } });
  divIn.title = "Diversity multiplier on the sampling noise. 1.0 = trained σ; higher gives a wider spread.";
  const methodSel = h("select", { style: { width: "86px" }, title: "Representative-selection method" }, ...["Farthest", "KMeans"].map((m) => h("option", { value: m, selected: m === prefs.method }, m)));
  methodSel.addEventListener("change", () => { prefs.method = methodSel.value; SS("areaMethod", prefs.method); });
  const genTools = [chipText, chipFill, h("span.tsep"), chipSeed, seedIn, h("span.tsep"), h("span.sub", { title: "Number of variations" }, "N"), nIn, h("span.sub", "×σ"), divIn, methodSel];
  const bRemove = h("button.link-btn", { on: { click: () => geom.removeSelected() } }, "Remove selected");
  const bClear = h("button.link-btn", { on: { click: () => geom.clear() } }, "Clear");
  const bSample = h("button.link-btn", { on: { click: () => geom.loadSample() } }, "Sample rooms");
  const bUse = h("button.btn-primary", { title: "Turn the drawn rooms into the area distribution for the next stages", on: { click: useDrawnLayout } }, "Use this layout");
  const bProceed = h("button.btn-primary", { disabled: true, on: { click: () => goto("zones") } }, "Proceed to Zones  →");
  const drawTools = [bRemove, bClear, bSample];
  const toolbar = h("div.toolbar", h("div.tools", ...genTools, ...drawTools), h("div.grow"), bUse, bProceed);

  body.append(modeBar, genInputs, genScroll, drawPanel, toolbar);

  function setMode(m) { mode = m; SS("areaMode", m); applyMode(); }
  function applyMode() {
    const draw = mode === "draw";
    bDraw.classList.toggle("active", draw); bGen.classList.toggle("active", !draw);
    genScroll.classList.toggle("hidden", draw); genInputs.classList.toggle("hidden", draw); genTop.classList.toggle("hidden", draw);
    drawPanel.classList.toggle("hidden", !draw);
    genTools.forEach((el) => el.classList.toggle("hidden", draw));
    drawTools.forEach((el) => el.classList.toggle("hidden", !draw));
    bUse.classList.toggle("hidden", !draw);
    drawCtl.classList.toggle("hidden", !draw);
    modeHint.textContent = draw ? "Drop rooms on the canvas and size them by dragging an edge or corner; then press Use this layout. The area model is optional." : "Optional: set the room counts and shapes, then let the area model sample area variations from the plan area. You can also draw the rooms yourself.";
    if (draw) requestAnimationFrame(() => geom.centre());
  }
  function syncInputs() {
    for (const rt of ROOM_TYPES_UI) counterRows[rt].value = store.inputs.room_counts[rt] ?? 0;
    for (const t of ROOM_TYPES_UI) arRows[t].set(store.inputs.aspect_ratios[t] ?? 1);
    refreshTotals(); syncPlan();
  }
  const refreshStage1 = () => { renderGen(); bProceed.disabled = store.stage1.selected_idx == null; };

  store.on("stage1", refreshStage1);
  store.on("inputsReset", syncInputs);
  store.on("reset", () => {
    syncInputs();
    if (store.drawnRooms && store.drawnRooms.length) geom.setRooms(store.drawnRooms); else geom.loadSample();
    geom.resetHistory();
    refreshStage1();
  });
  store.on("theme", () => { geom.draw(); cards.forEach((c) => c.redraw()); comp?.redraw(); });

  setDrawUnits(pg().units === "ft" ? "ft" : "m");
  syncInputs();
  geom.loadSample();
  geom.resetHistory();
  applyMode();
  refreshStage1();
  return {
    openRoomProgram() { setMode("draw"); if (!geom.rooms.length) geom.loadSample(); },
  };
}
