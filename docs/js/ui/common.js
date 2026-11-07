// common.js: DOM helpers, theme palette for canvases, room colours, canvas
// sizing, downloads, toasts and small widgets shared by the stages.

export const ROOM_COLORS = {
  living: "#8d746f", kitchen: "#e0a03c", bedroom: "#c0413f", bathroom: "#e7a9b3", dining: "#9a6f52",
  study: "#5f7d9a", office: "#4f9d8e", laundry: "#7d9bb5", other: "#9aa0a6", balcony: "#9e9e9e",
  storage: "#e3c24a", stair: "#4d4d4d", veranda: "#66c2a5", garden: "#6b6b6b", parking: "#7d7d7d",
  pool: "#5fb0d6", outside: "#6f6f6f",
};
export const roomColor = (t) => ROOM_COLORS[t] || ROOM_COLORS.other;
export const THEMES = ["offwhite", "white", "dark"];
export const THEME_LABELS = { offwhite: "Off-white", white: "White", dark: "Dark" };
export const ZONE_LABELS = { 0: "NW", 1: "N", 2: "NE", 3: "W", 4: "C", 5: "E", 6: "SW", 7: "S", 8: "SE" };
export const FONT = getComputedStyle(document.documentElement).getPropertyValue("--font") || "sans-serif";
export const MONO = getComputedStyle(document.documentElement).getPropertyValue("--mono") || "monospace";

