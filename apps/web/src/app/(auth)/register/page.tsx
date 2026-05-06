"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { apiJson } from "@/lib/api-client";
import {
  clearEmailVerificationRetryNeeded,
  markEmailVerificationRetryNeeded,
} from "@/lib/email-verification-state";
import { ApiError } from "@/lib/errors";
import { useRetryAfterGate } from "@/lib/retry-after";
import type {
  RegisterResponse,
  RequestEmailVerificationResponse,
  RegistrationStatusResponse,
} from "@/app/(auth)/types";

type Role = "coach" | "player";

const PASSWORD_HINT =
  "At least 8 characters, including one digit and one uppercase letter.";

export default function RegisterPage() {
  const router = useRouter();
  const [role, setRole] = useState<Role>("coach");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [institution, setInstitution] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<RegistrationStatusResponse | null>(null);
  const { retryAfterSeconds, retryBlocked, startRetryAfter } = useRetryAfterGate();

  useEffect(() => {
    let cancelled = false;
    apiJson<RegistrationStatusResponse>("/auth/registration/status", {
      method: "GET",
      cache: "no-store",
      noRefreshOn401: true,
    })
      .then((response) => {
        if (!cancelled) setStatus(response);
      })
      .catch(() => {
        if (!cancelled) {
          setStatus({
            mode: "disabled",
            invite_code_required: false,
            is_open_to_public: false,
          });
          setError("Registration status is unavailable. Please try again later.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const inviteRequired = status?.invite_code_required ?? false;
  const statusPending = status === null;
  const registrationDisabled = status?.mode === "disabled";
  const submitLabel = statusPending
    ? "Checking registration"
    : registrationDisabled
      ? "Registration closed"
      : retryBlocked
        ? `Try again in ${retryAfterSeconds}s`
        : submitting
          ? "Creating account…"
          : "Create account";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (retryBlocked || statusPending || registrationDisabled) return;
    setSubmitting(true);
    setError(null);
    try {
      await apiJson<RegisterResponse>("/auth/register", {
        method: "POST",
        json: {
          email,
          password,
          full_name: fullName,
          role,
          institution: institution || undefined,
          invite_code: inviteRequired ? inviteCode : undefined,
        },
        noRefreshOn401: true,
      });
      try {
        await apiJson<RequestEmailVerificationResponse>("/auth/email/verify/request", {
          method: "POST",
          json: {},
        });
        clearEmailVerificationRetryNeeded(email);
      } catch {
        // The signed-in app shell exposes a retry control. Registration should
        // not strand the user if the transactional provider is briefly down.
        markEmailVerificationRetryNeeded(email);
      }
      router.replace("/games");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
        startRetryAfter(err.retryAfterMs);
      } else {
        setError("Unable to create account. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Create account</h1>
        <p className="mt-1 text-sm text-[color:var(--color-nbu-text-muted)]">
          Coaches create teams and upload film. Players join via invite.
        </p>
      </div>
      {(statusPending || registrationDisabled) && (
        <p
          role="status"
          className="rounded-md border border-[color:var(--color-nbu-border)] p-3 text-sm text-[color:var(--color-nbu-text-muted)]"
        >
          {statusPending
            ? "Checking registration availability."
            : "Registration is currently closed on this deployment. If you have an existing account, you can still sign in."}
        </p>
      )}
      <fieldset className="grid grid-cols-2 gap-2 rounded-md border border-[color:var(--color-nbu-border)] p-1">
        <legend className="sr-only">Account type</legend>
        {(["coach", "player"] as Role[]).map((option) => (
          <label
            key={option}
            htmlFor={`role-${option}`}
            className={`cursor-pointer rounded px-3 py-1.5 text-center text-sm font-medium transition focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-[color:var(--color-nbu-text)] ${
              role === option
                ? "bg-[color:var(--color-nbu-text)] text-[color:var(--color-nbu-bg)]"
                : "text-[color:var(--color-nbu-text-muted)]"
            }`}
          >
            <input
              id={`role-${option}`}
              type="radio"
              name="role"
              value={option}
              checked={role === option}
              onChange={() => setRole(option)}
              className="sr-only"
            />
            {option === "coach" ? "I'm a coach" : "I'm a player"}
          </label>
        ))}
      </fieldset>
      <form className="space-y-4" onSubmit={handleSubmit} aria-label="Create account">
        <label className="block space-y-1">
          <span className="text-sm font-medium">Full name</span>
          <input
            required
            autoComplete="name"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
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
        <label className="block space-y-1">
          <span className="text-sm font-medium">Password</span>
          <input
            type="password"
            required
            autoComplete="new-password"
            minLength={8}
            maxLength={72}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          />
          <span className="block text-xs text-[color:var(--color-nbu-text-muted)]">
            {PASSWORD_HINT}
          </span>
        </label>
        <label className="block space-y-1">
          <span className="text-sm font-medium">
            Institution{" "}
            <span className="font-normal text-[color:var(--color-nbu-text-muted)]">
              (optional)
            </span>
          </span>
          <input
            value={institution}
            onChange={(e) => setInstitution(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        {inviteRequired && (
          <label className="block space-y-1">
            <span className="text-sm font-medium">Invite code</span>
            <input
              required
              autoComplete="off"
              value={inviteCode}
              onChange={(e) => setInviteCode(e.target.value)}
              className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
            />
            <span className="block text-xs text-[color:var(--color-nbu-text-muted)]">
              Pilot users received this code from NextBallUp directly.
            </span>
          </label>
        )}
        {error && (
          <p role="alert" className="text-sm text-[color:var(--color-nbu-error)]">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={submitting || retryBlocked || statusPending || registrationDisabled}
          className="w-full rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90 disabled:opacity-50"
        >
          {submitLabel}
        </button>
      </form>
      <p className="text-center text-sm text-[color:var(--color-nbu-text-muted)]">
        Have an account?{" "}
        <Link href="/login" className="font-medium underline">
          Sign in
        </Link>
      </p>
    </div>
  );
}
