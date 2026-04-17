import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TeamDetailResponse } from "@/lib/contract";
import TeamDetailPage from "@/app/(app)/teams/[teamId]/page";

const { serverApiOptional } = vi.hoisted(() => ({
  serverApiOptional: vi.fn(),
}));

vi.mock("@/lib/api-server", () => ({
  serverApiOptional,
}));

vi.mock("next/navigation", () => ({
  notFound: vi.fn(() => {
    throw new Error("NOT_FOUND");
  }),
}));

function teamDetail(
  overrides: Partial<TeamDetailResponse> = {},
): TeamDetailResponse {
  return {
    id: "team-1",
    name: "Lincoln Varsity Boys",
    sport: "basketball",
    level: "high_school",
    institution: "Lincoln High",
    institution_type: "k12_school",
    season: "2026-2027",
    invite_code: "TEAM-CODE",
    my_team_role: "head_coach",
    member_count: 1,
    members: [
      {
        user_id: "user-1",
        full_name: "Coach Carter",
        role: "coach",
        team_role: "head_coach",
        jersey_number: null,
        joined_at: new Date().toISOString(),
      },
    ],
    ...overrides,
  };
}

describe("TeamDetailPage", () => {
  beforeEach(() => {
    serverApiOptional.mockReset();
  });

  it("hides invite controls for non-coach members", async () => {
    serverApiOptional.mockResolvedValue(
      teamDetail({
        invite_code: null,
        my_team_role: "player",
      }),
    );

    render(
      await TeamDetailPage({ params: Promise.resolve({ teamId: "team-1" }) }),
    );

    expect(
      screen.getByText(/Coaches manage invite codes for this team/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("default-invite-code")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /generate invite/i }),
    ).not.toBeInTheDocument();
  });

  it("shows invite controls for coach seats", async () => {
    serverApiOptional.mockResolvedValue(teamDetail());

    render(
      await TeamDetailPage({ params: Promise.resolve({ teamId: "team-1" }) }),
    );

    expect(screen.getByTestId("default-invite-code")).toHaveTextContent(
      "TEAM-CODE",
    );
    expect(
      screen.getByRole("button", { name: /generate invite/i }),
    ).toBeInTheDocument();
  });
});
