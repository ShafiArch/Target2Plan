// gallery.mjs: runs the web app's own engine on a few conventional room
// programs and paints the README gallery frames (schematic synthesis and
// connectivity graph). Loaded by index.html; driven by make_gallery.py.
import { loadNets, runTask } from "../../docs/js/worker.js";
import { defaultInputs, ROOM_TYPES_UI } from "../../docs/js/store.js";
import { packCirclesNatural } from "../../docs/js/engine/area.js";
import { pyRound } from "../../docs/js/engine/pymath.js";
import { makeSchematics, bounds } from "../../docs/js/engine/schematic.js";
import { getSynthesis, paintFrame, planBounds } from "../../docs/js/ui/schematicPaint.js";
import { drawGraph, totalSteps } from "../../docs/js/ui/stage3.js";
import { palette, FONT, MONO } from "../../docs/js/ui/common.js";

// Room programs in metres, [type, width (W to E), depth (N to S)], drawn the
// way Stage 1 takes a drawn layout. Sizes follow common apartment practice.
export const PROGRAMS = {
  "1-bedroom": {
    label: "1 bedroom: living room, kitchen, bathroom, balcony",
    rooms: [["living", 5.0, 4.2], ["kitchen", 3.0, 2.8], ["bedroom", 3.6, 3.4], ["bathroom", 2.4, 2.0], ["balcony", 3.0, 1.5]],
  },
  "2-bedroom": {
    label: "2 bedrooms: living room, kitchen, bathroom, balcony",
    rooms: [["living", 5.4, 4.4], ["kitchen", 3.2, 3.0], ["bedroom", 3.8, 3.6], ["bedroom", 3.2, 3.0], ["bathroom", 2.4, 2.1], ["balcony", 3.2, 1.5]],
  },
  "3-bedroom": {
    label: "3 bedrooms: living room, kitchen, 2 bathrooms, balcony",
    rooms: [["living", 6.0, 4.6], ["kitchen", 3.4, 3.0], ["bedroom", 3.8, 3.6], ["bedroom", 3.4, 3.0], ["bedroom", 3.2, 3.0],
      ["bathroom", 2.4, 2.1], ["bathroom", 2.2, 1.8], ["balcony", 3.6, 1.6]],
  },
  "4-bedroom": {
    label: "4 bedrooms: living room, kitchen, 2 bathrooms, 2 balconies",
    rooms: [["living", 6.4, 4.8], ["kitchen", 3.6, 3.2], ["bedroom", 4.0, 3.6], ["bedroom", 3.6, 3.2], ["bedroom", 3.4, 3.0], ["bedroom", 3.2, 3.0],
      ["bathroom", 2.6, 2.2], ["bathroom", 2.4, 2.0], ["balcony", 3.8, 1.6], ["balcony", 3.0, 1.5]],
  },
};

let nets = null;
const kept = new Map();   // candidate id → { schematic, graph, table }

export async function init() {
  nets = await loadNets(new URL("../../docs/models/", import.meta.url));
  await Promise.all([`400 16px ${FONT}`, `600 16px ${FONT}`, `600 16px ${MONO}`].map((f) => document.fonts.load(f)));
  return { trained: nets.meta.zone.trained || null };
}

/** Default inputs (site, targets, WWR, parameters) with the program as the drawn layout. */
function setup(program) {
  const inputs = defaultInputs();
  const seen = {}, rooms = [];
  for (const [type, w, h] of program.rooms) { const n = seen[type] || 0; seen[type] = n + 1; rooms.push({ id: `${type}_${n}`, type, w, h }); }
  let total = 0;
  const out = rooms.map((r) => {
    const a = r.w * r.h;
    total += a;
    return { room_id: r.id, room_type: r.type, area: a, area_m2: pyRound(a, 2), w_m: pyRound(r.w, 3), h_m: pyRound(r.h, 3), aspect_ratio: r.h / r.w, radius: Math.sqrt(a / Math.PI) };
  });
  packCirclesNatural(out, 42);
  const share = {};
  for (const r of out) share[r.room_type] = (share[r.room_type] || 0) + r.area / total;
  const variation = { variation_idx: 0, rooms: out, total_area: total, units: "m", area_share_by_type: share, counts_by_type: { ...seen }, drawn: true };
  const pg = inputs.plan_geometry, asp = pg.width / pg.height;
  pg.width = pyRound(Math.sqrt(total * asp), 1); pg.height = pyRound(Math.sqrt(total / asp), 1);
  for (const rt of ROOM_TYPES_UI) inputs.room_counts[rt] = Number(seen[rt] || 0);
  return { inputs, variation, total };
}

