"use client";

import { useEffect } from "react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Global application error:", error);
  }, [error]);

  return (
    <html lang="en">
      <body>
        <main className="mx-auto flex min-h-screen w-full max-w-5xl items-center px-4 font-sans">
          <section
            role="alert"
            className="space-y-4 rounded-lg border border-slate-300 p-6 text-sm text-slate-900"
          >
            <h1 className="text-2xl font-semibold tracking-tight">
              Application error
            </h1>
            <p className="max-w-xl text-slate-600">
              The app could not finish loading. Retry the page, then sign in
              again if your session expired.
            </p>
            {error.digest && (
              <p className="font-mono text-xs text-slate-500">
                Reference: {error.digest}
              </p>
            )}
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={reset}
                className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white"
              >
                Try again
              </button>
              <a
                href="/login"
                className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium"
              >
                Sign in
              </a>
            </div>
          </section>
        </main>
      </body>
    </html>
  );
}
