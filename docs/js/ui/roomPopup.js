// roomPopup.js: the room card that opens when a room is clicked in a
// schematic (stages/stage4_schematic._RoomTooltip in the prototype):
// zone, area, aspect ratio, planned WWR, then how the view and acoustic
// scores come about and whether the room meets them.
import { h, ZONE_LABELS } from "./common.js";
import { siteFromInputs } from "../engine/zones.js";
import { ZONE_SIDES, VIEW_WEIGHTS, WWR_REF } from "../engine/scoring.js";
import { pyRound } from "../engine/pymath.js";

const f = (x, d) => pyRound(Number(x), d).toFixed(d);      // Python's format(x, ".df")
let current = null;

export function closeRoomPopup() {
  if (!current) return;
  current.cleanup();
  current.el.remove();
  current = null;
}

/** Open the card for `room` next to the viewport point (x, y). */
export function showRoomPopup(room, x, y, inputs) {
  closeRoomPopup();
  if (!room) return;
  const row = (k, v) => h("div.rp-row", h("span.k", k), h("span.v", v));
  const achievement = (label, ok, okText, failText, counted) => h("div.rp-row", h("span.k", label),
    !counted ? h("span.rp-chip.na", "not counted · weight 0") : h(`span.rp-chip.${ok ? "ok" : "fail"}`, ok ? `✓ ${okText}` : `✗ ${failText}`));

  const z = room.zone_assignment == null ? null : Number(room.zone_assignment);
  const wwr = Number(room.wwr || 0), weighted = !!room.scored;
  const props = [
    row("Zone", z == null ? "not zoned" : `${ZONE_LABELS[z]} (#${z + 1})`),
    row("Area", room.area_m2 != null ? `${f(room.area_m2, 1)} m²` : f(room.area || 0, 1)),
    row("Aspect ratio", f(room.aspect_ratio || 1, 2)),
    row("Planned WWR", `${f(wwr * 100, 0)}%`),
  ];

  const scores = [];
  const site = siteFromInputs(inputs?.site_conditions);
  const facades = z == null ? [] : ZONE_SIDES[z + 1] || [];
  const vw = (d) => VIEW_WEIGHTS[site.views[d]] ?? 0;
  const qv = room.quality_view_score, aq = room.acoustic_score;
  if (qv != null) {
    if (z != null) {
      if (facades.length) {
        const best = facades.reduce((b, d) => (vw(d) > vw(b) ? d : b), facades[0]);
        scores.push(row("Zone view weight v(z)", `${f(vw(best), 2)}  (${best}: ${site.views[best]})`));
        const others = facades.filter((d) => d !== best).map((d) => `${d}: ${site.views[d]}`);
        if (others.length) scores.push(row("Other façade", others.join(", ")));
      } else scores.push(row("Zone view weight v(z)", "0.00  (interior)"));
      const credit = Math.min(1, wwr / WWR_REF);
      scores.push(row("WWR credit", `${f(credit, 2)}  (${f(wwr * 100, 0)}% of ${f(WWR_REF * 100, 0)}%)`));
    }
    scores.push(row("View score q_i", f(qv, 3)));
    scores.push(achievement("Quality view", Number(qv) >= 0.5, "Achieved (q ≥ 0.5)", "Below 0.5", weighted));
  }
  if (aq != null) {
    if (facades.length) scores.push(row("Outdoor level", facades.map((d) => `${d} ${f(site.L[d], 0)}`).join(", ") + " dB(A)"));
    scores.push(row("Acoustic score a_i", f(aq, 3)));
  }
  if (room.est_avg_spl_dB != null) scores.push(row("Indoor level", `${f(room.est_avg_spl_dB, 1)} dB(A)` + (room.guideline_dB ? `  (guideline ${f(room.guideline_dB, 0)})` : "")));
  if (room.acoustic_ok != null) scores.push(achievement("Acoustic guideline", !!room.acoustic_ok, "Met", "Exceeded", weighted));
  if (z != null) scores.push(row("Index weight", weighted ? f(room.weight || 0, 3) : "0 (not a regularly occupied room)"));

  const el = h("div.room-pop", { role: "dialog", "aria-label": `Room ${room.room_id}` },
    h("div.rp-head", h("span.id", room.room_id), h("span.grow"), h("span.type", String(room.room_type || "").toUpperCase()),
      h("button.icon-btn", { title: "Close (Esc)", on: { click: () => closeRoomPopup() } }, "✕")),
    h("div.rp-div"), ...props, scores.length ? h("div.rp-div") : null, ...scores);
  document.body.append(el);

  // place it next to the click, inside the viewport
  const r = el.getBoundingClientRect(), M = 8;
  let left = x + 12, top = y + 12;
  if (left + r.width > window.innerWidth - M) left = Math.max(M, x - r.width - 12);
  if (top + r.height > window.innerHeight - M) top = Math.max(M, window.innerHeight - r.height - M);
  el.style.left = `${left}px`; el.style.top = `${top}px`;

  // Esc closes the card before anything else (e.g. the detail view), and so
  // does a click elsewhere or scrolling the page underneath it.
  const onKey = (e) => { if (e.key === "Escape") { e.stopImmediatePropagation(); e.preventDefault(); closeRoomPopup(); } };
  const onDown = (e) => { if (!el.contains(e.target)) closeRoomPopup(); };
  const onScroll = (e) => { if (!el.contains(e.target)) closeRoomPopup(); };
  window.addEventListener("keydown", onKey, true);
  setTimeout(() => { if (current && current.el === el) { window.addEventListener("pointerdown", onDown, true); window.addEventListener("scroll", onScroll, true); } }, 0);
  current = {
    el,
    cleanup() {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("scroll", onScroll, true);
    },
  };
}