/**
 * Practical for a presentation: every connectivity edge became a door or an
 * opening on a shared wall long enough for it, nothing was dropped, there is
 * an entrance, every room is reachable through doors, bedrooms, the kitchen
 * and one bathroom open off the living room, living room and bedrooms have a
 * window and rooms do not overlap. `fill` (indoor area over the area of its
 * bounding box) tells how compact the plan is.
 */
export function check(s, syn) {
  const why = [];
  if (syn.removed.length) why.push(`dropped ${syn.removed.join(", ")}`);
  const rects = syn.rects, byId = new Map(rects.map((r) => [r.room_id, r]));
  const type = (id) => byId.get(id)?.room?.room_type;
  const key = (a, b) => [a, b].sort().join("|");
  const links = new Set(), adj = new Map(rects.map((r) => [r.room_id, new Set()]));
  for (const o of syn.openings) {
    if (o.kind === "window" || o.kind === "entry") continue;
    const [a, b] = o.rooms;
    links.add(key(a, b)); adj.get(a)?.add(b); adj.get(b)?.add(a);
  }
  // the wall two connected rooms share: room for a door (1.2 m) or, for the
  // open kitchen, a real opening (1.8 m), not just a corner
  const shared = (a, b) => syn.walls.filter((w) => (w.rooms[0] === a && w.rooms[1] === b) || (w.rooms[0] === b && w.rooms[1] === a))
    .reduce((acc, w) => acc + Math.hypot(w.x2 - w.x1, w.y2 - w.y1), 0) / syn.metre;
  for (const e of s.edges || []) {
    if (String(e.source).startsWith("outside") || String(e.target).startsWith("outside")) continue;
    if (!byId.has(e.source) || !byId.has(e.target)) continue;
    if (!links.has(key(e.source, e.target))) { why.push(`no shared wall ${e.source}/${e.target}`); continue; }
    const open = [type(e.source), type(e.target)].sort().join() === "kitchen,living", L = shared(e.source, e.target);
    if (L < (open ? 1.8 : 1.2)) why.push(`short wall ${e.source}/${e.target} ${L.toFixed(1)} m`);
  }
  const entry = syn.openings.find((o) => o.kind === "entry");
  const start = entry ? entry.rooms[0] : null;
  if (!start) why.push("no entrance");
  const walk = (through) => {
    const seen = new Set(start ? [start] : []), q = start ? [start] : [];
    while (q.length) {
      const c = q.shift();
      if (c !== start && !through(type(c))) continue;
      for (const nb of adj.get(c) || []) if (!seen.has(nb)) { seen.add(nb); q.push(nb); }
    }
    return seen;
  };
  const all = walk(() => true), pub = walk((t) => t === "living");
  for (const r of rects) {
    const id = r.room_id, t = type(id);
    if (!all.has(id)) why.push(`${id} unreachable`);
    else if ((t === "bedroom" || t === "kitchen") && !pub.has(id)) why.push(`${id} not off the living room`);
    if ((t === "living" || t === "bedroom") && !syn.openings.some((o) => o.kind === "window" && o.rooms[0] === id)) why.push(`${id} no window`);
  }
  const baths = rects.filter((r) => type(r.room_id) === "bathroom");
  if (baths.length && !baths.some((r) => pub.has(r.room_id))) why.push("no bathroom off the living room");
  const indoor = rects.filter((r) => type(r.room_id) !== "balcony");
  const m2 = syn.metre * syn.metre;
  for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
    const [a0, a1, a2, a3] = bounds(rects[i]), [b0, b1, b2, b3] = bounds(rects[j]);
    const ov = Math.max(0, Math.min(a2, b2) - Math.max(a0, b0)) * Math.max(0, Math.min(a3, b3) - Math.max(a1, b1));
    if (ov > 0.05 * m2) why.push(`overlap ${rects[i].room_id}/${rects[j].room_id}`);
  }
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity, area = 0;
  for (const r of indoor) { const b = bounds(r); x0 = Math.min(x0, b[0]); y0 = Math.min(y0, b[1]); x1 = Math.max(x1, b[2]); y1 = Math.max(y1, b[3]); area += r.w * r.h; }
  const fill = area / Math.max(1e-9, (x1 - x0) * (y1 - y0));
  return { ok: why.length === 0, why, fill };
}

