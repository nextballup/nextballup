import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import LandingPage from "@/app/page";
import RegisterPage from "@/app/(auth)/register/page";
import { emailVerificationRetryStorageKey } from "@/lib/email-verification-state";
import { server } from "./setup";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
}));

const originalMode = process.env.NEXT_PUBLIC_REGISTRATION_MODE;

afterEach(() => {
  vi.restoreAllMocks();
  if (originalMode === undefined) {
    delete process.env.NEXT_PUBLIC_REGISTRATION_MODE;
  } else {
    process.env.NEXT_PUBLIC_REGISTRATION_MODE = originalMode;
  }
});

describe("registration channel UI", () => {
  // The public marketing site (nextballup.com) does not render same-origin
  // /register or /login links regardless of NEXT_PUBLIC_REGISTRATION_MODE.
  // The only marketing CTA is /pilot, and Sign in points at the gated
  // product host. These tests pin that invariant so a future copy change
  // can't accidentally re-introduce a /register CTA on a marketing build.
  it.each([
    ["unset", undefined],
    ["open", "open"],
    ["invite_only", "invite_only"],
    ["allowlist", "allowlist"],
    ["disabled", "disabled"],
    ["unknown", "invite-ony"],
  ] as const)(
    "never exposes same-origin /register or /login links on the marketing landing (mode=%s)",
    (_label, mode) => {
      if (mode === undefined) {
        delete process.env.NEXT_PUBLIC_REGISTRATION_MODE;
      } else {
        process.env.NEXT_PUBLIC_REGISTRATION_MODE = mode;
      }

      render(<LandingPage />);

      expect(
        screen.queryByRole("link", { name: /^create account$/i }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("link", { name: /have an invite/i }),
      ).not.toBeInTheDocument();
      const sameOriginAuth = screen
        .queryAllByRole("link")
        .filter((link) => {
          const href = link.getAttribute("href") ?? "";
          return href === "/register" || href === "/login";
        });
      expect(sameOriginAuth).toHaveLength(0);
    },
  );

  it("routes every Request pilot access CTA on the marketing landing to /pilot", () => {
    process.env.NEXT_PUBLIC_REGISTRATION_MODE = "allowlist";

    render(<LandingPage />);

    const pilotLinks = screen
      .getAllByRole("link", { name: /request pilot access/i })
      .map((link) => link.getAttribute("href"));
    expect(pilotLinks.length).toBeGreaterThan(0);
    for (const href of pilotLinks) {
      expect(href).toBe("/pilot");
    }
  });

  it("keeps the register form disabled until status loads", () => {
    server.use(
      http.get("/api/v1/auth/registration/status", async () => {
        await new Promise(() => undefined);
        return HttpResponse.json({
          mode: "open",
          invite_code_required: false,
          is_open_to_public: true,
        });
      }),
    );

    render(<RegisterPage />);

    expect(screen.getByRole("status")).toHaveTextContent(/checking registration/i);
    expect(screen.getByRole("button", { name: /checking registration/i })).toBeDisabled();
  });

  it("fails closed when registration status cannot be loaded", async () => {
    server.use(
      http.get("/api/v1/auth/registration/status", () =>
        HttpResponse.json({ error: { code: "UNAVAILABLE", message: "unavailable" } }, { status: 503 }),
      ),
    );

    render(<RegisterPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/status is unavailable/i);
    expect(screen.getByRole("button", { name: /registration closed/i })).toBeDisabled();
  });

  it("submits the invite code only when the backend requires it", async () => {
    const submissions: Array<Record<string, unknown>> = [];
    const verificationRequests: Request[] = [];
    const statusRequests: Request[] = [];
    server.use(
      http.get("/api/v1/auth/registration/status", ({ request }) => {
        statusRequests.push(request);
        return HttpResponse.json({
          mode: "invite_only",
          invite_code_required: true,
          is_open_to_public: false,
        });
      }),
      http.post("/api/v1/auth/register", async ({ request }) => {
        submissions.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(
          {
            id: "user-1",
            email: "pilot@example.com",
            full_name: "Pilot User",
            role: "coach",
            created_at: "2026-05-04T00:00:00Z",
          },
          { status: 201 },
        );
      }),
      http.post("/api/v1/auth/email/verify/request", ({ request }) => {
        verificationRequests.push(request);
        return HttpResponse.json(
          {
            requested_at: "2026-05-06T00:00:00Z",
            expires_at: "2026-05-06T01:00:00Z",
            delivery: "postmark",
          },
          { status: 202 },
        );
      }),
    );
    const user = userEvent.setup();

    render(<RegisterPage />);

    await user.type(screen.getByLabelText(/full name/i), "Pilot User");
    await user.type(screen.getByLabelText(/email/i), "pilot@example.com");
    await user.type(screen.getByLabelText(/password/i), "Password1!");
    await user.type(await screen.findByLabelText(/invite code/i), "PILOT-CODE-AAAA");
    await user.click(screen.getByRole("button", { name: /^create account$/i }));

    expect(submissions).toEqual([
      {
        email: "pilot@example.com",
        password: "Password1!",
        full_name: "Pilot User",
        role: "coach",
        invite_code: "PILOT-CODE-AAAA",
      },
    ]);
    expect(statusRequests[0]?.cache).toBe("no-store");
    expect(verificationRequests).toHaveLength(1);
  });

  it("keeps role selection as native radios", async () => {
    server.use(
      http.get("/api/v1/auth/registration/status", () =>
        HttpResponse.json({
          mode: "open",
          invite_code_required: false,
          is_open_to_public: true,
        }),
      ),
    );
    const user = userEvent.setup();

    render(<RegisterPage />);

    const playerRadio = await screen.findByRole("radio", { name: /i'm a player/i });
    await user.click(playerRadio);
    expect(playerRadio).toBeChecked();
    expect(screen.getByRole("radio", { name: /i'm a coach/i })).not.toBeChecked();
  });

  it("records a retry hint when registration email delivery fails", async () => {
    sessionStorage.clear();
    server.use(
      http.get("/api/v1/auth/registration/status", () =>
        HttpResponse.json({
          mode: "open",
          invite_code_required: false,
          is_open_to_public: true,
        }),
      ),
      http.post("/api/v1/auth/register", () =>
        HttpResponse.json(
          {
            id: "user-1",
            email: "pilot@example.com",
            full_name: "Pilot User",
            role: "coach",
            created_at: "2026-05-04T00:00:00Z",
          },
          { status: 201 },
        ),
      ),
      http.post("/api/v1/auth/email/verify/request", () =>
        HttpResponse.json(
          {
            error: {
              code: "EMAIL_DELIVERY_UNAVAILABLE",
              message: "Email delivery is temporarily unavailable",
            },
          },
          { status: 503 },
        ),
      ),
    );
    const user = userEvent.setup();

    render(<RegisterPage />);

    await user.type(screen.getByLabelText(/full name/i), "Pilot User");
    await user.type(screen.getByLabelText(/email/i), "pilot@example.com");
    await user.type(screen.getByLabelText(/password/i), "Password1!");
    await user.click(await screen.findByRole("button", { name: /^create account$/i }));

    await waitFor(() => {
      expect(
        sessionStorage.getItem(emailVerificationRetryStorageKey("pilot@example.com")),
      ).toBe("1");
    });
  });
});
