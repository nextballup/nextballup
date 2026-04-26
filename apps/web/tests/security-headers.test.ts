import { describe, expect, it } from "vitest";
import nextConfig from "../next.config";

describe("Next.js security headers", () => {
  it("sets HSTS for the frontend edge", async () => {
    const headers = await nextConfig.headers?.();
    const allHeaders = headers?.flatMap((entry) => entry.headers) ?? [];

    expect(allHeaders).toContainEqual({
      key: "Strict-Transport-Security",
      value: "max-age=63072000; includeSubDomains; preload",
    });
  });
});
