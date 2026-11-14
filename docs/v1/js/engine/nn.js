// nn.js: the three trained Target2Plan networks, running in plain JavaScript.
//
//   AreaNet  : LogisticNormalNet (Stage 1)  room counts → μ, log σ of area shares
//   ZoneNet  : v13 cGAT-AR       (Stage 2)  GAT encoder + 2-layer GRU decoder, masked AR decode
//   ConnNet  : EdgeARModel       (Stage 3)  type embeddings + GRU → edge probabilities
//
// Weights come from the exported .bin/.json pairs (float32, PyTorch row-major
// [out, in] layout). Activations are float64; results match PyTorch to ~1e-6.

export function loadWeights(json, buffer) {
  const all = new Float32Array(buffer);
  const t = (name) => {
    const e = json.tensors[name];
    if (!e) throw new Error(`missing tensor ${name}`);
    const n = e.shape.reduce((a, b) => a * b, 1);
    return all.subarray(e.offset, e.offset + n);
  };
  const shape = (name) => json.tensors[name].shape;
  return { t, shape, meta: json.meta, json };
}

// y[o] = b[o] + Σ_i W[o, i] · x[i]     (W row-major [outDim, inDim])
function linear(W, b, x, inDim, outDim, y = new Float64Array(outDim)) {
  for (let o = 0; o < outDim; o++) {
    let s = b ? b[o] : 0;
    const r = o * inDim;
    for (let i = 0; i < inDim; i++) s += W[r + i] * x[i];
    y[o] = s;
  }
  return y;
}

// Batched: Y[b, o] = bias[o] + Σ_i W[o, i] · X[b, i]; the weight row is reused across the batch
function linearBatch(W, bias, X, B, inDim, outDim, Y) {
  for (let o = 0; o < outDim; o++) {
    const r = o * inDim, bo = bias ? bias[o] : 0;
    for (let b = 0; b < B; b++) {
      const xo = b * inDim;
      let s = bo;
      for (let i = 0; i < inDim; i++) s += W[r + i] * X[xo + i];
      Y[b * outDim + o] = s;
    }
  }
  return Y;
}

const sigmoid = (v) => 1 / (1 + Math.exp(-v));
const relu = (v) => (v > 0 ? v : 0);

// PyTorch GRU cell (gate order r, z, n):
//   r = σ(Wir x + bir + Whr h + bhr)   z = σ(Wiz x + biz + Whz h + bhz)
//   n = tanh(Win x + bin + r ⊙ (Whn h + bhn))   h' = (1 − z) ⊙ n + z ⊙ h
// gi, gh: precomputed input / hidden projections (B × 3H); h updated in place.
function gruUpdate(gi, gh, h, B, H) {
  const H3 = 3 * H;
  for (let b = 0; b < B; b++) {
    const g = b * H3, ho = b * H;
    for (let k = 0; k < H; k++) {
      const r = sigmoid(gi[g + k] + gh[g + k]);
      const z = sigmoid(gi[g + H + k] + gh[g + H + k]);
      const n = Math.tanh(gi[g + 2 * H + k] + r * gh[g + 2 * H + k]);
      h[ho + k] = (1 - z) * n + z * h[ho + k];
    }
  }
}

// ─────────────────────────────────────────────────────────────────────
//  Stage 1: LogisticNormalNet
// ─────────────────────────────────────────────────────────────────────
export class AreaNet {
  constructor(w) {
    this.w = w;
    this.vocab = w.meta.vocab;
    [this.H, this.K] = w.shape("fc1.weight");
    this.L = w.shape("to_latent.weight")[0];
  }

  // counts in vocab order → { mu, logSigma } (input feature = log1p(counts))
  forward(counts) {
    const { t } = this.w, K = this.K, H = this.H, L = this.L;
    const x = Float64Array.from(counts, (c) => Math.log1p(c));
    const h1 = linear(t("fc1.weight"), t("fc1.bias"), x, K, H).map(relu);
    const h2 = linear(t("fc2.weight"), t("fc2.bias"), h1, H, H).map(relu);
    const z = linear(t("to_latent.weight"), t("to_latent.bias"), h2, H, L);
    return {
      mu: linear(t("mu_head.weight"), t("mu_head.bias"), z, L, K),
      logSigma: linear(t("logsig_head.weight"), t("logsig_head.bias"), z, L, K),
    };
  }
}

