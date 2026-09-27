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
    z' = a*z + b. With exactly 2 reference point pairs this has a unique
    exact solution; with 3 or more, it becomes an overdetermined linear
    system solved by least squares (the same "more equations than
    unknowns, fit the best line/transform through the noise" idea as
    multilateration.py's anchor solve), so real survey error at any one
    reference point gets averaged out instead of baked directly into the
    transform. Found as a real gap: a 2-point exact fit has no way to
    know which of its two points was the one a tape measure got slightly
    wrong, and that error compounds with distance from the two points
    ("wall-bleeding").
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
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .models import ZoneProfile

Point = Tuple[float, float]
Point3D = Tuple[float, float, float]


@dataclass(frozen=True)
class CoordinateCalibrator:
    """
    Maps pixel coordinates on an imported blueprint/CAD image to real-world
    coordinates (metres, matching the geofence's coordinate system).

    z' = a*z + b, with z, z', a, b as complex numbers, fitted from 2 or
    more (pixel, world) reference point pairs. Exactly 2 distinct points
    gives the same exact-fit transform this always computed; 3 or more
    lets the fit average out normal survey/measurement error rather than
    have it define the transform outright — see fit_residuals_m below to
    check how well the fit actually agrees with each reference point.
    """

    a: complex
    b: complex
    # RMS residual (metres) between each reference point's known world
    # position and where the fitted transform actually places it. Zero
    # for an exact 2-point fit; nonzero, and meaningful, for 3+ points —
    # a fit residual approaching your real survey error is expected and
    # healthy; one much larger than that means a reference point is
    # probably mismeasured, not that least squares failed.
    fit_residual_rms_m: float = 0.0

    @classmethod
    def from_reference_points(
        cls,
        reference_pairs: Sequence[Tuple[Point, Point]],
    ) -> "CoordinateCalibrator":
        """
        reference_pairs: [(pixel_point, world_point), ...], at least 2,
        with at least 2 distinct pixel points among them. 2 points fully
        determine rotation + uniform scale + translation exactly (as
        before); 3+ turns this into a least-squares fit instead of an
        exact one, which is the recommended way to calibrate against a
        real blueprint and a real on-site survey, not just the minimum
        that technically works.
        """
        if len(reference_pairs) < 2:
            raise ValueError("Need at least 2 reference point pairs to fit rotation, scale, and translation.")

        pixel_points = [p for p, _ in reference_pairs]
        world_points = [w for _, w in reference_pairs]
        if len(set(pixel_points)) < 2:
            raise ValueError("Reference pixel points must include at least 2 distinct points.")

        z = np.array([complex(*p) for p in pixel_points], dtype=complex)
        w = np.array([complex(*p) for p in world_points], dtype=complex)

        # [z_i, 1] @ [a, b]^T = w_i for every reference pair -- linear in
        # (a, b), solved directly in the complex domain (numpy's lstsq
        # supports complex dtypes natively) rather than splitting into a
        # 4-unknown real system, keeping the same "one complex
        # multiply-and-add" framing the rest of this module already uses.
        design = np.column_stack([z, np.ones(len(z), dtype=complex)])
        solution, *_ = np.linalg.lstsq(design, w, rcond=None)
        a, b = complex(solution[0]), complex(solution[1])

        predicted = a * z + b
        residuals = np.abs(predicted - w)
        rms = float(np.sqrt(np.mean(residuals ** 2)))

        return cls(a=a, b=b, fit_residual_rms_m=rms)

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
            "fit_residual_rms_m": self.fit_residual_rms_m,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CoordinateCalibrator":
        return cls(
            a=complex(d["a_re"], d["a_im"]),
            b=complex(d["b_re"], d["b_im"]),
            fit_residual_rms_m=d.get("fit_residual_rms_m", 0.0),
        )


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

    print("=== Coordinate calibration: 2-point exact fit vs. 3-point least squares ===\n")

    # True transform: 30 degree rotation, scale 0.1 (10 pixels = 1 metre),
    # origin offset -- unknown to the calibrator, used here only to
    # generate synthetic reference points and to check the fit afterward.
    true_a = 0.1 * cmath.exp(1j * math.radians(30))
    true_b = complex(-2.0, 1.5)

    def true_pixel_to_world(pixel_point: Point) -> Point:
        w = true_a * complex(*pixel_point) + true_b
        return (w.real, w.imag)

    # Three blueprint reference points a real on-site survey might use --
    # e.g. three marked corners. World coordinates come from the true
    # transform, THEN one gets a realistic tape-measure/rounding error
    # (15cm off in one axis) to simulate exactly the kind of small survey
    # mistake that's unavoidable in real fieldwork, not a gross blunder.
    pixel_pts = [(50.0, 50.0), (350.0, 50.0), (200.0, 300.0)]
    true_world_pts = [true_pixel_to_world(p) for p in pixel_pts]

    surveyed_world_pts = list(true_world_pts)
    surveyed_world_pts[2] = (surveyed_world_pts[2][0] + 0.15, surveyed_world_pts[2][1])
    print(f"Reference point 3's surveyed world position is off by 15cm from truth")
    print(f"  (true: {tuple(round(v, 3) for v in true_world_pts[2])}, "
          f"surveyed: {tuple(round(v, 3) for v in surveyed_world_pts[2])})\n")

    # A point NOT used as a reference point -- this is what actually
    # matters: does a downstream zone-containment check at some other
    # location on the blueprint get the right real-world position?
    test_pixel_point = (275.0, 175.0)
    true_test_world = true_pixel_to_world(test_pixel_point)

    # --- Old behavior: exact fit through 2 points, one of which (the ---
    # --- surveyed 3rd point isn't even used here) happens fine -- so ---
    # --- pick the 2-point case that WOULD be used in practice: point ---
    # --- 1 and the mismeasured point 3.                              ---
    two_point_calibrator = CoordinateCalibrator.from_reference_points([
        (pixel_pts[0], surveyed_world_pts[0]),
        (pixel_pts[2], surveyed_world_pts[2]),
    ])
    two_point_estimate = two_point_calibrator.pixel_to_world(test_pixel_point)
    two_point_error = math.dist(true_test_world, two_point_estimate)

    # --- New behavior: least squares across all 3 points, including ---
    # --- the same mismeasured one -- it still contributes, but can't ---
    # --- single-handedly define the transform.                       ---
    three_point_calibrator = CoordinateCalibrator.from_reference_points(
        list(zip(pixel_pts, surveyed_world_pts))
    )
    three_point_estimate = three_point_calibrator.pixel_to_world(test_pixel_point)
    three_point_error = math.dist(true_test_world, three_point_estimate)

    print(f"Test point (not a reference point): pixel {test_pixel_point}, true world {tuple(round(v, 3) for v in true_test_world)}")
    print(f"  2-point exact fit (using the mismeasured point):  "
          f"estimate {tuple(round(v, 3) for v in two_point_estimate)}, error {two_point_error * 100:.1f} cm")
    print(f"  3-point least-squares fit (same mismeasured point, "
          f"now outvoted by 2 good ones): estimate {tuple(round(v, 3) for v in three_point_estimate)}, "
          f"error {three_point_error * 100:.1f} cm")
    print(f"  Least-squares fit residual RMS: {three_point_calibrator.fit_residual_rms_m * 100:.1f} cm "
          f"(nonzero -- it didn't blindly trust the bad point, it outvoted it)\n")

    calibrator = three_point_calibrator
    print(
        f"Calibration in use: scale={calibrator.scale():.3f}, "
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
