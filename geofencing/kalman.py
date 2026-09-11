"""
Signal processing: 2D constant-velocity Kalman filter, logarithmic convergence,
and uncertainty-aware adaptive thresholds.

Math (IB AA HL flavour):
  - State vector geometry and matrix transformations
  - Logarithmic sequences / limits for convergence factor c(n)
  - Adaptive scaling functions τ(c)
  - Covariance as a measure of uncertainty (statistics)
"""

from __future__ import annotations
import numpy as np
from typing import Tuple, Optional
import os


def compute_log_convergence_factor(measurements: int, baseline: int = 15) -> float:
    """
    Logarithmic convergence factor.

        c(n) = min(1, ln(n + 1) / ln(B + 1))

    Grows quickly at first, then asymptotes to 1.
    """
    if measurements <= 0:
        return 0.0
    if baseline <= 0:
        return 1.0
    return float(min(1.0, np.log(measurements + 1) / np.log(baseline + 1)))


# Optional reproducibility
_seed = os.environ.get("GEOFENCE_SEED")
if _seed is not None:
    np.random.seed(int(_seed))


class KalmanFilter2DConstantVelocity:
    """
    2-D constant-velocity Kalman filter.

    State vector:
        x = [px, py, vx, vy]^T

    Transition (constant velocity):
        F = [[1, 0, dt, 0],
             [0, 1, 0, dt],
             [0, 0, 1,  0],
             [0, 0, 0,  1]]

    Measurement:
        z = [px, py]^T   (H selects position only)

    Process noise Q uses the continuous white-noise acceleration model
    discretised for the given dt.  Measurement noise R is diagonal and
    scaled by reported GPS accuracy.

    Velocity is taken directly from the state; acceleration is obtained by
    finite difference of successive velocity estimates (still simple, still
    transparent).
    """

    def __init__(
        self,
        process_noise_std: float = 0.35,         # m/s² – white-noise acceleration (walking scale)
        measurement_noise_std: float = 5.0,      # m – typical urban GPS
        enable_log_convergence: bool = True,
        baseline_convergence: int = 15,
    ):
        self.process_noise_std = float(process_noise_std)
        self.measurement_noise_std = float(measurement_noise_std)
        self.enable_log_convergence = enable_log_convergence
        self.baseline_convergence = baseline_convergence

        # State and covariance
        self.x = np.zeros(4)                     # [px, py, vx, vy]
        self.P = np.eye(4) * 100.0               # large initial uncertainty

        self.measurement_count = 0
        self.last_t: Optional[float] = None
        self.last_velocity: Optional[np.ndarray] = None  # for acceleration

    def _get_convergence_factor(self) -> float:
        if not self.enable_log_convergence:
            return 1.0
        return compute_log_convergence_factor(
            self.measurement_count, self.baseline_convergence
        )

    def _F(self, dt: float) -> np.ndarray:
        """State transition matrix."""
        return np.array([
            [1.0, 0.0, dt,  0.0],
            [0.0, 1.0, 0.0, dt],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ], dtype=float)

    def _Q(self, dt: float) -> np.ndarray:
        """
        Process-noise covariance for continuous white-noise acceleration.
        σ_a² * [[dt⁴/4, 0, dt³/2, 0],
                [0, dt⁴/4, 0, dt³/2],
                [dt³/2, 0, dt², 0],
                [0, dt³/2, 0, dt²]]
        """
        sa2 = self.process_noise_std ** 2
        dt2 = dt * dt
        dt3 = dt2 * dt
        dt4 = dt2 * dt2
        return sa2 * np.array([
            [dt4 / 4, 0.0,     dt3 / 2, 0.0    ],
            [0.0,     dt4 / 4, 0.0,     dt3 / 2],
            [dt3 / 2, 0.0,     dt2,     0.0    ],
            [0.0,     dt3 / 2, 0.0,     dt2    ],
        ], dtype=float)

    def _R(self, gps_accuracy: float) -> np.ndarray:
        """Measurement-noise covariance (isotropic)."""
        r = max(gps_accuracy, 0.5) ** 2
        return np.diag([r, r])

    def predict(self, dt: float) -> None:
        """Time-update step."""
        if dt <= 0:
            return
        F = self._F(dt)
        Q = self._Q(dt)
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q

    def update(self, measurement: np.ndarray, gps_accuracy: float) -> np.ndarray:
        """
        Measurement-update step.
        measurement : [px, py]
        Returns smoothed position [px, py].
        """
        self.measurement_count += 1
        z = np.asarray(measurement, dtype=float).reshape(2)
        H = np.array([[1.0, 0.0, 0.0, 0.0],
                      [0.0, 1.0, 0.0, 0.0]], dtype=float)
        R = self._R(gps_accuracy)

        # Classical Kalman gain
        S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.inv(S)

        # Logarithmic convergence: blend toward full gain.
        # Floor at 0.30 so early measurements are never completely ignored.
        c = self._get_convergence_factor()
        gain_scale = 0.30 + 0.70 * c
        K = K * gain_scale

        # Update
        innovation = z - H @ self.x
        self.x = self.x + K @ innovation
        I = np.eye(4)
        self.P = (I - K @ H) @ self.P
        # Numerical hygiene
        self.P = 0.5 * (self.P + self.P.T)
        min_eig = np.min(np.linalg.eigvalsh(self.P))
        if min_eig < 1e-8:
            self.P += np.eye(4) * (1e-6 - min_eig)

        return self.x[:2].copy()

    def process(
        self,
        t: float,
        measurement: np.ndarray,
        gps_accuracy: float,
    ) -> Tuple[np.ndarray, np.ndarray, float]:
        """
        Full predict + update cycle.

        Returns
        -------
        position : [px, py]
        velocity : [vx, vy]
        speed    : ||v||
        """
        if self.last_t is None:
            # First measurement – initialise position, leave velocity near zero
            self.x[:2] = measurement
            self.x[2:] = 0.0
            self.P = np.diag([gps_accuracy ** 2] * 2 + [4.0, 4.0])
            self.last_t = t
            self.last_velocity = self.x[2:].copy()
            self.measurement_count = 1
            return self.x[:2].copy(), self.x[2:].copy(), 0.0

        dt = max(t - self.last_t, 1e-3)
        # Guard against unrealistically large process noise when dt is large
        # (common with 30–60 s GPS intervals)
        dt_eff = min(dt, 15.0)
        self.predict(dt_eff)
        pos = self.update(measurement, gps_accuracy)
        vel = self.x[2:].copy()
        speed = float(np.linalg.norm(vel))

        self.last_t = t
        self.last_velocity = vel
        return pos, vel, speed

    def get_acceleration(self, current_velocity: np.ndarray, dt: float) -> float:
        """Finite-difference acceleration magnitude from successive velocities."""
        if self.last_velocity is None or dt <= 0:
            return 0.0
        a_vec = (current_velocity - self.last_velocity) / dt
        return float(np.linalg.norm(a_vec))

    def position_uncertainty(self) -> float:
        """Approximate 1-σ position uncertainty, capped for numerical stability."""
        P_pos = self.P[:2, :2]
        eig = np.linalg.eigvalsh(P_pos)
        unc = float(np.sqrt(np.mean(np.maximum(eig, 0.0))))
        # Cap at a realistic GPS-related upper bound
        return min(unc, 80.0)

    def reset(self):
        self.x = np.zeros(4)
        self.P = np.eye(4) * 100.0
        self.measurement_count = 0
        self.last_t = None
        self.last_velocity = None