/** A conventional access graph: one entrance, into the living room; the
 *  kitchen, every bedroom and at least one bathroom open off the living room;
 *  nothing is reached through a bedroom except an en-suite bathroom or a
 *  balcony, and every room has an edge. */
export function graphCheck(nodeIds, edges) {
  const why = [], nb = new Map(nodeIds.map((id) => [id, new Set()]));
  for (const [a, b] of edges) { nb.get(a)?.add(b); nb.get(b)?.add(a); }
  const t = (id) => id.replace(/_\d+$/, "");
  const offLiving = (id) => [...nb.get(id)].some((x) => t(x) === "living");
  for (const id of nodeIds) {
    const ty = t(id), adj = [...nb.get(id)];
    if (!adj.length) why.push(`${id} has no edge`);
    else if ((ty === "bedroom" || ty === "kitchen") && !offLiving(id)) why.push(`${id} not off the living room`);
    if (ty === "outside" && adj.some((x) => t(x) !== "living")) why.push(`entrance into ${adj.filter((x) => t(x) !== "living").join(", ")}`);
    if (ty === "bedroom") for (const x of adj) if (t(x) === "bedroom" || t(x) === "kitchen") why.push(`${id} opens to ${x}`);
    if (ty === "bathroom") for (const x of adj) if (t(x) === "kitchen" || t(x) === "bathroom") why.push(`${id} opens to ${x}`);
  }
  const baths = nodeIds.filter((id) => t(id) === "bathroom");
  if (baths.length && !baths.some(offLiving)) why.push("no bathroom off the living room");
  return why;
}

/** A graph that reads clearly. In the finished graph (the frame the animation
 *  holds) no edge runs over a node it does not join and no two nodes sit on
 *  top of each other; while the edges appear, no edge runs over a room that
 *  stays where it is (rooms still to be relocated wait at their start, unplaced
 *  balconies at the plan centre). Distances in the graph's 200-unit canvas. */
export function graphClear(g, gap = 8) {
  const nodes = g.nodes.filter((n) => !n.is_direction), why = [];
  for (const phase of ["start", "end"]) {
    const at = new Map(nodes.map((n) => [n.id, phase === "end" ? [n.x, n.y] : [n.x_start ?? n.x, n.y_start ?? n.y]]));
    const fixed = phase === "end" ? nodes : nodes.filter((n) => !n.relocated);
    for (const e of g.edges_ordered || []) {
      const a = at.get(e.source), b = at.get(e.target);
      if (!a || !b) continue;
      const dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy;
      for (const n of fixed) {
        if (n.id === e.source || n.id === e.target) continue;
        const p = at.get(n.id), u = L2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2)) : 0;
        if (Math.hypot(a[0] + u * dx - p[0], a[1] + u * dy - p[1]) < gap) why.push(`${phase}: ${e.source}-${e.target} runs over ${n.id}`);
      }
    }
    if (phase === "end") for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const p = at.get(nodes[i].id), q = at.get(nodes[j].id);
      if (Math.hypot(p[0] - q[0], p[1] - q[1]) < gap) why.push(`${phase}: ${nodes[i].id} on ${nodes[j].id}`);
    }
  }
  return why;
}

/**
 * The app's pipeline for one program: zone tables (one greedy and
 * zone_samples sampled decodes at the default temperature, the n_plans
 * closest to the targets), connectivity graphs, schematic synthesis. Repeated
 * over the zone model seeds and the connectivity model seeds (the app uses
 * 42); returns one summary row per plan.
 */
