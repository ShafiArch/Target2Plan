// exporters.js: ZIP (stored) archives, and the synthesis animation as GIF and MP4.

const CRC = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
  return t;
})();
const crc32 = (u8) => { let c = 0xffffffff; for (let i = 0; i < u8.length; i++) c = CRC[(c ^ u8[i]) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };

/** files: [{name, blob}] → a stored (uncompressed) ZIP blob. */
export async function zipBlob(files) {
  const enc = new TextEncoder(), parts = [], central = [];
  let offset = 0;
  const now = new Date();
  const dosTime = (now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1);
  const dosDate = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
  for (const f of files) {
    const data = new Uint8Array(await f.blob.arrayBuffer()), name = enc.encode(f.name), crc = crc32(data);
    const lh = new DataView(new ArrayBuffer(30));
    lh.setUint32(0, 0x04034b50, true); lh.setUint16(4, 20, true); lh.setUint16(6, 0x0800, true); lh.setUint16(8, 0, true);
    lh.setUint16(10, dosTime, true); lh.setUint16(12, dosDate, true); lh.setUint32(14, crc, true);
    lh.setUint32(18, data.length, true); lh.setUint32(22, data.length, true); lh.setUint16(26, name.length, true); lh.setUint16(28, 0, true);
    parts.push(lh.buffer, name, data);
    const ch = new DataView(new ArrayBuffer(46));
    ch.setUint32(0, 0x02014b50, true); ch.setUint16(4, 20, true); ch.setUint16(6, 20, true); ch.setUint16(8, 0x0800, true); ch.setUint16(10, 0, true);
    ch.setUint16(12, dosTime, true); ch.setUint16(14, dosDate, true); ch.setUint32(16, crc, true);
    ch.setUint32(20, data.length, true); ch.setUint32(24, data.length, true); ch.setUint16(28, name.length, true);
    ch.setUint32(42, offset, true);
    central.push(ch.buffer, name);
    offset += 30 + name.length + data.length;
  }
  const cdSize = central.reduce((s, p) => s + (p.byteLength ?? p.length), 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
  end.setUint32(12, cdSize, true); end.setUint32(16, offset, true);
  return new Blob([...parts, ...central, end.buffer], { type: "application/zip" });
}

/**
 * Encode frames as a GIF. drawFrame(ctx, i) paints frame i into a size x size
 * canvas; delays[i] in ms. One palette from the finished plan keeps colours stable.
 */
export async function encodeGIF(nFrames, size, drawFrame, delays, onProgress) {
  const { GIFEncoder, quantize, applyPalette } = await import("../vendor/gifenc.esm.js");
  const cv = document.createElement("canvas");
  cv.width = size; cv.height = size;
  const ctx = cv.getContext("2d", { willReadFrequently: true });
  drawFrame(ctx, nFrames - 1);
  const palette = quantize(ctx.getImageData(0, 0, size, size).data, 256);
  const gif = GIFEncoder();
  for (let i = 0; i < nFrames; i++) {
    drawFrame(ctx, i);
    const data = ctx.getImageData(0, 0, size, size).data;
    // Browsers slow GIF delays under 2 cs down to about 100 ms, so 20 ms is the floor.
    gif.writeFrame(applyPalette(data, palette), size, size, { palette: i === 0 ? palette : undefined, delay: Math.max(20, delays[i]) });
    if (i % 8 === 0) { onProgress?.(i / nFrames); await new Promise((r) => setTimeout(r, 0)); }
  }
  gif.finish();
  return new Blob([gif.bytes()], { type: "image/gif" });
}

/** Encode frames as H.264 MP4 through WebCodecs; returns null when the browser cannot. */
export async function encodeMP4(nFrames, size, drawFrame, delays, onProgress) {
  if (typeof VideoEncoder === "undefined") return null;
  const configs = ["avc1.42001f", "avc1.4d0028", "avc1.640028"].map((codec) => ({ codec, width: size, height: size, bitrate: 4_000_000, framerate: 25 }));
  let config = null;
  for (const c of configs) { try { if ((await VideoEncoder.isConfigSupported(c)).supported) { config = c; break; } } catch { /* try next */ } }
  if (!config) return null;
  const { Muxer, ArrayBufferTarget } = await import("../vendor/mp4-muxer.mjs");
  const muxer = new Muxer({ target: new ArrayBufferTarget(), video: { codec: "avc", width: size, height: size }, fastStart: "in-memory" });
  let failure = null;
  const enc = new VideoEncoder({ output: (chunk, meta) => muxer.addVideoChunk(chunk, meta), error: (e) => { failure = e; } });
  enc.configure(config);
  const cv = document.createElement("canvas");
  cv.width = size; cv.height = size;
  const ctx = cv.getContext("2d");
  // The muxer gives the last sample the same duration as the one before it,
  // so a long hold (the 2 s on the final frame) is written as repeats of
  // about 100 ms each; the file then runs for the full sum of the delays.
  let t = 0, count = 0;
  for (let i = 0; i < nFrames; i++) {
    drawFrame(ctx, i);
    const parts = Math.max(1, Math.round(delays[i] / 100)), step = (delays[i] * 1000) / parts;
    for (let k = 0; k < parts; k++) {
      const vf = new VideoFrame(cv, { timestamp: Math.round(t), duration: Math.round(step) });
      enc.encode(vf, { keyFrame: count % 50 === 0 });
      vf.close();
      t += step; count++;
      if (enc.encodeQueueSize > 8) await new Promise((r) => setTimeout(r, 0));
    }
    if (i % 8 === 0) { onProgress?.(i / nFrames); await new Promise((r) => setTimeout(r, 0)); }
  }
  await enc.flush();
  if (failure) throw failure;
  muxer.finalize();
  return new Blob([muxer.target.buffer], { type: "video/mp4" });
}
