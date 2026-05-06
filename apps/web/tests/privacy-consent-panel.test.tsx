import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { PrivacyConsentPanel } from "@/app/(app)/teams/[teamId]/privacy-consent-panel";
import { server } from "./setup";

function consent(overrides: Record<string, unknown> = {}) {
  return {
    id: "consent-1",
    team_id: "t1",
    recorded_by: "u1",
    label: "League filming terms",
    consent_source: "league_policy",
    covers_video_uploads: true,
    covers_cv_processing: true,
    commercial_ml_training_allowed: false,
    minors_authorized: true,
    athlete_pii_authorized: true,
    evidence_uri: "r2://evidence/league-terms.pdf",
    evidence_sha256: null,
    effective_at: "2026-05-06T00:00:00Z",
    expires_at: null,
    revoked_at: null,
    is_active: true,
    created_at: "2026-05-06T00:00:00Z",
    ...overrides,
  };
}

describe("PrivacyConsentPanel", () => {
  it("lists active consent evidence and records a new consent", async () => {
    const submissions: Array<Record<string, unknown>> = [];
    server.use(
      http.get("/api/v1/teams/t1/privacy-consents", () =>
        HttpResponse.json({ consents: [consent()], total: 1 }),
      ),
      http.post("/api/v1/teams/t1/privacy-consents", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        submissions.push(body);
        return HttpResponse.json(consent({ id: "consent-2", label: body.label }), {
          status: 201,
        });
      }),
    );
    const user = userEvent.setup();

    render(<PrivacyConsentPanel teamId="t1" />);

    expect(await screen.findByText(/league filming terms/i)).toBeInTheDocument();
    await user.clear(screen.getByLabelText(/label/i));
    await user.type(screen.getByLabelText(/label/i), "Club tournament waiver");
    await user.type(
      screen.getByLabelText(/evidence reference/i),
      "r2://evidence/tournament-waiver.pdf",
    );
    await user.click(screen.getByLabelText(/minor athletes authorized/i));
    await user.click(screen.getByRole("button", { name: /save consent evidence/i }));

    await waitFor(() => expect(submissions).toHaveLength(1));
    expect(submissions[0]).toMatchObject({
      label: "Club tournament waiver",
      evidence_uri: "r2://evidence/tournament-waiver.pdf",
      covers_video_uploads: true,
      covers_cv_processing: true,
      minors_authorized: true,
      athlete_pii_authorized: true,
      commercial_ml_training_allowed: false,
    });
    expect(await screen.findByRole("status")).toHaveTextContent(/saved/i);
  });

  it("surfaces email verification errors without calling them coach errors", async () => {
    server.use(
      http.get("/api/v1/teams/t1/privacy-consents", () =>
        HttpResponse.json({ consents: [], total: 0 }),
      ),
      http.post("/api/v1/teams/t1/privacy-consents", () =>
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

    render(<PrivacyConsentPanel teamId="t1" />);

    await screen.findByText(/no active consent/i);
    await user.type(
      screen.getByLabelText(/evidence reference/i),
      "r2://evidence/waiver.pdf",
    );
    await user.click(screen.getByRole("button", { name: /save consent evidence/i }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/verify your email/i);
    expect(alert.textContent).not.toMatch(/coach access/i);
  });
});
