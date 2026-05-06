import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { JoinTeamForm } from "@/app/(app)/teams/join/join-team-form";
import { server } from "./setup";

const replace = vi.fn();
const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh }),
}));

describe("JoinTeamForm", () => {
  it("joins via invite code and navigates to the team page", async () => {
    replace.mockReset();
    refresh.mockReset();
    let captured: unknown = null;
    server.use(
      http.post("/api/v1/teams/join", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({
          id: "joined-team",
          name: "Lincoln Varsity",
          sport: "basketball",
          level: "high_school",
          institution: null,
          institution_type: "k12_school",
          season: "2026-2027",
          invite_code: "LVB-2026",
          membership: {
            user_id: "u1",
            full_name: "James Williams",
            role: "player",
            team_role: "player",
            jersey_number: 23,
            joined_at: "2026-04-15T00:00:00Z",
          },
        });
      }),
    );
    const user = userEvent.setup();
    render(<JoinTeamForm initialCode="lvb-2026" />);
    const codeInput = screen.getByLabelText(/Invite code/i) as HTMLInputElement;
    expect(codeInput.value).toBe("LVB-2026");
    await user.type(screen.getByLabelText(/Jersey number/i), "23");
    await user.click(screen.getByRole("button", { name: /join team/i }));
    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toMatchObject({
      invite_code: "LVB-2026",
      jersey_number: 23,
    });
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/teams/joined-team"),
    );
    expect(refresh).toHaveBeenCalled();
  });

  it("translates jersey collision errors into a coach-friendly message", async () => {
    replace.mockReset();
    server.use(
      http.post("/api/v1/teams/join", () =>
        HttpResponse.json(
          {
            error: {
              code: "JERSEY_NUMBER_TAKEN",
              message: "Jersey number is already taken on this team",
            },
          },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    render(<JoinTeamForm initialCode="ABC" />);
    await user.type(screen.getByLabelText(/Jersey number/i), "5");
    await user.click(screen.getByRole("button", { name: /join team/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/already taken/i);
    expect(replace).not.toHaveBeenCalled();
  });
});
