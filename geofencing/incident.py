"""
incident.py — Emergency & incident response: automated mustering and
proximity dispatch.

Both operate on the last-known PositionSample per entity (see
tracking.py) plus the ZoneHierarchy (see floorplan.py) needed to know
which zone each position falls in.

Math foundation (IB AA HL tie-ins):
  - Mustering confidence decay: an exponential decay of trust in a
    "last known position" as time since its last update grows --
    exponential functions, same functional form as the barometric
    altitude model mentioned in the roadmap.
  - Proximity dispatch: nearest-neighbour by Euclidean distance (vector
    magnitude, reused from tracking.py's tethering distance), with a
    documented path to corridor-aware shortest-path as a phase 2.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from .tracking import PositionSample


# ---------------------------------------------------------------------------
# Automated mustering
# ---------------------------------------------------------------------------

@dataclass
class MusterZoneCount:
    zone_id: str
    zone_label: str
    total_count: int
    stale_count: int  # subset of total_count whose last position is aging


@dataclass
class MusterReport:
    zone_counts: List[MusterZoneCount]
    unresolved: List[str]  # entity_ids with no last-known zone at all

    def total_inside(self) -> int:
        return sum(z.total_count for z in self.zone_counts) + len(self.unresolved)


def position_confidence(sample: PositionSample, now_s: float, half_life_s: float = 60.0) -> float:
    """
    Confidence in a last-known position decays exponentially with time
    since it was last updated: confidence = 0.5 ** (age / half_life).
    A position that's 60s stale (the default half-life) is trusted half
    as much as a fresh one; a position that's several half-lives stale
    should be flagged, not presented with false confidence during an
    evacuation headcount.
    """
    age_s = max(0.0, now_s - sample.timestamp_s)
    return 0.5 ** (age_s / half_life_s)


def muster(
    last_known: Dict[str, PositionSample],
    zone_lookup,  # callable: PositionSample -> Optional[ZoneProfile] (i.e. ZoneHierarchy.resolve)
    now_s: float,
    stale_confidence_threshold: float = 0.25,
) -> MusterReport:
    """Group every currently-registered entity by last-known zone, flagging stale fixes."""
    per_zone: Dict[str, Dict[str, object]] = {}
    unresolved: List[str] = []

    for entity_id, sample in last_known.items():
        zone = zone_lookup(sample.position)
        if zone is None:
            unresolved.append(entity_id)
            continue

        confidence = position_confidence(sample, now_s)
        bucket = per_zone.setdefault(
            zone.zone_name, {"label": zone.zone_name, "total": 0, "stale": 0}
        )
        bucket["total"] += 1
        if confidence < stale_confidence_threshold:
            bucket["stale"] += 1

    zone_counts = [
        MusterZoneCount(zone_id=zid, zone_label=b["label"], total_count=b["total"], stale_count=b["stale"])
        for zid, b in per_zone.items()
    ]
    return MusterReport(zone_counts=zone_counts, unresolved=unresolved)


# ---------------------------------------------------------------------------
# Active presence tracking (TTL / heartbeat exit detection)
# ---------------------------------------------------------------------------
# muster() and position_confidence() above are passive: a stale position
# just gets flagged, and only when someone happens to run a report. Real
# on-site testing found tags that stop reporting (signal dropout, tag
# left behind, badge handed back without checkout) need an ACTIVE exit
# event fired on their own -- not just a flag waiting to be noticed.

@dataclass
class ExitEvent:
    entity_id: str
    last_seen_s: float
    silent_for_s: float


class PresenceTracker:
    """
    TTL/heartbeat presence tracking: call heartbeat() every time a
    position update arrives for an entity, and check_exits() on a
    regular timer (e.g. every 30-60s) -- not just when a muster report
    happens to be requested. Anyone silent longer than ttl_s gets an
    exit event fired exactly once, not repeated on every subsequent
    check while still silent.
    """

    def __init__(self, ttl_s: float = 240.0):  # 4 minutes, within the requested 3-5 min range
        self.ttl_s = ttl_s
        self._last_seen: Dict[str, float] = {}
        self._exited: set = set()

    def heartbeat(self, entity_id: str, timestamp_s: float) -> None:
        """Call on every position update. A fresh heartbeat un-exits someone who'd been flagged."""
        self._last_seen[entity_id] = timestamp_s
        self._exited.discard(entity_id)

    def check_exits(self, now_s: float) -> List[ExitEvent]:
        """Returns exit events for anyone silent past the TTL who hasn't already been flagged."""
        events = []
        for entity_id, last_seen in self._last_seen.items():
            if entity_id in self._exited:
                continue
            silent_for = now_s - last_seen
            if silent_for >= self.ttl_s:
                events.append(ExitEvent(entity_id=entity_id, last_seen_s=last_seen, silent_for_s=silent_for))
                self._exited.add(entity_id)
        return events

    def is_present(self, entity_id: str) -> bool:
        return entity_id in self._last_seen and entity_id not in self._exited