export function search(name, { seeds = [1, 2, 3, 4, 5, 6], connSeeds = [42], practicalOnly = false, targets = null } = {}) {
  const program = PROGRAMS[name];
  const { inputs: base, variation, total } = setup(program);
  const rows = [];
  // where each room sits in the finished plan (centre in 0..1 of the plan's
  // extent) and a rounded signature of it, to tell layouts apart
  const layoutOf = (syn) => {
    const b = syn.rects.filter((r) => r.room.room_type !== "balcony").map((r) => bounds(r));
    const x0 = Math.min(...b.map((v) => v[0])), y0 = Math.min(...b.map((v) => v[1])), x1 = Math.max(...b.map((v) => v[2])), y1 = Math.max(...b.map((v) => v[3]));
    const lay = syn.rects.filter((r) => r.room.room_type !== "balcony").map((r) => [r.room_id, (r.cx - x0) / (x1 - x0), (r.cy - y0) / (y1 - y0)]).sort((p, q) => (p[0] < q[0] ? -1 : 1));
    return { layout: lay, aspect: (x1 - x0) / (y1 - y0), sig: lay.map(([id, u, v]) => `${id}:${Math.round(u * 20)}:${Math.round(v * 20)}`).join(" ") };
  };
  const graphSig = (g) => g.nodes.filter((n) => !n.is_direction).map((n) => `${n.id}:${Math.round(n.x)}:${Math.round(n.y)}`).sort().join(" ") + "|" + (g.edges_ordered || []).map((e) => `${e.source}-${e.target}`).join(" ");
  // the default targets first, then any others asked for
  const pairs = targets || [[base.prefs.target_qvci, base.prefs.target_aqi]];
  for (const [ti, [tq, ta]] of pairs.entries()) for (const seed of seeds) {
    const ip = JSON.parse(JSON.stringify(base));
    ip.prefs.target_qvci = tq; ip.prefs.target_aqi = ta;
    const doneEdges = new Set();
    const res = runTask(nets, "zones", { inputs: ip, variation, opts: { nSamples: ip.params.zone_samples, temperature: ip.params.temperature, seed } });
    const display = res.tables.slice(0, ip.params.n_plans);
    display.forEach((t, i) => { t.table_index = i; t.plan_width = ip.plan_geometry.width; t.plan_height = ip.plan_geometry.height; });
    const idxs = display.map((t) => t.table_index);
    for (const connSeed of connSeeds) {
      const graphs = runTask(nets, "conn", { tables: display, selected: idxs, opts: { planWidth: ip.plan_geometry.width, planHeight: ip.plan_geometry.height, roomCounts: { ...ip.room_counts }, seed: connSeed } });
      const eSig = graphs.length ? graphs[0].edges_ordered.map((e) => `${e.source}-${e.target}`).join(" ") : "";
      if (practicalOnly) { if (doneEdges.has(eSig)) continue; doneEdges.add(eSig); }   // same edges, same plans
      const gWhy = graphs.length ? graphCheck(graphs[0].nodes.filter((x) => !x.is_direction).map((x) => x.id), graphs[0].edges_ordered.map((e) => [e.source, e.target])) : ["no graph"];
      const schematics = makeSchematics(display, graphs, idxs, ip);
      for (const s of schematics) {
        const t = display[s.plan_index], g = graphs.find((x) => x.meta.plan_index === s.plan_index);
        let c = { ok: false, why: gWhy.map((w) => `graph: ${w}`), fill: 0 }, frames = 0, practical = false, clear = false, lay = null;
        if (!gWhy.length) {
          const syn = getSynthesis(s);
          c = check(s, syn); frames = syn.frames.length; practical = c.ok;
          const drawn = g ? graphClear(g) : ["no graph"];
          clear = !drawn.length;
          if (drawn.length) c = { ...c, ok: false, why: [...c.why, ...drawn.map((w) => `drawing: ${w}`)] };
          if (practical) lay = { ...layoutOf(syn), graph_sig: g ? graphSig(g) : "" };
        }
        const id = `${name}|${ti}|${seed}|${connSeed}|${s.plan_index}`;
        if (practical) kept.set(id, { schematic: s, graph: g, table: t });
        if (practicalOnly && !practical) continue;
        rows.push({
          id, program: name, label: program.label, target: ti, seed, conn_seed: connSeed, plan_index: s.plan_index, ok: c.ok, practical, clear, ...(lay || {}), why: c.why, fill: c.fill,
          qvci: t.computed.qvci, aqi: t.computed.aqi, d_target: t.computed.d_target,
          target_qvci: ip.prefs.target_qvci, target_aqi: ip.prefs.target_aqi, rooms: (s.rooms || []).length,
          area_m2: pyRound(total, 1), plan: [ip.plan_geometry.width, ip.plan_geometry.height],
          edges: (s.edges || []).map((e) => [e.source, e.target]), frames, decodes: res.n_decodes,
        });
      }
    }
  }
  return rows;
}

