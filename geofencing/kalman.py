"""
Signal processing: GPS smoothing and adaptive thresholding.

Contains the Kalman filter used to smooth noisy GPS coordinates, the
logarithmic convergence factor that controls how quickly the system trusts
new data, and the adaptive threshold scaling that ties velocity/acceleration
tolerances to that convergence factor.
"""
import numpy as np
import os


def compute_log_convergence_factor(measurements: int, baseline: int = 15) -> float:
    """Compute the logarithmic convergence factor.

    The model used here is:
        c(n) = min(1, ln(n + 1) / ln(B + 1))

    where:
        - n is the current number of measurements
        - B is the baseline number of samples for near-complete convergence

    This makes the system start conservatively and gradually increase trust as
    more evidence is collected.
    """
    if measurements <= 0:
        return 0.0
    if baseline <= 0:
        return 1.0
    return min(1.0, np.log(measurements + 1) / np.log(baseline + 1))


# Optional reproducibility
_seed = os.environ.get("GEOFENCE_SEED")
if _seed is not None:
    np.random.seed(int(_seed))

class KalmanFilter1D:
    """1D Kalman filter with logarithmic convergence for smoothing GPS coordinates"""
    def __init__(self, process_variance: float = 10.0, measurement_variance: float = 5.0,
                 enable_log_convergence: bool = True, baseline_convergence: int = 15):
        self.process_variance = process_variance
        self.measurement_variance = measurement_variance
        self.estimate = 0.0
        self.estimate_error = 1.0
        self.enable_log_convergence = enable_log_convergence
        self.baseline_convergence = baseline_convergence  # samples needed for full convergence
        self.measurement_count = 0
    
    def _get_convergence_factor(self) -> float:
        """Apply logarithmic convergence scaling.

        The gain is reduced early by the factor:
            c(n) = min(1, ln(n + 1) / ln(B + 1))
        where n is the count of measurements seen so far and B is the baseline
        number of samples needed for near-full convergence.

        This factor is then used to smooth the Kalman gain scaling so early
        measurements have less influence and later measurements are trusted more.
        """
        if not self.enable_log_convergence:
            return 1.0
        return compute_log_convergence_factor(
            self.measurement_count,
            self.baseline_convergence,
        )
    
    def update(self, measurement: float) -> float:
        """Process one measurement and return smoothed value with log convergence"""
        self.measurement_count += 1
        prediction = self.estimate
        prediction_error = self.estimate_error + self.process_variance
        kalman_gain = prediction_error / (prediction_error + self.measurement_variance)
        
        # Apply logarithmic convergence to gain (reduces early influence)
        convergence_factor = self._get_convergence_factor()
        adjusted_kalman_gain = kalman_gain * convergence_factor
        
        self.estimate = prediction + adjusted_kalman_gain * (measurement - prediction)
        self.estimate_error = (1 - adjusted_kalman_gain) * prediction_error
        return self.estimate
    
    def reset(self):
        self.estimate = 0.0
        self.estimate_error = 1.0
        self.measurement_count = 0

def get_adaptive_thresholds(avg_gps_accuracy: float, convergence_factor: float = 1.0) -> dict:
    """Adapt thresholds based on GPS accuracy and convergence state.

    The convergence adjustment uses:
        tau(c) = 0.5 + 1.0 * c

    where c is the logarithmic convergence factor. This gives a looser threshold
    when the system is still in its early phase and tighter thresholds once it is
    more stable.

    Args:
        avg_gps_accuracy: GPS accuracy in meters
        convergence_factor: Logarithmic convergence (0-1), reduces thresholds early on
    """
    if avg_gps_accuracy < 5.0:
        base_thresholds = {'epsilon_v_factor': 1.0, 'epsilon_a_factor': 1.0, 'quality': 'EXCELLENT'}
    elif avg_gps_accuracy < 10.0:
        base_thresholds = {'epsilon_v_factor': 1.3, 'epsilon_a_factor': 1.2, 'quality': 'GOOD'}
    elif avg_gps_accuracy < 20.0:
        base_thresholds = {'epsilon_v_factor': 1.8, 'epsilon_a_factor': 1.6, 'quality': 'MODERATE'}
    else:
        base_thresholds = {'epsilon_v_factor': 2.5, 'epsilon_a_factor': 2.2, 'quality': 'POOR'}
    
    # Apply convergence-based adjustment: early phase gets more tolerance.
    # Using tau(c) = 0.5 + 1.0 * c, the tolerance ranges from 0.5x at the start
    # (c close to 0) to 1.5x when fully converged (c close to 1).
    phase_tolerance = 0.5 + 1.0 * convergence_factor
    
    return {
        'epsilon_v_factor': base_thresholds['epsilon_v_factor'] * phase_tolerance,
        'epsilon_a_factor': base_thresholds['epsilon_a_factor'] * phase_tolerance,
        'quality': base_thresholds['quality'],
        'convergence_factor': convergence_factor,
        'convergence_phase': 'early' if convergence_factor < 0.4 else ('mid' if convergence_factor < 0.7 else 'stable')
    }
