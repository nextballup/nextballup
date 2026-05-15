import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ThemeToggle } from "@/components/theme-toggle";

describe("ThemeToggle", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  it("toggles between light and dark and persists the choice across remounts", async () => {
    await act(async () => {
      render(<ThemeToggle />);
    });

    const button = screen.getByTestId("theme-toggle");
    // First click flips to dark (jsdom defaults to light prefers-color-scheme).
    fireEvent.click(button);
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(window.localStorage.getItem("nbu-theme")).toBe("dark");

    // Unmount and remount; the saved preference should still be applied.
    const { unmount } = render(<ThemeToggle />);
    unmount();

    await act(async () => {
      render(<ThemeToggle />);
    });
    // A fresh toggle starts from the stored value; clicking flips to light.
    const refreshed = screen.getAllByTestId("theme-toggle").at(-1)!;
    fireEvent.click(refreshed);
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(window.localStorage.getItem("nbu-theme")).toBe("light");
  });
});
