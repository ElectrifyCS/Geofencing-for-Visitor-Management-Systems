"""
Core detection engine: GPS smoothing pipeline and per-zone spoofing detection.
"""
from typing import List, Tuple, Optional
from datetime import datetime
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Circle

from .kalman import KalmanFilter1D, get_adaptive_thresholds
from .models import (
    Position, ZoneProfile, UserBehaviorProfile, DetectionResult,
    VisitorLocationReport, Visitor, VisitorRisk,
)


def smooth_gps_positions(positions: List[Position], process_variance: float = 10.0,
                         enable_log_convergence: bool = True) -> List[Position]:
    """Smooth GPS noise using Kalman filtering with optional logarithmic convergence"""
    if len(positions) < 2:
        return positions
    
    avg_accuracy = np.mean([p.gps_accuracy for p in positions])
    process_variance = max(5.0, avg_accuracy * 2.0)
    measurement_variance = max(1e-3, avg_accuracy)
    kalman_x = KalmanFilter1D(process_variance, measurement_variance,
                              enable_log_convergence=enable_log_convergence)
    kalman_y = KalmanFilter1D(process_variance, measurement_variance,
                              enable_log_convergence=enable_log_convergence)
    
    smoothed = []
    for i, pos in enumerate(positions):
        if i == 0:
            kalman_x.estimate = pos.x
            kalman_y.estimate = pos.y
            smoothed.append(pos) 
        else:
            smooth_x = kalman_x.update(pos.x)
            smooth_y = kalman_y.update(pos.y)
            smoothed.append(Position(t=pos.t, x=smooth_x, y=smooth_y, gps_accuracy=pos.gps_accuracy))
    
    return smoothed

