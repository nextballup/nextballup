import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { GameEditForm } from "@/app/(app)/games/[gameId]/game-edit-form";
import type { GameSummary } from "@/lib/contract";
import { server } from "./setup";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: () => {} }),
}));

function scheduledGame(overrides: Partial<GameSummary> = {}): GameSummary {
  return {
    id: "g1",
    team_id: "t1",
    opponent_name: "Jefferson",
    game_type: "regular_season",
    date: "2026-11-15",
    time: null,
    location: null,
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
    ...overrides,
  };
}

describe("GameEditForm", () => {
  it("sends a PATCH with the updated score and flashes success on 200", async () => {
    let captured: unknown = null;
    server.use(
      http.patch("/api/v1/games/g1", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(
          scheduledGame({ score_team: 67, score_opponent: 54, status: "completed" }),
        );
      }),
    );
    const user = userEvent.setup();
    render(<GameEditForm game={scheduledGame()} />);
    await user.clear(screen.getByLabelText(/Score \(us\)/i));
    await user.type(screen.getByLabelText(/Score \(us\)/i), "67");
    await user.clear(screen.getByLabelText(/Score \(opponent\)/i));
    await user.type(screen.getByLabelText(/Score \(opponent\)/i), "54");
    await user.selectOptions(screen.getByLabelText(/Status/i), "completed");
    await user.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toMatchObject({
      score_team: 67,
      score_opponent: 54,
      status: "completed",
    });
    await screen.findByRole("status");
  });

  it("surfaces the GAME_TERMINAL_STATUS error with an actionable message", async () => {
    server.use(
      http.patch("/api/v1/games/g1", () =>
        HttpResponse.json(
          {
            error: {
              code: "GAME_TERMINAL_STATUS",
              message:
                "Cannot change status of a terminal game; ask an admin to reopen",
            },
          },
          { status: 403 },
        ),
      ),
    );
    const user = userEvent.setup();
    render(<GameEditForm game={scheduledGame({ status: "completed" })} />);
    await user.selectOptions(screen.getByLabelText(/Status/i), "scheduled");
    await user.click(screen.getByRole("button", { name: /save changes/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/admin to reopen/i);
  });

  it("allows same-status edits on a terminal game (admin-reopen is the only blocked transition)", async () => {
    let captured: unknown = null;
    server.use(
      http.patch("/api/v1/games/g1", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(
          scheduledGame({ status: "completed", notes: "Updated notes" }),
        );
      }),
    );
    const user = userEvent.setup();
    render(<GameEditForm game={scheduledGame({ status: "completed" })} />);
    await user.type(screen.getByLabelText(/Notes/i), "Updated notes");
    await user.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toMatchObject({
      status: "completed",
      notes: "Updated notes",
    });
  });
});
