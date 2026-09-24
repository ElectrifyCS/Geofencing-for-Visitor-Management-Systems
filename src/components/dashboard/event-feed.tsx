import type { Severity, VmsEvent } from "@/lib/vms/event-log";
import { cn } from "@/lib/utils";

const SEV_CLASS: Record<Severity, string> = {
  info: "border-subtle bg-elevated",
  warning: "border-warn bg-warn-dim",
  alert: "border-alert bg-alert-dim",
  critical: "border-crit bg-crit-dim",
};

const SEV_LABEL: Record<Severity, string> = {
  info: "INFO",
  warning: "WARN",
  alert: "ALERT",
  critical: "CRIT",
};

function fmt(t: number): string {
  const s = Math.max(0, Math.floor(t));
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function EventFeed({
  events,
  minSeverity,
  onMinSeverity,
  newestId,
}: {
  events: VmsEvent[];
  minSeverity: Severity;
  onMinSeverity: (s: Severity) => void;
  newestId: number | null;
}) {
  const levels: Severity[] = ["info", "warning", "alert", "critical"];
  return (
    <section className="flex min-h-0 flex-1 flex-col rounded-2xl bg-surface p-3 shadow-[var(--shadow-border)] sm:p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium tracking-tight text-fg">Event stream</h2>
        <p className="font-mono text-xs text-subtle">EventLog.subscribe()</p>
      </div>
      <div className="mb-3 flex gap-1 rounded-xl bg-bg p-1">
        {levels.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onMinSeverity(s)}
            className={cn(
              "min-h-10 flex-1 rounded-lg px-2 text-xs font-medium capitalize transition-[background-color,color,transform] duration-150 ease-out active:scale-[0.96]",
              minSeverity === s ? "bg-elevated text-fg" : "text-subtle hover:text-muted",
            )}
          >
            {s === "info" ? "All" : `${s}+`}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
        {events.length === 0 ? (
          <p className="px-2 py-6 text-sm text-subtle">Waiting for the live pipeline…</p>
        ) : (
          events.map((e) => (
            <article
              key={e.event_id}
              className={cn(
                "flex gap-3 rounded-md border-l-[3px] px-3 py-2",
                SEV_CLASS[e.severity],
                e.event_id === newestId && "event-enter",
              )}
            >
              <time className="w-12 shrink-0 font-mono text-xs tabular-nums text-subtle">
                {fmt(e.timestamp_s)}
              </time>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                  <span className="font-mono text-xs font-medium tracking-wider text-muted">
                    {SEV_LABEL[e.severity]}
                  </span>
                  <span className="text-sm font-medium text-fg">
                    {e.display_name?.split(" (")[0] ?? e.entity_id}
                  </span>
                  {e.source_module ? (
                    <span className="font-mono text-xs text-subtle">{e.source_module}</span>
                  ) : null}
                </div>
                <p className="text-sm leading-snug text-muted">{e.message}</p>
              </div>
            </article>
          ))
        )}
      </div>
    </section>
  );
}
