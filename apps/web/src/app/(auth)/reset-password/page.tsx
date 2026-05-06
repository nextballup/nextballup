"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { apiJson } from "@/lib/api-client";
import { ApiError } from "@/lib/errors";
import type { PasswordResetConfirmResponse } from "@/app/(auth)/types";

const PASSWORD_HINT =
  "At least 8 characters, including one digit and one uppercase letter.";

function ResetPasswordForm() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [complete, setComplete] = useState(false);
  const [error, setError] = useState<string | null>(token ? null : "Reset link is missing.");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || complete) return;
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await apiJson<PasswordResetConfirmResponse>("/auth/password/reset", {
        method: "POST",
        json: { token, new_password: password },
        noRefreshOn401: true,
      });
      setComplete(true);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Unable to reset password. Please request a new link.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Set new password</h1>
        <p className="mt-1 text-sm text-[color:var(--color-nbu-text-muted)]">
          Choose a new password for your account.
        </p>
      </div>
      <form className="space-y-4" onSubmit={handleSubmit} aria-label="Set new password">
        <div className="space-y-1">
          <label htmlFor="new-password" className="block text-sm font-medium">
            New password
          </label>
          <input
            id="new-password"
            type="password"
            required
            autoComplete="new-password"
            minLength={8}
            maxLength={72}
            aria-describedby="new-password-hint"
            value={password}
            disabled={complete || !token}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)] disabled:opacity-60"
          />
          <p id="new-password-hint" className="text-xs text-[color:var(--color-nbu-text-muted)]">
            {PASSWORD_HINT}
          </p>
        </div>
        <div className="space-y-1">
          <label htmlFor="confirm-password" className="block text-sm font-medium">
            Confirm password
          </label>
          <input
            id="confirm-password"
            type="password"
            required
            autoComplete="new-password"
            minLength={8}
            maxLength={72}
            value={confirmPassword}
            disabled={complete || !token}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)] disabled:opacity-60"
          />
        </div>
        {complete && (
          <p role="status" className="text-sm text-[color:var(--color-nbu-text-muted)]">
            Password reset. You can sign in with the new password.
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-[color:var(--color-nbu-error)]">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={submitting || complete || !token}
          className="w-full rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90 disabled:opacity-50"
        >
          {submitting ? "Resetting..." : "Reset password"}
        </button>
      </form>
      <p className="text-center text-sm text-[color:var(--color-nbu-text-muted)]">
        <Link href="/login" className="font-medium underline">
          Back to sign in
        </Link>
      </p>
    </div>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordForm />
    </Suspense>
  );
}
