import { cookies } from "next/headers";
import Link from "next/link";
import { redirect } from "next/navigation";
import { serverApiJson } from "@/lib/api-server";
import { ApiError } from "@/lib/errors";
import { ACTIVE_TEAM_COOKIE, resolveActiveTeamId } from "@/lib/active-team";
import type { TeamListResponse } from "@/lib/contract";
import { CreateGameForm } from "./create-game-form";

export default async function NewGamePage() {
  let teamList: TeamListResponse;
  try {
    teamList = await serverApiJson<TeamListResponse>("/teams");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      redirect("/login");
    }
    throw error;
  }
  const coachTeams = teamList.teams.filter((t) =>
    t.my_team_role === "head_coach" || t.my_team_role === "assistant_coach",
  );
  const cookieValue = (await cookies()).get(ACTIVE_TEAM_COOKIE)?.value;
  const defaultTeamId = resolveActiveTeamId(cookieValue, coachTeams);

  return (
    <section className="space-y-6">
      <nav aria-label="Breadcrumb" className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
        <Link href="/games" className="hover:underline">
          ← All games
        </Link>
      </nav>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Create game</h1>
        <p className="mt-1 text-sm text-[color:var(--color-nbu-text-muted)]">
          Only coaches can create games. You can upload film from the game
          page after it is created.
        </p>
      </div>
      {coachTeams.length === 0 ? (
        <div className="rounded-md border border-dashed border-[color:var(--color-nbu-border)] px-4 py-8 text-center text-sm text-[color:var(--color-nbu-text-muted)]">
          <p>You need coach access on a team to create games.</p>
          <p className="mt-2">
            <Link href="/teams/new" className="font-medium underline">
              Create a team
            </Link>{" "}
            or{" "}
            <Link href="/teams/join" className="font-medium underline">
              join one with a coach invite
            </Link>
            .
          </p>
        </div>
      ) : (
        <CreateGameForm teams={coachTeams} defaultTeamId={defaultTeamId} />
      )}
    </section>
  );
}
