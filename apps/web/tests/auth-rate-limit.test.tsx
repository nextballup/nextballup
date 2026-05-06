import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import LoginPage from "@/app/(auth)/login/page";
import RegisterPage from "@/app/(auth)/register/page";
import { server } from "./setup";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
}));

describe("auth Retry-After handling", () => {
  it("prompts for MFA and resubmits the login code", async () => {
    const submissions: Array<Record<string, unknown>> = [];
    server.use(
      http.post("/api/v1/auth/login", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        submissions.push(body);
        if (!body.mfa_code) {
          return HttpResponse.json(
            {
              error: {
                code: "MFA_REQUIRED",
                message: "MFA code is required",
                details: { mfa_required: true },
              },
            },
            { status: 401 },
          );
        }
        return HttpResponse.json({
          user: {
            id: "user-1",
            email: body.email,
            full_name: "MFA User",
            role: "coach",
            teams: [],
          },
        });
      }),
    );
    const user = userEvent.setup();
    render(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "mfa@example.com");
    await user.type(screen.getByLabelText(/password/i), "Password1!");
    await user.click(screen.getByRole("button", { name: /^sign in$/i }));
    await user.type(await screen.findByLabelText(/authenticator or recovery code/i), "123456");
    await user.click(screen.getByRole("button", { name: /^sign in$/i }));

    expect(submissions).toHaveLength(2);
    expect(submissions[1]).toMatchObject({ mfa_code: "123456" });
  });

  it("disables login submission while rate-limited", async () => {
    server.use(
      http.post("/api/v1/auth/login", () =>
        HttpResponse.json(
          { error: { code: "RATE_LIMITED", message: "Too many attempts" } },
          { status: 429, headers: { "Retry-After": "4" } },
        ),
      ),
    );
    const user = userEvent.setup();
    render(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "rate@example.com");
    await user.type(screen.getByLabelText(/password/i), "Password1!");
    await user.click(screen.getByRole("button", { name: /^sign in$/i }));

    const button = await screen.findByRole("button", { name: /try again in 4s/i });
    expect(button).toBeDisabled();
  });

  it("disables registration submission while rate-limited", async () => {
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
          { error: { code: "RATE_LIMITED", message: "Too many attempts" } },
          { status: 429, headers: { "Retry-After": "5" } },
        ),
      ),
    );
    const user = userEvent.setup();
    render(<RegisterPage />);

    await user.type(screen.getByLabelText(/full name/i), "Rate Limited");
    await user.type(screen.getByLabelText(/email/i), "rate-register@example.com");
    await user.type(screen.getByLabelText(/password/i), "Password1!");
    await user.click(screen.getByRole("button", { name: /^create account$/i }));

    const button = await screen.findByRole("button", { name: /try again in 5s/i });
    expect(button).toBeDisabled();
  });
});
