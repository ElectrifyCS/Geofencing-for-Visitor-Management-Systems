"""
Badge/RFID tracking and correlation against reported GPS position.
"""
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
from datetime import datetime
import numpy as np


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

