// app.js: header, the horizontal strip of the five stage cards, theme,
// session load / export, and the hand-offs between stages.
import { store } from "./store.js";
import { runtime } from "./runtime.js";
import { h, THEMES, THEME_LABELS, downloadBlob, confirmAsk, toast, refreshSwatches, DESKTOP } from "./ui/common.js";
import { mountInputs } from "./ui/stage0.js";
import { mountArea } from "./ui/stage1.js";
import { mountZones } from "./ui/stage2.js";
import { mountConnectivity } from "./ui/stage3.js";
import { mountSchematic } from "./ui/stage4.js";
import { openAbout } from "./ui/about.js";

const STAGES = [
  { id: "inputs", num: "00", title: "Inputs", short: "Inputs", sub: "Rooms · geometry · site · targets", width: 460 },
  { id: "area", num: "01", title: "Area Distribution", short: "Areas", sub: "Draw the rooms · area model optional", width: 860 },
  { id: "zones", num: "02", title: "Zone Assignments", short: "Zones", sub: "Targets QVCI / AQI", width: 860 },
  { id: "connectivity", num: "03", title: "Connectivity", short: "Graphs", sub: "Nodes & edges per plan", width: 820 },
  { id: "schematic", num: "04", title: "Schematic Design", short: "Plans", sub: "Areas × zones × connectivity", width: 1080 },
];
// Phone layout (one stage per screen, stage tabs under the header); see css/app.css.
const phone = window.matchMedia ? window.matchMedia("(max-width: 760px)") : { matches: false };

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
  refreshSwatches();
  store.emit("theme");
}

// ── installing and the desktop app ──────────────────────────────────
// In a browser the site installs as an app (manifest + service worker; the
// Install button shows when the browser offers it). In the desktop app links
// open in the system browser.
let installPrompt = null;
const installBtn = h("button.pill.install", { title: "Install Target2Plan as an app on this computer; it then runs offline in its own window", style: { display: "none" },
  on: { click: async () => { if (!installPrompt) return; installPrompt.prompt(); await installPrompt.userChoice; installPrompt = null; installBtn.style.display = "none"; } } }, "Install app");
if (DESKTOP) {
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (a && /^(https?|mailto):/i.test(a.href)) { e.preventDefault(); DESKTOP.opener.openUrl(a.href); }
  });
} else {
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installPrompt = e; installBtn.style.display = ""; });
  window.addEventListener("appinstalled", () => { installPrompt = null; installBtn.style.display = "none"; toast("Target2Plan is installed; open it from your apps."); });
  if ("serviceWorker" in navigator && (location.protocol === "https:" || ["localhost", "127.0.0.1"].includes(location.hostname))) {
    navigator.serviceWorker.register("./sw.js").catch(() => {});
  }
}

// ── header ──────────────────────────────────────────────────────────
const status = h("span.model-status", "Loading models…");
const fileIn = h("input", { type: "file", accept: ".json,application/json", style: { display: "none" } });
const header = h("header.app-header",
  h("div.brand", h("span.diamond", "◆"), "Target2Plan"),
  h("div.header-spacer"), status,
  h("button.pill", { title: "Open a saved session (.json)", on: { click: () => fileIn.click() } }, "Load session"),
  h("button.pill", { title: "Save the whole session as a .json file", on: { click: exportSession } }, "Export session"),
  h("button.pill.danger", { on: { click: async () => { if (await confirmAsk("Reset the whole session? This cannot be undone.")) store.reset(); } } }, "Reset"),
  h("button.pill", { title: "Author, license and credits", on: { click: openAbout } }, "About"),
  installBtn, h("span.hsep"), themeBtn, fileIn);

runtime.onStatus((s, d) => {
  status.className = `model-status ${s === "ready" ? "ready" : s === "error" ? "error" : ""}`;
  if (s === "ready") { status.textContent = DESKTOP ? "Models ready · running on this computer" : "Models ready · running in your browser"; status.title = d && d.mainThread ? `Running on the main thread (${d.reason})` : "Area, zone and connectivity models loaded"; }
  else if (s === "error") { status.textContent = "Models failed to load"; status.title = String(d); }
});

