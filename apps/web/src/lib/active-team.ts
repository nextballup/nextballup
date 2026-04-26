/**
 * Active-team selection is UX state, not an auth boundary. The backend still
 * checks team membership on every request; this cookie just tracks which
 * team the user is currently viewing so a coach with multiple teams lands on
 * the right one after a refresh.
 *
 * Not httpOnly on purpose: the value is not a credential. The backend never
 * trusts this cookie for authz — it is strictly a client-side pointer.
 */
export const ACTIVE_TEAM_COOKIE = "nbu_active_team";

export function resolveActiveTeamId(
  cookieValue: string | undefined,
  teams: ReadonlyArray<{ id: string }>,
): string | null {
  if (teams.length === 0) return null;
  if (cookieValue && teams.some((t) => t.id === cookieValue)) {
    return cookieValue;
  }
  return teams[0].id;
}
