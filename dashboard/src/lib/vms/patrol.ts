/**
 * Security patrol verification — genuinely new, no existing Python pattern.
 *
 * Confirmed requirement:
 *   - BLE range for general checkpoints (proximity, noisy, debounce)
 *   - NFC tap as the high-integrity checkpoint (explicit tap; BLE range
 *     alone must never verify an NFC point)
 *
 * Sequence-and-timing: checkpoints must be taken in order, each within a
 * min/max transit window from the previous verified point.
 */

import type { EventLog } from "./event-log";

export type CheckpointModality = "ble" | "nfc";

export type Checkpoint = {
  id: string;
  name: string;
  zoneId: string;
  sequence: number;
  modality: CheckpointModality;
  x: number;
  y: number;
  z: number;
  bleRangeM: number;
  minTransitS: number;
  maxTransitS: number;
};

export type PatrolRoute = {
  id: string;
  name: string;
  guardId: string;
  checkpoints: Checkpoint[];
};

export type CheckpointUiStatus =
  | "pending"
  | "in_range"
  | "verified"
  | "missed"
  | "sequence_break"
  | "nfc_required";

export type CheckpointState = {
  status: CheckpointUiStatus;
  atS: number | null;
  note: string | null;
};

type GuardRun = {
  nextIndex: number;
  lastVerifiedAt: number | null;
  startedAt: number | null;
  bleConfirm: Record<string, number>;
  complete: boolean;
  missedLogged: Set<string>;
};

function dist2(ax: number, ay: number, bx: number, by: number): number {
  return Math.hypot(ax - bx, ay - by);
}

export class PatrolVerifier {
  private run: GuardRun;
  readonly states: Map<string, CheckpointState>;
  readonly route: PatrolRoute;

  constructor(route: PatrolRoute) {
    this.route = route;
    this.states = new Map();
    this.run = this.freshRun();
    this.resetStates();
  }

  private freshRun(): GuardRun {
    return {
      nextIndex: 0,
      lastVerifiedAt: null,
      startedAt: null,
      bleConfirm: {},
      complete: false,
      missedLogged: new Set(),
    };
  }

  private resetStates(): void {
    this.states.clear();
    for (const cp of this.route.checkpoints) {
      this.states.set(cp.id, { status: "pending", atS: null, note: null });
    }
  }

  reset(): void {
    this.run = this.freshRun();
    this.resetStates();
  }

  isComplete(): boolean {
    return this.run.complete;
  }

  nextCheckpoint(): Checkpoint | null {
    return this.route.checkpoints[this.run.nextIndex] ?? null;
  }

  /**
   * Horizontal + vertical proximity. Vertical uses a 1.2 m slab so a guard
   * on F2 does not mark an F1 BLE point.
   */
  inRange(cp: Checkpoint, x: number, y: number, z: number): boolean {
    if (Math.abs(z - cp.z) > 1.2) return false;
    return dist2(x, y, cp.x, cp.y) <= cp.bleRangeM;
  }

  observePosition(
    guardId: string,
    displayName: string,
    x: number,
    y: number,
    z: number,
    t: number,
    log: EventLog,
  ): void {
    if (guardId !== this.route.guardId || this.run.complete) return;

    for (const cp of this.route.checkpoints) {
      const st = this.states.get(cp.id)!;
      if (st.status === "verified" || st.status === "missed") continue;
      const close = this.inRange(cp, x, y, z);
      if (!close) {
        this.run.bleConfirm[cp.id] = 0;
        if (st.status === "in_range" || st.status === "nfc_required") {
          if (st.status === "in_range") st.status = "pending";
        }
        continue;
      }

      this.run.bleConfirm[cp.id] = (this.run.bleConfirm[cp.id] ?? 0) + 1;

      if (cp.modality === "nfc") {
        st.status = "nfc_required";
        st.note = "In BLE range — NFC tap required";
        continue;
      }

      st.status = "in_range";
      if (this.run.bleConfirm[cp.id] >= 2) {
        this.accept(cp, t, displayName, log, "ble");
      }
    }
  }

