import type { Snapshot } from "@/lib/vms/simulation";
import { cn } from "@/lib/utils";
import { Nfc, Radio } from "lucide-react";

const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  in_range: "In BLE range",
  verified: "Verified",
  missed: "Missed",
  sequence_break: "Out of sequence",
  nfc_required: "NFC tap required",
};

export function PatrolBoard({ data }: { data: Snapshot["patrol"] }) {
  const done = data.checkpoints.filter((c) => c.status === "verified").length;
  return (
    <section className="rounded-2xl bg-surface p-3 shadow-[var(--shadow-border)] sm:p-4">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <div>
          <h2 className="text-sm font-medium tracking-tight">{data.routeName}</h2>
          <p className="text-xs text-subtle">
            {data.guardName}
            {data.suppressNfc ? " · NFC suppressed this loop" : ""}
          </p>
        </div>
        <p className="font-mono text-sm tabular-nums text-muted">
          {done}/{data.checkpoints.length}
        </p>
      </div>
      <ol className="space-y-1">
        {data.checkpoints.map((c) => (
          <li
            key={c.id}
            className={cn(
              "flex items-center gap-3 rounded-lg px-2 py-2",
              c.status === "verified" && "bg-ok-dim",
              c.status === "missed" && "bg-crit-dim",
              c.status === "nfc_required" && "bg-warn-dim",
              c.status === "sequence_break" && "bg-alert-dim",
            )}
          >
            <span className="w-5 font-mono text-xs text-subtle">{c.sequence + 1}</span>
            {c.modality === "nfc" ? (
              <Nfc className="size-4 shrink-0 text-fg" strokeWidth={1.75} />
            ) : (
              <Radio className="size-4 shrink-0 text-muted" strokeWidth={1.75} />
            )}
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm text-fg">{c.name}</p>
              <p className="truncate font-mono text-xs uppercase tracking-wider text-subtle">
                {c.modality} · {STATUS_LABEL[c.status] ?? c.status}
                {c.note ? ` · ${c.note}` : ""}
              </p>
            </div>
            {c.status === "verified" ? (
              <span className="size-1.5 shrink-0 rounded-full bg-ok" />
            ) : c.status === "missed" ? (
              <span className="size-1.5 shrink-0 rounded-full bg-crit" />
            ) : c.status === "nfc_required" ? (
              <span className="size-1.5 shrink-0 rounded-full bg-warn" />
            ) : (
              <span className="size-1.5 shrink-0 rounded-full bg-border-strong" />
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
