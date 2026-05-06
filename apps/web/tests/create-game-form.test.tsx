import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { CreateGameForm } from "@/app/(app)/games/new/create-game-form";
import type { TeamListEntry } from "@/lib/contract";
import { server } from "./setup";

const replace = vi.fn();
const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh }),
}));

function coachTeam(overrides: Partial<TeamListEntry> = {}): TeamListEntry {
  return {
    id: "team-1",
    name: "Lincoln",
    sport: "basketball",
    level: "high_school",
    institution: null,
    institution_type: "k12_school",
    season: "2026-2027",
    invite_code: "LINC",
    my_team_role: "head_coach",
    member_count: 2,
    game_count: 0,
    ...overrides,
  };
}

describe("CreateGameForm", () => {
  it("POSTs /games with the selected team and fields, then redirects", async () => {
    replace.mockReset();
    refresh.mockReset();
    let captured: unknown = null;
    server.use(
      http.post("/api/v1/games", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(
          {
            id: "new-game",
            team_id: "team-1",
            opponent_name: "Jefferson",
            game_type: "regular_season",
            date: "2026-11-15",
            time: null,
            location: "Home gym",
            is_home: true,
            status: "scheduled",
            score_team: null,
            score_opponent: null,
            notes: null,
            periods: 4,
            period_length_minutes: 8,
            shot_clock_enabled: false,
            shot_clock_seconds: null,
            created_at: "2026-04-15T00:00:00Z",
          },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    render(
      <CreateGameForm
        teams={[coachTeam({ id: "team-1" })]}
        defaultTeamId="team-1"
      />,
    );
    await user.type(screen.getByLabelText(/Opponent/i), "Jefferson");
    await user.type(screen.getByLabelText(/Location/i), "Home gym");
    await user.click(screen.getByRole("button", { name: /create game/i }));
    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toMatchObject({
      team_id: "team-1",
      opponent_name: "Jefferson",
      location: "Home gym",
      is_home: true,
      shot_clock_enabled: false,
      shot_clock_seconds: null,
    });
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/games/new-game"),
    );
    expect(refresh).toHaveBeenCalled();
  });

  it("surfaces a coach-only error when the backend rejects", async () => {
    replace.mockReset();
    server.use(
      http.post("/api/v1/games", () =>
        HttpResponse.json(
          {
            error: {
              code: "FORBIDDEN",
              message: "Coach access required",
            },
          },
          { status: 403 },
        ),
      ),
    );
    const user = userEvent.setup();
    render(
      <CreateGameForm teams={[coachTeam()]} defaultTeamId="team-1" />,
    );
    await user.click(screen.getByRole("button", { name: /create game/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/coach access/i);
  });

  it("surfaces email verification errors separately from coach access", async () => {
    replace.mockReset();
    server.use(
      http.post("/api/v1/games", () =>
        HttpResponse.json(
          {
            error: {
              code: "FORBIDDEN",
              message: "Email verification is required before this action",
              details: { reason: "email_unverified" },
            },
          },
          { status: 403 },
        ),
      ),
    );
    const user = userEvent.setup();
    render(<CreateGameForm teams={[coachTeam()]} defaultTeamId="team-1" />);
    await user.click(screen.getByRole("button", { name: /create game/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/verify your email/i);
    expect(alert.textContent).not.toMatch(/coach access/i);
  });
});
