import Link from "next/link";
import { BrandLink } from "@/components/brand-link";

export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-5xl items-center px-4">
      <section className="space-y-5 rounded-lg border border-[color:var(--color-nbu-border)] p-6 text-sm">
        <BrandLink href="/" size="md" priority />
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">Page not found</h1>
          <p className="max-w-xl text-[color:var(--color-nbu-text-muted)]">
            This page may have moved, expired, or become unavailable to your
            account.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Link
            href="/games"
            className="rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2 font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90"
          >
            Go to games
          </Link>
          <Link
            href="/teams"
            className="rounded-md border border-[color:var(--color-nbu-border)] px-4 py-2 font-medium transition hover:border-[color:var(--color-nbu-text)]"
          >
            Go to teams
          </Link>
        </div>
      </section>
    </main>
  );
}
