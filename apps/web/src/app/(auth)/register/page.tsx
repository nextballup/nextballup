"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useState, type FormEvent } from "react";
import { apiJson } from "@/lib/api-client";
import { ApiError } from "@/lib/errors";
import { useRetryAfterGate } from "@/lib/retry-after";
import type { RegisterResponse } from "@/app/(auth)/types";

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
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { retryAfterSeconds, retryBlocked, startRetryAfter } = useRetryAfterGate();

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (retryBlocked) return;
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
        },
        noRefreshOn401: true,
      });
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
      <div
        role="radiogroup"
        aria-label="Account type"
        className="grid grid-cols-2 gap-2 rounded-md border border-[color:var(--color-nbu-border)] p-1"
      >
        {(["coach", "player"] as Role[]).map((option) => (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={role === option}
            onClick={() => setRole(option)}
            className={`rounded px-3 py-1.5 text-sm font-medium transition ${
              role === option
                ? "bg-[color:var(--color-nbu-text)] text-[color:var(--color-nbu-bg)]"
                : "text-[color:var(--color-nbu-text-muted)]"
            }`}
          >
            {option === "coach" ? "I'm a coach" : "I'm a player"}
          </button>
        ))}
      </div>
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
              ? "Creating account…"
              : "Create account"}
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
