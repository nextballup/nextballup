import { beforeEach, describe, expect, it, vi } from "vitest";

const { cookiesMock } = vi.hoisted(() => ({
  cookiesMock: vi.fn(),
}));

vi.mock("next/headers", () => ({
  cookies: cookiesMock,
}));

import { ApiError } from "@/lib/errors";
import { serverApiJson, serverApiOptional } from "@/lib/api-server";

describe("serverApi helpers", () => {
  beforeEach(() => {
    cookiesMock.mockReset();
    vi.restoreAllMocks();
  });

  it("forwards only NextBallUp auth cookies to the upstream API", async () => {
    cookiesMock.mockResolvedValue({
      getAll: () => [
        { name: "nbu_access_token", value: "access" },
        { name: "nbu_refresh_token", value: "refresh" },
        { name: "other_cookie", value: "ignore-me" },
      ],
    });
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json({ id: "u1" }));

    await serverApiJson<{ id: string }>("/auth/me");

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/api/v1/auth/me");
    expect(init?.cache).toBe("no-store");
    const headers = init?.headers as Headers;
    expect(headers.get("Cookie")).toBe(
      "nbu_access_token=access; nbu_refresh_token=refresh",
    );
  });

  it("can treat backend 403 isolation responses as null on SSR resource pages", async () => {
    cookiesMock.mockResolvedValue({
      getAll: () => [],
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json(
        {
          error: {
            code: "FORBIDDEN",
            message: "You are not a member of this team",
          },
        },
        { status: 403 },
      ),
    );

    const result = await serverApiOptional("/videos/vid-1", {
      nullOnStatuses: [403, 404],
    });

    expect(result).toBeNull();
  });

  it("still treats 401 as null even when callers opt into 403 handling", async () => {
    cookiesMock.mockResolvedValue({
      getAll: () => [],
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json(
        {
          error: {
            code: "UNAUTHENTICATED",
            message: "Missing authentication credentials",
          },
        },
        { status: 401 },
      ),
    );

    const result = await serverApiOptional("/videos/vid-1", {
      nullOnStatuses: [403, 404],
    });

    expect(result).toBeNull();
  });

  it("still throws when the caller does not opt into null-on-403", async () => {
    cookiesMock.mockResolvedValue({
      getAll: () => [],
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json(
        {
          error: {
            code: "FORBIDDEN",
            message: "You are not a member of this team",
          },
        },
        { status: 403 },
      ),
    );

    await expect(serverApiOptional("/videos/vid-1")).rejects.toBeInstanceOf(
      ApiError,
    );
  });
});
