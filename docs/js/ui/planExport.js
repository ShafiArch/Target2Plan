// planExport.js: a plan as drawn, to file. JPG and PNG (transparent
// background) are rasters of the plan; PDF and DXF are vectors from the same
// drawing: paintFrame draws into a recorder instead of a canvas, and the
// recorded paths and texts become PDF operators (A3, to a standard scale) or
// DXF entities on CAD layers (R12, metres, opens in AutoCAD, Revit, Rhino;
// AutoCAD saves it as DWG). The layer of each part comes from the ctx.layer()
// hints in schematicPaint.js.
import { themePalette, offscreen, canvasBlob, pad2, ROOM_COLORS, FONT } from "./common.js";
import { paintFrame, planBounds } from "./schematicPaint.js";

/** [id, label, description] of the export formats, for the editor's menu. */
export const EXPORT_FORMATS = [
  ["jpg", "JPG", "white background"],
  ["png", "PNG", "transparent background"],
  ["dxf", "DXF", "CAD layers in metres; opens in AutoCAD (save as DWG), Revit, Rhino"],
  ["pdf", "PDF", "vector, to scale on A3"],
];

const lightRoomColor = (t) => ROOM_COLORS[t] || ROOM_COLORS.other;
const today = () => new Date().toISOString().slice(0, 10);

/**
 * Export the plan as drawn. `view` is {syn, scored} from previewEdit, `s` the
 * Stage 4 plan, `opt` the editor's view options (edges, furniture style),
 * `edited` whether the drawing differs from the plan as generated.
 * Resolves to {blob, name}.
 */
export async function exportPlan(fmt, { s, view, opt, edited }) {
  const syn = view.syn, c = view.scored ? view.scored.computed : s.computed;
  const zp = (s.orig || s).computed || {}, f2 = (v) => Number(v ?? 0).toFixed(2);
  const base = `target2plan_plan_${pad2(s.plan_index)}${edited ? "_edited" : ""}`;
  const info = {
    title: `Target2Plan · plan #${pad2(s.plan_index)}${edited ? " (edited)" : ""}`,
    scores: `As drawn V ${f2(c.qvci)} · A ${f2(c.aqi)}   ·   zone plan V ${f2(zp.qvci)} · A ${f2(zp.aqi)}`,
  };
  if (fmt === "png" || fmt === "jpg") return { blob: await raster(fmt, s, syn, opt, info), name: `${base}.${fmt}` };
  if (fmt === "pdf") return { blob: await pdf(s, syn, opt, info), name: `${base}.pdf` };
  if (fmt === "dxf") return { blob: dxf(s, syn, opt, info), name: `${base}.dxf` };
  throw new Error(`unknown format ${fmt}`);
}

const lastFrame = (syn) => syn.frames[syn.frames.length - 1];
const paintOpts = (opt, extra) => ({ showEdges: !!opt.edges, showWalls: true, furniture: opt.furn || "line50", showLabels: true, roomColor: lightRoomColor, ...extra });

// ═══════════════════════════════ raster ═════════════════════════════════
// JPG: the plan on white with its title and scores below. PNG: the plan alone
// for presentation boards, transparent around it (the rooms keep the colours
// they have on white).
async function raster(fmt, s, syn, opt, info) {
  const [x0, y0, x1, y1] = planBounds(syn).final, M = syn.metre, jpg = fmt === "jpg";
  const pxm = 70, k = pxm / M, m = 36, foot = jpg ? 46 : 0;
  const W = Math.round((x1 - x0) * k + 2 * m), H = Math.round((y1 - y0) * k + 2 * m + foot);
  const { cv, ctx } = offscreen(W, H, Math.min(2.5, Math.sqrt(40e6 / (W * H)))), P = themePalette("white");
  ctx.fillStyle = "#ffffff";
  if (jpg) ctx.fillRect(0, 0, W, H);
  else for (const sh of lastFrame(syn).shapes || []) if ("w" in sh) ctx.fillRect(m + (sh.cx - sh.w / 2 - x0) * k, m + (sh.cy - sh.h / 2 - y0) * k, sh.w * k, sh.h * k);
  paintFrame(ctx, s, syn, lastFrame(syn), W, H, P, paintOpts(opt, { transform: { scale: k, ox: m - x0 * k, oy: m - y0 * k }, fontScale: pxm / 34 }));
  northArrow(ctx, P, W - m + 6, m - 6, 9);
  if (jpg) {
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.font = `600 13px ${FONT}`; ctx.fillStyle = P.ink; ctx.fillText(info.title, m, H - foot + 14);
    ctx.font = `11px ${FONT}`; ctx.fillStyle = P.ink2; ctx.fillText(`${info.scores}   ·   ${today()}`, m, H - foot + 32);
  }
  return canvasBlob(cv, jpg ? "image/jpeg" : "image/png", 0.92);
}

