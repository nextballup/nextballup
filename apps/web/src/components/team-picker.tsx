"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { ACTIVE_TEAM_COOKIE } from "@/lib/active-team";
import type { TeamListEntry } from "@/lib/contract";

function writeActiveTeamCookie(teamId: string) {
  document.cookie = `${ACTIVE_TEAM_COOKIE}=${encodeURIComponent(teamId)}; Path=/; Max-Age=${60 * 60 * 24 * 30}; SameSite=Lax`;
}

/**
 * Lightweight team picker for the top nav. The selection lives in a
 * non-httpOnly cookie because it is UX state, not an auth boundary — the
 * backend still verifies team membership on every request. Writing a
 * pure-client cookie (rather than round-tripping through the API) keeps the
 * switch instant.
 */
export function TeamPicker({
  teams,
  activeTeamId,
}: {
  teams: TeamListEntry[];
  activeTeamId: string | null;
}) {
  const router = useRouter();
  const resolvedTeamId = activeTeamId ?? teams[0]?.id ?? null;

  useEffect(() => {
    if (resolvedTeamId) {
      writeActiveTeamCookie(resolvedTeamId);
    }
  }, [resolvedTeamId]);

  if (teams.length === 0) {
    return (
      <Link
        href="/teams/new"
        className="rounded-full border border-dashed border-[color:var(--color-nbu-border)] px-3 py-1 text-xs text-[color:var(--color-nbu-text-muted)] transition hover:border-[color:var(--color-nbu-text)]"
      >
        + Create a team
      </Link>
    );
  }

  if (teams.length === 1) {
    const [only] = teams;
    return (
      <Link
        href={`/teams/${only.id}`}
        className="hidden items-center gap-2 rounded-full border border-[color:var(--color-nbu-border)] px-3 py-1 text-xs transition hover:border-[color:var(--color-nbu-text)] md:inline-flex"
        title={`${only.name} · ${only.season}`}
      >
        <span className="font-medium">{only.name}</span>
        <span className="text-[color:var(--color-nbu-text-muted)]">{only.season}</span>
      </Link>
    );
  }

  function handleChange(event: React.ChangeEvent<HTMLSelectElement>) {
    const value = event.target.value;
    writeActiveTeamCookie(value);
    router.refresh();
  }

  return (
    <label className="flex items-center gap-2 text-xs">
      <span className="sr-only">Active team</span>
      <select
        aria-label="Active team"
        value={resolvedTeamId ?? teams[0].id}
        onChange={handleChange}
        className="rounded-full border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-1 text-xs transition hover:border-[color:var(--color-nbu-text)] focus:border-[color:var(--color-nbu-text)] focus:outline-none"
      >
        {teams.map((team) => (
          <option key={team.id} value={team.id}>
            {team.name} · {team.season}
          </option>
        ))}
      </select>
    </label>
  );
}
