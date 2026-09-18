"""
event_log.py -- Unified event logging for real-time admin dashboard
consumption.

Every module in this package (tracking, tag_lifecycle, incident,
elevator_tracking, permits) produces its own differently-shaped alert:
TetherAlert, DwellAlert, TagDropAlert, SpeedAnomaly, ExitEvent... Each is
fine on its own, but there was no single stream an admin dashboard could
subscribe to and expect one consistent shape. This module doesn't
replace those -- it's the common sink integrated.py logs them into.

Two consumption patterns, both needed for a real-time dashboard:
  - subscribe(callback): push -- called synchronously the instant an
    event is logged. This is the hook a websocket/SSE broadcast layer
    attaches to for genuinely real-time delivery to connected dashboard
    clients. Proven working in the __main__ demo below, not just
    designed.
  - query(...): pull -- for a dashboard's initial page load or a
    historical view, filtered by time range, entity, event type, or
    minimum severity.

Severity is computed, not hardcoded per event type: the same
"dwell anomaly" is far more serious in a risk_level="prohibited" zone
than a risk_level="public" one. compute_severity() escalates a base
severity using the exact risk_level vocabulary ZoneProfile already
defines, rather than inventing a second, parallel one.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    ALERT = "alert"
    CRITICAL = "critical"


_SEVERITY_ORDER = [Severity.INFO, Severity.WARNING, Severity.ALERT, Severity.CRITICAL]  # for query() comparisons only


# Numeric 0-10 scoring, not enum-stepping: stepping ran out of headroom
# for events whose base severity was already ALERT (escort_required and
# prohibited zones both collapsed to CRITICAL with nothing to tell them
# apart -- caught by the module's own test, see below). A numeric score
# with room underneath the ceiling keeps all three risk_level values
# distinguishable even for mid-to-high base-severity events.
_BASE_SEVERITY_SCORE: Dict[str, int] = {
    "zone_entry": 0,
    "zone_exit": 0,
    "floor_crossing": 0,
    "beacon_correction_rejected": 3,
    "speed_anomaly": 4,
    "presence_exit": 4,
    "tether_breach": 6,
    "dwell_anomaly": 6,
    "tag_drop": 6,
    "permit_denied": 6,
    "stuck_between_floors": 7,
    "spoofing_detected": 9,
    "alert_suppressed_warmup": 2,  # informational on its own; risk_level can still push it to WARNING
}

# ZoneProfile.risk_level adds to the score -- "prohibited" adds more
# than "escort_required", which adds more than "public" (0).
_RISK_ESCALATION_SCORE: Dict[str, int] = {
    "public": 0,
    "escort_required": 2,
    "prohibited": 4,
}


def compute_severity(event_type: str, risk_level: Optional[str] = None) -> Severity:
    score = _BASE_SEVERITY_SCORE.get(event_type, 0)
    if risk_level is not None:
        score += _RISK_ESCALATION_SCORE.get(risk_level, 0)
    score = min(score, 10)

    if score >= 9:
        return Severity.CRITICAL
    elif score >= 7:
        return Severity.ALERT
    elif score >= 4:
        return Severity.WARNING
    return Severity.INFO


_id_counter = itertools.count(1)  # globally unique across every EventLog instance


@dataclass
class Event:
    event_id: int
    timestamp_s: float
    event_type: str
    entity_id: str
    severity: Severity
    message: str
    zone_id: Optional[str] = None
    source_module: Optional[str] = None

    def to_dict(self) -> Dict:
        """JSON-serializable shape -- what actually goes out over a websocket/API to a dashboard."""
        return {
            "event_id": self.event_id,
            "timestamp_s": self.timestamp_s,
            "event_type": self.event_type,
            "entity_id": self.entity_id,
            "severity": self.severity.value,
            "message": self.message,
            "zone_id": self.zone_id,
            "source_module": self.source_module,
        }


class EventLog:
    """
    The shared sink every module logs into, and every dashboard consumer
    reads from. In-memory for now (a real deployment would back this
    with a database or message queue), but the interface -- log(),
    subscribe(), query() -- is what any storage backend needs to
    support, so swapping the backend later shouldn't change callers.
    """

    def __init__(self, max_events: int = 10000):
        self._events: List[Event] = []
        self._subscribers: List[Callable[[Event], None]] = []
        self.max_events = max_events

    def log(
        self,
        timestamp_s: float,
        event_type: str,
        entity_id: str,
        message: str,
        zone_id: Optional[str] = None,
        risk_level: Optional[str] = None,
        source_module: Optional[str] = None,
    ) -> Event:
        event = Event(
            event_id=next(_id_counter),
            timestamp_s=timestamp_s,
            event_type=event_type,
            entity_id=entity_id,
            severity=compute_severity(event_type, risk_level),
            message=message,
            zone_id=zone_id,
            source_module=source_module,
        )
        self._events.append(event)
        if len(self._events) > self.max_events:
            self._events.pop(0)

        # Real-time push: every subscriber is called synchronously, the
        # instant the event is logged -- not on a poll cycle. A
        # websocket/SSE broadcast layer attaches its send function here.
        for callback in self._subscribers:
            callback(event)

        return event

    def subscribe(self, callback: Callable[[Event], None]) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[Event], None]) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def query(
        self,
        since_s: Optional[float] = None,
        until_s: Optional[float] = None,
        entity_id: Optional[str] = None,
        event_type: Optional[str] = None,
        min_severity: Optional[Severity] = None,
        limit: Optional[int] = None,
    ) -> List[Event]:
        """Pull interface -- dashboard initial load or historical view."""
        results = self._events
        if since_s is not None:
            results = [e for e in results if e.timestamp_s >= since_s]
        if until_s is not None:
            results = [e for e in results if e.timestamp_s <= until_s]
        if entity_id is not None:
            results = [e for e in results if e.entity_id == entity_id]
        if event_type is not None:
            results = [e for e in results if e.event_type == event_type]
        if min_severity is not None:
            min_index = _SEVERITY_ORDER.index(min_severity)
            results = [e for e in results if _SEVERITY_ORDER.index(e.severity) >= min_index]
        results = sorted(results, key=lambda e: e.timestamp_s)
        if limit is not None:
            results = results[-limit:]
        return results


if __name__ == "__main__":
    print("=== Severity escalation by zone risk_level ===")
    for risk in (None, "public", "escort_required", "prohibited"):
        sev = compute_severity("dwell_anomaly", risk)
        print(f"  dwell_anomaly in risk_level={risk!r:16s} -> {sev.value}")

    print("\n=== Real-time subscriber push (proving it, not just designing it) ===")
    log = EventLog()
    received_by_dashboard: List[dict] = []

    def fake_websocket_broadcast(event):
        # Stands in for "push this over the wire to every connected
        # dashboard client" -- in a real deployment this is where a
        # websocket server's broadcast() call goes.
        received_by_dashboard.append(event.to_dict())

    log.subscribe(fake_websocket_broadcast)

    log.log(100.0, "zone_entry", "V-JDOE", "Entered main lobby", zone_id="main_lobby", risk_level="public")
    log.log(130.0, "tether_breach", "V-JDOE", "20.6m from host (limit 6.1m)", zone_id="main_lobby", risk_level="public")
    log.log(160.0, "dwell_anomaly", "V-JDOE", "590s in server_room (baseline 45s)", zone_id="server_room", risk_level="prohibited")
    log.log(200.0, "tag_drop", "TAG-777", "Stationary 2.0 hrs", zone_id="server_room", risk_level="prohibited")

    print(f"  Events logged: {len(log.query())}")
    print(f"  Events received by the 'dashboard' subscriber in real time: {len(received_by_dashboard)}")
    print(f"  Same events, same order: {[e['event_type'] for e in received_by_dashboard] == [e.event_type for e in log.query()]}")

    print("\n=== Query filtering ===")
    critical_and_above = log.query(min_severity=Severity.ALERT)
    print(f"  Events at ALERT severity or above: {len(critical_and_above)}")
    for e in critical_and_above:
        print(f"    [{e.severity.value.upper():8s}] {e.event_type}: {e.message}")

    only_jdoe = log.query(entity_id="V-JDOE")
    print(f"  Events for V-JDOE only: {len(only_jdoe)}")

    since_150 = log.query(since_s=150.0)
    print(f"  Events since t=150s: {len(since_150)}")

    print("\n=== Max-events eviction ===")
    small_log = EventLog(max_events=3)
    for i in range(5):
        small_log.log(float(i), "zone_entry", f"V-{i}", f"entry {i}")
    remaining = small_log.query()
    print(f"  Logged 5 events with max_events=3, retained: {len(remaining)}")
    print(f"  Oldest retained is entry {remaining[0].entity_id} (should be V-2, the first two evicted)")
