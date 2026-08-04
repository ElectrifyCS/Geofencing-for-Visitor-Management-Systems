# Visitor Geofencing Security System

A location-based verification system for visitor management that detects
spoofed GPS coordinates using multi-factor anomaly detection, adaptive
thresholds, and badge/RFID correlation.

## What it does

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

## The math

A few pieces of the detection logic are worth calling out, since they're
built directly on top of some standard tools:

- **Kalman filter (1D, applied per axis)** — smooths each GPS coordinate
  by blending a prediction with each new noisy measurement, weighted by a
  Kalman gain.
- **Logarithmic convergence factor** — `c(n) = min(1, ln(n+1) / ln(B+1))`,
  where `n` is the number of measurements seen and `B` is a baseline
  sample size. This scales down the filter's trust in early
  measurements and ramps it up as more data comes in, so the system
  starts conservative and gets more confident over time.
- **Adaptive threshold scaling** — `τ(c) = 0.5 + 1.0·c` adjusts velocity
  and acceleration tolerances based on that same convergence factor,
  giving more leeway early on and tightening up once the system has
  enough history to trust its own estimate.
- **Anomaly scoring** — combines velocity/acceleration constraint
  violations, time spent outside a geofence boundary, and detected
  "teleportation" jumps into a single weighted risk score.

If you've done IB Mathematics AA HL, a lot of this maps directly onto
familiar topics: logarithmic functions and their transformations
(the convergence factor), sequences/limits (convergence as `n → ∞`),
vectors and vector geometry (position, displacement, and geofence
boundary checks in 2D), and statistics (mean/variance underlying the
Kalman filter and GPS noise modeling).

## Running it

```bash
pip install -r requirements.txt
python Geofencing.py
```

This runs a self-contained demo with synthetic visitors and simulated
GPS paths — no real data required — and saves two visualizations
(`visitor_legitimate_scenario.png`, `visitor_suspicious_scenario.png`)
showing a normal path versus a flagged one.

## Status

This is a personal project exploring GPS spoofing detection for visitor
management systems. All names, zones, and locations in the demo code
are synthetic.

## License

MIT — see [LICENSE](LICENSE).
