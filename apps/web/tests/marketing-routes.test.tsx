import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import FaqPage from "@/app/faq/page";
import ProductPage from "@/app/product/page";
import SecurityPage from "@/app/security/page";
import UseCasesPage from "@/app/use-cases/page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

const originalProductBase = process.env.NEXT_PUBLIC_PRODUCT_BASE_URL;

afterEach(() => {
  if (originalProductBase === undefined) {
    delete process.env.NEXT_PUBLIC_PRODUCT_BASE_URL;
  } else {
    process.env.NEXT_PUBLIC_PRODUCT_BASE_URL = originalProductBase;
  }
});

describe("Marketing detail routes", () => {
  it("renders /product with the workflow section and a pilot CTA", async () => {
    await act(async () => {
      render(<ProductPage />);
    });
    const main = screen.getByRole("main");
    expect(within(main).getByText(/1\. Upload game or practice film/i)).toBeInTheDocument();
    expect(within(main).getByText(/5\. Coach confirms, rejects, or tags/i)).toBeInTheDocument();
    const cta = screen.getByTestId("cta-pilot") as HTMLAnchorElement;
    expect(cta.getAttribute("href")).toBe("/pilot");
  });

  it("renders /use-cases with the use cases section and a pilot CTA", async () => {
    await act(async () => {
      render(<UseCasesPage />);
    });
    const main = screen.getByRole("main");
    expect(within(main).getByText(/High-school programs/i)).toBeInTheDocument();
    expect(within(main).getByText(/Club and AAU teams/i)).toBeInTheDocument();
    expect(screen.getByTestId("cta-pilot")).toBeInTheDocument();
  });

  it("renders /security with the security section and a pilot CTA", async () => {
    await act(async () => {
      render(<SecurityPage />);
    });
    const main = screen.getByRole("main");
    expect(within(main).getByText(/Restricted access/i)).toBeInTheDocument();
    expect(within(main).getByText(/Audit on every change/i)).toBeInTheDocument();
    expect(screen.getByTestId("cta-pilot")).toBeInTheDocument();
  });

  it("renders /faq with FAQ content and a pilot CTA", async () => {
    await act(async () => {
      render(<FaqPage />);
    });
    const main = screen.getByRole("main");
    expect(
      within(main).getByText(/Is this production-grade analytics\?/i),
    ).toBeInTheDocument();
    expect(within(main).getByText(/Where does my film go\?/i)).toBeInTheDocument();
    expect(screen.getByTestId("cta-pilot")).toBeInTheDocument();
  });

  it("each public marketing detail route uses the configured product host for auth links", async () => {
    process.env.NEXT_PUBLIC_PRODUCT_BASE_URL = "https://beta.nextballup.com";

    for (const Page of [ProductPage, UseCasesPage, SecurityPage, FaqPage]) {
      const { unmount } = render(<Page />);
      expect(
        (screen.getByTestId("header-signin") as HTMLAnchorElement).getAttribute(
          "href",
        ),
      ).toBe("https://beta.nextballup.com/login");
      const sameOriginAuth = screen.queryAllByRole("link").filter((link) => {
        const href = link.getAttribute("href") ?? "";
        return href === "/register" || href === "/login";
      });
      expect(sameOriginAuth).toHaveLength(0);
      unmount();
    }
  });
});
