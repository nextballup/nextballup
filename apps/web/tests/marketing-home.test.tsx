import { describe, expect, it, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import LandingPage from "@/app/page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

describe("Marketing home", () => {
  it("renders headline, workflow steps, security, and FAQ sections", async () => {
    await act(async () => {
      render(<LandingPage />);
    });

    // Hero — the prompt asks for "AI-assisted basketball film review" framing.
    const main = screen.getByRole("main");
    expect(
      within(main).getByRole("heading", { level: 1 }).textContent,
    ).toMatch(/AI-assisted basketball film review/i);

    // Workflow section: at least the five honest steps.
    expect(within(main).getByText(/1\. Upload game or practice film/i)).toBeInTheDocument();
    expect(within(main).getByText(/2\. Browser playback/i)).toBeInTheDocument();
    expect(within(main).getByText(/3\. Alpha detector preview/i)).toBeInTheDocument();
    expect(within(main).getByText(/4\. Review candidate moments/i)).toBeInTheDocument();
    expect(within(main).getByText(/5\. Coach confirms, rejects, or tags/i)).toBeInTheDocument();

    // Security and FAQ headings exist.
    expect(within(main).getByText(/Security & privacy/i)).toBeInTheDocument();
    expect(within(main).getByText(/Straight answers about the alpha/i)).toBeInTheDocument();
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

  it("explicitly disclaims production analytics in the FAQ", async () => {
    await act(async () => {
      render(<LandingPage />);
    });

    // The FAQ asks whether the alpha is production-grade analytics and
    // answers "No." The negation must be present somewhere on the page so
    // visitors who skim the FAQ get the correct read.
    const body = screen.getByRole("main").textContent ?? "";
    expect(body).toMatch(/production-grade analytics/i);
    expect(body).toMatch(/\bno\b\.?\s*the current alpha detector/i);
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
});
