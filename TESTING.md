# Testing Strategy

This project targets deployment in multi-story facilities, where
standard 2D geofencing (latitude/longitude) fails outright: a visitor on
floor 1 needs completely different triggers, services, and permissions
than one on floor 30. The Z-axis isn't an edge case here, it's a first-
class dimension — which is why `ZoneProfile` carries `floor_id`/
`z_min`/`z_max` and why `elevator_tracking.py` exists at all.

This document is the project's testing methodology, organized around
five pillars, each mapped honestly against what the code actually does
today — not what it's meant to do eventually. A ✅ means tested and
working, code reference included. A ⚠️ means partially covered, with
the specific gap named. A ❌ means genuinely not built — either out of
scope for this codebase (a deployment/device concern) or real future
work.

---

## Pillar 1 — Vertical Accuracy & Floor Detection (the Z-axis)

| Test item | Status | Notes |
|---|---|---|
| Floor boundary confusion (F14 vs F15) | ✅ | The exact failure mode `BeaconLockTracker` was built and proven against — 16 identity flips/40 readings with naive nearest-beacon logic, 0 with smoothing + hysteresis. See `elevator_tracking.py` `__main__`. |
| Elevator transitions at varying speed | ✅ | `ElevatorKalman1D`'s process model integrates *measured* acceleration, not an assumed constant velocity — handles real accelerate/cruise/decelerate profiles, tested against exact kinematics. |
| Barometric sensor calibration under weather variation | ❌ | Out of scope by design: this deployment uses beacon + accelerometer fusion, not barometric altitude, specifically because barometric drift with weather was a known weakness (see `elevator_tracking.py` module docstring). Not a gap in what was built — a different approach was chosen instead. |
| Stairwell floor tracking | ⚠️ | `tracking.py`'s `DwellMonitor` flags lingering too long in a stairwell zone, but nothing tracks *which floor* someone's on while walking stairs the way `elevator_tracking.py` does for the car. Real gap. |

## Pillar 2 — Signal Degradation & Indoor Positioning (IPS)

| Test item | Status | Notes |
|---|---|---|
| GPS-to-indoor failover | ❌ | This system was built indoor-beacon-first (`multilateration.py`), not GPS-first with an indoor fallback. There's no GPS path to fail over *from*. If exterior GPS (building approach, lobby) is in scope, that's new work, not a fallback path in existing code. |
| Signal interference at peak hours | ⚠️ | `multilateration.py`'s weighted least-squares already down-weights per-reading uncertainty (a noisier reading contributes less to the solved position), but nothing actively *detects* "this looks like RF noise, not real motion" as its own signal. Real gap. |
| Multi-mode positioning (BLE + Wi-Fi/GPS) | ⚠️ | Architecturally ready, not built: the same weighted least-squares solver generalizes to fusing multiple ranging sources with different uncertainty — a noisier Wi-Fi reading would naturally get down-weighted relative to a tighter BLE one. Not implemented because there's no real multi-mode data yet to validate it against. |
| Multipath/reflection causing coordinate spikes | ⚠️ | Found in round-2 on-site testing. Nothing in `multilateration.py` currently *detects* an individual anchor reading as an outlier — every reading is trusted at its declared uncertainty. Real fix: per-anchor residual check after an initial solve, down-weight or exclude outliers, same "reject the physically implausible" philosophy `check_speed_anomaly` already uses elsewhere. Not built yet. |
| Dilution of precision in narrow/colinear anchor geometry (e.g. a long hallway) | ⚠️ | Same root cause as the vertical-GDOP finding already documented in `multilateration.py`'s own module docstring — poor anchor geometry degrades position resolution on whichever axis the anchors don't spread across, horizontal or vertical. Real fix: expose the solve's actual condition number as a live confidence metric, not an assumed constant, and gate geofence triggers on it. Not built yet. |

## Pillar 3 — Boundary & Perimeter Testing (3D Geofence Volumes)

