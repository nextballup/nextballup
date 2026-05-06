import Image from "next/image";
import Link from "next/link";

// Public root copy is channel-aware. The marketing/waitlist surface
// (`nextballup.com`) sets NEXT_PUBLIC_REGISTRATION_MODE=disabled so the page
// does not expose an open signup CTA. Alpha/beta build environments set the
// matching mode and the CTA is hidden or routed appropriately. See
// docs/soc2/DEPLOYMENT_CHANNELS.md.
type PublicRegistrationMode = "open" | "invite_only" | "allowlist" | "disabled";

function publicRegistrationMode(): PublicRegistrationMode {
  const raw = process.env.NEXT_PUBLIC_REGISTRATION_MODE;
  if (raw === "open" || raw === "invite_only" || raw === "allowlist" || raw === "disabled") {
    return raw;
  }
  return "disabled";
}

export default function LandingPage() {
  const mode = publicRegistrationMode();
  const showCreateAccount = mode === "open";
  const showInviteCta = mode === "invite_only";
  const showPilotCta = mode === "allowlist";
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
          {showCreateAccount && (
            <Link
              href="/register"
              className="rounded-md border border-[color:var(--color-nbu-border)] px-6 py-3 text-sm font-medium transition hover:border-[color:var(--color-nbu-text)]"
            >
              Create account
            </Link>
          )}
          {showInviteCta && (
            <Link
              href="/register"
              className="rounded-md border border-[color:var(--color-nbu-border)] px-6 py-3 text-sm font-medium transition hover:border-[color:var(--color-nbu-text)]"
            >
              Have an invite?
            </Link>
          )}
          {showPilotCta && (
            <Link
              href="/register"
              className="rounded-md border border-[color:var(--color-nbu-border)] px-6 py-3 text-sm font-medium transition hover:border-[color:var(--color-nbu-text)]"
            >
              Pilot access
            </Link>
          )}
        </div>
        {mode === "disabled" && (
          <p className="mt-6 text-sm text-[color:var(--color-nbu-text-muted)]">
            Public access is not yet open. Please check back later.
          </p>
        )}
      </div>
    </main>
  );
}
