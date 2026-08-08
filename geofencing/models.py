"""
Data models: risk levels, positions, visitors, zones, and behavior profiles.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple, Optional
from enum import Enum
from datetime import datetime
import numpy as np

from .kalman import compute_log_convergence_factor


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