// ── stage strip ─────────────────────────────────────────────────────
const strip = h("main.strip");
const nav = h("nav.stage-nav", { "aria-label": "Stages" });
const cards = {}, dots = {}, navDots = {}, tabs = {}, bodies = {};
for (const st of STAGES) {
  dots[st.id] = h("span.status-dot");
  navDots[st.id] = h("span.status-dot");
  bodies[st.id] = h("div.stage-body");
  cards[st.id] = h("section.stage", { style: { width: `${st.width}px` }, "data-stage": st.id },
    h("div.stage-head", h("span.num", st.num), h("div.titles", h("div.t", st.title), h("div.s", st.sub)), dots[st.id]), bodies[st.id]);
  strip.append(cards[st.id]);
  tabs[st.id] = h("button.stage-tab", { title: st.title, on: { click: () => goto(st.id) } }, h("span.n", st.num), h("span.t", st.short), navDots[st.id]);
  nav.append(tabs[st.id]);
}
document.body.append(header, nav, strip);

// the tab of the stage filling the screen is marked active (phone)
function nearestStage() {
  let best = null, bestD = Infinity;
  for (const st of STAGES) { const d = Math.abs(cards[st.id].offsetLeft - strip.offsetLeft - strip.scrollLeft); if (d < bestD) { bestD = d; best = st.id; } }
  return best;
}
function markActive(id) { for (const st of STAGES) tabs[st.id].classList.toggle("active", st.id === id); }
let navTick = null;
strip.addEventListener("scroll", () => { if (navTick) return; navTick = requestAnimationFrame(() => { navTick = null; markActive(nearestStage()); }); }, { passive: true });
markActive("inputs");

// Moving to the next stage glides slowly (ease in and out, about a second,
// a little longer for long distances) so the eye can follow where the work
// went. Any wheel, touch or drag on the strip takes over at once.
let glide = null;
function glideTo(left) {
  if (glide) cancelAnimationFrame(glide.raf);
  const from = strip.scrollLeft, to = Math.max(0, Math.min(left, strip.scrollWidth - strip.clientWidth)), dist = to - from;
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  if (Math.abs(dist) < 2 || reduce) { strip.scrollLeft = to; return; }
  const dur = Math.min(1700, 850 + Math.abs(dist) * 0.3), t0 = performance.now();
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
  const snap = strip.style.scrollSnapType;
  strip.style.scrollSnapType = "none";              // phone layout snaps; let the glide run first
  const stop = () => { if (!glide) return; cancelAnimationFrame(glide.raf); glide = null; strip.style.scrollSnapType = snap; };
  const step = (now) => {
    const t = Math.min(1, (now - t0) / dur);
    strip.scrollLeft = from + dist * ease(t);
    if (t < 1) glide.raf = requestAnimationFrame(step); else stop();
  };
  glide = { raf: requestAnimationFrame(step) };
  for (const ev of ["wheel", "touchstart", "pointerdown"]) strip.addEventListener(ev, stop, { once: true, passive: true });
}

function goto(stageId) {
  const el = cards[stageId];
  if (el) {
    const pad = parseFloat(getComputedStyle(strip).paddingLeft) || 0;
    // phone: the stage fills the screen, so align it exactly; desktop: show the end of the previous stage too
    glideTo(el.offsetLeft - strip.offsetLeft - pad - (phone.matches || stageId === "zones" ? 0 : 200));
    markActive(stageId);
  }
  store.emit(`goto:${stageId}`);
}

const ctx = { store, runtime, goto };
mountInputs(bodies.inputs, { ...ctx, goRoomProgram: () => { area.openRoomProgram(); goto("area"); } });
const area = mountArea(bodies.area, ctx);
mountZones(bodies.zones, ctx);
mountConnectivity(bodies.connectivity, ctx);
const schematic = mountSchematic(bodies.schematic, ctx);

function refreshDots() { const s = store.statuses(); for (const [k, v] of Object.entries(s)) { dots[k].className = `status-dot ${v}`; navDots[k].className = `status-dot ${v}`; } }
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