class GeofenceSystem:
    def __init__(self, center: Tuple[float, float], radius: float, delta_t: float = 60.0,
                 zone_profile: Optional[ZoneProfile] = None, 
                 user_profile: Optional[UserBehaviorProfile] = None,
                 enable_kalman_smoothing: bool = True,
                 kalman_process_variance: float = 10.0,
                 enable_log_convergence: bool = True):
        """
        Initialize geofence system
        center: (x, y) coordinates of geofence center
        radius: geofence radius in meters
        delta_t: sampling interval in seconds
        zone_profile: ZoneProfile for context-aware thresholds
        user_profile: UserBehaviorProfile for personalized detection
        enable_kalman_smoothing: Apply Kalman filtering to GPS data
        kalman_process_variance: Process variance for Kalman filter
        enable_log_convergence: Enable logarithmic convergence to reduce false positives
        """
        self.center = np.array(center, dtype=float)
        self.radius = float(radius)
        self.delta_t = float(delta_t)
        self.zone_profile = zone_profile
        self.user_profile = user_profile
        self.enable_kalman_smoothing = enable_kalman_smoothing
        self.kalman_process_variance = kalman_process_variance
        self.enable_log_convergence = enable_log_convergence
        self.gps_quality = "UNKNOWN"
        self.last_positions_raw = None
        self.last_positions_smoothed = None
        self.last_convergence_factor = 1.0

        # Physical constraints - use zone profile if available
        if zone_profile:
            self.v_max = zone_profile.v_max
            self.a_max = zone_profile.a_max
            self.epsilon_v = zone_profile.epsilon_v
            self.epsilon_a = zone_profile.epsilon_a
            self.zone_type = zone_profile.zone_type
        else:
            # Defaults
            self.v_max_walk = 2.5  # m/s
            self.v_max_drive = 30.0  # m/s (urban)
            self.a_max_walk = 2.0  # m/s^2
            self.a_max_drive = 5.0  # m/s^2
            self.v_max = self.v_max_drive
            self.a_max = self.a_max_drive
            self.epsilon_v = 0.1 * self.v_max
            self.epsilon_a = 0.1 * self.a_max
            self.zone_type = "general"
        
        # Apply user profile adjustments if available
        if user_profile:
            self.v_max = user_profile.get_adjusted_v_max()
            self.a_max = user_profile.get_adjusted_a_max()

        # Anomaly weights and threshold
        self.weights = {
            'velocity': 0.45,      # Increased - velocity spikes are primary spoofing indicator
            'acceleration': 0.20,
            'path': 0.15,
            'geofence': 0.12,
            'boundary': 0.08
        }
        self.theta = 0.45  # Lower threshold for spoofing detection - more sensitive

    def _temporal_decay(self, index: int, total: int, decay_factor: float = 0.8) -> float:
        """
        Apply temporal weighting - more recent events matter more
        decay_factor: 0.5 means older events have half weight of recent ones
        """
        # Normalize index to [0, 1] where 1 is most recent
        recency = index / max(1, total - 1)
        # Apply decay to older events
        old_weight = decay_factor
        new_weight = 1.0
        return old_weight + (new_weight - old_weight) * recency

    def is_inside_geofence(self, pos: np.ndarray) -> bool:
        """Check if position is inside geofence"""
        return float(np.linalg.norm(pos - self.center)) <= self.radius

    def calculate_velocity(self, r_i: np.ndarray, r_i_plus_1: np.ndarray) -> float:
        """Calculate velocity magnitude between two positions"""
        displacement = r_i_plus_1 - r_i
        distance = float(np.linalg.norm(displacement))
        return distance / self.delta_t

    def calculate_acceleration(self, r_i_minus_1: np.ndarray, r_i: np.ndarray,
                               r_i_plus_1: np.ndarray) -> float:
        """Calculate acceleration magnitude using finite second derivative"""
        a = (r_i_plus_1 - 2 * r_i + r_i_minus_1) / (self.delta_t ** 2)
        return float(np.linalg.norm(a))

    def _normalize_score(self, violation_amount: float, max_value: float) -> float:
        """Normalize violation to [0, 1] score, safe when max_value == 0"""
        if violation_amount <= 0:
            return 0.0
        if max_value <= 0:
            return 1.0  # if no meaningful max, treat as full violation
        return min(1.0, violation_amount / max_value)

    def _get_convergence_factor(self) -> float:
        """Compute and record convergence factor for visitor and zone."""
        if not self.enable_log_convergence or not self.user_profile:
            return 1.0
        zone_name = self.zone_profile.zone_name if self.zone_profile else "global"
        convergence_factor = self.user_profile.get_convergence_factor(zone_name, baseline=15)
        self.last_convergence_factor = convergence_factor
        self.user_profile.record_measurement(zone_name)
        return convergence_factor

    def _calculate_motion_violations(self, coords: np.ndarray, adjusted_epsilon_v: float,
                                     adjusted_epsilon_a: float) -> tuple:
        """Calculate velocity, acceleration, and path violation metrics."""
        n = len(coords)
        velocity_violations = []
        acceleration_violations = []
        velocities = []
        accelerations = []

        for i in range(n - 1):
            v_i = self.calculate_velocity(coords[i], coords[i + 1])
            velocities.append(v_i)
            if v_i > self.v_max + adjusted_epsilon_v:
                temporal_weight = self._temporal_decay(i, n - 1, decay_factor=0.7)
                velocity_violations.append((i, v_i * temporal_weight))

        for i in range(1, n - 1):
            a_i = self.calculate_acceleration(coords[i - 1], coords[i], coords[i + 1])
            accelerations.append(a_i)
            if a_i > self.a_max + adjusted_epsilon_a:
                temporal_weight = self._temporal_decay(i, n - 1, decay_factor=0.7)
                acceleration_violations.append((i, a_i * temporal_weight))

        D_actual = sum(float(np.linalg.norm(coords[i + 1] - coords[i])) for i in range(n - 1))
        D_min = float(np.linalg.norm(coords[-1] - coords[0]))
        path_violation = D_actual > D_min + max(1.0, 0.05 * D_min)

        return velocities, accelerations, velocity_violations, acceleration_violations, path_violation

    def _calculate_geofence_metrics(self, coords: np.ndarray) -> tuple:
        """Compute time outside the geofence and teleportation boundary events."""
        n = len(coords)
        T_inside = 0.0
        for i in range(n - 1):
            midpoint = (coords[i] + coords[i + 1]) / 2.0
            if self.is_inside_geofence(midpoint):
                T_inside += self.delta_t

        T_total = (n - 1) * self.delta_t
        T_outside = max(0.0, T_total - T_inside)
        boundary_teleportations = []

        for i in range(n - 1):
            inside_i = self.is_inside_geofence(coords[i])
            inside_i_plus_1 = self.is_inside_geofence(coords[i + 1])
            if inside_i != inside_i_plus_1:
                distance = float(np.linalg.norm(coords[i + 1] - coords[i]))
                d_max = self.v_max * self.delta_t
                if distance > d_max:
                    boundary_teleportations.append(i)

        return T_outside, T_total, boundary_teleportations

    def _get_zone_weights(self) -> dict:
        """Return adjusted anomaly weights without mutating instance state."""
        weights = self.weights.copy()
        if self.zone_type == "stairwell":
            weights['acceleration'] = 0.15
            weights['velocity'] = 0.25
        elif self.zone_type == "wheelchair_accessible":
            weights['velocity'] = 0.2
            weights['acceleration'] = 0.15
        elif self.zone_type == "lobby":
            weights['velocity'] = 0.35
            weights['boundary'] = 0.15
        return weights

    def detect_spoofing(self, positions: List[Position]) -> DetectionResult:
        """
        Main detection algorithm with temporal weighting and Kalman smoothing
        positions: time-ordered list of Position
        """
        n = len(positions)
        if n < 2:
            return DetectionResult([], [], False, 0.0, [], 0.0, False)

        self.last_positions_raw = positions

        if self.enable_kalman_smoothing:
            positions = smooth_gps_positions(
                positions,
                process_variance=self.kalman_process_variance,
                enable_log_convergence=self.enable_log_convergence
            )
            self.last_positions_smoothed = positions

        avg_gps_accuracy = np.mean([p.gps_accuracy for p in self.last_positions_raw])
        convergence_factor = self._get_convergence_factor()
        adaptive_thresholds = get_adaptive_thresholds(avg_gps_accuracy, convergence_factor)
        self.gps_quality = adaptive_thresholds['quality']
        adjusted_epsilon_v = self.epsilon_v * adaptive_thresholds['epsilon_v_factor']
        adjusted_epsilon_a = self.epsilon_a * adaptive_thresholds['epsilon_a_factor']

        coords = np.array([[p.x, p.y] for p in positions], dtype=float)

        _, _, velocity_violations, acceleration_violations, path_violation = (
            self._calculate_motion_violations(coords, adjusted_epsilon_v, adjusted_epsilon_a)
        )
        T_outside, T_total, boundary_teleportations = self._calculate_geofence_metrics(coords)

        S_v = 0.0
        if velocity_violations:
            max_v = max(v for _, v in velocity_violations)
            S_v = self._normalize_score(max_v - self.v_max, self.v_max)

        S_a = 0.0
        if acceleration_violations:
            max_a = max(a for _, a in acceleration_violations)
            S_a = self._normalize_score(max_a - self.a_max, self.a_max)

        S_p = 1.0 if path_violation else 0.0
        S_g = min(1.0, T_outside / (T_total * 0.5)) if T_total > 0 else 0.0
        S_b = min(1.0, len(boundary_teleportations) / 3.0)

        weights = self._get_zone_weights()
        anomaly_score = (
            weights['velocity'] * S_v +
            weights['acceleration'] * S_a +
            weights['path'] * S_p +
            weights['geofence'] * S_g +
            weights['boundary'] * S_b
        )
        is_spoofed = anomaly_score > self.theta

        return DetectionResult(
            velocity_violations=velocity_violations,
            acceleration_violations=acceleration_violations,
            path_violation=path_violation,
            geofence_violations=T_outside,
            boundary_teleportations=boundary_teleportations,
            anomaly_score=anomaly_score,
            is_spoofed=is_spoofed
        )
    
    def verify_visitor(self, visitor: Visitor, positions: List[Position]) -> VisitorLocationReport:
        """
        Verify visitor location and generate security report
        
        Returns:
            VisitorLocationReport with risk assessment
        """
        detection = self.detect_spoofing(positions)
        
        # Determine risk level based on anomaly score
        if detection.anomaly_score < 0.2:
            risk = VisitorRisk.TRUSTED
        elif detection.anomaly_score < 0.35:
            risk = VisitorRisk.LOW
        elif detection.anomaly_score < 0.5:
            risk = VisitorRisk.MEDIUM
        elif detection.anomaly_score < 0.7:
            risk = VisitorRisk.HIGH
        else:
            risk = VisitorRisk.CRITICAL
        
        # Build violation details string
        violations = []
        if detection.velocity_violations:
            max_v = max(v for _, v in detection.velocity_violations)
            violations.append(f"Velocity spike: {max_v:.1f} m/s (limit: {self.v_max} m/s)")
        if detection.acceleration_violations:
            max_a = max(a for _, a in detection.acceleration_violations)
            violations.append(f"Acceleration spike: {max_a:.1f} m/s² (limit: {self.a_max} m/s²)")
        if detection.boundary_teleportations:
            violations.append(f"Impossible boundary crossing detected ({len(detection.boundary_teleportations)} events)")
        if detection.geofence_violations > (len(positions) * self.delta_t * 0.3):
            violations.append(f"Geofence violation: {detection.geofence_violations:.0f}s outside boundary")
        
        violation_details = " | ".join(violations) if violations else "No violations detected"
        
        # Determine flagged events for security team
        flagged_events = []
        if detection.is_spoofed:
            flagged_events.append("SPOOFING ALERT: Location data appears falsified")
        if detection.boundary_teleportations:
            flagged_events.append(f"Suspicious teleportation: {len(detection.boundary_teleportations)} events")
        if risk in [VisitorRisk.HIGH, VisitorRisk.CRITICAL]:
            flagged_events.append("Manual verification recommended")
        
        # Add GPS quality and convergence info
        accuracy_m = np.mean([p.gps_accuracy for p in self.last_positions_raw])
        flagged_events.append(f"GPS Quality: {self.gps_quality} (accuracy: {accuracy_m:.1f}m)")
        if self.enable_log_convergence:
            convergence_pct = int(self.last_convergence_factor * 100)
            phase = 'early' if self.last_convergence_factor < 0.4 else ('mid' if self.last_convergence_factor < 0.7 else 'stable')
            flagged_events.append(f"Convergence: {convergence_pct}% ({phase} phase)")
        
        # Calculate average location accuracy
        avg_accuracy = np.mean([p.gps_accuracy for p in positions])
        
        return VisitorLocationReport(
            visitor=visitor,
            timestamp=datetime.now(),
            anomaly_score=detection.anomaly_score,
            risk_level=risk,
            is_spoofed=detection.is_spoofed,
            violation_details=violation_details,
            flagged_events=flagged_events,
            location_accuracy=avg_accuracy
        )

    def visualize_path(self, positions: List[Position], result: DetectionResult,
                       title: str = "Visitor Path Analysis"):
        """Visualize the path with geofence and violations"""
        coords = np.array([[p.x, p.y] for p in positions], dtype=float)
        times = np.array([p.t for p in positions], dtype=float)

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
        ax1.set_aspect('equal', adjustable='datalim')

        # Draw geofence
        circle = Circle(tuple(self.center), self.radius, color='green', fill=False, linewidth=2, label='Geofence')
        ax1.add_patch(circle)

        # Path
        ax1.plot(coords[:, 0], coords[:, 1], 'b-o', linewidth=2, markersize=6, label='Path')
        ax1.plot(coords[0, 0], coords[0, 1], 'go', markersize=10, label='Start')
        ax1.plot(coords[-1, 0], coords[-1, 1], 'ro', markersize=10, label='End')

        # Mark velocity violations
        for i, v in result.velocity_violations:
            ax1.plot(coords[i, 0], coords[i, 1], 'r*', markersize=15)

        # Mark boundary teleportations
        for i in result.boundary_teleportations:
            ax1.plot([coords[i, 0], coords[i + 1, 0]], [coords[i, 1], coords[i + 1, 1]], 'm--', linewidth=2)

        ax1.set_xlabel('X (meters)')
        ax1.set_ylabel('Y (meters)')
        ax1.set_title(f'{title}\nAnomaly Score: {result.anomaly_score:.3f}')
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        # Velocity plot
        velocities = [self.calculate_velocity(coords[i], coords[i + 1]) for i in range(len(coords) - 1)]
        ax2.plot(times[:-1] / 60.0, velocities, 'b-o', linewidth=2, label='Velocity')
        ax2.axhline(y=self.v_max, color='r', linestyle='--', label=f'Max velocity ({self.v_max} m/s)')
        for i, v in result.velocity_violations:
            ax2.plot(times[i] / 60.0, v, 'r*', markersize=12)
        ax2.set_xlabel('Time (minutes)')
        ax2.set_ylabel('Velocity (m/s)')
        ax2.set_title('Velocity Analysis')
        ax2.legend()
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()
        return fig

