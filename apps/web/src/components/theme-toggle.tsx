"use client";

import { useEffect, useState } from "react";

type Theme = "light" | "dark";

const STORAGE_KEY = "nbu-theme";

function readSavedTheme(): Theme | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw === "light" || raw === "dark" ? raw : null;
  } catch {
    return null;
  }
}

function systemTheme(): Theme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
}

export function ThemeToggle() {
  // Render-time we don't know the visitor's saved preference; the inline
  // theme-script in <head> already set the document attribute before paint,
  // so the toggle reads back from there once mounted to avoid hydration
  // mismatches (server-rendered HTML has no data-theme, client may add it).
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    const saved = readSavedTheme();
    setTheme(saved ?? systemTheme());
  }, []);

  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    applyTheme(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Persistence may be unavailable (private mode); in-memory toggle still
      // works for the session.
    }
  };

  const label = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";

  return (
    <button
      type="button"
      onClick={toggle}
      data-testid="theme-toggle"
      aria-label={label}
      title={label}
      className="rounded-md border border-[color:var(--color-nbu-border)] px-2.5 py-1.5 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--color-nbu-text)]"
    >
      <span aria-hidden="true">{theme === "dark" ? "Light" : "Dark"}</span>
    </button>
  );
}
