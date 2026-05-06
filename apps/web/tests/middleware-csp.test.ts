import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "@/middleware";

describe("middleware CSP", () => {
  it("allows same-origin Next.js chunks while preserving script nonces", () => {
    const request = new NextRequest("https://alpha.nextballup.com/register");

    const response = middleware(request);
    const csp = response.headers.get("Content-Security-Policy");

    expect(csp).toBeTruthy();
    expect(csp).toContain("script-src 'self' 'nonce-");
    expect(csp).not.toContain("'strict-dynamic'");
    expect(csp).toContain("style-src 'self' 'unsafe-inline'");
    expect(csp).toContain("connect-src 'self' https:");
    expect(response.headers.get("Report-To")).toContain("csp-endpoint");
  });
});
