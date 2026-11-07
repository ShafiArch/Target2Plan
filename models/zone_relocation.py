"""
Zone reconciliation and secondary-room relocation (between Stage 2 and 3).

After the zone model has placed the rooms, two things are checked
against the user's inputs:

1. **Reconciliation** — the room list of the zone table is matched with
   the requested room counts. Requested rooms the zone stage did not
   place (the balcony, which is not a zoned type) are *added* with no
   zone; rooms that were not requested are *removed*.

2. **Relocation of secondary rooms** — the primary living areas
   (living, bedroom, kitchen) keep the zone the model predicted. Every
   other room (bathroom, balcony, …) must sit in the same zone as, or in
   a zone adjacent to, the primary room it is connected to:

   * if it already is, it stays where it is;
   * otherwise it moves to one of the adjacent zones of that primary
     room: the least crowded one, trying the centre zone Z5 last, and
     choosing at random (seeded, so reproducible) between equally
     crowded candidates.

Zones are numbered Z1 … Z9 row-major from the north-west (the table
stores them 0-based as ``zone_assignment``). Adjacency is the 8-neighbour
rule on the 3 × 3 grid::

    Z1 Z2 Z3
    Z4 Z5 Z6
    Z7 Z8 Z9
"""

from __future__ import annotations

import random
from collections import Counter, deque
from typing import Dict, Iterable, List, Optional, Tuple

PRIMARY_TYPES = {"living", "bedroom", "kitchen"}
CENTRE_ZONE = 4                                   # Z5, 0-based

# 0-based adjacency (Z1 == 0)
ADJACENT: Dict[int, Tuple[int, ...]] = {
    0: (1, 3, 4),
    1: (0, 2, 3, 4, 5),
    2: (1, 4, 5),
    3: (0, 1, 4, 6, 7),
    4: (0, 1, 2, 3, 5, 6, 7, 8),
    5: (1, 2, 4, 7, 8),
    6: (3, 4, 7),
    7: (3, 4, 5, 6, 8),
    8: (4, 5, 7),
}


def zone_label(z: Optional[int]) -> str:
    return "—" if z is None else f"Z{int(z) + 1}"


# ─────────────────────────────────────────────────────────────────────
#  1. Reconcile the zone table with the requested rooms
# ─────────────────────────────────────────────────────────────────────

def reconcile_rooms(rooms: List[dict], room_counts: Optional[Dict[str, int]]
                    ) -> Tuple[List[dict], List[str], List[str]]:
    """Return ``(rooms, added_ids, removed_ids)``.

    Rooms are matched by type and count: for every type the first
    ``count`` rooms of the table are kept (in table order), extra ones
    are dropped, and missing ones are appended as ``{type}_{k}`` with
    ``zone_assignment = None``.
    """
    if not room_counts:
        return list(rooms), [], []
    kept: List[dict] = []
    seen: Counter = Counter()
    removed: List[str] = []
    for r in rooms:
        rt = str(r.get("room_type", "other")).lower()
        want = int(room_counts.get(rt, 0) or 0)
        if seen[rt] < want:
            kept.append(r); seen[rt] += 1
        else:
            removed.append(r.get("room_id", f"{rt}_?"))
    added: List[str] = []
    for rt, want in room_counts.items():
        want = int(want or 0)
        while seen[rt] < want:
            rid = f"{rt}_{seen[rt]}"
            kept.append({"room_id": rid, "room_type": rt, "zone_assignment": None})
            added.append(rid); seen[rt] += 1
    return kept, added, removed


# ─────────────────────────────────────────────────────────────────────
#  2. Relocate secondary rooms next to the primary room they connect to
# ─────────────────────────────────────────────────────────────────────

def _anchor_primary(rid: str, adj: Dict[str, List[str]], primary_ids: set,
                    fallback: Optional[str]) -> Optional[str]:
    """Nearest primary room reachable from ``rid`` through the edges
    (breadth-first, so a bathroom reached through another bathroom
    still finds the bedroom behind it)."""
    seen = {rid}
    q = deque([rid])
    while q:
        cur = q.popleft()
        for nb in adj.get(cur, []):
            if nb in primary_ids:
                return nb
            if nb not in seen and not nb.startswith("outside"):
                seen.add(nb); q.append(nb)
    return fallback


BALCONY_ANCHOR_PRIORITY = {"living": 0, "bedroom": 1, "kitchen": 2}
OUTDOOR_TYPES = {"balcony"}
PERIMETER_ZONES = {0, 1, 2, 3, 5, 6, 7, 8}
# orthogonal (edge-sharing) neighbours on the 3 × 3 grid
ORTHO: Dict[int, Tuple[int, ...]] = {
    0: (1, 3), 1: (0, 2, 4), 2: (1, 5), 3: (0, 4, 6), 4: (1, 3, 5, 7),
    5: (2, 4, 8), 6: (3, 7), 7: (4, 6, 8), 8: (5, 7),
}


