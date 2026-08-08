## Code structure

Everything lives in a single file, `Geofencing.py`, organized in layers:

**Signal processing**
- `compute_log_convergence_factor()` — the `c(n)` function
- `KalmanFilter1D` — smooths one coordinate axis per instance; two are used together for (x, y)

**Domain models** (plain dataclasses)
- `Visitor`, `Position`, `VisitorLocationReport`, `DetectionResult`
- `ZoneProfile` — per-zone speed/acceleration limits (lobby vs. stairwell vs. parking, etc.)
- `UserBehaviorProfile` — a visitor's learned typical velocity/acceleration, refined over repeat visits
- `BadgeEvent`, `BadgeGPSCorrelation` — badge/RFID scan data and its comparison against GPS

**Detection engine**
- `GeofenceSystem` — the core class for one zone. `detect_spoofing()` runs Kalman smoothing, computes velocity/acceleration violations, geofence-boundary time, and teleportation jumps, then returns a weighted `DetectionResult`. `verify_visitor()` wraps this per-visitor and folds in behavior-profile adjustments.
- `BadgeSystem` — registers RFID readers and cross-checks badge scans against reported GPS via `correlate_badge_gps()`

**Integration layer**
- `VisitorManagementSystem` — the class you actually use day-to-day: `register_visitor()`, `add_geofence()`, `verify_visitor_location()`, `record_badge_event()`, `export_audit_log()`. It owns all active visitors, zones, and the running security-alert log.

**Demo / synthetic data**
- `generate_legitimate_path()`, `generate_spoofed_path()`, `generate_extreme_spoofed_path()` — build synthetic GPS traces for testing
- `_demo()` — the script's entry point, runs a full walkthrough with multiple zones and produces the two PNGs

### Minimal usage

```python
from Geofencing import VisitorManagementSystem, Visitor, Position
from datetime import datetime

vms = VisitorManagementSystem()
vms.add_geofence("main_lobby", center=(0.0, 0.0), radius=200.0)

visitor = Visitor(visitor_id="V001", name="Jane Doe", badge_tag="B-001",
                   entry_time=datetime.now(), allowed_areas=["main_lobby"])
vms.register_visitor(visitor)

positions = [Position(x=0.0, y=0.0, timestamp=datetime.now(), accuracy=5.0)]
report = vms.verify_visitor_location("V001", "main_lobby", positions)
print(report.risk_level, report.anomaly_score)
```