| Test item | Status | Notes |
|---|---|---|
| Horizontal boundaries (lobby, loading dock, courtyard) | ✅ | `ZoneProfile.contains()` — ray-casting point-in-polygon, arbitrary shapes, not limited to circles. |
| Vertical boundaries (top floor ceiling, basement floor) | ✅ | `ZoneProfile.contains_3d()` — no special-casing needed for the extremes, it's the same `z_min <= z <= z_max` check regardless of which floor. |
| Buffer zones / "teleportation" bugs — beacon identity | ✅ | This is the ping-pong bug, independently named and independently found/fixed in `elevator_tracking.py` (`BeaconLockTracker`). |
| Buffer zones / "teleportation" bugs — zone boundaries | ✅ | The same failure mode, found again in round-2 testing but for zone entry/exit rather than beacon identity — and caught a live instance of it: `integrated.py`'s zone_entry/zone_exit logging had zero debounce until this was added. Fixed with `ZoneLockTracker` (`tracking.py`): a candidate zone must be observed 3 consecutive times before the lock changes. Proven: 24 raw flips down to 3 over 50 readings under aggressive (40%) boundary jitter, while still confirming a real sustained transition correctly. |
| Non-convex polygon containment | ✅ | Tested directly against an L-shaped room: ray-casting correctly includes both arms and excludes the notch. Standard even-odd ray-casting handles simple non-convex polygons fine — this was *not* actually a gap, contrary to the initial testing note. |
| Self-intersecting / degenerate polygon input | ❌ | The genuine risk behind the non-convex concern: a sloppy blueprint trace that crosses itself isn't validated against, and ray-casting's behavior on a bowtie polygon isn't well-defined. Not built. |
| Coordinate calibration accuracy ("wall-bleeding": a tag reading as inside a restricted zone while actually just outside in a corridor) | ⚠️ | Found in round-2 on-site testing. Root cause: `CoordinateCalibrator`'s 2-point similarity transform can't correct for perspective/lens distortion in a real blueprint photo, so error compounds with distance from the reference points. Two real fixes, neither built yet: (1) support ≥3 reference points with a least-squares fit; (2) make zone containment confidence-aware the way `TetherMonitor` already is — don't fire a restricted-zone breach on a position barely inside the boundary if position uncertainty means it could plausibly be outside. |

## Pillar 4 — Device Performance & Battery Impact

| Test item | Status | Notes |
|---|---|---|
| Adaptive scan rate (passive when stationary) | ⚠️ | Not built, but the hook already exists: `tag_lifecycle.py`'s stationary-detection logic (built for tag-drop detection) computes exactly the signal — "this tag hasn't moved" — that adaptive scan throttling would key off. Wiring it to actual scan-rate control is real work, not started. |
| OS background location throttling | ❌ | Deployment/app-permissions concern (see `README.md` field validation notes), not something to patch in this codebase. |
| Device heterogeneity (high-end vs low-end) | ⚠️ | The architecture has the right hook — every `PositionSample` carries its own `sigma_m` — it just needs real per-device-class calibration data, which is a deployment task, not a code gap. |

## Pillar 5 — Edge Cases & Lifecycle Scenarios

| Test item | Status | Notes |
|---|---|---|
| High-density concurrent breaches (rush-hour elevator lobby) | ❌ | Backend/infrastructure concern (concurrent request handling at scale) — none of tonight's math modules address this, and none were meant to. |
| "Window effect" GPS/position spikes | ✅ | `tag_lifecycle.py`'s `check_speed_anomaly` — an implausible jump gets rejected, not blindly trusted. This is the same physical-plausibility philosophy the original spoofing detector (`geofence.py`) was built around from day one. |
| Hardware failures / graceful degradation | ✅ | Demonstrated, not just designed: `elevator_tracking.py`'s ride simulation shows the filter keeps running on pure accelerometer dead-reckoning when beacons go silent — worse accuracy, but functional, not broken. |
| Stale/zombie tags (dropped connection while inside a zone) | ✅ | `PresenceTracker` (`incident.py`) already existed and was tested standalone, but was never actually wired into the position-update pipeline until round-2 testing caught the gap. Now called on every `update_visitor_position()`, verified end-to-end. |
| Lag in smoothing filters causing delayed breach detection | ⚠️ | Real, inherent trade-off of any smoothing filter. Not built: a dual-path approach (smoothed position for normal tracking, a faster/less-smoothed check specifically for high-risk zones where detection speed matters more than jitter suppression). |
| Burst noise on wake from sleep/reconnection | ✅ | Fixed with `ReliabilityWarmup` (`tag_lifecycle.py`) — same start-conservative-earn-confidence principle as `kalman.py`'s logarithmic convergence factor, reapplied as alert suppression on freshly-reconnected tags. Proven end-to-end: a real tether breach present from the very first reading is correctly suppressed for 3 readings, then fires normally — with the suppression itself logged for audit visibility, not silently dropped. |

