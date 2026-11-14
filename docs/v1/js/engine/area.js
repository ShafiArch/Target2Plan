// area.js: Stage 1 pipeline (models/area_model.py + area_mock.pack_circles_natural).
// Same seed → same variations, areas and bubble positions as the Python prototype.
import { NumpyLegacy, PyRandom } from "./rng.js";

const softmax = (x) => {
  const m = Math.max(...x);
  const e = x.map((v) => Math.exp(v - m));
  const s = e.reduce((a, b) => a + b, 0) + 1e-12;
  return e.map((v) => v / s);
};

function farthestPointIndices(S, k, seed) {
  const n = S.length;
  k = Math.max(1, Math.min(k, n));
  const start = new NumpyLegacy(seed).randint(0, n);
  const chosen = [start];
  const d2s = (a, b) => a.reduce((s, v, i) => { const d = v - b[i]; return s + d * d; }, 0);
  const d2 = S.map((x) => d2s(x, S[start]));
  for (let c = 1; c < k; c++) {
    let nxt = 0;
    for (let i = 1; i < n; i++) if (d2[i] > d2[nxt]) nxt = i;      // np.argmax: first maximum
    chosen.push(nxt);
    for (let i = 0; i < n; i++) d2[i] = Math.min(d2[i], d2s(S[i], S[nxt]));
  }
  return [...new Set(chosen)];
}

function circleTangent(a, b, cr, eps = 1e-3) {
  const dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy;
  if (d2 < 1e-12) return [];
  const d = Math.sqrt(d2), R1 = a.r + cr, R2 = b.r + cr;
  if (d > R1 + R2 + eps || d < Math.abs(R1 - R2) - eps) return [];
  const aa = (R1 * R1 - R2 * R2 + d2) / (2 * d), h2 = R1 * R1 - aa * aa;
  if (h2 < 0) return [];
  const h = Math.sqrt(h2), ux = dx / d, uy = dy / d, mx = a.x + aa * ux, my = a.y + aa * uy;
  return [[mx - h * uy, my + h * ux], [mx + h * uy, my - h * ux]];
}

function noOverlap([px, py], cr, placed, eps = 0.01) {
  for (const c of placed) {
    const dx = px - c.x, dy = py - c.y, th = c.r + cr - eps;
    if (dx * dx + dy * dy < th * th) return false;
  }
  return true;
}

export function packCirclesNatural(rooms, seed = 42) {
  const active = rooms.filter((r) => (r.radius || 0) > 0);
  if (!active.length) return;
  const rng = new PyRandom(seed);
  let work = active.map((r) => ({ src: r, id: r.room_id, r: r.radius, type: r.room_type, x: 0, y: 0 }));
  const byType = new Map();
  for (const c of work) { if (!byType.has(c.type)) byType.set(c.type, []); byType.get(c.type).push(c); }
  for (const lst of byType.values()) rng.shuffle(lst);
  const ordered = [];
  while ([...byType.values()].some((l) => l.length)) {
    const groups = [...byType.entries()].filter(([, l]) => l.length);
    groups.sort((a, b) => b[1][0].r - a[1][0].r);                        // stable, like Python's sort
    for (const [, lst] of groups) ordered.push(lst.shift());
  }
  work = ordered;
  work[0].x = 0; work[0].y = 0;
  const placed = [work[0]];
  if (work.length >= 2) {
    const ang = rng.uniform(0, 2 * Math.PI), d = placed[0].r + work[1].r + 0.05;
    work[1].x = d * Math.cos(ang); work[1].y = d * Math.sin(ang);
    placed.push(work[1]);
  }
  for (const c of work.slice(2)) {
    let best = null, bestD2 = Infinity;
    for (let i = 0; i < placed.length; i++)
      for (let j = i + 1; j < placed.length; j++)
        for (const p of circleTangent(placed[i], placed[j], c.r))
          if (noOverlap(p, c.r, placed)) { const d2 = p[0] * p[0] + p[1] * p[1]; if (d2 < bestD2) { bestD2 = d2; best = p; } }
    if (!best) {
      for (const ref of placed) for (let ai = 0; ai < 36; ai++) {
        const a = (ai / 36) * 2 * Math.PI, d = ref.r + c.r + 0.1;
        const p = [ref.x + d * Math.cos(a), ref.y + d * Math.sin(a)];
        if (noOverlap(p, c.r, placed)) { const d2 = p[0] * p[0] + p[1] * p[1]; if (d2 < bestD2) { bestD2 = d2; best = p; } }
      }
    }
    if (!best) {
      const maxR = Math.max(...placed.map((p) => Math.sqrt(p.x * p.x + p.y * p.y) + p.r));
      const a = rng.uniform(0, 2 * Math.PI), d = maxR + c.r + 5;
      best = [d * Math.cos(a), d * Math.sin(a)];
    }
    [c.x, c.y] = best;
    placed.push(c);
  }
  for (const w of work) w.src.pos_natural = [w.x, w.y];
}

