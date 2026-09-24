/**
 * Port of BeaconRegistry + BeaconLockTracker from elevator_tracking.py.
 * Identity-based lookup (never sequential index) + EMA smoothing + hysteresis
 * so RSSI noise at a floor midpoint cannot flip the locked landing.
 */

export type FloorBeacon = {
  floor_id: string;
  z_height: number;
};

export class BeaconRegistry {
  private byId: Map<string, FloorBeacon>;

  constructor(beacons: FloorBeacon[]) {
    this.byId = new Map(beacons.map((b) => [b.floor_id, b]));
  }

  lookup(beaconId: string): FloorBeacon | undefined {
    return this.byId.get(beaconId);
  }

  all(): FloorBeacon[] {
    return [...this.byId.values()].sort((a, b) => a.z_height - b.z_height);
  }

  allWithinRange(z: number, detectionRadiusM: number): Record<string, number> {
    const out: Record<string, number> = {};
    for (const b of this.byId.values()) {
      const d = Math.abs(b.z_height - z);
      if (d <= detectionRadiusM) out[b.floor_id] = d;
    }
    return out;
  }
}

export class BeaconLockTracker {
  hysteresisM: number;
  smoothingAlpha: number;
  lockedBeaconId: string | null = null;
  private smoothed: Record<string, number> = {};

  constructor(hysteresisM = 0.5, smoothingAlpha = 0.3) {
    this.hysteresisM = hysteresisM;
    this.smoothingAlpha = smoothingAlpha;
  }

  reset(): void {
    this.lockedBeaconId = null;
    this.smoothed = {};
  }

  update(rawReadings: Record<string, number>): string | null {
    for (const [beaconId, raw] of Object.entries(rawReadings)) {
      const previous = this.smoothed[beaconId] ?? raw;
      this.smoothed[beaconId] =
        this.smoothingAlpha * raw + (1 - this.smoothingAlpha) * previous;
    }

    const ids = Object.keys(rawReadings);
    if (ids.length === 0) return this.lockedBeaconId;

    const candidates: Record<string, number> = {};
    for (const id of ids) candidates[id] = this.smoothed[id];
    const bestId = ids.reduce((a, b) =>
      candidates[a] <= candidates[b] ? a : b,
    );

    if (
      this.lockedBeaconId === null ||
      !(this.lockedBeaconId in candidates)
    ) {
      this.lockedBeaconId = bestId;
    } else if (bestId !== this.lockedBeaconId) {
      const current = candidates[this.lockedBeaconId];
      const challenger = candidates[bestId];
      if (current - challenger > this.hysteresisM) {
        this.lockedBeaconId = bestId;
      }
    }
    return this.lockedBeaconId;
  }
}

export function gaussian(): number {
  let u = 0;
  let v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}
