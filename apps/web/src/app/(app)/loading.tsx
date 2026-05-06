export default function AuthenticatedLoading() {
  return (
    <section className="space-y-6" role="status" aria-live="polite">
      <div className="space-y-2">
        <div className="h-7 w-40 animate-pulse rounded bg-[color:var(--color-nbu-surface)]" />
        <div className="h-4 w-64 animate-pulse rounded bg-[color:var(--color-nbu-surface)]" />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {[0, 1, 2, 3].map((item) => (
          <div
            key={item}
            className="space-y-3 rounded-lg border border-[color:var(--color-nbu-border)] p-4"
          >
            <div className="h-5 w-2/3 animate-pulse rounded bg-[color:var(--color-nbu-surface)]" />
            <div className="h-4 w-1/2 animate-pulse rounded bg-[color:var(--color-nbu-surface)]" />
            <div className="h-4 w-1/3 animate-pulse rounded bg-[color:var(--color-nbu-surface)]" />
          </div>
        ))}
      </div>
      <span className="sr-only">Loading</span>
    </section>
  );
}
