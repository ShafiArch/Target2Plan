// detail.js: the double-click detail views of a zone plan and of a schematic.
import { h, palette, roomColor, fitCanvas, onResize, pad2, fmt, FONT, ZONE_LABELS } from "./common.js";
import { zoneCellBox, arrangeRoomsInCell } from "../engine/connectivity.js";
import { planBox } from "./stage2.js";
import { getSynthesis, paintFrame, roomAt } from "./schematicPaint.js";
import { showRoomPopup, closeRoomPopup } from "./roomPopup.js";

export function overlay(content, onClose) {
  const ov = h("div.overlay", { on: { click: (e) => { if (e.target === ov) close(); } } }, content);
  const key = (e) => { if (e.key === "Escape") close(); };
  function close() { ov.remove(); window.removeEventListener("keydown", key); onClose?.(); }
  window.addEventListener("keydown", key);
  document.body.append(ov);
  return close;
}

function barRow(name, value, target = null, band = null) {
  const track = h("div.track");
  if (band) track.append(h("div.band", { style: { left: `${band[0] * 100}%`, width: `${Math.max(0.5, (band[1] - band[0]) * 100)}%` }, title: `reachable ${band[0].toFixed(2)} to ${band[1].toFixed(2)}` }));
  track.append(h("div.fill", { style: { width: `${Math.max(0, Math.min(1, value)) * 100}%` } }));
  if (target != null) track.append(h("div.tick", { style: { left: `calc(${Math.max(0, Math.min(1, target)) * 100}% - 1px)` }, title: `target ${target.toFixed(2)}` }));
  return h("div.bar-row", h("span", name), track, h("span.v", value.toFixed(3)));
}
const kv = (k, v) => h("div.kv", h("span", k), h("span", v));

function selectButton(isSel, onToggle) {
  const b = h("button");
  const set = (on) => { b.className = on ? "btn-primary" : "btn-ghost"; b.textContent = on ? "✓ Selected (click to remove)" : "Add to selection"; };
  set(isSel);
  b.addEventListener("click", () => { const on = onToggle(); set(on); });
  return b;
}

