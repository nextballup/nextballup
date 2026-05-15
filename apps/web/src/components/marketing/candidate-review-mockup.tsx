/**
 * A static, code-native preview of the candidate review panel that ships
 * in the gated product. No real videos, signed URLs, athlete identities,
 * model lineage strings, or storage keys — every value here is hard-coded
 * placeholder content. Render this on the marketing page so visitors get
 * an honest, inspectable preview without us exposing alpha data.
 */
type Status = "needs_review" | "approved" | "rejected";

type MockEvent = {
  id: string;
  label: string;
  timestamp: string;
  status: Status;
  source: "Alpha model" | "Manual tag";
};

const MOCK_EVENTS: ReadonlyArray<MockEvent> = [
  {
    id: "m1",
    label: "Shot attempt",
    timestamp: "0:42",
    status: "needs_review",
    source: "Alpha model",
  },
  {
    id: "m2",
    label: "Rebound",
    timestamp: "1:08",
    status: "approved",
    source: "Alpha model",
  },
  {
    id: "m3",
    label: "Made shot",
    timestamp: "1:24",
    status: "needs_review",
    source: "Alpha model",
  },
  {
    id: "m4",
    label: "Pass",
    timestamp: "1:53",
    status: "rejected",
    source: "Manual tag",
  },
  {
    id: "m5",
    label: "Shot attempt",
    timestamp: "2:11",
    status: "needs_review",
    source: "Alpha model",
  },
];

const STATUS_LABEL: Record<Status, string> = {
  needs_review: "Needs review",
  approved: "Approved",
  rejected: "Rejected",
};

export function CandidateReviewMockup() {
  return (
    <div
      aria-hidden="true"
      className="grid gap-4 rounded-lg border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] p-4 sm:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]"
    >
      <div className="space-y-2">
        <div className="aspect-video w-full rounded-md border border-[color:var(--color-nbu-border)] bg-black">
          <div className="flex h-full items-center justify-center text-xs text-[color:var(--color-nbu-text-muted)]">
            Game film preview
          </div>
        </div>
        <div className="flex items-center justify-between text-[10px] uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
          <span>Ready for playback</span>
          <span className="font-mono">mp4 · 10 min · 124 MB</span>
        </div>
      </div>
      <div className="space-y-3 rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-bg)] p-3 text-xs">
        <div className="flex items-baseline justify-between">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Alpha candidates
          </h3>
          <span className="font-mono text-[10px] text-[color:var(--color-nbu-text-muted)]">
            5 of 5
          </span>
        </div>
        <p className="text-[10px] text-[color:var(--color-nbu-text-muted)]">
          Review only. Not production analytics.
        </p>
        <div className="flex flex-wrap gap-1.5">
          {[
            { label: "Needs review", active: true },
            { label: "Approved", active: false },
            { label: "Rejected", active: false },
            { label: "All", active: false },
          ].map((chip) => (
            <span
              key={chip.label}
              className={
                chip.active
                  ? "rounded-md border border-[color:var(--color-nbu-text)] px-1.5 py-0.5 text-[10px] font-medium"
                  : "rounded-md border border-[color:var(--color-nbu-border)] px-1.5 py-0.5 text-[10px] text-[color:var(--color-nbu-text-muted)]"
              }
            >
              {chip.label}
            </span>
          ))}
        </div>
        <ul className="divide-y divide-[color:var(--color-nbu-border)] rounded-md border border-[color:var(--color-nbu-border)]">
          {MOCK_EVENTS.map((event) => (
            <li
              key={event.id}
              className="flex flex-wrap items-center gap-2 px-2 py-1.5"
            >
              <span className="font-medium">{event.label}</span>
              <span className="font-mono text-[10px] text-[color:var(--color-nbu-text-muted)]">
                {event.timestamp}
              </span>
              <span className="rounded-md border border-[color:var(--color-nbu-border)] px-1 py-0.5 font-mono text-[9px] uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
                {STATUS_LABEL[event.status]}
              </span>
              <span className="rounded-md border border-dashed border-[color:var(--color-nbu-border)] px-1 py-0.5 font-mono text-[9px] uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
                {event.source}
              </span>
              <span className="ml-auto flex gap-1">
                <span className="rounded-md border border-[color:var(--color-nbu-border)] px-1.5 py-0.5 text-[10px]">
                  Jump
                </span>
                <span className="rounded-md border border-[color:var(--color-nbu-border)] px-1.5 py-0.5 text-[10px]">
                  Approve
                </span>
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
