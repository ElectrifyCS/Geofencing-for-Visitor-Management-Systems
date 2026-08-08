"""
Integration layer: the VisitorManagementSystem class used day-to-day.
"""
from typing import List, Tuple, Optional
from datetime import datetime

from .models import Visitor, Position, VisitorLocationReport, VisitorRisk, ZoneProfile, UserBehaviorProfile
from .geofence import GeofenceSystem
from .badge import BadgeSystem, BadgeEvent


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

