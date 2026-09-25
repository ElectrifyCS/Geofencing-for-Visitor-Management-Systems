import assert from "node:assert/strict";
import { test } from "node:test";
import { ZoneLockTracker } from "./zone-lock.ts";

test("starts unlocked", () => {
  const t = new ZoneLockTracker();
  assert.equal(t.lockedZone, null);
});

test("locks after exactly minConfirmReadings consecutive matching readings", () => {
  const t = new ZoneLockTracker(3);
  assert.equal(t.update("lobby"), null);
  assert.equal(t.update("lobby"), null);
  assert.equal(t.update("lobby"), "lobby");
});

test("a single stray reading does not reset an otherwise-consistent run early", () => {
  // Two matching reads, then immediately a third matching read still locks —
  // regression guard against an off-by-one in the confirm count.
  const t = new ZoneLockTracker(3);
  t.update("it_dept");
  t.update("it_dept");
  assert.equal(t.update("it_dept"), "it_dept");
});

test("fewer than minConfirmReadings never locks", () => {
  const t = new ZoneLockTracker(3);
  t.update("server_room");
  assert.equal(t.update("server_room"), null);
});

test("boundary ping-pong between two zones never locks either one", () => {
  // The exact failure mode ZoneLockTracker exists to prevent: a tag jittering
  // between two zones near a shared border must never accumulate enough
  // consecutive same-zone readings to flip the lock.
  const t = new ZoneLockTracker(3);
  const sequence = ["lobby", "corridor", "lobby", "corridor", "lobby", "corridor"];
  for (const zone of sequence) {
    assert.equal(t.update(zone), null, `should still be unlocked after reading "${zone}"`);
  }
});

test("an established lock survives a short interruption shorter than the confirm threshold", () => {
  const t = new ZoneLockTracker(3);
  t.update("lobby");
  t.update("lobby");
  t.update("lobby");
  assert.equal(t.lockedZone, "lobby");

  // Two stray "corridor" reads — one short of the threshold — must not
  // dislodge the existing lock.
  assert.equal(t.update("corridor"), "lobby");
  assert.equal(t.update("corridor"), "lobby");

  // Back to the locked zone: candidate run resets, lock unchanged.
  assert.equal(t.update("lobby"), "lobby");
});

test("a sustained transition still confirms and switches the lock", () => {
  const t = new ZoneLockTracker(3);
  t.update("lobby");
  t.update("lobby");
  t.update("lobby");
  assert.equal(t.lockedZone, "lobby");

  t.update("corridor");
  t.update("corridor");
  assert.equal(t.lockedZone, "lobby", "not yet — only 2 of 3 confirmations");
  assert.equal(t.update("corridor"), "corridor", "3rd consecutive read confirms the switch");
});

test("null is tracked like any other zone value (e.g. leaving all defined zones)", () => {
  const t = new ZoneLockTracker(2);
  t.update("lobby");
  t.update("lobby");
  assert.equal(t.lockedZone, "lobby");

  t.update(null);
  assert.equal(t.update(null), null);
});

test("EDGE CASE (flag, not silently fixed): a brand-new tracker's first reading, if null, locks instantly with zero confirmations", () => {
  // lockedZone starts at null, and null is also a legitimate zone value
  // ("in no defined zone"), so a fresh tracker's first null reading matches
  // the *initial* lockedZone by coincidence and short-circuits the debounce
  // that every other transition (including real-zone -> null, tested above)
  // correctly goes through. Only matters if a tracker's first-ever reading
  // can legitimately be null in practice — worth a product decision, not
  // assumed here either way.
  const t = new ZoneLockTracker(3);
  assert.equal(t.update(null), null, "locks on the very first call, not the 3rd");
});

test("minConfirmReadings of 1 locks on the very first reading", () => {
  const t = new ZoneLockTracker(1);
  assert.equal(t.update("lobby"), "lobby");
});

test("reset() clears lock, candidate and count", () => {
  const t = new ZoneLockTracker(3);
  t.update("lobby");
  t.update("lobby");
  t.update("lobby");
  assert.equal(t.lockedZone, "lobby");

  t.reset();
  assert.equal(t.lockedZone, null);
  // After reset, a fresh 3-read run is required again — confirms internal
  // candidate/count were actually cleared, not just lockedZone.
  t.update("corridor");
  t.update("corridor");
  assert.equal(t.update("corridor"), "corridor");
});

test("re-confirming the already-locked zone resets the candidate run (no stale count leaks)", () => {
  const t = new ZoneLockTracker(3);
  t.update("lobby");
  t.update("lobby");
  t.update("lobby");

  // Start a candidate run for "corridor" but don't finish it.
  t.update("corridor");
  // A read of the currently-locked zone should fully reset that in-progress
  // candidate run, not just pause it.
  t.update("lobby");
  t.update("corridor");
  t.update("corridor");
  assert.equal(t.lockedZone, "lobby", "the earlier single corridor read must not count toward this run");
  assert.equal(t.update("corridor"), "corridor", "3rd consecutive corridor read now confirms");
});
