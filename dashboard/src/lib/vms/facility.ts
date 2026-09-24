import type { FloorBeacon } from "./beacon";
import type { PatrolRoute } from "./patrol";

export type RiskLevel = "public" | "escort_required" | "prohibited";

export type Zone = {
  id: string;
  name: string;
  floor: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  z0: number;
  z1: number;
  risk: RiskLevel;
  kind: "room" | "stairwell" | "elevator";
};

export type Person = {
  id: string;
  name: string;
  tagId: string;
  role: "visitor" | "host" | "guard" | "unknown";
  tone: "a" | "b" | "c" | "host" | "g1" | "g2" | "unk";
  authorized: string[];
};

export const FLOOR_HEIGHT = 3.5;

export const LANDINGS: FloorBeacon[] = [
  { floor_id: "F1", z_height: 0 },
  { floor_id: "F2", z_height: 3.5 },
  { floor_id: "F3", z_height: 7.0 },
];

export const ZONES: Zone[] = [
  { id: "lobby", name: "Main lobby", floor: 1, x0: 2, y0: 2, x1: 26, y1: 20, z0: -0.2, z1: 3.3, risk: "public", kind: "room" },
  { id: "it_dept", name: "IT department", floor: 1, x0: 28, y0: 2, x1: 46, y1: 20, z0: -0.2, z1: 3.3, risk: "escort_required", kind: "room" },
  { id: "server_room", name: "Server room", floor: 1, x0: 48, y0: 2, x1: 62, y1: 16, z0: -0.2, z1: 3.3, risk: "prohibited", kind: "room" },
  { id: "stairwell", name: "East stairwell", floor: 1, x0: 64, y0: 2, x1: 70, y1: 12, z0: -0.2, z1: 10.6, risk: "public", kind: "stairwell" },
  { id: "elevator", name: "Lift lobby", floor: 1, x0: 64, y0: 13, x1: 70, y1: 20, z0: -0.2, z1: 3.3, risk: "public", kind: "elevator" },
  { id: "f2_corridor", name: "Floor 2 corridor", floor: 2, x0: 2, y0: 2, x1: 26, y1: 20, z0: 3.3, z1: 6.8, risk: "public", kind: "room" },
  { id: "exec_suite", name: "Exec suite", floor: 2, x0: 28, y0: 2, x1: 46, y1: 20, z0: 3.3, z1: 6.8, risk: "escort_required", kind: "room" },
  { id: "f2_open", name: "Floor 2 landing", floor: 2, x0: 48, y0: 2, x1: 62, y1: 16, z0: 3.3, z1: 6.8, risk: "public", kind: "room" },
  { id: "f3_corridor", name: "Floor 3 corridor", floor: 3, x0: 2, y0: 2, x1: 46, y1: 20, z0: 6.8, z1: 10.6, risk: "public", kind: "room" },
  { id: "archive", name: "Archive", floor: 3, x0: 48, y0: 2, x1: 62, y1: 16, z0: 6.8, z1: 10.6, risk: "prohibited", kind: "room" },
];

export const PEOPLE: Person[] = [
  { id: "V-001", name: "A. Mwangi", tagId: "TAG-0042", role: "visitor", tone: "a", authorized: ["lobby", "stairwell", "f2_corridor", "f2_open", "elevator"] },
  { id: "V-002", name: "B. Otieno", tagId: "TAG-0043", role: "visitor", tone: "b", authorized: ["lobby", "it_dept", "stairwell"] },
  { id: "V-003", name: "D. Guest", tagId: "TAG-0044", role: "visitor", tone: "c", authorized: ["lobby"] },
  { id: "HOST-1", name: "K. Wanjiku", tagId: "HOST-1", role: "host", tone: "host", authorized: ["lobby", "it_dept", "server_room", "exec_suite", "archive", "stairwell", "f2_corridor", "f3_corridor"] },
  { id: "GRD-1", name: "J. Kamau", tagId: "GRD-1", role: "guard", tone: "g1", authorized: ["*"] },
  { id: "GRD-2", name: "S. Achieng", tagId: "GRD-2", role: "guard", tone: "g2", authorized: ["*"] },
  { id: "TAG-0099", name: "Unbound tag", tagId: "TAG-0099", role: "unknown", tone: "unk", authorized: [] },
];

export const NIGHT_LOOP: PatrolRoute = {
  id: "night-loop",
  name: "Night loop",
  guardId: "GRD-1",
  checkpoints: [
    { id: "CP-LBY", name: "Lobby desk", zoneId: "lobby", sequence: 0, modality: "ble", x: 8, y: 6, z: 0, bleRangeM: 5, minTransitS: 0, maxTransitS: 40 },
    { id: "CP-IT", name: "IT corridor", zoneId: "it_dept", sequence: 1, modality: "ble", x: 36, y: 10, z: 0, bleRangeM: 4.5, minTransitS: 8, maxTransitS: 40 },
    { id: "CP-ST1", name: "Stair F1", zoneId: "stairwell", sequence: 2, modality: "ble", x: 67, y: 7, z: 0, bleRangeM: 3.2, minTransitS: 6, maxTransitS: 35 },
    { id: "CP-F2", name: "F2 corridor", zoneId: "f2_corridor", sequence: 3, modality: "ble", x: 20, y: 10, z: 3.5, bleRangeM: 4.5, minTransitS: 10, maxTransitS: 40 },
    { id: "CP-EXC", name: "Exec suite door", zoneId: "exec_suite", sequence: 4, modality: "nfc", x: 36, y: 10, z: 3.5, bleRangeM: 3.5, minTransitS: 6, maxTransitS: 35 },
    { id: "CP-ST2", name: "Stair F2", zoneId: "stairwell", sequence: 5, modality: "ble", x: 67, y: 7, z: 3.5, bleRangeM: 3.2, minTransitS: 6, maxTransitS: 30 },
    { id: "CP-ARC", name: "Archive door", zoneId: "archive", sequence: 6, modality: "nfc", x: 55, y: 8, z: 7.0, bleRangeM: 3.5, minTransitS: 10, maxTransitS: 45 },
    { id: "CP-RET", name: "Lobby return", zoneId: "lobby", sequence: 7, modality: "ble", x: 8, y: 6, z: 0, bleRangeM: 5, minTransitS: 14, maxTransitS: 70 },
  ],
};

export function displayName(person: Person): string {
  return `${person.name} (${person.tagId})`;
}

export function personById(id: string): Person | undefined {
  return PEOPLE.find((p) => p.id === id);
}

export function resolveZone(x: number, y: number, z: number): Zone | null {
  const hits = ZONES.filter(
    (zn) => x >= zn.x0 && x <= zn.x1 && y >= zn.y0 && y <= zn.y1 && z >= zn.z0 && z <= zn.z1,
  );
  if (hits.length === 0) return null;
  hits.sort((a, b) => area(a) - area(b));
  return hits[0] ?? null;
}

function area(z: Zone): number {
  return (z.x1 - z.x0) * (z.y1 - z.y0);
}

export function floorOfZ(z: number): number {
  if (z >= 6.8) return 3;
  if (z >= 3.3) return 2;
  return 1;
}

export function isAuthorized(person: Person, zoneId: string): boolean {
  return person.authorized.includes("*") || person.authorized.includes(zoneId);
}

export const WORLD = { x0: 0, y0: 0, x1: 72, y1: 22 };
export const CYCLE_S = 200;
export const TETHER_LIMIT_M = 6.1;
export const SERVER_DWELL_S = 22;