# ---------------------------------------------------------------------------
# Proximity dispatch
# ---------------------------------------------------------------------------

@dataclass
class DispatchCandidate:
    guard_id: str
    distance_m: float


def nearest_guards(
    incident_position: tuple,
    guard_positions: Dict[str, PositionSample],
    top_n: int = 3,
) -> List[DispatchCandidate]:
    """
    Straight-line nearest-neighbour dispatch. Fine for open floor plans;
    for anything with corridors/locked doors between guard and incident,
    this can pick someone who's close as the crow flies but far as the
    crow walks. The real fix is a floor walking-graph (nodes = corridor
    junctions/doors, edges = weighted by walking distance) and shortest
    path via Dijkstra -- worth building once the floor plan data from
    floorplan.py has doors/corridors modelled, not before.
    """
    candidates = [
        DispatchCandidate(guard_id=gid, distance_m=math.dist(incident_position, sample.position))
        for gid, sample in guard_positions.items()
    ]
    candidates.sort(key=lambda c: c.distance_m)
    return candidates[:top_n]


if __name__ == "__main__":
    # --- Mustering demo ---
    print("=== Automated mustering ===")

    class _FakeZone:
        def __init__(self, zone_id, label):
            self.zone_name, self.label = zone_id, label

    lobby = _FakeZone("Z-LOBBY", "Public Lobby")
    server_room = _FakeZone("Z-SERVER", "Server Room")

    def fake_zone_lookup(pos):
        # In real use this is ZoneHierarchy.resolve(pos) from floorplan.py.
        x, y, z = pos
        if 20 <= x <= 28 and 5 <= y <= 12:
            return server_room
        if 0 <= x <= 30 and 0 <= y <= 20:
            return lobby
        return None

    now = 300.0
    last_known = {
        "VIS-1": PositionSample("VIS-1", 295.0, (5.0, 5.0, 1.5)),
        "VIS-2": PositionSample("VIS-2", 298.0, (6.0, 6.0, 1.5)),
        "VIS-3": PositionSample("VIS-3", 100.0, (24.0, 8.0, 1.5)),  # stale: 200s old
        "VIS-4": PositionSample("VIS-4", 299.5, (-5.0, -5.0, 1.5)),  # outside all zones
    }

    report = muster(last_known, fake_zone_lookup, now_s=now)
    for zc in report.zone_counts:
        flag = f" ({zc.stale_count} stale)" if zc.stale_count else ""
        print(f"  {zc.zone_label}: {zc.total_count} visitor(s){flag}")
    if report.unresolved:
        print(f"  Unresolved (no zone match): {report.unresolved}")
    print(f"  Total accounted for: {report.total_inside()}")

    # --- Proximity dispatch demo ---
    print("\n=== Proximity dispatch ===")
    incident_position = (24.0, 8.0, 1.5)  # breach in the server room
    guards = {
        "GRD-1": PositionSample("GRD-1", now, (2.0, 2.0, 1.5)),
        "GRD-2": PositionSample("GRD-2", now, (22.0, 9.0, 1.5)),
        "GRD-3": PositionSample("GRD-3", now, (28.0, 18.0, 1.5)),
    }
    for candidate in nearest_guards(incident_position, guards, top_n=3):
        print(f"  {candidate.guard_id}: {candidate.distance_m:.1f}m away")

    # --- Active presence / TTL exit demo ---
    print("\n=== Active presence tracking (TTL exit events) ===")
    presence = PresenceTracker(ttl_s=240.0)

    presence.heartbeat("VIS-5", timestamp_s=0.0)
    presence.heartbeat("VIS-6", timestamp_s=0.0)

    # VIS-6 keeps reporting normally; VIS-5 goes silent (dropped signal,
    # tag left behind, badge handed back without checkout).
    for t in (60.0, 120.0, 180.0):
        presence.heartbeat("VIS-6", timestamp_s=t)
        events = presence.check_exits(now_s=t)
        for e in events:
            print(f"  t={t:.0f}s: EXIT EVENT -- {e.entity_id} silent for {e.silent_for_s:.0f}s")
        if not events:
            print(f"  t={t:.0f}s: no exit events (VIS-5 silent {t:.0f}s so far, under the {presence.ttl_s:.0f}s TTL)")

    # Past the TTL now -- VIS-5 should fire exactly once.
    events = presence.check_exits(now_s=250.0)
    for e in events:
        print(f"  t=250s: EXIT EVENT -- {e.entity_id} silent for {e.silent_for_s:.0f}s")

    # Checking again later without a new heartbeat should NOT re-fire.
    events_again = presence.check_exits(now_s=400.0)
    print(f"  t=400s: re-check without new heartbeat -- {len(events_again)} new events (should be 0, already flagged)")

    # A late heartbeat un-exits them.
    presence.heartbeat("VIS-5", timestamp_s=450.0)
    print(f"  t=450s: VIS-5 sends a heartbeat again -- is_present={presence.is_present('VIS-5')}")
