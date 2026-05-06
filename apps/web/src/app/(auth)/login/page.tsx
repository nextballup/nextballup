"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useState, type FormEvent } from "react";
import { apiJson } from "@/lib/api-client";
import { ApiError } from "@/lib/errors";
import { useRetryAfterGate } from "@/lib/retry-after";
import type { LoginResponse } from "@/app/(auth)/types";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [mfaRequired, setMfaRequired] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { retryAfterSeconds, retryBlocked, startRetryAfter } = useRetryAfterGate();

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (retryBlocked) return;
    setSubmitting(true);
    setError(null);
    try {
      await apiJson<LoginResponse>("/auth/login", {
        method: "POST",
        json: {
          email,
          password,
          ...(mfaCode ? { mfa_code: mfaCode } : {}),
        },
        noRefreshOn401: true,
      });
      router.replace("/games");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === "MFA_REQUIRED") {
          setMfaRequired(true);
          setError("Enter your authenticator or recovery code.");
        } else {
          setError(err.message);
        }
        startRetryAfter(err.retryAfterMs);
      } else {
        setError("Unable to sign in. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
        <p className="mt-1 text-sm text-[color:var(--color-nbu-text-muted)]">
          Use your NextBallUp credentials.
        </p>
      </div>
      <form className="space-y-4" onSubmit={handleSubmit} aria-label="Sign in">
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
        {mfaRequired && (
          <label className="block space-y-1">
            <span className="text-sm font-medium">Authenticator or recovery code</span>
            <input
              type="text"
              required
              autoComplete="one-time-code"
              minLength={4}
              maxLength={32}
              value={mfaCode}
              onChange={(e) => setMfaCode(e.target.value)}
              className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
            />
          </label>
        )}
        <div className="space-y-1">
          <div className="flex items-center justify-between gap-3">
            <label htmlFor="password" className="text-sm font-medium">
              Password
            </label>
            <Link
              href="/forgot-password"
              className="text-xs font-medium text-[color:var(--color-nbu-text-muted)] underline"
            >
              Forgot password?
            </Link>
          </div>
          <input
            id="password"
            type="password"
            required
            autoComplete="current-password"
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </div>
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
              ? "Signing in…"
              : "Sign in"}
        </button>
      </form>
      <p className="text-center text-sm text-[color:var(--color-nbu-text-muted)]">
        Need an account?{" "}
        <Link href="/register" className="font-medium underline">
          Create one
        </Link>
      </p>
    </div>
  );
}
