import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import LandingPage from "@/app/page";
import RegisterPage from "@/app/(auth)/register/page";
import { server } from "./setup";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
}));

const originalMode = process.env.NEXT_PUBLIC_REGISTRATION_MODE;

afterEach(() => {
  if (originalMode === undefined) {
    delete process.env.NEXT_PUBLIC_REGISTRATION_MODE;
  } else {
    process.env.NEXT_PUBLIC_REGISTRATION_MODE = originalMode;
  }
});

describe("registration channel UI", () => {
  it("fails closed on the public landing page when mode is unset", () => {
    delete process.env.NEXT_PUBLIC_REGISTRATION_MODE;

    render(<LandingPage />);

    expect(screen.queryByRole("link", { name: /^create account$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /have an invite/i })).not.toBeInTheDocument();
    expect(screen.getByText(/public access is not yet open/i)).toBeInTheDocument();
  });

  it("shows the invite CTA on invite-only channels", () => {
    process.env.NEXT_PUBLIC_REGISTRATION_MODE = "invite_only";

    render(<LandingPage />);

    expect(screen.getByRole("link", { name: /have an invite/i })).toHaveAttribute(
      "href",
      "/register",
    );
    expect(screen.queryByRole("link", { name: /^create account$/i })).not.toBeInTheDocument();
  });

  it("does not ask for an invite on allowlist channels", () => {
    process.env.NEXT_PUBLIC_REGISTRATION_MODE = "allowlist";

    render(<LandingPage />);

    expect(screen.getByRole("link", { name: /pilot access/i })).toHaveAttribute(
      "href",
      "/register",
    );
    expect(screen.queryByRole("link", { name: /have an invite/i })).not.toBeInTheDocument();
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
});