export function openZoneDetail(t, store) {
  const c = t.computed, tg = t.targets || {}, rr = t.reachable || {}, cap = t.capacity || {};
  const cv = h("canvas");
  const sel = () => store.stage2.selected_idxs.includes(t.table_index);
  const toggle = () => { const s = new Set(store.stage2.selected_idxs); if (s.has(t.table_index)) s.delete(t.table_index); else s.add(t.table_index); store.setStage2Selection([...s]); return s.has(t.table_index); };
  const zc = {};
  for (const r of t.rooms) if (r.zone_assignment != null) zc[r.zone_assignment] = (zc[r.zone_assignment] || 0) + 1;
  const fullest = Math.max(0, ...Object.values(zc));
  const metrics = h("div.detail-sec", h("div.h", "PLAN METRICS"),
    barRow("QVCI", c.qvci, tg.present ? tg.qvci : null, rr.qvci), barRow("AQI", c.aqi, tg.present ? tg.aqi : null, rr.aqi),
    c.d_target != null ? kv("Distance to target", `${c.d_target.toFixed(3)}  (|ΔV| + |ΔA|)`) : null,
    rr.qvci ? kv("Reachable", `V ${rr.qvci[0].toFixed(2)} to ${rr.qvci[1].toFixed(2)} · A ${rr.aqi[0].toFixed(2)} to ${rr.aqi[1].toFixed(2)}`) : null,
    rr.aqi_collapsed ? h("div.hint", { style: { color: "var(--accent)" } }, "AQI cannot be changed on this site (every façade gives about the same indoor level).") : null,
    kv("Avg indoor level", `${fmt(c.avg_spl_dB, 1)} dB(A)`), kv("Zone spread", `${c.zone_spread} / 9`),
    kv("Fullest zone", `${fullest} room${fullest !== 1 ? "s" : ""}`),
    kv("Capacity", `corner ${cap.corner} · edge ${cap.cardinal} · interior ${cap.interior}`),
    kv("Capacity rule", c.valid ? "respected" : "violated"), kv("Decode", t.decode || "-"), kv("Model", "cGAT-AR"));
  const rows = t.rooms.map((r) => {
    const w = r.scored;
    return h("tr", h("td", r.room_id.replace(/_/g, " ")), h("td", r.zone_assignment == null ? "-" : ZONE_LABELS[r.zone_assignment]),
      h("td" + (r.quality_view_score == null ? ".dim" : w && r.quality_view_score >= 0.5 ? ".acc" : w ? "" : ".dim"), fmt(r.quality_view_score, 2)),
      h("td" + (r.acoustic_score == null ? ".dim" : w && r.acoustic_ok ? ".acc" : w ? "" : ".dim"), fmt(r.acoustic_score, 2)),
      h("td" + (r.est_avg_spl_dB == null ? ".dim" : ""), r.est_avg_spl_dB == null ? "-" : `${r.est_avg_spl_dB.toFixed(1)} / ${r.guideline_dB}`));
  });
  const rooms = h("div.detail-sec", h("div.h", `ROOMS (${t.rooms.length})`),
    h("table.detail-tbl", h("thead", h("tr", h("th", "Room"), h("th", "Zone"), h("th", "QV"), h("th", "AQ"), h("th", "dB(A)"))), h("tbody", ...rows)),
    h("div.hint", { style: { marginTop: "6px" } }, "QV = view score q_i, AQ = acoustic score a_i, dB(A) = modelled indoor level / guideline. Bathrooms carry no index weight; balconies are not zoned."));
  let close;
  const panel = h("div.detail",
    h("div.detail-head", h("span.h", "PLAN"), h("span.idx", `#${pad2(t.table_index)}`), h("span.sub", `· ${t.rooms.length} rooms`), h("span.grow"), selectButton(sel(), toggle), h("button.icon-btn", { title: "Close (Esc)", on: { click: () => close() } }, "✕")),
    h("div.detail-body", h("div.detail-canvas", cv), h("div.detail-side", metrics, rooms)));
  close = overlay(panel, () => ro.disconnect());
  const draw = () => {
    const { ctx, w, h: H } = fitCanvas(cv), P = palette();
    const [bx, by, bw, bh] = planBox(t, w - 40, H - 40);
    ctx.save(); ctx.translate(20 + bx, 20 + by);
    const zw = t.zone_weights || {}, zl = t.zone_levels_dB || {};
    for (let z = 0; z < 9; z++) {
      const [x, y, cw, ch] = zoneCellBox(z, bw, bh);
      ctx.strokeStyle = P.border; ctx.lineWidth = 1; ctx.strokeRect(x + 0.5, y + 0.5, cw - 1, ch - 1);
      ctx.font = `9px ${FONT}`; ctx.fillStyle = P.ink3; ctx.textAlign = "left"; ctx.textBaseline = "top";
      ctx.fillText(`Z${z + 1} ${ZONE_LABELS[z]}`, x + 5, y + 4);
      ctx.textAlign = "right"; ctx.fillText(`v ${Math.round(Number(zw[`zone_${z}`] || 0) * 100)} · ${Number(zl[`zone_${z}`] || 0).toFixed(0)} dB`, x + cw - 5, y + 4);
    }
    const byZone = new Map();
    for (const r of t.rooms) { if (r.zone_assignment == null) continue; if (!byZone.has(r.zone_assignment)) byZone.set(r.zone_assignment, []); byZone.get(r.zone_assignment).push(r); }
    for (const [z, rs] of byZone) {
      const [x, y, cw, ch] = zoneCellBox(z, bw, bh);
      arrangeRoomsInCell(rs, x, y, cw, ch).forEach((p, k) => {
        const rad = Math.max(8, Math.min(cw, ch) * 0.14);
        ctx.beginPath(); ctx.arc(p.cx, p.cy, rad, 0, 2 * Math.PI); ctx.fillStyle = roomColor(rs[k].room_type); ctx.fill();
        ctx.font = `500 9.5px ${FONT}`; ctx.fillStyle = P.ink; ctx.textAlign = "center"; ctx.textBaseline = "top";
        ctx.fillText(rs[k].room_id.replace(/_/g, " "), Math.round(p.cx), Math.round(p.cy + rad + 3));
      });
    }
    ctx.font = `600 10px ${FONT}`; ctx.fillStyle = P.accent; ctx.textAlign = "center";
    ctx.textBaseline = "bottom"; ctx.fillText("N", bw / 2, -4); ctx.textBaseline = "top"; ctx.fillText("S", bw / 2, bh + 4);
    ctx.textBaseline = "middle"; ctx.textAlign = "left"; ctx.fillText("E", bw + 5, bh / 2); ctx.textAlign = "right"; ctx.fillText("W", -5, bh / 2);
    ctx.restore();
  };
  const ro = onResize(cv.parentElement, draw);
}

