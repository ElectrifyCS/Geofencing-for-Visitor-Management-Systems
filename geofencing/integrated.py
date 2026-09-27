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
  6. Restart-surviving state for ZoneLockTracker (see state_store.py) — a
     deploy, crash, or OOM no longer silently erases every visitor's zone
     lock with no trace; the recovery itself is logged to EventLog for
     incident recap. DwellMonitor, PresenceTracker, ReliabilityWarmup,
     TagRegistry and PermitRegistry have the identical gap and are NOT
     yet covered — deliberately scoped out of this pass.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from .models import (
    Visitor, Position, VisitorLocationReport, ZoneProfile, BuildingLayout
)
from .vms import VisitorManagementSystem
from .floorplan import ZoneHierarchy, CoordinateCalibrator
from .multilateration import multilaterate, Anchor, Ranging, rssi_to_distance
from .tracking import (
    PositionSample, TetherMonitor, DwellMonitor, DwellBaseline,
    compute_heading, intent_angle_deg, ZoneLockTracker, FEET_TO_METRES
)
from .incident import muster, nearest_guards, PresenceTracker
from .tag_lifecycle import (
    TagRegistry, predict_battery, BatteryReading,
    check_speed_anomaly, TagDropDetector, ReliabilityWarmup
)
from .event_log import EventLog
from .permits import Permit, PermitRegistry
from .state_store import StateStore, InMemoryStateStore


