import { afterEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { ApiError } from "@/lib/errors";
import { apiJson } from "@/lib/api-client";
import { server } from "./setup";

describe("apiJson", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("decodes a happy response", async () => {
    server.use(
      http.get("/api/v1/auth/me", () =>
        HttpResponse.json({
          id: "u1",
          email: "a@b.com",
          full_name: "A B",
          role: "coach",
          teams: [],
        }),
      ),
    );
    const result = await apiJson<{ id: string }>("/auth/me");
    expect(result.id).toBe("u1");
  });

  it("raises ApiError for a structured error envelope", async () => {
    server.use(
      http.post("/api/v1/auth/login", () =>
        HttpResponse.json(
          {
            error: {
              code: "INVALID_CREDENTIALS",
              message: "Invalid email or password",
              details: {},
            },
            request_id: "req-42",
          },
          { status: 401 },
        ),
      ),
    );
    await expect(
      apiJson("/auth/login", {
        method: "POST",
        json: { email: "x", password: "y" },
        noRefreshOn401: true,
      }),
    ).rejects.toMatchObject({
      code: "INVALID_CREDENTIALS",
      status: 401,
      requestId: "req-42",
    });
  });

  it("surfaces Retry-After on rate-limited responses", async () => {
    server.use(
      http.post("/api/v1/auth/login", () =>
        HttpResponse.json(
          {
            error: {
              code: "RATE_LIMITED",
              message: "Too many attempts",
            },
          },
          { status: 429, headers: { "Retry-After": "7" } },
        ),
      ),
    );

    await expect(
      apiJson("/auth/login", {
        method: "POST",
        json: { email: "x", password: "y" },
        noRefreshOn401: true,
      }),
    ).rejects.toMatchObject({
      code: "RATE_LIMITED",
      status: 429,
      retryAfterMs: 7000,
    });
  });

  it("auto-retries once on 401 after a successful refresh", async () => {
    let videoCalls = 0;
    server.use(
      http.get("/api/v1/videos/abc", () => {
        videoCalls += 1;
        if (videoCalls === 1) {
          return HttpResponse.json(
            { error: { code: "UNAUTHENTICATED", message: "nope" } },
            { status: 401 },
          );
        }
        return HttpResponse.json({ id: "abc", status: "processed" });
      }),
      http.post("/api/v1/auth/refresh", () =>
        HttpResponse.json({ refreshed_at: "2026-04-16T00:00:00Z" }),
      ),
    );
    const response = await apiJson<{ id: string; status: string }>(
      "/videos/abc",
    );
    expect(response.id).toBe("abc");
    expect(videoCalls).toBe(2);
  });

  it("re-reads the CSRF cookie before retrying a mutating request after refresh", async () => {
    document.cookie = "nbu_csrf_token=csrf-before-refresh";
    let updateCalls = 0;
    const seenHeaders: string[] = [];
    server.use(
      http.patch("/api/v1/games/g-refresh", ({ request }) => {
        seenHeaders.push(request.headers.get("X-CSRF-Token") ?? "");
        updateCalls += 1;
        if (updateCalls === 1) {
          return HttpResponse.json(
            { error: { code: "UNAUTHENTICATED", message: "expired" } },
            { status: 401 },
          );
        }
        return HttpResponse.json({ id: "g-refresh", status: "scheduled" });
      }),
      http.post("/api/v1/auth/refresh", () => {
        document.cookie = "nbu_csrf_token=csrf-after-refresh";
        return HttpResponse.json({ refreshed_at: "2026-04-19T00:00:00Z" });
      }),
    );
    try {
      await apiJson("/games/g-refresh", {
        method: "PATCH",
        json: { status: "scheduled" },
      });
      expect(seenHeaders).toEqual([
        "csrf-before-refresh",
        "csrf-after-refresh",
      ]);
    } finally {
      document.cookie = "nbu_csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
    }
  });

  it("does not auto-refresh login's own 401", async () => {
    let loginCalls = 0;
    server.use(
      http.post("/api/v1/auth/login", () => {
        loginCalls += 1;
        return HttpResponse.json(
          { error: { code: "INVALID_CREDENTIALS", message: "nope" } },
          { status: 401 },
        );
      }),
    );
    await expect(
      apiJson("/auth/login", {
        method: "POST",
        json: { email: "a", password: "b" },
        noRefreshOn401: true,
      }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(loginCalls).toBe(1);
  });

  it("mirrors the CSRF cookie into X-CSRF-Token on mutating requests", async () => {
    const original = document.cookie;
    // jsdom cookie jar: set directly so readCsrfCookie can see it.
    document.cookie = "nbu_csrf_token=abc-csrf-123";
    let seenCsrf: string | null = null;
    server.use(
      http.patch("/api/v1/games/g1", ({ request }) => {
        seenCsrf = request.headers.get("X-CSRF-Token");
        return HttpResponse.json({ id: "g1", status: "scheduled" });
      }),
    );
    try {
      await apiJson("/games/g1", { method: "PATCH", json: { status: "scheduled" } });
      expect(seenCsrf).toBe("abc-csrf-123");
    } finally {
      document.cookie = "nbu_csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
      // Restore any prior cookies (e.g. if tests later add auth cookies).
      if (original) document.cookie = original;
    }
  });

  it("warns when a mutating request is missing the CSRF cookie", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    server.use(
      http.patch("/api/v1/games/g1", () =>
        HttpResponse.json({ id: "g1", status: "scheduled" }),
      ),
    );

    await apiJson("/games/g1", { method: "PATCH", json: { status: "scheduled" } });

    expect(warn).toHaveBeenCalledWith(expect.stringMatching(/CSRF cookie is missing/i));
  });

  it("does not warn for anonymous CSRF-optional auth mutations", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    server.use(
      http.post("/api/v1/auth/login", () =>
        HttpResponse.json({ access_token_expires_at: "2026-05-06T00:00:00Z" }),
      ),
    );

    await apiJson("/auth/login", {
      method: "POST",
      json: { email: "pilot@example.com", password: "Password1!" },
    });

    expect(warn).not.toHaveBeenCalled();
  });

  it("does not attach X-CSRF-Token on GET requests", async () => {
    document.cookie = "nbu_csrf_token=should-not-leak";
    let seenCsrf: string | null | undefined = undefined;
    server.use(
      http.get("/api/v1/games/g2", ({ request }) => {
        seenCsrf = request.headers.get("X-CSRF-Token");
        return HttpResponse.json({ id: "g2" });
      }),
    );
    try {
      await apiJson("/games/g2");
      expect(seenCsrf).toBeNull();
    } finally {
      document.cookie = "nbu_csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
    }
  });
});