  observeNfcTap(
    guardId: string,
    displayName: string,
    checkpointId: string,
    x: number,
    y: number,
    z: number,
    t: number,
    log: EventLog,
  ): void {
    if (guardId !== this.route.guardId || this.run.complete) return;
    const cp = this.route.checkpoints.find((c) => c.id === checkpointId);
    if (!cp) return;
    if (cp.modality !== "nfc") {
      log.log(
        t,
        "nfc_spoof_attempt",
        guardId,
        `NFC tap at ${cp.name} ignored — checkpoint is BLE-range, not NFC`,
        {
          zone_id: cp.zoneId,
          source_module: "patrol",
          display_name: displayName,
        },
      );
      return;
    }
    if (!this.inRange(cp, x, y, z)) {
      log.log(
        t,
        "nfc_spoof_attempt",
        guardId,
        `NFC tap for ${cp.name} rejected — tag not in range of the reader`,
        {
          zone_id: cp.zoneId,
          source_module: "patrol",
          display_name: displayName,
        },
      );
      return;
    }
    this.accept(cp, t, displayName, log, "nfc");
  }

  tick(t: number, displayName: string, log: EventLog): void {
    if (this.run.complete) return;
    const next = this.nextCheckpoint();
    if (!next) return;
    const origin = this.run.lastVerifiedAt ?? this.run.startedAt;
    if (origin == null) return;
    const elapsed = t - origin;
    if (elapsed <= next.maxTransitS) return;
    const st = this.states.get(next.id)!;
    if (st.status === "verified" || st.status === "missed") return;
    st.status = "missed";
    st.note = `Window ${next.maxTransitS}s expired`;
    if (!this.run.missedLogged.has(next.id)) {
      this.run.missedLogged.add(next.id);
      log.log(
        t,
        next.modality === "nfc" ? "patrol_missed" : "patrol_overdue",
        this.route.guardId,
        `Missed ${next.modality.toUpperCase()} checkpoint ${next.name} (seq ${next.sequence + 1})`,
        {
          zone_id: next.zoneId,
          source_module: "patrol",
          display_name: displayName,
        },
      );
    }
    this.run.nextIndex += 1;
    this.run.lastVerifiedAt = t;
    if (this.run.nextIndex >= this.route.checkpoints.length) {
      this.run.complete = true;
    }
  }

  private accept(
    cp: Checkpoint,
    t: number,
    displayName: string,
    log: EventLog,
    how: "ble" | "nfc",
  ): void {
    const st = this.states.get(cp.id)!;
    if (st.status === "verified") return;

    const expected = this.route.checkpoints[this.run.nextIndex];
    let sequenceOk = expected?.id === cp.id;

    if (this.run.lastVerifiedAt != null) {
      const dt = t - this.run.lastVerifiedAt;
      if (dt < cp.minTransitS) {
        sequenceOk = false;
        st.note = `Transit ${dt.toFixed(0)}s < min ${cp.minTransitS}s`;
        log.log(
          t,
          "patrol_sequence_break",
          this.route.guardId,
          `Reached ${cp.name} in ${dt.toFixed(0)}s (min ${cp.minTransitS}s) — physically implausible`,
          {
            zone_id: cp.zoneId,
            source_module: "patrol",
            display_name: displayName,
          },
        );
      }
    }

    if (!sequenceOk && expected && expected.id !== cp.id) {
      st.status = "sequence_break";
      st.note = `Expected ${expected.name} next`;
      log.log(
        t,
        "patrol_sequence_break",
        this.route.guardId,
        `Out of sequence at ${cp.name} — expected ${expected.name}`,
        {
          zone_id: cp.zoneId,
          source_module: "patrol",
          display_name: displayName,
        },
      );
    }

    st.status = "verified";
    st.atS = t;
    if (!st.note) st.note = how === "nfc" ? "NFC tap verified" : "BLE range confirmed";

    if (this.run.startedAt == null) this.run.startedAt = t;
    this.run.lastVerifiedAt = t;

    const idx = this.route.checkpoints.findIndex((c) => c.id === cp.id);
    if (idx === this.run.nextIndex) this.run.nextIndex += 1;
    else if (idx > this.run.nextIndex) this.run.nextIndex = idx + 1;

    log.log(
      t,
      how === "nfc" ? "patrol_nfc_verified" : "patrol_checkpoint",
      this.route.guardId,
      how === "nfc"
        ? `NFC tap verified at ${cp.name} (seq ${cp.sequence + 1}/${this.route.checkpoints.length})`
        : `BLE checkpoint ${cp.name} (seq ${cp.sequence + 1}/${this.route.checkpoints.length})`,
      {
        zone_id: cp.zoneId,
        source_module: "patrol",
        display_name: displayName,
      },
    );

    if (this.run.nextIndex >= this.route.checkpoints.length) {
      this.run.complete = true;
      log.log(
        t,
        "patrol_complete",
        this.route.guardId,
        `Patrol '${this.route.name}' complete — ${this.route.checkpoints.length} checkpoints`,
        { source_module: "patrol", display_name: displayName },
      );
    }
  }
}
