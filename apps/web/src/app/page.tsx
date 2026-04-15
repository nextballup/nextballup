import Link from "next/link";

export default function LandingPage() {
  return (
    <main className="flex min-h-screen items-center justify-center p-8">
      <div className="max-w-xl text-center">
        <h1 className="mb-4 bg-gradient-to-br from-green-500 via-blue-500 to-purple-500 bg-clip-text text-5xl font-bold text-transparent">
          NextBallUp
        </h1>
        <p className="mb-8 text-lg text-[color:var(--color-nbu-text-muted)]">
          Hidden impact metrics beyond box scores.
        </p>
        <div className="flex justify-center gap-4">
          <Link
            href="/login"
            className="rounded-md bg-[color:var(--color-nbu-text)] px-6 py-3 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90"
          >
            Sign in
          </Link>
          <Link
            href="/register"
            className="rounded-md border border-[color:var(--color-nbu-border)] px-6 py-3 text-sm font-medium transition hover:border-[color:var(--color-nbu-text)]"
          >
            Create account
          </Link>
        </div>
      </div>
    </main>
  );
}
