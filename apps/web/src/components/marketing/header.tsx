import Image from "next/image";
import Link from "next/link";
import { ThemeToggle } from "@/components/theme-toggle";

/**
 * The "product" link on the public marketing site points at the gated
 * pilot environment, not the same-origin app routes — the marketing
 * deployment must never serve the product auth surface, so cookies stay
 * scoped to beta/alpha hosts. Override via NEXT_PUBLIC_PRODUCT_BASE_URL
 * if a non-default product host is in use.
 */
function productBaseUrl(): string {
  const explicit = process.env.NEXT_PUBLIC_PRODUCT_BASE_URL?.trim();
  if (explicit) return explicit.replace(/\/+$/, "");
  return "https://beta.nextballup.com";
}

const NAV_LINKS: ReadonlyArray<{ href: string; label: string }> = [
  { href: "#workflow", label: "Workflow" },
  { href: "#use-cases", label: "Use cases" },
  { href: "#security", label: "Security" },
  { href: "#faq", label: "FAQ" },
];

export function MarketingHeader() {
  const productBase = productBaseUrl();
  return (
    <header className="sticky top-0 z-30 border-b border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-bg)]/90 backdrop-blur">
      <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
        <Link
          href="/"
          className="flex items-center gap-2 rounded-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--color-nbu-text)]"
          aria-label="NextBallUp home"
        >
          <span className="relative inline-block h-7 w-7">
            <Image
              src="/brand/logo-color-transparent.png"
              alt=""
              fill
              priority
              sizes="28px"
              className="object-contain"
            />
          </span>
          <span className="text-sm font-semibold tracking-tight">NextBallUp</span>
        </Link>
        <nav
          aria-label="Primary"
          className="hidden items-center gap-1 text-sm md:flex"
        >
          {NAV_LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="rounded-md px-2 py-1 text-[color:var(--color-nbu-text-muted)] transition hover:text-[color:var(--color-nbu-text)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--color-nbu-text)]"
            >
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <Link
            href={`${productBase}/login`}
            className="hidden rounded-md border border-[color:var(--color-nbu-border)] px-3 py-1.5 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)] sm:inline-flex"
            data-testid="header-signin"
          >
            Sign in
          </Link>
          <Link
            href="/pilot"
            className="rounded-md bg-[color:var(--color-nbu-text)] px-3 py-1.5 text-xs font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--color-nbu-text)]"
            data-testid="header-pilot-cta"
          >
            Request pilot access
          </Link>
        </div>
      </div>
    </header>
  );
}
