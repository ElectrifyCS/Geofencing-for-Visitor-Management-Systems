# Visitor Geofencing Security System

A production-grade, math-driven location verification system for visitor management.  
It detects GPS spoofing and physically implausible movement using Kalman filtering, logarithmic convergence, adaptive thresholds, per-visitor behaviour profiles, zone-aware rules, and badge/RFID correlation.

Originally developed and implemented as part of a real-world visitor management project. The core detection logic proved reliable in practice and is now released as open source (demo uses fully synthetic data).

## What it does

Visitors are tracked via GPS while moving through a facility. The system continuously evaluates whether reported positions are *physically plausible*:

- Humans do not teleport
- Walking/running speeds stay within realistic bounds
- Acceleration is limited
- Reported GPS position should be consistent with badge/RFID reader locations

Violations are scored by risk level and logged for security review.

### Core capabilities

- **Kalman filtering (1D per axis)** – smooths noisy GPS before evaluation
- **Logarithmic convergence factor** – starts conservative and gains confidence with more observations
- **Adaptive thresholds** – automatically loosen/tighten based on GPS accuracy and observation history
- **Per-visitor behaviour profiles** – learn typical velocity/acceleration across visits
- **Zone-aware rules** – different normal speeds for lobby, stairwell, parking, etc.
- **Badge / GPS correlation** – cross-checks RFID scans against GPS position
- **Risk scoring + audit logging** – ready for a security dashboard

## The math (why it is reliable)

The detection layer is deliberately built on transparent, standard mathematical tools rather than opaque heuristics:

- **Kalman filter (1D)**  
  Predicts the next position and blends it with the new noisy measurement using a Kalman gain.

- **Logarithmic convergence factor**  where `n` = number of measurements so far and `B` = baseline sample size.  
Early readings have reduced influence; confidence grows smoothly as evidence accumulates.

- **Adaptive threshold scaling**  Velocity and acceleration tolerances start loose and tighten as the system gains trust in its own estimate.

- **Anomaly scoring**  
Weighted combination of velocity/acceleration violations, time outside geofence, and detected teleportation jumps.

These map directly onto standard topics (logarithms & limits, vectors/geometry in 2D, mean/variance, sequences) and make the behaviour of the system predictable and tunable.

## Running the demo

```bash
pip install -r requirements.txt
python Geofencing.py