/** h("div.cls#id", {attrs, on: {click}}, ...children) */
export function h(spec, attrs = {}, ...children) {
  const m = spec.match(/^([a-z0-9]+)?((?:[.#][\w-]+)*)$/i);
  const el = document.createElement(m[1] || "div");
  for (const part of m[2].match(/[.#][\w-]+/g) || []) {
    if (part[0] === ".") el.classList.add(part.slice(1)); else el.id = part.slice(1);
  }
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) { children.unshift(attrs); attrs = {}; }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "on") for (const [e, fn] of Object.entries(v)) el.addEventListener(e, fn);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "html") el.innerHTML = v;
    else if (k in el && k !== "list" && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) if (c != null && c !== false) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}
export const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };

/** Current palette as CSS colour strings plus rgb triplets, read from the theme variables. */
export function palette() {
  const cs = getComputedStyle(document.documentElement);
  const out = {};
  for (const k of ["bg", "surface", "surface2", "border", "ink", "ink2", "ink3", "accent", "accent2", "accent-soft", "good", "warn", "bad", "teal"]) {
    const v = cs.getPropertyValue(`--${k}-rgb`).trim().split(/\s+/).map(Number);
    out[k.replace("-", "_")] = `rgb(${v.join(",")})`;
    out[`${k.replace("-", "_")}Rgb`] = v;
  }
  out.a = (key, alpha) => { const v = out[`${key}Rgb`]; return `rgba(${v[0]},${v[1]},${v[2]},${alpha})`; };
  return out;
}
export const hexA = (hex, a) => { const n = parseInt(hex.slice(1), 16); return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`; };

/** Size a canvas to its CSS box at the device pixel ratio; returns {ctx, w, h} in CSS pixels. */
export function fitCanvas(cv, w = null, hgt = null) {
  const r = cv.getBoundingClientRect();
  const cw = Math.max(1, Math.round(w ?? r.width)), ch = Math.max(1, Math.round(hgt ?? r.height));
  const dpr = Math.min(window.devicePixelRatio || 1, 3);
  if (cv.width !== Math.round(cw * dpr) || cv.height !== Math.round(ch * dpr)) { cv.width = Math.round(cw * dpr); cv.height = Math.round(ch * dpr); }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cw, ch);
  return { ctx, w: cw, h: ch };
}

/** Offscreen canvas at a given CSS size and scale (for PNG exports). */
export function offscreen(w, hgt, scale = 3) {
  const cv = document.createElement("canvas");
  cv.width = Math.round(w * scale); cv.height = Math.round(hgt * scale);
  const ctx = cv.getContext("2d");
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  return { cv, ctx };
}

export function text(ctx, str, x, y, { font = `11px ${FONT}`, color = "#000", align = "left", base = "middle", weight = "" } = {}) {
  ctx.font = weight ? `${weight} ${font}` : font;
  ctx.fillStyle = color; ctx.textAlign = align; ctx.textBaseline = base;
  ctx.fillText(str, x, y);
}

export function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}
export const canvasBlob = (cv, type = "image/png", q) => new Promise((res) => cv.toBlob(res, type, q));

let toastTimer = null;
export function toast(msg, ms = 3200) {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const t = h("div.toast", msg);
  document.body.append(t);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), ms);
}

/** Collapsible section in the desktop's style. */
export function section(title, subtitle, expanded = true, ...body) {
  const caret = h("span.caret", expanded ? "▾" : "▸");
  const sec = h("div.section" + (expanded ? "" : ".collapsed"));
  const head = h("div.section-head", { on: { click: () => { sec.classList.toggle("collapsed"); caret.textContent = sec.classList.contains("collapsed") ? "▸" : "▾"; } } },
    caret, h("div", h("div.h", title), subtitle ? h("div.st", subtitle) : null));
  const bodyEl = h("div.section-body", ...body);
  sec.append(head, bodyEl);
  sec.body = bodyEl;
  return sec;
}

/** label (+ small sublabel) · slider · value readout */
export function sliderRow(label, sub, value, lo, hi, step, onChange, { digits = 2, disabled = false } = {}) {
  const v = h("span.v", Number(value).toFixed(digits));
  const input = h("input", { type: "range", min: lo, max: hi, step, value, disabled });
  input.addEventListener("input", () => { v.textContent = Number(input.value).toFixed(digits); onChange(Number(input.value)); });
  const row = h("div.slider-row" + (disabled ? ".disabled" : ""), h("div.lbl", h("span.n", label), sub ? h("span.d", sub) : null), input, v);
  row.set = (x) => { input.value = x; v.textContent = Number(x).toFixed(digits); };
  row.setEnabled = (on) => { input.disabled = !on; row.classList.toggle("disabled", !on); };
  return row;
}

/** colour swatch · name · slider · value */
export function roomSliderRow(type, value, lo, hi, step, onChange) {
  const v = h("span.v", Number(value).toFixed(2));
  const input = h("input", { type: "range", min: lo, max: hi, step, value });
  input.addEventListener("input", () => { v.textContent = Number(input.value).toFixed(2); onChange(Number(input.value)); });
  const row = h("div.room-row", h("span.swatch", { style: { background: roomColor(type) } }), h("span.n", type[0].toUpperCase() + type.slice(1)), input, v);
  row.set = (x) => { input.value = x; v.textContent = Number(x).toFixed(2); };
  return row;
}

export function numberInput(value, { min, max, step = 1, suffix = "", width = null, onChange }) {
  const input = h("input", { type: "number", value, min, max, step });
  const commit = () => {
    let x = Number(input.value);
    if (!Number.isFinite(x)) x = Number(value);
    if (min != null) x = Math.max(min, x);
    if (max != null) x = Math.min(max, x);
    input.value = x;
    onChange(x);
  };
  input.addEventListener("change", commit);
  const wrap = suffix ? h("span.num-unit", input, h("span", suffix)) : input;
  if (width) wrap.style.width = typeof width === "number" ? `${width}px` : width;
  wrap.input = input;
  wrap.set = (x) => { input.value = x; };
  return wrap;
}

export function emptyState(title, sub, btnLabel = null, onClick = null) {
  return h("div.empty-state", h("div.t", title), h("div.s", sub), btnLabel ? h("button.btn-primary", { on: { click: onClick } }, btnLabel) : null);
}

/** Debounce helper. */
export function debounce(fn, ms) {
  let t = null;
  const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  d.cancel = () => clearTimeout(t);
  return d;
}

/** Call fn when the element's size changes (and once initially). */
export function onResize(el, fn) { const ro = new ResizeObserver(() => fn()); ro.observe(el); return ro; }

export const fmt = (v, d) => (v == null || Number.isNaN(Number(v)) ? "-" : Number(v).toFixed(d));
export const pad2 = (n) => String(n).padStart(2, "0");
