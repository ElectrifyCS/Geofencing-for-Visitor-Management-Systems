import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Snapshot } from "@/lib/vms/simulation";

const TOOLTIP_STYLE = {
  background: "var(--color-elevated)",
  border: "1px solid var(--color-border)",
  borderRadius: 8,
  fontSize: 12,
  fontFamily: "var(--font-sans)",
};
const AXIS_TICK = { fill: "var(--color-subtle)", fontSize: 10 };

export function ActivityChart({ data }: { data: Snapshot["charts"]["activity"] }) {
  if (data.length === 0) {
    return <EmptyChart label="Activity trend" note="No events yet this loop." />;
  }
  return (
    <div className="h-40 sm:h-48">
      <p className="mb-2 text-sm font-medium tracking-tight">Activity trend</p>
      <ResponsiveContainer width="100%" height="85%">
        <AreaChart data={data} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
          <CartesianGrid stroke="var(--color-border)" vertical={false} />
          <XAxis dataKey="bucket" tick={AXIS_TICK} axisLine={{ stroke: "var(--color-border)" }} tickLine={false} />
          <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} allowDecimals={false} width={28} />
          <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={() => "recent activity"} />
          <Area type="monotone" dataKey="info" stackId="1" stroke="var(--color-subtle)" fill="var(--color-subtle)" fillOpacity={0.25} />
          <Area type="monotone" dataKey="warning" stackId="1" stroke="var(--color-warn)" fill="var(--color-warn)" fillOpacity={0.35} />
          <Area type="monotone" dataKey="alert" stackId="1" stroke="var(--color-alert)" fill="var(--color-alert)" fillOpacity={0.4} />
          <Area type="monotone" dataKey="critical" stackId="1" stroke="var(--color-crit)" fill="var(--color-crit)" fillOpacity={0.5} />
        </AreaChart>
      </ResponsiveContainer>
      <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-subtle">
        <li className="flex items-center gap-1"><Swatch color="var(--color-subtle)" />info</li>
        <li className="flex items-center gap-1"><Swatch color="var(--color-warn)" />warning</li>
        <li className="flex items-center gap-1"><Swatch color="var(--color-alert)" />alert</li>
        <li className="flex items-center gap-1"><Swatch color="var(--color-crit)" />critical</li>
      </ul>
    </div>
  );
}

export function BreachLeaderboard({ data }: { data: Snapshot["charts"]["byEntity"] }) {
  if (data.length === 0) {
    return <EmptyChart label="Breaches by tag" note="No breaches recorded yet." />;
  }
  const top = data.slice(0, 6).map((d) => ({ ...d, shortName: d.displayName.split(" (")[0] ?? d.displayName }));
  return (
    <div className="h-40 sm:h-48">
      <p className="mb-2 text-sm font-medium tracking-tight">Breaches by tag</p>
      <ResponsiveContainer width="100%" height="85%">
        <BarChart data={top} layout="vertical" margin={{ top: 4, right: 12, left: 4, bottom: 0 }}>
          <CartesianGrid stroke="var(--color-border)" horizontal={false} />
          <XAxis type="number" tick={AXIS_TICK} axisLine={false} tickLine={false} allowDecimals={false} />
          <YAxis
            type="category"
            dataKey="shortName"
            tick={AXIS_TICK}
            axisLine={false}
            tickLine={false}
            width={72}
          />
          <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(value: number) => [`${value} breaches`, ""]} />
          <Bar dataKey="count" fill="var(--color-alert)" radius={[0, 4, 4, 0]} maxBarSize={16} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function Swatch({ color }: { color: string }) {
  return <span className="inline-block size-2 rounded-full" style={{ background: color }} />;
}

function EmptyChart({ label, note }: { label: string; note: string }) {
  return (
    <div className="flex h-40 flex-col justify-center sm:h-48">
      <p className="text-sm font-medium tracking-tight">{label}</p>
      <p className="mt-1 text-sm text-subtle">{note}</p>
    </div>
  );
}