---

## On-site field test findings

Beacon hardware was tested at an active real-world multi-story facility
— confirmed broadcasting and detectable throughout the building,
including inside elevator shafts. `elevator_tracking.py`'s first on-site
test against real hardware surfaced four issues, exactly the kind of
thing simulation can't reliably reproduce (real RSSI noise character,
real device OS behavior, real RF multipath in an actual shaft):

1. **Beacon-identity ping-ponging near floor boundaries** — fixed.
   `BeaconLockTracker` (exponential moving average + hysteresis margin).
   See Pillar 1 and Pillar 3 above.
2. **Missed exit events** — fixed. `PresenceTracker` in `incident.py`:
   an active TTL/heartbeat model that fires an exit event on its own
   after a configurable silence threshold (default 4 minutes), rather
   than only flagging staleness passively when a report happens to be
   requested.
3. **OS background sleep** killing the scanning app — not a code fix,
   an app-permissions and battery-optimization deployment requirement.
4. **Signal reflection/blockage** in parts of the facility — a
   deployment-topology and hardware decision (more overlapping beacons,
   multi-mode positioning). See Pillar 2's multi-mode note above for
   what's architecturally ready if this gets pursued.

This section gets updated as further on-site results come in, not
claimed ahead of them.

## On-site field test findings — round 2 (multi-building)

`floorplan.py`, `multilateration.py`, `tracking.py`, `tag_lifecycle.py`,
and `permits.py` had their first on-site test across multiple buildings.
Same pattern as round 1: real testing surfaced real issues simulation
alone hadn't caught, several with a fix already applied, some still
genuinely open — listed precisely, not summarized as "passed."

**Fixed this round:**
1. Zone-boundary ping-ponging (`ZoneLockTracker`) — see Pillar 3.
2. Stale/zombie tags never wired to an active exit mechanism
   (`PresenceTracker` connected to the pipeline) — see Pillar 5.
3. Burst noise on tag reconnection (`ReliabilityWarmup`) — see Pillar 5.

**Still open, real work:**
4. Coordinate calibration accuracy ("wall-bleeding") — see Pillar 3.
5. Multipath-induced coordinate spikes in `multilateration.py` — see
   Pillar 2.
6. Dilution of precision in narrow/colinear anchor layouts — see
   Pillar 2.
7. Smoothing-filter lag on high-risk zone breaches — see Pillar 5.

**Clarified, not a gap:** non-convex polygon containment was flagged as
a concern but tested directly and found to work correctly — see Pillar
3 for the actual test and what the real risk is instead (self-
intersecting input, not non-convexity).

This section, like the elevator one above it, gets updated as further
on-site results come in.

---

## Tag access control — dynamic, role-based zone rights

Round-2 field testing identified the gap this closes: `Visitor.allowed_areas`
was a static list, so a guest who stated "IT department" at reception, or was
handed an escorted tag for a server room, was still evaluated against whatever
that list happened to contain. The system could raise alerts about
unauthorized presence, but it could never *proactively authorize* anything.

`permits.py` had the right building block (time-windowed `Permit` objects with
a `requires_escort` flag) but was never wired into the live path — confirmed by
inspection: zero references to it in `integrated.py`. This meant the
`permit_denied` event type already defined in `event_log.py` could never
actually fire.

**What was built:**

- `IntegratedVisitorManagement.check_in_visitor()` — turns a stated destination
  list plus a tag type into real, time-windowed permits. Two tag types:
  `"standard"` (public and escort-required destinations; can never be granted a
  `prohibited` zone) and `"escorted"` (can reach `prohibited` zones, but every
  non-public permit it issues carries `requires_escort=True`). Refusals come
  back with a reason, and are logged, because reception needs to see *why*.
