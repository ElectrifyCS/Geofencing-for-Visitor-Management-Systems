## The math (and why each piece is there)

Every part of the detection logic exists to solve a specific, concrete problem — not for its own sake. Here's why each one is in the code, explained without assuming any particular math background:

**GPS readings are noisy → Kalman filter (1D)**
Consumer GPS jitters by a few meters even when someone is standing still. Feeding raw coordinates straight into anomaly checks would trigger false alarms constantly. The Kalman filter predicts where the visitor *should* be next based on their last known position, then blends that prediction with the new noisy reading — weighted by how much it trusts each one. The result is a smoothed position that's far more stable than either the prediction or the raw reading alone.

**New visitors have no track record → logarithmic convergence factor**
The very first GPS ping from a brand-new visitor tells you almost nothing — is 3 m/s their walking speed or a glitch? The system shouldn't be fully confident on one data point, but it also shouldn't wait forever to trust anything. `c(n) = min(1, ln(n + 1) / ln(B + 1))` grows quickly at first and then levels off — so confidence builds fast early on and then plateaus, rather than needing hundreds of readings to become useful.

**Fixed limits punish legitimate variation → adaptive thresholds**
A single hard speed limit is either too strict for a new visitor (lots of false positives before the system has learned anything) or too loose once you'd otherwise have enough history to be stricter. `τ(c) = 0.5 + 1.0 · c` ties the tolerance directly to the convergence factor above: loose at first, tightening automatically as the system accumulates evidence.

**One violation isn't proof of spoofing → weighted anomaly scoring**
A visitor could trip one check for an innocent reason (dropped signal, elevator, etc.). Spoofing is more convincingly signaled by *multiple* things going wrong together — speed, acceleration, time outside the geofence, and sudden jumps are combined into a single weighted score, and that score maps to a risk level (`TRUSTED` → `CRITICAL`) rather than a binary flag.

<details>
<summary>If you've done IB Mathematics AA HL, here's the syllabus mapping</summary>

| AA HL topic | Where it shows up |
|---|---|
| Logarithmic functions | `compute_log_convergence_factor()` |
| Sequences and limits | Behaviour of `c(n)` as `n → ∞` |
| Vectors and vector geometry | Position/displacement/geofence checks in `GeofenceSystem` |
| Statistics (mean & variance) | Kalman filter's noise model |
| Functions and transformations | The `τ(c)` threshold-scaling function |

</details>

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
