import { NIGHT_LOOP, WORLD, ZONES, type Zone } from "@/lib/vms/facility";
import type { EntityPose } from "@/lib/vms/simulation";
import { cn } from "@/lib/utils";

const PAD = 18;
const VW = 760;
const VH = 248;
const SX = (VW - PAD * 2) / (WORLD.x1 - WORLD.x0);
const SY = (VH - PAD * 2) / (WORLD.y1 - WORLD.y0);

function px(x: number, y: number): [number, number] {
  return [PAD + (x - WORLD.x0) * SX, PAD + (y - WORLD.y0) * SY];
}

function zoneFill(z: Zone): string {
  if (z.kind === "stairwell") return "var(--color-elevated)";
  if (z.kind === "elevator") return "var(--color-surface)";
  if (z.risk === "public") return "var(--color-zone-public)";
  if (z.risk === "escort_required") return "var(--color-zone-escort)";
  return "var(--color-zone-prohibited)";
}

function zoneStroke(z: Zone): string {
  if (z.kind === "stairwell") return "var(--color-border-strong)";
  if (z.kind === "elevator") return "var(--color-border)";
  if (z.risk === "public") return "var(--color-zone-public-stroke)";
  if (z.risk === "escort_required") return "var(--color-zone-escort-stroke)";
  return "var(--color-zone-prohibited-stroke)";
}

const TONE: Record<string, string> = {
  a: "var(--color-tone-a)",
  b: "var(--color-tone-b)",
  c: "var(--color-tone-c)",
  host: "var(--color-tone-host)",
  g1: "var(--color-tone-g1)",
  g2: "var(--color-tone-g2)",
  unk: "var(--color-tone-unk)",
};

export function FacilityMap({
  entities,
  floor,
}: {
  entities: EntityPose[];
  floor: number;
}) {
  const rooms = ZONES.filter(
    (z) => z.kind === "room" && z.floor === floor,
  );
  const shafts = ZONES.filter((z) => z.kind !== "room");
  const cps = NIGHT_LOOP.checkpoints.filter((c) => {
    const f = c.z >= 6.8 ? 3 : c.z >= 3.3 ? 2 : 1;
    return f === floor;
  });

  return (
    <svg
      viewBox={`0 0 ${VW} ${VH}`}
      className="block h-auto w-full rounded-lg bg-bg"
      role="img"
      aria-label={`Facility floor ${floor} map with live unit positions`}
    >
      {rooms.map((z) => {
        const [x, y] = px(z.x0, z.y0);
        const [x1, y1] = px(z.x1, z.y1);
        return (
          <g key={z.id}>
            <rect
              x={x}
              y={y}
              width={x1 - x}
              height={y1 - y}
              rx={8}
              fill={zoneFill(z)}
              stroke={zoneStroke(z)}
              strokeWidth={1}
            />
            <text
              x={x + 8}
              y={y + 16}
              fill="var(--color-muted)"
              fontSize={11}
              fontFamily="var(--font-sans)"
            >
              {z.name.replace(" department", "").replace("Main ", "").replace(" room", "").replace("Floor 2 ", "").replace("Floor 3 ", "").replace(" suite", "")}
            </text>
          </g>
        );
      })}
      {shafts.map((z) => {
        const [x, y] = px(z.x0, z.y0);
        const [x1, y1] = px(z.x1, z.y1);
        return (
          <g key={z.id}>
            <rect
              x={x}
              y={y}
              width={x1 - x}
              height={y1 - y}
              rx={8}
              fill={zoneFill(z)}
              stroke={zoneStroke(z)}
              strokeWidth={1}
              strokeDasharray={z.kind === "stairwell" ? "4 3" : undefined}
            />
            <text
              x={x + 6}
              y={y + 14}
              fill="var(--color-subtle)"
              fontSize={10}
              fontFamily="var(--font-sans)"
            >
              {z.kind === "stairwell" ? "Stairs" : "Lift"}
            </text>
          </g>
        );
      })}
      {cps.map((c) => {
        const [cx, cy] = px(c.x, c.y);
        return (
          <g key={c.id}>
            {c.modality === "nfc" ? (
              <rect
                x={cx - 4}
                y={cy - 4}
                width={8}
                height={8}
                fill="none"
                stroke="var(--color-accent)"
                strokeWidth={1.2}
              />
            ) : (
              <circle
                cx={cx}
                cy={cy}
                r={5}
                fill="none"
                stroke="var(--color-muted)"
                strokeWidth={1}
                strokeDasharray="2 2"
              />
            )}
          </g>
        );
      })}
      {(() => {
        const visible = entities.filter((e) => !e.hidden);
        const here = visible.filter((e) => e.floor === floor);
        const lifts = layoutEntityLabels(
          here.map((e) => {
            const [cx, cy] = px(e.x, e.y);
            return { id: e.id, cx, cy, width: labelWidth(e.name) };
          }),
        );
        return visible.map((e) => {
          const [cx, cy] = px(e.x, e.y);
          const onFloor = e.floor === floor;
          const fill = TONE[e.tone] ?? TONE.unk;
          const lift = lifts.get(e.id) ?? -11;
          return (
            <g key={e.id} opacity={onFloor ? 1 : 0.42}>
              <circle
                cx={cx}
                cy={cy}
                r={onFloor ? 7 : 5}
                fill={onFloor ? fill : "none"}
                stroke={fill}
                strokeWidth={onFloor ? 1.5 : 1.2}
                strokeDasharray={onFloor ? undefined : "2 2"}
              />
              {onFloor ? (
                <>
                  <rect
                    x={cx + 9}
                    y={cy + lift - 8}
                    width={labelWidth(e.name)}
                    height={14}
                    rx={3}
                    fill="var(--color-bg)"
                    fillOpacity={0.85}
                  />
                  <text
                    x={cx + 13}
                    y={cy + lift + 3}
                    fill="var(--color-fg)"
                    fontSize={11}
                    fontFamily="var(--font-sans)"
                    fontWeight={500}
                  >
                    {e.name}
                  </text>
                </>
              ) : null}
            </g>
          );
        });
      })()}
    </svg>
  );
}

