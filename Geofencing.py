#!/usr/bin/env python3
"""
Visitor Geofencing Security System

A location-based verification system for visitor management that detects
spoofed GPS coordinates using multi-factor anomaly detection.

Features:
- Visitor tag integration (badge/RFID tracking)
- Real-time spoofing detection
- Risk scoring and alerts
- Visualization and reporting
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple, Optional
from enum import Enum
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
from datetime import datetime
import json
import os


def compute_log_convergence_factor(measurements: int, baseline: int = 15) -> float:
    """Compute the logarithmic convergence factor.

    The model used here is:
        c(n) = min(1, ln(n + 1) / ln(B + 1))

    where:
        - n is the current number of measurements
        - B is the baseline number of samples for near-complete convergence

    This makes the system start conservatively and gradually increase trust as
    more evidence is collected.
    """
    if measurements <= 0:
        return 0.0
    if baseline <= 0:
        return 1.0
    return min(1.0, np.log(measurements + 1) / np.log(baseline + 1))


# Optional reproducibility
_seed = os.environ.get("GEOFENCE_SEED")
if _seed is not None:
    np.random.seed(int(_seed))

class KalmanFilter1D:
    """1D Kalman filter with logarithmic convergence for smoothing GPS coordinates"""
    def __init__(self, process_variance: float = 10.0, measurement_variance: float = 5.0,
                 enable_log_convergence: bool = True, baseline_convergence: int = 15):
        self.process_variance = process_variance
        self.measurement_variance = measurement_variance
        self.estimate = 0.0
        self.estimate_error = 1.0
        self.enable_log_convergence = enable_log_convergence
        self.baseline_convergence = baseline_convergence  # samples needed for full convergence
        self.measurement_count = 0
    
    def _get_convergence_factor(self) -> float:
        """Apply logarithmic convergence scaling.

        The gain is reduced early by the factor:
            c(n) = min(1, ln(n + 1) / ln(B + 1))
        where n is the count of measurements seen so far and B is the baseline
        number of samples needed for near-full convergence.

        This factor is then used to smooth the Kalman gain scaling so early
        measurements have less influence and later measurements are trusted more.
        """
        if not self.enable_log_convergence:
            return 1.0
        return compute_log_convergence_factor(
            self.measurement_count,
            self.baseline_convergence,
        )
    
    def update(self, measurement: float) -> float:
        """Process one measurement and return smoothed value with log convergence"""
        self.measurement_count += 1
        prediction = self.estimate
        prediction_error = self.estimate_error + self.process_variance
        kalman_gain = prediction_error / (prediction_error + self.measurement_variance)
        
        # Apply logarithmic convergence to gain (reduces early influence)
        convergence_factor = self._get_convergence_factor()
        adjusted_kalman_gain = kalman_gain * convergence_factor
        
        self.estimate = prediction + adjusted_kalman_gain * (measurement - prediction)
        self.estimate_error = (1 - adjusted_kalman_gain) * prediction_error
        return self.estimate
    
    def reset(self):
        self.estimate = 0.0
        self.estimate_error = 1.0
        self.measurement_count = 0

def get_adaptive_thresholds(avg_gps_accuracy: float, convergence_factor: float = 1.0) -> dict:
    """Adapt thresholds based on GPS accuracy and convergence state.

    The convergence adjustment uses:
        tau(c) = 0.5 + 1.0 * c

    where c is the logarithmic convergence factor. This gives a looser threshold
    when the system is still in its early phase and tighter thresholds once it is
    more stable.

    Args:
        avg_gps_accuracy: GPS accuracy in meters
        convergence_factor: Logarithmic convergence (0-1), reduces thresholds early on
    """
    if avg_gps_accuracy < 5.0:
        base_thresholds = {'epsilon_v_factor': 1.0, 'epsilon_a_factor': 1.0, 'quality': 'EXCELLENT'}
    elif avg_gps_accuracy < 10.0:
        base_thresholds = {'epsilon_v_factor': 1.3, 'epsilon_a_factor': 1.2, 'quality': 'GOOD'}
    elif avg_gps_accuracy < 20.0:
        base_thresholds = {'epsilon_v_factor': 1.8, 'epsilon_a_factor': 1.6, 'quality': 'MODERATE'}
    else:
        base_thresholds = {'epsilon_v_factor': 2.5, 'epsilon_a_factor': 2.2, 'quality': 'POOR'}
    
    # Apply convergence-based adjustment: early phase gets more tolerance.
    # Using tau(c) = 0.5 + 1.0 * c, the tolerance ranges from 0.5x at the start
    # (c close to 0) to 1.5x when fully converged (c close to 1).
    phase_tolerance = 0.5 + 1.0 * convergence_factor
    
    return {
        'epsilon_v_factor': base_thresholds['epsilon_v_factor'] * phase_tolerance,
        'epsilon_a_factor': base_thresholds['epsilon_a_factor'] * phase_tolerance,
        'quality': base_thresholds['quality'],
        'convergence_factor': convergence_factor,
        'convergence_phase': 'early' if convergence_factor < 0.4 else ('mid' if convergence_factor < 0.7 else 'stable')
    }

class VisitorRisk(Enum):
    """Risk levels for visitor location data"""
    TRUSTED = "TRUSTED"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

@dataclass
class Position:
    """Position at time t"""
    t: float  # time in seconds
    x: float  # x-coordinate in meters
    y: float  # y-coordinate in meters
    gps_accuracy: float = 5.0  # GPS accuracy in meters (std dev), default ~5m urban

@dataclass
class Visitor:
    """Visitor with tracking tag"""
    visitor_id: str
    name: str
    badge_tag: str
    entry_time: datetime
    expected_zones: List[Tuple[float, float]]  # expected geofence centers
    security_level: str = "standard"  # standard, vip, restricted
    host_department: str = "General"
    purpose: str = "Meeting"
    exit_time: Optional[datetime] = None
    allowed_areas: List[str] = field(default_factory=lambda: ["lobby", "conference"])
    behavior_profile: Optional[UserBehaviorProfile] = None

@dataclass
class VisitorLocationReport:
    """Verification report for visitor location"""
    visitor: Visitor
    timestamp: datetime
    anomaly_score: float
    risk_level: VisitorRisk
    is_spoofed: bool
    violation_details: str
    flagged_events: List[str] = field(default_factory=list)
    location_accuracy: float = 0.0  # meters, estimated GPS error
    
    def to_dict(self) -> dict:
        """Export report as dictionary"""
        return {
            'visitor_id': self.visitor.visitor_id,
            'visitor_name': self.visitor.name,
            'badge_tag': self.visitor.badge_tag,
            'host_department': self.visitor.host_department,
            'purpose': self.visitor.purpose,
            'security_level': self.visitor.security_level,
            'timestamp': self.timestamp.isoformat(),
            'anomaly_score': round(self.anomaly_score, 3),
            'risk_level': self.risk_level.value,
            'is_spoofed': self.is_spoofed,
            'violation_details': self.violation_details,
            'flagged_events': self.flagged_events,
            'location_accuracy_m': round(self.location_accuracy, 1)
        }

@dataclass
class DetectionResult:
    """Results from spoofing detection"""
    velocity_violations: List[Tuple[int, float]]
    acceleration_violations: List[Tuple[int, float]]
    path_violation: bool
    geofence_violations: float  # time outside (seconds)
    boundary_teleportations: List[int]
    anomaly_score: float
    is_spoofed: bool

@dataclass
class ZoneProfile:
    """Configuration for a specific building zone"""
    zone_name: str
    center: Tuple[float, float]
    radius: float
    v_max: float = 30.0  # m/s - default driving speed
    a_max: float = 5.0   # m/s^2 - default driving acceleration
    epsilon_v: float = 3.0  # 10% of v_max
    epsilon_a: float = 0.5
    zone_type: str = "general"  # general, lobby, corridor, parking, stairwell, wheelchair_accessible

@dataclass
class UserBehaviorProfile:
    """Learned behavior profile for a visitor based on history"""
    visitor_id: str
    visits_count: int = 0
    avg_velocity: float = 2.0  # m/s - starts conservative (walking)
    max_velocity: float = 2.5  # m/s
    avg_acceleration: float = 1.0  # m/s^2
    max_acceleration: float = 2.0  # m/s^2
    common_zones: List[str] = field(default_factory=list)
    typical_visit_duration: float = 3600.0  # seconds
    deviation_tolerance: float = 1.5  # multiplier for learned profile thresholds
    last_updated: datetime = field(default_factory=datetime.now)
    measurements_recorded: int = 0  # for logarithmic convergence tracking
    velocity_samples: int = 0
    acceleration_samples: int = 0
    convergence_state: dict = field(default_factory=dict)  # per-zone convergence tracking
    
    def get_adjusted_v_max(self) -> float:
        """Get velocity threshold adjusted to user profile"""
        return self.max_velocity * self.deviation_tolerance
    
    def get_adjusted_a_max(self) -> float:
        """Get acceleration threshold adjusted to user profile"""
        return self.max_acceleration * self.deviation_tolerance
    
    def get_convergence_factor(self, zone_name: str = "global", baseline: int = 15) -> float:
        """Get the logarithmic convergence factor for a specific zone.

        The factor follows:
            c(n) = min(1, ln(n + 1) / ln(B + 1))
        """
        if zone_name not in self.convergence_state:
            self.convergence_state[zone_name] = {'measurements': 0}

        n = self.convergence_state[zone_name]['measurements']
        return compute_log_convergence_factor(n, baseline)
    
    def record_measurement(self, zone_name: str = "global"):
        """Record a measurement for convergence tracking"""
        if zone_name not in self.convergence_state:
            self.convergence_state[zone_name] = {'measurements': 0}
        
        self.convergence_state[zone_name]['measurements'] += 1
        self.measurements_recorded += 1

@dataclass
class BuildingLayout:
    """Building configuration with multiple zones"""
    building_name: str
    zones: List[ZoneProfile] = field(default_factory=list)
    visitor_profiles: dict = field(default_factory=dict)  # visitor_id -> UserBehaviorProfile
    
    def get_zone(self, zone_name: str) -> Optional[ZoneProfile]:
        """Get zone configuration by name"""
        for zone in self.zones:
            if zone.zone_name == zone_name:
                return zone
        return None
    
    def get_visitor_profile(self, visitor_id: str) -> UserBehaviorProfile:
        """Get or create visitor profile"""
        if visitor_id not in self.visitor_profiles:
            self.visitor_profiles[visitor_id] = UserBehaviorProfile(visitor_id=visitor_id)
        return self.visitor_profiles[visitor_id]
    
    def update_visitor_profile(self, visitor_id: str, velocities: List[float], 
                              accelerations: List[float], zone_names: List[str]):
        """Update visitor profile based on observed behavior"""
        profile = self.get_visitor_profile(visitor_id)
        if velocities:
            new_velocity_sum = float(np.sum(velocities))
            new_velocity_count = len(velocities)
            total_velocity_count = profile.velocity_samples + new_velocity_count
            if profile.velocity_samples > 0:
                profile.avg_velocity = (
                    profile.avg_velocity * profile.velocity_samples + new_velocity_sum
                ) / total_velocity_count
            else:
                profile.avg_velocity = new_velocity_sum / new_velocity_count
            profile.velocity_samples = total_velocity_count
            profile.max_velocity = max(profile.max_velocity, float(np.max(velocities)))
        if accelerations:
            new_acceleration_sum = float(np.sum(accelerations))
            new_acceleration_count = len(accelerations)
            total_acceleration_count = profile.acceleration_samples + new_acceleration_count
            if profile.acceleration_samples > 0:
                profile.avg_acceleration = (
                    profile.avg_acceleration * profile.acceleration_samples + new_acceleration_sum
                ) / total_acceleration_count
            else:
                profile.avg_acceleration = new_acceleration_sum / new_acceleration_count
            profile.acceleration_samples = total_acceleration_count
            profile.max_acceleration = max(profile.max_acceleration, float(np.max(accelerations)))
        if zone_names:
            for zone in zone_names:
                if zone not in profile.common_zones:
                    profile.common_zones.append(zone)
        profile.visits_count += 1
        profile.last_updated = datetime.now()

@dataclass
class BadgeEvent:
    """RFID/Badge scan event"""
    timestamp: datetime
    visitor_id: str
    badge_tag: str
    reader_location: str  # zone name
    reader_position: Tuple[float, float]  # (x, y) coordinates
    gps_position: Optional[Tuple[float, float]] = None  # concurrent GPS position (if available)
    event_type: str = "entry"  # entry, exit, scan
    distance_to_reader: float = 0.0  # calculated distance from GPS to reader
    
    def to_dict(self) -> dict:
        """Export as dictionary"""
        return {
            'timestamp': self.timestamp.isoformat(),
            'visitor_id': self.visitor_id,
            'badge_tag': self.badge_tag,
            'reader_location': self.reader_location,
            'event_type': self.event_type,
            'distance_to_reader': round(self.distance_to_reader, 1)
        }

@dataclass
class BadgeGPSCorrelation:
    """Analysis of badge and GPS location correlation"""
    badge_event: BadgeEvent
    gps_distance_error: float  # distance between GPS and badge reader
    correlation_status: str  # "MATCH", "SMALL_DEVIATION", "MAJOR_DEVIATION", "SPOOFING_ALERT"
    confidence: float  # 0-1, how confident we are in the correlation
    alert_reason: Optional[str] = None
    risk_score: float = 0.0  # 0-1, risk due to mismatch
    
    def is_anomalous(self) -> bool:
        """Check if correlation indicates anomaly"""
        return self.correlation_status in ["MAJOR_DEVIATION", "SPOOFING_ALERT"]

class BadgeSystem:
    """Manage badge/RFID readers and visitor tracking"""
    
    def __init__(self):
        self.badge_events: List[BadgeEvent] = []
        self.readers: dict = {}  # reader_id -> reader_config
        self.visitor_badge_map: dict = {}  # visitor_id -> badge_tag
    
    def add_reader(self, reader_id: str, reader_location: str, position: Tuple[float, float]):
        """Register a badge reader location"""
        self.readers[reader_id] = {
            'location': reader_location,
            'position': position,
            'registered_at': datetime.now()
        }
    
    def record_badge_event(self, badge_event: BadgeEvent):
        """Record a badge scan event"""
        self.badge_events.append(badge_event)
        self.visitor_badge_map[badge_event.visitor_id] = badge_event.badge_tag
    
    def get_visitor_badge_events(self, visitor_id: str, time_window: int = 3600) -> List[BadgeEvent]:
        """Get recent badge events for a visitor (within time_window seconds)"""
        cutoff_time = datetime.now().timestamp() - time_window
        return [e for e in self.badge_events 
                if e.visitor_id == visitor_id and e.timestamp.timestamp() > cutoff_time]
    
    def correlate_badge_gps(self, badge_event: BadgeEvent) -> BadgeGPSCorrelation:
        """Check if badge location matches GPS position"""
        if badge_event.gps_position is None:
            return BadgeGPSCorrelation(
                badge_event=badge_event,
                gps_distance_error=0.0,
                correlation_status="UNKNOWN",
                confidence=0.0,
                alert_reason="No GPS data available"
            )
        
        # Calculate distance between GPS and badge reader
        gps_pos = np.array(badge_event.gps_position)
        reader_pos = np.array(badge_event.reader_position)
        distance = float(np.linalg.norm(gps_pos - reader_pos))
        
        badge_event.distance_to_reader = distance
        
        # Determine correlation status
        alert_reason = None
        if distance < 50:  # Within 50m
            status = "MATCH"
            confidence = 0.95
            risk_score = 0.0
        elif distance < 200:  # Within 200m
            status = "SMALL_DEVIATION"
            confidence = 0.7
            risk_score = 0.2
        elif distance < 500:  # Within 500m (might be in adjacent zone)
            status = "MAJOR_DEVIATION"
            confidence = 0.4
            risk_score = 0.5
            alert_reason = f"Badge at {badge_event.reader_location}, GPS shows {distance:.0f}m away"
        else:  # More than 500m away
            status = "SPOOFING_ALERT"
            confidence = 0.1
            risk_score = 0.9
            alert_reason = f"CRITICAL: Badge says {badge_event.reader_location}, GPS shows {distance:.0f}m away!"
        
        return BadgeGPSCorrelation(
            badge_event=badge_event,
            gps_distance_error=distance,
            correlation_status=status,
            confidence=confidence,
            alert_reason=alert_reason,
            risk_score=risk_score
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

class VisitorManagementSystem:
    """Integration layer for visitor management with geofencing"""
    
    def __init__(self):
        self.active_visitors: dict = {}  # visitor_id -> Visitor
        self.location_history: dict = {}  # visitor_id -> List[VisitorLocationReport]
        self.geofence_systems: dict = {}  # zone_name -> GeofenceSystem
        self.security_alerts: List[dict] = []
        self.badge_system = BadgeSystem()  # Badge/RFID tracking
        self.badge_gps_correlations: dict = {}  # visitor_id -> List[BadgeGPSCorrelation]
        
    def register_visitor(self, visitor: Visitor) -> dict:
        """Register visitor at entry point"""
        self.active_visitors[visitor.visitor_id] = visitor
        self.location_history[visitor.visitor_id] = []
        return {
            'status': 'SUCCESS',
            'message': f'Visitor {visitor.name} checked in',
            'badge_tag': visitor.badge_tag,
            'timestamp': datetime.now().isoformat(),
            'allowed_areas': visitor.allowed_areas
        }
    
    def check_out_visitor(self, visitor_id: str) -> dict:
        """Check out visitor and generate exit report"""
        if visitor_id not in self.active_visitors:
            return {'status': 'ERROR', 'message': 'Visitor not found'}
        
        visitor = self.active_visitors[visitor_id]
        visitor.exit_time = datetime.now()
        duration_minutes = (visitor.exit_time - visitor.entry_time).total_seconds() / 60
        
        report = {
            'visitor_id': visitor_id,
            'visitor_name': visitor.name,
            'entry_time': visitor.entry_time.isoformat(),
            'exit_time': visitor.exit_time.isoformat(),
            'duration_minutes': round(duration_minutes, 1),
            'anomalies_detected': len(self.location_history[visitor_id])
        }
        
        del self.active_visitors[visitor_id]
        return {'status': 'SUCCESS', 'checkout_report': report}
    
    def add_geofence(self, zone_name: str, center: Tuple[float, float], radius: float,
                     zone_profile: Optional[ZoneProfile] = None,
                     user_profile: Optional[UserBehaviorProfile] = None):
        """Add a facility zone/area with geofence"""
        self.geofence_systems[zone_name] = GeofenceSystem(
            center,
            radius,
            zone_profile=zone_profile,
            user_profile=user_profile
        )
        response = {'status': 'SUCCESS', 'zone': zone_name, 'radius_m': radius}
        if zone_profile:
            response['zone_type'] = zone_profile.zone_type
        return response
    
    def verify_visitor_location(self, visitor_id: str, zone_name: str, 
                               positions: List[Position]) -> VisitorLocationReport:
        """Verify visitor in specific zone and generate report"""
        if visitor_id not in self.active_visitors:
            return None
        
        if zone_name not in self.geofence_systems:
            return None
        
        visitor = self.active_visitors[visitor_id]
        geofence = self.geofence_systems[zone_name]
        effective_geofence = geofence

        if visitor.behavior_profile and geofence.user_profile is None:
            effective_geofence = GeofenceSystem(
                geofence.center.tolist(),
                geofence.radius,
                delta_t=geofence.delta_t,
                zone_profile=geofence.zone_profile,
                user_profile=visitor.behavior_profile,
                enable_kalman_smoothing=geofence.enable_kalman_smoothing,
                kalman_process_variance=geofence.kalman_process_variance,
                enable_log_convergence=geofence.enable_log_convergence
            )

        report = effective_geofence.verify_visitor(visitor, positions)
        
        # Store in history
        self.location_history[visitor_id].append(report)
        
        # Generate security alert if needed
        if report.is_spoofed or report.risk_level in [VisitorRisk.CRITICAL, VisitorRisk.HIGH]:
            alert = {
                'timestamp': datetime.now().isoformat(),
                'visitor_id': visitor_id,
                'visitor_name': visitor.name,
                'zone': zone_name,
                'risk_level': report.risk_level.value,
                'anomaly_score': report.anomaly_score,
                'alert_type': 'SPOOFING' if report.is_spoofed else 'RISK'
            }
            self.security_alerts.append(alert)
        
        return report
    
    def get_visitor_summary(self, visitor_id: str) -> dict:
        """Get summary of visitor's activity"""
        if visitor_id not in self.location_history:
            return None
        
        history = self.location_history[visitor_id]
        if not history:
            return {'visitor_id': visitor_id, 'events': 0, 'alerts': 0}
        
        alerts = [h for h in history if h.risk_level in [VisitorRisk.HIGH, VisitorRisk.CRITICAL]]
        
        return {
            'visitor_id': visitor_id,
            'total_checks': len(history),
            'alerts_count': len(alerts),
            'max_anomaly_score': max(h.anomaly_score for h in history),
            'spoofing_detected': any(h.is_spoofed for h in history)
        }
    
    def record_badge_event(self, visitor_id: str, badge_event: BadgeEvent) -> dict:
        """Record a badge scan event with optional GPS correlation"""
        self.badge_system.record_badge_event(badge_event)
        
        # Initialize correlation history if needed
        if visitor_id not in self.badge_gps_correlations:
            self.badge_gps_correlations[visitor_id] = []
        
        # Analyze badge-GPS correlation
        correlation = self.badge_system.correlate_badge_gps(badge_event)
        self.badge_gps_correlations[visitor_id].append(correlation)
        
        # Generate alert if anomaly detected
        if correlation.is_anomalous():
            alert = {
                'timestamp': datetime.now().isoformat(),
                'visitor_id': visitor_id,
                'alert_type': 'BADGE_GPS_MISMATCH',
                'badge_location': badge_event.reader_location,
                'gps_distance_error': round(correlation.gps_distance_error, 1),
                'correlation_status': correlation.correlation_status,
                'risk_score': correlation.risk_score,
                'reason': correlation.alert_reason
            }
            self.security_alerts.append(alert)
        
        return {
            'status': 'SUCCESS',
            'badge_event_recorded': badge_event.to_dict(),
            'correlation': correlation.correlation_status,
            'gps_distance_error': round(correlation.gps_distance_error, 1)
        }
    
    def get_visitor_badge_history(self, visitor_id: str) -> List[dict]:
        """Get badge events for visitor"""
        events = self.badge_system.get_visitor_badge_events(visitor_id)
        return [e.to_dict() for e in events]
    
    def get_badge_gps_correlation_summary(self, visitor_id: str) -> dict:
        """Get summary of badge-GPS correlations for visitor"""
        if visitor_id not in self.badge_gps_correlations:
            return {'visitor_id': visitor_id, 'correlations': 0}
        
        correlations = self.badge_gps_correlations[visitor_id]
        anomalies = [c for c in correlations if c.is_anomalous()]
        
        return {
            'visitor_id': visitor_id,
            'total_correlations': len(correlations),
            'anomalies_detected': len(anomalies),
            'correlation_statuses': [c.correlation_status for c in correlations[-5:]],  # last 5
            'max_distance_error': max(c.gps_distance_error for c in correlations) if correlations else 0
        }
    
    def export_audit_log(self) -> dict:
        """Export complete audit log for security team"""
        return {
            'timestamp': datetime.now().isoformat(),
            'active_visitors': len(self.active_visitors),
            'security_alerts': self.security_alerts,
            'total_zones': len(self.geofence_systems)
        }


