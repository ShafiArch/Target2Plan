#!/usr/bin/env python3
"""Render the README gallery with the web app's own engine in headless Chromium.

    python3 tools/gallery/make_gallery.py [--seeds 1-8] [--conn-seeds 42]
        [--pick PROGRAM=SEED:CONN_SEED:PLAN ...] [--dry-run] [--sheet DIR]
        [--out media/gallery] [--fonts DIR]

For every room program in gallery.mjs the app's pipeline runs once per zone
model seed and connectivity model seed, with the default site, targets
(QVCI 0.70, AQI 0.60) and parameters; the app itself samples the
connectivity model with seed 42. Plans that fail the practicality check in
gallery.mjs are left out. Of the rest, those within 0.01 of the smallest
distance to the targets are kept and the most compact of them is drawn:
<program>_plan.gif (the schematic synthesis), <program>_graph.gif (the
connectivity graph), plans.gif (the chosen plans side by side at one scale)
and gallery.json (what was drawn).

--pick draws a given candidate instead (only its own seeds are run),
--dry-run only lists the candidates and --sheet writes a contact sheet of
the practical ones per program. --fonts is a folder with the Inter and
JetBrains Mono woff2 files (inter-latin-400-normal.woff2 and so on) for
machines that cannot reach Google Fonts.

Needs numpy, Pillow and Playwright for Python with Chromium.
"""
import argparse
import base64
import functools
import http.server
import io
import json
import math
import threading
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


class Quiet(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript", ".js": "text/javascript"}

    def log_message(self, *args):
        pass


def serve(root):
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def route_fonts(page, folder):
    files = {p.name: p for p in Path(folder).glob("*.woff2")}
    faces = []
    for family, stem in (("Inter", "inter"), ("JetBrains Mono", "jetbrains-mono")):
        for weight in (400, 500, 600, 700):
            name = f"{stem}-latin-{weight}-normal.woff2"
            if name in files:
                faces.append(f"@font-face {{ font-family: '{family}'; font-style: normal; font-weight: {weight}; "
                             f"src: url(https://fonts.gstatic.com/local/{name}) format('woff2'); }}")
    cors = {"Access-Control-Allow-Origin": "*"}
    page.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(status=200, content_type="text/css", headers=cors, body="\n".join(faces)))
    page.route("https://fonts.gstatic.com/local/**", lambda r: r.fulfill(
        status=200, content_type="font/woff2", headers=cors, body=files[r.request.url.rsplit("/", 1)[1]].read_bytes()))


def nearest(im, pal):
    """Palette index of every pixel of im, the closest palette colour exactly
    (Pillow's own mapping goes through a reduced colour cache and would turn
    white into off-white)."""
    a = np.asarray(im, dtype=np.int64)
    key = (a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]
    uniq, inv = np.unique(key.ravel(), return_inverse=True)
    cols = np.stack([uniq >> 16, (uniq >> 8) & 255, uniq & 255], axis=1)
    idx = ((cols[:, None, :] - pal[None, :, :]) ** 2).sum(axis=2).argmin(axis=1).astype(np.uint8)
    return idx[inv.ravel()].reshape(a.shape[:2])


