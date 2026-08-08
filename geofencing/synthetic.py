"""
Synthetic GPS path generators used by the demo (fully synthetic, no real data).
"""
from typing import List, Tuple
import numpy as np

from .models import Position


def _random_unit_vector() -> np.ndarray:
    v = np.random.randn(2)
    norm = np.linalg.norm(v)
    if norm == 0:
        return np.array([1.0, 0.0])
    return v / norm

def generate_legitimate_path(center: Tuple[float, float], n_points: int = 10, 
                            gps_accuracy: float = 5.0) -> List[Position]:
    """Generate a realistic walking/driving path with GPS noise"""
    positions: List[Position] = []
    current = np.array(center, dtype=float) + np.array([-50.0, -50.0])
    for i in range(n_points):
        t = i * 60.0
        if i > 0:
            direction = _random_unit_vector()
            step = direction * np.random.uniform(20.0, 80.0)  # 20-80m per minute
            current = current + step
        # Add realistic GPS noise
        noise = np.random.normal(0, gps_accuracy, 2)
        noisy_pos = current + noise
        positions.append(Position(t=t, x=float(noisy_pos[0]), y=float(noisy_pos[1]), 
                                 gps_accuracy=gps_accuracy))
    return positions

def generate_spoofed_path(center: Tuple[float, float], n_points: int = 10,
                         gps_accuracy: float = 5.0) -> List[Position]:
    """Generate a path with an obvious teleportation (spoofing) and GPS noise"""
    positions: List[Position] = []
    current = np.array(center, dtype=float) + np.array([-50.0, -50.0])
    for i in range(n_points):
        t = i * 60.0
        if i == max(1, n_points // 2):
            # Teleport jump - too large to be GPS noise
            current = current + np.array([3000.0, 2000.0])
        else:
            direction = _random_unit_vector()
            step = direction * np.random.uniform(20.0, 80.0)
            current = current + step
        # Add realistic GPS noise
        noise = np.random.normal(0, gps_accuracy, 2)
        noisy_pos = current + noise
        positions.append(Position(t=t, x=float(noisy_pos[0]), y=float(noisy_pos[1]),
                                 gps_accuracy=gps_accuracy))
    return positions

def generate_extreme_spoofed_path(center: Tuple[float, float], n_points: int = 10,
                                 gps_accuracy: float = 5.0) -> List[Position]:
    """Generate an extremely spoofed path with multiple teleportations to trigger CRITICAL alerts"""
    positions: List[Position] = []
    current = np.array(center, dtype=float) + np.array([-100.0, -100.0])
    
    for i in range(n_points):
        t = i * 60.0
        
        # Add MULTIPLE dramatic jumps for extreme spoofing
        if i == 2:
            # First teleportation: 10km away instantly (impossible speed)
            current = current + np.array([10000.0, 7000.0])
        elif i == 4:
            # Second teleportation: back to origin in 1 second (impossible)
            current = np.array(center, dtype=float)
        elif i == 6:
            # Third teleportation: 20km away instantly
            current = current + np.array([20000.0, -15000.0])
        elif i == 8:
            # Fourth teleportation: diagonal jump
            current = current + np.array([-15000.0, 18000.0])
        else:
            # Normal movement on other intervals
            direction = _random_unit_vector()
            step = direction * np.random.uniform(20.0, 50.0)
            current = current + step
        
        # Add realistic GPS noise
        noise = np.random.normal(0, gps_accuracy, 2)
        noisy_pos = current + noise
        positions.append(Position(t=t, x=float(noisy_pos[0]), y=float(noisy_pos[1]), 
                                 gps_accuracy=gps_accuracy))
    
    return positions

