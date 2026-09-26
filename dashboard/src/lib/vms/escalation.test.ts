import assert from "node:assert/strict";
import { test } from "node:test";
import { detectEscalations } from "./escalation.ts";
import type { VmsEvent } from "./event-log.ts";

let nextId = 1;
function breach(entityId: string, timestamp_s: number, overrides: Partial<VmsEvent> = {}): VmsEvent {
  return {
    event_id: nextId++,
    timestamp_s,
    event_type: "tether_breach",
    entity_id: entityId,
    severity: "alert",
    message: "breach",
    zone_id: null,
    source_module: null,
    display_name: `Test Visitor (${entityId})`,
    ...overrides,
  };
}

test("no events → no candidates", () => {
  assert.deepEqual(detectEscalations([], 100), []);
});

test("fewer breaches than the threshold does not flag", () => {
  const events = [breach("V-001", 10), breach("V-001", 20)];
  assert.deepEqual(detectEscalations(events, 30, { windowS: 90, threshold: 3 }), []);
});

test("exactly `threshold` breaches within the window flags the tag", () => {
  const events = [breach("V-001", 10), breach("V-001", 20), breach("V-001", 30)];
  const result = detectEscalations(events, 30, { windowS: 90, threshold: 3 });
  assert.equal(result.length, 1);
  assert.equal(result[0]!.entityId, "V-001");
  assert.equal(result[0]!.count, 3);
});

test("breaches outside the window are excluded from the count", () => {
  // Two breaches long ago, one recent — should not reach threshold 3 within
  // a 90s window ending at t=200.
  const events = [breach("V-001", 10), breach("V-001", 20), breach("V-001", 195)];
  assert.deepEqual(detectEscalations(events, 200, { windowS: 90, threshold: 3 }), []);
});

test("window boundaries are inclusive at both ends", () => {
  // nowS=100, windowS=90 → window is [10, 100]. A breach exactly at 10 and
  // exactly at 100 should both count.
  const events = [breach("V-001", 10), breach("V-001", 50), breach("V-001", 100)];
  const result = detectEscalations(events, 100, { windowS: 90, threshold: 3 });
  assert.equal(result.length, 1, "breaches at both window edges should count");

  const justOutside = [breach("V-001", 9), breach("V-001", 50), breach("V-001", 100)];
  assert.deepEqual(
    detectEscalations(justOutside, 100, { windowS: 90, threshold: 3 }),
    [],
    "a breach 1s before the window starts must not count",
  );
});

test("non-breach event types never count toward the threshold, however many there are", () => {
  const events = [
    breach("V-001", 10, { event_type: "zone_entry" }),
    breach("V-001", 20, { event_type: "zone_exit" }),
    breach("V-001", 30, { event_type: "patrol_checkpoint" }),
    breach("V-001", 40, { event_type: "permit_granted" }),
  ];
  assert.deepEqual(detectEscalations(events, 40, { windowS: 90, threshold: 3 }), []);
});

test("different entities are tracked independently — one tag's breaches never count toward another's", () => {
  const events = [
    breach("V-001", 10),
    breach("V-001", 20),
    breach("V-002", 15),
    breach("V-002", 25),
  ];
  // Neither has reached 3 individually, even though 4 breach events exist total.
  assert.deepEqual(detectEscalations(events, 30, { windowS: 90, threshold: 3 }), []);
});

test("multiple flagged entities are sorted by count descending", () => {
  const events = [
    breach("V-001", 10),
    breach("V-001", 20),
    breach("V-001", 30),
    breach("V-002", 5),
    breach("V-002", 15),
    breach("V-002", 25),
    breach("V-002", 35),
  ];
  const result = detectEscalations(events, 40, { windowS: 90, threshold: 3 });
  assert.equal(result.length, 2);
  assert.equal(result[0]!.entityId, "V-002", "4 breaches should sort before 3");
  assert.equal(result[0]!.count, 4);
  assert.equal(result[1]!.entityId, "V-001");
  assert.equal(result[1]!.count, 3);
});

test("a tie in count breaks by most recent breach first", () => {
  const events = [
    breach("V-001", 10),
    breach("V-001", 20),
    breach("V-001", 30),
    breach("V-002", 15),
    breach("V-002", 25),
    breach("V-002", 45),
  ];
  const result = detectEscalations(events, 50, { windowS: 90, threshold: 3 });
  assert.equal(result.length, 2);
  assert.equal(result[0]!.entityId, "V-002", "last breach at t=45 is more recent than V-001's t=30");
});

test("displayName comes from the latest event, falling back to entityId if absent", () => {
  const withName = [breach("V-001", 10), breach("V-001", 20), breach("V-001", 30)];
  assert.equal(
    detectEscalations(withName, 30, { windowS: 90, threshold: 3 })[0]!.displayName,
    "Test Visitor (V-001)",
  );

  const withoutName = withName.map((e) => ({ ...e, display_name: undefined }));
  assert.equal(
    detectEscalations(withoutName, 30, { windowS: 90, threshold: 3 })[0]!.displayName,
    "V-001",
  );
});

test("firstAtS and lastAtS reflect the actual span of the flagged breaches, not the whole window", () => {
  const events = [breach("V-001", 12), breach("V-001", 40), breach("V-001", 55)];
  const result = detectEscalations(events, 90, { windowS: 90, threshold: 3 });
  assert.equal(result[0]!.firstAtS, 12);
  assert.equal(result[0]!.lastAtS, 55);
});

test("default config flags 3 breaches within 90s", () => {
  const events = [breach("V-001", 0), breach("V-001", 45), breach("V-001", 89)];
  assert.equal(detectEscalations(events, 89).length, 1);
});