def balcony_anchor(rid: str, adj: Dict[str, List[str]], by_id: Dict[str, dict],
                   taken: Dict[str, str]) -> Tuple[Optional[str], bool]:
    """The room a balcony opens from.

    The predicted connection is kept when it is a living room, bedroom or
    kitchen (living preferred when there are several). A balcony the
    model connected to a bathroom only (or to nothing) is re-assigned to
    the living room or a bedroom that does not yet have a balcony
    (living first); ``taken`` maps rooms that already own a balcony to
    it. Returns ``(anchor_id, rewired)``."""
    cands = [n for n in adj.get(rid, []) if n in by_id
             and str(by_id[n].get("room_type", "")) in BALCONY_ANCHOR_PRIORITY
             and by_id[n].get("zone_assignment") is not None]
    if cands:
        cands.sort(key=lambda n: (BALCONY_ANCHOR_PRIORITY[str(by_id[n]["room_type"])],
                                  n in taken, n))
        return cands[0], False
    free = [n for n, r in by_id.items()
            if str(r.get("room_type")) in ("living", "bedroom")
            and r.get("zone_assignment") is not None and n not in taken]
    if not free:
        free = [n for n, r in by_id.items()
                if str(r.get("room_type")) in ("living", "bedroom")
                and r.get("zone_assignment") is not None]
    if not free:
        return None, False
    free.sort(key=lambda n: (BALCONY_ANCHOR_PRIORITY[str(by_id[n]["room_type"])], n))
    return free[0], True


# sides of each zone that lie on the plan boundary
ZONE_FACADES: Dict[int, Tuple[str, ...]] = {
    0: ("N", "W"), 1: ("N",), 2: ("N", "E"),
    3: ("W",), 4: (), 5: ("E",),
    6: ("S", "W"), 7: ("S",), 8: ("S", "E"),
}
SIDE_ZONES: Dict[str, Tuple[int, int, int]] = {
    "N": (0, 1, 2), "S": (6, 7, 8), "W": (0, 3, 6), "E": (2, 5, 8),
}


def balcony_facade(zone: int, occupancy, rng: random.Random, used: set) -> Tuple[str, str]:
    """Which façade (N/S/E/W) of ``zone`` a balcony should hang on."""
    sides = list(ZONE_FACADES.get(zone, ()))
    if not sides:                       # interior room: the quietest boundary
        sides = ["N", "S", "E", "W"]
        why = "interior room — least crowded side of the plan"
    elif len(sides) == 1:
        return sides[0], "only façade of this zone"
    else:
        why = "least crowded of the zone's two façades"
    fresh = [sd for sd in sides if (zone, sd) not in used] or sides
    load = {sd: sum(occupancy[z] for z in SIDE_ZONES[sd]) for sd in fresh}
    best = min(load.values())
    tied = [sd for sd in fresh if load[sd] == best]
    if len(tied) == 1:
        return tied[0], why
    return rng.choice(tied), why + ", random tie"


def rewire_balcony_edges(rooms: List[dict], edges: List[Tuple[str, str]]
                         ) -> Tuple[List[Tuple[str, str]], List[dict]]:
    """Replace the connections of every balcony by a single edge to its
    anchor room (see :func:`balcony_anchor`). Returns the new edge list
    and a log of the changes."""
    by_id = {r["room_id"]: r for r in rooms}
    adj: Dict[str, List[str]] = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b); adj.setdefault(b, []).append(a)
    living = next((r["room_id"] for r in rooms if r.get("room_type") == "living"
                   and r.get("zone_assignment") is not None), None)
    balconies = [r["room_id"] for r in rooms if str(r.get("room_type")) in OUTDOOR_TYPES]
    if not balconies:
        return list(edges), []
    out: List[Tuple[str, str]] = []
    log: List[dict] = []
    keep: Dict[str, str] = {}
    taken: Dict[str, str] = {}
    # balconies the model already attached to a living/bedroom/kitchen first,
    # so the re-assigned ones see which rooms are taken
    def _has_good(bid):
        return any(str(by_id.get(n, {}).get("room_type")) in BALCONY_ANCHOR_PRIORITY
                   for n in adj.get(bid, []))
    for bid in sorted(balconies, key=lambda b: (not _has_good(b), b)):
        anchor, rewired = balcony_anchor(bid, adj, by_id, taken)
        if anchor is None:
            continue
        keep[bid] = anchor
        taken[anchor] = bid
        dropped = [n for n in adj.get(bid, []) if n != anchor]
        if rewired or dropped:
            log.append({"room_id": bid, "anchor": anchor, "dropped": dropped,
                        "reason": (f"{bid} was on {', '.join(dropped) or 'nothing'} → re-assigned to {anchor}" if rewired
                                   else f"{bid} kept on {anchor}, dropped {', '.join(dropped)}")})
    for a, b in edges:
        if a in keep or b in keep:
            continue
        out.append((a, b))
    for bid, anchor in keep.items():
        out.append((anchor, bid))
    return out, log


