"""
Geofencing for Visitor Management Systems

A production-grade GPS spoofing detection system for visitor management that uses:
  - 2D constant-velocity Kalman filtering for position smoothing
  - Adaptive thresholds (logarithmic convergence)
  - Badge/RFID correlation for multi-factor anomaly detection
  - 3D floor-aware zone containment (ZoneHierarchy)
  - Anchor-based multilateration (UWB/BLE alternative to GPS)
  - Real-time policy enforcement (escort tethering, dwell monitoring, tag drops)
  - Emergency response (automated mustering, proximity dispatch)
  - Hardware lifecycle management (tag provisioning, battery health, anti-passback)

Core modules:
  - kalman.py: Kalman filtering + adaptive thresholds
  - models.py: Data structures (Visitor, Position, ZoneProfile, etc.)
  - badge.py: Badge/RFID correlation and GPS risk scoring
  - geofence.py: Spoofing detection engine
  - vms.py: Day-to-day visitor management system API
  - synthetic.py: Synthetic path generators for testing
  - floorplan.py: Floor plan calibration & 3D zone hierarchy
  - multilateration.py: UWB/BLE anchor-based positioning
  - tracking.py: Escort tethering, dwell monitoring, heading computation
  - incident.py: Emergency mustering and proximity dispatch
  - tag_lifecycle.py: Tag provisioning, battery health, anti-passback
  - integrated.py: Master orchestration layer wiring everything together

Mathematical foundations (IB AA HL):
  - Complex numbers (CoordinateCalibrator similarity transforms)
  - Linear algebra (Kalman filter state-space, multilateration least-squares)
  - Statistics (robust median/MAD, exponential forgetting)
  - Calculus (derivatives for velocity/acceleration, log convergence)
  - Coordinate geometry (point-in-polygon, 3D containment)
"""

from .kalman import (
    compute_log_convergence_factor,
    KalmanFilter2DConstantVelocity,
    get_adaptive_thresholds,
)
from .models import (
    VisitorRisk, Position, Visitor, VisitorLocationReport, DetectionResult,
    ZoneProfile, UserBehaviorProfile, BuildingLayout, TransitionGraph,
)
from .badge import BadgeEvent, BadgeGPSCorrelation, BadgeSystem
from .geofence import smooth_and_derive, GeofenceSystem
from .vms import VisitorManagementSystem
from .synthetic import (
    generate_legitimate_path,
    generate_spoofed_path,
    generate_extreme_spoofed_path,
)
# Prototype modules (now wired in)
from .floorplan import CoordinateCalibrator, ZoneHierarchy
from .multilateration import Anchor, Ranging, multilaterate, rssi_to_distance
from .tracking import (
    PositionSample,
    TetherAlert,
    TetherMonitor,
    DwellBaseline,
    DwellAlert,
    DwellMonitor,
    Heading,
    compute_heading,
    intent_angle_deg,
)
from .incident import MusterZoneCount, MusterReport, DispatchCandidate, muster, nearest_guards
from .tag_lifecycle import (
    TagAssignment,
    TagRegistry,
    BatteryReading,
    BatteryPrediction,
    predict_battery,
    SpeedAnomaly,
    check_speed_anomaly,
    TagDropAlert,
    TagDropDetector,
)
from .integrated import IntegratedVisitorManagement

__all__ = [
    # Kalman filtering
    "compute_log_convergence_factor",
    "KalmanFilter2DConstantVelocity",
    "get_adaptive_thresholds",
    # Core data models
    "VisitorRisk", "Position", "Visitor", "VisitorLocationReport", "DetectionResult",
    "ZoneProfile", "UserBehaviorProfile", "BuildingLayout", "TransitionGraph",
    # Badge system
    "BadgeEvent", "BadgeGPSCorrelation", "BadgeSystem",
    # Geofencing engine
    "smooth_and_derive", "GeofenceSystem",
    # Visitor management system
    "VisitorManagementSystem",
    # Synthetic testing
    "generate_legitimate_path", "generate_spoofed_path", "generate_extreme_spoofed_path",
    # Floor planning & zones
    "CoordinateCalibrator", "ZoneHierarchy",
    # Multilateration (UWB/BLE)
    "Anchor", "Ranging", "multilaterate", "rssi_to_distance",
    # Tracking & policy enforcement
    "PositionSample", "TetherAlert", "TetherMonitor", "DwellBaseline", "DwellAlert",
    "DwellMonitor", "Heading", "compute_heading", "intent_angle_deg",
    # Incident response
    "MusterZoneCount", "MusterReport", "DispatchCandidate", "muster", "nearest_guards",
    # Tag lifecycle
    "TagAssignment", "TagRegistry", "BatteryReading", "BatteryPrediction", "predict_battery",
    "SpeedAnomaly", "check_speed_anomaly", "TagDropAlert", "TagDropDetector",
    # Master integration
    "IntegratedVisitorManagement",
]

__version__ = "1.0.0"
__author__ = "ElectrifyCS"
__description__ = "Location-based verification system for visitor management with GPS spoofing detection"
