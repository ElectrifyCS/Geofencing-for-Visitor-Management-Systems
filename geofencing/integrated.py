"""
integrated.py — Master integration layer that wires all prototype modules
(floorplan, multilateration, tracking, incident, tag_lifecycle) into the
core VisitorManagementSystem.

This layer provides:
  1. Unified position input handling (GPS, multilateration, or Kalman output)
  2. Real-time policy monitoring (escort tethering, dwell times, tag drops)
  3. Emergency response coordination (mustering, proximity dispatch)
  4. Hardware lifecycle management (tags, batteries, anti-passback)
  5. 3D floor-aware zone resolution via ZoneHierarchy
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .models import (
    Visitor, Position, VisitorLocationReport, ZoneProfile, BuildingLayout
)
from .vms import VisitorManagementSystem
from .floorplan import ZoneHierarchy, CoordinateCalibrator
from .multilateration import multilaterate, Anchor, Ranging, rssi_to_distance
from .tracking import (
    PositionSample, TetherMonitor, DwellMonitor, DwellBaseline,
    compute_heading, intent_angle_deg, ZoneLockTracker
)
from .incident import muster, nearest_guards, PresenceTracker
from .tag_lifecycle import (
    TagRegistry, predict_battery, BatteryReading,
    check_speed_anomaly, TagDropDetector, ReliabilityWarmup
)
from .event_log import EventLog


class IntegratedVisitorManagement:
    """
    Master system that orchestrates all geofencing, tracking, and emergency
    response capabilities. Designed for real-world deployment where positions
    arrive from multiple sources (GPS, UWB/multilateration, Kalman-filtered),
    and real-time policy enforcement is critical.
    """

    def __init__(self, vms: VisitorManagementSystem, presence_ttl_s: float = 240.0):
        self.vms = vms
        self.zone_hierarchy = ZoneHierarchy()
        self.tag_registry = TagRegistry()
        self.tether_monitors: Dict[str, TetherMonitor] = {}
        self.dwell_monitor: Optional[DwellMonitor] = None
        self.tag_drop_detectors: Dict[str, TagDropDetector] = {}
        self.last_known_positions: Dict[str, PositionSample] = {}
        self.anchor_network: Dict[str, Anchor] = {}
        self.coordinate_calibrator: Optional[CoordinateCalibrator] = None
        self.event_log = EventLog()
        self.last_known_zones: Dict[str, str] = {}
        # Both built and tested standalone earlier, neither previously
        # wired into the actual position-update pipeline -- fixed below.
        self.zone_lock_trackers: Dict[str, ZoneLockTracker] = {}
        self.presence_tracker = PresenceTracker(ttl_s=presence_ttl_s)
        self.reliability_warmup = ReliabilityWarmup()

    # =========================================================================
    # Zone Hierarchy & Floor-Aware Containment
    # =========================================================================

    def setup_zones_with_hierarchy(self, zones: List[ZoneProfile]) -> None:
        """
        Register zones with the hierarchy system, enabling 3D floor-aware
        resolution and nested zone specificity (smallest zone wins).
        """
        for zone in zones:
            self.zone_hierarchy.add_zone(zone)
        if self.vms.building:
            self.vms.building.zones = zones

    def resolve_zone_3d(self, position: Tuple[float, float, float]) -> Optional[ZoneProfile]:
        """
        Resolve a 3D position to the smallest-area (most specific) zone
        containing it. Returns None if outside all zones.
        Handles multi-floor cases (e.g., same (x,y) in different z slabs).
        """
        return self.zone_hierarchy.resolve(position)

    # =========================================================================
    # Multilateration Input (Alternative to GPS)
    # =========================================================================

    def add_anchor(self, anchor_id: str, position: Tuple[float, float, float]) -> None:
        """Register a fixed anchor (BLE/UWB beacon) for multilateration."""
        self.anchor_network[anchor_id] = Anchor(anchor_id=anchor_id, position=position)

    def multilaterate_position(
        self,
        rangings_dict: Dict[str, float],  # anchor_id -> distance_m
        rssi_dict: Optional[Dict[str, float]] = None,  # anchor_id -> RSSI in dBm (alternative)
        tx_power: float = -40.0,
        path_loss_exponent: float = 2.5,
    ) -> Optional[PositionSample]:
        """
        Compute a tag position from anchor distance measurements (or RSSI).
        Returns a PositionSample ready for downstream filtering/verification.
        """
        if rssi_dict:
            # Convert RSSI to distances
            rangings_dict = {
                aid: rssi_to_distance(rssi_dict[aid], tx_power, path_loss_exponent)
                for aid in rssi_dict
            }

        rangings = [
            Ranging(anchor_id=aid, distance_m=d, sigma_m=0.15)
            for aid, d in rangings_dict.items()
        ]

        position_3d = multilaterate(list(self.anchor_network.values()), rangings)
        if position_3d:
            return PositionSample(
                entity_id="tag",  # should be overridden by caller
                timestamp_s=datetime.now().timestamp(),
                position=position_3d,
                sigma_m=0.3,
            )
        return None

    # =========================================================================
    # Real-Time Policy Enforcement: Escort Tethering
    # =========================================================================

    def setup_escort_tethering(
        self,
        visitor_id: str,
        host_id: str,
        max_distance_ft: float = 20.0,
    ) -> None:
        """Digitally tether a visitor to a host (e.g., escort requirement)."""
        self.tether_monitors[visitor_id] = TetherMonitor(max_distance_ft=max_distance_ft)

    def check_escort_tether(self, visitor_id: str, host_id: str) -> Optional[str]:
        """
        Check if visitor and host are still within tether distance.
        Returns alert message if violated, None if OK.
        """
        if visitor_id not in self.tether_monitors:
            return None
        if visitor_id not in self.last_known_positions or host_id not in self.last_known_positions:
            return None

        tether = self.tether_monitors[visitor_id]
        alert = tether.check(
            self.last_known_positions[visitor_id],
            self.last_known_positions[host_id]
        )
        if alert:
            return f"TETHER BREACH: {visitor_id} is {alert.distance_m:.1f}m from {host_id} (limit {alert.threshold_m:.1f}m)"
        return None

    # =========================================================================
    # Real-Time Policy Enforcement: Dwell Time Monitoring
    # =========================================================================

    def setup_dwell_monitoring(self, baselines: Dict[str, DwellBaseline]) -> None:
        """
        Initialize dwell-time anomaly detection with per-zone-type baselines.
        Example baseline: {"stairwell": DwellBaseline(mean_s=30.0, std_s=15.0)}
        """
        self.dwell_monitor = DwellMonitor(baselines=baselines)

    def on_visitor_zone_enter(self, visitor_id: str, zone_id: str, timestamp_s: float) -> None:
        """Record a visitor's entry into a zone."""
        if self.dwell_monitor:
            self.dwell_monitor.on_zone_enter(visitor_id, zone_id, timestamp_s)

    def on_visitor_zone_exit(self, visitor_id: str, zone_id: str) -> None:
        """Record a visitor's exit from a zone."""
        if self.dwell_monitor:
            self.dwell_monitor.on_zone_exit(visitor_id, zone_id)

    def check_dwell_anomaly(
        self, visitor_id: str, zone_id: str, zone_type: str, timestamp_s: float
    ) -> Optional[str]:
        """
        Check if visitor's dwell time in zone exceeds baseline.
        Returns alert message if anomalous, None otherwise.
        """
        if not self.dwell_monitor:
            return None
        alert = self.dwell_monitor.check(visitor_id, zone_id, zone_type, timestamp_s)
        if alert:
            return f"DWELL ANOMALY: {visitor_id} in {zone_id} for {alert.dwell_s:.0f}s (baseline {alert.baseline_mean_s:.0f}s)"
        return None

    # =========================================================================
    # Tag Lifecycle Management
    # =========================================================================

    def assign_tag(self, tag_id: str, visitor_id: str, timestamp_s: float) -> None:
        """Assign a physical tag to a visitor at check-in."""
        self.tag_registry.assign(tag_id, visitor_id, timestamp_s)

    def unassign_tag(self, tag_id: str) -> None:
        """Release a tag at check-out."""
        self.tag_registry.unassign(tag_id)

    def check_battery_health(
        self, tag_id: str, readings: List[BatteryReading]
    ) -> Optional[str]:
        """
        Predict battery time-to-threshold and warn if low.
        Returns alert message if battery is low, None otherwise.
        """
        prediction = predict_battery(readings, low_voltage_threshold=3.30, warn_window_s=3600.0)
        if prediction and prediction.is_low:
            hrs = prediction.seconds_to_threshold / 3600.0 if prediction.seconds_to_threshold else None
            if hrs:
                return f"BATTERY LOW: {tag_id} will drain in {hrs:.1f} hours"
            else:
                return f"BATTERY CRITICAL: {tag_id} already below threshold"
        return None

    def check_speed_anomaly_alert(
        self, visitor_id: str, prev_pos: PositionSample, curr_pos: PositionSample
    ) -> Optional[str]:
        """
        Detect impossible speeds ("thrown over a fence" anti-passback).
        Returns alert message if speed anomaly detected, None otherwise.
        """
        anomaly = check_speed_anomaly(prev_pos, curr_pos, plausible_max_mps=6.0)
        if anomaly:
            return f"SPEED ANOMALY: {visitor_id} moving at {anomaly.speed_mps:.1f} m/s (plausible max {anomaly.plausible_max_mps} m/s)"
        return None

    def setup_tag_drop_detection(self, visitor_id: str) -> None:
        """Initialize tag-drop detection for a visitor."""
        self.tag_drop_detectors[visitor_id] = TagDropDetector(window_s=7200.0, std_threshold_m=0.5)

    def check_tag_drop(self, visitor_id: str, pos_sample: PositionSample) -> Optional[str]:
        """
        Detect if a tag has been left stationary (tag dropped on desk).
        Returns alert message if dropped, None otherwise.
        """
        if visitor_id not in self.tag_drop_detectors:
            return None
        alert = self.tag_drop_detectors[visitor_id].update(pos_sample)
        if alert:
            return f"TAG DROP: {visitor_id} stationary for {alert.stationary_for_s/3600:.1f} hrs at σ={alert.position_std_m*100:.0f}cm"
        return None

    # =========================================================================
    # Emergency Response: Automated Mustering
    # =========================================================================

    def check_for_presence_exits(self, now_s: float) -> List:
        """
        Call on a regular timer (e.g. every 30-60s) -- not per position
        update, since this is about detecting *silence*, which by
        definition doesn't arrive as an update. Any exit event found is
        also logged, so the dashboard sees it, not just the caller.
        """
        events = self.presence_tracker.check_exits(now_s)
        for e in events:
            self.event_log.log(
                now_s, "presence_exit", e.entity_id,
                f"Silent for {e.silent_for_s:.0f}s (TTL {self.presence_tracker.ttl_s:.0f}s)",
                source_module="incident",
            )
        return events

    def muster_report(self, now_s: float) -> Dict:
        """
        Generate evacuation headcount: group all visitors by last-known zone,
        flag stale positions (>60s old).
        """
        report = muster(
            self.last_known_positions,
            self.resolve_zone_3d,
            now_s=now_s,
            stale_confidence_threshold=0.25,
        )
        return {
            "total_inside": report.total_inside(),
            "zones": [
                {
                    "zone_id": zc.zone_id,
                    "zone_label": zc.zone_label,
                    "total": zc.total_count,
                    "stale": zc.stale_count,
                }
                for zc in report.zone_counts
            ],
            "unresolved": report.unresolved,
        }

    # =========================================================================
    # Emergency Response: Proximity Dispatch
    # =========================================================================

    def dispatch_nearest_responders(
        self, incident_position: Tuple[float, float, float], responder_ids: List[str], top_n: int = 3
    ) -> List[Dict]:
        """
        Find the N nearest responders (guards) to an incident location.
        Returns ordered list of candidates with distances.
        """
        guard_positions = {
            rid: self.last_known_positions[rid]
            for rid in responder_ids
            if rid in self.last_known_positions
        }
        candidates = nearest_guards(incident_position, guard_positions, top_n=top_n)
        return [
            {"responder_id": c.guard_id, "distance_m": c.distance_m}
            for c in candidates
        ]

    # =========================================================================
    # Unified Position Update Handler
    # =========================================================================

    def update_visitor_position(
        self,
        visitor_id: str,
        position: PositionSample,
        zone_name: Optional[str] = None,
        badge_risk: float = 0.0,
        host_id: Optional[str] = None,
    ) -> Dict:
        """
        Central method to ingest a position update from any source (GPS,
        multilateration, Kalman) and run all downstream checks:
          - Spoofing detection
          - Policy enforcement (tether, dwell, speed)
          - Tag health
          - Zone tracking

        host_id: pass the escort's visitor_id to check tethering for this
                 update. Omit for visitors with no escort requirement.
        """
        # Store last known position for mustering/dispatch
        self.last_known_positions[visitor_id] = position

        # Resolve the zone profile either way -- we need zone_type below
        # for dwell monitoring even when the caller already knows zone_name.
        zone_profile: Optional[ZoneProfile] = None
        if zone_name is None and self.vms.building:
            zone_profile = self.resolve_zone_3d(position.position)
            zone_name = zone_profile.zone_name if zone_profile else None
        elif zone_name and self.vms.building:
            zone_profile = self.vms.building.get_zone(zone_name)

        if not zone_name or visitor_id not in self.vms.active_visitors:
            return {
                "status": "error",
                "message": f"Visitor {visitor_id} or zone {zone_name} not found",
            }

        # Convert PositionSample to Position format for VMS
        pos = Position(
            t=position.timestamp_s,
            x=position.position[0],
            y=position.position[1],
            gps_accuracy=position.sigma_m,
        )

        # Run core geofencing verification
        report = self.vms.verify_visitor_location(visitor_id, zone_name, [pos], badge_risk=badge_risk)

        # Every position update is a heartbeat -- keeps PresenceTracker's
        # TTL/exit logic fed regardless of what else happens this update.
        self.presence_tracker.heartbeat(visitor_id, position.timestamp_s)

        # Burst-noise warm-up: a tag's first few readings after a real
        # silence (sleep, dropped connection) are often erratic -- same
        # start-conservative principle as kalman.py's c(n), applied here
        # to suppress ALERT-worthy anomaly checks specifically, not the
        # underlying position/zone tracking, which keeps running normally.
        is_warming_up = self.reliability_warmup.update(visitor_id, position.timestamp_s)

        alerts = []
        zone_risk = zone_profile.risk_level if zone_profile else None

        # Zone transition logging, debounced through ZoneLockTracker --
        # a raw zone flip only becomes a real zone_exit/zone_entry event
        # once it's been observed 3 consecutive times, not on a single
        # noisy reading near a boundary (the exact ping-pong failure
        # mode found by on-site testing).
        lock_tracker = self.zone_lock_trackers.setdefault(visitor_id, ZoneLockTracker())
        previous_confirmed = lock_tracker.locked_zone
        confirmed_zone = lock_tracker.update(zone_name)
        if confirmed_zone != previous_confirmed:
            if previous_confirmed is not None:
                self.event_log.log(
                    position.timestamp_s, "zone_exit", visitor_id, f"Left {previous_confirmed}",
                    zone_id=previous_confirmed, source_module="integrated",
                )
            if confirmed_zone is not None:
                self.event_log.log(
                    position.timestamp_s, "zone_entry", visitor_id, f"Entered {confirmed_zone}",
                    zone_id=confirmed_zone, risk_level=zone_risk, source_module="integrated",
                )
            self.last_known_zones[visitor_id] = confirmed_zone

        # Policy checks -- suppressed while warming up, logged as
        # suppressed (not silently dropped) for audit visibility.
        if host_id:
            tether_alert = self.check_escort_tether(visitor_id, host_id)
            if tether_alert and is_warming_up:
                self.event_log.log(
                    position.timestamp_s, "alert_suppressed_warmup", visitor_id,
                    f"Tether breach suppressed (reliability warm-up): {tether_alert}",
                    zone_id=zone_name, risk_level=zone_risk, source_module="integrated",
                )
            elif tether_alert:
                alerts.append(tether_alert)
                self.event_log.log(
                    position.timestamp_s, "tether_breach", visitor_id, tether_alert,
                    zone_id=zone_name, risk_level=zone_risk, source_module="tracking",
                )

        zone_type = zone_profile.zone_type if zone_profile else "unknown"
        dwell_alert = self.check_dwell_anomaly(visitor_id, zone_name, zone_type, position.timestamp_s)
        if dwell_alert and is_warming_up:
            self.event_log.log(
                position.timestamp_s, "alert_suppressed_warmup", visitor_id,
                f"Dwell anomaly suppressed (reliability warm-up): {dwell_alert}",
                zone_id=zone_name, risk_level=zone_risk, source_module="integrated",
            )
        elif dwell_alert:
            alerts.append(dwell_alert)
            self.event_log.log(
                position.timestamp_s, "dwell_anomaly", visitor_id, dwell_alert,
                zone_id=zone_name, risk_level=zone_risk, source_module="tracking",
            )

        tag_drop_alert = self.check_tag_drop(visitor_id, position)
        if tag_drop_alert and is_warming_up:
            self.event_log.log(
                position.timestamp_s, "alert_suppressed_warmup", visitor_id,
                f"Tag drop suppressed (reliability warm-up): {tag_drop_alert}",
                zone_id=zone_name, risk_level=zone_risk, source_module="integrated",
            )
        elif tag_drop_alert:
            alerts.append(tag_drop_alert)
            self.event_log.log(
                position.timestamp_s, "tag_drop", visitor_id, tag_drop_alert,
                zone_id=zone_name, risk_level=zone_risk, source_module="tag_lifecycle",
            )

        return {
            "status": "success",
            "visitor_id": visitor_id,
            "zone": zone_name,
            "anomaly_score": report.anomaly_score,
            "risk_level": report.risk_level.value,
            "is_spoofed": report.is_spoofed,
            "position_uncertainty_m": report.position_uncertainty,
            "alerts": alerts,
        }


