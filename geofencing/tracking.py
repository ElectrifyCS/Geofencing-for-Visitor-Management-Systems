"""
tracking.py — Real-time policy enforcement on a stream of filtered
positions: escort tethering, dwell time monitoring, directional vectors.

All three consume PositionSample objects -- whatever your Kalman filter
(kalman.py) or multilateration.py is already producing per tag, per
update. Nothing here cares where the position came from.

Math foundation (IB AA HL tie-ins):
  - TetherMonitor: vector magnitude / Euclidean distance between two
    position vectors, with a confidence-aware threshold built from each
    tag's own position uncertainty -- the same statistical-baselining
    pattern as the rest of this project, applied to a distance instead
    of a raw position.
  - DwellMonitor: zone dwell times modelled as an exponential
    distribution (natural for a Poisson-process departure model);
    anomalies flagged at mean + k*sigma, the same z-score pattern used
    for position anomaly scoring elsewhere in the project.
  - Directional vectors: velocity as a finite-difference derivative of
    position (rate of change); heading via atan2 (inverse trig); "is this
    person walking toward that door" via the dot product angle-between-
    vectors formula, cos(theta) = (v . d) / (|v||d|).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

Point3D = Tuple[float, float, float]

FEET_TO_METRES = 0.3048


@dataclass(frozen=True)
class PositionSample:
    entity_id: str
    timestamp_s: float          # seconds, monotonic
    position: Point3D           # filtered (x, y, z), metres
    sigma_m: float = 0.3        # 1-sigma position uncertainty from the filter


# ---------------------------------------------------------------------------
# Escort tethering
# ---------------------------------------------------------------------------

@dataclass
class TetherAlert:
    visitor_id: str
    host_id: str
    distance_m: float
    threshold_m: float
    timestamp_s: float


class TetherMonitor:
    """
    Digitally tethers a visitor tag to a host tag. Alerts on "host
    separation" once the visitors' estimated distance apart, discounted
    by combined position uncertainty, exceeds the configured threshold --
    so two tags with noisy fixes sitting right next to each other don't
    false-fire just because their error ellipses happen to point apart.
    """

    def __init__(self, max_distance_ft: float = 20.0, confidence_k: float = 2.0):
        self.max_distance_m = max_distance_ft * FEET_TO_METRES
        self.confidence_k = confidence_k  # ~2 sigma ~ 95% one-tailed confidence

    def check(self, visitor: PositionSample, host: PositionSample) -> Optional[TetherAlert]:
        distance = math.dist(visitor.position, host.position)
        combined_sigma = math.sqrt(visitor.sigma_m**2 + host.sigma_m**2)

        # Only alert once we're statistically confident the *true*
        # separation exceeds the threshold, not just the point estimate.
        confident_lower_bound = distance - self.confidence_k * combined_sigma

        if confident_lower_bound > self.max_distance_m:
            return TetherAlert(
                visitor_id=visitor.entity_id,
                host_id=host.entity_id,
                distance_m=distance,
                threshold_m=self.max_distance_m,
                timestamp_s=visitor.timestamp_s,
            )
        return None


# ---------------------------------------------------------------------------
# Dwell time monitoring
# ---------------------------------------------------------------------------

@dataclass
class DwellBaseline:
    """Historical dwell-time baseline for a zone type, in seconds."""
    mean_s: float
    std_s: float


@dataclass
class DwellAlert:
    entity_id: str
    zone_id: str
    dwell_s: float
    baseline_mean_s: float
    timestamp_s: float


class DwellMonitor:
    """
    Tracks how long each entity has continuously been in each zone, and
    flags when a dwell exceeds mean + k*sigma for that zone's baseline --
    same anomaly-scoring pattern the project already uses, applied to a
    duration instead of a position.
    """

    def __init__(self, baselines: Dict[str, DwellBaseline], confidence_k: float = 2.5):
        self.baselines = baselines
        self.confidence_k = confidence_k
        self._entry_times: Dict[Tuple[str, str], float] = {}  # (entity_id, zone_id) -> entry ts
        self._alerted: set = set()  # avoid re-alerting every tick while still over threshold

    def on_zone_enter(self, entity_id: str, zone_id: str, timestamp_s: float) -> None:
        self._entry_times[(entity_id, zone_id)] = timestamp_s

    def on_zone_exit(self, entity_id: str, zone_id: str) -> None:
        self._entry_times.pop((entity_id, zone_id), None)
        self._alerted.discard((entity_id, zone_id))

    def check(self, entity_id: str, zone_id: str, zone_type: str, timestamp_s: float) -> Optional[DwellAlert]:
        key = (entity_id, zone_id)
        entry_time = self._entry_times.get(key)
        baseline = self.baselines.get(zone_type)
        if entry_time is None or baseline is None or key in self._alerted:
            return None

        dwell = timestamp_s - entry_time
        threshold = baseline.mean_s + self.confidence_k * baseline.std_s

        if dwell > threshold:
            self._alerted.add(key)
            return DwellAlert(
                entity_id=entity_id, zone_id=zone_id, dwell_s=dwell,
                baseline_mean_s=baseline.mean_s, timestamp_s=timestamp_s,
            )
        return None


# ---------------------------------------------------------------------------
# Directional vectors
# ---------------------------------------------------------------------------

@dataclass
class Heading:
    velocity: Tuple[float, float]   # (vx, vy), m/s -- horizontal only
    speed_mps: float
    bearing_deg: float              # 0-360, 0 = +x axis, matches atan2 convention


def compute_heading(prev: PositionSample, curr: PositionSample) -> Optional[Heading]:
    """Finite-difference velocity between two samples, converted to speed + bearing."""
    dt = curr.timestamp_s - prev.timestamp_s
    if dt <= 0:
        return None

    vx = (curr.position[0] - prev.position[0]) / dt
    vy = (curr.position[1] - prev.position[1]) / dt
    speed = math.hypot(vx, vy)
    bearing = math.degrees(math.atan2(vy, vx)) % 360.0

    return Heading(velocity=(vx, vy), speed_mps=speed, bearing_deg=bearing)


def intent_angle_deg(heading: Heading, target_xy: Tuple[float, float], from_xy: Tuple[float, float]) -> Optional[float]:
    """
    Angle between the direction someone is walking and the direction
    toward a target point (e.g. a restricted door), via the dot product:
    cos(theta) = (v . d) / (|v||d|). A small angle means they're walking
    straight at it -- this is the actual "intercept before breach" signal.
    Returns None if not moving fast enough to have a meaningful heading.
    """
    if heading.speed_mps < 0.1:  # effectively stationary; heading is noise
        return None

    dx = target_xy[0] - from_xy[0]
    dy = target_xy[1] - from_xy[1]
    target_dist = math.hypot(dx, dy)
    if target_dist < 1e-6:
        return 0.0

    vx, vy = heading.velocity
    dot = vx * dx + vy * dy
    cos_theta = dot / (heading.speed_mps * target_dist)
    cos_theta = max(-1.0, min(1.0, cos_theta))  # clamp for float rounding
    return math.degrees(math.acos(cos_theta))


if __name__ == "__main__":
    # --- Tethering demo: host stands still, visitor walks away ---
    print("=== Escort tethering ===")
    tether = TetherMonitor(max_distance_ft=20.0, confidence_k=2.0)
    host = PositionSample("HOST-1", 0.0, (10.0, 10.0, 1.5), sigma_m=0.2)

    for step_m in (1.0, 3.0, 5.0, 6.0, 6.5, 7.5):
        visitor = PositionSample("VIS-1", 0.0, (10.0 + step_m, 10.0, 1.5), sigma_m=0.2)
        alert = tether.check(visitor, host)
        status = f"ALERT (dist {alert.distance_m:.2f}m > {alert.threshold_m:.2f}m)" if alert else "ok"
        print(f"  visitor {step_m:.1f}m away -> {status}")

    # --- Dwell demo: stairwell baseline ~30s, visitor lingers for 4 minutes ---
    print("\n=== Dwell time monitoring ===")
    dwell = DwellMonitor(baselines={"stairwell": DwellBaseline(mean_s=30.0, std_s=15.0)})
    dwell.on_zone_enter("VIS-2", "Z-STAIR-1", timestamp_s=0.0)
    for elapsed in (20.0, 45.0, 90.0, 240.0):
        alert = dwell.check("VIS-2", "Z-STAIR-1", zone_type="stairwell", timestamp_s=elapsed)
        status = f"ALERT (dwell {alert.dwell_s:.0f}s > baseline {alert.baseline_mean_s:.0f}s)" if alert else "ok"
        print(f"  {elapsed:.0f}s elapsed -> {status}")

    # --- Directional vector demo: visitor walking toward a restricted door ---
    print("\n=== Directional vectors ===")
    restricted_door = (30.0, 10.0)
    p1 = PositionSample("VIS-3", 0.0, (10.0, 10.0, 1.5))
    p2 = PositionSample("VIS-3", 2.0, (11.6, 10.0, 1.5))  # walked straight toward the door
    heading = compute_heading(p1, p2)
    angle = intent_angle_deg(heading, restricted_door, from_xy=(p2.position[0], p2.position[1]))
    print(f"  speed={heading.speed_mps:.2f} m/s, bearing={heading.bearing_deg:.1f} deg, "
          f"angle to restricted door={angle:.1f} deg -> "
          f"{'APPROACHING' if angle < 20 else 'not on intercept course'}")

    p3 = PositionSample("VIS-3", 4.0, (11.6, 14.0, 1.5))  # then turns and walks away
    heading2 = compute_heading(p2, p3)
    angle2 = intent_angle_deg(heading2, restricted_door, from_xy=(p3.position[0], p3.position[1]))
    print(f"  speed={heading2.speed_mps:.2f} m/s, bearing={heading2.bearing_deg:.1f} deg, "
          f"angle to restricted door={angle2:.1f} deg -> "
          f"{'APPROACHING' if angle2 < 20 else 'not on intercept course'}")
