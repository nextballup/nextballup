import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { ApiError } from "@/lib/errors";
import { apiJson } from "@/lib/api-client";
import { server } from "./setup";

describe("apiJson", () => {
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
        HttpResponse.json({ access_token: "a", refresh_token: "r" }),
      ),
    );
    const response = await apiJson<{ id: string; status: string }>(
      "/videos/abc",
    );
    expect(response.id).toBe("abc");
    expect(videoCalls).toBe(2);
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
});
