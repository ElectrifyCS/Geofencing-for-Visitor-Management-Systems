"""
Synthetic GPS path generators (fully synthetic, no real data).
"""

from typing import List, Tuple
import numpy as np
from .models import Position


def _random_unit_vector() -> np.ndarray:
    v = np.random.randn(2)
    n = np.linalg.norm(v)
    if n == 0:
        return np.array([1.0, 0.0])
    return v / n


def generate_legitimate_path(
    center: Tuple[float, float],
    n_points: int = 10,
    gps_accuracy: float = 5.0,
) -> List[Position]:
    positions: List[Position] = []
    current = np.array(center, dtype=float) + np.array([-50.0, -50.0])
    for i in range(n_points):
        t = i * 60.0
        if i > 0:
            direction = _random_unit_vector()
            step = direction * np.random.uniform(15.0, 70.0)   # realistic walking
            current = current + step
        noise = np.random.normal(0, gps_accuracy, 2)
        noisy = current + noise
        positions.append(Position(t=t, x=float(noisy[0]), y=float(noisy[1]),
                                  gps_accuracy=gps_accuracy))
    return positions


def generate_spoofed_path(
    center: Tuple[float, float],
    n_points: int = 10,
    gps_accuracy: float = 5.0,
) -> List[Position]:
    positions: List[Position] = []
    current = np.array(center, dtype=float) + np.array([-50.0, -50.0])
    for i in range(n_points):
        t = i * 60.0
        if i == max(1, n_points // 2):
            current = current + np.array([3000.0, 2000.0])   # clear teleport
        else:
            direction = _random_unit_vector()
            step = direction * np.random.uniform(15.0, 70.0)
            current = current + step
        noise = np.random.normal(0, gps_accuracy, 2)
        noisy = current + noise
        positions.append(Position(t=t, x=float(noisy[0]), y=float(noisy[1]),
                                  gps_accuracy=gps_accuracy))
    return positions


def generate_extreme_spoofed_path(
    center: Tuple[float, float],
    n_points: int = 10,
    gps_accuracy: float = 5.0,
) -> List[Position]:
    positions: List[Position] = []
    current = np.array(center, dtype=float) + np.array([-100.0, -100.0])
    for i in range(n_points):
        t = i * 60.0
        if i == 2:
            current = current + np.array([10000.0, 7000.0])
        elif i == 4:
            current = np.array(center, dtype=float)
        elif i == 6:
            current = current + np.array([20000.0, -15000.0])
        elif i == 8:
            current = current + np.array([-15000.0, 18000.0])
        else:
            direction = _random_unit_vector()
            step = direction * np.random.uniform(15.0, 50.0)
            current = current + step
        noise = np.random.normal(0, gps_accuracy, 2)
        noisy = current + noise
        positions.append(Position(t=t, x=float(noisy[0]), y=float(noisy[1]),
                                  gps_accuracy=gps_accuracy))
    return positions