def get_adaptive_thresholds(
    avg_gps_accuracy: float,
    convergence_factor: float = 1.0,
    position_uncertainty: float = 5.0,
) -> dict:
    """
    Adaptive thresholds that also incorporate filter uncertainty.

        τ(c) = 0.5 + 1.0 · c

    Additional inflation when the filter itself reports high uncertainty.
    """
    if avg_gps_accuracy < 5.0:
        base = {"epsilon_v_factor": 1.0, "epsilon_a_factor": 1.0, "quality": "EXCELLENT"}
    elif avg_gps_accuracy < 10.0:
        base = {"epsilon_v_factor": 1.3, "epsilon_a_factor": 1.2, "quality": "GOOD"}
    elif avg_gps_accuracy < 20.0:
        base = {"epsilon_v_factor": 1.8, "epsilon_a_factor": 1.6, "quality": "MODERATE"}
    else:
        base = {"epsilon_v_factor": 2.5, "epsilon_a_factor": 2.2, "quality": "POOR"}

    # Convergence phase tolerance
    phase_tolerance = 0.5 + 1.0 * convergence_factor

    # Extra inflation from filter covariance (normalised around 5 m)
    unc_factor = 1.0 + max(0.0, (position_uncertainty - 5.0) / 20.0)

    return {
        "epsilon_v_factor": base["epsilon_v_factor"] * phase_tolerance * unc_factor,
        "epsilon_a_factor": base["epsilon_a_factor"] * phase_tolerance * unc_factor,
        "quality": base["quality"],
        "convergence_factor": convergence_factor,
        "convergence_phase": (
            "early" if convergence_factor < 0.4
            else ("mid" if convergence_factor < 0.7 else "stable")
        ),
        "position_uncertainty": position_uncertainty,
        "uncertainty_factor": unc_factor,
    }
