import { useEffect, useSyncExternalStore } from "react";
import { Pause, Play, Radio } from "lucide-react";
import { simulation, type Snapshot } from "@/lib/vms/simulation";
import type { Severity } from "@/lib/vms/event-log";
import { cn } from "@/lib/utils";
import { EmergencyDispatch } from "./emergency-dispatch";
import { EventFeed } from "./event-feed";
import { FacilityMap, FloorPills } from "./facility-map";
import { PatrolBoard } from "./patrol-board";
import { ActivityChart, BreachLeaderboard } from "./soc-charts";
import { StairwellShaft } from "./stairwell-shaft";

function useOps(): Snapshot {
  return useSyncExternalStore(
    simulation.subscribe,
    simulation.getSnapshot,
    simulation.getServerSnapshot,
  );
}

function fmtClock(t: number): string {
  const s = Math.max(0, Math.floor(t));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function Kpi({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | string;
  tone?: "crit" | "warn" | "ok";
}) {
  return (
    <div className="rounded-2xl bg-surface px-3 py-3 shadow-[var(--shadow-border)] sm:px-4">
      <p className="text-xs text-subtle">{label}</p>
      <p
        className={cn(
          "mt-1 font-mono text-xl font-medium tabular-nums tracking-tight",
          tone === "crit" && "text-crit",
          tone === "warn" && "text-alert",
          tone === "ok" && "text-ok",
          !tone && "text-fg",
        )}
      >
        {value}
      </p>
    </div>
  );
}

export function OpsApp() {
  const snap = useOps();

  useEffect(() => {
    simulation.start();
    return () => simulation.stop();
  }, []);

  const newestId = snap.events[0]?.event_id ?? null;

  return (
    <div className="min-h-dvh bg-bg text-fg">
      <header className="border-b border-border px-4 py-3 sm:px-6">
        <div className="mx-auto flex max-w-[1400px] flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-start gap-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="live-dot size-2 rounded-full bg-ok" />
                <h1 className="text-lg font-medium tracking-tight sm:text-xl">
                  VMS Sentinel
                </h1>
              </div>
              <p className="mt-0.5 text-sm text-muted">
                Demo Facility HQ · live EventLog, not a recorded replay
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-mono text-sm tabular-nums text-muted">
              {fmtClock(snap.t)}
              <span className="ml-2 text-subtle">loop {snap.cycle + 1}</span>
            </p>
            <button
              type="button"
              onClick={() => simulation.setPlaying(!snap.playing)}
              className="inline-flex min-h-11 items-center gap-2 rounded-xl bg-accent px-3.5 pr-3 text-sm font-medium text-accent-fg transition-transform duration-150 ease-out active:scale-[0.96]"
            >
              {snap.playing ? (
                <Pause className="size-4" strokeWidth={1.75} />
              ) : (
                <Play className="size-4" strokeWidth={1.75} />
              )}
              {snap.playing ? "Pause" : "Resume"}
            </button>
            {[1, 4, 10].map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => simulation.setSpeed(s)}
                className={cn(
                  "min-h-11 rounded-xl px-3 font-mono text-sm transition-[background-color,color,transform] duration-150 ease-out active:scale-[0.96]",
                  snap.speed === s
                    ? "bg-elevated text-fg shadow-[var(--shadow-border)]"
                    : "text-subtle hover:text-fg",
                )}
              >
                {s}×
              </button>
            ))}
          </div>
        </div>
      </header>

      {snap.latestCritical ? (
        <div className="border-b border-crit/30 bg-crit-dim px-4 py-2.5 sm:px-6">
          <p className="mx-auto max-w-[1400px] text-sm font-medium text-fg">
            {snap.latestCritical.display_name?.split(" (")[0] ?? snap.latestCritical.entity_id}
            <span className="ml-2 font-normal text-muted">{snap.latestCritical.message}</span>
          </p>
        </div>
      ) : null}

      <main className="mx-auto max-w-[1400px] px-4 py-4 sm:px-6 sm:py-5">
        <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-7">
          <Kpi label="Tracked" value={snap.kpis.tracked} />
          <Kpi label="Events" value={snap.kpis.events} />
          <Kpi label="Denied" value={snap.kpis.denied} tone="crit" />
          <Kpi label="Breaches" value={snap.kpis.breaches} tone="warn" />
          <Kpi label="Floor crossings" value={snap.kpis.floorCrossings} />
          <Kpi
            label="Patrol misses"
            value={snap.kpis.patrolMisses}
            tone={snap.kpis.patrolMisses > 0 ? "crit" : "ok"}
          />
          <Kpi
            label="Escalations"
            value={snap.kpis.escalations}
            tone={snap.kpis.escalations > 0 ? "crit" : "ok"}
          />
        </div>

        <div className="mb-4">
          <EmergencyDispatch escalations={snap.escalations} />
        </div>

        <div className="mb-4 flex flex-col gap-3 lg:flex-row">
          <button
            type="button"
            title={`Stream live · seq ${snap.streamSeq}`}
            className="inline-flex min-h-11 items-center gap-2 self-start rounded-xl bg-elevated px-3 text-xs font-medium text-muted shadow-[var(--shadow-border)]"
            disabled
          >
            <Radio className="size-3.5 shrink-0" strokeWidth={1.75} />
            <span className="hidden sm:inline">Stream live · seq {snap.streamSeq}</span>
            <span className="sm:hidden">seq {snap.streamSeq}</span>
          </button>
          <div className="flex flex-wrap gap-2">
            {(
              [
                ["missed-nfc", "Miss NFC tap"],
                ["stair-loiter", "Stair loiter"],
                ["floor-skip", "Floor skip"],
                ["tailgate", "Tailgate"],
                ["probe", "Repeat breach"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => simulation.inject(id)}
                className="min-h-11 rounded-xl bg-surface px-3 text-sm text-fg shadow-[var(--shadow-border)] transition-[transform,background-color] duration-150 ease-out hover:bg-elevated active:scale-[0.96]"
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(320px,0.9fr)]">
          <div className="flex min-h-0 flex-col gap-4">
            <section className="rounded-2xl bg-surface p-3 shadow-[var(--shadow-border)] sm:p-4">
              <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <h2 className="text-sm font-medium tracking-tight">Live map</h2>
                <FloorPills floor={snap.floor} onChange={(f) => simulation.setFloor(f)} />
              </div>
              <FacilityMap entities={snap.entities} floor={snap.floor} />
              <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-subtle">
                <li>Olive public</li>
                <li>Sand escort-required</li>
                <li>Red prohibited</li>
                <li>Dashed east stairwell</li>
                <li>Square NFC · ring BLE</li>
              </ul>
            </section>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <StairwellShaft data={snap.stairwell} />
              <PatrolBoard data={snap.patrol} />
            </div>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <section className="rounded-2xl bg-surface p-3 shadow-[var(--shadow-border)] sm:p-4">
                <ActivityChart data={snap.charts.activity} />
              </section>
              <section className="rounded-2xl bg-surface p-3 shadow-[var(--shadow-border)] sm:p-4">
                <BreachLeaderboard data={snap.charts.byEntity} />
              </section>
            </div>
          </div>
          <div className="flex min-h-[420px] flex-col xl:max-h-[calc(100dvh-12rem)]">
            <EventFeed
              events={snap.events}
              minSeverity={snap.minSeverity}
              onMinSeverity={(s: Severity) => simulation.setMinSeverity(s)}
              newestId={newestId}
            />
          </div>
        </div>
      </main>
    </div>
  );
}
