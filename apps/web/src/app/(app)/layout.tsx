import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { serverApiOptional } from "@/lib/api-server";
import { ACTIVE_TEAM_COOKIE, resolveActiveTeamId } from "@/lib/active-team";
import type { TeamListResponse, UserPublic } from "@/lib/contract";
import { AppShell } from "./app-shell";

export default async function AppLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // Session bootstrap: if `/auth/me` can't validate the httpOnly cookie, send
  // the user to /login before they ever see the authenticated UI. This also
  // means we never render partial server components that would downstream
  // call the API and get 401s.
  const user = await serverApiOptional<UserPublic>("/auth/me");
  if (!user) {
    redirect("/login");
  }

  // Team list is tenant-aware on the backend and cheap (1 row/team). Loading
  // it here lets every authenticated page share one picker state without
  // re-fetching per route.
  const teamList = await serverApiOptional<TeamListResponse>("/teams");
  const teams = teamList?.teams ?? [];
  const cookieValue = (await cookies()).get(ACTIVE_TEAM_COOKIE)?.value;
  const activeTeamId = resolveActiveTeamId(cookieValue, teams);

  return (
    <AppShell user={user} teams={teams} activeTeamId={activeTeamId}>
      {children}
    </AppShell>
  );
}
