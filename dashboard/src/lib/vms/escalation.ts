import { BREACH_EVENT_TYPES, type VmsEvent } from "./event-log.ts";

export type EscalationCandidate = {
  entityId: string;
  displayName: string;
  count: number;
  windowS: number;
  firstAtS: number;
  lastAtS: number;
  events: VmsEvent[];
};

export type EscalationConfig = {
  /** How far back, in simulated seconds, to look for breaches. */
  windowS: number;
  /** Breach count within that window that flags the tag. */
  threshold: number;
};

export const DEFAULT_ESCALATION_CONFIG: EscalationConfig = {
  windowS: 90,
  threshold: 3,
};

/**
 * Flags a tag as a repeat-breach pattern when the SAME entity produces
 * `threshold` or more breach-type events (tether/dwell/loitering/tailgate/
 * stairwell-loiter) within a `windowS`-second sliding window ending at
 * `nowS`. A single breach, or breaches spread thinly across a long visit,
 * do not flag — this is specifically "several breaches, close together,
 * same tag," the pattern that suggests deliberate probing rather than one
 * bad wander.
 *
 * Pure and stateless by design: it re-derives candidates fresh from the
 * event log every call rather than tracking state itself, so a caller can
 * diff this tick's candidates against last tick's to detect newly-crossed
 * thresholds (see Simulation.emit()) without this function needing to know
 * anything about "new" vs "still flagged."
 */
export function detectEscalations(
  events: readonly VmsEvent[],
  nowS: number,
  config: EscalationConfig = DEFAULT_ESCALATION_CONFIG,
): EscalationCandidate[] {
  const windowStart = nowS - config.windowS;
  const byEntity = new Map<string, VmsEvent[]>();

  for (const e of events) {
    if (!BREACH_EVENT_TYPES.includes(e.event_type)) continue;
    if (e.timestamp_s < windowStart || e.timestamp_s > nowS) continue;
    const list = byEntity.get(e.entity_id);
    if (list) list.push(e);
    else byEntity.set(e.entity_id, [e]);
  }

  const out: EscalationCandidate[] = [];
  for (const [entityId, list] of byEntity) {
    if (list.length < config.threshold) continue;
    const sorted = [...list].sort((a, b) => a.timestamp_s - b.timestamp_s);
    const latest = sorted[sorted.length - 1]!;
    out.push({
      entityId,
      displayName: latest.display_name ?? entityId,
      count: sorted.length,
      windowS: config.windowS,
      firstAtS: sorted[0]!.timestamp_s,
      lastAtS: latest.timestamp_s,
      events: sorted,
    });
  }

  return out.sort((a, b) => b.count - a.count || b.lastAtS - a.lastAtS);
}
