"""
permits.py -- Time-windowed zone/floor authorization.

The gap named explicitly in the architecture walkthrough: Visitor.allowed_
areas says *which* zones a visitor can access, but nothing in the existing
model says *when*. This fixes that generically -- a Permit works for any
zone_id, whether that's a ZoneProfile.zone_name (lobby, server_room) or
an elevator FloorBeacon.floor_id (F1, F2, ...). One authorization model,
not a zone-flavoured one and a floor-flavoured one -- see floorplan.py's
history tonight for why that's worth avoiding on purpose.

Not really "math" -- this is a data model plus a datetime comparison.
Included here because it's real, named work, not because it needed new
theory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Permit:
    visitor_id: str
    zone_id: str  # a ZoneProfile.zone_name OR a FloorBeacon.floor_id -- same field, either use
    valid_from: datetime
    valid_until: datetime
    requires_escort: bool = False


def check_authorization(
    visitor_id: str,
    zone_id: str,
    now: datetime,
    permits: List[Permit],
    escort_present: bool = False,
) -> Tuple[bool, str]:
    """
    Returns (allowed, reason). Reason is always populated, including on
    denial -- for a security decision, "why" matters as much as the
    boolean, both for the person at the door and for the audit log.
    """
    matching = [p for p in permits if p.visitor_id == visitor_id and p.zone_id == zone_id]
    if not matching:
        return False, f"No permit exists for {visitor_id} on {zone_id}"

    active = [p for p in matching if p.valid_from <= now <= p.valid_until]
    if not active:
        nearest = min(
            matching,
            key=lambda p: min(abs((now - p.valid_from).total_seconds()), abs((now - p.valid_until).total_seconds())),
        )
        if now < nearest.valid_from:
            return False, f"Permit for {visitor_id} on {zone_id} not yet active (starts {nearest.valid_from})"
        return False, f"Permit for {visitor_id} on {zone_id} expired at {nearest.valid_until}"

    permit = active[0]
    if permit.requires_escort and not escort_present:
        return False, f"Permit for {visitor_id} on {zone_id} requires an escort"

    return True, f"Authorized ({permit.valid_from} - {permit.valid_until})"


class PermitRegistry:
    """Thin lookup wrapper so callers don't have to pass the full permit list around."""

    def __init__(self) -> None:
        self._permits: List[Permit] = []

    def add(self, permit: Permit) -> None:
        self._permits.append(permit)

    def check(
        self, visitor_id: str, zone_id: str, now: datetime, escort_present: bool = False
    ) -> Tuple[bool, str]:
        return check_authorization(visitor_id, zone_id, now, self._permits, escort_present)


if __name__ == "__main__":
    from datetime import timedelta

    registry = PermitRegistry()
    today = datetime(2026, 9, 14, 9, 0)  # 9:00 AM

    registry.add(Permit(
        visitor_id="V-JDOE", zone_id="F5",
        valid_from=today, valid_until=today + timedelta(hours=8),  # 9am-5pm
        requires_escort=False,
    ))
    registry.add(Permit(
        visitor_id="V-JDOE", zone_id="server_room",
        valid_from=today, valid_until=today + timedelta(hours=8),
        requires_escort=True,
    ))

    cases = [
        ("Within window, no escort needed", "V-JDOE", "F5", today + timedelta(hours=2), False),
        ("After hours (7pm)", "V-JDOE", "F5", today + timedelta(hours=10), False),
        ("Before window opens (6am next check)", "V-JDOE", "F5", today - timedelta(hours=3), False),
        ("Escort-required zone, no escort present", "V-JDOE", "server_room", today + timedelta(hours=1), False),
        ("Escort-required zone, escort present", "V-JDOE", "server_room", today + timedelta(hours=1), True),
        ("No permit exists at all", "V-JDOE", "F12", today + timedelta(hours=1), False),
    ]

    print("=== Permit authorization checks ===")
    for label, vid, zone, when, escort in cases:
        allowed, reason = registry.check(vid, zone, when, escort)
        status = "ALLOWED" if allowed else "DENIED "
        print(f"  [{status}] {label}")
        print(f"            {reason}")
