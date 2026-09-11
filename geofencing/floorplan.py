"""
floorplan.py — Dynamic floor plan mapping & multi-tiered zone containment.

The foundation layer for Zone Mapping & Geofence Management: turns a blueprint
image + hand-drawn polygons into real-world zone geometry, and resolves any
tag's (x, y, z) position to the correct — and most specific — zone.

Math foundation (IB AA HL tie-ins, matching the rest of this project):
  - CoordinateCalibrator: complex numbers in modulus-argument form. A
    similarity transform (rotation + uniform scale + translation) between
    two coordinate systems is exactly one complex multiply-and-add,
    z' = a*z + b, solved from two reference point pairs.
  - Polygon.area(): the shoelace formula, a direct sigma-notation sum over
    vertex pairs — reused as the tie-breaker for nested zones.
  - Polygon.contains_point(): ray-casting point-in-polygon, coordinate
    geometry / vectors.
  - ZoneHierarchy: containment resolved by ascending polygon area, since a
    restricted zone is by construction smaller than the zone it nests
    inside (Server Room < Escort Required < Public Lobby).

Pure stdlib — no external dependencies, so it drops into the existing
package without touching requirements.
"""

from __future__ import annotations

import cmath
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

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
class Polygon:
    """A single geofence zone boundary: an ordered list of (x, y) vertices, in metres."""

    vertices: List[Point]
    zone_id: str
    label: str = ""
    risk_level: str = "public"  # "public" | "escort_required" | "prohibited"
    floor_id: str = "1"
    z_min: float = 0.0
    z_max: float = 3.0  # default single-storey slab height, metres

    def area(self) -> float:
        """Shoelace formula: A = 1/2 * |sum(x_i * y_(i+1) - x_(i+1) * y_i)|."""
        n = len(self.vertices)
        if n < 3:
            return 0.0
        total = 0.0
        for i in range(n):
            x1, y1 = self.vertices[i]
            x2, y2 = self.vertices[(i + 1) % n]
            total += x1 * y2 - x2 * y1
        return abs(total) / 2.0

    def contains_point(self, point: Point) -> bool:
        """Ray-casting point-in-polygon: odd number of edge crossings = inside."""
        x, y = point
        n = len(self.vertices)
        inside = False
        x1, y1 = self.vertices[-1]
        for i in range(n):
            x2, y2 = self.vertices[i]
            if (y1 > y) != (y2 > y):
                x_intersect = (y - y1) * (x2 - x1) / (y2 - y1) + x1
                if x < x_intersect:
                    inside = not inside
            x1, y1 = x2, y2
        return inside

    def contains_point_3d(self, point3d: Point3D) -> bool:
        """3D containment: the 2D polygon extruded into a vertical prism between z_min/z_max."""
        x, y, z = point3d
        return self.z_min <= z <= self.z_max and self.contains_point((x, y))


@dataclass
class ZoneHierarchy:
    """
    Every zone polygon for a facility, across any number of floors,
    pre-sorted by ascending area so the most specific nested zone resolves
    first (Server Room before Escort Required before Public Lobby).
    """

    zones: List[Polygon] = field(default_factory=list)

    def add_zone(self, polygon: Polygon) -> None:
        self.zones.append(polygon)
        self.zones.sort(key=lambda p: p.area())

    def resolve(self, point3d: Point3D) -> Optional[Polygon]:
        """Smallest-area zone containing the point, or None if outside every zone."""
        for zone in self.zones:
            if zone.contains_point_3d(point3d):
                return zone
        return None

    def resolve_all(self, point3d: Point3D) -> List[Polygon]:
        """Every zone containing the point, smallest to largest (audit/debug)."""
        return [z for z in self.zones if z.contains_point_3d(point3d)]

    def to_json(self) -> str:
        return json.dumps(
            [
                {
                    "zone_id": z.zone_id,
                    "label": z.label,
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

    lobby = Polygon(
        vertices=[(0, 0), (30, 0), (30, 20), (0, 20)],
        zone_id="Z-LOBBY", label="Public Lobby", risk_level="public",
        floor_id="1", z_min=0.0, z_max=3.0,
    )
    server_room = Polygon(
        vertices=[(20, 5), (28, 5), (28, 12), (20, 12)],
        zone_id="Z-SERVER", label="Server Room", risk_level="prohibited",
        floor_id="1", z_min=0.0, z_max=3.0,
    )
    exec_suite_above = Polygon(
        vertices=[(20, 5), (28, 5), (28, 12), (20, 12)],
        zone_id="Z-EXEC-2F", label="Executive Suite", risk_level="escort_required",
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
            f"{zone.zone_id} ({zone.risk_level}, floor {zone.floor_id})"
            if zone else "OUTSIDE ALL ZONES"
        )
        print(f"  {label:40s} {pt} -> {result}")

    print("\nZone manifest (JSON):")
    print(hierarchy.to_json())
