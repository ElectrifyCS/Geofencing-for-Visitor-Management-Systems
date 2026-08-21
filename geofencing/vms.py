"""
Integration layer – day-to-day API used by the existing VMS.
"""

from __future__ import annotations

from typing import List, Tuple, Optional, Dict
from datetime import datetime

from .models import (
    Visitor, Position, VisitorLocationReport, VisitorRisk,
    ZoneProfile, UserBehaviorProfile, BuildingLayout, TransitionGraph,
)
from .geofence import GeofenceSystem
from .badge import BadgeSystem, BadgeEvent, BadgeGPSCorrelation


class VisitorManagementSystem:
    def __init__(self):
        self.active_visitors: Dict[str, Visitor] = {}
        self.location_history: Dict[str, List[VisitorLocationReport]] = {}
        self.geofence_systems: Dict[str, GeofenceSystem] = {}
        self.security_alerts: List[dict] = []
        self.badge_system = BadgeSystem()
        self.badge_gps_correlations: Dict[str, List[BadgeGPSCorrelation]] = {}
        self.building: Optional[BuildingLayout] = None
        self._last_zone: Dict[str, str] = {}          # visitor_id → last known zone

    # ------------------------------------------------------------------
    # Facility setup
    # ------------------------------------------------------------------
    def set_building_layout(self, layout: BuildingLayout):
        self.building = layout
        for zone in layout.zones:
            self.add_geofence(
                zone.zone_name,
                zone.center,
                zone.radius,
                zone_profile=zone,
            )

    def add_geofence(
        self,
        zone_name: str,
        center: Tuple[float, float],
        radius: float,
        zone_profile: Optional[ZoneProfile] = None,
        user_profile: Optional[UserBehaviorProfile] = None,
    ):
        tg = self.building.transition_graph if self.building else None
        self.geofence_systems[zone_name] = GeofenceSystem(
            center,
            radius,
            zone_profile=zone_profile,
            user_profile=user_profile,
            transition_graph=tg,
        )
        resp = {"status": "SUCCESS", "zone": zone_name, "radius_m": radius}
        if zone_profile:
            resp["zone_type"] = zone_profile.zone_type
            resp["polygonal"] = zone_profile.is_polygonal()
        return resp

    # ------------------------------------------------------------------
    # Visitor lifecycle
    # ------------------------------------------------------------------
    def register_visitor(self, visitor: Visitor) -> dict:
        self.active_visitors[visitor.visitor_id] = visitor
        self.location_history[visitor.visitor_id] = []
        if self.building and visitor.behavior_profile is None:
            visitor.behavior_profile = self.building.get_visitor_profile(visitor.visitor_id)
        return {
            "status": "SUCCESS",
            "message": f"Visitor {visitor.name} checked in",
            "badge_tag": visitor.badge_tag,
            "timestamp": datetime.now().isoformat(),
            "allowed_areas": visitor.allowed_areas,
        }

    def check_out_visitor(self, visitor_id: str) -> dict:
        if visitor_id not in self.active_visitors:
            return {"status": "ERROR", "message": "Visitor not found"}
        visitor = self.active_visitors[visitor_id]
        visitor.exit_time = datetime.now()
        duration = (visitor.exit_time - visitor.entry_time).total_seconds() / 60
        report = {
            "visitor_id": visitor_id,
            "visitor_name": visitor.name,
            "entry_time": visitor.entry_time.isoformat(),
            "exit_time": visitor.exit_time.isoformat(),
            "duration_minutes": round(duration, 1),
            "anomalies_detected": len(self.location_history.get(visitor_id, [])),
        }
        del self.active_visitors[visitor_id]
        self._last_zone.pop(visitor_id, None)
        return {"status": "SUCCESS", "checkout_report": report}

    # ------------------------------------------------------------------
    # Core verification (badge risk fused automatically)
    # ------------------------------------------------------------------
    def verify_visitor_location(
        self,
        visitor_id: str,
        zone_name: str,
        positions: List[Position],
        badge_risk: Optional[float] = None,
    ) -> Optional[VisitorLocationReport]:
        if visitor_id not in self.active_visitors or zone_name not in self.geofence_systems:
            return None

        visitor = self.active_visitors[visitor_id]
        geofence = self.geofence_systems[zone_name]

        # Pull latest badge risk if not supplied
        if badge_risk is None:
            badge_risk = self._latest_badge_risk(visitor_id)

        # Attach behaviour profile if available
        if visitor.behavior_profile and geofence.user_profile is None:
            effective = GeofenceSystem(
                tuple(geofence.center),
                geofence.radius,
                delta_t=geofence.delta_t,
                zone_profile=geofence.zone_profile,
                user_profile=visitor.behavior_profile,
                enable_kalman_smoothing=geofence.enable_kalman_smoothing,
                process_noise_std=geofence.process_noise_std,
                enable_log_convergence=geofence.enable_log_convergence,
                transition_graph=geofence.transition_graph,
                previous_zone=self._last_zone.get(visitor_id),
            )
        else:
            effective = geofence
            effective.previous_zone = self._last_zone.get(visitor_id)

        report = effective.verify_visitor(
            visitor,
            positions,
            badge_risk=badge_risk,
            previous_zone=self._last_zone.get(visitor_id),
        )

        self.location_history[visitor_id].append(report)
        self._last_zone[visitor_id] = zone_name

        # Update robust profile (only fully trusted observations shrink the MAD)
        trusted = report.risk_level in (VisitorRisk.TRUSTED, VisitorRisk.LOW)
        if self.building and positions:
            # crude speed samples for profile update
            speeds = []
            for i in range(len(positions) - 1):
                dt = max(positions[i + 1].t - positions[i].t, 1e-3)
                d = ((positions[i + 1].x - positions[i].x) ** 2 +
                     (positions[i + 1].y - positions[i].y) ** 2) ** 0.5
                speeds.append(d / dt)
            self.building.update_visitor_profile(
                visitor_id, speeds, [], [zone_name], trusted=trusted
            )

        if report.is_spoofed or report.risk_level in (VisitorRisk.CRITICAL, VisitorRisk.HIGH):
            self.security_alerts.append({
                "timestamp": datetime.now().isoformat(),
                "visitor_id": visitor_id,
                "visitor_name": visitor.name,
                "zone": zone_name,
                "risk_level": report.risk_level.value,
                "anomaly_score": report.anomaly_score,
                "position_uncertainty": report.position_uncertainty,
                "badge_risk": report.badge_risk_contribution,
                "alert_type": "SPOOFING" if report.is_spoofed else "RISK",
            })

        return report

    def _latest_badge_risk(self, visitor_id: str) -> float:
        corrs = self.badge_gps_correlations.get(visitor_id, [])
        if not corrs:
            return 0.0
        return corrs[-1].risk_score

    # ------------------------------------------------------------------
    # Badge events
    # ------------------------------------------------------------------
    def record_badge_event(self, visitor_id: str, badge_event: BadgeEvent) -> dict:
        self.badge_system.record_badge_event(badge_event)
        if visitor_id not in self.badge_gps_correlations:
            self.badge_gps_correlations[visitor_id] = []

        correlation = self.badge_system.correlate_badge_gps(badge_event)
        self.badge_gps_correlations[visitor_id].append(correlation)

        if correlation.is_anomalous():
            self.security_alerts.append({
                "timestamp": datetime.now().isoformat(),
                "visitor_id": visitor_id,
                "alert_type": "BADGE_GPS_MISMATCH",
                "badge_location": badge_event.reader_location,
                "gps_distance_error": round(correlation.gps_distance_error, 1),
                "correlation_status": correlation.correlation_status,
                "risk_score": correlation.risk_score,
                "reason": correlation.alert_reason,
            })

        return {
            "status": "SUCCESS",
            "badge_event_recorded": badge_event.to_dict(),
            "correlation": correlation.correlation_status,
            "gps_distance_error": round(correlation.gps_distance_error, 1),
            "risk_score": correlation.risk_score,
        }

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def get_visitor_summary(self, visitor_id: str) -> Optional[dict]:
        history = self.location_history.get(visitor_id)
        if history is None:
            return None
        if not history:
            return {"visitor_id": visitor_id, "events": 0, "alerts": 0}
        alerts = [h for h in history if h.risk_level in (VisitorRisk.HIGH, VisitorRisk.CRITICAL)]
        return {
            "visitor_id": visitor_id,
            "total_checks": len(history),
            "alerts_count": len(alerts),
            "max_anomaly_score": max(h.anomaly_score for h in history),
            "spoofing_detected": any(h.is_spoofed for h in history),
            "avg_uncertainty": round(
                sum(h.position_uncertainty for h in history) / len(history), 1
            ),
        }

    def export_audit_log(self) -> dict:
        return {
            "timestamp": datetime.now().isoformat(),
            "active_visitors": len(self.active_visitors),
            "security_alerts": self.security_alerts,
            "total_zones": len(self.geofence_systems),
        }
