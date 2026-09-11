"""
Data models: risk levels, positions, visitors, polygonal zones,
robust behaviour profiles, and allowed-transition graphs.

Math flavour:
  - Vector geometry for point-in-polygon and distances
  - Robust statistics (median, MAD)
  - Logarithmic convergence tracking
  - Exponential forgetting (geometric sequences)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Set
from enum import Enum
from datetime import datetime
import numpy as np

from .kalman import compute_log_convergence_factor


class VisitorRisk(Enum):
    TRUSTED = "TRUSTED"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class Position:
    t: float
    x: float
    y: float
    gps_accuracy: float = 5.0


@dataclass
class Visitor:
    visitor_id: str
    name: str
    badge_tag: str
    entry_time: datetime
    expected_zones: List[Tuple[float, float]] = field(default_factory=list)
    security_level: str = "standard"
    host_department: str = "General"
    purpose: str = "Meeting"
    exit_time: Optional[datetime] = None
    allowed_areas: List[str] = field(default_factory=lambda: ["lobby", "conference"])
    behavior_profile: Optional["UserBehaviorProfile"] = None


@dataclass
class VisitorLocationReport:
    visitor: Visitor
    timestamp: datetime
    anomaly_score: float
    risk_level: VisitorRisk
    is_spoofed: bool
    violation_details: str
    flagged_events: List[str] = field(default_factory=list)
    location_accuracy: float = 0.0
    # New fields for uncertainty-aware reporting
    position_uncertainty: float = 0.0
    badge_risk_contribution: float = 0.0

    def to_dict(self) -> dict:
        return {
            "visitor_id": self.visitor.visitor_id,
            "visitor_name": self.visitor.name,
            "badge_tag": self.visitor.badge_tag,
            "host_department": self.visitor.host_department,
            "purpose": self.visitor.purpose,
            "security_level": self.visitor.security_level,
            "timestamp": self.timestamp.isoformat(),
            "anomaly_score": round(self.anomaly_score, 3),
            "risk_level": self.risk_level.value,
            "is_spoofed": self.is_spoofed,
            "violation_details": self.violation_details,
            "flagged_events": self.flagged_events,
            "location_accuracy_m": round(self.location_accuracy, 1),
            "position_uncertainty_m": round(self.position_uncertainty, 1),
            "badge_risk_contribution": round(self.badge_risk_contribution, 3),
        }


@dataclass
class DetectionResult:
    velocity_violations: List[Tuple[int, float]]
    acceleration_violations: List[Tuple[int, float]]
    path_violation: bool
    geofence_violations: float
    boundary_teleportations: List[int]
    anomaly_score: float
    is_spoofed: bool
    # Uncertainty & badge fusion
    position_uncertainty: float = 0.0
    badge_risk: float = 0.0
    transition_violation: bool = False


# ---------------------------------------------------------------------------
# Polygonal zones
# ---------------------------------------------------------------------------

@dataclass
class ZoneProfile:
    """
    Zone definition – now supports both circular and polygonal geometry.

    For polygons the vertices list is used; centre/radius remain for
    backwards compatibility and for quick bounding-box checks.
    """
    zone_name: str
    center: Tuple[float, float]
    radius: float = 100.0
    vertices: Optional[List[Tuple[float, float]]] = None  # if set → polygon
    v_max: float = 2.5          # default walking
    a_max: float = 2.0
    epsilon_v: float = 0.3
    epsilon_a: float = 0.3
    zone_type: str = "general"  # lobby, corridor, parking, stairwell, ...

    def is_polygonal(self) -> bool:
        return self.vertices is not None and len(self.vertices) >= 3

    def contains(self, point: np.ndarray) -> bool:
        """Point-in-zone test (ray-casting for polygons, distance for circles)."""
        if self.is_polygonal():
            return _point_in_polygon(point, self.vertices)
        return float(np.linalg.norm(point - np.asarray(self.center))) <= self.radius


def _point_in_polygon(point: np.ndarray, vertices: List[Tuple[float, float]]) -> bool:
    """
    Ray-casting algorithm (even-odd rule).
    Classic computational-geometry vector technique.
    """
    x, y = float(point[0]), float(point[1])
    n = len(vertices)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = vertices[i]
        xj, yj = vertices[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


# ---------------------------------------------------------------------------
# Allowed-transition graph
# ---------------------------------------------------------------------------

@dataclass
class TransitionGraph:
    """
    Directed graph of permitted zone-to-zone movements.
    Edges may carry a maximum plausible transit time (seconds).
    """
    edges: Dict[str, Set[str]] = field(default_factory=dict)
    max_transit_time: Dict[Tuple[str, str], float] = field(default_factory=dict)

    def add_edge(self, from_zone: str, to_zone: str, max_time: float = 300.0):
        if from_zone not in self.edges:
            self.edges[from_zone] = set()
        self.edges[from_zone].add(to_zone)
        self.max_transit_time[(from_zone, to_zone)] = max_time

    def is_allowed(self, from_zone: str, to_zone: str) -> bool:
        if from_zone == to_zone:
            return True
        return to_zone in self.edges.get(from_zone, set())

    def max_time(self, from_zone: str, to_zone: str) -> float:
        return self.max_transit_time.get((from_zone, to_zone), 600.0)


# ---------------------------------------------------------------------------
# Robust behaviour profiles
# ---------------------------------------------------------------------------

@dataclass
class UserBehaviorProfile:
    """
    Robust, forgetting-aware behaviour profile.

    Statistics are maintained with exponential forgetting (geometric sequence)
    and protected by median / MAD so a single spoofed session cannot dominate.
    """
    visitor_id: str
    visits_count: int = 0

    # Robust location (median of recent velocities / accelerations)
    median_velocity: float = 1.4
    mad_velocity: float = 0.4
    median_acceleration: float = 0.6
    mad_acceleration: float = 0.3

    # Soft max (slowly adapting)
    soft_max_velocity: float = 2.5
    soft_max_acceleration: float = 2.0

    common_zones: List[str] = field(default_factory=list)
    typical_visit_duration: float = 3600.0
    deviation_tolerance: float = 1.8          # multiplier on MAD-based limits
    last_updated: datetime = field(default_factory=datetime.now)

    # Convergence tracking
    measurements_recorded: int = 0
    convergence_state: Dict[str, dict] = field(default_factory=dict)

    # Exponential forgetting factor (0 < λ ≤ 1).  λ close to 1 = long memory.
    forgetting: float = 0.92

    # Raw sample buffers (kept short for median/MAD)
    _vel_buffer: List[float] = field(default_factory=list, repr=False)
    _acc_buffer: List[float] = field(default_factory=list, repr=False)
    _buffer_max: int = 40

    def get_adjusted_v_max(self) -> float:
        """Robust upper velocity = median + k·MAD, then soft-max clamp."""
        robust = self.median_velocity + self.deviation_tolerance * self.mad_velocity
        return max(robust, self.soft_max_velocity * 0.7)

    def get_adjusted_a_max(self) -> float:
        robust = self.median_acceleration + self.deviation_tolerance * self.mad_acceleration
        return max(robust, self.soft_max_acceleration * 0.7)

    def get_convergence_factor(self, zone_name: str = "global", baseline: int = 15) -> float:
        if zone_name not in self.convergence_state:
            self.convergence_state[zone_name] = {"measurements": 0}
        n = self.convergence_state[zone_name]["measurements"]
        return compute_log_convergence_factor(n, baseline)

    def record_measurement(self, zone_name: str = "global"):
        if zone_name not in self.convergence_state:
            self.convergence_state[zone_name] = {"measurements": 0}
        self.convergence_state[zone_name]["measurements"] += 1
        self.measurements_recorded += 1

    def update_from_observations(
        self,
        velocities: List[float],
        accelerations: List[float],
        zone_names: List[str],
        trusted: bool = True,
    ):
        """
        Robust online update.
        Only fully trusted observations are allowed to shrink the MAD;
        untrusted ones may still raise the soft-max (safety).
        """
        if not velocities and not accelerations:
            return

        # Append to short buffers
        if velocities:
            self._vel_buffer.extend(velocities)
            self._vel_buffer = self._vel_buffer[-self._buffer_max:]
        if accelerations:
            self._acc_buffer.extend(accelerations)
            self._acc_buffer = self._acc_buffer[-self._buffer_max:]

        # Median & MAD
        if len(self._vel_buffer) >= 3:
            med = float(np.median(self._vel_buffer))
            mad = float(np.median(np.abs(np.array(self._vel_buffer) - med))) + 1e-3
            # Exponential blend
            self.median_velocity = self.forgetting * self.median_velocity + (1 - self.forgetting) * med
            self.mad_velocity = self.forgetting * self.mad_velocity + (1 - self.forgetting) * mad
            if trusted:
                self.soft_max_velocity = max(self.soft_max_velocity * 0.995, med + 2.5 * mad)
            else:
                # Untrusted can only raise the ceiling, never lower it
                self.soft_max_velocity = max(self.soft_max_velocity, med + 3.0 * mad)

        if len(self._acc_buffer) >= 3:
            med = float(np.median(self._acc_buffer))
            mad = float(np.median(np.abs(np.array(self._acc_buffer) - med))) + 1e-3
            self.median_acceleration = self.forgetting * self.median_acceleration + (1 - self.forgetting) * med
            self.mad_acceleration = self.forgetting * self.mad_acceleration + (1 - self.forgetting) * mad
            if trusted:
                self.soft_max_acceleration = max(self.soft_max_acceleration * 0.995, med + 2.5 * mad)
            else:
                self.soft_max_acceleration = max(self.soft_max_acceleration, med + 3.0 * mad)

        for z in zone_names:
            if z not in self.common_zones:
                self.common_zones.append(z)

        self.visits_count += 1
        self.last_updated = datetime.now()


@dataclass
class BuildingLayout:
    building_name: str
    zones: List[ZoneProfile] = field(default_factory=list)
    visitor_profiles: Dict[str, UserBehaviorProfile] = field(default_factory=dict)
    transition_graph: TransitionGraph = field(default_factory=TransitionGraph)

    def get_zone(self, zone_name: str) -> Optional[ZoneProfile]:
        for z in self.zones:
            if z.zone_name == zone_name:
                return z
        return None

    def get_visitor_profile(self, visitor_id: str) -> UserBehaviorProfile:
        if visitor_id not in self.visitor_profiles:
            self.visitor_profiles[visitor_id] = UserBehaviorProfile(visitor_id=visitor_id)
        return self.visitor_profiles[visitor_id]

    def update_visitor_profile(
        self,
        visitor_id: str,
        velocities: List[float],
        accelerations: List[float],
        zone_names: List[str],
        trusted: bool = True,
    ):
        profile = self.get_visitor_profile(visitor_id)
        profile.update_from_observations(velocities, accelerations, zone_names, trusted=trusted)
