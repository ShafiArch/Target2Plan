// pymath.js: two Python math behaviours the prototype's results depend on.
//
// pyRound(x, d): Python's round(), i.e. correctly rounded on the exact binary
//   value with ties to even (JavaScript's toFixed rounds ties up).
// pyHypot(x, y): CPython's math.hypot (vector_norm: lossless scaling and
//   squaring, one differential correction), bit-for-bit. Engines' Math.hypot
//   can differ in the last bit, which flips exact ties such as a door hung at
//   whichever wall end is nearer to a point in the middle of the wall.

const dv = new DataView(new ArrayBuffer(8));

export function pyRound(x, d = 0) {
  if (!Number.isFinite(x) || x === 0) return x;
  dv.setFloat64(0, x);
  const hi = dv.getUint32(0), lo = dv.getUint32(4);
  const neg = hi >>> 31 === 1, expBits = (hi >>> 20) & 0x7ff;
  let mant = (BigInt(hi & 0xfffff) << 32n) | BigInt(lo);
  let e;
  if (expBits === 0) e = -1074; else { mant |= 1n << 52n; e = expBits - 1075; }
  let num = mant, den = 1n;
  if (d >= 0) num *= 10n ** BigInt(d); else den *= 10n ** BigInt(-d);
  if (e >= 0) num <<= BigInt(e); else den <<= BigInt(-e);
  let q = num / den;
  const r2 = 2n * (num - q * den);
  if (r2 > den || (r2 === den && (q & 1n) === 1n)) q += 1n;
  const s = `${neg ? "-" : ""}${q}`;
  return Number(d >= 0 ? `${s}e-${d}` : `${s}e${-d}`);
}

function dlMul(x, y) {                       // exact product: hi + lo == x * y
  const hi = x * y, c = 134217729;
  let t = c * x;
  const xh = t - (t - x), xl = x - xh;
  t = c * y;
  const yh = t - (t - y), yl = y - yh;
  return [hi, ((xh * yh - hi) + xh * yl + xl * yh) + xl * yl];
}

function frexpExp(v) {                       // e such that v = m * 2^e, 0.5 <= m < 1
  dv.setFloat64(0, v);
  const bits = (dv.getUint32(0) >>> 20) & 0x7ff;
  if (bits === 0) return frexpExp(v * 2 ** 64) - 64;
  return bits - 1022;
}

export function pyHypot(a, b) {
  a = Math.abs(a); b = Math.abs(b);
  if (a === Infinity || b === Infinity) return Infinity;
  if (Number.isNaN(a) || Number.isNaN(b)) return NaN;
  const max = Math.max(a, b);
  if (max === 0) return 0;
  const scale = 2 ** -frexpExp(max);
  let csum = 1.0, frac1 = 0.0, frac2 = 0.0;
  for (const v of [a, b]) {
    const x = v * scale;
    const [ph, pl] = dlMul(x, x);
    const sh = csum + ph, sl = ph - (sh - csum);
    csum = sh; frac1 += pl; frac2 += sl;
  }
  let h = Math.sqrt(csum - 1.0 + (frac1 + frac2));
  const [ph, pl] = dlMul(-h, h);
  const sh = csum + ph, sl = ph - (sh - csum);
  csum = sh; frac1 += pl; frac2 += sl;
  h += (csum - 1.0 + (frac1 + frac2)) / (2.0 * h);
  return h / scale;
}

// Python's sum() over floats (Neumaier-compensated since Python 3.12).
export function pySum(values) {
  let s = 0, c = 0, started = false;
  for (const x of values) {
    if (!started) { s = x; started = true; continue; }
    const t = s + x;
    if (Math.abs(s) >= Math.abs(x)) c += (s - t) + x; else c += (x - t) + s;
    s = t;
  }
  if (c && Number.isFinite(c)) s += c;
  return s;
}

// numpy's ndarray.sum() over a float64 vector: 0 + pairwise sum (8 lanes).
function pairwise(a, lo, n) {
  if (n < 8) { let res = 0; for (let i = 0; i < n; i++) res += a[lo + i]; return res; }
  if (n <= 128) {
    const r = [a[lo], a[lo + 1], a[lo + 2], a[lo + 3], a[lo + 4], a[lo + 5], a[lo + 6], a[lo + 7]];
    let i = 8;
    for (; i < n - (n % 8); i += 8) for (let j = 0; j < 8; j++) r[j] += a[lo + i + j];
    let res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
    for (; i < n; i++) res += a[lo + i];
    return res;
  }
  let n2 = Math.floor(n / 2);
  n2 -= n2 % 8;
  return pairwise(a, lo, n2) + pairwise(a, lo + n2, n - n2);
}
export const npSum = (values) => 0 + pairwise(Array.from(values), 0, values.length);
