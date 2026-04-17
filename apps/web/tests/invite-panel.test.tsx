import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { InvitePanel } from "@/app/(app)/teams/[teamId]/invite-panel";
import { server } from "./setup";

describe("InvitePanel", () => {
  it("shows the default team code and allows generating a new invite", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/v1/teams/t1/invite", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(
          {
            invite_code: "PLYR-NEW",
            invite_url: "http://localhost:3000/join/PLYR-NEW",
            expires_at: new Date(Date.now() + 86_400_000).toISOString(),
            remaining_uses: 5,
            role: "player",
          },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    render(<InvitePanel teamId="t1" defaultInviteCode="DEFAULT-XYZ" />);
    expect(screen.getByTestId("default-invite-code")).toHaveTextContent(
      "DEFAULT-XYZ",
    );
    fireEvent.change(screen.getByLabelText(/Max uses/i), {
      target: { value: "5" },
    });
    await user.click(screen.getByRole("button", { name: /generate invite/i }));
    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toMatchObject({ role: "player", max_uses: 5 });
    const box = await screen.findByTestId("generated-invite");
    expect(box.textContent).toMatch(/PLYR-NEW/);
  });

  it("surfaces a coach-only error when the user is not authorized", async () => {
    server.use(
      http.post("/api/v1/teams/t1/invite", () =>
        HttpResponse.json(
          {
            error: {
              code: "FORBIDDEN",
              message: "Coach access required for this action",
            },
          },
          { status: 403 },
        ),
      ),
    );
    const user = userEvent.setup();
    render(<InvitePanel teamId="t1" defaultInviteCode="X" />);
    await user.click(screen.getByRole("button", { name: /generate invite/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/coach access/i);
  });
});