def write_gif(frames, path, diff=False):
    """One palette for the whole animation, no dithering. The palette keeps the
    most used colours exactly (background, room fills, ink) and fills the rest
    with a median cut of frames taken across the animation. Pillow stores only
    the changed box of each frame and merges repeated frames. With diff, pixels
    that did not change since the previous frame are written as transparent
    (index 255), which keeps large animations small."""
    imgs = [Image.open(io.BytesIO(base64.b64decode(f["png"].split(",", 1)[1]))).convert("RGB") for f in frames]
    n, (w, h) = len(imgs), imgs[0].size
    picks = sorted({0, n // 6, n // 3, n // 2, 2 * n // 3, 5 * n // 6, n - 1})
    sheet = Image.new("RGB", (w, h * len(picks)))
    for i, k in enumerate(picks):
        sheet.paste(imgs[k], (0, h * i))
    size = 255 if diff else 256
    exact = [c for _, c in sorted(sheet.getcolors(w * h * len(picks)), reverse=True)[:96]]
    cut = sheet.quantize(colors=size - len(exact), method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).getpalette()
    pal = np.array([v for c in exact for v in c] + cut[: 3 * (size - len(exact))], dtype=np.int64).reshape(-1, 3)
    idx = [nearest(im, pal) for im in imgs]
    if diff:
        enc = [idx[0]] + [np.where(idx[i] == idx[i - 1], 255, idx[i]).astype(np.uint8) for i in range(1, n)]
        pal = np.vstack([pal, [[255, 0, 255]]])
    else:
        enc = idx
    flat = pal.astype(np.uint8).ravel().tolist()
    q = []
    for a in enc:
        im = Image.frombytes("P", (w, h), a.tobytes())
        im.putpalette(flat)
        q.append(im)
    extra = {"transparency": 255} if diff else {}
    q[0].save(path, save_all=True, append_images=q[1:], duration=[int(f["delay"]) for f in frames], loop=0, optimize=False, disposal=1, **extra)
    return path.stat().st_size


def contact_sheet(page, rows, path, cell=280, cols=6):
    from PIL import ImageDraw
    sheet = Image.new("RGB", (cell * cols, (cell + 18) * ((len(rows) + cols - 1) // cols)), "white")
    draw = ImageDraw.Draw(sheet)
    for i, r in enumerate(rows):
        png = page.evaluate("([id, s]) => G.planStill(id, s)", [r["id"], cell])
        im = Image.open(io.BytesIO(base64.b64decode(png.split(",", 1)[1]))).convert("RGB")
        x, y = (i % cols) * cell, (i // cols) * (cell + 18)
        sheet.paste(im, (x, y))
        draw.text((x + 6, y + cell + 2), f"{r['seed']}:{r['conn_seed']}:{r['plan_index']}  Q {r['qvci']:.2f} A {r['aqi']:.2f} fill {r['fill']:.2f}", fill=(60, 60, 60))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def layout_dist(a, b):
    """How far apart two plans of one program are: room centres (0..1 of the
    plan extent) plus the difference in proportion."""
    la = {rid: (u, v) for rid, u, v in a["layout"]}
    d = sum(abs(la[rid][0] - u) + abs(la[rid][1] - v) for rid, u, v in b["layout"] if rid in la)
    return d + abs(math.log(a["aspect"] / b["aspect"]))


def graph_dist(a, b):
    """How far apart two connectivity graphs are: node positions plus edges not shared."""
    def parse(sig):
        nodes, _, edges = sig.partition("|")
        return {t.rsplit(":", 2)[0]: tuple(int(v) for v in t.rsplit(":", 2)[1:]) for t in nodes.split()}, set(edges.split())
    (pa, ea), (pb, eb) = parse(a["graph_sig"]), parse(b["graph_sig"])
    return sum(abs(pa[k][0] - pb[k][0]) + abs(pa[k][1] - pb[k][1]) for k in pa if k in pb) / 200 + 0.5 * len(ea ^ eb)


def spread(pool, k, dist, first):
    """k members of pool, starting with first, each next one the farthest from those chosen."""
    chosen, rest = [first], [r for r in pool if r is not first]
    while len(chosen) < k and rest:
        best = max(rest, key=lambda r: min(dist(r, c) for c in chosen))
        chosen.append(best)
        rest.remove(best)
    return chosen


# target pairs for the mosaic, the default first: different targets give different arrangements
GRID_TARGETS = [[0.7, 0.6], [0.6, 0.7], [0.75, 0.5], [0.5, 0.65], [0.65, 0.55], [0.55, 0.75], [0.8, 0.6], [0.6, 0.5]]


def make_grid(page, names, seeds, conn_seeds, picks, path, per_row=8, graphs_per=2):
    """The mosaic: per program a row of plans as different from each other as
    possible, then a row of connectivity graphs, written as one GIF."""
    plan_rows, graph_ids, spare = [], [], []
    for name in names:
        pick = picks.get(name)
        cs = ([pick[1]] if pick else []) + [c for c in conn_seeds if not pick or c != pick[1]]
        rows = page.evaluate("([n, s, c, t]) => G.search(n, { seeds: s, connSeeds: c, targets: t, practicalOnly: true })", [name, seeds, cs, GRID_TARGETS])
        by_sig = {}
        for r in sorted(rows, key=lambda r: (r["d_target"], -r["fill"])):
            by_sig.setdefault(r["sig"], r)
        pool = list(by_sig.values())
        for floor in (0.6, 0.5, 0.0):
            compact = [r for r in pool if r["fill"] >= floor]
            if len(compact) >= per_row:
                break
        is_pick = lambda r: pick and r["target"] == 0 and (r["seed"], r["conn_seed"], r["plan_index"]) == pick
        first = next((r for r in compact if is_pick(r)), None) or min(compact, key=lambda r: r["d_target"])
        chosen = spread(compact, per_row, layout_dist, first)
        plan_rows.append([r["id"] for r in chosen])
        gpool = {}
        for r in sorted((r for r in rows if r["clear"]), key=lambda r: r["d_target"]):
            gpool.setdefault(r["graph_sig"], r)
        gl = list(gpool.values())
        gfirst = next((r for r in gl if is_pick(r)), None) or (gl[0] if gl else None)
        if gfirst:
            got = spread(gl, graphs_per + 2, graph_dist, gfirst)
            graph_ids += [r["id"] for r in got[:graphs_per]]
            spare += [r["id"] for r in got[graphs_per:]]
        print(f"{name}: {len(rows)} practical plans, {len(pool)} distinct layouts, {len(compact)} compact; {len(gl)} distinct clear graphs")
        for r in chosen:
            print(f"  t{r['target']} {r['seed']}:{r['conn_seed']}:{r['plan_index']}  Q {r['qvci']:.2f} A {r['aqi']:.2f} fill {r['fill']:.2f}")
    graph_ids = (graph_ids + spare)[: per_row]   # a program short of clear graphs leaves its place to another
    frames = page.evaluate("([rows, graphs]) => G.gridFrames(rows, graphs)", [plan_rows, graph_ids])
    print(f"wrote {path.name} ({write_gif(frames, path, diff=True) // 1024} KB, {len(frames)} frames)")


def seed_list(spec):
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "media" / "gallery"))
    ap.add_argument("--seeds", default="1-8", help="zone model seeds, e.g. 1-8 or 1,4,9")
    ap.add_argument("--conn-seeds", default="42", help="connectivity model seeds")
    ap.add_argument("--programs", default="", help="comma separated subset of the programs")
    ap.add_argument("--pick", action="append", default=[], help="program=seed:conn_seed:plan")
    ap.add_argument("--sheet", help="folder for contact sheets of the practical plans")
    ap.add_argument("--dpr", type=float, default=2.4, help="device pixels per CSS pixel (the cards are 300 CSS px wide)")
    ap.add_argument("--strip-dpr", type=float, default=1.6, help="the same for the strip of all plans")
    ap.add_argument("--fonts")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--grid", action="store_true", help="write the mosaic plans_grid.gif instead (searches --seeds x --conn-seeds)")
    args = ap.parse_args()
    seeds, conn_seeds = seed_list(args.seeds), seed_list(args.conn_seeds)
    picks = {k: tuple(int(x) for x in v.split(":")) for k, v in (p.split("=") for p in args.pick)}
    out = Path(args.out)
    httpd = serve(ROOT)
    summary = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 900}, device_scale_factor=1)
        page.on("pageerror", lambda e: print("page error:", e))
        if args.fonts:
            route_fonts(page, args.fonts)
        page.goto(f"http://127.0.0.1:{httpd.server_address[1]}/tools/gallery/index.html")
        page.wait_for_function("window.galleryInfo !== undefined", timeout=180000)
        names = page.evaluate("Object.keys(G.PROGRAMS)")
        if args.programs:
            names = [n for n in names if n in args.programs.split(",")]
        if args.grid:
            out.mkdir(parents=True, exist_ok=True)
            make_grid(page, names, seeds, conn_seeds, picks, out / "plans_grid.gif")
            names = []
        for name in names:
            # a picked plan only needs its own seeds
            s_, c_ = ([picks[name][0]], [picks[name][1]]) if name in picks else (seeds, conn_seeds)
            rows = page.evaluate("([n, s, c]) => G.search(n, { seeds: s, connSeeds: c })", [name, s_, c_])
            ok, seen = [], set()
            for r in rows:   # the same plan with the same edges from another connectivity seed counts once
                key = (r["seed"], r["plan_index"], tuple(map(tuple, r["edges"])))
                if r["ok"] and key not in seen:
                    seen.add(key)
                    ok.append(r)
            reasons = Counter(w.split(":")[0] if w.startswith("graph:") else " ".join(w.split(" ")[:2]) for r in rows for w in r["why"][:1])
            good_graphs = sorted({r["conn_seed"] for r in rows if not any(w.startswith("graph:") for w in r["why"])})
            print(f"\n{name}: {len(ok)} practical of {len(rows)} plans ({rows[0]['area_m2']} m2, plan {rows[0]['plan'][0]} x {rows[0]['plan'][1]} m)")
            print(f"  conventional graphs from connectivity seeds {good_graphs}")
            print("  first reason for rejecting:", dict(reasons.most_common(6)))
            # closest to the targets, then the most compact of those within 0.01
            ranked = sorted(ok, key=lambda r: (round(r["d_target"] / 0.01), -r["fill"], r["d_target"]))
            for r in ranked[:8]:
                print(f"  {r['seed']}:{r['conn_seed']}:{r['plan_index']}  Q {r['qvci']:.3f} A {r['aqi']:.3f} d {r['d_target']:.3f} fill {r['fill']:.2f}")
            if args.sheet and ok:
                contact_sheet(page, ranked[:24], Path(args.sheet) / f"{name}.png")
            if name in picks:
                seed, cs, idx = picks[name]
                best = next((r for r in rows if (r["seed"], r["conn_seed"], r["plan_index"]) == (seed, cs, idx)), None)
                if not best or not best["ok"]:
                    raise SystemExit(f"{name}: seed {seed}, connectivity seed {cs}, plan {idx} is not a practical candidate")
            elif ok:
                dmin = min(r["d_target"] for r in ok)
                best = max((r for r in ok if r["d_target"] <= dmin + 0.01), key=lambda r: (r["fill"], -r["d_target"]))
            else:
                print("  nothing practical; try more seeds")
                continue
            print(f"  drawing seed {best['seed']}, connectivity seed {best['conn_seed']}, plan {best['plan_index']}")
            entry = {k: best[k] for k in ("program", "label", "seed", "conn_seed", "plan_index", "qvci", "aqi", "target_qvci", "target_aqi", "rooms", "area_m2", "plan")}
            entry["edges"] = [f"{x}-{y}" for x, y in best["edges"]]
            if not args.dry_run:
                out.mkdir(parents=True, exist_ok=True)
                fr = page.evaluate("([id, d]) => G.planFrames(id, { dpr: d })", [best["id"], args.dpr])
                entry["plan_gif"] = f"{name}_plan.gif"
                kb = write_gif(fr, out / entry["plan_gif"]) // 1024
                fr = page.evaluate("([id, d]) => G.graphFrames(id, { dpr: d })", [best["id"], args.dpr])
                entry["graph_gif"] = f"{name}_graph.gif"
                kb2 = write_gif(fr, out / entry["graph_gif"]) // 1024
                print(f"  wrote {entry['plan_gif']} ({kb} KB), {entry['graph_gif']} ({kb2} KB)")
            entry["id"] = best["id"]
            summary.append(entry)
        if not args.dry_run and len(summary) > 1:
            fr = page.evaluate("([ids, d]) => G.stripFrames(ids, { dpr: d })", [[e["id"] for e in summary], args.strip_dpr])
            print(f"\nwrote plans.gif ({write_gif(fr, out / 'plans.gif') // 1024} KB)")
        for e in summary:
            del e["id"]
        browser.close()
    httpd.shutdown()
    if not args.dry_run and summary:
        (out / "gallery.json").write_text(json.dumps({"plans": summary}, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
