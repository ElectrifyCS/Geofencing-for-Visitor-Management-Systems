# Geofencing for Visitor Management System

A production-grade, **math-driven** location verification system for visitor management.  
It detects GPS spoofing and physically implausible movement using Kalman filtering, logarithmic convergence, adaptive thresholds, per-visitor behaviour profiles, zone-aware rules, and badge/RFID correlation.

I originally developed and implemented this as part of a real-world visitor management project over the course of a year. The core detection logic proved reliable in practice and is now released as open source (demo uses fully synthetic data).

![Legitimate visitor scenario](assets/visitor_legitimate_scenario.png)
![Suspicious visitor scenario](assets/visitor_suspicious_scenario.png)

## What it does

Visitors are tracked via **visitor tags** (not personal phones) while moving through a facility.  
The system continuously evaluates whether their reported positions are *physically plausible*:

- Humans do not teleport  
- Walking/running speeds stay within realistic bounds  
- Acceleration is limited  
- Reported GPS position should be consistent with badge/RFID reader locations  

When a location report breaks these physical constraints (or disagrees with badge/RFID data), it is flagged as a possible spoofing attempt and given a risk score.

### Core capabilities

- **2-D constant-velocity Kalman filter** – smooths noisy GPS and derives velocity & acceleration directly from the filter state  
- **Logarithmic convergence factor** – starts conservative and increases confidence as more data arrives  
- **Uncertainty-aware adaptive thresholds** – automatically loosen or tighten based on GPS accuracy, observation history *and* the filter’s own position uncertainty  
- **Robust per-visitor behaviour profiles** – median + MAD with exponential forgetting (only trusted observations shrink the MAD)  
- **Polygonal zones + directed transition graph** – supports complex floor plans and forbidden zone-to-zone jumps  
- **Badge / GPS risk fusion** – RFID mismatch is no longer a parallel signal; it is a first-class term inside the main anomaly score  
- **Risk scoring + audit logging** – ready for a security dashboard  

---

## The math (and why each piece is there)

Every part of the detection logic exists to solve a specific, concrete problem — not for its own sake.

**GPS readings are noisy → 2-D constant-velocity Kalman**  
Consumer GPS (and even industrial tags) jitter by several metres.  
State vector \(\mathbf{x} = [p_x, p_y, v_x, v_y]^\top\).  
Velocity is taken from the filter state; acceleration is obtained by finite difference of successive velocity estimates.  
This replaces the earlier 1-D-per-axis approach and gives cleaner kinematic signals.

**New visitors have no track record → logarithmic convergence factor**  
\[
c(n) = \min\bigl(1,\ \tfrac{\ln(n+1)}{\ln(B+1)}\bigr)
\]  
Grows quickly at first, then asymptotes. Applied both to the Kalman gain (with a floor so early measurements are never completely ignored) and to the adaptive thresholds.

**Fixed limits punish legitimate variation → adaptive thresholds**  
\[
\tau(c) = 0.5 + c
\]  
Further inflated by the filter’s own position uncertainty \(\sigma\):  
\[
\tau(c,\sigma) = \tau(c)\cdot\bigl(1 + \max(0,(\sigma-5)/20)\bigr)
\]  
Loose at the start, tighter once the system has evidence, and automatically more tolerant when the filter itself is uncertain.

**One violation isn’t proof of spoofing → weighted, uncertainty-aware anomaly score**  
Velocity, acceleration, path efficiency, geofence dwell, boundary jumps, **badge risk** and **forbidden transitions** are combined into a single score.  
When filter uncertainty is high, kinematic weights are down-scaled and badge weight is increased. The weights are re-normalised so the total remains 1.

**Behaviour profiles must not be poisoned by a single spoofed visit → robust statistics**  
Online median + MAD with exponential forgetting \(\lambda\).  
Only observations that the system itself judged TRUSTED/LOW are allowed to shrink the MAD. Untrusted data can only raise the soft ceiling (safety).

**Complex buildings need more than circles → polygons + transition graph**  
Point-in-polygon by ray casting (even-odd rule).  
Directed graph of allowed zone-to-zone movements with optional maximum transit times.

