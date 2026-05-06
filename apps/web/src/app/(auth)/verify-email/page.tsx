"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { apiJson } from "@/lib/api-client";
import { ApiError } from "@/lib/errors";
import type { ConfirmEmailVerificationResponse } from "@/app/(auth)/types";

function VerifyEmailContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const requested = useRef(false);
  const [complete, setComplete] = useState(false);
  const [error, setError] = useState<string | null>(token ? null : "Verification link is missing.");

  useEffect(() => {
    if (!token || requested.current) return;
    requested.current = true;
    apiJson<ConfirmEmailVerificationResponse>("/auth/email/verify/confirm", {
      method: "POST",
      json: { token },
      noRefreshOn401: true,
    })
      .then(() => {
        setComplete(true);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError) {
          setError(err.message);
        } else {
          setError("Unable to verify email. Please request a new link.");
        }
      });
  }, [token]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Verify email</h1>
        <p className="mt-1 text-sm text-[color:var(--color-nbu-text-muted)]">
          Confirm your email address for NextBallUp.
        </p>
      </div>
      {complete && (
        <p role="status" className="text-sm text-[color:var(--color-nbu-text-muted)]">
          Email verified. You can continue to your workspace.
        </p>
      )}
      {!complete && !error && (
        <p role="status" className="text-sm text-[color:var(--color-nbu-text-muted)]">
          Checking verification link.
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-[color:var(--color-nbu-error)]">
          {error}
        </p>
      )}
      <p className="text-center text-sm text-[color:var(--color-nbu-text-muted)]">
        <Link href="/games" className="font-medium underline">
          Go to workspace
        </Link>
      </p>
    </div>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={null}>
      <VerifyEmailContent />
    </Suspense>
  );
}