/** The finished plan of a kept candidate as a PNG data URL (for contact sheets). */
export function planStill(id, size = 280) {
  const { schematic: s } = kept.get(id);
  const syn = getSynthesis(s), P = palette(), cv = document.createElement("canvas");
  cv.width = size; cv.height = size;
  const ctx = cv.getContext("2d");
  ctx.fillStyle = P.surface; ctx.fillRect(0, 0, size, size);
  paintFrame(ctx, s, syn, syn.frames[syn.frames.length - 1], size, size, P, { showEdges: false, showWalls: true, furniture: "line50", showLabels: false, showCompass: false });
  return cv.toDataURL("image/png");
}

// ── painting ─────────────────────────────────────────────────────────
// Frames are painted like a card in the app (sizes in CSS pixels: a 300 px
// canvas, a 22 px footer) on a high-density canvas, dpr device pixels per CSS
// pixel, so the GIFs look like the app on a retina screen.
const CARD = 300, FOOT = 22, PAD = 4;

function surface(cols, dpr, ch = CARD) {
  const cv = document.createElement("canvas");
  cv.width = Math.round(CARD * cols * dpr); cv.height = Math.round((ch + FOOT) * dpr);
  return [cv, cv.getContext("2d")];
}

/** A card footer (.plain-foot) under a canvas ch high: items [text, font, colour, space before] from the left and from the right. */
function footer(ctx, x0, left, right, ch = CARD) {
  const y = ch + FOOT / 2 - 1;
  ctx.textBaseline = "middle";
  let x = x0 + 8;
  for (const [txt, font, col, gap] of left) {
    x += gap; ctx.font = font; ctx.fillStyle = col; ctx.textAlign = "left"; ctx.fillText(txt, x, y); x += ctx.measureText(txt).width;
  }
  x = x0 + CARD - 8;
  for (const [txt, font, col, gap] of [...right].reverse()) {
    ctx.font = font; ctx.fillStyle = col; ctx.textAlign = "right"; ctx.fillText(txt, x, y); x -= ctx.measureText(txt).width + gap;
  }
}

/** Footer of the Schematic card: Q and A of the plan, its room count. */
function planFooter(s, t, P) {
  const lab = `400 9px ${FONT}`, val = `600 10px ${MONO}`;
  return [[["Q", lab, P.ink3, 0], [Number(t.computed.qvci).toFixed(2), val, P.accent, 2], ["A", lab, P.ink3, 8], [Number(t.computed.aqi).toFixed(2), val, P.accent, 2]],
    [[`${(s.rooms || []).length} rooms`, lab, P.ink3, 0]]];
}

/** One synthesis frame in card column `col` of a canvas ch high: walls, doors and windows, white line furniture at 0.5; no labels, edges or compass. */
function paintPlan(ctx, s, t, frame, col, P, dpr, fitDims = null, ch = CARD) {
  ctx.setTransform(dpr, 0, 0, dpr, CARD * col * dpr, 0);
  ctx.save(); ctx.translate(PAD, PAD);
  paintFrame(ctx, s, getSynthesis(s), frame, CARD - 2 * PAD, ch - 2 * PAD, P, { showEdges: false, showWalls: true, furniture: "line50", showLabels: false, showCaption: false, showCompass: false, fitDims });
  ctx.restore();
  footer(ctx, 0, ...planFooter(s, t, P), ch);
}