For reference, this maps onto IB Mathematics AA HL as follows: logarithmic functions and transformations (the convergence factor), sequences and limits (\(c(n)\) as \(n\to\infty\)), vectors and vector geometry (position/displacement/geofence/polygon checks), statistics — mean, variance, median, MAD (Kalman noise model and robust profiles), and functions and transformations (\(\tau(c,\sigma)\)).

---

## What’s new in this release (the upgrades)

| Upgrade | What changed | Why it matters for live VMS |
|---------|--------------|-----------------------------|
| 2-D constant-velocity Kalman | Velocity & acceleration now come from the filter state instead of raw finite differences | Cleaner signals on 15–60 s tag reporting intervals |
| Uncertainty-aware scoring | Filter covariance \(\sigma\) influences both thresholds and component weights | Reduces false alarms when GPS is noisy |
| Polygonal zones + transition graph | Full polygon support + forbidden zone jumps | Handles real floor plans (multi-building sites) |
| Robust behaviour profiles | Median/MAD + exponential forgetting; trusted-only updates | A single spoofed visit can no longer poison a visitor’s profile |
| Badge risk fusion | RFID mismatch is a weighted term inside the main anomaly score | One coherent risk number for the security dashboard |

These upgrades directly address the live-testing challenges of coordinate drift near boundaries, large reporting intervals, complex layouts, and alert fatigue — while preserving the original transparent, tag-based mathematical philosophy.

---

## Repo layout
Geofencing-for-Visitor-Management-Systems/
├── geofencing/          # the package — detection engine
│   ├── init.py      # public API
│   ├── kalman.py        # 2-D CV Kalman + c(n) + τ(c,σ)
│   ├── models.py        # dataclasses, polygons, robust profiles, transition graph
│   ├── badge.py         # badge/RFID tracking and GPS correlation
│   ├── geofence.py      # GeofenceSystem — the core detection engine
│   ├── vms.py           # VisitorManagementSystem — day-to-day integration layer
│   └── synthetic.py     # synthetic GPS path generators for the demo
├── demo.py              # self-contained demo (produces the two images above)
├── Geofencing.py        # thin backwards-compatible entry point
├── assets/              # demo output images
├── requirements.txt
├── LICENSE
└── README.md


## Minimal usage

```python
from geofencing import (
    VisitorManagementSystem, Visitor, Position,
    ZoneProfile, TransitionGraph, BuildingLayout
)
from datetime import datetime

lobby = ZoneProfile(
    zone_name="main_lobby",
    center=(0, 0),
    radius=80,
    vertices=[(-60, -40), (60, -40), (70, 50), (-50, 55)],  # polygonal
    v_max=2.2, a_max=1.8, zone_type="lobby"
)

tg = TransitionGraph()
tg.add_edge("parking", "main_lobby", max_time=180)

layout = BuildingLayout("CBK HQ", zones=[lobby], transition_graph=tg)

vms = VisitorManagementSystem()
vms.set_building_layout(layout)

visitor = Visitor(
    visitor_id="V001", name="Jane Doe", badge_tag="B-001",
    entry_time=datetime.now(), allowed_areas=["main_lobby"]
)
vms.register_visitor(visitor)

positions = [
    Position(t=0.0,  x=0.0,  y=0.0, gps_accuracy=4.5),
    Position(t=45.0, x=12.0, y=3.0, gps_accuracy=5.0),
    Position(t=90.0, x=25.0, y=8.0, gps_accuracy=4.8),
]
report = vms.verify_visitor_location("V001", "main_lobby", positions)
print(report.risk_level, report.anomaly_score, report.position_uncertainty)

Running the demo
pip install -r requirements.txt
python Geofencing.py

The script runs a self-contained demonstration with synthetic visitors and simulated GPS paths, and writes the two images shown above to assets/. No real location data is required or included.
Requirements

Python 3.9+
numpy ≥ 1.24
matplotlib ≥ 3.7

Status
This repository contains the detection engine that was implemented and validated in a live visitor-management context. The public demo uses completely synthetic names, zones, and trajectories. The mathematical approach (2-D constant-velocity Kalman + logarithmic convergence + uncertainty-aware adaptive thresholds + robust profiles) proved reliable and forms the foundation of this open-source release.
License
MIT — see LICENSE.
