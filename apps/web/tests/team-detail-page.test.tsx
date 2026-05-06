import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import type { TeamDetailResponse } from "@/lib/contract";
import TeamDetailPage from "@/app/(app)/teams/[teamId]/page";
import { server } from "./setup";

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
    server.use(
      http.get("/api/v1/teams/team-1/privacy-consents", () =>
        HttpResponse.json({ consents: [], total: 0 }),
      ),
    );
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
    expect(
      await screen.findByText(/privacy consent evidence/i),
    ).toBeInTheDocument();
  });

  it("renders an empty roster state with recovery copy", async () => {
    serverApiOptional.mockResolvedValue(
      teamDetail({
        member_count: 0,
        members: [],
      }),
    );

    render(
      await TeamDetailPage({ params: Promise.resolve({ teamId: "team-1" }) }),
    );

    expect(screen.getByText(/no roster members are visible yet/i)).toBeInTheDocument();
    expect(screen.getByText(/share the team invite code/i)).toBeInTheDocument();
  });
});
