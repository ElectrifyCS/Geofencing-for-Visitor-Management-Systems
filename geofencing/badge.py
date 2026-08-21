"""
Badge / RFID tracking and GPS correlation.
Risk score is designed to be fused directly into the main anomaly score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Optional
from datetime import datetime
import numpy as np


@dataclass
class BadgeEvent:
    timestamp: datetime
    visitor_id: str
    badge_tag: str
    reader_location: str
    reader_position: Tuple[float, float]
    gps_position: Optional[Tuple[float, float]] = None
    event_type: str = "entry"
    distance_to_reader: float = 0.0

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "visitor_id": self.visitor_id,
            "badge_tag": self.badge_tag,
            "reader_location": self.reader_location,
            "event_type": self.event_type,
            "distance_to_reader": round(self.distance_to_reader, 1),
        }


@dataclass
class BadgeGPSCorrelation:
    badge_event: BadgeEvent
    gps_distance_error: float
    correlation_status: str
    confidence: float
    alert_reason: Optional[str] = None
    risk_score: float = 0.0

    def is_anomalous(self) -> bool:
        return self.correlation_status in ("MAJOR_DEVIATION", "SPOOFING_ALERT")


class BadgeSystem:
    def __init__(self):
        self.badge_events: List[BadgeEvent] = []
        self.readers: dict = {}
        self.visitor_badge_map: dict = {}

    def add_reader(self, reader_id: str, reader_location: str, position: Tuple[float, float]):
        self.readers[reader_id] = {
            "location": reader_location,
            "position": position,
            "registered_at": datetime.now(),
        }

    def record_badge_event(self, badge_event: BadgeEvent):
        self.badge_events.append(badge_event)
        self.visitor_badge_map[badge_event.visitor_id] = badge_event.badge_tag

    def get_visitor_badge_events(self, visitor_id: str, time_window: int = 3600) -> List[BadgeEvent]:
        cutoff = datetime.now().timestamp() - time_window
        return [
            e for e in self.badge_events
            if e.visitor_id == visitor_id and e.timestamp.timestamp() > cutoff
        ]

    def correlate_badge_gps(
        self,
        badge_event: BadgeEvent,
        match_threshold: float = 50.0,
        small_threshold: float = 200.0,
        major_threshold: float = 500.0,
    ) -> BadgeGPSCorrelation:
        """
        Distance-based correlation.
        Thresholds are configurable so each facility (CBK, Parklands, …)
        can tune them to its GPS environment.
        """
        if badge_event.gps_position is None:
            return BadgeGPSCorrelation(
                badge_event=badge_event,
                gps_distance_error=0.0,
                correlation_status="UNKNOWN",
                confidence=0.0,
                alert_reason="No GPS data available",
                risk_score=0.0,
            )

        gps_pos = np.asarray(badge_event.gps_position, dtype=float)
        reader_pos = np.asarray(badge_event.reader_position, dtype=float)
        distance = float(np.linalg.norm(gps_pos - reader_pos))
        badge_event.distance_to_reader = distance

        if distance < match_threshold:
            status, confidence, risk = "MATCH", 0.95, 0.0
            reason = None
        elif distance < small_threshold:
            status, confidence, risk = "SMALL_DEVIATION", 0.70, 0.15
            reason = None
        elif distance < major_threshold:
            status, confidence, risk = "MAJOR_DEVIATION", 0.40, 0.55
            reason = f"Badge at {badge_event.reader_location}, GPS {distance:.0f} m away"
        else:
            status, confidence, risk = "SPOOFING_ALERT", 0.10, 0.92
            reason = f"CRITICAL: Badge says {badge_event.reader_location}, GPS {distance:.0f} m away"

        return BadgeGPSCorrelation(
            badge_event=badge_event,
            gps_distance_error=distance,
            correlation_status=status,
            confidence=confidence,
            alert_reason=reason,
            risk_score=risk,
        )
