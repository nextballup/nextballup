import Image from "next/image";
import Link from "next/link";

export default function LandingPage() {
  return (
    <main className="flex min-h-screen items-center justify-center p-8">
      <div className="max-w-xl text-center">
        <div className="mb-6 flex justify-center">
          <div className="relative h-28 w-28">
            <Image
              src="/brand/logo-color-transparent.png"
              alt="NextBallUp logo"
              fill
              priority
              sizes="112px"
              className="object-contain"
            />
          </div>
        </div>
        <h1 className="mb-4 text-5xl font-bold tracking-tight text-[color:var(--color-nbu-text)]">
          NextBallUp
        </h1>
        <p className="mb-8 text-lg text-[color:var(--color-nbu-text-muted)]">
          Upload, archive, and review game film with your team.
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