def relocate_secondary_rooms(rooms: List[dict], edges: Iterable[Tuple[str, str]],
                             seed: int = 0) -> List[dict]:
    """Decide the final zone of every room.

    Returns a list of ``{"room_id", "room_type", "zone_start",
    "zone", "anchor", "reason"}`` — one per room, in input order.
    Primary rooms never move. Balconies always end on a perimeter zone
    next to (preferably edge-sharing with) the room they open from.
    The input dicts are not modified.
    """
    rng = random.Random(int(seed))
    by_id = {r["room_id"]: r for r in rooms}
    primary_ids = {r["room_id"] for r in rooms
                   if str(r.get("room_type", "")).lower() in PRIMARY_TYPES
                   and r.get("zone_assignment") is not None}
    adj: Dict[str, List[str]] = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    living = next((r["room_id"] for r in rooms if r.get("room_type") == "living"
                   and r.get("zone_assignment") is not None), None)

    # current occupancy (updated as rooms move, so later decisions see
    # earlier moves)
    occupancy: Counter = Counter(int(r["zone_assignment"]) for r in rooms
                                 if r.get("zone_assignment") is not None)
    out: List[dict] = []
    facades_used: set = set()
    # secondary rooms in a stable order: bathrooms first, then the rest
    order = sorted(rooms, key=lambda r: (r["room_id"] in primary_ids,
                                         str(r.get("room_type")) != "bathroom",
                                         r["room_id"]))
    for r in order:
        rid = r["room_id"]
        z0 = r.get("zone_assignment")
        z0 = None if z0 is None else int(z0)
        rec = {"room_id": rid, "room_type": r.get("room_type"), "zone_start": z0,
               "zone": z0, "anchor": None, "reason": "primary — fixed"}
        if rid in primary_ids:
            out.append(rec); continue
        is_outdoor = str(r.get("room_type")) in OUTDOOR_TYPES
        if is_outdoor:
            anchor = next((n for n in adj.get(rid, []) if n in by_id
                           and str(by_id[n].get("room_type")) in BALCONY_ANCHOR_PRIORITY), None)
        else:
            anchor = _anchor_primary(rid, adj, primary_ids, living)
        rec["anchor"] = anchor
        if anchor is None or by_id[anchor].get("zone_assignment") is None:
            rec["reason"] = "no connected primary room — kept"
            out.append(rec); continue
        za = int(by_id[anchor]["zone_assignment"])
        if is_outdoor:
            # A balcony has no zone: it hangs outside the plan on a façade
            # of the room it opens from. Façade = a side of that room's
            # zone that lies on the plan boundary (an interior room gets
            # the boundary side with the fewest rooms); ties are random.
            side, why = balcony_facade(za, occupancy, rng, facades_used)
            facades_used.add((za, side))
            rec["zone"] = None
            rec["facade"] = side
            rec["reason"] = f"balcony outside {anchor} on its {side} façade ({why})"
            out.append(rec); continue
        allowed = set(ADJACENT[za]) | {za}
        if z0 is not None and z0 in allowed:
            rec["reason"] = ("same zone as " if z0 == za else "adjacent to ") + anchor
            out.append(rec); continue
        # must move: adjacent zones of the anchor, least crowded, Z5 last
        cands = list(ADJACENT[za])
        non_centre = [z for z in cands if z != CENTRE_ZONE]
        pool = non_centre or cands
        best = min(occupancy[z] for z in pool)
        if best > 0 and CENTRE_ZONE in cands and occupancy[CENTRE_ZONE] < best:
            choice = CENTRE_ZONE                       # every other neighbour is busier
            why = "all other adjacent zones crowded — centre Z5"
        else:
            tied = [z for z in pool if occupancy[z] == best]
            choice = tied[0] if len(tied) == 1 else rng.choice(tied)
            why = ("least crowded adjacent zone" if len(tied) == 1
                   else "random among equally crowded adjacent zones")
        if z0 is not None:
            occupancy[z0] -= 1
        occupancy[choice] += 1
        rec["zone"] = choice
        rec["reason"] = f"moved {zone_label(z0)} → {zone_label(choice)} next to {anchor} ({why})"
        out.append(rec)
    # restore input order
    pos = {r["room_id"]: i for i, r in enumerate(rooms)}
    out.sort(key=lambda d: pos[d["room_id"]])
    return out
