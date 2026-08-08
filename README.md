## The math (why it is reliable)

The detection layer is deliberately built on transparent, standard mathematical tools rather than opaque heuristics:

- **Kalman filter (1D)**  
  Predicts the next position and blends it with the new noisy measurement using a Kalman gain.

- **Logarithmic convergence factor**  where `n` = number of measurements so far and `B` = baseline sample size.  
Early readings have reduced influence; confidence grows smoothly as evidence accumulates.

- **Adaptive threshold scaling**  Velocity and acceleration tolerances start loose and tighten as the system gains trust in its own estimate.

- **Anomaly scoring**  
Weighted combination of velocity/acceleration violations, time outside geofence, and detected teleportation jumps.

If you've done IB Mathematics AA HL, a lot of this maps directly onto familiar topics: logarithmic functions and their transformations (the convergence factor), sequences/limits (convergence as `n → ∞`), vectors and vector geometry (position, displacement, and geofence boundary checks in 2D), and statistics (mean/variance underlying the Kalman filter and GPS noise modeling).