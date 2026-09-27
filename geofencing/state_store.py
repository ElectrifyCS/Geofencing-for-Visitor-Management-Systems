"""
state_store.py — Durable, restart-surviving key-value persistence for
per-entity tracking state.

The problem this exists to fix: every tracker in this project today
(ZoneLockTracker, DwellMonitor, PresenceTracker, ReliabilityWarmup,
TagRegistry, PermitRegistry) lives as a plain dict on a single in-process
object. A deploy, a crash, or an OOM kill wipes all of it instantly —
every visitor's zone lock, every dwell timer, every warm-up state resets
to zero with no trace. For a demo run that's harmless; for a real 24/7
deployment, it means an incident leaves no record of what state existed
right before it happened, which is exactly what IT needs for a recap.

This module only solves the storage half of that. It knows nothing about
ZoneLockTracker or any other specific tracker — it stores and retrieves
plain JSON-serializable dicts by string key. The caller (see
IntegratedVisitorManagement in integrated.py) decides what to serialize
and when.

Two implementations behind one small interface:
  - InMemoryStateStore: a plain dict. Zero external dependency, and the
    default everywhere unless a caller explicitly configures something
    else — demo.py's zero-install-friction story is completely
    unaffected by this module existing.
  - RedisStateStore: backed by any redis-py-compatible client. In
    production that's a real Redis connection; in tests, it's
    fakeredis.FakeStrictRedis() — same interface, no live Redis server
    required to test any of this.

Scope note: this is the FIRST tracker to get this treatment
(ZoneLockTracker, wired in integrated.py). DwellMonitor, PresenceTracker,
ReliabilityWarmup, TagRegistry and PermitRegistry all have the identical
problem and need the identical fix — deliberately not done in this pass.
"""

from __future__ import annotations

import json
from typing import List, Optional, Protocol


class StateStore(Protocol):
    def get(self, key: str) -> Optional[dict]: ...
    def set(self, key: str, value: dict) -> None: ...
    def delete(self, key: str) -> None: ...
    def keys(self, prefix: str) -> List[str]: ...


class InMemoryStateStore:
    """
    Default backend. A plain dict, process-local, with no persistence
    across a restart at all — this is the pre-existing behavior of every
    tracker in the project today, kept as the default so nothing changes
    for anyone who doesn't explicitly configure a RedisStateStore.
    """

    def __init__(self):
        self._data: dict = {}

    def get(self, key: str) -> Optional[dict]:
        return self._data.get(key)

    def set(self, key: str, value: dict) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def keys(self, prefix: str) -> List[str]:
        return [k for k in self._data if k.startswith(prefix)]


class RedisStateStore:
    """
    Backed by any redis-py-compatible client. Values are stored as JSON
    strings — Redis itself only stores bytes/strings, so (de)serialization
    happens here, once, rather than in every caller.

    `key_prefix` namespaces every key this store touches (default
    "geofencing:"), so this can share a Redis instance with other
    services without key collisions.
    """

    def __init__(self, client, key_prefix: str = "geofencing:"):
        self.client = client
        self.key_prefix = key_prefix

    def _full_key(self, key: str) -> str:
        return f"{self.key_prefix}{key}"

    def get(self, key: str) -> Optional[dict]:
        raw = self.client.get(self._full_key(key))
        if raw is None:
            return None
        return json.loads(raw)

    def set(self, key: str, value: dict) -> None:
        self.client.set(self._full_key(key), json.dumps(value))

    def delete(self, key: str) -> None:
        self.client.delete(self._full_key(key))

    def keys(self, prefix: str) -> List[str]:
        full_prefix = self._full_key(prefix)
        raw_keys = self.client.keys(f"{full_prefix}*")
        strip = len(self.key_prefix)
        return [(k.decode() if isinstance(k, bytes) else k)[strip:] for k in raw_keys]


if __name__ == "__main__":
    print("=== InMemoryStateStore: basic round-trip ===")
    mem = InMemoryStateStore()
    mem.set("zone_lock:V-001", {"locked_zone": "lobby", "min_confirm_readings": 3})
    print(f"  get -> {mem.get('zone_lock:V-001')}")
    print(f"  keys('zone_lock:') -> {mem.keys('zone_lock:')}")
    mem.delete("zone_lock:V-001")
    print(f"  after delete, get -> {mem.get('zone_lock:V-001')}")

    print("\n=== RedisStateStore: same round-trip, against fakeredis (no live server) ===")
    try:
        import fakeredis

        client = fakeredis.FakeStrictRedis()
        store = RedisStateStore(client)
        store.set("zone_lock:V-002", {"locked_zone": "server_room", "min_confirm_readings": 3})
        print(f"  get -> {store.get('zone_lock:V-002')}")
        print(f"  keys('zone_lock:') -> {store.keys('zone_lock:')}")

        print("\n=== The actual point: state survives a simulated process restart ===")
        # "Before the crash": one store instance writes state.
        store_before = RedisStateStore(client)
        store_before.set("zone_lock:V-003", {"locked_zone": "it_dept", "min_confirm_readings": 3})
        # "The crash": drop that reference entirely.
        del store_before
        # "After restart": a brand-new RedisStateStore object (as a fresh
        # process would create), pointed at the same underlying Redis.
        store_after = RedisStateStore(client)
        restored = store_after.get("zone_lock:V-003")
        print(f"  Restored after 'restart' with zero replayed events: {restored}")
        assert restored == {"locked_zone": "it_dept", "min_confirm_readings": 3}, (
            "state did not survive the simulated restart"
        )
        print("  OK — state store contents outlive the Python object that wrote them,")
        print("  which is the whole point: it's Redis holding this, not process memory.")
    except ImportError:
        print("  fakeredis not installed — skipping (pip install fakeredis to run this part)")