function northArrow(ctx, P, x, y, s) {
  ctx.layer?.("A-ANNO-TEXT");
  ctx.fillStyle = P.accent;
  ctx.beginPath(); ctx.moveTo(x, y - s); ctx.lineTo(x + s * 0.55, y + s * 0.6); ctx.lineTo(x, y + s * 0.25); ctx.lineTo(x - s * 0.55, y + s * 0.6); ctx.closePath(); ctx.fill();
  ctx.font = `600 ${s * 1.1}px ${FONT}`; ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.fillText("N", x, y + s * 0.8);
}

// ═══════════════════════════════ recorder ═══════════════════════════════
// Helvetica advance widths (1/1000 em) for ASCII 32 to 126 (text width in PDF
// and the recorder's measureText).
const HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584];
const charWidth = (ch) => { const c = ch.charCodeAt(0); return c >= 32 && c <= 126 ? HELV[c - 32] : c === 0xb7 ? 278 : c === 0xb2 ? 333 : c === 0xd7 ? 584 : 556; };
const textWidth = (t, size, bold) => [...String(t)].reduce((s, ch) => s + charWidth(ch), 0) * size / 1000 * (bold ? 1.06 : 1);
function parseFont(f) {
  const m = /(?:(\d{3}|bold)\s+)?([\d.]+)px/.exec(String(f)) || [];
  return { size: Number(m[2] || 10), bold: m[1] === "bold" || Number(m[1]) >= 600 };
}
function parseColor(c) {
  c = String(c).trim();
  let m;
  if ((m = /^#([0-9a-f]{3})$/i.exec(c))) return [...m[1]].map((x) => parseInt(x + x, 16) / 255).concat(1);
  if ((m = /^#([0-9a-f]{6})$/i.exec(c))) return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16) / 255).concat(1);
  if ((m = /^rgba?\(([^)]+)\)$/i.exec(c))) {
    const p = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    return [p[0] / 255, p[1] / 255, p[2] / 255, p.length > 3 ? p[3] : 1];
  }
  return [0, 0, 0, 1];
}

/** A stand-in for CanvasRenderingContext2D that records what paintFrame draws. */
class Recorder {
  constructor() {
    this.ops = []; this.path = []; this.stack = []; this.layerName = "0";
    this.st = { fillStyle: "#000000", strokeStyle: "#000000", lineWidth: 1, lineCap: "butt", lineJoin: "miter", font: "10px sans-serif", textAlign: "start", textBaseline: "alphabetic", globalAlpha: 1, dash: [] };
    for (const k of ["fillStyle", "strokeStyle", "lineWidth", "lineCap", "lineJoin", "font", "textAlign", "textBaseline", "globalAlpha"]) {
      Object.defineProperty(this, k, { get: () => this.st[k], set: (v) => { this.st[k] = v; } });
    }
  }
  layer(name) { this.layerName = name; }
  save() { this.stack.push({ st: { ...this.st, dash: [...this.st.dash] }, layer: this.layerName }); }
  restore() { const s = this.stack.pop(); if (s) { this.st = s.st; this.layerName = s.layer; } }
  setLineDash(d) { this.st.dash = [...d]; }
  getLineDash() { return [...this.st.dash]; }
  setTransform() {} resetTransform() {} clearRect() {} clip() {}
  beginPath() { this.path = []; }
  moveTo(x, y) { this.path.push({ t: "M", x, y }); }
  lineTo(x, y) { this.path.push({ t: this.path.length ? "L" : "M", x, y }); }
  closePath() { this.path.push({ t: "Z" }); }
  rect(x, y, w, h) { this.path.push({ t: "M", x, y }, { t: "L", x: x + w, y }, { t: "L", x: x + w, y: y + h }, { t: "L", x, y: y + h }, { t: "Z" }); }
  roundRect(x, y, w, h) { this.rect(x, y, w, h); }
  arc(cx, cy, r, a0, a1, ccw = false) { this.path.push({ t: "A", cx, cy, r, a0, a1, ccw: !!ccw }); }
  fill() { this.ops.push({ op: "fill", path: this.path.slice(), color: this.st.fillStyle, alpha: this.st.globalAlpha, layer: this.layerName }); }
  stroke() { this.ops.push({ op: "stroke", path: this.path.slice(), color: this.st.strokeStyle, alpha: this.st.globalAlpha, lw: this.st.lineWidth, dash: [...this.st.dash], cap: this.st.lineCap, layer: this.layerName }); }
  fillRect(x, y, w, h) { const p = this.path; this.path = []; this.rect(x, y, w, h); this.fill(); this.path = p; }
  strokeRect(x, y, w, h) { const p = this.path; this.path = []; this.rect(x, y, w, h); this.stroke(); this.path = p; }
  fillText(text, x, y) { this.ops.push({ op: "text", text: String(text), x, y, font: parseFont(this.st.font), align: this.st.textAlign, baseline: this.st.textBaseline, color: this.st.fillStyle, alpha: this.st.globalAlpha, layer: this.layerName }); }
  measureText(text) { const f = parseFont(this.st.font); return { width: textWidth(text, f.size, f.bold) }; }
}

/** Sweep of a canvas arc (canvas rules: clockwise unless ccw, full turn when asked). */
function sweepOf(seg) {
  const TAU = 2 * Math.PI;
  if (!seg.ccw) return seg.a1 - seg.a0 >= TAU - 1e-9 ? TAU : (((seg.a1 - seg.a0) % TAU) + TAU) % TAU;
  return seg.a0 - seg.a1 >= TAU - 1e-9 ? -TAU : -((((seg.a0 - seg.a1) % TAU) + TAU) % TAU);
}

// ═══════════════════════════════ PDF ════════════════════════════════════
const SCALES = [20, 50, 100, 200, 500, 1000];
const PT_PER_M = 72 / 0.0254;

async function pdf(s, syn, opt, info) {
  const [x0, y0, x1, y1] = planBounds(syn).final, M = syn.metre;
  const wm = (x1 - x0) / M, hm = (y1 - y0) / M;
  const land = wm >= hm, PW = land ? 1190.55 : 841.89, PH = land ? 841.89 : 1190.55;
  const mar = 42, foot = 70, aw = PW - 2 * mar, ah = PH - 2 * mar - foot;
  const denom = SCALES.find((d) => wm * PT_PER_M / d <= aw && hm * PT_PER_M / d <= ah) || SCALES[SCALES.length - 1];
  const ptm = PT_PER_M / denom, k = ptm / M;
  const ox = mar + (aw - wm * ptm) / 2 - x0 * k, oy = mar + (ah - hm * ptm) / 2 - y0 * k;
  const P = themePalette("white"), rec = new Recorder();
  paintFrame(rec, s, syn, lastFrame(syn), PW, PH, P, paintOpts(opt, { transform: { scale: k, ox, oy }, fontScale: Math.max(0.8, Math.min(1.2, ptm / 34)) }));
  // title block: name, scores, scale, scale bar, north arrow, date
  const ty = PH - mar - foot + 28;
  rec.layer("A-ANNO-TEXT");
  rec.strokeStyle = P.border; rec.lineWidth = 0.6; rec.beginPath(); rec.moveTo(mar, ty - 16); rec.lineTo(PW - mar, ty - 16); rec.stroke();
  rec.textAlign = "left"; rec.textBaseline = "middle";
  rec.font = "600 12px x"; rec.fillStyle = P.ink; rec.fillText(info.title, mar, ty);
  rec.font = "9px x"; rec.fillStyle = P.ink2; rec.fillText(`${info.scores}   ·   scale 1:${denom} on A3   ·   ${today()}`, mar, ty + 18);
  const barM = denom <= 50 ? 5 : denom <= 100 ? 10 : denom <= 200 ? 20 : 50, bx = PW - mar - barM * ptm, by = ty + 2;
  rec.lineWidth = 0.6; rec.strokeStyle = P.ink;
  for (let i = 0; i < 5; i++) {
    rec.fillStyle = i % 2 ? "#ffffff" : P.ink;
    rec.beginPath(); rec.rect(bx + (i * barM * ptm) / 5, by, (barM * ptm) / 5, 4); rec.fill(); rec.stroke();
  }
  rec.font = "8px x"; rec.fillStyle = P.ink2; rec.textAlign = "center";
  rec.fillText("0", bx, by + 13); rec.fillText(`${barM} m`, bx + barM * ptm, by + 13);
  northArrow(rec, P, PW - mar - 6, mar + 10, 10);
  return new Blob([await pdfBytes(rec.ops, PW, PH, info.title)], { type: "application/pdf" });
}

const n3 = (v) => { const r = Math.round(v * 1000) / 1000; return Object.is(r, -0) ? "0" : String(r); };
function pdfText(t) {
  let out = "";
  for (const ch of String(t)) {
    const c = ch.codePointAt(0);
    if (ch === "(" || ch === ")" || ch === "\\") out += "\\" + ch;
    else if (c >= 32 && c < 127) out += ch;
    else if (c >= 0xa0 && c <= 0xff) out += "\\" + c.toString(8).padStart(3, "0");
    else out += "?";
  }
  return out;
}

async function pdfBytes(ops, PW, PH, title) {
  const Y = (y) => PH - y, gsIds = new Map(), lines = [];
  const gs = (a) => { const key = a.toFixed(3); if (!gsIds.has(key)) gsIds.set(key, `GS${gsIds.size + 1}`); return gsIds.get(key); };
  const rgb = ([r, g, b]) => `${n3(r)} ${n3(g)} ${n3(b)}`;
  for (const o of ops) {
    const col = parseColor(o.color), a = col[3] * (o.alpha ?? 1);
    if (a <= 0.002) continue;
    if (o.op !== "text" && !o.path.some((seg) => seg.t !== "Z")) continue;     // nothing to paint
    lines.push("q");
    if (a < 0.998) lines.push(`/${gs(a)} gs`);
    if (o.op === "text") {
      const f = o.font, w = textWidth(o.text, f.size, f.bold);
      let x = o.x, y = o.y;
      if (o.align === "center") x -= w / 2; else if (o.align === "right" || o.align === "end") x -= w;
      if (o.baseline === "middle") y += f.size * 0.35; else if (o.baseline === "top" || o.baseline === "hanging") y += f.size * 0.76; else if (o.baseline === "bottom") y -= f.size * 0.22;
      lines.push(`BT /${f.bold ? "F2" : "F1"} ${n3(f.size)} Tf ${rgb(col)} rg ${n3(x)} ${n3(Y(y))} Td (${pdfText(o.text)}) Tj ET`, "Q");
      continue;
    }
    if (o.op === "fill") lines.push(`${rgb(col)} rg`);
    else lines.push(`${rgb(col)} RG ${n3(o.lw)} w ${o.cap === "round" ? 1 : o.cap === "square" ? 2 : 0} J 0 j [${o.dash.map(n3).join(" ")}] 0 d`);
    let started = false;
    for (const seg of o.path) {
      if (seg.t === "M") { lines.push(`${n3(seg.x)} ${n3(Y(seg.y))} m`); started = true; }
      else if (seg.t === "L") { lines.push(`${n3(seg.x)} ${n3(Y(seg.y))} l`); started = true; }
      else if (seg.t === "Z") lines.push("h");
      else if (seg.t === "A") {
        const sw = sweepOf(seg), n = Math.max(1, Math.ceil(Math.abs(sw) / (Math.PI / 2) - 1e-9)), d = sw / n;
        const pt = (t) => [seg.cx + seg.r * Math.cos(t), seg.cy + seg.r * Math.sin(t)];
        const [sx, sy] = pt(seg.a0);
        lines.push(`${n3(sx)} ${n3(Y(sy))} ${started ? "l" : "m"}`); started = true;
        for (let i = 0; i < n; i++) {
          const t0 = seg.a0 + i * d, t1 = t0 + d, kk = (4 / 3) * Math.tan(d / 4);
          const [ax, ay] = pt(t0), [bx, by] = pt(t1);
          const c1 = [ax - kk * seg.r * Math.sin(t0), ay + kk * seg.r * Math.cos(t0)], c2 = [bx + kk * seg.r * Math.sin(t1), by - kk * seg.r * Math.cos(t1)];
          lines.push(`${n3(c1[0])} ${n3(Y(c1[1]))} ${n3(c2[0])} ${n3(Y(c2[1]))} ${n3(bx)} ${n3(Y(by))} c`);
        }
      }
    }
    lines.push(o.op === "fill" ? "f" : "S", "Q");
  }
  const enc = new TextEncoder();
  let content = enc.encode(lines.join("\n") + "\n"), filter = "";
  if (typeof CompressionStream !== "undefined") {
    try {
      const cs = new Blob([content]).stream().pipeThrough(new CompressionStream("deflate"));
      content = new Uint8Array(await new Response(cs).arrayBuffer());
      filter = " /Filter /FlateDecode";
    } catch { /* uncompressed */ }
  }
  const gsDict = [...gsIds].map(([a, id]) => `/${id} << /Type /ExtGState /ca ${a} /CA ${a} >>`).join(" ");
  const now = new Date(), pd = (v) => String(v).padStart(2, "0");
  const date = `D:${now.getUTCFullYear()}${pd(now.getUTCMonth() + 1)}${pd(now.getUTCDate())}${pd(now.getUTCHours())}${pd(now.getUTCMinutes())}${pd(now.getUTCSeconds())}Z`;
  const objs = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${n3(PW)} ${n3(PH)}] /Resources << /Font << /F1 5 0 R /F2 6 0 R >> /ExtGState << ${gsDict} >> >> /Contents 4 0 R >>`,
    null,                                         // the content stream
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    `<< /Title (${pdfText(title)}) /Creator (Target2Plan) /Producer (Target2Plan) /CreationDate (${date}) >>`,
  ];
  const parts = [], offsets = [];
  let len = 0;
  const add = (b) => { const u = typeof b === "string" ? enc.encode(b) : b; parts.push(u); len += u.length; };
  add("%PDF-1.4\n"); add(new Uint8Array([37, 226, 227, 207, 211, 10]));
  objs.forEach((o, i) => {
    offsets.push(len);
    if (o == null) { add(`${i + 1} 0 obj\n<< /Length ${content.length}${filter} >>\nstream\n`); add(content); add("\nendstream\nendobj\n"); }
    else add(`${i + 1} 0 obj\n${o}\nendobj\n`);
  });
  const xref = len;
  add(`xref\n0 ${objs.length + 1}\n0000000000 65535 f \n${offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("")}`);
  add(`trailer\n<< /Size ${objs.length + 1} /Root 1 0 R /Info 7 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  const out = new Uint8Array(len);
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}

// ═══════════════════════════════ DXF ════════════════════════════════════
// AutoCAD colour index per layer (7 is black on a white background)
const LAYERS = {
  "A-AREA-LIVING": 8, "A-AREA-BEDROOM": 1, "A-AREA-KITCHEN": 40, "A-AREA-BATHROOM": 221, "A-AREA-BALCONY": 9,
  "A-WALL": 7, "A-WALL-RAIL": 8, "A-GLAZ": 4, "A-DOOR": 140, "A-ENTRY": 1, "A-FURN": 9, "A-ANNO-TEXT": 7, "A-ANNO-EDGE": 8,
};
// which recorded drawing is kept per layer: rooms and the entrance marker as outlines of their fills, the rest as strokes
const keepFill = (layer) => layer.startsWith("A-AREA") || layer === "A-ENTRY";
const dxfText = (t) => String(t).replace(/\s*·\s*/g, ", ").replace(/²/g, "2").replace(/×/g, "x").replace(/[^\x20-\x7e]/g, "?");

function dxf(s, syn, opt, info) {
  const [x0, y0, x1, y1] = planBounds(syn).final, M = syn.metre;
  // painted in millimetres (paintFrame rounds label positions to whole units),
  // written in metres: y turns up and the plan sits in the first quadrant
  const k = 1000 / M, hmm = (y1 - y0) * k;
  const rec = new Recorder();
  paintFrame(rec, s, syn, lastFrame(syn), 1, 1, themePalette("white"), paintOpts(opt, { transform: { scale: k, ox: -x0 * k, oy: -y0 * k }, fontScale: 26 }));
  const X = (x) => x / 1000, Yd = (y) => (hmm - y) / 1000;
  const out = [];
  // integer group codes (flags, colours, justification) take whole numbers, the rest are reals
  const intCode = (c) => (c >= 60 && c <= 99) || (c >= 170 && c <= 179) || (c >= 270 && c <= 289) || (c >= 370 && c <= 389) || (c >= 400 && c <= 409);
  const n6 = (v) => { const r = Math.round(v * 1e6) / 1e6; return Object.is(r, -0) ? "0.0" : (Number.isInteger(r) ? r.toFixed(1) : String(r)); };
  const g = (code, v) => out.push(String(code), typeof v !== "number" ? String(v) : intCode(code) ? String(Math.round(v)) : n6(v));
  const used = new Set();
  let ext = [Infinity, Infinity, -Infinity, -Infinity];
  const grow = (x, y) => { ext = [Math.min(ext[0], x), Math.min(ext[1], y), Math.max(ext[2], x), Math.max(ext[3], y)]; };
  const ents = [];
  const E = (fn) => ents.push(fn);
  const line = (L, a, b) => { used.add(L); grow(...a); grow(...b); E(() => { g(0, "LINE"); g(8, L); g(10, a[0]); g(20, a[1]); g(30, 0); g(11, b[0]); g(21, b[1]); g(31, 0); }); };
  const poly = (L, pts, closed) => {
    if (pts.length < 2) return;
    if (pts.length === 2 && !closed) return line(L, pts[0], pts[1]);
    used.add(L); pts.forEach((p) => grow(...p));
    E(() => { g(0, "POLYLINE"); g(8, L); g(66, 1); g(10, 0); g(20, 0); g(30, 0); g(70, closed ? 1 : 0); for (const p of pts) { g(0, "VERTEX"); g(8, L); g(10, p[0]); g(20, p[1]); g(30, 0); } g(0, "SEQEND"); g(8, L); });
  };
  const arc = (L, seg) => {
    used.add(L);
    const cx = X(seg.cx), cy = Yd(seg.cy), r = seg.r / 1000, sw = sweepOf(seg);
    grow(cx - r, cy - r); grow(cx + r, cy + r);
    if (Math.abs(sw) >= 2 * Math.PI - 1e-6) { E(() => { g(0, "CIRCLE"); g(8, L); g(10, cx); g(20, cy); g(30, 0); g(40, r); }); return; }
    // y flips, so a canvas angle t is -t here and the sweep turns round
    let a = -seg.a0, b = -(seg.a0 + sw);
    if (b < a) [a, b] = [b, a];
    const deg = (r) => (((r * 180) / Math.PI) % 360 + 360) % 360;
    E(() => { g(0, "ARC"); g(8, L); g(10, cx); g(20, cy); g(30, 0); g(40, r); g(50, deg(a)); g(51, deg(b)); });
  };
  const seen = new Set();
  for (const o of rec.ops) {
    const L = LAYERS[o.layer] != null ? o.layer : "A-ANNO-TEXT";
    if (o.op === "text") {
      if (!o.text.trim()) continue;
      used.add(L);
      const hgt = o.font.size / 1000, x = X(o.x), y = Yd(o.y);
      const ha = o.align === "center" ? 1 : o.align === "right" || o.align === "end" ? 2 : 0, va = o.baseline === "middle" ? 2 : o.baseline === "top" || o.baseline === "hanging" ? 3 : o.baseline === "bottom" ? 1 : 0;
      grow(x, y);
      E(() => { g(0, "TEXT"); g(8, L); g(10, x); g(20, y); g(30, 0); g(40, hgt); g(1, dxfText(o.text)); if (ha || va) { g(72, ha); g(73, va); g(11, x); g(21, y); g(31, 0); } });
      continue;
    }
    if ((o.op === "fill") !== keepFill(L) || !o.path.length) continue;
    const key = `${L}|${JSON.stringify(o.path)}`;
    if (seen.has(key)) continue;        // the same outline filled and stroked: once
    seen.add(key);
    let pts = [], closed = false;
    const flush = () => { if (pts.length) poly(L, pts, closed); pts = []; closed = false; };
    for (const seg of o.path) {
      if (seg.t === "M") { flush(); pts.push([X(seg.x), Yd(seg.y)]); }
      else if (seg.t === "L") pts.push([X(seg.x), Yd(seg.y)]);
      else if (seg.t === "Z") { closed = true; flush(); }
      else if (seg.t === "A") { flush(); arc(L, seg); }
    }
    flush();
  }
  // title under the plan
  const tl = [ext[0], ext[1] - 0.9];
  used.add("A-ANNO-TEXT");
  E(() => { g(0, "TEXT"); g(8, "A-ANNO-TEXT"); g(10, tl[0]); g(20, tl[1]); g(30, 0); g(40, 0.3); g(1, dxfText(`${info.title}, ${info.scores}, units: metres, ${today()}`)); });
  grow(tl[0], tl[1] - 0.3);

  g(0, "SECTION"); g(2, "HEADER");
  g(9, "$ACADVER"); g(1, "AC1009");
  g(9, "$INSBASE"); g(10, 0); g(20, 0); g(30, 0);
  g(9, "$EXTMIN"); g(10, ext[0]); g(20, ext[1]); g(30, 0);
  g(9, "$EXTMAX"); g(10, ext[2]); g(20, ext[3]); g(30, 0);
  g(0, "ENDSEC");
  g(0, "SECTION"); g(2, "TABLES");
  g(0, "TABLE"); g(2, "LTYPE"); g(70, 1);
  g(0, "LTYPE"); g(2, "CONTINUOUS"); g(70, 0); g(3, "Solid line"); g(72, 65); g(73, 0); g(40, 0);
  g(0, "ENDTAB");
  const names = Object.keys(LAYERS).filter((L) => used.has(L));
  g(0, "TABLE"); g(2, "LAYER"); g(70, names.length + 1);
  g(0, "LAYER"); g(2, "0"); g(70, 0); g(62, 7); g(6, "CONTINUOUS");
  for (const L of names) { g(0, "LAYER"); g(2, L); g(70, 0); g(62, LAYERS[L]); g(6, "CONTINUOUS"); }
  g(0, "ENDTAB");
  g(0, "TABLE"); g(2, "STYLE"); g(70, 1);
  g(0, "STYLE"); g(2, "STANDARD"); g(70, 0); g(40, 0); g(41, 1); g(50, 0); g(71, 0); g(42, 0.25); g(3, "txt"); g(4, "");
  g(0, "ENDTAB");
  g(0, "ENDSEC");
  g(0, "SECTION"); g(2, "ENTITIES");
  for (const fn of ents) fn();
  g(0, "ENDSEC"); g(0, "EOF");
  return new Blob([out.join("\r\n") + "\r\n"], { type: "application/dxf" });
}

