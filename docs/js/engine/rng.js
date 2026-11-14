// rng.js: seedable random numbers that reproduce the Python prototype.
//
// PyRandom    : CPython's random.Random (MT19937, seeded by init_by_array),
//               with random(), uniform(), shuffle(), choice(), randbelow().
// NumpyLegacy : numpy's legacy RandomState (MT19937 seeded by init_genrand),
//               with random_sample(), randn() (polar Box-Muller with the cached
//               second value) and randint() (masked rejection).
// Both produce the same streams as Python / numpy for the same seed, so a
// seeded run in the browser gives the same result as the Python prototype.

const N = 624, M = 397;
const MATRIX_A = 0x9908b0df, UPPER = 0x80000000, LOWER = 0x7fffffff;

class MT19937 {
  constructor() { this.mt = new Uint32Array(N); this.mti = N + 1; }

  initGenrand(s) {
    const mt = this.mt;
    mt[0] = s >>> 0;
    for (let i = 1; i < N; i++) {
      const p = mt[i - 1] ^ (mt[i - 1] >>> 30);
      mt[i] = (Math.imul(1812433253, p) + i) >>> 0;
    }
    this.mti = N;
  }

  initByArray(key) {
    const mt = this.mt;
    this.initGenrand(19650218);
    let i = 1, j = 0;
    for (let k = Math.max(N, key.length); k > 0; k--) {
      const p = mt[i - 1] ^ (mt[i - 1] >>> 30);
      mt[i] = ((mt[i] ^ Math.imul(p, 1664525)) + key[j] + j) >>> 0;
      i++; j++;
      if (i >= N) { mt[0] = mt[N - 1]; i = 1; }
      if (j >= key.length) j = 0;
    }
    for (let k = N - 1; k > 0; k--) {
      const p = mt[i - 1] ^ (mt[i - 1] >>> 30);
      mt[i] = ((mt[i] ^ Math.imul(p, 1566083941)) - i) >>> 0;
      i++;
      if (i >= N) { mt[0] = mt[N - 1]; i = 1; }
    }
    mt[0] = 0x80000000;
    this.mti = N;
  }

  uint32() {
    const mt = this.mt;
    if (this.mti >= N) {
      let kk = 0, y;
      for (; kk < N - M; kk++) {
        y = (mt[kk] & UPPER) | (mt[kk + 1] & LOWER);
        mt[kk] = mt[kk + M] ^ (y >>> 1) ^ (y & 1 ? MATRIX_A : 0);
      }
      for (; kk < N - 1; kk++) {
        y = (mt[kk] & UPPER) | (mt[kk + 1] & LOWER);
        mt[kk] = mt[kk + (M - N)] ^ (y >>> 1) ^ (y & 1 ? MATRIX_A : 0);
      }
      y = (mt[N - 1] & UPPER) | (mt[0] & LOWER);
      mt[N - 1] = mt[M - 1] ^ (y >>> 1) ^ (y & 1 ? MATRIX_A : 0);
      this.mti = 0;
    }
    let y = mt[this.mti++];
    y ^= y >>> 11;
    y ^= (y << 7) & 0x9d2c5680;
    y ^= (y << 15) & 0xefc60000;
    y ^= y >>> 18;
    return y >>> 0;
  }

  // 53-bit float in [0, 1), identical in CPython and numpy legacy
  double() {
    const a = this.uint32() >>> 5, b = this.uint32() >>> 6;
    return (a * 67108864 + b) / 9007199254740992;
  }
}

function bitLength(n) { let k = 0; while (n > 0) { k++; n = Math.floor(n / 2); } return k; }

export class PyRandom {
  constructor(seed = 0) { this.g = new MT19937(); this.seed(seed); }

  seed(s) {
    let n = Math.abs(Math.trunc(s));
    const key = [];
    do { key.push(n % 4294967296 >>> 0); n = Math.floor(n / 4294967296); } while (n > 0);
    this.g.initByArray(key);
  }

  random() { return this.g.double(); }
  uniform(a, b) { return a + (b - a) * this.random(); }
  getrandbits(k) { return k <= 0 ? 0 : this.g.uint32() >>> (32 - k); }   // k ≤ 32

  randbelow(n) {
    const k = bitLength(n);
    let r = this.getrandbits(k);
    while (r >= n) r = this.getrandbits(k);
    return r;
  }

  choice(seq) { return seq[this.randbelow(seq.length)]; }
  randint(a, b) { return a + this.randbelow(b - a + 1); }

  shuffle(arr) {
    for (let i = arr.length - 1; i > 0; i--) {
      const j = this.randbelow(i + 1);
      const t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    }
    return arr;
  }
}

export class NumpyLegacy {
  constructor(seed = 0) { this.g = new MT19937(); this.seed(seed); }

  seed(s) { this.g.initGenrand(s >>> 0); this.hasGauss = false; this.gaussVal = 0; }

  random_sample() { return this.g.double(); }

  gauss() {
    if (this.hasGauss) { this.hasGauss = false; return this.gaussVal; }
    let x1, x2, r2;
    do {
      x1 = 2 * this.g.double() - 1;
      x2 = 2 * this.g.double() - 1;
      r2 = x1 * x1 + x2 * x2;
    } while (r2 >= 1 || r2 === 0);
    const f = Math.sqrt(-2 * Math.log(r2) / r2);
    this.gaussVal = f * x1; this.hasGauss = true;
    return f * x2;
  }

  randn(k) { const out = new Float64Array(k); for (let i = 0; i < k; i++) out[i] = this.gauss(); return out; }

  // randint(low, high) with exclusive high, ranges < 2^32 (masked rejection)
  randint(low, high) {
    const rng = high - low - 1;
    if (rng <= 0) return low;
    let mask = rng;
    mask |= mask >>> 1; mask |= mask >>> 2; mask |= mask >>> 4; mask |= mask >>> 8; mask |= mask >>> 16;
    mask >>>= 0;
    let v;
    while ((v = (this.g.uint32() & mask) >>> 0) > rng);
    return low + v;
  }
}
