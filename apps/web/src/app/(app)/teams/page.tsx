import Link from "next/link";
import { redirect } from "next/navigation";
import { serverApiJson } from "@/lib/api-server";
import { ApiError } from "@/lib/errors";
import type { TeamListResponse } from "@/lib/contract";

const TEAM_ROLE_LABELS: Record<string, string> = {
  head_coach: "Head coach",
  assistant_coach: "Assistant coach",
  manager: "Manager",
  player: "Player",
  captain: "Captain",
};

export default async function TeamsPage() {
  let list: TeamListResponse;
  try {
    list = await serverApiJson<TeamListResponse>("/teams");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      redirect("/login");
    }
    throw error;
  }

  return (
    <section className="space-y-6">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Teams</h1>
          <p className="text-sm text-[color:var(--color-nbu-text-muted)]">
            {list.teams.length === 0
              ? "You aren't on any teams yet."
              : `${list.teams.length} team${list.teams.length > 1 ? "s" : ""}`}
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-sm">
          <Link
            href="/teams/new"
            className="rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2 font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90"
          >
            + Create team
          </Link>
          <Link
            href="/teams/join"
            className="rounded-md border border-[color:var(--color-nbu-border)] px-4 py-2 font-medium transition hover:border-[color:var(--color-nbu-text)]"
          >
            Join with invite code
          </Link>
        </div>
      </header>

      {list.teams.length === 0 ? (
        <div className="space-y-3 rounded-md border border-dashed border-[color:var(--color-nbu-border)] px-4 py-8 text-center text-sm text-[color:var(--color-nbu-text-muted)]">
          <p>
            Coaches usually start by creating a team. Players wait for a coach
            to share an invite code, then join here.
          </p>
          <p>
            <Link href="/teams/new" className="font-medium underline">
              Create team
            </Link>{" "}
            or{" "}
            <Link href="/teams/join" className="font-medium underline">
              join with invite code
            </Link>
            .
          </p>
        </div>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {list.teams.map((team) => (
            <li key={team.id}>
              <Link
                href={`/teams/${team.id}`}
                className="flex flex-col gap-2 rounded-lg border border-[color:var(--color-nbu-border)] p-4 transition hover:border-[color:var(--color-nbu-text)]"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="text-sm font-medium">{team.name}</span>
                  <span className="rounded-full border border-[color:var(--color-nbu-border)] px-2 py-0.5 text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
                    {TEAM_ROLE_LABELS[team.my_team_role] ?? team.my_team_role}
                  </span>
                </div>
                <div className="flex items-center justify-between text-xs text-[color:var(--color-nbu-text-muted)]">
                  <span>
                    {team.sport} · {team.level.replaceAll("_", " ")}
                  </span>
                  <span>{team.season}</span>
                </div>
                <div className="flex items-center gap-3 text-xs text-[color:var(--color-nbu-text-muted)]">
                  <span>
                    {team.member_count} member
                    {team.member_count === 1 ? "" : "s"}
                  </span>
                  <span>·</span>
                  <span>
                    {team.game_count} game
                    {team.game_count === 1 ? "" : "s"}
                  </span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
