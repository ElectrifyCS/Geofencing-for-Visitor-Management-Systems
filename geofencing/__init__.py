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
from .kalman import compute_log_convergence_factor, KalmanFilter1D, get_adaptive_thresholds
from .models import (
    VisitorRisk, Position, Visitor, VisitorLocationReport, DetectionResult,
    ZoneProfile, UserBehaviorProfile, BuildingLayout,
)
from .badge import BadgeEvent, BadgeGPSCorrelation, BadgeSystem
from .geofence import smooth_gps_positions, GeofenceSystem
from .vms import VisitorManagementSystem
from .synthetic import generate_legitimate_path, generate_spoofed_path, generate_extreme_spoofed_path

__all__ = [
    "compute_log_convergence_factor", "KalmanFilter1D", "get_adaptive_thresholds",
    "VisitorRisk", "Position", "Visitor", "VisitorLocationReport", "DetectionResult",
    "ZoneProfile", "UserBehaviorProfile", "BuildingLayout",
    "BadgeEvent", "BadgeGPSCorrelation", "BadgeSystem",
    "smooth_gps_positions", "GeofenceSystem",
    "VisitorManagementSystem",
    "generate_legitimate_path", "generate_spoofed_path", "generate_extreme_spoofed_path",
]