/** paintFrame's drawing area inside a w x h view (its margin, no compass). */
const availFor = (w, h) => { const m = Math.max(6, Math.min(w, h) * 0.035); return [w - 2 * m, h - 2 * m]; };

/** One scale in pixels per metre for every plan (the largest at which all fit
 *  in a w wide view), the view height (at most maxH) that holds the tallest of
 *  them, and the fitDims that make paintFrame draw each plan at that scale. */
function sharedScale(schematics, w = CARD - 2 * PAD, maxH = w) {
  const ext = schematics.map((s) => { const syn = getSynthesis(s), b = planBounds(syn).final; return [(b[2] - b[0]) / syn.metre, (b[3] - b[1]) / syn.metre, syn.metre]; });
  let h = maxH, pxPerM = 0;
  for (let k = 0; k < 4; k++) {
    const [aw, ah] = availFor(w, h);
    pxPerM = Math.min(...ext.map(([ew, eh]) => Math.min(aw / ew, ah / eh)));
    h = Math.min(maxH, Math.ceil(Math.max(...ext.map(([, eh]) => eh)) * pxPerM + (h - ah)) + 2);
  }
  const [aw, ah] = availFor(w, h);
  pxPerM = Math.min(...ext.map(([ew, eh]) => Math.min(aw / ew, ah / eh)));
  return { h, ch: h + 2 * PAD, fits: ext.map(([, , metre]) => [[aw * metre / pxPerM, ah * metre / pxPerM]]) };
}

function clearAll(ctx, cv, P) { ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.fillStyle = P.surface; ctx.fillRect(0, 0, cv.width, cv.height); }

/** The schematic synthesis of one kept plan, frame by frame. */
export function planFrames(id, { dpr = 2.4, ms = 40, first = 700, hold = 2800 } = {}) {
  const { schematic: s, table: t } = kept.get(id);
  const syn = getSynthesis(s), P = palette(), [cv, ctx] = surface(1, dpr), n = syn.frames.length;
  return syn.frames.map((frame, i) => {
    clearAll(ctx, cv, P);
    paintPlan(ctx, s, t, frame, 0, P, dpr);
    return { png: cv.toDataURL("image/png"), delay: i === n - 1 ? hold : i === 0 ? first : ms };
  });
}

/** Several kept plans side by side at one scale, each running its synthesis and holding the finished plan. */
export function stripFrames(ids, { dpr = 1.6, ms = 40, first = 700, hold = 3200 } = {}) {
  const items = ids.map((id) => kept.get(id)), P = palette();
  const n = Math.max(...items.map((it) => getSynthesis(it.schematic).frames.length));
  const { ch, fits } = sharedScale(items.map((it) => it.schematic)), [cv, ctx] = surface(ids.length, dpr, ch);
  const out = [];
  for (let i = 0; i < n; i++) {
    clearAll(ctx, cv, P);
    items.forEach((it, col) => { const fr = getSynthesis(it.schematic).frames; paintPlan(ctx, it.schematic, it.table, fr[Math.min(i, fr.length - 1)], col, P, dpr, fits[col], ch); });
    out.push({ png: cv.toDataURL("image/png"), delay: i === n - 1 ? hold : i === 0 ? first : ms });
  }
  return out;
}

/** The connectivity animation of the same plan, painted like the Connectivity card. */
export function graphFrames(id, { dpr = 2.4, edgeMs = 260, first = 900, pauseMs = 60, moveMs = 45, hold = 2800 } = {}) {
  const { graph: g } = kept.get(id);
  const P = palette(), [cv, ctx] = surface(1, dpr), n = totalSteps(g), nE = (g.edges_ordered || []).length;
  const rooms = g.nodes.filter((x) => !x.is_direction && x.type !== "outside").length, moved = (g.relocations || []).length;
  const num = `600 10px ${FONT}`, lab = `400 10px ${FONT}`, sp = 2.8;
  const left = [[String(rooms), num, P.ink, 0], ["rooms", lab, P.ink3, sp]];
  if (moved) left.push([String(moved), num, P.ink, 8], ["moved", lab, P.ink3, sp]);
  const right = [[String(nE), num, P.ink, 0], ["edges", lab, P.ink3, sp]];
  const out = [];
  for (let f = 0; f <= n; f++) {
    clearAll(ctx, cv, P);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.save(); ctx.translate(PAD, PAD);
    drawGraph(ctx, g, CARD - 2 * PAD, CARD - 2 * PAD, f, P, 1);
    ctx.restore();
    footer(ctx, 0, left, right);
    const delay = f === n ? hold : f === 0 ? first : f <= nE ? edgeMs : f <= nE + 8 ? pauseMs : moveMs;
    out.push({ png: cv.toDataURL("image/png"), delay });
  }
  return out;
}