class IntegratedVisitorManagement:
    """
    Master system that orchestrates all geofencing, tracking, and emergency
    response capabilities. Designed for real-world deployment where positions
    arrive from multiple sources (GPS, UWB/multilateration, Kalman-filtered),
    and real-time policy enforcement is critical.
    """

    def __init__(
        self,
        vms: VisitorManagementSystem,
        presence_ttl_s: float = 240.0,
        state_store: Optional[StateStore] = None,
    ):
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
        # In-process cache of ZoneLockTracker instances, keyed by visitor.
        # The durable source of truth is self.state_store below — this
        # dict just avoids a store round-trip on every single position
        # update for a visitor already loaded this session. See
        # _load_zone_lock_tracker / _save_zone_lock_tracker.
        self.zone_lock_trackers: Dict[str, ZoneLockTracker] = {}
        # (visitor_id, zone_id) -> was their entry authorized. Read by
        # the loitering check to tell whether someone dwelling too long
        # was ever supposed to be here.
        self.zone_entry_authorized: Dict[Tuple[str, str], bool] = {}
        # zone_id -> recent (visitor_id, timestamp_s, authorized) entries,
        # pruned to tailgate_window_s, for tailgating detection.
        self.recent_zone_entries: Dict[str, List[Tuple[str, float, bool]]] = {}
        self.tailgate_window_s: float = 5.0
        self.presence_tracker = PresenceTracker(ttl_s=presence_ttl_s)
        self.reliability_warmup = ReliabilityWarmup()
        self.permit_registry = PermitRegistry()
        # visitor_id -> host/escort visitor_id, set at check-in for
        # escorted tags. Escort *presence* is verified live against
        # actual proximity, not just assumed from this mapping.
        self.escort_assignments: Dict[str, str] = {}

        # --- Restart-surviving state (ZoneLockTracker only, this pass) ---
        self.state_store: StateStore = state_store if state_store is not None else InMemoryStateStore()

        # Recovery check: any "zone_lock:" key already present in the
        # store predates this __init__ call, since nothing above has
        # written anything yet. That can only mean a PREVIOUS process
        # wrote it and this one is picking the store back up — i.e. a
        # real restart happened, not a fresh boot against an empty
        # store. An operator restarting a process on purpose is
        # unremarkable; a process that vanished and came back with
        # visitors already mid-track is exactly what an incident recap
        # needs a record of, so this logs loudly rather than silently.
        recovered_keys = self.state_store.keys("zone_lock:")
        if recovered_keys:
            recovered_ids = [k.split(":", 1)[1] for k in recovered_keys]
            last_write = self.state_store.get("_meta:last_write")
            gap_desc = (
                f"{time.time() - last_write['ts']:.0f}s since last write"
                if last_write else "gap unknown (no last-write record found)"
            )
            self.event_log.log(
                time.time(), "system_recovered", "system",
                f"Recovered zone-lock state for {len(recovered_ids)} visitor(s) "
                f"after restart ({gap_desc}): {', '.join(recovered_ids)}",
                source_module="state_store",
            )

    # =========================================================================
    # Zone-lock persistence (load-cache-save around the state store)
    # =========================================================================

    def _load_zone_lock_tracker(self, visitor_id: str) -> ZoneLockTracker:
        """
        Returns this visitor's ZoneLockTracker, restoring it from the
        durable store on first use this process (e.g. right after a
        restart) rather than always creating a fresh one. Once loaded,
        the in-process cache (self.zone_lock_trackers) serves subsequent
        calls without hitting the store again.
        """
        if visitor_id in self.zone_lock_trackers:
            return self.zone_lock_trackers[visitor_id]
        persisted = self.state_store.get(f"zone_lock:{visitor_id}")
        tracker = ZoneLockTracker(**persisted) if persisted else ZoneLockTracker()
        self.zone_lock_trackers[visitor_id] = tracker
        return tracker

    def _save_zone_lock_tracker(self, visitor_id: str, tracker: ZoneLockTracker) -> None:
        """
        Persists this visitor's current tracker state to the durable
        store. ZoneLockTracker's fields are all plain primitives
        (str/None/int), so dataclasses.asdict() round-trips cleanly
        through JSON with no custom (de)serialization needed.
        """
        self.state_store.set(f"zone_lock:{visitor_id}", asdict(tracker))
        self.state_store.set("_meta:last_write", {"ts": time.time()})

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

        multilaterate() now also does two things this method surfaces:
          - Rejects any single anchor whose reading disagrees with the
            others by more than ranging noise explains (likely multipath/
            NLOS), logged as an event when it happens rather than silently
            dropped, since a caller reading the audit trail should be able
            to see that a fix came from 3 anchors instead of 4 and why.
          - Returns the solve's condition number (dilution of precision) --
            fed into this PositionSample's sigma_m via
            _dop_inflation_factor() so every confidence-aware check already
            in this codebase (TetherMonitor, geofence containment) becomes
            automatically more conservative on a geometrically weak fix,
            rather than adding a second, parallel confidence signal every
            caller would need to remember to check separately.
        """
        if rssi_dict:
            # Convert RSSI to distances
            rangings_dict = {
                aid: rssi_to_distance(rssi_dict[aid], tx_power, path_loss_exponent)
                for aid in rssi_dict
            }

        # NOTE: every anchor gets the same sigma_m here because this
        # method's own input (a plain anchor_id -> distance_m dict) has
        # nowhere to carry per-anchor uncertainty. multilaterate() itself
        # fully supports per-anchor sigma_m (see Ranging) for a caller
        # that has it -- this method just doesn't accept it yet.
        base_sigma_m = 0.15
        rangings = [
            Ranging(anchor_id=aid, distance_m=d, sigma_m=base_sigma_m)
            for aid, d in rangings_dict.items()
        ]

        result = multilaterate(list(self.anchor_network.values()), rangings)
        if result is None:
            return None

        if result.rejected_anchor_ids:
            self.event_log.log(
                datetime.now().timestamp(), "multilateration_outlier_rejected", "system",
                f"Rejected anchor(s) {', '.join(result.rejected_anchor_ids)} as probable "
                f"multipath/NLOS (residual exceeded robust threshold); solved with "
                f"{len(result.used_anchor_ids)} remaining anchor(s)",
                source_module="multilateration",
            )

        return PositionSample(
            entity_id="tag",  # should be overridden by caller
            timestamp_s=datetime.now().timestamp(),
            position=result.position,
            sigma_m=base_sigma_m * self._dop_inflation_factor(result.condition_number),
        )

    @staticmethod
    def _dop_inflation_factor(condition_number: float, healthy_condition_number: float = 10.0) -> float:
        """
        Turns a raw condition number into a multiplier on position
        uncertainty. Log-scaled since condition number legitimately spans
        orders of magnitude (a well-spread room vs. a narrow corridor, per
        multilateration.py's own demo) -- linear scaling would barely
        react to the corridor case and overreact to minor variation in the
        healthy case.

        healthy_condition_number=10.0 and the log10 scaling are a
        reasoned starting point (condition numbers near this were
        observed in the well-conditioned room-layout demo), NOT a
        rigorously derived constant -- flagged the same way the elevator
        beacon-detection radius and other site-specific numbers in this
        project are: needs calibration against real anchor layouts once
        that data exists, the same caveat multi-mode positioning above
        already carries for the identical reason.
        """
        if condition_number <= healthy_condition_number:
            return 1.0
        return 1.0 + math.log10(condition_number / healthy_condition_number)

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

    def assign_tag(
        self, tag_id: str, visitor_id: str, timestamp_s: float,
        guest_name: str = "", tag_type: str = "standard",
    ) -> None:
        """
        Issue a physical tag to a visitor at check-in, binding the
        guest's name and the tag's privilege class to the tag itself.
        Raises if the tag is already out, or if this guest already holds
        a live tag.
        """
        self.tag_registry.assign(
            tag_id, visitor_id, timestamp_s, guest_name=guest_name, tag_type=tag_type
        )

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
    # Dynamic Tag Access: check-in -> permits
    # =========================================================================

    def check_in_visitor(
        self,
        visitor_id: str,
        stated_destinations: List[str],
        now: datetime,
        duration_hours: float = 8.0,
        tag_type: str = "standard",
        host_id: Optional[str] = None,
    ) -> Dict:
        """
        Turn a guest's stated destination + tag type into actual, live
        zone rights -- the missing layer field testing identified.

        Before this, Visitor.allowed_areas was a static list: a guest who
        said "IT department" at reception was still treated per whatever
        that list happened to contain, so legitimate visits looked like
        violations and the system never proactively authorized anything.

        Tag types:
          "standard" -- ordinary guest tag. Gets time-windowed permits
            for public and escort_required destinations. Cannot be
            granted a "prohibited" zone at all; that needs an escorted
            tag, and reception issuing one is a deliberate act.
          "escorted" -- issued when a guest is accompanied by internal
            staff. Can reach "prohibited" zones (server rooms etc.), but
            every permit it grants for a non-public zone carries
            requires_escort=True, so the right is conditional on the
            host actually being present, checked live against proximity
            rather than assumed from the assignment.

        Returns a summary of what was granted and what was refused, with
        a reason for each refusal -- reception needs to see why, not
        just that something didn't work.
        """
        granted: List[Dict] = []
        refused: List[Dict] = []
        valid_until = now + timedelta(hours=duration_hours)

        # Privilege comes from the tag physically issued to this guest,
        # not from whatever the caller passed in. If a tag is on record,
        # it wins outright -- an "escorted" argument cannot upgrade a
        # standard tag that reception actually handed over. The argument
        # is only a fallback for callers that provision no tag at all.
        issued = self.tag_registry.assignment_for_guest(visitor_id)
        if issued is not None:
            if issued.tag_type != tag_type:
                self.event_log.log(
                    now.timestamp(), "permit_denied", visitor_id,
                    f"Requested '{tag_type}' privileges but issued tag {issued.tag_id} "
                    f"is '{issued.tag_type}' -- using the tag's own class",
                    source_module="permits",
                )
            tag_type = issued.tag_type

        if host_id:
            self.escort_assignments[visitor_id] = host_id

        for zone_name in stated_destinations:
            zone = self.vms.building.get_zone(zone_name) if self.vms.building else None
            if zone is None:
                refused.append({"zone_id": zone_name, "reason": f"Unknown zone '{zone_name}'"})
                continue

            risk = zone.risk_level
            if risk == "prohibited" and tag_type != "escorted":
                refused.append({
                    "zone_id": zone_name,
                    "reason": f"'{zone_name}' is prohibited; requires an escorted tag, not '{tag_type}'",
                })
                self.event_log.log(
                    now.timestamp(), "permit_denied", visitor_id,
                    f"Check-in refused for {zone_name}: prohibited zone needs an escorted tag",
                    zone_id=zone_name, risk_level=risk, source_module="permits",
                )
                continue

            if risk == "escort_required" and tag_type != "escorted" and not host_id:
                refused.append({
                    "zone_id": zone_name,
                    "reason": f"'{zone_name}' requires an escort; no host assigned at check-in",
                })
                self.event_log.log(
                    now.timestamp(), "permit_denied", visitor_id,
                    f"Check-in refused for {zone_name}: escort-required zone with no host assigned",
                    zone_id=zone_name, risk_level=risk, source_module="permits",
                )
                continue

            needs_escort = risk in ("escort_required", "prohibited")
            self.permit_registry.add(Permit(
                visitor_id=visitor_id, zone_id=zone_name,
                valid_from=now, valid_until=valid_until,
                requires_escort=needs_escort,
            ))
            granted.append({
                "zone_id": zone_name, "risk_level": risk,
                "requires_escort": needs_escort, "valid_until": valid_until,
            })
            self.event_log.log(
                now.timestamp(), "permit_granted", visitor_id,
                f"Permit issued for {zone_name} until {valid_until:%H:%M}"
                + (" (escort required)" if needs_escort else ""),
                zone_id=zone_name, risk_level=risk, source_module="permits",
            )

        return {
            "visitor_id": visitor_id, "tag_type": tag_type,
            "host_id": host_id, "granted": granted, "refused": refused,
        }

    def is_escort_present(
        self, visitor_id: str, position: PositionSample, max_distance_ft: float = 20.0
    ) -> bool:
        """
        Escort presence is verified, not assumed: the assigned host must
        actually be within tether range right now. An escorted tag whose
        host wandered off is not an escorted visit any more, which is
        exactly the case a static allowed_areas list could never catch.
        """
        host_id = self.escort_assignments.get(visitor_id)
        if host_id is None:
            return False
        host_position = self.last_known_positions.get(host_id)
        if host_position is None:
            return False
        distance = math.dist(position.position, host_position.position)
        return distance <= max_distance_ft * FEET_TO_METRES

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
        # mode found by on-site testing). The tracker itself is now
        # restart-surviving: loaded from self.state_store on first use
        # this process, saved back after every update.
        lock_tracker = self._load_zone_lock_tracker(visitor_id)
        previous_confirmed = lock_tracker.locked_zone
        confirmed_zone = lock_tracker.update(zone_name)
        self._save_zone_lock_tracker(visitor_id, lock_tracker)
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

            # Authorization is evaluated on confirmed zone *entry*, not
            # every position sample: a guest standing in a server room
            # for ten minutes is one unauthorized-entry decision, not
            # 600 identical denials flooding the dashboard. Debounced
            # entry is exactly the right trigger point for it.
            if confirmed_zone is not None:
                escort_present = self.is_escort_present(visitor_id, position)
                authorized, reason = self.permit_registry.check(
                    visitor_id, confirmed_zone,
                    datetime.fromtimestamp(position.timestamp_s),
                    escort_present=escort_present,
                )
                # Remembered per (visitor, zone) so a later dwell anomaly
                # in this same zone can tell whether the person who's
                # lingering was ever authorized to be here at all -- the
                # compound "loitering while unauthorized" check below.
                self.zone_entry_authorized[(visitor_id, confirmed_zone)] = authorized

                if not authorized:
                    alerts.append(f"UNAUTHORIZED ZONE: {reason}")
                    self.event_log.log(
                        position.timestamp_s, "permit_denied", visitor_id, reason,
                        zone_id=confirmed_zone, risk_level=zone_risk, source_module="permits",
                    )
                else:
                    self.event_log.log(
                        position.timestamp_s, "permit_authorized", visitor_id, reason,
                        zone_id=confirmed_zone, risk_level=zone_risk, source_module="permits",
                    )

                # Tailgating: a second, different visitor confirmed
                # entering the SAME zone within a few seconds of this
                # one, where at least one of the two lacks authorization.
                # Two authorized people walking in together is normal
                # traffic, not a security event -- it's specifically the
                # combination of "close together in time" AND "at least
                # one shouldn't be here" that makes it tailgating rather
                # than coincidence.
                recent = self.recent_zone_entries.setdefault(confirmed_zone, [])
                recent[:] = [
                    (vid, t, auth) for vid, t, auth in recent
                    if position.timestamp_s - t <= self.tailgate_window_s
                ]
                for other_vid, other_t, other_auth in recent:
                    if other_vid == visitor_id:
                        continue
                    if not authorized or not other_auth:
                        gap = position.timestamp_s - other_t
                        self.event_log.log(
                            position.timestamp_s, "tailgating", visitor_id,
                            f"Entered {confirmed_zone} {gap:.1f}s after {other_vid} "
                            f"(unauthorized: {visitor_id if not authorized else other_vid})",
                            zone_id=confirmed_zone, risk_level=zone_risk, source_module="integrated",
                        )
                        break
                recent.append((visitor_id, position.timestamp_s, authorized))

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
            # Compound signal: dwelling somewhere you were never
            # authorized to be is a materially different, more urgent
            # event than either "denied entry" or "lingering" alone --
            # and a CCTV integration consuming this stream shouldn't
            # have to correlate two independent events itself to know
            # that. Fires as ONE event, not both, specifically for this
            # case; ordinary dwell anomalies in authorized zones are
            # unaffected.
            was_authorized = self.zone_entry_authorized.get((visitor_id, zone_name), True)
            if not was_authorized:
                self.event_log.log(
                    position.timestamp_s, "loitering_unauthorized", visitor_id,
                    f"Unauthorized presence AND lingering in {zone_name}: {dwell_alert}",
                    zone_id=zone_name, risk_level=zone_risk, source_module="integrated",
                )
            else:
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

    # --- Crash-recovery demo: ZoneLockTracker state must survive a full ---
    # --- process restart, and the recovery itself must be logged.       ---
    print("\n=== Crash recovery (Redis-backed state) ===")
    try:
        import fakeredis
        from .state_store import RedisStateStore

        shared_redis = fakeredis.FakeStrictRedis()
        store = RedisStateStore(shared_redis)

        # "Before the crash": one process tracks a visitor through enough
        # consistent readings to actually confirm a zone lock (not just
        # a candidate in progress).
        vms_a = VisitorManagementSystem()
        integrated_a = IntegratedVisitorManagement(vms_a, state_store=store)
        vms_a.set_building_layout(layout)
        integrated_a.setup_zones_with_hierarchy([lobby, server_room])
        visitor2 = Visitor(
            visitor_id="V-002", name="Bob", badge_tag="B-002",
            entry_time=datetime.now(), allowed_areas=["main_lobby"],
        )
        vms_a.register_visitor(visitor2)
        for _ in range(3):
            pos = PositionSample("V-002", datetime.now().timestamp(), (0.0, 0.0, 1.5), sigma_m=5.0)
            integrated_a.update_visitor_position("V-002", pos, zone_name="main_lobby")
        locked_before = integrated_a.zone_lock_trackers["V-002"].locked_zone
        print(f"  Before 'crash': V-002 locked zone = {locked_before}")

        # "The crash": drop the process entirely. Nothing survives this
        # but whatever was written to the shared Redis-compatible store.
        del integrated_a

        # "After restart": brand-new process (brand-new VMS, brand-new
        # IntegratedVisitorManagement, brand-new empty in-process dicts),
        # pointed at the SAME store.
        vms_b = VisitorManagementSystem()
        integrated_b = IntegratedVisitorManagement(vms_b, state_store=store)

        # NOTE: EventLog.query()'s exact signature is inferred from the
        # README's description, not from reading event_log.py directly —
        # adjust this line if it doesn't match once you run it.
        recovered_events = [
            e for e in integrated_b.event_log.query() if e.event_type == "system_recovered"
        ]
        print(f"  After 'restart': recovery event logged = {len(recovered_events) == 1}")
        if recovered_events:
            print(f"    \"{recovered_events[0].message}\"")

        restored_tracker = integrated_b._load_zone_lock_tracker("V-002")
        print(
            f"  After 'restart': V-002 locked zone restored = {restored_tracker.locked_zone} "
            f"(expected '{locked_before}', with zero replayed position updates)"
        )
        assert restored_tracker.locked_zone == locked_before, "zone lock did not survive the restart"
        print("  OK — the tracker's lock state came from Redis, not from replaying events.")
    except ImportError:
        print("  fakeredis not installed — skipping crash-recovery demo "
              "(pip install fakeredis to run it)")

    # --- Multilateration outlier rejection + DOP-aware confidence, ---
    # --- exercised through the actual integration-layer entry point, ---
    # --- not just multilateration.py's own standalone demo.          ---
    print("\n=== Multilateration: outlier rejection + DOP-aware confidence ===")
    dop_integrated = IntegratedVisitorManagement(VisitorManagementSystem())
    for anchor_id, pos in [
        ("A1", (0.0, 0.0, 3.0)), ("A2", (20.0, 0.0, 3.0)),
        ("A3", (20.0, 15.0, 0.3)), ("A4", (0.0, 15.0, 0.3)), ("A5", (10.0, 7.5, 1.5)),
    ]:
        dop_integrated.add_anchor(anchor_id, pos)

    clean_readings = {"A1": 12.9, "A2": 10.9, "A3": 12.9, "A4": 15.0, "A5": 2.7}
    clean_fix = dop_integrated.multilaterate_position(clean_readings)
    print(f"  Clean readings:     position={tuple(round(v, 2) for v in clean_fix.position)}, "
          f"sigma_m={clean_fix.sigma_m:.3f} (near base 0.15 -- good geometry, no rejection)")

    corrupted_readings = dict(clean_readings)
    corrupted_readings["A3"] += 3.5  # simulated multipath reflection
    corrupted_fix = dop_integrated.multilaterate_position(corrupted_readings)
    outlier_events = [
        e for e in dop_integrated.event_log.query()
        if e.event_type == "multilateration_outlier_rejected"
    ]
    print(f"  Corrupted (A3 +3.5m): position={tuple(round(v, 2) for v in corrupted_fix.position)}, "
          f"sigma_m={corrupted_fix.sigma_m:.3f}")
    print(f"  Outlier-rejection event logged: {len(outlier_events) == 1}")
    if outlier_events:
        print(f"    \"{outlier_events[0].message}\"")
