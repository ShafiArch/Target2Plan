// stage3.js: Connectivity. One graph per selected zone plan, the same edges on
// every card (the edge model runs once), primaries pinned in their zone,
// secondary rooms relocated next to their primary room. The animation reveals
// the predicted edges, pauses, then moves the relocated rooms.
import { h, clear, palette, roomColor, fitCanvas, onResize, emptyState, toast, pad2, offscreen, canvasBlob, downloadBlob, hexA, FONT, numberInput } from "./common.js";

const MOVE_FRAMES = 24, PAUSE_FRAMES = 8;
const nEdges = (g) => (g.edges_ordered || []).length;
const hasMoves = (g) => g.nodes.some((n) => Math.abs(n.x - n.x_start) > 1e-6 || Math.abs(n.y - n.y_start) > 1e-6);
export const totalSteps = (g) => nEdges(g) + (hasMoves(g) ? PAUSE_FRAMES + MOVE_FRAMES : 0);
function eased(g, frame) {
  if (!hasMoves(g)) return 1;
  const t = Math.max(0, Math.min(1, (frame - (nEdges(g) + PAUSE_FRAMES)) / MOVE_FRAMES));
  return t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
}

/** Paint a graph at animation step `frame` into a w x h area (animated_graph.paintEvent). */
export function drawGraph(ctx, g, w, h, frame, P = palette(), fs = 1) {
  const src = Number(g.canvas_size || 200), side = Math.min(w, h), s = side / src, ox = (w - side) / 2, oy = (h - side) / 2;
  const sc = (x, y) => [ox + x * s, oy + y * s];
  const nodeR = Math.max(3, side / 40);
  const [rxs, rys, rws, rhs] = g.plan_rect || [0, 0, src, src];
  const [rx, ry] = sc(rxs, rys), [rx2, ry2] = sc(rxs + rws, rys + rhs), rw = rx2 - rx, rh = ry2 - ry;
  ctx.font = `${7.5 * fs}px ${FONT}`; ctx.fillStyle = P.a("ink3", 0.55); ctx.textAlign = "left"; ctx.textBaseline = "top";
  for (let row = 0; row < 3; row++) for (let col = 0; col < 3; col++) ctx.fillText(`Z${row * 3 + col + 1}`, rx + (col * rw) / 3 + 2, ry + (row * rh) / 3 + 1);
  ctx.font = `700 ${9 * fs}px ${FONT}`; ctx.fillStyle = P.a("ink3", 0.85);
  ctx.textAlign = "center"; ctx.textBaseline = "bottom"; ctx.fillText("N", rx + rw / 2, ry - 2);
  ctx.textBaseline = "top"; ctx.fillText("S", rx + rw / 2, ry + rh + 2);
  ctx.textBaseline = "middle"; ctx.textAlign = "left"; ctx.fillText("E", rx + rw + 3, ry + rh / 2);
  ctx.textAlign = "right"; ctx.fillText("W", rx - 3, ry + rh / 2);
  const e = eased(g, frame);
  const pos = new Map(g.nodes.map((n) => [n.id, sc(n.x_start + (n.x - n.x_start) * e, n.y_start + (n.y - n.y_start) * e)]));
  if (hasMoves(g) && e > 0) {
    for (const n of g.nodes) {
      if (!n.relocated) continue;
      const [sx, sy] = sc(n.x_start, n.y_start), [fx, fy] = sc(n.x, n.y);
      ctx.strokeStyle = P.a("ink3", 0.55); ctx.lineWidth = 0.8; ctx.setLineDash([0.8, 1.6]);
      ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(fx, fy); ctx.stroke(); ctx.setLineDash([]);
      ctx.lineWidth = 0.7; ctx.beginPath(); ctx.arc(sx, sy, nodeR * 0.9, 0, 2 * Math.PI); ctx.stroke();
    }
  }
  const edges = g.edges_ordered || [], vis = Math.min(edges.length, frame);
  for (let i = 0; i < vis; i++) {
    const a = pos.get(edges[i].source), b = pos.get(edges[i].target);
    if (!a || !b) continue;
    const latest = i === frame - 1;
    ctx.strokeStyle = P.a("ink2", latest ? 0.9 : 0.55); ctx.lineWidth = latest ? 1.6 : 0.9;
    ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
  }
  for (const n of g.nodes) {
    if (n.is_direction) continue;
    const [x, y] = pos.get(n.id);
    if (n.is_primary) {
      ctx.strokeStyle = P.a("accent", 0.3); ctx.lineWidth = 0.7; ctx.setLineDash([1.05, 1.05]);
      ctx.beginPath(); ctx.arc(x, y, nodeR * 1.7, 0, 2 * Math.PI); ctx.stroke(); ctx.setLineDash([]);
    }
    ctx.fillStyle = hexA(roomColor(n.type), 0.92); ctx.strokeStyle = P.surface; ctx.lineWidth = 0.8;
    ctx.beginPath(); ctx.arc(x, y, nodeR * (n.type === "outside" ? 0.85 : 1), 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
  }
}

export function mountConnectivity(body, { store, runtime, goto }) {
  let speed = (() => { try { return Number(localStorage.getItem("t2p.connSpeed") ?? 25); } catch { return 25; } })();
  let busy = false, cards = [], lastGraphs = null, error = null;
  const scroll = h("div.scroll", { style: { padding: "12px 12px 8px" } });

  const bRegen = h("button.link-btn", { on: { click: () => regenerate() } }, "Regenerate");
  const speedIn = numberInput(speed, { min: 0, max: 250, step: 5, suffix: "ms", width: 82, onChange: (v) => { speed = v; try { localStorage.setItem("t2p.connSpeed", v); } catch { /* ignore */ } replayAll(); } });
  const bReplay = h("button.link-btn", { on: { click: () => replayAll() } }, "↻ Replay");
  const bAll = h("button.chip", { on: { click: () => { const st = store.stage3; store.setStage3Selection(st.selected_idxs.length === st.graphs.length ? [] : st.graphs.map((g) => g.meta.plan_index)); } } }, "■ Select all");
  const bBatch = h("button.link-btn", { title: "Save the graphs as one contact-sheet PNG", on: { click: saveBatch } }, "Save Batch");
  const selLbl = h("span.sel-label");
  const bProceed = h("button.btn-primary", { on: { click: () => goto("schematic") } }, "Proceed to Schematic  →");
  body.append(scroll, h("div.toolbar", bRegen, h("span.sub", "Anim speed"), speedIn, bReplay, bAll, bBatch, h("div.grow"), selLbl, bProceed));

  async function regenerate() {
    const st2 = store.stage2;
    if (!st2.tables.length || !st2.selected_idxs.length) return;
    busy = true; render();
    const ep = store.epoch;
    try {
      const pg = store.inputs.plan_geometry;
      const graphs = await runtime.run("conn", { tables: st2.tables, selected: st2.selected_idxs, opts: { planWidth: pg.width, planHeight: pg.height, roomCounts: { ...store.inputs.room_counts } } });
      if (ep !== store.epoch) return;
      error = null; busy = false;
      store.setStage3(graphs, graphs.map((g) => g.meta.plan_index));
    } catch (err) {
      if (ep !== store.epoch) return;
      error = err.message; busy = false; toast(`Connectivity model failed: ${err.message}`); render();
    }
  }

  function card(g, sel) {
    const cv = h("canvas");
    const rooms = g.nodes.filter((n) => !n.is_direction && n.type !== "outside").length;
    const relocs = g.relocations || [];
    const foot = h("div.plain-foot", h("span", h("b", String(rooms)), " ", h("span.g", "rooms")),
      relocs.length ? h("span", { title: "Secondary rooms moved next to the primary room they connect to:\n" + relocs.map((d) => d.reason).join("\n") }, h("b", String(relocs.length)), " ", h("span.g", "moved")) : null,
      h("span.grow"), h("span", h("b", String(nEdges(g))), " ", h("span.g", "edges")));
    const el = h("div.plain-card.conn-card", { on: { click: () => toggle(g.meta.plan_index) } },
      h("div.plain-head", h("span.idx", `#${pad2(g.meta.plan_index)}`), sel ? h("span.badge", "✓ SELECTED") : null), h("div.plain-body", cv), foot);
    let frame = totalSteps(g), timer = null;
    el.draw = () => { const { ctx, w, h: H } = fitCanvas(cv); drawGraph(ctx, g, w, H, frame); };
    el.replay = () => {
      clearInterval(timer);
      if (speed <= 0 || totalSteps(g) === 0) { frame = totalSteps(g); el.draw(); return; }
      frame = 0; el.draw();
      timer = setInterval(() => { frame += 1; if (frame >= totalSteps(g)) clearInterval(timer); el.draw(); }, speed);
    };
    el.stop = () => clearInterval(timer);
    el.g = g;
    return el;
  }

  function render() {
    cards.forEach((c) => c.stop()); clear(scroll); cards = [];
    const st2 = store.stage2, st3 = store.stage3;
    if (busy) scroll.append(h("div.empty-state", h("span.busy", "Decoding the access graph…")));
    else if (!st2.selected_idxs.length) scroll.append(emptyState("Waiting for zone selection", "Complete Stage 2 (select one or more zone-assignment plans) to generate connectivity graphs."));
    else if (!st3.graphs.length) {
      const n = st2.selected_idxs.length;
      scroll.append(emptyState("Ready to generate connectivity", error ? `The model could not run: ${error}` : `The connectivity model will produce a graph for each of the ${n} selected zone-assignment plan${n !== 1 ? "s" : ""}. Primary rooms stay pinned at their zone positions; secondary rooms move next to the room they connect to.`, `Generate ${n} graph${n !== 1 ? "s" : ""}`, () => regenerate()));
    } else {
      const sel = new Set(st3.selected_idxs), grid = h("div.grid4");
      for (const g of st3.graphs) { const c = card(g, sel.has(g.meta.plan_index)); cards.push(c); grid.append(c); }
      scroll.append(grid);
      requestAnimationFrame(() => cards.forEach((c) => c.replay()));
    }
    refreshToolbar();
  }

  function toggle(idx) {
    const sel = new Set(store.stage3.selected_idxs);
    if (sel.has(idx)) sel.delete(idx); else sel.add(idx);
    store.setStage3Selection([...sel]);
  }
  function refreshSelection() {
    const sel = new Set(store.stage3.selected_idxs);
    for (const c of cards) {
      const on = sel.has(c.g.meta.plan_index), head = c.querySelector(".plain-head"), badge = head.querySelector(".badge");
      if (on && !badge) head.append(h("span.badge", "✓ SELECTED")); else if (!on && badge) badge.remove();
    }
    refreshToolbar();
  }
  function refreshToolbar() {
    const st = store.stage3, has = st.graphs.length > 0, n = st.selected_idxs.length;
    speedIn.input.disabled = !has; bReplay.disabled = !(has && speed > 0); bAll.disabled = !has; bBatch.disabled = !has;
    bRegen.disabled = !store.stage2.selected_idxs.length || busy;
    selLbl.textContent = !has ? "" : n === 0 ? "Click graphs to select" : n === 1 ? "1 graph selected" : `${n} graphs selected`;
    bProceed.disabled = !has || n === 0;
    const all = has && n === st.graphs.length;
    bAll.classList.toggle("active", all); bAll.textContent = all ? "■ Select none" : "■ Select all";
  }
  function replayAll() { cards.forEach((c) => c.replay()); refreshToolbar(); }

  async function saveBatch() {
    const graphs = store.stage3.graphs;
    if (!graphs.length) return toast("Generate graphs before saving a batch sheet.");
    const cols = Math.min(4, graphs.length), rows = Math.ceil(graphs.length / cols), cell = 260, head = 40;
    const W = cols * cell, H = head + rows * (cell + 18);
    const { cv, ctx } = offscreen(W, H, 3), P = palette();
    ctx.fillStyle = P.surface; ctx.fillRect(0, 0, W, H);
    ctx.font = `600 13px ${FONT}`; ctx.fillStyle = P.ink; ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.fillText(`Target2Plan · connectivity · ${graphs.length} graph${graphs.length > 1 ? "s" : ""}`, 12, 20);
    graphs.forEach((g, i) => {
      const x = (i % cols) * cell, y = head + Math.floor(i / cols) * (cell + 18);
      ctx.save(); ctx.translate(x, y); drawGraph(ctx, g, cell, cell, totalSteps(g), P, 1.1); ctx.restore();
      ctx.font = `11px ${FONT}`; ctx.fillStyle = P.ink2; ctx.textAlign = "center";
      ctx.fillText(`#${pad2(g.meta.plan_index)} · ${nEdges(g)} edges`, x + cell / 2, y + cell + 6);
    });
    downloadBlob(await canvasBlob(cv), "connectivity_batch.png");
  }

  store.on("stage3", () => { if (store.stage3.graphs !== lastGraphs) { lastGraphs = store.stage3.graphs; render(); } else refreshSelection(); });
  store.on("stage2", () => { if (!busy && !store.stage3.graphs.length) render(); else refreshToolbar(); });
  store.on("theme", () => cards.forEach((c) => c.draw()));
  store.on("reset", () => { busy = false; lastGraphs = store.stage3.graphs; error = null; render(); });
  store.on("goto:connectivity", () => regenerate());
  onResize(scroll, () => cards.forEach((c) => c.draw()));
  render();
}