/** Small centred caption: [text, font, colour] parts. */
function caption(ctx, cx, y, parts) {
  ctx.textBaseline = "middle"; ctx.textAlign = "left";
  const widths = parts.map(([txt, font]) => { ctx.font = font; return ctx.measureText(txt).width; });
  let x = cx - widths.reduce((a, b) => a + b, 0) / 2;
  parts.forEach(([txt, font, col], i) => { ctx.font = font; ctx.fillStyle = col; ctx.fillText(txt, x, y); x += widths[i]; });
}

/**
 * A mosaic: rows of plans (each row at one scale) and a last row of
 * connectivity graphs, all animating together. Every synthesis and graph is
 * stretched over the same n frames, so they start and finish at once.
 */
export function gridFrames(planRows, graphIds, { cell = 180, gap = 12, margin = 28, label = 16, graphH = 160, n = 72, ms = 55, first = 600, hold = 3200 } = {}) {
  const P = palette();
  const rows = planRows.map((ids) => { const items = ids.map((id) => kept.get(id)); return { items, ...sharedScale(items.map((it) => it.schematic), cell, cell) }; });
  const graphs = graphIds.map((id) => kept.get(id).graph);
  const cols = Math.max(graphs.length, ...rows.map((r) => r.items.length));
  const W = 2 * margin + cols * cell + (cols - 1) * gap;
  const H = 2 * margin + rows.reduce((a, r) => a + r.h + label + gap, 0) + graphH + label;
  const cv = document.createElement("canvas");
  cv.width = W; cv.height = H;
  const ctx = cv.getContext("2d");
  const lab = `400 7.5px ${FONT}`, val = `600 8px ${MONO}`, num = `600 8px ${FONT}`, word = `400 8px ${FONT}`;
  const out = [];
  for (let f = 0; f < n; f++) {
    const t = f / (n - 1);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = P.surface; ctx.fillRect(0, 0, W, H);
    let y = margin;
    for (const r of rows) {
      r.items.forEach((it, k) => {
        const syn = getSynthesis(it.schematic), fr = syn.frames, x = margin + k * (cell + gap);
        ctx.setTransform(1, 0, 0, 1, x, y);
        paintFrame(ctx, it.schematic, syn, fr[Math.round(t * (fr.length - 1))], cell, r.h, P,
          { showEdges: false, showWalls: true, furniture: "line50", showLabels: false, showCaption: false, showCompass: false, fitDims: r.fits[k] });
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        const c = it.table.computed;
        caption(ctx, x + cell / 2, y + r.h + label / 2, [["Q ", lab, P.ink3], [Number(c.qvci).toFixed(2), val, P.accent], ["   A ", lab, P.ink3], [Number(c.aqi).toFixed(2), val, P.accent]]);
      });
      y += r.h + label + gap;
    }
    graphs.forEach((g, k) => {
      const x = margin + k * (cell + gap);
      ctx.setTransform(1, 0, 0, 1, x, y);
      drawGraph(ctx, g, cell, graphH, Math.round(t * totalSteps(g)), P, 0.85);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      const rooms = g.nodes.filter((nd) => !nd.is_direction && nd.type !== "outside").length;
      caption(ctx, x + cell / 2, y + graphH + label / 2, [[String(rooms), num, P.ink], [" rooms   ", word, P.ink3], [String((g.edges_ordered || []).length), num, P.ink], [" edges", word, P.ink3]]);
    });
    out.push({ png: cv.toDataURL("image/png"), delay: f === n - 1 ? hold : f === 0 ? first : ms });
  }
  return out;
}
