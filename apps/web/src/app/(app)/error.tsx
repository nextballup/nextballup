"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { apiVoid } from "@/lib/api-client";

export default function AuthenticatedSegmentError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();

  useEffect(() => {
    // Surface unexpected failures in the browser console for dev; in prod
    // Next.js will have already sent the digest to the server logs.
    console.error("Authenticated segment error:", error);
  }, [error]);

  async function signInAgain() {
    try {
      await apiVoid("/auth/logout", {
        method: "POST",
        noRefreshOn401: true,
      });
    } finally {
      queryClient.clear();
      router.replace("/login");
    }
  }

  return (
    <section
      role="alert"
      className="mx-auto max-w-xl space-y-4 rounded-lg border border-[color:var(--color-nbu-border)] p-6 text-sm"
    >
      <h1 className="text-lg font-semibold tracking-tight">
        Something went wrong.
      </h1>
      <p className="text-[color:var(--color-nbu-text-muted)]">
        We couldn&rsquo;t load this page. The backend may be unreachable, or
        the request failed in an unexpected way. Retry once, then sign in
        again if your session expired.
      </p>
      {error.digest && (
        <p className="font-mono text-xs text-[color:var(--color-nbu-text-muted)]">
          Reference: {error.digest}
        </p>
      )}
      <div className="flex gap-3">
        <button
          type="button"
          onClick={reset}
          className="rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90"
        >
          Try again
        </button>
        <button
          type="button"
          onClick={signInAgain}
          className="rounded-md border border-[color:var(--color-nbu-border)] px-4 py-2 text-sm font-medium transition hover:border-[color:var(--color-nbu-text)]"
        >
          Sign in again
        </button>
      </div>
    </section>
  );
}