- `is_escort_present()` — escort presence is **verified against live proximity**,
  not assumed from the check-in assignment. An escorted tag whose host wandered
  off is no longer an escorted visit, which is precisely the case a static
  `allowed_areas` list could never catch.
- Authorization evaluated in `update_visitor_position()` on **confirmed zone
  entry** (via `ZoneLockTracker`), not on every position sample — a guest
  standing in a server room for ten minutes is one authorization decision, not
  600 identical denials flooding the dashboard.
- `permit_granted` / `permit_authorized` / `permit_denied` all flow through the
  existing `EventLog`, so the real-time `subscribe()` path delivers them with no
  transport changes.

**Verified end-to-end:**

| Scenario | Result |
|---|---|
| Standard tag states `it_dept` (escort-required), no host assigned | Refused at check-in, with reason |
| Standard tag states `server_room` (prohibited) | Refused — prohibited needs an escorted tag |
| Escorted tag with host, same two destinations | Both granted, `requires_escort=True` |
| Escorted guest in server room, host 1m away | `permit_authorized` |
| Escorted guest in server room, host 45m away | `permit_denied` — CRITICAL: right revoked live when the escort left |
| Standard guest wanders into server room with no permit | `permit_denied` — CRITICAL, fires once on confirmed entry |

**Closed since:** tag type is now bound to the physical tag in `TagRegistry`,
alongside the guest's name, rather than passed as a `check_in_visitor()`
argument. `check_in_visitor()` reads the privilege class off the tag actually
issued and ignores any conflicting argument (logging the mismatch), and a guest
cannot hold two live tags at once. Verified against the escalation path
directly: a guest issued a standard tag who re-checks in requesting `escorted`
privileges for a prohibited zone is still refused, and a second tag issued to
the same guest while the first is live raises rather than silently succeeding.

A VMS registers people, not tags, so `TagRegistry` also resolves the other
direction — `display_name()` gives operators "A. Mwangi (TAG-0042)" instead of
a bare tag ID, which is what now appears in dashboard event rows.

---

## Event logging for the admin dashboard

Every module above (`tracking`, `tag_lifecycle`, `incident`,
`elevator_tracking`, `permits`) produces its own differently-shaped
alert — fine in isolation, but there was no single stream an admin
dashboard could subscribe to and expect one consistent shape.

`event_log.py` is that shared sink. `integrated.py` now logs into it at
every point an alert is already generated (tether breach, dwell
anomaly, tag drop, zone entry/exit, and warm-up suppressions) — not a
separate parallel logging pass, the same call sites the alerts
themselves come from.

- **Severity is computed, not fixed per event type.** The same dwell
  anomaly is `WARNING` in a `public` zone, `ALERT` in `escort_required`,
  `CRITICAL` in `prohibited` — using `ZoneProfile.risk_level`'s own
  vocabulary, not a second parallel scale. (First attempt at this used
  simple enum-stepping and lost the distinction between
  `escort_required` and `prohibited` for high-base-severity events —
  caught by the module's own test, fixed with numeric scoring instead.)
- **Real-time push is proven, not just designed.** `EventLog.subscribe()`
  calls every registered callback synchronously the instant an event is
  logged. Verified end-to-end: ran the full escorted-visit scenario with
  a subscriber attached from the start, confirmed 10/10 events reached
  it, in order, matching exactly what `query()` returns after the fact.
  This is the hook a websocket/SSE layer attaches to for delivering
  events to connected dashboard clients as they happen.
- **`query()` is the pull side** — a dashboard's initial page load or a
  historical view, filtered by time range, entity, event type, or
  minimum severity (e.g. "show me everything ALERT or above, right
  now").

**Not yet done:** the actual transport layer (websocket/SSE server)
that would push `EventLog` events to a browser-based admin dashboard in
real time. `subscribe()` is the integration point a transport layer
needs; building that layer depends on what the existing VMS admin
dashboard's stack actually is, which isn't something to guess at rather
than confirm.