export function openSchematicDetail(s, store, opt) {
  const c = s.computed || {}, tg = s.targets || {}, rr = s.reachable || {};
  const cv = h("canvas");
  const sel = () => store.stage4.selected_idxs.includes(s.plan_index);
  const toggle = () => { const st = new Set(store.stage4.selected_idxs); if (st.has(s.plan_index)) st.delete(s.plan_index); else st.add(s.plan_index); store.setStage4Selection([...st]); return st.has(s.plan_index); };
  const metrics = h("div.detail-sec", h("div.h", "PLAN METRICS"),
    barRow("QVCI", Number(c.qvci || 0), tg.present ? tg.qvci : null, rr.qvci), barRow("AQI", Number(c.aqi || 0), tg.present ? tg.aqi : null, rr.aqi),
    c.d_target != null ? kv("Distance to target", Number(c.d_target).toFixed(3)) : null,
    kv("Avg indoor level", `${fmt(c.avg_spl_dB, 1)} dB(A)`), kv("Zone spread", `${c.zone_spread ?? "-"} / 9`));
  const rows = (s.rooms || []).map((r) => h("tr.clickable", { title: "Room details", on: { click: (e) => { e.stopPropagation(); showRoomPopup(r, e.clientX, e.clientY, store.inputs); } } },
    h("td", r.room_id.replace(/_/g, " ")), h("td", fmt(r.area_m2, 1)),
    h("td", r.zone_assignment == null ? "-" : ZONE_LABELS[r.zone_assignment]), h("td", fmt(r.wwr, 2)),
    h("td" + (r.quality_view_score == null ? ".dim" : r.scored && r.quality_view_score >= 0.5 ? ".acc" : ""), fmt(r.quality_view_score, 2)),
    h("td" + (r.acoustic_score == null ? ".dim" : r.scored && r.acoustic_ok ? ".acc" : ""), fmt(r.acoustic_score, 2)),
    h("td" + (r.est_avg_spl_dB == null ? ".dim" : ""), fmt(r.est_avg_spl_dB, 1))));
  const rooms = h("div.detail-sec", h("div.h", `ROOMS (${(s.rooms || []).length})`), h("div.hint", "Click or tap a room or a row to see its details"),
    h("table.detail-tbl", h("thead", h("tr", h("th", "Room"), h("th", "m²"), h("th", "Zone"), h("th", "WWR"), h("th", "QV"), h("th", "AQ"), h("th", "dB(A)"))), h("tbody", ...rows)));
  const syn = getSynthesis(s);
  let frameIdx = -1, timer = null, T = null, shown = null;
  const draw = () => {
    const { ctx, w, h: H } = fitCanvas(cv);
    shown = frameIdx < 0 ? syn.frames[syn.frames.length - 1] : syn.frames[frameIdx];
    T = paintFrame(ctx, s, syn, shown, w, H, palette(), { showEdges: opt.edges, showWalls: opt.walls, furniture: opt.furn, showLabels: true, showCaption: frameIdx >= 0, fontScale: Math.max(1, w / 440) });
  };
  cv.addEventListener("click", (e) => {
    const b = cv.getBoundingClientRect(), room = roomAt(shown, T, e.clientX - b.left, e.clientY - b.top);
    if (room) showRoomPopup(room, e.clientX, e.clientY, store.inputs); else closeRoomPopup();
  });
  cv.addEventListener("mousemove", (e) => {
    const b = cv.getBoundingClientRect();
    cv.style.cursor = roomAt(shown, T, e.clientX - b.left, e.clientY - b.top) ? "pointer" : "";
  });
  const replay = () => {
    clearInterval(timer);
    frameIdx = 0; draw();
    timer = setInterval(() => { frameIdx += 1; if (frameIdx >= syn.frames.length - 1) { frameIdx = -1; clearInterval(timer); } draw(); }, Math.max(15, opt.anim || 25));
  };
  let close;
  const panel = h("div.detail",
    h("div.detail-head", h("span.h", "PLAN"), h("span.idx", `#${pad2(s.plan_index)}`), h("span.sub", `· ${(s.rooms || []).length} rooms · ${(s.edges || []).length} edges${syn.removed.length ? ` · removed ${syn.removed.join(", ")}` : ""}`), h("span.grow"),
      h("button.link-btn", { on: { click: replay } }, "↻ Replay synthesis"), selectButton(sel(), toggle), h("button.icon-btn", { title: "Close (Esc)", on: { click: () => close() } }, "✕")),
    h("div.detail-body", h("div.detail-canvas", cv), h("div.detail-side", metrics, rooms)));
  close = overlay(panel, () => { clearInterval(timer); ro.disconnect(); closeRoomPopup(); });
  const ro = onResize(cv.parentElement, draw);
}
