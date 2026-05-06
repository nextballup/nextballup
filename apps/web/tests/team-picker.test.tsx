import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TeamPicker } from "@/components/team-picker";
import type { TeamListEntry } from "@/lib/contract";
import { ACTIVE_TEAM_COOKIE } from "@/lib/active-team";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

function team(overrides: Partial<TeamListEntry> = {}): TeamListEntry {
  return {
    id: "t1",
    name: "Team One",
    sport: "basketball",
    level: "high_school",
    institution: null,
    institution_type: "k12_school",
    season: "2026-2027",
    invite_code: "ABC-123",
    my_team_role: "head_coach",
    member_count: 1,
    game_count: 0,
    ...overrides,
  };
}

function clearCookies() {
  for (const cookie of document.cookie.split(";")) {
    const [name] = cookie.split("=");
    document.cookie = `${name.trim()}=; Path=/; Max-Age=0`;
  }
}

describe("TeamPicker", () => {
  beforeEach(() => {
    refresh.mockReset();
    clearCookies();
  });
  afterEach(() => {
    clearCookies();
  });

  it("shows a create-team CTA when the user has no teams", () => {
    render(<TeamPicker teams={[]} activeTeamId={null} />);
    const link = screen.getByRole("link", { name: /create a team/i });
    expect(link.getAttribute("href")).toBe("/teams/new");
  });

  it("renders a select when the user has multiple teams and writes a cookie on change", async () => {
    const teams = [
      team({ id: "a", name: "Alpha", season: "2025-2026" }),
      team({ id: "b", name: "Beta", season: "2026-2027" }),
    ];
    render(<TeamPicker teams={teams} activeTeamId="a" />);
    const select = screen.getByLabelText(/active team/i) as HTMLSelectElement;
    expect(select.value).toBe("a");
    await userEvent.selectOptions(select, "b");
    expect(document.cookie).toContain(`${ACTIVE_TEAM_COOKIE}=b`);
    expect(refresh).toHaveBeenCalled();
  });

  it("renders the single team as a compact link when there is only one", () => {
    const only = team({ id: "solo", name: "Solo" });
    render(<TeamPicker teams={[only]} activeTeamId="solo" />);
    expect(
      screen.getByRole("link", { name: /solo/i }).getAttribute("href"),
    ).toBe("/teams/solo");
  });

  it("repairs a stale active-team cookie to the resolved active team", async () => {
    document.cookie = `${ACTIVE_TEAM_COOKIE}=deleted-team; Path=/; Max-Age=60`;
    render(<TeamPicker teams={[team({ id: "current" })]} activeTeamId="current" />);

    await waitFor(() => {
      expect(document.cookie).toContain(`${ACTIVE_TEAM_COOKIE}=current`);
    });
  });
});
