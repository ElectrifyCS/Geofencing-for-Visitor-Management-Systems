# Visitor Geofencing Security System – Math Upgrades

Production-grade, **math-driven** location verification for visitor management systems.  
Detects GPS spoofing and physically implausible movement using:

- 2-D constant-velocity Kalman filtering
- Logarithmic convergence factor \(c(n)\)
- Uncertainty-aware adaptive thresholds \(\tau(c,\sigma)\)
- Robust (median + MAD) per-visitor behaviour profiles with exponential forgetting
- Polygonal zones + directed transition graph
- Badge/RFID risk fused directly into the anomaly score

Originally developed for a live visitor-management deployment.  
Demo uses fully synthetic data.

---

## The Math (IB AA HL style)

### 1. Logarithmic convergence

\[
c(n)=\min\bigl(1,\tfrac{\ln(n+1)}{\ln(B+1)}\bigr)
\]

Starts conservative, rises quickly, then asymptotes.  
Used both on the Kalman gain and on the adaptive thresholds.

### 2. Adaptive thresholds

\[
\tau(c)=0.5+c
\]

Further inflated by the filter’s own position uncertainty \(\sigma\):

\[
\tau(c,\sigma)=\tau(c)\cdot\bigl(1+\max(0,(\sigma-5)/20)\bigr)
\]

### 3. 2-D constant-velocity Kalman

State \(\mathbf{x}=[p_x,p_y,v_x,v_y]^\top\)

\[
F=\begin{pmatrix}1&0&\Delta t&0\\0&1&0&\Delta t\\0&0&1&0\\0&0&0&1\end{pmatrix}
\]

Process noise from continuous white-noise acceleration.  
Velocity taken directly from the state; acceleration by finite difference of successive velocity estimates.

### 4. Robust behaviour profiles

Online median + MAD with exponential forgetting \(\lambda\in(0,1]\):

\[
m_t=\lambda m_{t-1}+(1-\lambda)\operatorname{median}(\text{buffer})
\]

Only trusted observations are allowed to shrink the MAD.

### 5. Uncertainty-aware scoring

Kinematic signals are down-weighted when \(\sigma\) is large; badge risk is correspondingly up-weighted.  
Component scores are re-normalised so the total weight remains 1.

### 6. Polygonal geometry & transition graph

Point-in-polygon by ray casting (even-odd rule).  
Directed graph of allowed zone transitions with optional maximum transit times.

---

## Package layout

```
geofencing/
├── kalman.py      # 2-D CV Kalman + c(n) + τ(c,σ)
├── models.py      # dataclasses, polygons, robust profiles, transition graph
├── badge.py       # RFID correlation (risk ready for fusion)
├── geofence.py    # core detection engine
├── vms.py         # day-to-day integration layer
└── synthetic.py   # demo path generators
```

---

## Minimal usage

```python
from geofencing import (
    VisitorManagementSystem, Visitor, Position,
    ZoneProfile, TransitionGraph, BuildingLayout
)
from datetime import datetime

# Polygonal lobby example
lobby = ZoneProfile(
    zone_name="main_lobby",
    center=(0, 0),
    radius=80,
    vertices=[(-60, -40), (60, -40), (70, 50), (-50, 55)],
    v_max=2.2, a_max=1.8, zone_type="lobby"
)

tg = TransitionGraph()
tg.add_edge("parking", "main_lobby", max_time=180)
tg.add_edge("main_lobby", "corridor_a", max_time=60)

layout = BuildingLayout("CBK HQ", zones=[lobby], transition_graph=tg)

vms = VisitorManagementSystem()
vms.set_building_layout(layout)

visitor = Visitor(
    visitor_id="V001", name="Jane Doe", badge_tag="B-001",
    entry_time=datetime.now(), allowed_areas=["main_lobby"]
)
vms.register_visitor(visitor)

positions = [
    Position(t=0.0, x=0.0, y=0.0, gps_accuracy=4.5),
    Position(t=45.0, x=12.0, y=3.0, gps_accuracy=5.0),
    Position(t=90.0, x=25.0, y=8.0, gps_accuracy=4.8),
]
report = vms.verify_visitor_location("V001", "main_lobby", positions)
print(report.risk_level, report.anomaly_score, report.position_uncertainty)
```

---

## Requirements

- Python 3.9+
- numpy ≥ 1.24
- matplotlib ≥ 3.7

---

## Status

Core detection logic validated in a live visitor-management environment.  
Public demo uses completely synthetic trajectories.  
All upgrades preserve the original transparent, math-first philosophy.