/** Approximate on-screen label pill width for a given name, capped like before. */
function labelWidth(name: string): number {
  return Math.min(86, name.length * 6.4 + 8);
}

/**
 * Greedy label placement: entities clustered together (e.g. several people
 * standing in the same small zone) no longer all default to the same lift
 * and render on top of each other. Each entity picks the first vertical
 * offset, from a small ladder of candidates, whose label pill doesn't
 * overlap a pill already placed for this floor.
 */
function layoutEntityLabels(
  points: { id: string; cx: number; cy: number; width: number }[],
): Map<string, number> {
  const LABEL_H = 14;
  const CANDIDATES = [-11, 7, -27, 23, -43, 39, -59, 55];
  const placed: { left: number; right: number; top: number; bottom: number }[] = [];
  const lifts = new Map<string, number>();

  // Deterministic order so re-renders don't jitter which entity "wins" a slot.
  const ordered = [...points].sort((a, b) => a.id.localeCompare(b.id));

  for (const p of ordered) {
    const left = p.cx + 9;
    const right = left + p.width;
    let chosen = CANDIDATES[0]!;
    for (const lift of CANDIDATES) {
      const top = p.cy + lift - 8;
      const bottom = top + LABEL_H;
      const overlaps = placed.some(
        (b) => left < b.right && right > b.left && top < b.bottom && bottom > b.top,
      );
      if (!overlaps) {
        chosen = lift;
        break;
      }
    }
    placed.push({ left, right, top: p.cy + chosen - 8, bottom: p.cy + chosen - 8 + LABEL_H });
    lifts.set(p.id, chosen);
  }

  return lifts;
}

export function FloorPills({
  floor,
  onChange,
}: {
  floor: number;
  onChange: (f: number) => void;
}) {
  return (
    <div className="flex gap-1 rounded-xl bg-bg p-1">
      {[1, 2, 3].map((f) => (
        <button
          key={f}
          type="button"
          onClick={() => onChange(f)}
          className={cn(
            "min-h-11 flex-1 rounded-lg px-3 text-sm font-medium transition-[background-color,color,transform] duration-150 ease-out active:scale-[0.96]",
            floor === f
              ? "bg-accent text-accent-fg"
              : "text-muted hover:text-fg",
          )}
        >
          Floor {f}
        </button>
      ))}
    </div>
  );
}
