// pipeline_parity.mjs: checks the web engine (stages 2 to 4) against the prototype's pipeline dump.
// usage: node tools/parity/pipeline_parity.mjs <pipeline.json>
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const E = (m) => import(join(ROOT, "docs", "js", "engine", m));
const { loadWeights, ConnNet } = await E("nn.js");
const { PlanSetup } = await E("zones.js");
const { generateConnectivityGraphs } = await E("connectivity.js");
const { makeSchematics, synthesize, referenceBox, paretoFrontIndices } = await E("schematic.js");

const dump = JSON.parse(readFileSync(process.argv[2], "utf8"));
const loadModel = (name) => {
  const buf = readFileSync(join(ROOT, "docs", "models", `${name}.bin`));
  return loadWeights(JSON.parse(readFileSync(join(ROOT, "docs", "models", `${name}.json`), "utf8")), buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
};
const conn = new ConnNet(loadModel("conn"));

const rows = [];
const report = (c, check, value, ok) => rows.push({ case: c, check, value, ok: ok ? "PASS" : "FAIL" });
const maxAbs = (a, b) => { const fa = a.flat(Infinity), fb = b.flat(Infinity); if (fa.length !== fb.length) return Infinity; let m = 0; for (let i = 0; i < fa.length; i++) { const x = fa[i], y = fb[i]; if (typeof x === "number" && typeof y === "number") m = Math.max(m, Math.abs(x - y)); else if (x !== y) return Infinity; } return m; };
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

for (const c of dump.cases) {
  const inputs = c.inputs;
  // ── Stage 2: table building for the prototype's arrangements ──
  const ps = new PlanSetup(inputs, c.variation);
  let dT = 0, fieldsOk = true;
  for (const t of c.tables) {
    const zones1 = t.rooms.filter((r) => r.zoned).map((r) => r.zone_assignment + 1);
    const js = ps.table(zones1, [t.targets.qvci, t.targets.aqi], t.targets.present, t.decode);
    for (const k of ["qvci", "aqi", "avg_spl_dB", "concentration"]) dT = Math.max(dT, Math.abs(js.computed[k] - t.computed[k]));
    fieldsOk &&= js.computed.zone_spread === t.computed.zone_spread && js.computed.valid === t.computed.valid &&
      (t.computed.d_target == null ? js.computed.d_target == null : Math.abs(js.computed.d_target - t.computed.d_target) < 2e-4);
    fieldsOk &&= same(js.rooms.map((r) => [r.room_id, r.zone_assignment, r.acoustic_ok, r.scored]), t.rooms.map((r) => [r.room_id, r.zone_assignment, r.acoustic_ok, r.scored]));
    dT = Math.max(dT, maxAbs(js.rooms.map((r) => [r.quality_view_score ?? 0, r.acoustic_score ?? 0, r.est_avg_spl_dB ?? 0, r.area_m2]), t.rooms.map((r) => [r.quality_view_score ?? 0, r.acoustic_score ?? 0, r.est_avg_spl_dB ?? 0, r.area_m2])));
    fieldsOk &&= same(js.reachable, t.reachable) && same(js.capacity, t.capacity) && same(js.zone_weights, t.zone_weights) && same(js.zone_levels_dB, t.zone_levels_dB);
  }
  report(c.name, `Stage 2: ${c.tables.length} cards rebuilt (metrics, rooms, reachable range)`, `max |Δ| ${dT.toExponential(1)}`, dT <= 1.01e-4 && fieldsOk);

  // ── Stage 3: edges + layout, from the prototype's zone tables ──
  const pg = inputs.plan_geometry;
  const sel = c.tables.map((t) => t.table_index);
  const graphs = generateConnectivityGraphs(conn, c.tables, sel, { planWidth: pg.width, planHeight: pg.height, roomCounts: inputs.room_counts });
  let edgesOk = graphs.length === c.graphs.length, dPos = 0, relocOk = true;
  graphs.forEach((g, i) => {
    const p = c.graphs[i];
    edgesOk &&= same(g.edges_ordered, p.edges_ordered);
    dPos = Math.max(dPos, maxAbs(g.nodes.map((n) => [n.id, n.x, n.y, n.x_start, n.y_start]), p.nodes.map((n) => [n.id, n.x, n.y, n.x_start, n.y_start])));
    relocOk &&= same(g.nodes.map((n) => [n.zone_start, n.zone, n.facade, n.anchor, n.relocated]), p.nodes.map((n) => [n.zone_start, n.zone, n.facade, n.anchor, n.relocated]));
    relocOk &&= same(g.relocations.map((r) => r.label), p.relocations.map((r) => r.label)) && same(g.rooms_added, p.rooms_added);
  });
  report(c.name, `Stage 3: ${graphs.length} graphs (edges, relocation, node positions)`, `edges ${edgesOk ? "identical" : "differ"} · pos ${dPos.toExponential(1)}`, edgesOk && relocOk && dPos < 1e-9);

  // ── Stage 4: synthesis from the prototype's graphs ──
  const sch = makeSchematics(c.tables, c.graphs, c.graphs.map((g) => g.meta.plan_index), inputs);
  let dR = 0, wallsOk = true, openOk = true, furnOk = true, framesOk = true, dW = 0;
  sch.forEach((s, i) => {
    const [bw, bh] = referenceBox(s);
    const syn = synthesize(s, "C", bw, bh);
    const p = c.synthesis[i];
    dR = Math.max(dR, maxAbs(syn.rects.map((r) => [r.room_id, r.cx, r.cy, r.w, r.h]), p.rects));
    wallsOk &&= syn.walls.length === p.walls.length && same(syn.walls.map((w) => [w.kind, w.rooms]), p.walls.map((w) => [w[4], w[5]]));
    dW = Math.max(dW, maxAbs(syn.walls.map((w) => [w.x1, w.y1, w.x2, w.y2]), p.walls.map((w) => w.slice(0, 4))));
    const wi = new Map(syn.walls.map((w, k) => [w, k]));
    openOk &&= same(syn.openings.map((o) => [o.kind, wi.get(o.wall)]), p.openings.map((o) => [o[0], o[5]]));
    dW = Math.max(dW, maxAbs(syn.openings.map((o) => [o.x1, o.y1, o.x2, o.y2]), p.openings.map((o) => o.slice(1, 5))));
    furnOk &&= same(syn.furniture.map((f) => [f.room_id, f.name, f.side]), p.furniture.map((f) => f.slice(0, 3)));
    dW = Math.max(dW, maxAbs(syn.furniture.map((f) => f.box), p.furniture.map((f) => f[3])));
    framesOk &&= syn.frames.length === p.n_frames && same(syn.removed, p.removed) && Math.abs(syn.metre - p.metre) < 1e-9;
    dR = Math.max(dR, maxAbs(syn.frames[Math.floor(syn.frames.length / 3)].shapes.map((s_) => [s_.room_id, s_.cx, s_.cy]), p.frame_mid));
  });
  report(c.name, `Stage 4: ${sch.length} syntheses (rects, walls, doors/windows, furniture, frames)`,
    `rects ${dR.toExponential(1)} · geometry ${dW.toExponential(1)}${wallsOk && openOk && furnOk && framesOk ? "" : ` · walls ${wallsOk} openings ${openOk} furniture ${furnOk} frames ${framesOk}`}`,
    dR < 1e-7 && dW < 1e-7 && wallsOk && openOk && furnOk && framesOk);
  const par = paretoFrontIndices(sch);
  report(c.name, "Stage 4: Pareto front", `[${par.join(", ")}]`, same(par, c.pareto));
}
console.table(rows);
const fails = rows.filter((r) => r.ok !== "PASS").length;
console.log(fails ? `${fails} check(s) FAILED` : `all ${rows.length} checks passed`);
process.exit(fails ? 1 : 0);
