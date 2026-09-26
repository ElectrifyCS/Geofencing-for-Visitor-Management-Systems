import { BREACH_EVENT_TYPES, EventLog, type Severity, type VmsEvent } from "./event-log";
import { StairwellTracker } from "./stairwell";
import { PatrolVerifier } from "./patrol";
import { ZoneLockTracker } from "./zone-lock";
import { detectEscalations, type EscalationCandidate } from "./escalation";
import { proveEngines } from "./prove";
import {
  CYCLE_S,
  NIGHT_LOOP,
  PEOPLE,
  SERVER_DWELL_S,
  TETHER_LIMIT_M,
  ZONES,
  displayName,
  floorOfZ,
  isAuthorized,
  personById,
  resolveZone,
  LANDINGS,
  type Person,
} from "./facility";

export type EntityPose = {
  id: string;
  name: string;
  tagId: string;
  role: Person["role"];
  tone: Person["tone"];
  x: number;
  y: number;
  z: number;
  floor: number;
  zoneId: string | null;
  zoneName: string | null;
  hidden: boolean;
};

export type Snapshot = {
  t: number;
  cycle: number;
  playing: boolean;
  speed: number;
  events: VmsEvent[];
  entities: EntityPose[];
  minSeverity: Severity;
  floor: number;
  stairwell: {
    occupants: { id: string; name: string; tone: Person["tone"]; z: number; lockedFloor: string | null }[];
    landings: { id: string; z: number; lit: boolean }[];
  };
  patrol: {
    routeName: string;
    guardName: string;
    complete: boolean;
    suppressNfc: boolean;
    checkpoints: {
      id: string;
      name: string;
      modality: "ble" | "nfc";
      sequence: number;
      status: string;
      atS: number | null;
      note: string | null;
    }[];
  };
  kpis: {
    tracked: number;
    events: number;
    denied: number;
    breaches: number;
    floorCrossings: number;
    patrolMisses: number;
    escalations: number;
  };
  latestCritical: VmsEvent | null;
  streamSeq: number;
  /** Tags currently showing a repeat-breach pattern — see escalation.ts. */
  escalations: (EscalationCandidate & { dispatched: boolean })[];
  charts: {
    /** Most recent events (all severities, for overall system pulse),
     * grouped into fixed buckets by recency — not by absolute time,
     * since timestamp_s resets every loop cycle while the event log
     * itself does not, so absolute-time buckets would double-count
     * across cycle boundaries. Bucketing by recent event order
     * sidesteps that entirely. Oldest bucket first. */
    activity: { bucket: number; info: number; warning: number; alert: number; critical: number }[];
    /** Breach counts by entity, across the whole session, most first. */
    byEntity: { entityId: string; displayName: string; count: number }[];
  };
};

type Keyframe = {
  t: number;
  x: number;
  y: number;
  z: number;
  hidden?: boolean;
  tap?: string;
};

type EntityRuntime = {
  person: Person;
  frames: Keyframe[];
  zoneLock: ZoneLockTracker;
  lastZone: string | null;
  zoneEnteredAt: number | null;
  dwellLogged: boolean;
  lastTap: string | null;
  hidden: boolean;
};

function lerp(a: number, b: number, u: number): number {
  return a + (b - a) * u;
}

function poseAt(frames: Keyframe[], t: number): Keyframe {
  if (frames.length === 0) return { t: 0, x: 0, y: 0, z: 0, hidden: true };
  if (t <= frames[0]!.t) return frames[0]!;
  const last = frames[frames.length - 1]!;
  if (t >= last.t) return last;
  for (let i = 0; i < frames.length - 1; i++) {
    const a = frames[i]!;
    const b = frames[i + 1]!;
    if (t >= a.t && t <= b.t) {
      const span = b.t - a.t || 1;
      const u = (t - a.t) / span;
      return {
        t,
        x: lerp(a.x, b.x, u),
        y: lerp(a.y, b.y, u),
        z: lerp(a.z, b.z, u),
        hidden: Boolean(a.hidden || b.hidden),
        tap: t >= b.t - 0.6 && b.tap ? b.tap : undefined,
      };
    }
  }
  return last;
}