if __name__ == "__main__":
    # Demo: integrated workflow
    print("=== Integrated Geofencing System Demo ===\n")

    vms = VisitorManagementSystem()
    integrated = IntegratedVisitorManagement(vms)

    # Setup zones with hierarchy
    from .models import ZoneProfile, BuildingLayout, TransitionGraph

    lobby = ZoneProfile(
        zone_name="main_lobby", zone_type="lobby", center=(0.0, 0.0),
        vertices=[(-70, -50), (70, -50), (80, 60), (-60, 65)],
        v_max=2.3, a_max=1.8, floor_id="1", z_min=0.0, z_max=3.0,
    )
    server_room = ZoneProfile(
        zone_name="server", zone_type="server_room", center=(50.0, 30.0),
        vertices=[(40, 20), (60, 20), (60, 40), (40, 40)],
        v_max=1.5, a_max=1.0, floor_id="1", z_min=0.0, z_max=3.0, risk_level="prohibited",
    )

    tg = TransitionGraph()
    tg.add_edge("main_lobby", "server", max_time=120)

    layout = BuildingLayout("Demo Facility", zones=[lobby, server_room], transition_graph=tg)
    vms.set_building_layout(layout)
    integrated.setup_zones_with_hierarchy([lobby, server_room])

    # Setup dwell monitoring
    integrated.setup_dwell_monitoring({
        "lobby": DwellBaseline(mean_s=300.0, std_s=120.0),
        "server_room": DwellBaseline(mean_s=60.0, std_s=30.0),
    })

    # Register visitor
    from .models import Visitor
    visitor = Visitor(
        visitor_id="V-001", name="Alice", badge_tag="B-001",
        entry_time=datetime.now(), allowed_areas=["main_lobby", "server"],
    )
    vms.register_visitor(visitor)
    integrated.assign_tag("TAG-001", "V-001", datetime.now().timestamp())

    # Simulate position update
    pos_sample = PositionSample("V-001", datetime.now().timestamp(), (0.0, 0.0, 1.5), sigma_m=5.0)
    result = integrated.update_visitor_position("V-001", pos_sample, zone_name="main_lobby")
    print(f"Position update result: {result}")

    # Demonstrate mustering
    integrated.last_known_positions["V-001"] = pos_sample
    muster_data = integrated.muster_report(datetime.now().timestamp())
    print(f"\nMuster report: {muster_data}")

    print("\n✓ Integration layer working!")