// ─────────────────────────────────────────────────────────────────────
//  Stage 2: v13 cGAT-AR zone model
// ─────────────────────────────────────────────────────────────────────
export class ZoneNet {
  constructor(w) {
    this.w = w;
    const c = w.meta.config;
    this.D = c.GNN_DIM; this.layers = c.GNN_LAYERS; this.heads = c.GNN_HEADS;
    this.G = c.GRU_DIM; this.Z = 9; this.FEAT = c.ROOM_FEAT_DIM; this.CTX = c.CTX_DIM;
    this.IN0 = this.D + this.CTX + this.Z;       // 168
    if (w.shape("gru.weight_ih_l0")[1] !== this.IN0) throw new Error("unexpected GRU input width");
  }

  // feats: n × 11 (row-major), zv/zn: 9 → embeddings n × D
  encode(feats, n, zv, zn) {
    const { t } = this.w, D = this.D, F = this.FEAT, IN = F + 18;
    const x = new Float64Array(IN);
    let h = new Float64Array(n * D);
    const Wp = t("proj.weight"), bp = t("proj.bias");
    for (let i = 0; i < n; i++) {
      for (let k = 0; k < F; k++) x[k] = feats[i * F + k];
      for (let k = 0; k < 9; k++) { x[F + k] = zv[k]; x[F + 9 + k] = zn[k]; }
      const y = linear(Wp, bp, x, IN, D);
      for (let o = 0; o < D; o++) h[i * D + o] = relu(y[o]);
    }
    const dh = D / this.heads;
    for (let l = 0; l < this.layers; l++) {
      const cat = new Float64Array(n * D);
      for (let hd = 0; hd < this.heads; hd++) {
        const W = t(`layers.${l}.heads.${hd}.W.weight`), a = t(`layers.${l}.heads.${hd}.a.weight`);
        const Wh = new Float64Array(n * dh);
        for (let i = 0; i < n; i++) linear(W, null, h.subarray(i * D, (i + 1) * D), D, dh, Wh.subarray(i * dh, (i + 1) * dh));
        const s1 = new Float64Array(n), s2 = new Float64Array(n);
        for (let i = 0; i < n; i++) {
          let p = 0, q = 0;
          for (let k = 0; k < dh; k++) { p += a[k] * Wh[i * dh + k]; q += a[dh + k] * Wh[i * dh + k]; }
          s1[i] = p; s2[i] = q;
        }
        for (let i = 0; i < n; i++) {
          const e = new Float64Array(n);
          let mx = -Infinity;
          for (let j = 0; j < n; j++) {
            const v = s1[i] + s2[j];
            e[j] = v > 0 ? v : 0.2 * v;                  // leaky_relu(·, 0.2)
            if (e[j] > mx) mx = e[j];
          }
          let sum = 0;
          for (let j = 0; j < n; j++) { e[j] = Math.exp(e[j] - mx); sum += e[j]; }
          for (let k = 0; k < dh; k++) {
            let acc = 0;
            for (let j = 0; j < n; j++) acc += e[j] * Wh[j * dh + k];
            cat[i * D + hd * dh + k] = acc / sum;
          }
        }
      }
      const g = t(`layers.${l}.norm.weight`), be = t(`layers.${l}.norm.bias`);
      const out = new Float64Array(n * D);
      for (let i = 0; i < n; i++) {
        let mean = 0;
        for (let k = 0; k < D; k++) { cat[i * D + k] += h[i * D + k]; mean += cat[i * D + k]; }
        mean /= D;
        let v = 0;
        for (let k = 0; k < D; k++) { const d = cat[i * D + k] - mean; v += d * d; }
        const inv = 1 / Math.sqrt(v / D + 1e-5);
        for (let k = 0; k < D; k++) out[i * D + k] = relu((cat[i * D + k] - mean) * inv * g[k] + be[k]);
      }
      h = out;
    }
    return h;
  }

