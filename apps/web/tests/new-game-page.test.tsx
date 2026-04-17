import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/errors";
import NewGamePage from "@/app/(app)/games/new/page";

const { serverApiJson, redirect } = vi.hoisted(() => ({
  serverApiJson: vi.fn(),
  redirect: vi.fn((url: string) => {
    throw new Error(`REDIRECT:${url}`);
  }),
}));

vi.mock("@/lib/api-server", () => ({
  serverApiJson,
}));

vi.mock("next/navigation", () => ({
  redirect,
}));

vi.mock("next/headers", () => ({
  cookies: vi.fn(async () => ({
    get: vi.fn(() => undefined),
  })),
}));

describe("NewGamePage", () => {
  it("redirects to login when SSR team loading gets a 401", async () => {
    serverApiJson.mockRejectedValue(
      new ApiError(401, "UNAUTHORIZED", "Missing authentication credentials"),
    );

    await expect(NewGamePage()).rejects.toThrow("REDIRECT:/login");
    expect(redirect).toHaveBeenCalledWith("/login");
  });
});
