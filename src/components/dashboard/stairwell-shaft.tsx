import type { Snapshot } from "@/lib/vms/simulation";

const TONE: Record<string, string> = {
  a: "var(--color-tone-a)",
  b: "var(--color-tone-b)",
  c: "var(--color-tone-c)",
  host: "var(--color-tone-host)",
  g1: "var(--color-tone-g1)",
  g2: "var(--color-tone-g2)",
  unk: "var(--color-tone-unk)",
};

export function StairwellShaft({ data }: { data: Snapshot["stairwell"] }) {
  const maxZ = 7;
  const h = 220;
  const top = 18;
  const bot = h - 18;
  const yOf = (z: number) => bot - (z / maxZ) * (bot - top);

  return (
    <section className="rounded-2xl bg-surface p-3 shadow-[var(--shadow-border)] sm:p-4">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium tracking-tight">East stairwell</h2>
        <p className="text-xs text-subtle">Gateway crossing · no Kalman</p>
      </div>
      <div className="grid grid-cols-[96px_1fr] gap-3 sm:grid-cols-[120px_1fr]">
        <svg viewBox="0 0 96 220" className="h-56 w-full rounded-lg bg-bg">
          <line
            x1="48"
            y1={top}
            x2="48"
            y2={bot}
            stroke="var(--color-border-strong)"
            strokeWidth="2"
            strokeDasharray="3 4"
          />
          {data.landings.map((l) => {
            const y = yOf(l.z);
            return (
              <g key={l.id}>
                <rect
                  x={16}
                  y={y - 7}
                  width={64}
                  height={14}
                  rx={4}
                  fill={l.lit ? "var(--color-ok-dim)" : "var(--color-elevated)"}
                  stroke={l.lit ? "var(--color-ok)" : "var(--color-border)"}
                  strokeWidth={1}
                />
                <text
                  x={48}
                  y={y + 3.5}
                  textAnchor="middle"
                  fill="var(--color-fg)"
                  fontSize={10}
                  fontFamily="var(--font-mono)"
                >
                  {l.id}
                </text>
              </g>
            );
          })}
          {data.occupants.map((o) => {
            const y = yOf(Math.min(maxZ, Math.max(0, o.z)));
            return (
              <circle
                key={o.id}
                cx={48}
                cy={y}
                r={6}
                fill={TONE[o.tone] ?? TONE.unk}
                stroke="var(--color-bg)"
                strokeWidth={1.4}
              />
            );
          })}
        </svg>
        <div className="flex flex-col justify-between py-1">
          <p className="text-xs leading-relaxed text-muted">
            Landing beacons identified by ID with smoothed hysteresis — same
            lock tracker as the elevator shaft, without accelerometer fusion.
          </p>
          <ul className="mt-3 space-y-2">
            {data.occupants.length === 0 ? (
              <li className="text-sm text-subtle">No one on the stairs</li>
            ) : (
              data.occupants.map((o) => (
                <li key={o.id} className="text-sm">
                  <span className="font-medium text-fg">{o.name}</span>
                  <span className="ml-2 font-mono text-xs text-muted">
                    z {o.z.toFixed(1)}m
                    {o.lockedFloor ? ` · lock ${o.lockedFloor}` : " · between landings"}
                  </span>
                </li>
              ))
            )}
          </ul>
        </div>
      </div>
    </section>
  );
}