  /**
   * Autoregressive decode of B arrangements in one batch.
   * opts: { feats, n, zv, zn, tgt:[q,a], present, cap: 9 ints, allowed: n×9 bools (row-major),
   *         B, greedy, temperature, rng (object with random()), forced?: Int32Array[B][n],
   *         record?: bool }
   * Returns { zones: Array<Int32Array(n)> (0-based), logits?: Float64Array[n][B*9] }
   */
  generate(o) {
    const { t } = this.w, D = this.D, G = this.G, Z = 9, IN0 = this.IN0, n = o.n, B = o.B;
    const emb = this.encode(o.feats, n, o.zv, o.zn);
    const Wih0 = t("gru.weight_ih_l0"), Whh0 = t("gru.weight_hh_l0"), bih0 = t("gru.bias_ih_l0"), bhh0 = t("gru.bias_hh_l0");
    const Wih1 = t("gru.weight_ih_l1"), Whh1 = t("gru.weight_hh_l1"), bih1 = t("gru.bias_ih_l1"), bhh1 = t("gru.bias_hh_l1");
    const W1 = t("head.0.weight"), b1 = t("head.0.bias"), W2 = t("head.3.weight"), b2 = t("head.3.bias");

    const h0 = new Float64Array(B * G), h1 = new Float64Array(B * G);
    const counts = new Float64Array(B * Z), prev = new Float64Array(B * Z);
    const X = new Float64Array(B * IN0);
    const gi = new Float64Array(B * 3 * G), gh = new Float64Array(B * 3 * G);
    const hid = new Float64Array(B * 128), logits = new Float64Array(B * Z);
    const zones = Array.from({ length: B }, () => new Int32Array(n));
    const rec = o.record ? [] : null;
    const tgt3 = [o.present ? o.tgt[0] : 0, o.present ? o.tgt[1] : 0, o.present ? 1 : 0];
    const nn = Math.max(n, 1), T = Math.max(o.temperature ?? 1, 1e-6);

    for (let s = 0; s < n; s++) {
      const roomsRem = Math.min(Math.max((n - s) / nn, 0), 1);
      for (let b = 0; b < B; b++) {
        const xo = b * IN0;
        for (let k = 0; k < D; k++) X[xo + k] = emb[s * D + k];
        let c = xo + D;
        X[c++] = tgt3[0]; X[c++] = tgt3[1]; X[c++] = tgt3[2]; X[c++] = roomsRem;
        for (let z = 0; z < Z; z++) X[c++] = counts[b * Z + z] / nn;
        for (let z = 0; z < Z; z++) X[c++] = o.zv[z];
        for (let z = 0; z < Z; z++) X[c++] = o.zn[z];
        for (let z = 0; z < Z; z++) X[c++] = prev[b * Z + z];
      }
      linearBatch(Wih0, bih0, X, B, IN0, 3 * G, gi);
      linearBatch(Whh0, bhh0, h0, B, G, 3 * G, gh);
      gruUpdate(gi, gh, h0, B, G);
      linearBatch(Wih1, bih1, h0, B, G, 3 * G, gi);
      linearBatch(Whh1, bhh1, h1, B, G, 3 * G, gh);
      gruUpdate(gi, gh, h1, B, G);
      linearBatch(W1, b1, h1, B, G, 128, hid);
      for (let i = 0; i < hid.length; i++) hid[i] = relu(hid[i]);
      linearBatch(W2, b2, hid, B, 128, Z, logits);

      for (let b = 0; b < B; b++) {
        const allowed = new Array(Z);
        let any = false;
        for (let z = 0; z < Z; z++) {
          let ok = counts[b * Z + z] < o.cap[z] && o.allowed[s * Z + z];
          if (s === n - 1 && n > 1 && counts[b * Z + z] + 1 === n) ok = false;   // never all rooms in one zone
          allowed[z] = ok; any = any || ok;
        }
        if (!any) {                                       // impossible caps: relax to capacity alone
          let anyCap = false;
          for (let z = 0; z < Z; z++) { allowed[z] = counts[b * Z + z] < o.cap[z]; anyCap = anyCap || allowed[z]; }
          if (!anyCap) allowed.fill(true);
        }
        for (let z = 0; z < Z; z++) if (!allowed[z]) logits[b * Z + z] = -1e9;

        let pick;
        if (o.forced) pick = o.forced[b][s];
        else if (o.greedy) {
          pick = 0;
          for (let z = 1; z < Z; z++) if (logits[b * Z + z] > logits[b * Z + pick]) pick = z;
        } else {
          let mx = -Infinity;
          for (let z = 0; z < Z; z++) mx = Math.max(mx, logits[b * Z + z] / T);
          const p = new Float64Array(Z);
          let sum = 0;
          for (let z = 0; z < Z; z++) { p[z] = Math.exp(logits[b * Z + z] / T - mx); sum += p[z]; }
          let u = o.rng.random() * sum;
          pick = Z - 1;
          for (let z = 0; z < Z; z++) { u -= p[z]; if (u < 0 && allowed[z]) { pick = z; break; } }
          if (!allowed[pick]) pick = allowed.lastIndexOf(true);
        }
        zones[b][s] = pick;
        counts[b * Z + pick] += 1;
        for (let z = 0; z < Z; z++) prev[b * Z + z] = z === pick ? 1 : 0;
      }
      if (rec) rec.push(Float64Array.from(logits));
    }
    return { zones, logits: rec };
  }
}

