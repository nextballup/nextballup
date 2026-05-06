"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { apiJson } from "@/lib/api-client";
import { ApiError } from "@/lib/errors";
import { useRetryAfterGate } from "@/lib/retry-after";
import type { PasswordResetRequestResponse } from "@/app/(auth)/types";

const SUCCESS_MESSAGE =
  "If that email belongs to an active account, a reset link is on the way.";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { retryAfterSeconds, retryBlocked, startRetryAfter } = useRetryAfterGate();

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (retryBlocked) return;
    setSubmitting(true);
    setError(null);
    try {
      await apiJson<PasswordResetRequestResponse>("/auth/password/forgot", {
        method: "POST",
        json: { email },
        noRefreshOn401: true,
      });
      setSent(true);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
        startRetryAfter(err.retryAfterMs);
      } else {
        setError("Unable to request a reset link. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Reset password</h1>
        <p className="mt-1 text-sm text-[color:var(--color-nbu-text-muted)]">
          Enter the email for your NextBallUp account.
        </p>
      </div>
      <form className="space-y-4" onSubmit={handleSubmit} aria-label="Request password reset">
        <label className="block space-y-1">
          <span className="text-sm font-medium">Email</span>
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        {sent && (
          <p role="status" className="text-sm text-[color:var(--color-nbu-text-muted)]">
            {SUCCESS_MESSAGE}
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-[color:var(--color-nbu-error)]">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={submitting || retryBlocked}
          className="w-full rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90 disabled:opacity-50"
        >
          {retryBlocked
            ? `Try again in ${retryAfterSeconds}s`
            : submitting
              ? "Sending..."
              : "Send reset link"}
        </button>
      </form>
      <p className="text-center text-sm text-[color:var(--color-nbu-text-muted)]">
        Remembered it?{" "}
        <Link href="/login" className="font-medium underline">
          Sign in
        </Link>
      </p>
    </div>
  );
}
