// nn_parity.mjs: checks the JavaScript port against the PyTorch / prototype ground truth.
// usage: node tools/parity/nn_parity.mjs <weights_dir> <reference.json>
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { loadWeights, AreaNet, ZoneNet, ConnNet } from "../../docs/js/engine/nn.js";
import * as S from "../../docs/js/engine/scoring.js";
import { PyRandom, NumpyLegacy } from "../../docs/js/engine/rng.js";
import { generateAreaVariations, packCirclesNatural } from "../../docs/js/engine/area.js";

const [wdir, refPath] = process.argv.slice(2);
const ref = JSON.parse(readFileSync(refPath, "utf8"));
const load = (name) => {
  const buf = readFileSync(join(wdir, `${name}.bin`));
  return loadWeights(JSON.parse(readFileSync(join(wdir, `${name}.json`), "utf8")),
    buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
};
const area = new AreaNet(load("area")), zone = new ZoneNet(load("zone")), conn = new ConnNet(load("conn"));

const rows = [];
const maxAbs = (a, b) => { let m = 0; const fa = a.flat(Infinity), fb = b.flat(Infinity); if (fa.length !== fb.length) return Infinity; for (let i = 0; i < fa.length; i++) m = Math.max(m, Math.abs(fa[i] - fb[i])); return m; };
const report = (stage, check, value, ok) => rows.push({ stage, check, value, ok: ok ? "PASS" : "FAIL" });
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// ── RNG streams ───────────────────────────────────────────────────────
{
  const py = ref.rng.py, r = new PyRandom(42);
  const sh = [...Array(10).keys()]; r.shuffle(sh);
  const stream = Array.from({ length: 5 }, () => r.random());
  const ch = Array.from({ length: 10 }, () => r.choice([3, 1, 4, 1, 5, 9, 2, 6]));
  const un = Array.from({ length: 3 }, () => r.uniform(0, 6.283185307179586));
  report("RNG", "Python random.Random(42): random/shuffle/choice/uniform", `max |Δ| ${maxAbs([stream, un], [py.stream, py.uniform]).toExponential(1)}`,
    maxAbs(stream, py.stream) === 0 && eq(sh, py.shuffled) && eq(ch, py.choice) && maxAbs(un, py.uniform) === 0);
  const np = new NumpyLegacy(42), rn = Array.from(np.randn(12));
  const ri = [0, 1, 42, 99].map((s) => new NumpyLegacy(s).randint(0, 200));
  report("RNG", "numpy RandomState: randn / randint", `max |Δ| ${maxAbs(rn, ref.rng.np.randn).toExponential(1)}, randint ${eq(ri, ref.rng.np.randint) ? "equal" : "differs"}`,
    maxAbs(rn, ref.rng.np.randn) < 1e-15 && eq(ri, ref.rng.np.randint));
}

// ── Stage 1 ───────────────────────────────────────────────────────────
for (const [ci, c] of ref.area.cases.entries()) {
  const { mu, logSigma } = area.forward(ref.area.vocab.map((t) => c.counts[t] || 0));
  const d = Math.max(maxAbs(Array.from(mu), c.mu), maxAbs(Array.from(logSigma), c.log_sigma));
  const vars = generateAreaVariations(area, c.counts, c.total_area, { seed: 42 });
  const areasJs = vars.map((v) => v.rooms.map((r) => r.area)), areasPy = c.variations.map((v) => v.rooms.map((r) => r.area));
  const da = maxAbs(areasJs, areasPy);
  report("1 Area", `case ${ci}: network μ/logσ + ${vars.length} area variations (seed 42)`,
    `net ${d.toExponential(1)} · room areas ${da.toExponential(1)} m²`, d < 1e-5 && da < 1e-4);
  // bubble packer fed the prototype's radii. A layout may only differ where two
  // mirror-image spots are exactly as close to the centre (an exact tie that the
  // last bit of sin/cos decides); every bubble's distance from the centre then
  // still matches as a set up to that choice, so check identical-or-tie.
  let same = 0, ties = 0, bad = 0;
  c.variations.forEach((v, vi) => {
    const rooms = v.rooms.map((r) => ({ room_id: r.room_id, room_type: r.room_id.replace(/_\d+$/, ""), area: r.area, radius: r.radius }));
    packCirclesNatural(rooms, 42 + vi);
    const d = maxAbs(rooms.map((r) => r.pos_natural), v.rooms.map((r) => r.pos));
    if (d < 1e-9) { same++; return; }
    // first divergent bubble (in placement order ≈ nearest to the centre first) must be a tie
    const rad = (p) => Math.hypot(p[0], p[1]);
    const div = rooms.map((r, i) => ({ js: r.pos_natural, py: v.rooms[i].pos }))
      .filter((e) => Math.abs(e.js[0] - e.py[0]) + Math.abs(e.js[1] - e.py[1]) > 1e-9)
      .sort((a, b) => rad(a.py) - rad(b.py))[0];
    if (Math.abs(rad(div.js) - rad(div.py)) < 1e-9) ties++; else bad++;
  });
  report("1 Area", `case ${ci}: bubble packing (prototype radii)`, `${same}/${c.variations.length} identical, ${ties} mirror tie-break(s)`, bad === 0);
}

// ── Stage 2 ───────────────────────────────────────────────────────────
for (const [ci, c] of ref.zone.entries()) {
  const n = c.n;
  // scoring tables + reachable range (scipy Hungarian vs JS Hungarian)
  const T = S.tables(c.rooms, c.site), w = S.weights(c.rooms);
  const allowed = c.allowed.map((r) => r.map(Boolean));
  const reach = S.reachable(T.A, T.Q, w, allowed, c.cap), free = S.reachable(T.A, T.Q, w, allowed);
  const dT = Math.max(maxAbs(T.A, c.A), maxAbs(T.Q, c.Q), maxAbs(T.L, c.L), maxAbs(w, c.w));
  const dR = Math.max(maxAbs([reach.Q, reach.A], [c.reach.Q, c.reach.A]), maxAbs([free.Q, free.A], [c.reach_free.Q, c.reach_free.A]));
  report("2 Zones", `case ${ci} (${n} rooms): scoring tables A/Q/L + reachable range`, `tables ${dT.toExponential(1)} · reach ${dR.toExponential(1)}`, dT < 1e-6 && dR < 1e-6);
  // features
  const feats = S.roomFeatures(c.rooms), { zv, zn } = S.siteContext(c.site);
  const dF = Math.max(maxAbs(Array.from(feats), c.feats.flat()), maxAbs(zv, c.zv), maxAbs(zn, c.zn));
  // encoder
  const emb = zone.encode(feats, n, zv, zn);
  const dE = maxAbs(Array.from(emb), c.emb0.flat());
  // teacher-forced logits for the 81 sequences torch decoded
  const B = c.sampled.length;
  const base = { feats, n, zv, zn, tgt: c.targets, present: c.present, cap: c.cap, allowed: allowed.flat(), temperature: 0.8 };
  const forced = zone.generate({ ...base, B, forced: c.sampled.map((z) => Int32Array.from(z)), record: true });
  let dL = 0;
  for (let s = 0; s < n; s++) for (let i = 0; i < B * 9; i++) {
    const a = forced.logits[s][i], b = c.logits[s][Math.floor(i / 9)][i % 9];
    if (b > -1e8 || a > -1e8) dL = Math.max(dL, Math.abs(a - b));
  }
  const greedy = zone.generate({ ...base, B: 1, greedy: true }).zones[0];
  report("2 Zones", `case ${ci}: features + GAT encoder + GRU logits (81 decodes)`, `feat ${dF.toExponential(1)} · enc ${dE.toExponential(1)} · logits ${dL.toExponential(1)}`, dF < 1e-6 && dE < 1e-5 && dL < 1e-4);
  report("2 Zones", `case ${ci}: greedy arrangement`, `${eq(Array.from(greedy), c.greedy) ? "identical" : "differs"} [${Array.from(greedy).map((z) => z + 1).join(" ")}]`, eq(Array.from(greedy), c.greedy));
  // scores of the prototype's top tables recomputed in JS
  let dS = 0;
  for (const t of c.tables_head) {
    const z1 = t.zones.map((z) => z + 1);
    const [q, a] = S.evaluate(z1, T.A, T.Q, w);
    dS = Math.max(dS, Math.abs(q - t.computed.qvci), Math.abs(a - t.computed.aqi));
  }
  report("2 Zones", `case ${ci}: QVCI/AQI of the prototype's 12 cards`, `max |Δ| ${dS.toExponential(1)} (the prototype rounds to 4 dp)`, dS < 6e-5);
}

// ── Stage 3 ───────────────────────────────────────────────────────────
for (const [ci, c] of ref.conn.cases.entries()) {
  const forced = conn.generate(c.type_idx, { forcedBits: c.bits });
  const dp = maxAbs(forced.probs, c.probs);
  const sampled = conn.generate(c.type_idx, { sample: true, rng: new PyRandom(42) });
  report("3 Connectivity", `case ${ci} (${c.node_types.length} nodes): edge probabilities + sampled edges (seed 42)`,
    `probs ${dp.toExponential(1)} · edges ${eq(sampled.edges, c.edges) ? "identical" : "differ"} (${c.edges.length})`, dp < 1e-5 && eq(sampled.edges, c.edges));
}

// ── timing ────────────────────────────────────────────────────────────
{
  const c = ref.zone[4];
  const feats = S.roomFeatures(c.rooms), { zv, zn } = S.siteContext(c.site);
  const base = { feats, n: c.n, zv, zn, tgt: c.targets, present: c.present, cap: c.cap, allowed: c.allowed.flat().map(Boolean), temperature: 0.8 };
  const t0 = performance.now();
  zone.generate({ ...base, B: 1, greedy: true });
  zone.generate({ ...base, B: 80, greedy: false, rng: new PyRandom(1) });
  const t1 = performance.now();
  const c5 = ref.zone[0];
  const f5 = S.roomFeatures(c5.rooms), s5 = S.siteContext(c5.site);
  const b5 = { feats: f5, n: c5.n, zv: s5.zv, zn: s5.zn, tgt: c5.targets, present: c5.present, cap: c5.cap, allowed: c5.allowed.flat().map(Boolean), temperature: 0.8 };
  const t2 = performance.now();
  zone.generate({ ...b5, B: 1, greedy: true });
  zone.generate({ ...b5, B: 80, greedy: false, rng: new PyRandom(1) });
  const t3 = performance.now();
  console.log(`\ntiming (Node, 1 thread): 81 decodes · ${c5.n} rooms ${(t3 - t2).toFixed(0)} ms · ${c.n} rooms ${(t1 - t0).toFixed(0)} ms`);
}

console.table(rows);
const fails = rows.filter((r) => r.ok !== "PASS").length;
console.log(fails ? `${fails} check(s) FAILED` : `all ${rows.length} checks passed`);
process.exit(fails ? 1 : 0);
