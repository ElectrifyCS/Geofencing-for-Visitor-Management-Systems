"""
elevator_tracking.py -- 1D vertical position tracking for an elevator
car, fusing car-mounted accelerometer/IMU readings with shaft-beacon
floor-crossing events.

Hardware confirmed for this deployment: beacons at each floor landing
PLUS an accelerometer/IMU on the car itself -- not beacon-only floor
classification. That combination is exactly what this module is built
for: the accelerometer drives continuous dead-reckoning between floors,
and each beacon crossing corrects accumulated drift, the same role GPS
plays for IMU dead-reckoning in vehicle navigation.

Why this is a *better* problem than general indoor 3D positioning: the
car's (x, y) never changes, it's locked to the shaft. There's no
geometry to solve, no GDOP, no anchor-height-spread weakness like we
found in multilateration.py -- z is the only unknown, so a 1D Kalman
filter is the right tool, not trilateration.

Math foundation:
  - ElevatorKalman1D: a kinematic Kalman filter with control input.
    State [z, v_z]; prediction integrates *measured* acceleration
    (calculus -- double integration of a(t) gives z(t)), not an assumed
    constant-velocity model. This is more accurate than the pedestrian
    Kalman filter in kalman.py because elevator motion is smoother and
    the accelerometer gives real, not inferred, kinematics.
  - Beacon correction: a standard Kalman measurement update with
    H = [1, 0] -- we only observe position directly, never velocity --
    and very low measurement noise, since an RSSI proximity peak at a
    known, surveyed beacon height pins z tightly.
  - Anomaly checks: same statistical-threshold pattern as
    tag_lifecycle.py's speed-anomaly and stationary-tag-drop detection,
    applied to one axis instead of two.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np


@dataclass(frozen=True)
class FloorBeacon:
    floor_id: str
    z_height: float  # metres, surveyed height from shaft base


class BeaconRegistry:
    """
    Maps a beacon's broadcast ID to its surveyed floor height. Corrections
    always look up the SPECIFIC beacon that was actually detected, never
    assume floors are crossed in sequence. A missed detection then just
    means one fewer correction opportunity -- not a wrong one.

    (Earlier version of this module walked a sequential index instead;
    a missed detection there caused the filter to be corrected against
    the WRONG floor's height, up to a full floor-height's worth of false
    correction, confidently. Identity-based lookup can't make that
    mistake -- it either recognises the beacon it heard, or it doesn't
    correct at all.)
    """

    def __init__(self, beacons: List[FloorBeacon]):
        self._by_id: Dict[str, FloorBeacon] = {b.floor_id: b for b in beacons}

    def lookup(self, beacon_id: str) -> Optional[FloorBeacon]:
        return self._by_id.get(beacon_id)

    def nearest_within_range(self, z: float, detection_radius_m: float) -> Optional[FloorBeacon]:
        """
        Which beacon (if any) would a real receiver actually hear right
        now: the closest one within detection range, by physical
        proximity -- not by any assumption about ride order.
        """
        candidates = [b for b in self._by_id.values() if abs(b.z_height - z) <= detection_radius_m]
        if not candidates:
            return None
        return min(candidates, key=lambda b: abs(b.z_height - z))

    def all_within_range(self, z: float, detection_radius_m: float) -> Dict[str, float]:
        """
        Every beacon currently detectable, with its implied distance --
        not just the single nearest one. Near the midpoint between two
        floors, more than one beacon can be weakly audible at once; a
        lock tracker needs to see all of them to smooth and hysteresis
        the identity decision, not just whichever won a single instant.
        """
        return {
            b.floor_id: abs(b.z_height - z)
            for b in self._by_id.values()
            if abs(b.z_height - z) <= detection_radius_m
        }


class BeaconLockTracker:
    """
    Prevents beacon-identity ping-ponging near the midpoint between two
    floors: real RSSI-derived distance is noisy, and near a boundary two
    beacons can be nearly equidistant, so a naive "whichever is closer
    right now" decision flips on noise alone -- confirmed tonight during
    on-site testing.

    Two defences, matching standard BLE presence-detection practice:
      - Smoothing: an exponential moving average over each candidate
        beacon's distance readings, so a single noisy sample can't swing
        the decision on its own.
      - Hysteresis: the locked beacon only changes when a challenger's
        *smoothed* distance beats the current lock's by more than
        hysteresis_m -- not just by being marginally closer. This is the
        same asymmetric enter/exit-threshold idea used in geofence
        debouncing generally: harder to leave a state than to enter it.
    """

    def __init__(self, hysteresis_m: float = 0.5, smoothing_alpha: float = 0.3):
        self.hysteresis_m = hysteresis_m
        self.smoothing_alpha = smoothing_alpha
        self._smoothed: Dict[str, float] = {}
        self.locked_beacon_id: Optional[str] = None

    def update(self, raw_readings: Dict[str, float]) -> Optional[str]:
        """
        raw_readings: {beacon_id: raw_distance_estimate_m} for every
        beacon detectable at all this instant (see
        BeaconRegistry.all_within_range). Returns the currently locked
        beacon ID, which may be unchanged from before this call.
        """
        for beacon_id, raw_distance in raw_readings.items():
            previous = self._smoothed.get(beacon_id, raw_distance)
            self._smoothed[beacon_id] = (
                self.smoothing_alpha * raw_distance + (1 - self.smoothing_alpha) * previous
            )

        if not raw_readings:
            # Nothing detectable at all right now -- no new information,
            # keep whatever lock already exists rather than guessing.
            return self.locked_beacon_id

        # Only beacons actually heard THIS update are eligible. Without
        # this restriction, a beacon's smoothed value from minutes ago --
        # now completely out of range -- can keep "winning" forever
        # simply because it used to be very close (confirmed: this was a
        # real bug, not hypothetical -- see module tests).
        candidates = {bid: self._smoothed[bid] for bid in raw_readings}
        best_id = min(candidates, key=candidates.get)

        if self.locked_beacon_id is None or self.locked_beacon_id not in candidates:
            # No existing lock, or the current lock isn't even audible
            # right now -- adopt the best currently-heard candidate.
            self.locked_beacon_id = best_id
        elif best_id != self.locked_beacon_id:
            current_smoothed = candidates[self.locked_beacon_id]
            challenger_smoothed = candidates[best_id]
            if current_smoothed - challenger_smoothed > self.hysteresis_m:
                self.locked_beacon_id = best_id

        return self.locked_beacon_id


class ElevatorKalman1D:
    """
    Tracks a single elevator car's vertical position and velocity along
    its shaft. Call predict() on every accelerometer sample, and
    correct_with_beacon() whenever a floor beacon crossing is detected.
    """

    def __init__(
        self,
        initial_z: float = 0.0,
        initial_v: float = 0.0,
        accel_noise_std: float = 0.05,   # m/s^2, accelerometer sensor noise
        beacon_noise_std: float = 0.15,  # m, uncertainty in a beacon-crossing fix
    ):
        self.x = np.array([initial_z, initial_v], dtype=float)
        self.P = np.eye(2) * 1.0
        self.accel_noise_std = accel_noise_std
        self.beacon_noise_std = beacon_noise_std

    def predict(self, dt: float, measured_accel: float) -> None:
        """
        Advance the state using the accelerometer as a control input:
          z' = z + v*dt + 0.5*a*dt^2
          v' = v + a*dt
        Process noise Q is the standard discretized constant-acceleration
        model, scaled by the accelerometer's own noise -- an inherently
        noisier accelerometer widens the filter's uncertainty faster,
        which is exactly what should happen.
        """
        F = np.array([[1.0, dt], [0.0, 1.0]])
        B = np.array([0.5 * dt**2, dt])
        self.x = F @ self.x + B * measured_accel

        q = self.accel_noise_std**2
        Q = q * np.array([
            [dt**4 / 4, dt**3 / 2],
            [dt**3 / 2, dt**2],
        ])
        self.P = F @ self.P @ F.T + Q

    def correct_with_beacon(self, beacon_z: float, detection_radius_m: float = 0.0) -> None:
        """
        Measurement update: a floor beacon crossing pins position (not
        velocity). If detection is proximity-based -- the receiver only
        knows it's SOMEWHERE within detection_radius_m of the beacon, not
        exactly at it -- that radius contributes its own uncertainty on
        top of the beacon reading's own noise, not instead of it. Modelled
        as uniform over the detection window: variance = radius^2 / 3.
        Ignoring this (treating "detected" as "exactly here") understates
        how wrong a wide-radius correction can be.
        """
        H = np.array([1.0, 0.0])
        detection_variance = (detection_radius_m ** 2) / 3.0
        R = self.beacon_noise_std**2 + detection_variance
        y = beacon_z - H @ self.x
        S = H @ self.P @ H.T + R
        K = self.P @ H.T / S
        self.x = self.x + K * y
        self.P = (np.eye(2) - np.outer(K, H)) @ self.P

    @property
    def z(self) -> float:
        return float(self.x[0])

    @property
    def v(self) -> float:
        return float(self.x[1])

    @property
    def z_std(self) -> float:
        return float(math.sqrt(self.P[0, 0]))

    @property
    def v_std(self) -> float:
        return float(math.sqrt(self.P[1, 1]))


def resolve_floor(kalman: ElevatorKalman1D, beacons: List[FloorBeacon]) -> FloorBeacon:
    """Nearest surveyed floor beacon to the filter's current z estimate."""
    return min(beacons, key=lambda b: abs(b.z_height - kalman.z))


