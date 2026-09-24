# Visitor Geofencing Security System

A location-based verification system for visitor management. The project has
two parts:

1. **`Geofencing.py`** — a GPS-spoofing detection engine: Kalman-filtered
   position tracking, adaptive anomaly thresholds, and badge/RFID
   correlation.
2. **VMS Sentinel dashboard** (`dashboard/`) — a live security operations
   dashboard exploring a parallel detection surface for an indoor facility:
   BLE/NFC patrol integrity, beacon-based floor-lock for stairwells,
   zone-entry debounce, tailgating, and host-tether breaches. Mirrors the
   kind of real-time geofencing view already used on the deployed system
   this project is based on.

Both explore the same underlying question — *is this visitor's reported
location physically plausible, and does it match what other signals say?*
— from two different angles: outdoor GPS vs. indoor BLE/NFC/zone tracking.

---

## 1. GPS spoofing detection (`Geofencing.py`)

Visitors are tracked via GPS while moving through a facility. The system
continuously checks whether their reported movement is *physically
plausible* — humans don't teleport, and walking speed rarely exceeds a few
metres per second. When a location report breaks those physical
constraints, or disagrees with where a badge/RFID reader says the visitor
is, it's flagged as a possible spoofing attempt and scored by risk level.

Core features:

- **Kalman filtering** to smooth noisy GPS readings before they're evaluated
- **Adaptive thresholds** that loosen or tighten based on GPS accuracy and
  how much data has been observed so far
- **Per-visitor behavior profiles** that learn a visitor's typical
  velocity/acceleration over repeat visits, so thresholds personalize
  instead of using one-size-fits-all limits
- **Badge/GPS correlation** — cross-checks RFID badge scans against GPS
  position to catch mismatches
- **Zone-aware rules** — a stairwell, a parking garage, and a lobby have
  very different "normal" speeds, so each zone gets its own profile
- **Risk scoring and audit logging** for a security dashboard view

### The math

- **Kalman filter (1D, applied per axis)** — smooths each GPS coordinate by
  blending a prediction with each new noisy measurement, weighted by a
  Kalman gain.
- **Logarithmic convergence factor** — `c(n) = min(1, ln(n+1) / ln(B+1))`,
  where `n` is the number of measurements seen and `B` is a baseline sample
  size. This scales down the filter's trust in early measurements and ramps
  it up as more data comes in, so the system starts conservative and gets
  more confident over time.
- **Adaptive threshold scaling** — `τ(c) = 0.5 + 1.0·c` adjusts velocity and
  acceleration tolerances based on that same convergence factor, giving
  more leeway early on and tightening up once the system has enough history
  to trust its own estimate.
- **Anomaly scoring** — combines velocity/acceleration constraint
  violations, time spent outside a geofence boundary, and detected
  "teleportation" jumps into a single weighted risk score.

### Running it

```bash
pip install -r requirements.txt
python Geofencing.py
```

This runs a self-contained demo with synthetic visitors and simulated GPS
paths — no real data required — and saves two visualizations
(`visitor_legitimate_scenario.png`, `visitor_suspicious_scenario.png`)
showing a normal path versus a flagged one.

---

## 2. VMS Sentinel dashboard

**Why this exists:** the facility where this system is actually deployed
already has a security operations dashboard that shows geofencing activity
in real time — so it made sense to have a version of that living in this
repo alongside the detection engine, rather than the engine being the only
piece anyone outside the deployment ever sees. Note that this dashboard
runs entirely on a **synthetic simulation** (`src/lib/vms/simulation.ts`)
scripted for demo purposes — it does not connect to, or reflect data from,
the live production system.

![VMS Sentinel dashboard — floor 2, live map and event stream](screenshots/floor-2.png)

A live, in-browser simulation of a 3-floor facility's security operations
center — not a recorded replay. It runs a scripted cast of visitors, a
host, and two guards through a demo day, and reacts to their positions in
real time.

Core detection modules (`dashboard/src/lib/vms/`):

- **Beacon floor-lock** (`beacon.ts`, `stairwell.ts`) — EMA-smoothed,
  hysteresis-locked landing detection so RSSI noise near a floor midpoint
  can't flip which floor a tracked tag is "on."
- **Zone debounce** (`zone-lock.ts`) — requires several consecutive
  readings before confirming a zone change, so a tag hovering on a boundary
  doesn't spam entry/exit events.
- **Patrol verification** (`patrol.ts`) — BLE range for general
  checkpoints, mandatory NFC tap for high-integrity checkpoints (BLE
  proximity alone can never satisfy an NFC point), plus sequence and
  min/max transit-time checks between checkpoints.
- **Severity-scored event log** (`event-log.ts`) — severity is computed
  from event type + zone risk level, not hardcoded per event.
- **Simulation engine** (`simulation.ts`) — ties it together: zone
  entry/exit + permit checks, tailgating detection, host-tether breach
  detection, dwell/loitering anomalies in prohibited zones.

### Running it

```bash
cd dashboard
npm install
npm run dev
```

Then open the printed local URL. Use the speed controls (1× / 4× / 10×) to
run the demo loop faster, and the inject buttons (miss NFC tap, stair
loiter, floor skip, tailgate) to force specific anomaly scenarios on
demand.

### Tests

- **Detection-logic self-checks** (`dashboard/src/lib/vms/prove.ts`) — run
  automatically once, the moment the simulation starts. They check a
  handful of invariants directly: severity scoring escalates correctly by
  risk level, the beacon-lock tracker stays stable where a naive
  nearest-reading approach would ping-pong between two floors, a stairwell
  climb produces a floor-crossing event, and — the one most worth calling
  out — that standing in BLE range of an NFC checkpoint can **never**
  verify it, and a remote NFC tap outside BLE range is rejected as a spoof
  attempt. If one of these regresses, it throws and logs to the browser
  console (`[vms] engine proof failed`) rather than failing silently.
- **`npm test`** also runs a broader suite (`dashboard/scripts/*.test.mjs`,
  a couple of `dashboard/src/lib/**/*.test.ts` files) inherited from the
  app-builder template this was scaffolded with. Right now those cover
  build tooling and an auth system that's disabled and unused in this
  dashboard — not the geofencing/security logic itself. Worth knowing so
  a green `npm test` isn't mistaken for coverage of the detection logic.
- **Known gap:** beyond the `prove.ts` boot checks, the detection modules
  (`patrol.ts`, `stairwell.ts`, `zone-lock.ts`, `event-log.ts`) don't yet
  have a conventional unit-test suite. That's the natural next thing to
  add if this dashboard moves beyond a demo.

### Status

Both the Python engine and the dashboard are demo/prototype code. All
names, zones, and locations are synthetic.

## License

MIT — see [LICENSE](LICENSE).
