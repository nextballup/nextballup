import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import VerifyEmailPage from "@/app/(auth)/verify-email/page";
import { server } from "./setup";

const navigationState = vi.hoisted(() => ({
  searchParams: new URLSearchParams("token=verify-token"),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => navigationState.searchParams,
}));

describe("email verification UI", () => {
  beforeEach(() => {
    navigationState.searchParams = new URLSearchParams("token=verify-token");
  });

  it("confirms the verification token from the link", async () => {
    const submissions: Array<Record<string, unknown>> = [];
    server.use(
      http.post("/api/v1/auth/email/verify/confirm", async ({ request }) => {
        submissions.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({
          confirmed_at: "2026-05-06T00:00:00Z",
          is_verified: true,
        });
      }),
    );

    render(<VerifyEmailPage />);

    await waitFor(() => expect(submissions).toEqual([{ token: "verify-token" }]));
    expect(await screen.findByRole("status")).toHaveTextContent(/email verified/i);
  });

  it("rejects a link without a token before calling the API", () => {
    let called = false;
    navigationState.searchParams = new URLSearchParams("");
    server.use(
      http.post("/api/v1/auth/email/verify/confirm", () => {
        called = true;
        return HttpResponse.json({
          confirmed_at: "2026-05-06T00:00:00Z",
          is_verified: true,
        });
      }),
    );

    render(<VerifyEmailPage />);

    expect(screen.getByRole("alert")).toHaveTextContent(/verification link is missing/i);
    expect(called).toBe(false);
  });

  it("surfaces expired verification-link errors from the API", async () => {
    server.use(
      http.post("/api/v1/auth/email/verify/confirm", () =>
        HttpResponse.json(
          {
            error: {
              code: "EMAIL_VERIFICATION_TOKEN_EXPIRED",
              message: "Verification link has expired",
            },
          },
          { status: 400 },
        ),
      ),
    );

    render(<VerifyEmailPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/has expired/i);
  });
});