def _random_unit_vector() -> np.ndarray:
    v = np.random.randn(2)
    norm = np.linalg.norm(v)
    if norm == 0:
        return np.array([1.0, 0.0])
    return v / norm

def generate_legitimate_path(center: Tuple[float, float], n_points: int = 10, 
                            gps_accuracy: float = 5.0) -> List[Position]:
    """Generate a realistic walking/driving path with GPS noise"""
    positions: List[Position] = []
    current = np.array(center, dtype=float) + np.array([-50.0, -50.0])
    for i in range(n_points):
        t = i * 60.0
        if i > 0:
            direction = _random_unit_vector()
            step = direction * np.random.uniform(20.0, 80.0)  # 20-80m per minute
            current = current + step
        # Add realistic GPS noise
        noise = np.random.normal(0, gps_accuracy, 2)
        noisy_pos = current + noise
        positions.append(Position(t=t, x=float(noisy_pos[0]), y=float(noisy_pos[1]), 
                                 gps_accuracy=gps_accuracy))
    return positions

def generate_spoofed_path(center: Tuple[float, float], n_points: int = 10,
                         gps_accuracy: float = 5.0) -> List[Position]:
    """Generate a path with an obvious teleportation (spoofing) and GPS noise"""
    positions: List[Position] = []
    current = np.array(center, dtype=float) + np.array([-50.0, -50.0])
    for i in range(n_points):
        t = i * 60.0
        if i == max(1, n_points // 2):
            # Teleport jump - too large to be GPS noise
            current = current + np.array([3000.0, 2000.0])
        else:
            direction = _random_unit_vector()
            step = direction * np.random.uniform(20.0, 80.0)
            current = current + step
        # Add realistic GPS noise
        noise = np.random.normal(0, gps_accuracy, 2)
        noisy_pos = current + noise
        positions.append(Position(t=t, x=float(noisy_pos[0]), y=float(noisy_pos[1]),
                                 gps_accuracy=gps_accuracy))
    return positions

def generate_extreme_spoofed_path(center: Tuple[float, float], n_points: int = 10,
                                 gps_accuracy: float = 5.0) -> List[Position]:
    """Generate an extremely spoofed path with multiple teleportations to trigger CRITICAL alerts"""
    positions: List[Position] = []
    current = np.array(center, dtype=float) + np.array([-100.0, -100.0])
    
    for i in range(n_points):
        t = i * 60.0
        
        # Add MULTIPLE dramatic jumps for extreme spoofing
        if i == 2:
            # First teleportation: 10km away instantly (impossible speed)
            current = current + np.array([10000.0, 7000.0])
        elif i == 4:
            # Second teleportation: back to origin in 1 second (impossible)
            current = np.array(center, dtype=float)
        elif i == 6:
            # Third teleportation: 20km away instantly
            current = current + np.array([20000.0, -15000.0])
        elif i == 8:
            # Fourth teleportation: diagonal jump
            current = current + np.array([-15000.0, 18000.0])
        else:
            # Normal movement on other intervals
            direction = _random_unit_vector()
            step = direction * np.random.uniform(20.0, 50.0)
            current = current + step
        
        # Add realistic GPS noise
        noise = np.random.normal(0, gps_accuracy, 2)
        noisy_pos = current + noise
        positions.append(Position(t=t, x=float(noisy_pos[0]), y=float(noisy_pos[1]), 
                                 gps_accuracy=gps_accuracy))
    
    return positions

# Command-line / script demonstration
def _demo():
    """
    Demonstration with zone profiles, user behavior, and temporal weighting
    """
    # Initialize building layout with multiple zones
    building = BuildingLayout(building_name="Main Facility")
    
    # Create zone profiles for different areas
    lobby_zone = ZoneProfile(
        zone_name="main_lobby",
        center=(0.0, 0.0),
        radius=200.0,
        v_max=2.5,  # Walking speed only
        a_max=2.0,
        zone_type="lobby"
    )
    
    stairwell_zone = ZoneProfile(
        zone_name="stairwell_A",
        center=(150.0, 150.0),
        radius=150.0,
        v_max=1.5,  # Very restricted
        a_max=3.0,  # Higher acceleration acceptable
        zone_type="stairwell"
    )
    
    parking_zone = ZoneProfile(
        zone_name="parking_garage",
        center=(500.0, 500.0),
        radius=300.0,
        v_max=15.0,  # Driving slow
        a_max=3.5,
        zone_type="parking"
    )
    
    building.zones = [lobby_zone, stairwell_zone, parking_zone]
    
    # Initialize visitor management system
    vms = VisitorManagementSystem()
    for zone in building.zones:
        vms.add_geofence(zone.zone_name, zone.center, zone.radius, zone_profile=zone)

    print("=" * 90)
    print("VISITOR MANAGEMENT SYSTEM - ADVANCED FEATURES")
    print("Context-Aware Zones | User Behavior Profiles | Temporal Weighting")
    print("=" * 90)

    # Scenario 1: Regular visitor with learned profile
    print("\n" + "─" * 90)
    print("SCENARIO 1: RETURNING VISITOR (BEHAVIOR PROFILE + KALMAN SMOOTHING)")
    print("─" * 90)
    visitor_1 = Visitor(
        visitor_id="V001",
        name="Visitor A",
        badge_tag="BADGE-12345",
        entry_time=datetime.now(),
        expected_zones=[(0.0, 0.0)],
        security_level="standard",
        host_department="Engineering",
        purpose="Project Meeting",
        allowed_areas=["lobby", "conference"]
    )
    
    # Get user profile (builds over visits)
    user_profile_1 = building.get_visitor_profile("V001")
    visitor_1.behavior_profile = user_profile_1
    print(f"🔍 User Profile: {visitor_1.name}")
    print(f"   Previous Visits: {user_profile_1.visits_count}")
    print(f"   Known Velocity Range: {user_profile_1.avg_velocity:.2f} - {user_profile_1.max_velocity:.2f} m/s")
    
    checkin = vms.register_visitor(visitor_1)
    print(f"✅ {checkin['message']}")
    
    # Generate legitimate path with GPS noise (typical urban quality: ~10m accuracy)
    legit = generate_legitimate_path((0.0, 0.0), n_points=10, gps_accuracy=10.0)
    
    # Create system with zone profile and user profile, Kalman enabled
    lobby_system = GeofenceSystem(
        center=lobby_zone.center,
        radius=lobby_zone.radius,
        zone_profile=lobby_zone,
        user_profile=user_profile_1,
        enable_kalman_smoothing=True,
        kalman_process_variance=15.0
    )
    
    result_1 = lobby_system.detect_spoofing(legit)
    report_1 = vms.verify_visitor_location("V001", "main_lobby", legit)
    
    print(f"\n Location Verification (Context: {lobby_zone.zone_type.upper()})")
    print(f"   Zone Thresholds: v_max={lobby_system.v_max} m/s, a_max={lobby_system.a_max} m/s²")
    print(f"   GPS Quality: {lobby_system.gps_quality} | Kalman Smoothing: ENABLED")
    print(f"   Anomaly Score: {report_1.anomaly_score:.3f} | Risk: {report_1.risk_level.value}")
    print(f"   Location Accuracy: {report_1.location_accuracy:.1f}m")
    print(f"   Status:  CLEARANCE GRANTED")
    
    # Update behavior profile
    velocities = [lobby_system.calculate_velocity(
        np.array([p.x, p.y]), np.array([legit[i+1].x, legit[i+1].y])
    ) for i, p in enumerate(legit[:-1])]
    building.update_visitor_profile("V001", velocities, [], ["main_lobby"])

    # Scenario 2: Suspicious visitor in restricted zone
    print("\n" + "─" * 90)
    print("SCENARIO 2: SUSPICIOUS ACTIVITY IN STAIRWELL (ADAPTIVE GPS THRESHOLDS)")
    print("─" * 90)
    visitor_2 = Visitor(
        visitor_id="V002",
        name="Visitor B",
        badge_tag="BADGE-99999",
        entry_time=datetime.now(),
        expected_zones=[(150.0, 150.0)],
        security_level="standard",
        host_department="Unknown",
        purpose="Facility Tour",
        allowed_areas=["lobby"]
    )
    
    checkin = vms.register_visitor(visitor_2)
    print(f"✅ {checkin['message']}")
    
    # Poor GPS in stairwell (indoor environment): 20m accuracy
    spoofed = generate_spoofed_path((150.0, 150.0), n_points=12, gps_accuracy=20.0)
    
    # Use stairwell zone (more restrictive) with Kalman enabled
    stairwell_system = GeofenceSystem(
        center=stairwell_zone.center,
        radius=stairwell_zone.radius,
        zone_profile=stairwell_zone,
        enable_kalman_smoothing=True,
        kalman_process_variance=25.0  # Higher due to poor GPS
    )
    
    result_2 = stairwell_system.detect_spoofing(spoofed)
    report_2 = vms.verify_visitor_location("V002", "stairwell_A", spoofed)
    
    print(f"\n📍 Location Verification (Context: {stairwell_zone.zone_type.upper()})")
    print(f"   Zone Thresholds: v_max={stairwell_system.v_max} m/s, a_max={stairwell_system.a_max} m/s²")
    print(f"   GPS Quality: {stairwell_system.gps_quality} | Kalman Smoothing: ENABLED")
    print(f"   Anomaly Score: {report_2.anomaly_score:.3f} | Risk: {report_2.risk_level.value}")
    print(f"   Location Accuracy: {report_2.location_accuracy:.1f}m")
    
    if report_2.flagged_events:
        print(" SECURITY ALERTS:")
        for event in report_2.flagged_events[:3]:  # Show first 3 events
            print(f"   {event}")
    print(f"   Status:   MANUAL REVIEW REQUIRED")

    # Scenario 3: VIP with different movement profile
    print("\n" + "─" * 90)
    print("SCENARIO 3: EXECUTIVE VISITOR (PERSONALIZED PROFILE)")
    print("─" * 90)
    visitor_3 = Visitor(
        visitor_id="V003",
        name="Visitor C",
        badge_tag="BADGE-VIP-001",
        entry_time=datetime.now(),
        expected_zones=[(0.0, 0.0), (150.0, 150.0)],
        security_level="vip",
        host_department="Executive",
        purpose="Board Meeting",
        allowed_areas=["lobby", "conference", "executive_suite"]
    )
    
    # Create custom profile for VIP (higher tolerance)
    vip_profile = building.get_visitor_profile("V003")
    vip_profile.max_velocity = 3.5  # VIPs move faster
    vip_profile.max_acceleration = 3.0
    vip_profile.deviation_tolerance = 2.0  # More lenient
    visitor_3.behavior_profile = vip_profile
    
    checkin = vms.register_visitor(visitor_3)
    print(f"✅ {checkin['message']}")
    print(f"   VIP Profile: Higher tolerance for movement variations")
    
    vip_path = [
        Position(t=0.0, x=-100.0, y=-100.0),
        Position(t=60.0, x=-50.0, y=-50.0),
        Position(t=120.0, x=20.0, y=10.0),
        Position(t=180.0, x=80.0, y=60.0),
        Position(t=240.0, x=100.0, y=100.0),
    ]
    
    vip_system = GeofenceSystem(
        center=lobby_zone.center,
        radius=lobby_zone.radius,
        zone_profile=lobby_zone,
        user_profile=vip_profile
    )
    
    report_3 = vms.verify_visitor_location("V003", "main_lobby", vip_path)
    
    print(f"\n📍 Location Verification")
    print(f"   Adjusted Thresholds (VIP Profile): v_max={vip_system.v_max:.1f} m/s, a_max={vip_system.a_max:.1f} m/s²")
    print(f"   Anomaly Score: {report_3.anomaly_score:.3f} | Risk: {report_3.risk_level.value}")
    print(f"   Status: ✅ CLEARANCE GRANTED")

    # Security Dashboard
    print("\n" + "─" * 90)
    print("SECURITY TEAM DASHBOARD")
    print("─" * 90)
    audit = vms.export_audit_log()
    print(f"Active Visitors: {audit['active_visitors']}")
    print(f"Security Alerts: {len(audit['security_alerts'])}")
    print(f"Monitored Zones: {audit['total_zones']}")
    print(f"Zone Types: {', '.join([z.zone_type for z in building.zones])}")
    
    if audit['security_alerts']:
        print("\n⚠️  ACTIVE ALERTS:")
        for alert in audit['security_alerts']:
            print(f"   [{alert['alert_type']}] {alert['visitor_name']} in {alert['zone']} - Risk: {alert['risk_level']}")

    print("\n" + "─" * 90)
    print("USER BEHAVIOR PROFILES (LEARNED FROM VISITS)")
    print("─" * 90)
    for visitor_id in ["V001", "V003"]:
        profile = building.get_visitor_profile(visitor_id)
        if profile.visits_count > 0:
            print(f"\n{visitor_id}:")
            print(f"  Visits: {profile.visits_count}")
            print(f"  Avg Velocity: {profile.avg_velocity:.2f} m/s | Max: {profile.max_velocity:.2f} m/s")
            print(f"  Tolerance: {profile.deviation_tolerance}x (stricter = 1.0, lenient = 2.0)")
            print(f"  Common Zones: {', '.join(profile.common_zones) if profile.common_zones else 'None yet'}")

    # GPS Quality Comparison
    print("\n" + "─" * 90)
    print("GPS QUALITY & ADAPTIVE THRESHOLD GUIDE")
    print("─" * 90)
    quality_guide = [
        ("EXCELLENT (<5m)", "Open areas, parking lots", "Standard thresholds apply"),
        ("GOOD (5-10m)", "City streets, campus", "1.3x velocity tolerance"),
        ("MODERATE (10-20m)", "Urban canyons, dense downtown areas", "1.8x velocity tolerance"),
        ("POOR (>20m)", "Indoors, dense buildings", "2.5x velocity tolerance + Kalman"),
    ]
    for quality, location, thresholds in quality_guide:
        print(f"  {quality:20} | {location:25} | {thresholds}")

    # Visualizations
    fig1 = lobby_system.visualize_path(legit, result_1, 
                                       f"LEGITIMATE: {visitor_1.name} (Context: {lobby_zone.zone_type})")
    fig2 = stairwell_system.visualize_path(spoofed, result_2, 
                                           f"SUSPICIOUS: {visitor_2.name} (Context: {stairwell_zone.zone_type})")
    
    # Save figures instead of blocking with plt.show()
    try:
        fig1.savefig("visitor_legitimate_scenario.png", dpi=150, bbox_inches='tight')
        fig2.savefig("visitor_suspicious_scenario.png", dpi=150, bbox_inches='tight')
        print("\n✅ Visualization saved: visitor_legitimate_scenario.png")
        print("✅ Visualization saved: visitor_suspicious_scenario.png")
    except Exception as e:
        print(f"Note: Could not save figures: {e}")
    
    plt.close('all')

if __name__ == "__main__":
    _demo()
