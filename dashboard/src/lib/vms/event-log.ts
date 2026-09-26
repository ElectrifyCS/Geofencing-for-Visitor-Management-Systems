/**
 * Port of geofencing/event_log.py — shared sink every engine logs into,
 * and the dashboard subscribes to. Severity is computed, not hardcoded.
 */

export type Severity = "info" | "warning" | "alert" | "critical";

const SEVERITY_ORDER: Severity[] = ["info", "warning", "alert", "critical"];

const BASE_SEVERITY_SCORE: Record<string, number> = {
  zone_entry: 0,
  zone_exit: 0,
  floor_crossing: 0,
  stairwell_entry: 0,
  stairwell_exit: 0,
  beacon_correction_rejected: 3,
  speed_anomaly: 4,
  stairwell_speed: 4,
  presence_exit: 4,
  tether_breach: 6,
  dwell_anomaly: 6,
  tag_drop: 6,
  permit_denied: 6,
  permit_granted: 0,
  permit_authorized: 0,
  tailgating: 7,
  loitering_unauthorized: 8,
  stuck_between_floors: 7,
  spoofing_detected: 9,
  alert_suppressed_warmup: 2,
  patrol_checkpoint: 0,
  patrol_nfc_verified: 0,
  patrol_ble_proximity: 0,
  patrol_complete: 0,
  patrol_missed: 6,
  patrol_overdue: 6,
  patrol_sequence_break: 7,
  nfc_tap_required: 3,
  nfc_spoof_attempt: 8,
  stairwell_loiter: 6,
  repeat_breach_pattern: 9,
  guard_dispatched: 0,
};

/**
 * Event types that count as a genuine breach for KPI/escalation purposes —
 * the single source of truth so the "Breaches" KPI and repeat-offender
 * escalation detection can never quietly drift apart on what counts.
 */
export const BREACH_EVENT_TYPES: readonly string[] = [
  "tether_breach",
  "dwell_anomaly",
  "loitering_unauthorized",
  "tailgating",
  "stairwell_loiter",
];

const RISK_ESCALATION_SCORE: Record<string, number> = {
  public: 0,
  escort_required: 2,
  prohibited: 4,
};

export function computeSeverity(
  eventType: string,
  riskLevel?: string | null,
): Severity {
  let score = BASE_SEVERITY_SCORE[eventType] ?? 0;
  if (riskLevel) score += RISK_ESCALATION_SCORE[riskLevel] ?? 0;
  score = Math.min(score, 10);
  if (score >= 9) return "critical";
  if (score >= 7) return "alert";
  if (score >= 4) return "warning";
  return "info";
}

export type VmsEvent = {
  event_id: number;
  timestamp_s: number;
  event_type: string;
  entity_id: string;
  severity: Severity;
  message: string;
  zone_id: string | null;
  source_module: string | null;
  display_name?: string;
};

let nextId = 1;

export class EventLog {
  private events: VmsEvent[] = [];
  private subscribers: Array<(event: VmsEvent) => void> = [];

  maxEvents: number;

  constructor(maxEvents = 800) {
    this.maxEvents = maxEvents;
  }

  log(
    timestamp_s: number,
    event_type: string,
    entity_id: string,
    message: string,
    opts?: {
      zone_id?: string | null;
      risk_level?: string | null;
      source_module?: string | null;
      display_name?: string;
    },
  ): VmsEvent {
    const event: VmsEvent = {
      event_id: nextId++,
      timestamp_s,
      event_type,
      entity_id,
      severity: computeSeverity(event_type, opts?.risk_level),
      message,
      zone_id: opts?.zone_id ?? null,
      source_module: opts?.source_module ?? null,
      display_name: opts?.display_name,
    };
    this.events.push(event);
    if (this.events.length > this.maxEvents) this.events.shift();
    for (const cb of this.subscribers) cb(event);
    return event;
  }

  subscribe(callback: (event: VmsEvent) => void): () => void {
    this.subscribers.push(callback);
    return () => {
      this.subscribers = this.subscribers.filter((c) => c !== callback);
    };
  }

  query(filter?: {
    since_s?: number;
    until_s?: number;
    entity_id?: string;
    event_type?: string;
    min_severity?: Severity;
    limit?: number;
  }): VmsEvent[] {
    let results = this.events;
    if (filter?.since_s != null) {
      results = results.filter((e) => e.timestamp_s >= filter.since_s!);
    }
    if (filter?.until_s != null) {
      results = results.filter((e) => e.timestamp_s <= filter.until_s!);
    }
    if (filter?.entity_id) {
      results = results.filter((e) => e.entity_id === filter.entity_id);
    }
    if (filter?.event_type) {
      results = results.filter((e) => e.event_type === filter.event_type);
    }
    if (filter?.min_severity) {
      const min = SEVERITY_ORDER.indexOf(filter.min_severity);
      results = results.filter((e) => SEVERITY_ORDER.indexOf(e.severity) >= min);
    }
    results = [...results].sort((a, b) => a.timestamp_s - b.timestamp_s);
    if (filter?.limit != null) results = results.slice(-filter.limit);
    return results;
  }

  clear(): void {
    this.events = [];
  }
}

export function severityRank(s: Severity): number {
  return SEVERITY_ORDER.indexOf(s);
}
