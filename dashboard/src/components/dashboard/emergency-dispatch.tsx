import { Siren } from "lucide-react";
import { simulation } from "@/lib/vms/simulation";
import type { EscalationCandidate } from "@/lib/vms/escalation";
import { cn } from "@/lib/utils";

type FlaggedTag = EscalationCandidate & { dispatched: boolean };

export function EmergencyDispatch({ escalations }: { escalations: FlaggedTag[] }) {
  const active = escalations.length > 0;

  return (
    <section
      className={cn(
        "rounded-2xl p-3 shadow-[var(--shadow-border)] sm:p-4",
        active ? "bg-crit-dim ring-1 ring-crit/40" : "bg-surface",
      )}
    >
      <div className="flex items-center gap-2">
        <Siren className={cn("size-4", active ? "text-crit" : "text-subtle")} strokeWidth={1.75} />
        <h2 className="text-sm font-medium tracking-tight">
          Emergency dispatch
          {active ? ` — ${escalations.length} flagged` : null}
        </h2>
      </div>

      {!active ? (
        <p className="mt-2 text-sm text-subtle">
          No repeat-breach patterns detected. A tag is flagged here after 3+ breaches from the
          same visitor within a 90s window — a pattern that suggests deliberate probing rather
          than one bad wander.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {escalations.map((e) => {
            const lastEvent = e.events[e.events.length - 1];
            return (
              <li
                key={e.entityId}
                className="flex flex-col gap-2 rounded-xl bg-surface p-3 shadow-[var(--shadow-border)] sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-fg">{e.displayName}</p>
                  <p className="text-xs text-muted">
                    {e.count} breaches in {Math.max(1, Math.round(e.lastAtS - e.firstAtS))}s
                    {lastEvent ? ` · last: ${lastEvent.event_type.replace(/_/g, " ")}` : null}
                  </p>
                </div>
                <button
                  type="button"
                  disabled={e.dispatched}
                  onClick={() => simulation.dispatch(e.entityId)}
                  className={cn(
                    "min-h-9 shrink-0 rounded-lg px-3 text-xs font-medium transition-[transform,background-color] duration-150 ease-out active:scale-[0.96]",
                    e.dispatched
                      ? "bg-elevated text-subtle"
                      : "bg-crit text-accent-fg hover:brightness-110",
                  )}
                >
                  {e.dispatched ? "Guard dispatched" : "Dispatch guard"}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
