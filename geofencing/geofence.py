"""
Core detection engine – upgraded.

- 2-D constant-velocity Kalman → velocity & acceleration from filter state
- Uncertainty-aware (covariance-scaled) anomaly score
- Polygonal zones + transition-graph checks
- Badge risk fused into the final score
"""

from __future__ import annotations

from typing import List, Tuple, Optional
from datetime import datetime
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon as MplPolygon

from .kalman import KalmanFilter2DConstantVelocity, get_adaptive_thresholds, compute_log_convergence_factor
from .models import (
    Position, ZoneProfile, UserBehaviorProfile, DetectionResult,
    VisitorLocationReport, Visitor, VisitorRisk, TransitionGraph,
)


def smooth_and_derive(
    positions: List[Position],
    process_noise_std: float = 0.35,
    enable_log_convergence: bool = True,
) -> Tuple[List[Position], List[float], List[float], float]:
    """
    Run the 2-D CV Kalman over the trajectory.

    Returns
    -------
    smoothed_positions : List[Position]
    speeds             : list of ||v|| at each step after the first
    accelerations      : list of acceleration magnitudes
    mean_uncertainty   : average position uncertainty (metres)
    """
    if len(positions) < 2:
        return positions, [], [], 5.0

    kf = KalmanFilter2DConstantVelocity(
        process_noise_std=process_noise_std,
        measurement_noise_std=float(np.mean([p.gps_accuracy for p in positions])),
        enable_log_convergence=enable_log_convergence,
    )

    smoothed: List[Position] = []
    speeds: List[float] = []
    accelerations: List[float] = []
    uncertainties: List[float] = []

    prev_vel = None
    prev_t = None

    for i, pos in enumerate(positions):
        meas = np.array([pos.x, pos.y])
        p_smooth, vel, speed = kf.process(pos.t, meas, pos.gps_accuracy)
        smoothed.append(Position(t=pos.t, x=float(p_smooth[0]), y=float(p_smooth[1]),
                                 gps_accuracy=pos.gps_accuracy))
        uncertainties.append(kf.position_uncertainty())

        if i > 0 and prev_vel is not None and prev_t is not None:
            dt = max(pos.t - prev_t, 1e-3)
            speeds.append(speed)
            acc = kf.get_acceleration(vel, dt)
            accelerations.append(acc)
        prev_vel = vel
        prev_t = pos.t

    mean_unc = float(np.mean(uncertainties)) if uncertainties else 5.0
    return smoothed, speeds, accelerations, mean_unc


