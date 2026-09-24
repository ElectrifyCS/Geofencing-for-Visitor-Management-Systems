/**
 * Module proofs — same spirit as the Python __main__ blocks.
 * Run once at simulation boot; throw if a regression lands.
 */

import { BeaconLockTracker } from "./beacon";
import { EventLog, computeSeverity } from "./event-log";
import { PatrolVerifier } from "./patrol";
import { StairwellTracker } from "./stairwell";
import { LANDINGS, NIGHT_LOOP } from "./facility";

function seededGaussian(seed: { n: number }): number {
  seed.n = (seed.n * 1664525 + 1013904223) % 4294967296;
  const u = seed.n / 4294967296;
  seed.n = (seed.n * 1664525 + 1013904223) % 4294967296;
  const v = seed.n / 4294967296;
  return Math.sqrt(-2 * Math.log(Math.max(u, 1e-9))) * Math.cos(2 * Math.PI * v);
}

export function proveEngines(): void {
  proveSeverity();
  proveBeaconLock();
  proveStairwellGateway();
  provePatrolNfcIntegrity();
}

function proveSeverity(): void {
  if (computeSeverity("dwell_anomaly", "public") !== "warning") {
    throw new Error("dwell public should be warning");
  }
  if (computeSeverity("dwell_anomaly", "prohibited") !== "critical") {
    throw new Error("dwell prohibited should be critical");
  }
  if (computeSeverity("loitering_unauthorized", "prohibited") !== "critical") {
    throw new Error("loitering in prohibited should be critical");
  }
}

function proveBeaconLock(): void {
  const seed = { n: 99 };
  const naive = { flips: 0, current: null as string | null };
  const tracker = new BeaconLockTracker(0.5, 0.3);
  let prev: string | null = null;
  let trackerFlips = 0;
  for (let i = 0; i < 40; i++) {
    const readings = {
      F4: 1.75 + seededGaussian(seed) * 0.3,
      F5: 1.75 + seededGaussian(seed) * 0.3,
    };
    const naiveWinner = readings.F4 <= readings.F5 ? "F4" : "F5";
    if (naive.current && naiveWinner !== naive.current) naive.flips += 1;
    naive.current = naiveWinner;
    const locked = tracker.update(readings);
    if (prev && locked !== prev) trackerFlips += 1;
    prev = locked;
  }
  if (naive.flips < 4) {
    throw new Error(`expected naive ping-pong, got ${naive.flips} flips`);
  }
  if (trackerFlips > 2) {
    throw new Error(`lock tracker too jumpy: ${trackerFlips} flips`);
  }
}

function proveStairwellGateway(): void {
  const log = new EventLog();
  const stairs = new StairwellTracker("stairwell", LANDINGS, 2.0);
  const dt = 0.2;
  let z = 0;
  for (let t = 0; t <= 18; t += dt) {
    z = Math.min(7, t * 0.4);
    stairs.update("V-TEST", "Test", z, t, true, log, "public");
  }
  const types = log.query().map((e) => e.event_type);
  if (!types.includes("stairwell_entry")) {
    throw new Error("stairwell entry missing");
  }
  const crossings = log.query({ event_type: "floor_crossing" });
  if (crossings.length < 1) {
    throw new Error("expected at least one floor_crossing on a 7m climb");
  }
}

function provePatrolNfcIntegrity(): void {
  const log = new EventLog();
  const v = new PatrolVerifier(NIGHT_LOOP);
  const guard = "GRD-1";
  const name = "J. Kamau (GRD-1)";
  const exec = NIGHT_LOOP.checkpoints.find((c) => c.id === "CP-EXC")!;

  v.observePosition(guard, name, exec.x, exec.y, exec.z, 10, log);
  v.observePosition(guard, name, exec.x, exec.y, exec.z, 11, log);
  const execState = v.states.get("CP-EXC")!;
  if (execState.status === "verified") {
    throw new Error("BLE range must not verify an NFC checkpoint");
  }
  if (execState.status !== "nfc_required") {
    throw new Error(`expected nfc_required, got ${execState.status}`);
  }

  v.observeNfcTap(guard, name, "CP-EXC", 8, 6, 0, 12, log);
  const spoof = log.query({ event_type: "nfc_spoof_attempt" });
  if (spoof.length < 1) {
    throw new Error("remote NFC tap must be rejected");
  }

  const v2 = new PatrolVerifier(NIGHT_LOOP);
  const lby = NIGHT_LOOP.checkpoints[0]!;
  v2.observePosition(guard, name, lby.x, lby.y, lby.z, 1, log);
  v2.observePosition(guard, name, lby.x, lby.y, lby.z, 2, log);
  if (v2.states.get("CP-LBY")!.status !== "verified") {
    throw new Error("BLE checkpoint should verify after two in-range ticks");
  }
}