// legacy RandomState.choice(n, k, replace=False) = permutation(n)[:k]
function legacyChoiceNoReplace(rs, n, k) {
  const arr = Array.from({ length: n }, (_, i) => i);
  for (let i = n - 1; i > 0; i--) {
    const j = rs.randint(0, i + 1);
    const t = arr[i]; arr[i] = arr[j]; arr[j] = t;
  }
  return arr.slice(0, k);
}

function kmeansRepresentatives(X, k, seed = 42, iters = 25) {
  const n = X.length, d = X[0].length;
  k = Math.max(1, Math.min(k, n));
  const rs = new NumpyLegacy(seed);
  let C = legacyChoiceNoReplace(rs, n, k).map((i) => [...X[i]]);
  const dist = (a, b) => { let s = 0; for (let q = 0; q < d; q++) { const t = a[q] - b[q]; s += t * t; } return s; };
  const argmin = (arr) => { let b = 0; for (let i = 1; i < arr.length; i++) if (arr[i] < arr[b]) b = i; return b; };
  for (let it = 0; it < iters; it++) {
    const labels = X.map((x) => argmin(C.map((c) => dist(x, c))));
    const newC = C.map((c) => [...c]);
    for (let j = 0; j < k; j++) {
      const members = X.filter((_, i) => labels[i] === j);
      if (members.length) {
        const m = new Array(d).fill(0);
        for (const x of members) for (let q = 0; q < d; q++) m[q] += x[q];
        newC[j] = m.map((v) => v / members.length);
      } else newC[j] = [...X[rs.randint(0, n)]];
    }
    C = newC;
  }
  const reps = C.map((c) => argmin(X.map((x) => dist(x, c))));
  return [...new Set(reps)];
}

/** generate_area_variations: net: AreaNet, counts: {type: n}, totalArea in plan units */
export function generateAreaVariations(net, counts, totalArea, { nVariations = 12, nSamples = 200, diversity = 1, seed = 42, method = "FarthestPoint" } = {}) {
  const vocab = net.vocab;
  const cvec = vocab.map((t) => counts[t] || 0);
  if (cvec.reduce((a, b) => a + b, 0) <= 0) return [];
  // numpy keeps μ and σ in float32 (the network's output dtype); mirror that
  const { mu: mu64, logSigma } = net.forward(cvec);
  const mu = Array.from(mu64, Math.fround);
  const sigma = Array.from(logSigma, (v) => Math.fround(Math.exp(Math.fround(Math.min(Math.max(v, -5), 1)))));
  const np = new NumpyLegacy(seed);
  const S = [];
  for (let s = 0; s < nSamples; s++) {
    const eps = np.randn(mu.length);
    S.push(softmax(mu.map((m, i) => m + Math.fround(sigma[i] * diversity) * eps[i])));
  }
  const k = Math.min(nVariations, S.length);
  const idx = String(method).toLowerCase() === "kmeans" ? kmeansRepresentatives(S, k, seed, 30) : farthestPointIndices(S, k, seed);
  const reps = idx.map((i) => S[i]);
  return reps.map((share, vi) => {
    const rooms = [];
    for (const t of vocab) {
      const c = counts[t] || 0;
      if (c <= 0) continue;
      const a = (share[vocab.indexOf(t)] * totalArea) / c;
      const radius = a > 0 ? Math.sqrt(a / Math.PI) : 2;
      for (let i = 0; i < c; i++) rooms.push({ room_id: `${t}_${i}`, room_type: t, area: a, radius });
    }
    packCirclesNatural(rooms, 42 + vi);
    return { variation_idx: vi, rooms, total_area: totalArea };
  });
}