class GeofenceSystem:
    def __init__(
        self,
        center: Tuple[float, float],
        radius: float,
        delta_t: float = 60.0,
        zone_profile: Optional[ZoneProfile] = None,
        user_profile: Optional[UserBehaviorProfile] = None,
        enable_kalman_smoothing: bool = True,
        process_noise_std: float = 0.35,
        enable_log_convergence: bool = True,
        transition_graph: Optional[TransitionGraph] = None,
        previous_zone: Optional[str] = None,
    ):
        self.center = np.array(center, dtype=float)
        self.radius = float(radius)
        self.delta_t = float(delta_t)
        self.zone_profile = zone_profile
        self.user_profile = user_profile
        self.enable_kalman_smoothing = enable_kalman_smoothing
        self.process_noise_std = process_noise_std
        self.enable_log_convergence = enable_log_convergence
        self.transition_graph = transition_graph
        self.previous_zone = previous_zone

        self.gps_quality = "UNKNOWN"
        self.last_positions_raw: Optional[List[Position]] = None
        self.last_positions_smoothed: Optional[List[Position]] = None
        self.last_convergence_factor = 1.0
        self.last_position_uncertainty = 5.0

        # Physical limits
        if zone_profile:
            self.v_max = zone_profile.v_max
            self.a_max = zone_profile.a_max
            self.epsilon_v = zone_profile.epsilon_v
            self.epsilon_a = zone_profile.epsilon_a
            self.zone_type = zone_profile.zone_type
        else:
            self.v_max = 2.5
            self.a_max = 2.0
            self.epsilon_v = 0.3
            self.epsilon_a = 0.3
            self.zone_type = "general"

        if user_profile:
            self.v_max = user_profile.get_adjusted_v_max()
            self.a_max = user_profile.get_adjusted_a_max()

        # Base anomaly weights (will be adjusted by zone & uncertainty)
        self.weights = {
            "velocity": 0.40,
            "acceleration": 0.15,
            "path": 0.08,
            "geofence": 0.10,
            "boundary": 0.10,
            "badge": 0.12,          # fused badge risk
            "transition": 0.05,
        }
        self.theta = 0.35

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------
    def is_inside_geofence(self, pos: np.ndarray) -> bool:
        if self.zone_profile is not None:
            return self.zone_profile.contains(pos)
        return float(np.linalg.norm(pos - self.center)) <= self.radius

    def _temporal_decay(self, index: int, total: int, decay_factor: float = 0.75) -> float:
        recency = index / max(1, total - 1)
        return decay_factor + (1.0 - decay_factor) * recency

    def _normalize_score(self, violation_amount: float, max_value: float) -> float:
        if violation_amount <= 0:
            return 0.0
        if max_value <= 0:
            return 1.0
        return min(1.0, violation_amount / max_value)

    def _get_convergence_factor(self) -> float:
        if not self.enable_log_convergence or not self.user_profile:
            return 1.0
        zone_name = self.zone_profile.zone_name if self.zone_profile else "global"
        c = self.user_profile.get_convergence_factor(zone_name, baseline=15)
        self.last_convergence_factor = c
        self.user_profile.record_measurement(zone_name)
        return c

    def _get_zone_weights(self) -> dict:
        w = self.weights.copy()
        if self.zone_type == "stairwell":
            w["acceleration"] = 0.12
            w["velocity"] = 0.22
        elif self.zone_type == "wheelchair_accessible":
            w["velocity"] = 0.18
            w["acceleration"] = 0.12
        elif self.zone_type == "lobby":
            w["velocity"] = 0.28
            w["boundary"] = 0.12
        elif self.zone_type == "parking":
            w["velocity"] = 0.40
            w["acceleration"] = 0.22
        return w

    # ------------------------------------------------------------------
    # Core detection
    # ------------------------------------------------------------------
    def detect_spoofing(
        self,
        positions: List[Position],
        badge_risk: float = 0.0,
        previous_zone: Optional[str] = None,
    ) -> DetectionResult:
        n = len(positions)
        if n < 2:
            return DetectionResult([], [], False, 0.0, [], 0.0, False,
                                   position_uncertainty=5.0, badge_risk=badge_risk)

        self.last_positions_raw = positions
        prev_zone = previous_zone or self.previous_zone

        # --- Kalman smoothing + kinematic derivation ---
        if self.enable_kalman_smoothing:
            smoothed, speeds, accelerations, mean_unc = smooth_and_derive(
                positions,
                process_noise_std=self.process_noise_std,
                enable_log_convergence=self.enable_log_convergence,
            )
            self.last_positions_smoothed = smoothed
            self.last_position_uncertainty = mean_unc
            coords = np.array([[p.x, p.y] for p in smoothed], dtype=float)
        else:
            coords = np.array([[p.x, p.y] for p in positions], dtype=float)
            speeds, accelerations = [], []
            mean_unc = 5.0
            # fallback finite differences
            for i in range(n - 1):
                dt = max(positions[i + 1].t - positions[i].t, 1e-3)
                d = np.linalg.norm(coords[i + 1] - coords[i])
                speeds.append(d / dt)
            for i in range(1, n - 1):
                dt = max(positions[i].t - positions[i - 1].t, 1e-3)
                a = (coords[i + 1] - 2 * coords[i] + coords[i - 1]) / (dt ** 2)
                accelerations.append(float(np.linalg.norm(a)))

        # Adaptive thresholds (now also uncertainty-aware)
        avg_gps = float(np.mean([p.gps_accuracy for p in positions]))
        c = self._get_convergence_factor()
        adaptive = get_adaptive_thresholds(avg_gps, c, mean_unc)
        self.gps_quality = adaptive["quality"]
        adj_eps_v = self.epsilon_v * adaptive["epsilon_v_factor"]
        adj_eps_a = self.epsilon_a * adaptive["epsilon_a_factor"]

        # --- Motion violations (using filter-derived speeds/accels) ---
        velocity_violations = []
        acceleration_violations = []

        for i, v in enumerate(speeds):
            if v > self.v_max + adj_eps_v:
                tw = self._temporal_decay(i, len(speeds), 0.7)
                velocity_violations.append((i, v * tw))

        for i, a in enumerate(accelerations):
            if a > self.a_max + adj_eps_a:
                tw = self._temporal_decay(i, len(accelerations), 0.7)
                acceleration_violations.append((i, a * tw))

        # Path-efficiency (still secondary)
        D_actual = sum(float(np.linalg.norm(coords[i + 1] - coords[i])) for i in range(n - 1))
        D_min = float(np.linalg.norm(coords[-1] - coords[0]))
        path_violation = D_actual > D_min + max(1.0, 0.08 * D_min)

        # Geofence dwell & boundary jumps
        T_outside, T_total, boundary_teleportations = self._geofence_metrics(coords, positions)

        # Transition-graph check
        transition_violation = False
        if self.transition_graph and prev_zone and self.zone_profile:
            current_zone = self.zone_profile.zone_name
            if not self.transition_graph.is_allowed(prev_zone, current_zone):
                transition_violation = True

        # --- Normalised component scores ---
        S_v = 0.0
        if velocity_violations:
            max_v = max(v for _, v in velocity_violations)
            # Saturate aggressively for extreme jumps (teleportation)
            excess = max_v - self.v_max
            S_v = self._normalize_score(excess, max(self.v_max * 0.5, 2.0))

        S_a = 0.0
        if acceleration_violations:
            max_a = max(a for _, a in acceleration_violations)
            S_a = self._normalize_score(max_a - self.a_max, self.a_max)

        S_p = 1.0 if path_violation else 0.0
        S_g = min(1.0, T_outside / (T_total * 0.5)) if T_total > 0 else 0.0
        S_b = min(1.0, len(boundary_teleportations) / 3.0)
        S_badge = float(np.clip(badge_risk, 0.0, 1.0))
        S_trans = 1.0 if transition_violation else 0.0

        # Uncertainty-aware weighting: when the filter is uncertain,
        # we down-weight kinematic signals and up-weight badge / transition.
        unc_scale = 1.0 / (1.0 + mean_unc / 15.0)          # ∈ (0,1]
        w = self._get_zone_weights()
        w["velocity"] *= unc_scale
        w["acceleration"] *= unc_scale
        w["badge"] *= (2.0 - unc_scale)                    # more trust in badge when GPS is shaky
        # re-normalise
        total_w = sum(w.values())
        for k in w:
            w[k] /= total_w

        anomaly_score = (
            w["velocity"] * S_v +
            w["acceleration"] * S_a +
            w["path"] * S_p +
            w["geofence"] * S_g +
            w["boundary"] * S_b +
            w["badge"] * S_badge +
            w["transition"] * S_trans
        )
        is_spoofed = anomaly_score > self.theta

        return DetectionResult(
            velocity_violations=velocity_violations,
            acceleration_violations=acceleration_violations,
            path_violation=path_violation,
            geofence_violations=T_outside,
            boundary_teleportations=boundary_teleportations,
            anomaly_score=float(anomaly_score),
            is_spoofed=is_spoofed,
            position_uncertainty=mean_unc,
            badge_risk=S_badge,
            transition_violation=transition_violation,
        )

    def _geofence_metrics(
        self, coords: np.ndarray, positions: List[Position]
    ) -> Tuple[float, float, List[int]]:
        n = len(coords)
        T_inside = 0.0
        for i in range(n - 1):
            dt = max(positions[i + 1].t - positions[i].t, self.delta_t)
            midpoint = (coords[i] + coords[i + 1]) / 2.0
            if self.is_inside_geofence(midpoint):
                T_inside += dt
        T_total = sum(
            max(positions[i + 1].t - positions[i].t, self.delta_t) for i in range(n - 1)
        )
        T_outside = max(0.0, T_total - T_inside)

        boundary_teleportations = []
        for i in range(n - 1):
            inside_i = self.is_inside_geofence(coords[i])
            inside_ip1 = self.is_inside_geofence(coords[i + 1])
            if inside_i != inside_ip1:
                dt = max(positions[i + 1].t - positions[i].t, 1e-3)
                dist = float(np.linalg.norm(coords[i + 1] - coords[i]))
                if dist > self.v_max * dt * 1.5:          # generous but still physical
                    boundary_teleportations.append(i)
        return T_outside, T_total, boundary_teleportations

    # ------------------------------------------------------------------
    # Public verification API
    # ------------------------------------------------------------------
    def verify_visitor(
        self,
        visitor: Visitor,
        positions: List[Position],
        badge_risk: float = 0.0,
        previous_zone: Optional[str] = None,
    ) -> VisitorLocationReport:
        detection = self.detect_spoofing(positions, badge_risk=badge_risk, previous_zone=previous_zone)

        # Risk mapping (slightly tighter at the top end because we now have uncertainty)
        s = detection.anomaly_score
        if s < 0.18:
            risk = VisitorRisk.TRUSTED
        elif s < 0.32:
            risk = VisitorRisk.LOW
        elif s < 0.48:
            risk = VisitorRisk.MEDIUM
        elif s < 0.68:
            risk = VisitorRisk.HIGH
        else:
            risk = VisitorRisk.CRITICAL

        violations = []
        if detection.velocity_violations:
            max_v = max(v for _, v in detection.velocity_violations)
            violations.append(f"Velocity spike: {max_v:.1f} m/s (limit {self.v_max:.1f})")
        if detection.acceleration_violations:
            max_a = max(a for _, a in detection.acceleration_violations)
            violations.append(f"Acceleration spike: {max_a:.1f} m/s² (limit {self.a_max:.1f})")
        if detection.boundary_teleportations:
            violations.append(f"Impossible boundary crossing ({len(detection.boundary_teleportations)} events)")
        if detection.geofence_violations > 30.0:
            violations.append(f"Geofence dwell outside: {detection.geofence_violations:.0f}s")
        if detection.transition_violation:
            violations.append("Forbidden zone transition")
        if detection.badge_risk > 0.4:
            violations.append(f"Badge/GPS mismatch risk {detection.badge_risk:.2f}")

        violation_details = " | ".join(violations) if violations else "No violations detected"

        flagged = []
        if detection.is_spoofed:
            flagged.append("SPOOFING ALERT: location data appears falsified")
        if detection.boundary_teleportations:
            flagged.append(f"Suspicious teleportation: {len(detection.boundary_teleportations)} events")
        if risk in (VisitorRisk.HIGH, VisitorRisk.CRITICAL):
            flagged.append("Manual verification recommended")
        if detection.transition_violation:
            flagged.append("Transition graph violation")

        avg_acc = float(np.mean([p.gps_accuracy for p in positions]))
        flagged.append(f"GPS Quality: {self.gps_quality} (accuracy {avg_acc:.1f} m)")
        flagged.append(f"Filter uncertainty: {detection.position_uncertainty:.1f} m")
        if self.enable_log_convergence:
            pct = int(self.last_convergence_factor * 100)
            phase = adaptive_phase(self.last_convergence_factor)
            flagged.append(f"Convergence: {pct}% ({phase})")

        return VisitorLocationReport(
            visitor=visitor,
            timestamp=datetime.now(),
            anomaly_score=detection.anomaly_score,
            risk_level=risk,
            is_spoofed=detection.is_spoofed,
            violation_details=violation_details,
            flagged_events=flagged,
            location_accuracy=avg_acc,
            position_uncertainty=detection.position_uncertainty,
            badge_risk_contribution=detection.badge_risk,
        )

    # ------------------------------------------------------------------
    # Visualisation (unchanged style, now supports polygons)
    # ------------------------------------------------------------------
    def visualize_path(
        self,
        positions: List[Position],
        result: DetectionResult,
        title: str = "Visitor Path Analysis",
    ):
        coords = np.array([[p.x, p.y] for p in positions], dtype=float)
        times = np.array([p.t for p in positions], dtype=float)

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
        ax1.set_aspect("equal", adjustable="datalim")

        # Draw zone
        if self.zone_profile and self.zone_profile.is_polygonal():
            poly = MplPolygon(self.zone_profile.vertices, fill=False, edgecolor="green", linewidth=2, label="Geofence")
            ax1.add_patch(poly)
        else:
            circle = Circle(tuple(self.center), self.radius, color="green", fill=False, linewidth=2, label="Geofence")
            ax1.add_patch(circle)

        ax1.plot(coords[:, 0], coords[:, 1], "b-o", linewidth=2, markersize=6, label="Path")
        ax1.plot(coords[0, 0], coords[0, 1], "go", markersize=10, label="Start")
        ax1.plot(coords[-1, 0], coords[-1, 1], "ro", markersize=10, label="End")

        for i, _ in result.velocity_violations:
            if i < len(coords):
                ax1.plot(coords[i, 0], coords[i, 1], "r*", markersize=15)
        for i in result.boundary_teleportations:
            if i + 1 < len(coords):
                ax1.plot([coords[i, 0], coords[i + 1, 0]], [coords[i, 1], coords[i + 1, 1]], "m--", linewidth=2)

        ax1.set_xlabel("X (m)")
        ax1.set_ylabel("Y (m)")
        ax1.set_title(f"{title}\nAnomaly Score: {result.anomaly_score:.3f}  |  σ≈{result.position_uncertainty:.1f} m")
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        # Velocity plot (recompute simple finite-diff for display)
        vels = []
        for i in range(len(coords) - 1):
            dt = max(times[i + 1] - times[i], 1e-3)
            vels.append(float(np.linalg.norm(coords[i + 1] - coords[i]) / dt))
        ax2.plot(times[:-1] / 60.0, vels, "b-o", linewidth=2, label="Speed")
        ax2.axhline(y=self.v_max, color="r", linestyle="--", label=f"v_max ({self.v_max:.1f} m/s)")
        ax2.set_xlabel("Time (min)")
        ax2.set_ylabel("Speed (m/s)")
        ax2.set_title("Velocity Analysis")
        ax2.legend()
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()
        return fig


def adaptive_phase(c: float) -> str:
    if c < 0.4:
        return "early"
    if c < 0.7:
        return "mid"
    return "stable"
