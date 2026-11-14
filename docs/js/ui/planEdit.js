// planEdit.js: edits of Stage 4 plans made in the plan editor. An edited plan
// keeps its rooms in metres (s.edit.rooms); its synthesis, room scores and
// QVCI / AQI then follow the drawing. The plan as generated stays available
// (s.orig, the original synthesis) for "Reset" and for clearing the edit.
import { buildLayout, layoutFromSynthesis } from "../engine/layout.js";
import { originalSynthesis } from "./schematicPaint.js";

// not saved with the session (toSession drops "_" keys; orig is rebuilt on load)
const hidden = (o, k, v) => { if (Object.prototype.hasOwnProperty.call(o, k)) o[k] = v; else Object.defineProperty(o, k, { value: v, enumerable: false, writable: true, configurable: true }); };
const q3 = (v) => Math.round(Number(v) * 1000) / 1000;

/** Rooms in metres of the plan as generated. */
export function originalLayout(s) { return layoutFromSynthesis(originalSynthesis(s)); }

/** Rooms in metres as the plan stands now (edited or as generated). */
export function currentLayout(s) { return s.edit ? s.edit.rooms.map((r) => ({ ...r })) : originalLayout(s); }

/** Same rooms at the same places and sizes (to the millimetre)? */
export function sameLayout(a, b) {
  if (a.length !== b.length) return false;
  const byId = new Map(b.map((r) => [r.id, r]));
  return a.every((r) => { const o = byId.get(r.id); return o && ["x", "y", "w", "h"].every((k) => Math.abs(q3(r[k]) - q3(o[k])) < 1e-9); });
}

/** Build an edited plan without changing it (the editor's live view and its exports). */
export function previewEdit(s, rooms, inputs) {
  return buildLayout(s, rooms, originalSynthesis(s), inputs);
}

/** Put edited rooms into the plan. Rooms equal to the plan as generated clear the edit. */
export function applyEdit(s, rooms, inputs) {
  if (!s.orig) hidden(s, "orig", { computed: s.computed, rooms: s.rooms });
  if (sameLayout(rooms, originalLayout(s))) return clearEdit(s);
  const { syn, scored } = previewEdit(s, rooms, inputs);
  const present = new Set(rooms.map((r) => r.id));
  s.edit = { rooms: rooms.map(({ id, type, x, y, w, h }) => ({ id, type, x: q3(x), y: q3(y), w: q3(w), h: q3(h) })) };
  s.computed = { ...scored.computed };
  // rooms dropped by the synthesis (a detached balcony) stay listed as they were
  s.rooms = [...scored.rooms, ...s.orig.rooms.filter((r) => !present.has(r.room_id))];
  hidden(s, "_syn", syn);
  return s;
}

/** Back to the plan as generated. */
export function clearEdit(s) {
  if (s.orig) { s.computed = s.orig.computed; s.rooms = s.orig.rooms; }
  delete s.edit;
  hidden(s, "_syn", originalSynthesis(s));
  return s;
}

/** Re-apply the edits of a loaded session to the rebuilt plans; returns how many applied. */
export function applySessionEdits(schematics, edits, inputs) {
  let n = 0;
  if (!edits) return n;
  for (const s of schematics) {
    const rooms = edits[s.plan_index] || edits[String(s.plan_index)];
    if (!Array.isArray(rooms) || !rooms.length) continue;
    const ids = new Set(originalLayout(s).map((r) => r.id));
    if (!rooms.every((r) => ids.has(r.id))) continue;            // a different plan now: leave it as generated
    applyEdit(s, rooms.map((r) => ({ ...r })), inputs);
    n++;
  }
  return n;
}