def correct_with_identified_beacon(
    kalman: ElevatorKalman1D,
    beacon_id: str,
    registry: BeaconRegistry,
    noisy_z: float,
    detection_radius_m: float = 0.0,
) -> Tuple[bool, str]:
    """
    The actual production path: hardware reports a beacon ID it heard
    (plus a noisy z reading), software looks that ID up and corrects --
    or safely declines if the ID isn't recognised, rather than guessing.
    This is the method a real integration should call; the demo below
    exercises it directly rather than only the lower-level proximity
    helper used to *simulate* what hardware would detect.
    """
    beacon = registry.lookup(beacon_id)
    if beacon is None:
        return False, f"Unrecognised beacon ID '{beacon_id}' -- ignoring, not correcting"
    kalman.correct_with_beacon(noisy_z, detection_radius_m=detection_radius_m)
    return True, f"Corrected using beacon {beacon_id} (surveyed z={beacon.z_height}m)"


def check_speed_anomaly(
    kalman: ElevatorKalman1D,
    rated_max_speed_mps: float,
    confidence_k: float = 2.0,
) -> Optional[str]:
    """
    Flags vertical speed exceeding the car's rated maximum, discounted
    by the filter's own velocity uncertainty so early-transit noise
    doesn't false-fire. Same pattern as tag_lifecycle.py's
    check_speed_anomaly, applied to one axis.
    """
    confident_speed = abs(kalman.v) - confidence_k * kalman.v_std
    if confident_speed > rated_max_speed_mps:
        return f"SPEED ANOMALY: {abs(kalman.v):.2f} m/s exceeds rated max {rated_max_speed_mps} m/s"
    return None


