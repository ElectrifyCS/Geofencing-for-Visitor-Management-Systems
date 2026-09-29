import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { EntityPose, ZoneOccupancy } from "@/lib/vms/simulation";

const RISK_COLOR: Record<string, string> = {
  public: "var(--color-zone-public-stroke)",
  escort_required: "var(--color-zone-escort-stroke)",
  prohibited: "var(--color-zone-prohibited-stroke)",
};

const TOOLTIP_STYLE = {
  background: "var(--color-elevated)",
  border: "1px solid var(--color-border)",
  borderRadius: 8,
  fontSize: 12,
  fontFamily: "var(--font-sans)",
};
const AXIS_TICK = { fill: "var(--color-subtle)", fontSize: 10 };

function ZoneTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: { payload: ZoneOccupancy }[];
}) {
  if (!active || !payload?.length) return null;
  const z = payload[0]!.payload;
  return (
    <div style={TOOLTIP_STYLE} className="px-2.5 py-2">
      <p className="font-medium text-fg">{z.zoneName}</p>
      <p className="text-subtle">
        {z.risk.replace("_", " ")} · {z.count} tracked
      </p>
    </div>
  );
}

export function ZoneOccupancyChart({ zoneOccupancy }: { zoneOccupancy: ZoneOccupancy[] }) {
  if (zoneOccupancy.length === 0 || zoneOccupancy.every((z) => z.count === 0)) {
    return (
      <div className="flex h-40 flex-col justify-center sm:h-48">
        <p className="text-sm font-medium tracking-tight">Zone risk exposure</p>
        <p className="mt-1 text-sm text-subtle">No tags currently in a room zone.</p>
      </div>
    );
  }
  return (
    <div className="h-40 sm:h-48">
      <p className="mb-2 text-sm font-medium tracking-tight">Zone risk exposure</p>
      <ResponsiveContainer width="100%" height="85%">
        <BarChart data={zoneOccupancy} layout="vertical" margin={{ top: 4, right: 12, left: 4, bottom: 0 }}>
          <CartesianGrid stroke="var(--color-border)" horizontal={false} />
          <XAxis type="number" tick={AXIS_TICK} axisLine={false} tickLine={false} allowDecimals={false} />
          <YAxis
            type="category"
            dataKey="zoneName"
            tick={AXIS_TICK}
            axisLine={false}
            tickLine={false}
            width={92}
          />
          <Tooltip content={<ZoneTooltip />} cursor={{ fill: "var(--color-elevated)" }} />
          <Bar dataKey="count" radius={[0, 4, 4, 0]} maxBarSize={16}>
            {zoneOccupancy.map((z) => (
              <Cell key={z.zoneId} fill={RISK_COLOR[z.risk] ?? "var(--color-muted)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function SignalQualityPanel({ entities }: { entities: EntityPose[] }) {
  const tracked = [...entities].filter((e) => !e.hidden).sort((a, b) => a.name.localeCompare(b.name));
  return (
    <div className="flex h-40 flex-col sm:h-48">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <p className="text-sm font-medium tracking-tight">Positioning telemetry</p>
        <p className="font-mono text-xs text-subtle">RSSI → range</p>
      </div>
      {tracked.length === 0 ? (
        <p className="text-sm text-subtle">No tags in range.</p>
      ) : (
        <ul className="min-h-0 flex-1 space-y-2.5 overflow-y-auto pr-1">
          {tracked.map((e) => {
            const conf = e.confidencePct;
            const tone =
              conf >= 66 ? "var(--color-ok)" : conf >= 33 ? "var(--color-warn)" : "var(--color-crit)";
            return (
              <li key={e.id}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-sm text-fg">{e.name}</span>
                  <span className="shrink-0 font-mono text-xs text-subtle">
                    {e.rssiDbm.toFixed(0)} dBm
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-bg">
                    <div
                      className="h-full rounded-full transition-[width] duration-300 ease-out"
                      style={{ width: `${conf}%`, background: tone }}
                    />
                  </div>
                  <span className="w-[5.5rem] shrink-0 text-right font-mono text-xs text-subtle">
                    ±{e.accuracyM.toFixed(1)}m · {conf}%
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
