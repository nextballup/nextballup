import Link from "next/link";
import { notFound } from "next/navigation";
import { serverApiOptional } from "@/lib/api-server";
import type { TeamDetailResponse } from "@/lib/contract";
import { InvitePanel } from "./invite-panel";

const TEAM_ROLE_LABELS: Record<string, string> = {
  head_coach: "Head coach",
  assistant_coach: "Assistant coach",
  manager: "Manager",
  player: "Player",
  captain: "Captain",
};

const COACH_SEAT = new Set(["head_coach", "assistant_coach"]);

export default async function TeamDetailPage({
  params,
}: {
  params: Promise<{ teamId: string }>;
}) {
  const { teamId } = await params;
  const team = await serverApiOptional<TeamDetailResponse>(`/teams/${teamId}`, {
    nullOnStatuses: [403, 404],
  });
  if (!team) {
    notFound();
  }

  const canManageInvites = COACH_SEAT.has(team.my_team_role);

  return (
    <section className="space-y-6">
      <nav aria-label="Breadcrumb" className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
        <Link href="/teams" className="hover:underline">
          ← All teams
        </Link>
      </nav>

      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{team.name}</h1>
          <p className="text-sm text-[color:var(--color-nbu-text-muted)]">
            {team.sport} · {team.level.replaceAll("_", " ")} · {team.season}
            {team.institution ? ` · ${team.institution}` : ""}
          </p>
        </div>
        <div className="flex gap-2 text-sm">
          <Link
            href="/games/new"
            className="rounded-md border border-[color:var(--color-nbu-border)] px-4 py-2 font-medium transition hover:border-[color:var(--color-nbu-text)]"
          >
            + Create game
          </Link>
        </div>
      </header>

      {canManageInvites && team.invite_code ? (
        <InvitePanel teamId={team.id} defaultInviteCode={team.invite_code} />
      ) : (
        <section className="rounded-lg border border-[color:var(--color-nbu-border)] p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Team invite
          </h2>
          <p className="mt-1 text-sm text-[color:var(--color-nbu-text-muted)]">
            Coaches manage invite codes for this team. Ask a coach if someone
            needs access.
          </p>
        </section>
      )}

      <section aria-labelledby="roster-heading" className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 id="roster-heading" className="text-sm font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Roster · {team.member_count}
          </h2>
        </div>
        <ul className="divide-y divide-[color:var(--color-nbu-border)] rounded-lg border border-[color:var(--color-nbu-border)]">
          {team.members.map((member) => (
            <li
              key={member.user_id}
              className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm"
            >
              <div>
                <div className="font-medium">{member.full_name}</div>
                <div className="text-xs text-[color:var(--color-nbu-text-muted)]">
                  {TEAM_ROLE_LABELS[member.team_role] ?? member.team_role}
                  {COACH_SEAT.has(member.team_role) ? " · coach" : ""}
                </div>
              </div>
              <div className="text-xs font-mono text-[color:var(--color-nbu-text-muted)]">
                {member.jersey_number != null ? `#${member.jersey_number}` : ""}
              </div>
            </li>
          ))}
        </ul>
      </section>
    </section>
  );
}
