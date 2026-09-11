"""
Visitor Geofencing Security System – upgraded core.

Math-driven location verification:
  - 2-D constant-velocity Kalman filter
  - Logarithmic convergence c(n)
  - Uncertainty-aware adaptive thresholds τ(c, σ)
  - Robust (median/MAD) behaviour profiles with exponential forgetting
  - Polygonal zones + directed transition graph
  - Badge risk fused into the anomaly score
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

__all__ = [
    "compute_log_convergence_factor",
    "KalmanFilter2DConstantVelocity",
    "get_adaptive_thresholds",
    "VisitorRisk", "Position", "Visitor", "VisitorLocationReport", "DetectionResult",
    "ZoneProfile", "UserBehaviorProfile", "BuildingLayout", "TransitionGraph",
    "BadgeEvent", "BadgeGPSCorrelation", "BadgeSystem",
    "smooth_and_derive", "GeofenceSystem",
    "VisitorManagementSystem",
    "generate_legitimate_path", "generate_spoofed_path", "generate_extreme_spoofed_path",
]
