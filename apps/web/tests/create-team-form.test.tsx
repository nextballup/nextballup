import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { CreateTeamForm } from "@/app/(app)/teams/new/create-team-form";
import { server } from "./setup";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));

describe("CreateTeamForm", () => {
  it("POSTs /teams with the entered fields and navigates to the new team", async () => {
    replace.mockReset();
    let captured: unknown = null;
    server.use(
      http.post("/api/v1/teams", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(
          {
            id: "new-team",
            name: "Lincoln Varsity Boys",
            sport: "basketball",
            level: "high_school",
            institution: "Lincoln High",
            institution_type: "k12_school",
            season: "2026-2027",
            invite_code: "LINC-ABC",
            created_at: "2026-04-15T00:00:00Z",
            member_count: 1,
          },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    render(<CreateTeamForm />);
    await user.type(screen.getByLabelText(/Team name/i), "Lincoln Varsity Boys");
    await user.clear(screen.getByLabelText(/^Season$/i));
    await user.type(screen.getByLabelText(/^Season$/i), "2026-2027");
    await user.type(
      screen.getByLabelText(/^Institution\b.*optional/i),
      "Lincoln High",
    );
    await user.click(screen.getByRole("button", { name: /create team/i }));
    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toMatchObject({
      name: "Lincoln Varsity Boys",
      season: "2026-2027",
      institution: "Lincoln High",
      sport: "basketball",
    });
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/teams/new-team"));
  });

  it("tells a player-account user that only coaches can create teams", async () => {
    replace.mockReset();
    server.use(
      http.post("/api/v1/teams", () =>
        HttpResponse.json(
          {
            error: {
              code: "FORBIDDEN",
              message: "This action requires a different role",
            },
          },
          { status: 403 },
        ),
      ),
    );
    const user = userEvent.setup();
    render(<CreateTeamForm />);
    await user.type(screen.getByLabelText(/Team name/i), "Any Name");
    await user.click(screen.getByRole("button", { name: /create team/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/only coach accounts/i);
    expect(replace).not.toHaveBeenCalled();
  });
});
