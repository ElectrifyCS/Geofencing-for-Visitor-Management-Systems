"""
Geofencing for Visitor Management Systems

A math-driven location-verification library for visitor management. It
flags physically implausible movement (GPS/RFID spoofing), enforces zone
rules, and produces an auditable event stream, using:
  - 2D constant-velocity Kalman filtering for position smoothing
  - Adaptive thresholds (logarithmic convergence)
  - Badge/RFID correlation for multi-factor anomaly detection
  - 3D floor-aware zone containment (ZoneHierarchy)
  - Anchor-based multilateration (UWB/BLE alternative to GPS), with
    multipath outlier rejection and a dilution-of-precision signal
  - Real-time policy enforcement (escort tethering, dwell monitoring,
    debounced zone locks, tag drops)
  - Time-windowed permits and privilege bound to the physical tag
  - Elevator/vertical tracking with beacon identity locking
  - Emergency response (automated mustering, proximity dispatch)
  - Hardware lifecycle management (tag provisioning, battery health,
    anti-passback, reconnect warm-up)
  - A unified EventLog with severity computed from zone risk
  - Restart-surviving state (StateStore; ZoneLockTracker so far)

Core modules:
  - kalman.py: Kalman filtering + adaptive thresholds
  - models.py: Data structures (Visitor, Position, ZoneProfile, etc.)
  - badge.py: Badge/RFID correlation and GPS risk scoring
  - geofence.py: Spoofing detection engine
  - vms.py: Day-to-day visitor management system API
  - synthetic.py: Synthetic path generators for testing
  - floorplan.py: Floor plan calibration & 3D zone hierarchy
  - multilateration.py: UWB/BLE anchor-based positioning
  - tracking.py: Escort tethering, dwell monitoring, zone lock, heading
  - incident.py: Emergency mustering, presence (TTL) tracking, dispatch
  - tag_lifecycle.py: Tag registry, battery health, anti-passback, warm-up
  - elevator_tracking.py: 1D elevator Kalman + beacon identity lock
  - permits.py: Time-windowed zone/floor authorization
  - event_log.py: Unified event sink with subscribe()/query()
  - state_store.py: In-memory and Redis-backed state persistence
  - integrated.py: Master orchestration layer wiring everything together

Optional dependencies (the core package needs only numpy):
  - matplotlib: GeofenceSystem.visualize_path()   -> geofencing-vms[plot]
  - redis:      RedisStateStore's real connection  -> geofencing-vms[redis]

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
from .floorplan import CoordinateCalibrator, ZoneHierarchy
from .multilateration import (
    Anchor,
    Ranging,
    MultilaterationResult,
    multilaterate,
    rssi_to_distance,
)
from .tracking import (
    PositionSample,
    TetherAlert,
    TetherMonitor,
    DwellBaseline,
    DwellAlert,
    DwellMonitor,
    ZoneLockTracker,
    Heading,
    compute_heading,
    intent_angle_deg,
    FEET_TO_METRES,
)
from .incident import (
    MusterZoneCount,
    MusterReport,
    ExitEvent,
    PresenceTracker,
    DispatchCandidate,
    position_confidence,
    muster,
    nearest_guards,
)
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
    ReliabilityWarmup,
)
from .elevator_tracking import (
    FloorBeacon,
    BeaconRegistry,
    BeaconLockTracker,
    ElevatorKalman1D,
    resolve_floor,
    correct_with_identified_beacon,
    check_stuck_between_floors,
    # tag_lifecycle and elevator_tracking both define check_speed_anomaly
    # (visitor anti-passback vs. elevator car speed). The visitor one keeps
    # the plain name above since that's what was already exported; the
    # elevator one is aliased so both stay reachable from the top level.
    check_speed_anomaly as check_elevator_speed_anomaly,
)
from .event_log import Severity, Event, EventLog, compute_severity
from .permits import Permit, PermitRegistry, check_authorization
from .state_store import StateStore, InMemoryStateStore, RedisStateStore
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
    "Anchor", "Ranging", "MultilaterationResult", "multilaterate", "rssi_to_distance",
    # Tracking & policy enforcement
    "PositionSample", "TetherAlert", "TetherMonitor", "DwellBaseline", "DwellAlert",
    "DwellMonitor", "ZoneLockTracker", "Heading", "compute_heading", "intent_angle_deg",
    "FEET_TO_METRES",
    # Incident response
    "MusterZoneCount", "MusterReport", "ExitEvent", "PresenceTracker",
    "DispatchCandidate", "position_confidence", "muster", "nearest_guards",
    # Tag lifecycle
    "TagAssignment", "TagRegistry", "BatteryReading", "BatteryPrediction", "predict_battery",
    "SpeedAnomaly", "check_speed_anomaly", "TagDropAlert", "TagDropDetector",
    "ReliabilityWarmup",
    # Elevator / vertical tracking
    "FloorBeacon", "BeaconRegistry", "BeaconLockTracker", "ElevatorKalman1D",
    "resolve_floor", "correct_with_identified_beacon", "check_stuck_between_floors",
    "check_elevator_speed_anomaly",
    # Event log
    "Severity", "Event", "EventLog", "compute_severity",
    # Permits
    "Permit", "PermitRegistry", "check_authorization",
    # State persistence
    "StateStore", "InMemoryStateStore", "RedisStateStore",
    # Master integration
    "IntegratedVisitorManagement",
]

# Single source of truth for the package version: pyproject.toml reads this
# line (see [tool.hatch.version]), so the two can never disagree.
__version__ = "0.1.0"
__author__ = "ElectrifyCS"
__description__ = "Location-based verification system for visitor management with GPS spoofing detection"
