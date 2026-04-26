import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "@/app/(app)/app-shell";
import type { UserPublic } from "@/lib/contract";

const mocks = vi.hoisted(() => ({
  apiVoid: vi.fn(async () => undefined),
  pathname: vi.fn(() => "/games"),
  replace: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
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
});
