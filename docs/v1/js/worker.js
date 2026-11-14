// worker.js: runs the three trained networks off the main thread.
// Messages: {id, type: "init" | "area" | "zones" | "conn", payload} → {id, ok, result | error}
import { loadWeights, AreaNet, ZoneNet, ConnNet } from "./engine/nn.js";
import { generateAreaVariations } from "./engine/area.js";
import { generateZoneTables } from "./engine/zones.js";
import { generateConnectivityGraphs } from "./engine/connectivity.js";

let nets = null;

export async function loadNets(base) {
  const get = async (name) => {
    const [j, b] = await Promise.all([
      fetch(new URL(`${name}.json`, base)).then((r) => { if (!r.ok) throw new Error(`${name}.json: HTTP ${r.status}`); return r.json(); }),
      fetch(new URL(`${name}.bin`, base)).then((r) => { if (!r.ok) throw new Error(`${name}.bin: HTTP ${r.status}`); return r.arrayBuffer(); }),
    ]);
    return loadWeights(j, b);
  };
  const [a, z, c] = await Promise.all([get("area"), get("zone"), get("conn")]);
  return { area: new AreaNet(a), zone: new ZoneNet(z), conn: new ConnNet(c), meta: { zone: z.meta, area: a.meta, conn: c.meta } };
}

export function runTask(n, type, p) {
  if (type === "area") return generateAreaVariations(n.area, p.counts, p.totalArea, p.opts);
  if (type === "zones") return generateZoneTables(n.zone, p.inputs, p.variation, p.opts);
  if (type === "conn") return generateConnectivityGraphs(n.conn, p.tables, p.selected, p.opts);
  throw new Error(`unknown task ${type}`);
}

if (typeof self !== "undefined" && typeof window === "undefined") {
  self.onmessage = async (e) => {
    const { id, type, payload } = e.data;
    try {
      if (type === "init") {
        nets = await loadNets(new URL(payload.base, self.location.href));
        self.postMessage({ id, ok: true, result: { trained: nets.meta.zone.trained || null } });
        return;
      }
      if (!nets) throw new Error("models not loaded");
      const t0 = performance.now();
      const result = runTask(nets, type, payload);
      self.postMessage({ id, ok: true, result, ms: performance.now() - t0 });
    } catch (err) {
      self.postMessage({ id, ok: false, error: String((err && err.message) || err) });
    }
  };
}