def check_stuck_between_floors(
    kalman: ElevatorKalman1D,
    beacons: List[FloorBeacon],
    still_threshold_mps: float = 0.05,
    min_distance_from_floor_m: float = 0.5,
) -> Optional[str]:
    """
    Flags a car that has stopped moving but isn't at a known floor --
    the vertical-shaft mirror of tag_lifecycle.py's stationary tag-drop
    detection: there, "too still for too long" meant a dropped tag;
    here it means a car stuck mid-shaft.
    """
    nearest = resolve_floor(kalman, beacons)
    distance = abs(nearest.z_height - kalman.z)
    if abs(kalman.v) < still_threshold_mps and distance > min_distance_from_floor_m:
        return f"STUCK: car stopped {distance:.2f}m from nearest floor ({nearest.floor_id})"
    return None


if __name__ == "__main__":
    # --- Realistic ride: 8 floors, 3.5m each, trapezoidal velocity profile ---
    rng = np.random.default_rng(seed=11)

    floor_height = 3.5
    n_floors = 8
    beacons = [FloorBeacon(f"F{i+1}", i * floor_height) for i in range(n_floors)]
    target_z = beacons[-1].z_height  # riding from F1 to F8

    a_accel = 1.0   # m/s^2
    v_max = 2.5     # m/s
    dt = 0.1

    t_accel = v_max / a_accel
    d_accel = 0.5 * a_accel * t_accel**2
    d_cruise = target_z - 2 * d_accel
    t_cruise = d_cruise / v_max
    t_total = 2 * t_accel + t_cruise

    print(f"Ride profile: {target_z}m over {t_total:.1f}s "
          f"(accelerate {t_accel:.1f}s, cruise {t_cruise:.1f}s, decelerate {t_accel:.1f}s)\n")

    def true_accel(t):
        if t < t_accel:
            return a_accel
        elif t < t_accel + t_cruise:
            return 0.0
        elif t < t_total:
            return -a_accel
        return 0.0

    def true_z_exact(t):
        """
        Closed-form position at time t, piecewise per phase -- used as
        ground truth instead of Euler-integrating the profile, which
        leaves a small but real discretization artifact (confirmed: up
        to 25cm at dt=0.1, not physical, just integration error) that
        would understate how good the filter's beacon-corrected estimate
        actually is relative to true position.
        """
        if t <= t_accel:
            return 0.5 * a_accel * t**2
        elif t <= t_accel + t_cruise:
            return d_accel + v_max * (t - t_accel)
        else:
            tau = t - (t_accel + t_cruise)
            return d_accel + d_cruise + v_max * tau - 0.5 * a_accel * tau**2

    def true_v_exact(t):
        if t <= t_accel:
            return a_accel * t
        elif t <= t_accel + t_cruise:
            return v_max
        else:
            tau = t - (t_accel + t_cruise)
            return max(0.0, v_max - a_accel * tau)

    # --- Run WITH beacon corrections, using identity-based detection ---
    registry = BeaconRegistry(beacons)
    detection_radius = 0.3  # metres -- realistic short-range BLE proximity
    lock_tracker = BeaconLockTracker(hysteresis_m=0.5, smoothing_alpha=0.3)

    kf = ElevatorKalman1D(initial_z=0.0, initial_v=0.0)
    t = 0.0
    crossing_log = []
    corrected_ids: set = set()

    # --- Also run a pure dead-reckoning filter, no beacon corrections at all ---
    kf_no_correction = ElevatorKalman1D(initial_z=0.0, initial_v=0.0)
    accel_bias = 0.02  # small constant sensor bias, realistic for a real IMU

    while t < t_total:
        a_true = true_accel(t)
        true_z = true_z_exact(t + dt)

        a_measured = a_true + accel_bias + rng.normal(0, 0.05)
        kf.predict(dt, a_measured)
        kf_no_correction.predict(dt, a_measured)

        # Identity-based detection with smoothing + hysteresis: every
        # beacon audible right now (not just the nearest one), fed
        # through the lock tracker so noise near a floor boundary can't
        # flip the identity decision -- confirmed necessary tonight.
        raw_readings = registry.all_within_range(true_z, detection_radius)
        noisy_readings = {bid: dist + rng.normal(0, 0.1) for bid, dist in raw_readings.items()}
        locked_id = lock_tracker.update(noisy_readings)

        if locked_id is not None and locked_id not in corrected_ids:
            beacon = registry.lookup(locked_id)
            noisy_reading = beacon.z_height + rng.normal(0, 0.15)
            applied, reason = correct_with_identified_beacon(
                kf, locked_id, registry, noisy_reading, detection_radius_m=detection_radius
            )
            if applied:
                crossing_log.append((t, locked_id, kf.z, true_z))
                corrected_ids.add(locked_id)

        t += dt

    true_z_final = true_z_exact(t_total)

    print("Floor crossings (with beacon correction):")
    for t_cross, floor_id, est_z, true_z_at_cross in crossing_log:
        print(f"  t={t_cross:5.1f}s  {floor_id}  estimate={est_z:6.2f}m  "
              f"true={true_z_at_cross:6.2f}m  error={abs(est_z - true_z_at_cross)*100:.1f}cm")

    print(f"\nFinal position -- with beacon correction:    "
          f"estimate={kf.z:.2f}m, true={true_z_final:.2f}m, error={abs(kf.z - true_z_final)*100:.1f}cm")
    print(f"Final position -- pure dead-reckoning (no beacons): "
          f"estimate={kf_no_correction.z:.2f}m, true={true_z_final:.2f}m, "
          f"error={abs(kf_no_correction.z - true_z_final)*100:.1f}cm")

    # --- Anomaly checks ---
    print("\n=== Anomaly detection ===")
    fast_kf = ElevatorKalman1D(initial_z=10.0, initial_v=6.0)  # way over rated speed
    fast_kf.P = np.eye(2) * 0.01  # tight uncertainty so the check isn't swallowed by noise
    result = check_speed_anomaly(fast_kf, rated_max_speed_mps=3.0)
    print(f"Speed check (6.0 m/s vs 3.0 rated): {result}")

    stuck_kf = ElevatorKalman1D(initial_z=9.0, initial_v=0.0)  # between F3 (7.0) and F4 (10.5)
    stuck_kf.P = np.eye(2) * 0.01
    result = check_stuck_between_floors(stuck_kf, beacons)
    print(f"Stuck check (car at 9.0m, between floors): {result}")

    normal_kf = ElevatorKalman1D(initial_z=7.0, initial_v=0.0)  # sitting right at F3
    normal_kf.P = np.eye(2) * 0.01
    result = check_stuck_between_floors(normal_kf, beacons)
    print(f"Stuck check (car at 7.0m, exactly at F3):  {result if result else '(no alert -- correctly at a floor)'}")

    # --- Unknown beacon ID: RF interference / neighbouring-building leak ---
    print("\n=== Unrecognised beacon ID handling ===")
    garbled_kf = ElevatorKalman1D(initial_z=9.0, initial_v=0.0)
    z_before = garbled_kf.z
    applied, reason = correct_with_identified_beacon(
        garbled_kf, "F99-GARBLED", registry, noisy_z=100.0
    )
    print(f"  Reported ID 'F99-GARBLED': applied={applied} -- {reason}")
    print(f"  Position unchanged: {z_before}m -> {garbled_kf.z}m (correctly ignored, not corrected to 100m)")

    # --- Ping-pong stress test: car parked exactly at the midpoint      ---
    # --- between two floor beacons -- the worst case for identity flip- ---
    # --- flopping, found during on-site testing.                       ---
    print("\n=== Beacon ping-pong stress test (car at exact midpoint between two floors) ===")
    stress_rng = np.random.default_rng(seed=99)
    true_dist_a, true_dist_b = 1.75, 1.75  # equidistant -- worst case
    naive_flips, naive_current = 0, None
    stress_tracker = BeaconLockTracker(hysteresis_m=0.5, smoothing_alpha=0.3)
    tracker_flips, tracker_prev = 0, None
    for _ in range(40):
        readings = {
            "F4": true_dist_a + stress_rng.normal(0, 0.3),
            "F5": true_dist_b + stress_rng.normal(0, 0.3),
        }
        naive_winner = min(readings, key=readings.get)
        if naive_current is not None and naive_winner != naive_current:
            naive_flips += 1
        naive_current = naive_winner

        locked = stress_tracker.update(readings)
        if tracker_prev is not None and locked != tracker_prev:
            tracker_flips += 1
        tracker_prev = locked

    print(f"  Naive instantaneous-nearest: {naive_flips} identity flips over 40 readings")
    print(f"  Smoothed + hysteresis lock:  {tracker_flips} identity flips over 40 readings")
