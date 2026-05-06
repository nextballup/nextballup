import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ForgotPasswordPage from "@/app/(auth)/forgot-password/page";
import LoginPage from "@/app/(auth)/login/page";
import ResetPasswordPage from "@/app/(auth)/reset-password/page";
import { server } from "./setup";

const navigationState = vi.hoisted(() => ({
  searchParams: new URLSearchParams("token=reset-token"),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => navigationState.searchParams,
}));

describe("password reset UI", () => {
  beforeEach(() => {
    navigationState.searchParams = new URLSearchParams("token=reset-token");
  });

  it("links to account recovery from login", () => {
    render(<LoginPage />);
    expect(screen.getByRole("link", { name: /forgot password/i })).toHaveAttribute(
      "href",
      "/forgot-password",
    );
  });

  it("requests a reset without revealing account existence", async () => {
    const submissions: Array<Record<string, unknown>> = [];
    server.use(
      http.post("/api/v1/auth/password/forgot", async ({ request }) => {
        submissions.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(
          { requested_at: "2026-05-01T00:00:00Z", delivery: "logging" },
          { status: 202 },
        );
      }),
    );
    const user = userEvent.setup();
    render(<ForgotPasswordPage />);

    await user.type(screen.getByLabelText(/email/i), "coach@example.com");
    await user.click(screen.getByRole("button", { name: /^send reset link$/i }));

    expect(submissions).toEqual([{ email: "coach@example.com" }]);
    expect(await screen.findByRole("status")).toHaveTextContent(
      /if that email belongs to an active account/i,
    );
  });

  it("submits the reset token and new password", async () => {
    const submissions: Array<Record<string, unknown>> = [];
    server.use(
      http.post("/api/v1/auth/password/reset", async ({ request }) => {
        submissions.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ reset_at: "2026-05-01T00:00:00Z" });
      }),
    );
    const user = userEvent.setup();
    render(<ResetPasswordPage />);

    await user.type(screen.getByLabelText(/^new password$/i), "NewPassword1!");
    await user.type(screen.getByLabelText(/^confirm password$/i), "NewPassword1!");
    await user.click(screen.getByRole("button", { name: /^reset password$/i }));

    expect(submissions).toEqual([{ token: "reset-token", new_password: "NewPassword1!" }]);
    expect(await screen.findByRole("status")).toHaveTextContent(/password reset/i);
  });

  it("blocks mismatched confirmation before calling the API", async () => {
    let called = false;
    server.use(
      http.post("/api/v1/auth/password/reset", () => {
        called = true;
        return HttpResponse.json({ reset_at: "2026-05-01T00:00:00Z" });
      }),
    );
    const user = userEvent.setup();
    render(<ResetPasswordPage />);

    await user.type(screen.getByLabelText(/^new password$/i), "NewPassword1!");
    await user.type(screen.getByLabelText(/^confirm password$/i), "OtherPassword1!");
    await user.click(screen.getByRole("button", { name: /^reset password$/i }));

    expect(called).toBe(false);
    expect(await screen.findByRole("alert")).toHaveTextContent(/passwords do not match/i);
  });

  it("disables reset when the link is missing a token", () => {
    navigationState.searchParams = new URLSearchParams("");

    render(<ResetPasswordPage />);

    expect(screen.getByRole("alert")).toHaveTextContent(/reset link is missing/i);
    expect(screen.getByRole("button", { name: /^reset password$/i })).toBeDisabled();
    expect(screen.getByLabelText(/^new password$/i)).toBeDisabled();
    expect(screen.getByLabelText(/^confirm password$/i)).toBeDisabled();
  });

  it("surfaces expired reset-token errors from the API", async () => {
    server.use(
      http.post("/api/v1/auth/password/reset", () =>
        HttpResponse.json(
          {
            error: {
              code: "PASSWORD_RESET_TOKEN_EXPIRED",
              message: "Password reset link has expired",
            },
          },
          { status: 400 },
        ),
      ),
    );
    const user = userEvent.setup();
    render(<ResetPasswordPage />);

    await user.type(screen.getByLabelText(/^new password$/i), "NewPassword1!");
    await user.type(screen.getByLabelText(/^confirm password$/i), "NewPassword1!");
    await user.click(screen.getByRole("button", { name: /^reset password$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/has expired/i);
  });

  it("surfaces used reset-token errors from the API", async () => {
    server.use(
      http.post("/api/v1/auth/password/reset", () =>
        HttpResponse.json(
          {
            error: {
              code: "PASSWORD_RESET_TOKEN_USED",
              message: "Password reset link has already been used",
            },
          },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    render(<ResetPasswordPage />);

    await user.type(screen.getByLabelText(/^new password$/i), "NewPassword1!");
    await user.type(screen.getByLabelText(/^confirm password$/i), "NewPassword1!");
    await user.click(screen.getByRole("button", { name: /^reset password$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already been used/i);
  });
});
