import { describe, expect, it, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import LandingPage from "@/app/page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

describe("Marketing home", () => {
  it("renders the hero and teaser cards linking out to each detail route", async () => {
    await act(async () => {
      render(<LandingPage />);
    });

    const main = screen.getByRole("main");
    // Hero — the prompt asks for "AI-assisted basketball film review" framing.
    expect(
      within(main).getByRole("heading", { level: 1 }).textContent,
    ).toMatch(/AI-assisted basketball film review/i);

    // Homepage now teases each detail page via cards instead of inlining
    // every section. Each card must link to its dedicated route.
    expect(
      (screen.getByTestId("teaser-product") as HTMLAnchorElement).getAttribute(
        "href",
      ),
    ).toBe("/product");
    expect(
      (screen.getByTestId("teaser-use-cases") as HTMLAnchorElement).getAttribute(
        "href",
      ),
    ).toBe("/use-cases");
    expect(
      (screen.getByTestId("teaser-security") as HTMLAnchorElement).getAttribute(
        "href",
      ),
    ).toBe("/security");
    expect(
      (screen.getByTestId("teaser-faq") as HTMLAnchorElement).getAttribute(
        "href",
      ),
    ).toBe("/faq");
  });

  it("never asserts forbidden marketing phrases without an explicit negation in the same sentence", async () => {
    await act(async () => {
      render(<LandingPage />);
    });

    const body = screen.getByRole("main").textContent ?? "";
    // Honesty discipline: if any of these appear (e.g. in the FAQ to be
    // explicitly disclaimed), they must be inside a sentence containing a
    // negation token. A positive marketing claim like "delivers automatic
    // stats" must not be possible without this test failing.
    const forbidden = [
      /automatic stats/i,
      /accurate tracking/i,
      /verified events?/i,
      /recruiting database/i,
      /production-grade analytics/i,
    ];
    const sentences = body
      .split(/(?<=[.?!])\s+/)
      .map((s) => s.trim())
      .filter(Boolean);
    for (const pattern of forbidden) {
      for (const sentence of sentences) {
        if (!pattern.test(sentence)) continue;
        expect(
          sentence,
        ).toMatch(/\b(not|no|never|nor|isn't|aren't|cannot|without)\b/i);
      }
    }
  });

  it("does not mention competitor or third-party trade names anywhere", async () => {
    await act(async () => {
      render(<LandingPage />);
    });
    const body = screen.getByRole("main").textContent ?? "";
    // Trade-dress hygiene: marketing page must not reference Hudl, NBA, NCAA,
    // school names, or other third-party platforms we don't have rights to.
    const forbidden = [/\bhudl\b/i, /\bnba\b/i, /\bncaa\b/i, /\bspacejam\b/i];
    for (const pattern of forbidden) {
      expect(body).not.toMatch(pattern);
    }
  });

  it("points the hero pilot CTA at /pilot and sign-in at the gated product host", async () => {
    await act(async () => {
      render(<LandingPage />);
    });

    const pilot = screen.getByTestId("hero-pilot-cta") as HTMLAnchorElement;
    expect(pilot.getAttribute("href")).toBe("/pilot");

    const signIn = screen.getByTestId("header-signin") as HTMLAnchorElement;
    // Default product host is beta.nextballup.com per the marketing header;
    // marketing must not deep-link into same-origin /login (cookies must
    // stay scoped to the gated host).
    expect(signIn.getAttribute("href")).toBe(
      "https://beta.nextballup.com/login",
    );
    expect(signIn.getAttribute("href")).not.toBe("/login");
  });

  it("nav links point at routes, not in-page anchors", async () => {
    await act(async () => {
      render(<LandingPage />);
    });

    const nav = screen.getByRole("navigation", { name: /primary/i });
    const links = within(nav)
      .getAllByRole("link")
      .map((link) => link.getAttribute("href") ?? "");
    expect(links).toEqual(
      expect.arrayContaining(["/product", "/use-cases", "/security", "/faq"]),
    );
    // No anchor-only links anywhere in the primary nav — anchors break on
    // any page that doesn't have that section (e.g. /pilot).
    for (const href of links) {
      expect(href.startsWith("#")).toBe(false);
    }
  });
});
