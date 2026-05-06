import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "@/app/(app)/app-shell";
import type { UserPublic } from "@/lib/contract";

const mocks = vi.hoisted(() => ({
  apiJson: vi.fn(async (..._args: unknown[]): Promise<unknown> => ({
    is_verified: true,
    pending_request: false,
    last_requested_at: null,
    last_confirmed_at: null,
  })),
  apiVoid: vi.fn(async () => undefined),
  pathname: vi.fn(() => "/games"),
  replace: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  apiJson: mocks.apiJson,
  apiVoid: mocks.apiVoid,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => mocks.pathname(),
  useRouter: () => ({ replace: mocks.replace }),
}));

const user: UserPublic = {
  id: "u1",
  email: "coach@example.com",
  full_name: "Coach User",
  role: "coach",
  teams: [],
};

describe("AppShell", () => {
  beforeEach(() => {
    mocks.apiVoid.mockClear();
    mocks.apiJson.mockClear();
    mocks.apiJson.mockResolvedValue({
      is_verified: true,
      pending_request: false,
      last_requested_at: null,
      last_confirmed_at: null,
    });
    mocks.pathname.mockClear();
    mocks.replace.mockClear();
  });

  it("clears cached session data before navigating on logout", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    queryClient.setQueryData(["videos", "secret"], {
      playback_url: "https://signed.example/video.mp4",
    });

    render(
      <QueryClientProvider client={queryClient}>
        <AppShell user={user} teams={[]} activeTeamId={null}>
          <div>Dashboard</div>
        </AppShell>
      </QueryClientProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: /sign out/i }));

    await waitFor(() =>
      expect(mocks.apiVoid).toHaveBeenCalledWith("/auth/logout", {
        method: "POST",
        noRefreshOn401: true,
      }),
    );
    expect(queryClient.getQueryCache().findAll()).toHaveLength(0);
    expect(mocks.replace).toHaveBeenCalledWith("/login");
  });

  it("lets an unverified user request a verification email", async () => {
    mocks.apiJson.mockImplementation(async (...args: unknown[]) => {
      const path = args[0];
      if (path === "/auth/email/verify/status") {
        return {
          is_verified: false,
          pending_request: false,
          last_requested_at: null,
          last_confirmed_at: null,
        };
      }
      if (path === "/auth/email/verify/request") {
        return {
          requested_at: "2026-05-06T00:00:00Z",
          expires_at: "2026-05-06T01:00:00Z",
          delivery: "postmark",
        };
      }
      throw new Error(`unexpected path: ${path}`);
    });

    render(
      <QueryClientProvider client={new QueryClient()}>
        <AppShell user={user} teams={[]} activeTeamId={null}>
          <div>Dashboard</div>
        </AppShell>
      </QueryClientProvider>,
    );

    expect(await screen.findByText(/verify your email/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /send verification email/i }));

    await waitFor(() =>
      expect(mocks.apiJson).toHaveBeenCalledWith("/auth/email/verify/request", {
        method: "POST",
        json: {},
      }),
    );
    expect(await screen.findByText(/verification email sent/i)).toBeInTheDocument();
  });
});
