"""
floorplan.py — Dynamic floor plan mapping & multi-tiered zone containment.

The foundation layer for Zone Mapping & Geofence Management: turns a blueprint
image + hand-drawn polygons into real-world zone geometry, and resolves any
tag's (x, y, z) position to the correct — and most specific — zone.

This module previously defined its own standalone `Polygon` class, which
duplicated point-in-polygon and area logic already present on the real
`ZoneProfile` (see models.py). That duplication has been reconciled:
`ZoneProfile` now owns the 3D containment (`contains_3d`) and area
(`area`) logic, and `ZoneHierarchy` below operates directly on
`ZoneProfile` instances instead of a parallel type. This module now only
adds what `ZoneProfile` didn't already have: blueprint pixel-coordinate
calibration, and area-based resolution across a whole facility's zones.

Math foundation (IB AA HL tie-ins, matching the rest of this project):
  - CoordinateCalibrator: complex numbers in modulus-argument form. A
    similarity transform (rotation + uniform scale + translation) between
    two coordinate systems is exactly one complex multiply-and-add,
    z' = a*z + b, solved from two reference point pairs.
  - ZoneProfile.area() (models.py): the shoelace formula, a direct
    sigma-notation sum over vertex pairs — used here as the tie-breaker
    for nested zones.
  - ZoneProfile.contains() / contains_3d() (models.py): ray-casting
    point-in-polygon, coordinate geometry / vectors, extruded into a
    vertical prism for floor-aware containment.
  - ZoneHierarchy: containment resolved by ascending zone area, since a
    restricted zone is by construction smaller than the zone it nests
    inside (Server Room < Escort Required < Public Lobby).
"""

from __future__ import annotations

import cmath
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .models import ZoneProfile

Point = Tuple[float, float]
Point3D = Tuple[float, float, float]


@dataclass(frozen=True)
class CoordinateCalibrator:
    """
    Maps pixel coordinates on an imported blueprint/CAD image to real-world
    coordinates (metres, matching the geofence's coordinate system).

    Two reference points (a pixel location whose real-world position is
    known) fully determine rotation, uniform scale, and translation, via
    z' = a*z + b with z, z', a, b as complex numbers.
    """

    a: complex
    b: complex

    @classmethod
    def from_reference_points(
        cls,
        pixel_ref_1: Point,
        world_ref_1: Point,
        pixel_ref_2: Point,
        world_ref_2: Point,
    ) -> "CoordinateCalibrator":
        z1, z2 = complex(*pixel_ref_1), complex(*pixel_ref_2)
        w1, w2 = complex(*world_ref_1), complex(*world_ref_2)

        if z1 == z2:
            raise ValueError("Reference pixel points must be distinct.")

        a = (w2 - w1) / (z2 - z1)
        b = w1 - a * z1
        return cls(a=a, b=b)

    def pixel_to_world(self, pixel_point: Point) -> Point:
        z = complex(*pixel_point)
        w = self.a * z + self.b
        return (w.real, w.imag)

    def scale(self) -> float:
        """Uniform scale factor recovered from |a|."""
        return abs(self.a)

    def rotation_degrees(self) -> float:
        """Rotation applied by the transform, recovered from arg(a)."""
        return math.degrees(cmath.phase(self.a))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "a_re": self.a.real, "a_im": self.a.imag,
            "b_re": self.b.real, "b_im": self.b.imag,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CoordinateCalibrator":
        return cls(a=complex(d["a_re"], d["a_im"]), b=complex(d["b_re"], d["b_im"]))


@dataclass
class ZoneHierarchy:
    """
    Every ZoneProfile for a facility, across any number of floors,
    pre-sorted by ascending area so the most specific nested zone resolves
    first (Server Room before Escort Required before Public Lobby).

    Operates on the real ZoneProfile from models.py, not a parallel type —
    add any zone you'd otherwise hand to GeofenceSystem/BuildingLayout,
    just with floor_id/z_min/z_max/risk_level populated for the 3D cases.
    """

    zones: List[ZoneProfile] = field(default_factory=list)

    def add_zone(self, zone: ZoneProfile) -> None:
        self.zones.append(zone)
        self.zones.sort(key=lambda z: z.area())

    def resolve(self, point3d: Point3D) -> Optional[ZoneProfile]:
        """Smallest-area zone containing the point, or None if outside every zone."""
        for zone in self.zones:
            if zone.contains_3d(point3d):
                return zone
        return None

    def resolve_all(self, point3d: Point3D) -> List[ZoneProfile]:
        """Every zone containing the point, smallest to largest (audit/debug)."""
        return [z for z in self.zones if z.contains_3d(point3d)]

    def to_json(self) -> str:
        return json.dumps(
            [
                {
                    "zone_name": z.zone_name,
                    "zone_type": z.zone_type,
                    "risk_level": z.risk_level,
                    "floor_id": z.floor_id,
                    "z_min": z.z_min,
                    "z_max": z.z_max,
                    "area_sqm": round(z.area(), 3),
                    "vertices": z.vertices,
                }
                for z in self.zones
            ],
            indent=2,
        )


if __name__ == "__main__":
    # Demo: a lobby with a nested, prohibited server room, and an executive
    # suite directly above the server room on floor 2 — the exact "guest in
    # the lobby vs. suite above it" case from the roadmap.

    calibrator = CoordinateCalibrator.from_reference_points(
        pixel_ref_1=(50, 50), world_ref_1=(0.0, 0.0),
        pixel_ref_2=(350, 50), world_ref_2=(30.0, 0.0),
    )
    print(
        f"Calibration: scale={calibrator.scale():.3f}, "
        f"rotation={calibrator.rotation_degrees():.2f} deg"
    )
    print(f"  pixel (200, 50) -> world {calibrator.pixel_to_world((200, 50))}\n")

    lobby = ZoneProfile(
        zone_name="Z-LOBBY", zone_type="lobby", center=(15.0, 10.0),
        vertices=[(0, 0), (30, 0), (30, 20), (0, 20)], risk_level="public",
        floor_id="1", z_min=0.0, z_max=3.0,
    )
    server_room = ZoneProfile(
        zone_name="Z-SERVER", zone_type="server_room", center=(24.0, 8.5),
        vertices=[(20, 5), (28, 5), (28, 12), (20, 12)], risk_level="prohibited",
        floor_id="1", z_min=0.0, z_max=3.0,
    )
    exec_suite_above = ZoneProfile(
        zone_name="Z-EXEC-2F", zone_type="executive_suite", center=(24.0, 8.5),
        vertices=[(20, 5), (28, 5), (28, 12), (20, 12)], risk_level="escort_required",
        floor_id="2", z_min=3.0, z_max=6.0,
    )

    hierarchy = ZoneHierarchy()
    for z in (lobby, server_room, exec_suite_above):
        hierarchy.add_zone(z)

    test_points: Dict[str, Point3D] = {
        "visitor in open lobby": (5.0, 5.0, 1.5),
        "visitor inside server room": (24.0, 8.0, 1.5),
        "same x/y, one floor up (exec suite)": (24.0, 8.0, 4.5),
        "outside the building entirely": (-5.0, -5.0, 1.5),
    }

    print("Zone resolution:")
    for label, pt in test_points.items():
        zone = hierarchy.resolve(pt)
        result = (
            f"{zone.zone_name} ({zone.risk_level}, floor {zone.floor_id})"
            if zone else "OUTSIDE ALL ZONES"
        )
        print(f"  {label:40s} {pt} -> {result}")

    print("\nZone manifest (JSON):")
    print(hierarchy.to_json())