function mwangi(cycle: number): Keyframe[] {
  void cycle;
  return [
    { t: 0, x: 12, y: 10, z: 0 },
    { t: 38, x: 12, y: 10, z: 0 },
    { t: 46, x: 67, y: 7, z: 0 },
    { t: 47, x: 67, y: 7, z: 0.15 },
    { t: 60, x: 67, y: 7, z: 3.5 },
    { t: 68, x: 18, y: 10, z: 3.5 },
    { t: 88, x: 18, y: 10, z: 3.5 },
    { t: 96, x: 67, y: 7, z: 3.5 },
    { t: 109, x: 67, y: 7, z: 0 },
    { t: 118, x: 12, y: 10, z: 0 },
    { t: CYCLE_S, x: 12, y: 10, z: 0 },
  ];
}

function otieno(): Keyframe[] {
  return [
    { t: 0, x: 14, y: 10, z: 0 },
    { t: 18, x: 14, y: 10, z: 0 },
    { t: 32, x: 36, y: 10, z: 0 },
    { t: 58, x: 36, y: 10, z: 0 },
    { t: 72, x: 55, y: 8, z: 0 },
    { t: 108, x: 55, y: 8, z: 0 },
    { t: 124, x: 14, y: 10, z: 0 },
    { t: CYCLE_S, x: 14, y: 10, z: 0 },
  ];
}

function guest(): Keyframe[] {
  return [
    { t: 0, x: 10, y: 12, z: 0 },
    { t: 70, x: 10, y: 12, z: 0 },
    { t: 74, x: 55, y: 9, z: 0 },
    { t: 102, x: 55, y: 9, z: 0 },
    { t: 116, x: 10, y: 12, z: 0 },
    { t: CYCLE_S, x: 10, y: 12, z: 0 },
  ];
}

function host(): Keyframe[] {
  return [
    { t: 0, x: 10, y: 10, z: 0 },
    { t: CYCLE_S, x: 10, y: 10, z: 0 },
  ];
}

function guard1(suppressNfc: boolean): Keyframe[] {
  const taps = suppressNfc
    ? []
    : [
        { t: 68, tap: "CP-EXC" as const },
        { t: 102, tap: "CP-ARC" as const },
      ];
  const base: Keyframe[] = [
    { t: 0, x: 8, y: 6, z: 0 },
    { t: 8, x: 8, y: 6, z: 0 },
    { t: 20, x: 36, y: 10, z: 0 },
    { t: 32, x: 67, y: 7, z: 0 },
    { t: 46, x: 67, y: 7, z: 3.5 },
    { t: 54, x: 20, y: 10, z: 3.5 },
    { t: 64, x: 36, y: 10, z: 3.5 },
    { t: 76, x: 36, y: 10, z: 3.5 },
    { t: 86, x: 67, y: 7, z: 3.5 },
    { t: 100, x: 67, y: 7, z: 7.0 },
    { t: 108, x: 55, y: 8, z: 7.0 },
    { t: 118, x: 55, y: 8, z: 7.0 },
    { t: 128, x: 67, y: 7, z: 7.0 },
    { t: 150, x: 67, y: 7, z: 0 },
    { t: 164, x: 8, y: 6, z: 0 },
    { t: CYCLE_S, x: 8, y: 6, z: 0 },
  ];
  return base.map((kf) => {
    const tap = taps.find((t) => Math.abs(t.t - kf.t) < 1.2);
    return tap ? { ...kf, tap: tap.tap } : kf;
  });
}

function guard2(): Keyframe[] {
  return [
    { t: 0, x: 40, y: 16, z: 0 },
    { t: CYCLE_S, x: 41, y: 15, z: 0 },
  ];
}

function unbound(): Keyframe[] {
  return [
    { t: 0, x: 67, y: 7, z: 1.6, hidden: true },
    { t: 122, x: 67, y: 7, z: 1.6, hidden: true },
    { t: 124, x: 67, y: 7, z: 1.6, hidden: false },
    { t: 168, x: 67, y: 7, z: 1.8, hidden: false },
    { t: 176, x: 67, y: 7, z: 0, hidden: false },
    { t: 184, x: 4, y: 4, z: 0, hidden: true },
    { t: CYCLE_S, x: 4, y: 4, z: 0, hidden: true },
  ];
}