// ─────────────────────────────────────────────────────────────────────
//  Stage 3: EdgeARModel
// ─────────────────────────────────────────────────────────────────────
export class ConnNet {
  constructor(w) {
    this.w = w;
    this.vocab = w.meta.type_vocab;                         // { type: index }
    [this.nTypes, this.E] = w.shape("type_emb.weight");
    this.P = w.shape("prev_edge_emb.weight")[1];
    this.H = w.shape("gru.weight_hh_l0")[1];
    this.IN = 2 * this.E + this.P;
  }

  /**
   * Decode the lower-triangular edge bits for node types (indices into the vocab).
   * opts: { sample: bool, threshold: number, rng (random()), forcedBits?: number[] }
   * Returns { edges: [[i, j]], probs: number[] } in pair order (1,0), (2,0), (2,1), …
   */
  generate(typeIdx, o = {}) {
    const { t } = this.w, E = this.E, P = this.P, H = this.H, IN = this.IN;
    const emb = t("type_emb.weight"), pe = t("prev_edge_emb.weight");
    const Wih = t("gru.weight_ih_l0"), Whh = t("gru.weight_hh_l0"), bih = t("gru.bias_ih_l0"), bhh = t("gru.bias_hh_l0");
    const Wo = t("head.weight"), bo = t("head.bias");
    const n = typeIdx.length, h = new Float64Array(H), x = new Float64Array(IN);
    const gi = new Float64Array(3 * H), gh = new Float64Array(3 * H);
    const edges = [], probs = [];
    let prevBit = 0, step = 0;
    for (let i = 1; i < n; i++) {
      for (let j = 0; j < i; j++) {
        x.set(emb.subarray(typeIdx[i] * E, (typeIdx[i] + 1) * E), 0);
        x.set(emb.subarray(typeIdx[j] * E, (typeIdx[j] + 1) * E), E);
        x.set(pe.subarray(prevBit * P, (prevBit + 1) * P), 2 * E);
        linear(Wih, bih, x, IN, 3 * H, gi);
        linear(Whh, bhh, h, H, 3 * H, gh);
        gruUpdate(gi, gh, h, 1, H);
        let s = bo[0];
        for (let k = 0; k < H; k++) s += Wo[k] * h[k];
        const p = sigmoid(s);
        probs.push(p);
        let bit;
        if (o.forcedBits) bit = o.forcedBits[step];
        else if (o.sample ?? true) bit = o.rng.random() < p ? 1 : 0;
        else bit = p >= (o.threshold ?? 0.5) ? 1 : 0;
        if (bit) edges.push([i, j]);
        prevBit = bit; step++;
      }
    }
    return { edges, probs };
  }
}
