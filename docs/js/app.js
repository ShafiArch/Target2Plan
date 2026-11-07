// app.js: header, the horizontal strip of the five stage cards, theme,
// session load / export, and the hand-offs between stages.
import { store } from "./store.js";
import { runtime } from "./runtime.js";
import { h, THEMES, THEME_LABELS, downloadBlob, toast } from "./ui/common.js";
import { mountInputs } from "./ui/stage0.js";
import { mountArea } from "./ui/stage1.js";
import { mountZones } from "./ui/stage2.js";
import { mountConnectivity } from "./ui/stage3.js";
import { mountSchematic } from "./ui/stage4.js";

const STAGES = [
  { id: "inputs", num: "00", title: "Inputs", sub: "Rooms · geometry · site · targets", width: 460 },
  { id: "area", num: "01", title: "Area Distribution", sub: "Pick one variation", width: 860 },
  { id: "zones", num: "02", title: "Zone Assignments", sub: "v13 model · targets QVCI / AQI", width: 860 },
  { id: "connectivity", num: "03", title: "Connectivity", sub: "Nodes & edges per plan", width: 1080 },
  { id: "schematic", num: "04", title: "Schematic Design", sub: "Areas × zones × connectivity", width: 1080 },
];

// ── theme ───────────────────────────────────────────────────────────
let theme = (() => { try { return localStorage.getItem("t2p.theme") || "offwhite"; } catch { return "offwhite"; } })();
if (!THEMES.includes(theme)) theme = "offwhite";
document.documentElement.dataset.theme = theme;
store.ui.theme = theme;
const themeBtn = h("button.pill.theme", { title: "Switch theme", on: { click: () => setTheme(THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length]) } }, THEME_LABELS[theme]);
function setTheme(t) {
  theme = t; store.ui.theme = t;
  document.documentElement.dataset.theme = t;
  themeBtn.textContent = THEME_LABELS[t];
  try { localStorage.setItem("t2p.theme", t); } catch { /* storage unavailable */ }
  store.emit("theme");
}

// ── header ──────────────────────────────────────────────────────────
const status = h("span.model-status", "Loading models…");
const fileIn = h("input", { type: "file", accept: ".json,application/json", style: { display: "none" } });
const header = h("header.app-header",
  h("div.brand", h("span.diamond", "◆"), "Target2Plan"),
  h("div.header-spacer"), status,
  h("button.pill", { title: "Open a saved session (.json)", on: { click: () => fileIn.click() } }, "Load session"),
  h("button.pill", { title: "Save the whole session as a .json file", on: { click: exportSession } }, "Export session"),
  h("button.pill.danger", { on: { click: () => { if (confirm("Reset the whole session? This cannot be undone.")) store.reset(); } } }, "Reset"),
  h("span.hsep"), themeBtn, fileIn);

runtime.onStatus((s, d) => {
  status.className = `model-status ${s === "ready" ? "ready" : s === "error" ? "error" : ""}`;
  if (s === "ready") { status.textContent = "Models ready · running in your browser"; status.title = d && d.mainThread ? `Running on the main thread (${d.reason})` : "Area, v13 zone and connectivity models loaded"; }
  else if (s === "error") { status.textContent = "Models failed to load"; status.title = String(d); }
});

// ── stage strip ─────────────────────────────────────────────────────
const strip = h("main.strip");
const cards = {}, dots = {}, bodies = {};
for (const st of STAGES) {
  dots[st.id] = h("span.status-dot");
  bodies[st.id] = h("div.stage-body");
  cards[st.id] = h("section.stage", { style: { width: `${st.width}px` }, "data-stage": st.id },
    h("div.stage-head", h("span.num", st.num), h("div.titles", h("div.t", st.title), h("div.s", st.sub)), dots[st.id]), bodies[st.id]);
  strip.append(cards[st.id]);
}
document.body.append(header, strip);

function goto(stageId) {
  const el = cards[stageId];
  if (el) strip.scrollTo({ left: Math.max(0, el.offsetLeft - strip.offsetLeft - 20 - (stageId === "zones" ? 0 : 200)), behavior: "smooth" });
  store.emit(`goto:${stageId}`);
}

const ctx = { store, runtime, goto };
mountInputs(bodies.inputs, { ...ctx, goRoomProgram: () => { area.openRoomProgram(); goto("area"); } });
const area = mountArea(bodies.area, ctx);
mountZones(bodies.zones, ctx);
mountConnectivity(bodies.connectivity, ctx);
const schematic = mountSchematic(bodies.schematic, ctx);

function refreshDots() { const s = store.statuses(); for (const [k, v] of Object.entries(s)) dots[k].className = `status-dot ${v}`; }
store.on("state", refreshDots);
refreshDots();

// ── sessions ────────────────────────────────────────────────────────
function exportSession() {
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  const name = `target2plan_session_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}.json`;
  downloadBlob(new Blob([JSON.stringify(store.toSession(), null, 1)], { type: "application/json" }), name);
}
fileIn.addEventListener("change", async () => {
  const f = fileIn.files[0];
  fileIn.value = "";
  if (!f) return;
  try {
    const s = JSON.parse(await f.text());
    const stage4 = store.fromSession(s);
    if (s.ui && THEMES.includes(s.ui.theme)) setTheme(s.ui.theme);
    if (stage4) schematic.rebuild(stage4);
    toast(`Loaded ${f.name}`);
  } catch (err) {
    toast(`Could not load the session: ${err.message}`);
  }
});

runtime.init();