const IDLE: Snapshot = {
  t: 0,
  cycle: 0,
  playing: true,
  speed: 10,
  events: [],
  entities: [],
  minSeverity: "info",
  floor: 1,
  stairwell: { occupants: [], landings: LANDINGS.map((l) => ({ id: l.floor_id, z: l.z_height, lit: false })) },
  patrol: {
    routeName: NIGHT_LOOP.name,
    guardName: "J. Kamau",
    complete: false,
    suppressNfc: false,
    checkpoints: NIGHT_LOOP.checkpoints.map((c) => ({
      id: c.id,
      name: c.name,
      modality: c.modality,
      sequence: c.sequence,
      status: "pending",
      atS: null,
      note: null,
    })),
  },
  kpis: { tracked: 0, events: 0, denied: 0, breaches: 0, floorCrossings: 0, patrolMisses: 0, escalations: 0 },
  latestCritical: null,
  streamSeq: 0,
  escalations: [],
  charts: { activity: [], byEntity: [] },
};

export class Simulation {
  readonly log = new EventLog(900);
  private stairs = new StairwellTracker("stairwell", LANDINGS);
  private patrol = new PatrolVerifier(NIGHT_LOOP);
  private runtimes = new Map<string, EntityRuntime>();
  private listeners = new Set<() => void>();
  private snapshot: Snapshot = IDLE;
  private timer: ReturnType<typeof setInterval> | null = null;
  private lastWall = 0;
  t = 0;
  cycle = 0;
  playing = true;
  speed = 10;
  minSeverity: Severity = "info";
  floor = 1;
  suppressNfc = false;
  private streamSeq = 0;
  private checkInLogged = false;
  private lastTetherLog = -99;
  private zoneEnterAuth = new Map<string, boolean>();
  private recentEntries: { zone: string; id: string; t: number; auth: boolean }[] = [];
  private proven = false;
  /** Entity ids currently showing a repeat-breach pattern, as of the last
   * emit() — diffed each tick to log repeat_breach_pattern exactly once
   * per new escalation, not on every tick it remains flagged. */
  private lastEscalatedIds = new Set<string>();
  /** Entity ids an operator has already dispatched a guard to. Pruned to
   * only currently-flagged ids each tick, so a tag that clears and later
   * re-escalates prompts a fresh dispatch rather than staying silenced. */
  private dispatchedIds = new Set<string>();

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };

  getSnapshot = (): Snapshot => this.snapshot;
  getServerSnapshot = (): Snapshot => IDLE;

  start(): void {
    if (!this.proven) {
      try {
        proveEngines();
      } catch (err) {
        console.error("[vms] engine proof failed", err);
      }
      this.proven = true;
    }
    if (this.runtimes.size === 0) this.bootCycle();
    this.step();
    this.emit();
    if (this.timer) return;
    this.lastWall = performance.now();
    this.timer = setInterval(() => this.tickWall(), 90);
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  setPlaying(v: boolean): void {
    this.playing = v;
    this.lastWall = performance.now();
    this.emit();
  }

  setSpeed(v: number): void {
    this.speed = v;
    this.emit();
  }

  setFloor(v: number): void {
    this.floor = v;
    this.emit();
  }

  setMinSeverity(v: Severity): void {
    this.minSeverity = v;
    this.emit();
  }

  /** Operator acknowledges a flagged repeat-breach pattern and dispatches
   * a guard. Logs an audit-trail event; does not itself change the
   * underlying breach detection, which keeps evaluating independently. */
  dispatch(entityId: string): void {
    if (this.dispatchedIds.has(entityId)) return;
    this.dispatchedIds.add(entityId);
    const person = personById(entityId);
    this.log.log(this.t, "guard_dispatched", entityId, `Guard dispatched to ${person?.name ?? entityId}`, {
      source_module: "escalation",
      display_name: person ? displayName(person) : entityId,
    });
    this.emit();
  }

  inject(kind: "missed-nfc" | "stair-loiter" | "floor-skip" | "tailgate" | "probe"): void {
    const t = this.t;
    if (kind === "probe") {
      // Directly logs the breach events rather than scripting a physical
      // path, the same way "missed-nfc" announces its condition directly:
      // the point here is demonstrating the escalation/dispatch response on
      // demand, not re-testing dwell detection's own trigger physics, which
      // already has its own coverage. Three dwell_anomaly events for V-003,
      // a few seconds apart, well inside the default 90s/3-breach window.
      const guestP = personById("V-003")!;
      const durations = [25, 40, 58];
      for (let i = 0; i < durations.length; i++) {
        this.log.log(
          t + i * 2,
          "dwell_anomaly",
          "V-003",
          `DWELL ANOMALY: V-003 in server_room for ${durations[i]}s (baseline 20s)`,
          {
            zone_id: "server_room",
            risk_level: "prohibited",
            source_module: "tracking",
            display_name: displayName(guestP),
          },
        );
      }
    } else if (kind === "missed-nfc") {
      this.suppressNfc = true;
      this.log.log(t, "nfc_tap_required", "GRD-1", "Demo inject: NFC taps suppressed for this loop — BLE range will not count", {
        source_module: "patrol",
        display_name: displayName(personById("GRD-1")!),
      });
    } else if (kind === "stair-loiter") {
      const rt = this.runtimes.get("TAG-0099");
      if (rt) {
        rt.frames = [
          { t: t, x: 67, y: 7, z: 1.7, hidden: false },
          { t: t + 40, x: 67, y: 7, z: 1.9, hidden: false },
          { t: t + 48, x: 4, y: 4, z: 0, hidden: true },
        ];
      }
    } else if (kind === "floor-skip") {
      const rt = this.runtimes.get("V-001");
      if (rt) {
        const p = poseAt(rt.frames, t);
        rt.frames = [
          { t, x: p.x, y: p.y, z: 0.2 },
          { t: t + 0.4, x: 67, y: 7, z: 0.2 },
          { t: t + 1.0, x: 67, y: 7, z: 7.0 },
          { t: t + 12, x: 55, y: 8, z: 7.0 },
          { t: t + 20, x: 12, y: 10, z: 0 },
        ];
      }
    } else if (kind === "tailgate") {
      const rt = this.runtimes.get("V-003");
      if (rt) {
        rt.frames = [
          { t, x: 12, y: 10, z: 0 },
          { t: t + 3, x: 55, y: 8, z: 0 },
          { t: t + 18, x: 55, y: 8, z: 0 },
          { t: t + 26, x: 12, y: 10, z: 0 },
        ];
      }
    }
    this.emit();
  }

  private bootCycle(): void {
    this.stairs.reset();
    this.patrol.reset();
    this.zoneEnterAuth.clear();
    this.recentEntries = [];
    this.checkInLogged = false;
    this.lastTetherLog = -99;
    const odd = this.cycle % 2 === 1;
    this.suppressNfc = odd;
    this.runtimes.clear();
    const frames: Record<string, Keyframe[]> = {
      "V-001": mwangi(this.cycle),
      "V-002": otieno(),
      "V-003": guest(),
      "HOST-1": host(),
      "GRD-1": guard1(this.suppressNfc),
      "GRD-2": guard2(),
      "TAG-0099": unbound(),
    };
    for (const person of PEOPLE) {
      this.runtimes.set(person.id, {
        person,
        frames: frames[person.id] ?? [{ t: 0, x: 0, y: 0, z: 0, hidden: true }],
        zoneLock: new ZoneLockTracker(2),
        lastZone: null,
        zoneEnteredAt: null,
        dwellLogged: false,
        lastTap: null,
        hidden: false,
      });
    }
  }

  private tickWall(): void {
    const now = performance.now();
    const dtWall = Math.min(0.2, (now - this.lastWall) / 1000);
    this.lastWall = now;
    if (!this.playing) return;
    this.t += dtWall * this.speed;
    if (this.t >= CYCLE_S) {
      this.t = 0;
      this.cycle += 1;
      this.bootCycle();
    }
    this.step();
    this.emit();
  }

  private step(): void {
    const t = this.t;
    if (!this.checkInLogged) {
      this.logCheckIn(t);
      this.checkInLogged = true;
    }

    const poses: EntityPose[] = [];
    const byId = new Map<string, EntityPose>();

    for (const rt of this.runtimes.values()) {
      const kf = poseAt(rt.frames, t);
      rt.hidden = Boolean(kf.hidden);
      const zone = kf.hidden ? null : resolveZone(kf.x, kf.y, kf.z);
      const pose: EntityPose = {
        id: rt.person.id,
        name: rt.person.name,
        tagId: rt.person.tagId,
        role: rt.person.role,
        tone: rt.person.tone,
        x: kf.x,
        y: kf.y,
        z: kf.z,
        floor: floorOfZ(kf.z),
        zoneId: zone?.id ?? null,
        zoneName: zone?.name ?? null,
        hidden: rt.hidden,
      };
      poses.push(pose);
      byId.set(pose.id, pose);

      if (rt.hidden) {
        this.stairs.update(rt.person.id, displayName(rt.person), kf.z, t, false, this.log);
        continue;
      }

      const inStairs = zone?.kind === "stairwell";
      this.stairs.update(
        rt.person.id,
        displayName(rt.person),
        kf.z,
        t,
        Boolean(inStairs),
        this.log,
        zone?.risk ?? "public",
      );

      const confirmed = rt.zoneLock.update(zone?.id ?? null);
      if (confirmed !== rt.lastZone) {
        if (rt.lastZone) {
          this.log.log(t, "zone_exit", rt.person.id, `Left ${rt.lastZone}`, {
            zone_id: rt.lastZone,
            source_module: "integrated",
            display_name: displayName(rt.person),
          });
        }
        if (confirmed) {
          const zn = ZONES.find((z) => z.id === confirmed);
          const auth = isAuthorized(rt.person, confirmed);
          this.zoneEnterAuth.set(`${rt.person.id}:${confirmed}`, auth);
          this.log.log(t, "zone_entry", rt.person.id, `Entered ${confirmed}`, {
            zone_id: confirmed,
            risk_level: zn?.risk,
            source_module: "integrated",
            display_name: displayName(rt.person),
          });
          if (!auth) {
            this.log.log(
              t,
              "permit_denied",
              rt.person.id,
              `No permit exists for ${rt.person.id} on ${confirmed}`,
              {
                zone_id: confirmed,
                risk_level: zn?.risk,
                source_module: "permits",
                display_name: displayName(rt.person),
              },
            );
          } else {
            this.log.log(
              t,
              "permit_authorized",
              rt.person.id,
              `Authorized on ${confirmed}`,
              {
                zone_id: confirmed,
                risk_level: zn?.risk,
                source_module: "permits",
                display_name: displayName(rt.person),
              },
            );
          }
          this.detectTailgate(rt.person, confirmed, auth, t, zn?.risk ?? null);
        }
        rt.lastZone = confirmed;
        rt.zoneEnteredAt = confirmed ? t : null;
        rt.dwellLogged = false;
      }

      if (
        confirmed === "server_room" &&
        rt.zoneEnteredAt != null &&
        t - rt.zoneEnteredAt > SERVER_DWELL_S &&
        !rt.dwellLogged
      ) {
        rt.dwellLogged = true;
        const auth = this.zoneEnterAuth.get(`${rt.person.id}:server_room`) ?? false;
        if (!auth) {
          this.log.log(
            t,
            "loitering_unauthorized",
            rt.person.id,
            `Unauthorized presence AND lingering in server_room: ${Math.round(t - rt.zoneEnteredAt)}s`,
            {
              zone_id: "server_room",
              risk_level: "prohibited",
              source_module: "integrated",
              display_name: displayName(rt.person),
            },
          );
        } else {
          this.log.log(
            t,
            "dwell_anomaly",
            rt.person.id,
            `DWELL ANOMALY: ${rt.person.id} in server_room for ${Math.round(t - rt.zoneEnteredAt)}s (baseline 20s)`,
            {
              zone_id: "server_room",
              risk_level: "prohibited",
              source_module: "tracking",
              display_name: displayName(rt.person),
            },
          );
        }
      }

      if (rt.person.id === "GRD-1") {
        this.patrol.observePosition(
          rt.person.id,
          displayName(rt.person),
          kf.x,
          kf.y,
          kf.z,
          t,
          this.log,
        );
        if (kf.tap && kf.tap !== rt.lastTap && !this.suppressNfc) {
          rt.lastTap = kf.tap;
          this.patrol.observeNfcTap(
            rt.person.id,
            displayName(rt.person),
            kf.tap,
            kf.x,
            kf.y,
            kf.z,
            t,
            this.log,
          );
        }
      }
    }

    this.patrol.tick(t, displayName(personById("GRD-1")!), this.log);

    const hostPose = byId.get("HOST-1");
    const vis = byId.get("V-002");
    if (hostPose && vis && !vis.hidden && !hostPose.hidden) {
      const d = Math.hypot(hostPose.x - vis.x, hostPose.y - vis.y);
      if (d > TETHER_LIMIT_M && vis.zoneId && t - this.lastTetherLog > 11) {
        this.lastTetherLog = t;
        const zn = ZONES.find((z) => z.id === vis.zoneId);
        this.log.log(
          t,
          "tether_breach",
          "V-002",
          `TETHER BREACH: V-002 is ${d.toFixed(1)}m from HOST-1 (limit ${TETHER_LIMIT_M}m)`,
          {
            zone_id: vis.zoneId,
            risk_level: zn?.risk,
            source_module: "tracking",
            display_name: displayName(personById("V-002")!),
          },
        );
      }
    }

    this.lastPoses = poses;
  }

  private lastPoses: EntityPose[] = [];

  private detectTailgate(
    person: Person,
    zone: string,
    auth: boolean,
    t: number,
    risk: string | null,
  ): void {
    this.recentEntries = this.recentEntries.filter((e) => t - e.t <= 5);
    const others = this.recentEntries.filter((e) => e.zone === zone && e.id !== person.id);
    for (const o of others) {
      if (!auth || !o.auth) {
        this.log.log(
          t,
          "tailgating",
          person.id,
          `Entered ${zone} ${(t - o.t).toFixed(1)}s after ${o.id} (unauthorized: ${auth ? o.id : person.id})`,
          {
            zone_id: zone,
            risk_level: risk,
            source_module: "integrated",
            display_name: displayName(person),
          },
        );
        break;
      }
    }
    this.recentEntries.push({ zone, id: person.id, t, auth });
  }

  private logCheckIn(t: number): void {
    const mw = personById("V-001")!;
    const ot = personById("V-002")!;
    this.log.log(t, "permit_granted", mw.id, "Permit issued for lobby until 17:00", {
      zone_id: "lobby",
      risk_level: "public",
      source_module: "permits",
      display_name: displayName(mw),
    });
    this.log.log(t, "permit_denied", mw.id, "Check-in refused for it_dept: escort-required zone with no host assigned", {
      zone_id: "it_dept",
      risk_level: "escort_required",
      source_module: "permits",
      display_name: displayName(mw),
    });
    this.log.log(t, "permit_denied", mw.id, "Check-in refused for server_room: prohibited zone needs an escorted tag", {
      zone_id: "server_room",
      risk_level: "prohibited",
      source_module: "permits",
      display_name: displayName(mw),
    });
    this.log.log(t, "permit_granted", ot.id, "Permit issued for lobby until 17:00", {
      zone_id: "lobby",
      risk_level: "public",
      source_module: "permits",
      display_name: displayName(ot),
    });
    this.log.log(t, "permit_granted", ot.id, "Permit issued for it_dept until 17:00 (escort required)", {
      zone_id: "it_dept",
      risk_level: "escort_required",
      source_module: "permits",
      display_name: displayName(ot),
    });
    if (this.suppressNfc) {
      this.log.log(t, "nfc_tap_required", "GRD-1", "This loop: NFC checkpoints require a tap — standing in BLE range will not close them", {
        source_module: "patrol",
        display_name: displayName(personById("GRD-1")!),
      });
    }
  }

  private emit(): void {
    this.streamSeq += 1;
    const events = this.log.query({ min_severity: this.minSeverity, limit: 80 });
    const all = this.log.query();

    const escalated = detectEscalations(all, this.t);
    const escalatedIds = new Set(escalated.map((e) => e.entityId));
    for (const candidate of escalated) {
      if (this.lastEscalatedIds.has(candidate.entityId)) continue;
      // Newly crossed the threshold this tick — log it once, not every tick
      // it remains flagged.
      this.log.log(
        this.t,
        "repeat_breach_pattern",
        candidate.entityId,
        `${candidate.count} breaches in ${Math.round(candidate.lastAtS - candidate.firstAtS)}s — pattern suggests deliberate probing, not accidental wandering`,
        { source_module: "escalation", display_name: candidate.displayName },
      );
    }
    this.lastEscalatedIds = escalatedIds;
    // A tag that's dispatched then clears (ages out of the window) should
    // prompt a fresh dispatch if it escalates again later, not stay silenced.
    for (const id of this.dispatchedIds) {
      if (!escalatedIds.has(id)) this.dispatchedIds.delete(id);
    }
    const escalations = escalated.map((e) => ({ ...e, dispatched: this.dispatchedIds.has(e.entityId) }));

    // All events, not just breaches — a SOC activity chart should show the
    // overall pulse of the system, with severity stacking making a spike
    // in breach-level events visible against ordinary background traffic
    // (zone entries, patrol checkpoints, etc.), not a mostly-empty chart.
    const recentEvents = all.slice(-60);
    const BUCKET_SIZE = 5;
    const activity: Snapshot["charts"]["activity"] = [];
    for (let i = 0; i < recentEvents.length; i += BUCKET_SIZE) {
      const slice = recentEvents.slice(i, i + BUCKET_SIZE);
      const row = { bucket: activity.length, info: 0, warning: 0, alert: 0, critical: 0 };
      for (const e of slice) row[e.severity] += 1;
      activity.push(row);
    }
    const byEntityMap = new Map<string, { entityId: string; displayName: string; count: number }>();
    for (const e of all) {
      if (!BREACH_EVENT_TYPES.includes(e.event_type)) continue;
      const existing = byEntityMap.get(e.entity_id);
      if (existing) existing.count += 1;
      else byEntityMap.set(e.entity_id, { entityId: e.entity_id, displayName: e.display_name ?? e.entity_id, count: 1 });
    }
    const byEntity = [...byEntityMap.values()].sort((a, b) => b.count - a.count);
    const occupants = this.stairs.occupants().map((o) => {
      const p = personById(o.entityId);
      return {
        id: o.entityId,
        name: p?.name ?? o.entityId,
        tone: p?.tone ?? ("unk" as const),
        z: o.z,
        lockedFloor: o.lockedFloor,
      };
    });
    const lit = new Set(occupants.map((o) => o.lockedFloor).filter(Boolean) as string[]);
    const tracked = this.lastPoses.filter((e) => !e.hidden).length;
    this.snapshot = {
      t: this.t,
      cycle: this.cycle,
      playing: this.playing,
      speed: this.speed,
      events: events.slice().reverse(),
      entities: this.lastPoses,
      minSeverity: this.minSeverity,
      floor: this.floor,
      stairwell: {
        occupants,
        landings: LANDINGS.map((l) => ({ id: l.floor_id, z: l.z_height, lit: lit.has(l.floor_id) })),
      },
      patrol: {
        routeName: NIGHT_LOOP.name,
        guardName: "J. Kamau",
        complete: this.patrol.isComplete(),
        suppressNfc: this.suppressNfc,
        checkpoints: NIGHT_LOOP.checkpoints.map((c) => {
          const st = this.patrol.states.get(c.id)!;
          return {
            id: c.id,
            name: c.name,
            modality: c.modality,
            sequence: c.sequence,
            status: st.status,
            atS: st.atS,
            note: st.note,
          };
        }),
      },
      kpis: {
        tracked,
        events: all.length,
        denied: all.filter((e) => e.event_type === "permit_denied").length,
        breaches: all.filter((e) => BREACH_EVENT_TYPES.includes(e.event_type)).length,
        floorCrossings: all.filter((e) => e.event_type === "floor_crossing").length,
        patrolMisses: all.filter((e) =>
          ["patrol_missed", "patrol_overdue", "patrol_sequence_break", "nfc_spoof_attempt"].includes(e.event_type),
        ).length,
        escalations: escalations.length,
      },
      latestCritical: [...all].reverse().find((e) => e.severity === "critical") ?? null,
      streamSeq: this.streamSeq,
      escalations,
      charts: { activity, byEntity },
    };
    for (const l of this.listeners) l();
  }
}

export const simulation = new Simulation();

