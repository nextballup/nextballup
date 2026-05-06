export default function RootLoading() {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-5xl items-center px-4">
      <div className="w-full space-y-4" role="status" aria-live="polite">
        <div className="h-8 w-40 animate-pulse rounded-md bg-[color:var(--color-nbu-surface)]" />
        <div className="space-y-3 rounded-lg border border-[color:var(--color-nbu-border)] p-4">
          <div className="h-5 w-1/3 animate-pulse rounded bg-[color:var(--color-nbu-surface)]" />
          <div className="h-4 w-2/3 animate-pulse rounded bg-[color:var(--color-nbu-surface)]" />
          <div className="h-24 animate-pulse rounded bg-[color:var(--color-nbu-surface)]" />
        </div>
        <span className="sr-only">Loading</span>
      </div>
    </main>
  );
}
