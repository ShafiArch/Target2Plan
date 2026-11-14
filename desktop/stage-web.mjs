// stage-web.mjs: copies the web app (../docs) into ./web for the desktop build,
// leaving out the archived version 1.0 in docs/v1, which only the website serves.
// Runs before every `tauri build` / `tauri dev` (see src-tauri/tauri.conf.json).
import { cpSync, rmSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "docs"), dst = join(here, "web");
rmSync(dst, { recursive: true, force: true });
cpSync(src, dst, {
  recursive: true,
  filter: (p) => { const r = relative(src, p); return r !== "v1" && !r.startsWith(`v1${sep}`); },
});
console.log(`stage-web: ${src} -> ${dst} (without v1/)`);
