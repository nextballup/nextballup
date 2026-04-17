"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { BrandLink } from "@/components/brand-link";
import { TeamPicker } from "@/components/team-picker";
import { apiVoid } from "@/lib/api-client";
import type { TeamListEntry, UserPublic } from "@/lib/contract";

const BASE_NAV_ITEMS: Array<{ href: string; label: string }> = [
  { href: "/games", label: "Games" },
  { href: "/teams", label: "Teams" },
];
const ADMIN_NAV_ITEM = { href: "/admin/audit", label: "Audit" };

export function AppShell({
  user,
  teams,
  activeTeamId,
  children,
}: {
  user: UserPublic;
  teams: TeamListEntry[];
  activeTeamId: string | null;
  children: React.ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [loggingOut, setLoggingOut] = useState(false);
  const navItems =
    user.role === "admin" ? [...BASE_NAV_ITEMS, ADMIN_NAV_ITEM] : BASE_NAV_ITEMS;

  async function handleLogout() {
    setLoggingOut(true);
    try {
      await apiVoid("/auth/logout", {
        method: "POST",
        noRefreshOn401: true,
      });
    } finally {
      // Whether the API call succeeds or fails (e.g. token already expired),
      // we hard-navigate to /login so the server layout re-bootstraps the
      // session and clears any cached user state.
      router.replace("/login");
    }
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-[color:var(--color-nbu-border)]">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex flex-wrap items-center gap-4">
            <BrandLink href="/games" size="sm" />
            <nav aria-label="Primary" className="hidden gap-4 sm:flex">
              {navItems.map((item) => {
                const active =
                  pathname === item.href || pathname.startsWith(`${item.href}/`);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`text-sm transition ${
                      active
                        ? "font-semibold"
                        : "text-[color:var(--color-nbu-text-muted)] hover:text-[color:var(--color-nbu-text)]"
                    }`}
                  >
                    {item.label}
                  </Link>
                );
              })}
            </nav>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <TeamPicker teams={teams} activeTeamId={activeTeamId} />
            <div className="hidden text-right text-xs sm:block">
              <div className="font-medium">{user.full_name}</div>
              <div className="text-[color:var(--color-nbu-text-muted)]">
                {user.role}
                {teams.length > 0 ? ` · ${teams.length} team${teams.length > 1 ? "s" : ""}` : ""}
              </div>
            </div>
            <button
              type="button"
              onClick={handleLogout}
              disabled={loggingOut}
              className="rounded-md border border-[color:var(--color-nbu-border)] px-3 py-1.5 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)] disabled:opacity-50"
            >
              {loggingOut ? "Signing out…" : "Sign out"}
            </button>
          </div>
        </div>
        <nav aria-label="Primary-mobile" className="flex gap-4 border-t border-[color:var(--color-nbu-border)] px-4 py-2 text-sm sm:hidden">
          {navItems.map((item) => {
            const active =
              pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`transition ${
                  active
                    ? "font-semibold"
                    : "text-[color:var(--color-nbu-text-muted)] hover:text-[color:var(--color-nbu-text)]"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </header>
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6">{children}</main>
    </div>
  );
}
