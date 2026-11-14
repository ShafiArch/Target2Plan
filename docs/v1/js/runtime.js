// runtime.js: talks to the model worker (or runs the models on the main
// thread when a module worker is not available, e.g. very old browsers).

const MODELS_URL = new URL("../models/", import.meta.url);

class Runtime {
  constructor() {
    this.ready = false;
    this.seq = 0;
    this.pending = new Map();
    this.local = null;          // main-thread fallback
    this.listeners = new Set();
    this.state = "loading";
  }

  onStatus(fn) { this.listeners.add(fn); fn(this.state, this.detail); }
  setStatus(state, detail = "") { this.state = state; this.detail = detail; for (const fn of this.listeners) fn(state, detail); }

  async init() {
    try {
      this.worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
      this.worker.onmessage = (e) => {
        const p = this.pending.get(e.data.id);
        if (!p) return;
        this.pending.delete(e.data.id);
        if (e.data.ok) p.resolve(e.data.result); else p.reject(new Error(e.data.error));
      };
      this.worker.onerror = (e) => { e.preventDefault?.(); this.fallback("worker error"); };
      const info = await this.post("init", { base: MODELS_URL.href });
      this.ready = true;
      this.setStatus("ready", info);
    } catch (err) {
      await this.fallback(err.message);
    }
  }

  async fallback(reason) {
    if (this.local) return;
    try {
      if (this.worker) this.worker.terminate();
      this.worker = null;
      for (const p of this.pending.values()) p.reject(new Error("restarting on the main thread"));
      this.pending.clear();
      const mod = await import("./worker.js");
      this.local = { nets: await mod.loadNets(MODELS_URL), run: mod.runTask };
      this.ready = true;
      this.setStatus("ready", { mainThread: true, reason });
    } catch (err) {
      this.setStatus("error", err.message);
      throw err;
    }
  }

  post(type, payload) {
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage({ id, type, payload });
    });
  }

  async whenReady() {
    if (this.ready) return;
    await new Promise((res, rej) => this.onStatus((s, d) => { if (s === "ready") res(); else if (s === "error") rej(new Error(d)); }));
  }

  async run(type, payload) {
    await this.whenReady();
    if (this.local) {
      await new Promise((r) => setTimeout(r, 0));     // let the UI paint the busy state first
      return this.local.run(this.local.nets, type, payload);
    }
    return this.post(type, payload);
  }
}

export const runtime = new Runtime();
