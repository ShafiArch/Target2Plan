// planEdit.js: edits of Stage 4 plans made in the plan editor. An edited plan
// keeps its rooms in metres (s.edit.rooms) and the doors turned with Ctrl+D
// (s.edit.doors); its synthesis, room scores and QVCI / AQI then follow the
// drawing. The plan as generated stays available (s.orig, the original
// synthesis) for "Reset" and for clearing the edit.
import { buildLayout, layoutFromSynthesis } from "../engine/layout.js";
import { doorKey } from "../engine/schematic.js";
import { originalSynthesis } from "./schematicPaint.js";

// not saved with the session (toSession drops "_" keys; orig is rebuilt on load)
const hidden = (o, k, v) => { if (Object.prototype.hasOwnProperty.call(o, k)) o[k] = v; else Object.defineProperty(o, k, { value: v, enumerable: false, writable: true, configurable: true }); };
const q3 = (v) => Math.round(Number(v) * 1000) / 1000;

/** Rooms in metres of the plan as generated. */
export function originalLayout(s) { return layoutFromSynthesis(originalSynthesis(s)); }

/** Rooms in metres as the plan stands now (edited or as generated). */
export function currentLayout(s) { return s.edit ? s.edit.rooms.map((r) => ({ ...r })) : originalLayout(s); }

/** Doors turned in the editor as the plan stands now: {doorKey: {end, into}} (none: {}). */
export function currentDoors(s) { return s.edit && s.edit.doors ? copyDoors(s.edit.doors) : {}; }

const copyDoors = (d) => Object.fromEntries(Object.entries(d || {}).map(([k, v]) => [k, { end: v.end, into: v.into }]));

/** Same rooms at the same places and sizes (to the millimetre)? */
export function sameLayout(a, b) {
  if (a.length !== b.length) return false;
  const byId = new Map(b.map((r) => [r.id, r]));
  return a.every((r) => { const o = byId.get(r.id); return o && ["x", "y", "w", "h"].every((k) => Math.abs(q3(r[k]) - q3(o[k])) < 1e-9); });
}

/** The same doors turned the same way? */
export function sameDoors(a, b) {
  const ka = Object.keys(a || {}), kb = Object.keys(b || {});
  return ka.length === kb.length && ka.every((k) => b[k] && a[k].end === b[k].end && a[k].into === b[k].into);
}

/** Door choices that fit a plan with rooms `ids`: both rooms in it, an end of 0 or 1,
 *  opening into one of the two (anything else, from an older or altered file, is dropped). */
function validDoors(doors, ids) {
  const out = {};
  for (const [k, v] of Object.entries(doors || {})) {
    const pair = String(k).split("|");
    if (pair.length !== 2 || !pair.every((id) => ids.has(id)) || doorKey(...pair) !== k || !v || typeof v !== "object") continue;
    if ((v.end === 0 || v.end === 1) && pair.includes(v.into)) out[k] = { end: v.end, into: v.into };
  }
  return out;
}

/** Build an edited plan without changing it (the editor's live view and its exports). */
export function previewEdit(s, rooms, inputs, doors = null) {
  return buildLayout(s, rooms, originalSynthesis(s), inputs, doors);
}

/** Put edited rooms (and turned doors) into the plan. The plan as generated with no
 *  door turned clears the edit. */
export function applyEdit(s, rooms, inputs, doors = null) {
  if (!s.orig) hidden(s, "orig", { computed: s.computed, rooms: s.rooms });
  const turned = validDoors(doors, new Set(rooms.map((r) => r.id)));
  const anyDoor = Object.keys(turned).length > 0;
  if (!anyDoor && sameLayout(rooms, originalLayout(s))) return clearEdit(s);
  const { syn, scored } = previewEdit(s, rooms, inputs, anyDoor ? turned : null);
  const present = new Set(rooms.map((r) => r.id));
  s.edit = { rooms: rooms.map(({ id, type, x, y, w, h }) => ({ id, type, x: q3(x), y: q3(y), w: q3(w), h: q3(h) })) };
  if (anyDoor) s.edit.doors = turned;
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

/** Re-apply the edits of a loaded session to the rebuilt plans; returns how many applied.
 *  An edit is {rooms, doors} (files saved before doors could be turned hold the rooms alone). */
export function applySessionEdits(schematics, edits, inputs) {
  let n = 0;
  if (!edits) return n;
  for (const s of schematics) {
    const e = edits[s.plan_index] ?? edits[String(s.plan_index)];
    const rooms = Array.isArray(e) ? e : e && Array.isArray(e.rooms) ? e.rooms : null;
    if (!rooms || !rooms.length) continue;
    const ids = new Set(originalLayout(s).map((r) => r.id));
    if (!rooms.every((r) => ids.has(r.id))) continue;            // a different plan now: leave it as generated
    applyEdit(s, rooms.map((r) => ({ ...r })), inputs, Array.isArray(e) ? null : e.doors);
    n++;
  }
  return n;
}
