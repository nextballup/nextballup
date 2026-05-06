"use client";

import { useEffect, useState } from "react";
import { apiJson } from "@/lib/api-client";
import { ApiError } from "@/lib/errors";
import type {
  EmailVerificationStatusResponse,
  RequestEmailVerificationResponse,
} from "@/app/(auth)/types";

type BannerState = {
  isVerified: boolean;
  pendingRequest: boolean;
};

export function EmailVerificationBanner({ email }: { email: string }) {
  const [state, setState] = useState<BannerState | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiJson<EmailVerificationStatusResponse>("/auth/email/verify/status", {
      method: "GET",
      cache: "no-store",
    })
      .then((status) => {
        if (!cancelled) {
          setState({
            isVerified: status.is_verified,
            pendingRequest: status.pending_request,
          });
        }
      })
      .catch(() => {
        if (!cancelled) setState({ isVerified: true, pendingRequest: false });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function requestVerification() {
    setSubmitting(true);
    setError(null);
    setMessage(null);
    try {
      await apiJson<RequestEmailVerificationResponse>("/auth/email/verify/request", {
        method: "POST",
        json: {},
      });
      setState({ isVerified: false, pendingRequest: true });
      setMessage(`Verification email sent to ${email}.`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Unable to send verification email.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (state === null || state.isVerified) {
    return null;
  }

  return (
    <div className="border-b border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)]">
      <div className="mx-auto flex max-w-5xl flex-col gap-3 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-1">
          <p className="font-medium">Verify your email to unlock team creation.</p>
          <p className="text-[color:var(--color-nbu-text-muted)]">
            {message ??
              (state.pendingRequest
                ? `Check your inbox at ${email}.`
                : `Send a verification link to ${email}.`)}
          </p>
          {error && (
            <p role="alert" className="text-[color:var(--color-nbu-error)]">
              {error}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={requestVerification}
          disabled={submitting}
          className="w-fit rounded-md border border-[color:var(--color-nbu-border)] px-3 py-1.5 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)] disabled:opacity-50"
        >
          {submitting
            ? "Sending..."
            : state.pendingRequest
              ? "Resend verification"
              : "Send verification email"}
        </button>
      </div>
    </div>
  );
}
