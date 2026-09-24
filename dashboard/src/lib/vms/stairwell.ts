/**
 * Vertical floor transition for stairwells.
 *
 * Reuses the gateway-crossing pattern from elevator_tracking.py
 * (BeaconRegistry identity lookup + BeaconLockTracker hysteresis) WITHOUT
 * the 1D Kalman / accelerometer fusion — there is no car motion to model
 * on stairs. Floor identity is the locked landing beacon, not a filtered z.
 *
 * Detection radius is wide enough that two landings overlap near a flight
 * midpoint (the on-site ping-pong case), which is exactly why the lock
 * tracker exists.
 */

import {
  BeaconLockTracker,
  BeaconRegistry,
  gaussian,
  type FloorBeacon,
} from "./beacon";
import type { EventLog } from "./event-log";

export type StairOccupant = {
  entityId: string;
  z: number;
  lockedFloor: string | null;
  inStairwell: boolean;
  enteredAt: number | null;
};

type EntityState = {
  lock: BeaconLockTracker;
  lastLocked: string | null;
  lastZ: number | null;
  lastT: number | null;
  enteredAt: number | null;
  lastLandingAt: number | null;
  inStairwell: boolean;
  loiterLogged: boolean;
  speedLogged: boolean;
};

export class StairwellTracker {
  readonly registry: BeaconRegistry;
  private entities = new Map<string, EntityState>();

  constructor(
    readonly stairwellId: string,
    landings: FloorBeacon[],
    readonly detectionRadiusM = 2.0,
    readonly maxVerticalSpeedMps = 1.15,
    readonly loiterS = 32,
  ) {
    this.registry = new BeaconRegistry(landings);
  }

  reset(): void {
    this.entities.clear();
  }

  occupant(entityId: string): StairOccupant | null {
    const s = this.entities.get(entityId);
    if (!s) return null;
    return {
      entityId,
      z: s.lastZ ?? 0,
      lockedFloor: s.lastLocked,
      inStairwell: s.inStairwell,
      enteredAt: s.enteredAt,
    };
  }

  occupants(): StairOccupant[] {
    const out: StairOccupant[] = [];
    for (const [id, s] of this.entities) {
      if (!s.inStairwell) continue;
      out.push({
        entityId: id,
        z: s.lastZ ?? 0,
        lockedFloor: s.lastLocked,
        inStairwell: true,
        enteredAt: s.enteredAt,
      });
    }
    return out;
  }

  landings(): FloorBeacon[] {
    return this.registry.all();
  }

  update(
    entityId: string,
    displayName: string,
    z: number,
    t: number,
    inStairwell: boolean,
    log: EventLog,
    riskLevel: string | null = "public",
  ): void {
    let s = this.entities.get(entityId);
    if (!s) {
      s = {
        lock: new BeaconLockTracker(0.5, 0.3),
        lastLocked: null,
        lastZ: null,
        lastT: null,
        enteredAt: null,
        lastLandingAt: null,
        inStairwell: false,
        loiterLogged: false,
        speedLogged: false,
      };
      this.entities.set(entityId, s);
    }

    if (!inStairwell) {
      if (s.inStairwell) {
        const from = s.lastLocked ?? "unknown";
        log.log(
          t,
          "stairwell_exit",
          entityId,
          `Left ${this.stairwellId} onto ${from}`,
          {
            zone_id: this.stairwellId,
            risk_level: riskLevel,
            source_module: "stairwell",
            display_name: displayName,
          },
        );
      }
      s.inStairwell = false;
      s.enteredAt = null;
      s.loiterLogged = false;
      s.speedLogged = false;
      s.lock.reset();
      s.lastZ = z;
      s.lastT = t;
      return;
    }

    if (!s.inStairwell) {
      s.inStairwell = true;
      s.enteredAt = t;
      s.loiterLogged = false;
      s.speedLogged = false;
      log.log(
        t,
        "stairwell_entry",
        entityId,
        `Entered ${this.stairwellId}` +
          (s.lastLocked ? ` (last floor ${s.lastLocked})` : ""),
        {
          zone_id: this.stairwellId,
          risk_level: riskLevel,
          source_module: "stairwell",
          display_name: displayName,
        },
      );
    }

    if (s.lastZ != null && s.lastT != null && t > s.lastT) {
      const vz = Math.abs(z - s.lastZ) / (t - s.lastT);
      if (vz > this.maxVerticalSpeedMps && !s.speedLogged) {
        s.speedLogged = true;
        log.log(
          t,
          "stairwell_speed",
          entityId,
          `Vertical speed ${vz.toFixed(2)} m/s exceeds walking-stairs max ${this.maxVerticalSpeedMps} m/s`,
          {
            zone_id: this.stairwellId,
            risk_level: riskLevel,
            source_module: "stairwell",
            display_name: displayName,
          },
        );
      }
    }

    const trueReadings = this.registry.allWithinRange(z, this.detectionRadiusM);
    const noisy: Record<string, number> = {};
    for (const [id, d] of Object.entries(trueReadings)) {
      noisy[id] = Math.max(0, d + gaussian() * 0.22);
    }
    const locked = s.lock.update(noisy);

    if (locked && locked !== s.lastLocked) {
      const beacon = this.registry.lookup(locked);
      const prev = s.lastLocked;
      s.lastLocked = locked;
      s.lastLandingAt = t;
      s.loiterLogged = false;
      if (prev) {
        log.log(
          t,
          "floor_crossing",
          entityId,
          `Stairwell gateway ${prev} → ${locked}` +
            (beacon ? ` (z=${beacon.z_height.toFixed(1)}m)` : ""),
          {
            zone_id: this.stairwellId,
            risk_level: riskLevel,
            source_module: "stairwell",
            display_name: displayName,
          },
        );
      }
    }

    const nearLanding = Object.keys(trueReadings).length > 0;
    if (
      s.enteredAt != null &&
      !nearLanding &&
      !s.loiterLogged &&
      t - (s.lastLandingAt ?? s.enteredAt) > this.loiterS
    ) {
      s.loiterLogged = true;
      log.log(
        t,
        "stairwell_loiter",
        entityId,
        `Lingering ${Math.round(t - (s.lastLandingAt ?? s.enteredAt))}s between landings in ${this.stairwellId}`,
        {
          zone_id: this.stairwellId,
          risk_level: riskLevel,
          source_module: "stairwell",
          display_name: displayName,
        },
      );
    }

    s.lastZ = z;
    s.lastT = t;
  }
}
