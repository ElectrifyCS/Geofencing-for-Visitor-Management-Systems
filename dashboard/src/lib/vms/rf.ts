/**
 * Lightweight RF/path-loss layer standing in for the accuracy figures a
 * real BLE/UWB positioning stack reports alongside every fix. Nothing the
 * dashboard shows an operator as "distance" or "confidence" is read
 * straight off ground truth — it's derived from a noisy simulated RSSI
 * reading, the same gap between true position and measured position that
 * Geofencing.py's Kalman layer exists to close.
 *
 * Log-distance path-loss model:
 *   RSSI(d) = TX_POWER_1M - 10 * n * log10(d) + shadowing noise
 *
 * n (path-loss exponent) sits ~2.0-3.5 indoors depending on construction;
 * shadowing is modeled as zero-mean Gaussian noise in the dB domain
 * (log-normal shadowing), which is the standard way indoor BLE ranging
 * error is characterized.
 */

import { gaussian } from "./beacon";

export const TX_POWER_DBM_AT_1M = -59; // calibrated RSSI at 1m, typical BLE beacon
export const PATH_LOSS_EXPONENT = 2.35; // indoor office: drywall partitions + furniture
export const SHADOWING_SIGMA_DB = 3.4; // log-normal shadowing std dev

export function rssiFromDistance(distanceM: number): number {
  const d = Math.max(0.1, distanceM);
  const pathLoss = 10 * PATH_LOSS_EXPONENT * Math.log10(d);
  return TX_POWER_DBM_AT_1M - pathLoss + gaussian() * SHADOWING_SIGMA_DB;
}

export function distanceFromRssi(rssiDbm: number): number {
  const exp = (TX_POWER_DBM_AT_1M - rssiDbm) / (10 * PATH_LOSS_EXPONENT);
  return Math.pow(10, exp);
}

/**
 * Direct port of geofencing/kalman.py::compute_log_convergence_factor.
 * Confidence in a fix builds quickly over the first few readings, then
 * asymptotes to 1 as more measurements accumulate — same shape as
 * covariance shrinking with each Kalman update.
 *
 *   c(n) = min(1, ln(n + 1) / ln(baseline + 1))
 */
export function logConvergenceFactor(measurements: number, baseline = 15): number {
  if (measurements <= 0) return 0;
  if (baseline <= 0) return 1;
  return Math.min(1, Math.log(measurements + 1) / Math.log(baseline + 1));
}

export type RangeReading = {
  estDistanceM: number;
  rssiDbm: number;
  confidence: number; // 0..1
  accuracyM: number; // 1-sigma-ish radius
};

/**
 * EMA-smoothed RSSI-to-distance estimator for one tracked pair (tag vs.
 * reader, or tag vs. tag for tethering). Mirrors BeaconLockTracker's
 * smoothing so a single bad multipath reading can't flip a determination.
 */
export class RangeEstimator {
  private smoothedRssi: number | null = null;
  private readingCount = 0;
  readonly smoothingAlpha: number;

  constructor(smoothingAlpha = 0.35) {
    this.smoothingAlpha = smoothingAlpha;
  }

  observe(trueDistanceM: number): RangeReading {
    const raw = rssiFromDistance(trueDistanceM);
    this.smoothedRssi =
      this.smoothedRssi == null
        ? raw
        : this.smoothingAlpha * raw + (1 - this.smoothingAlpha) * this.smoothedRssi;
    this.readingCount += 1;
    const confidence = logConvergenceFactor(this.readingCount);
    const estDistanceM = distanceFromRssi(this.smoothedRssi);
    // Ranging error grows with distance and shrinks as confidence
    // converges — same shape as covariance shrinking with more updates.
    const baseAccuracy = 0.4 + estDistanceM * 0.09;
    const accuracyM = baseAccuracy * (1.6 - 0.6 * confidence);
    return { estDistanceM, rssiDbm: this.smoothedRssi, confidence, accuracyM };
  }

  reset(): void {
    this.smoothedRssi = null;
    this.readingCount = 0;
  }
}
