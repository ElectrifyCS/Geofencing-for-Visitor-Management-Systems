"""
tag_lifecycle.py — Hardware & tag lifecycle management: rapid
provisioning, battery health prediction, and anti-passback / tag-drop
detection.

Math foundation (IB AA HL tie-ins):
  - BatteryHealthMonitor: least-squares linear regression on voltage vs.
    time to estimate discharge rate (dV/dt) and extrapolate time-to-
    threshold -- straight algebra/lines, and the same least-squares
    machinery already used in multilateration.py, applied to 1D data.
  - Speed anomaly ("thrown over a fence"): instantaneous speed from
    consecutive positions, reusing tracking.py's finite-difference
    velocity, flagged against a physically-plausible human speed bound.
  - Tag-drop detection ("left on a desk"): rolling variance of position
    over a sliding window -- statistics (variance/standard deviation),
    the mirror image of tracking.py's dwell-time anomaly (there: flag
    too-long-moving-through; here: flag too-long-not-moving-at-all).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from .tracking import PositionSample, compute_heading


# ---------------------------------------------------------------------------
# Rapid provisioning
# ---------------------------------------------------------------------------

@dataclass
class TagAssignment:
    tag_id: str
    guest_id: str
    assigned_at_s: float


class TagRegistry:
    """Pairs a physical tag ID to a guest profile and back, in O(1)."""

    def __init__(self) -> None:
        self._by_tag: Dict[str, TagAssignment] = {}

    def assign(self, tag_id: str, guest_id: str, timestamp_s: float) -> TagAssignment:
        if tag_id in self._by_tag:
            raise ValueError(f"Tag {tag_id} is already assigned to {self._by_tag[tag_id].guest_id}")
        assignment = TagAssignment(tag_id=tag_id, guest_id=guest_id, assigned_at_s=timestamp_s)
        self._by_tag[tag_id] = assignment
        return assignment

    def unassign(self, tag_id: str) -> Optional[TagAssignment]:
        return self._by_tag.pop(tag_id, None)

    def guest_for_tag(self, tag_id: str) -> Optional[str]:
        assignment = self._by_tag.get(tag_id)
        return assignment.guest_id if assignment else None


# ---------------------------------------------------------------------------
# Battery health
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BatteryReading:
    tag_id: str
    timestamp_s: float
    voltage: float


@dataclass
class BatteryPrediction:
    tag_id: str
    current_voltage: float
    discharge_rate_v_per_s: float  # negative while discharging
    seconds_to_threshold: Optional[float]  # None if not discharging / already below
    is_low: bool


def predict_battery(
    readings: List[BatteryReading],
    low_voltage_threshold: float,
    warn_window_s: float = 3600.0,
) -> Optional[BatteryPrediction]:
    """
    Least-squares linear fit of voltage vs. time: V(t) = slope*t + intercept.
    slope = sum((t_i - t_mean)(v_i - v_mean)) / sum((t_i - t_mean)^2)

    Flags a tag "low" either because it's already under threshold, or
    because it's on track to cross it within warn_window_s -- so a
    receptionist gets a warning before handing out a tag that'll die
    mid-visit, not just after it already has.
    """
    if len(readings) < 2:
        return None

    readings = sorted(readings, key=lambda r: r.timestamp_s)
    ts = [r.timestamp_s for r in readings]
    vs = [r.voltage for r in readings]
    t_mean = sum(ts) / len(ts)
    v_mean = sum(vs) / len(vs)

    numerator = sum((t - t_mean) * (v - v_mean) for t, v in zip(ts, vs))
    denominator = sum((t - t_mean) ** 2 for t in ts)
    if denominator == 0:
        return None

    slope = numerator / denominator  # dV/dt
    intercept = v_mean - slope * t_mean

    current_voltage = vs[-1]
    current_time = ts[-1]

    seconds_to_threshold: Optional[float] = None
    if slope < 0:
        # Solve V(t) = threshold for t, relative to the latest reading.
        t_at_threshold = (low_voltage_threshold - intercept) / slope
        seconds_to_threshold = t_at_threshold - current_time
        if seconds_to_threshold < 0:
            seconds_to_threshold = 0.0

    is_low = (
        current_voltage <= low_voltage_threshold
        or (seconds_to_threshold is not None and seconds_to_threshold <= warn_window_s)
    )

    return BatteryPrediction(
        tag_id=readings[-1].tag_id,
        current_voltage=current_voltage,
        discharge_rate_v_per_s=slope,
        seconds_to_threshold=seconds_to_threshold,
        is_low=is_low,
    )


# ---------------------------------------------------------------------------
# Anti-passback & tag-drop detection
# ---------------------------------------------------------------------------

@dataclass
class SpeedAnomaly:
    entity_id: str
    speed_mps: float
    plausible_max_mps: float
    timestamp_s: float


def check_speed_anomaly(
    prev: PositionSample,
    curr: PositionSample,
    plausible_max_mps: float = 6.0,  # ~sprint pace; tune per deployment
    confidence_k: float = 2.0,
) -> Optional[SpeedAnomaly]:
    """
    Flags 'thrown over a fence'-type anomalies: instantaneous speed that
    exceeds what's physically plausible for a person, discounted by
    position uncertainty so filter jitter at low speed doesn't false-fire.
    """
    heading = compute_heading(prev, curr)
    if heading is None:
        return None

    combined_sigma = math.sqrt(prev.sigma_m**2 + curr.sigma_m**2)
    dt = curr.timestamp_s - prev.timestamp_s
    # Uncertainty in position translates to uncertainty in speed via dt.
    speed_sigma = (confidence_k * combined_sigma) / dt if dt > 0 else 0.0

    if (heading.speed_mps - speed_sigma) > plausible_max_mps:
        return SpeedAnomaly(
            entity_id=curr.entity_id,
            speed_mps=heading.speed_mps,
            plausible_max_mps=plausible_max_mps,
            timestamp_s=curr.timestamp_s,
        )
    return None


@dataclass
class TagDropAlert:
    entity_id: str
    stationary_for_s: float
    position_std_m: float


class TagDropDetector:
    """
    Flags a tag as likely removed and left stationary (lanyard on a desk)
    when the standard deviation of its position over a sliding window
    stays near zero for longer than the configured duration -- the
    statistical mirror of dwell-time monitoring (too-still, not
    too-long).
    """

    def __init__(self, window_s: float = 7200.0, std_threshold_m: float = 0.5):
        self.window_s = window_s
        self.std_threshold_m = std_threshold_m
        self._history: Dict[str, Deque[PositionSample]] = {}

    def update(self, sample: PositionSample) -> Optional[TagDropAlert]:
        history = self._history.setdefault(sample.entity_id, deque())
        history.append(sample)

        # Drop anything older than the window.
        cutoff = sample.timestamp_s - self.window_s
        while history and history[0].timestamp_s < cutoff:
            history.popleft()

        span_s = history[-1].timestamp_s - history[0].timestamp_s
        if span_s < self.window_s or len(history) < 3:
            return None  # not enough history yet to judge

        xs = [p.position[0] for p in history]
        ys = [p.position[1] for p in history]
        x_mean, y_mean = sum(xs) / len(xs), sum(ys) / len(ys)
        variance = sum((x - x_mean) ** 2 + (y - y_mean) ** 2 for x, y in zip(xs, ys)) / len(xs)
        std = math.sqrt(variance)

        if std < self.std_threshold_m:
            return TagDropAlert(entity_id=sample.entity_id, stationary_for_s=span_s, position_std_m=std)
        return None


if __name__ == "__main__":
    print("=== Rapid provisioning ===")
    registry = TagRegistry()
    registry.assign("TAG-042", "GUEST-JDOE", timestamp_s=0.0)
    print(f"  TAG-042 -> {registry.guest_for_tag('TAG-042')}")
    registry.unassign("TAG-042")
    print(f"  after checkout: TAG-042 -> {registry.guest_for_tag('TAG-042')}")

    print("\n=== Battery health ===")
    readings = [
        BatteryReading("TAG-042", 0.0, 4.10),
        BatteryReading("TAG-042", 3600.0, 4.02),
        BatteryReading("TAG-042", 7200.0, 3.94),
    ]
    prediction = predict_battery(readings, low_voltage_threshold=3.30, warn_window_s=3600.0 * 6)
    hrs_remaining = prediction.seconds_to_threshold / 3600.0 if prediction.seconds_to_threshold else None
    print(f"  current={prediction.current_voltage}V, rate={prediction.discharge_rate_v_per_s * 3600:.4f} V/hr, "
          f"time to 3.30V threshold: {hrs_remaining:.1f} hrs" if hrs_remaining else "  not discharging")
    print(f"  flagged low? {prediction.is_low}")

    print("\n=== Anti-passback: speed anomaly ===")
    p1 = PositionSample("VIS-9", 0.0, (0.0, 0.0, 1.5), sigma_m=0.2)
    p2_normal = PositionSample("VIS-9", 1.0, (1.2, 0.0, 1.5), sigma_m=0.2)   # 1.2 m/s, walking
    p2_thrown = PositionSample("VIS-9", 1.0, (18.0, 0.0, 1.5), sigma_m=0.2)  # 18 m/s in 1s

    print(f"  normal walk: {check_speed_anomaly(p1, p2_normal)}")
    anomaly = check_speed_anomaly(p1, p2_thrown)
    print(f"  thrown case: speed={anomaly.speed_mps:.1f} m/s > plausible {anomaly.plausible_max_mps} m/s -> FLAGGED")

    print("\n=== Anti-passback: tag drop ===")
    detector = TagDropDetector(window_s=7200.0, std_threshold_m=0.5)
    result = None
    # Tag sits within a 10cm jitter radius for 2+ hours (left on a desk).
    import random
    random.seed(3)
    t = 0.0
    while t <= 7300.0:
        jitter_x = random.uniform(-0.05, 0.05)
        jitter_y = random.uniform(-0.05, 0.05)
        sample = PositionSample("TAG-042", t, (12.0 + jitter_x, 4.0 + jitter_y, 0.9))
        result = detector.update(sample)
        t += 300.0  # a position update every 5 minutes
    if result:
        print(f"  stationary {result.stationary_for_s / 3600:.2f} hrs, "
              f"position std {result.position_std_m * 100:.1f}cm -> TAG DROP FLAGGED")
    else:
        print("  no tag drop detected")
