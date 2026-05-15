"use client";

import { useState, type FormEvent } from "react";
import { apiJson } from "@/lib/api-client";
import { ApiError } from "@/lib/errors";

const ROLE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "head_coach", label: "Head coach" },
  { value: "assistant_coach", label: "Assistant coach" },
  { value: "trainer", label: "Skills trainer" },
  { value: "program_director", label: "Program director" },
  { value: "other", label: "Something else" },
];

type Submission = {
  full_name: string;
  email: string;
  role: string;
  organization?: string | null;
  message?: string | null;
};

export function PilotInterestForm() {
  const [status, setStatus] = useState<"idle" | "submitting" | "ok" | "error">(
    "idle",
  );
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setStatus("submitting");
    setErrorMessage(null);

    const form = event.currentTarget;
    const formData = new FormData(form);
    const payload: Submission = {
      full_name: String(formData.get("full_name") ?? "").trim(),
      email: String(formData.get("email") ?? "").trim(),
      role: String(formData.get("role") ?? "").trim(),
      organization: emptyToNull(formData.get("organization")),
      message: emptyToNull(formData.get("message")),
    };

    try {
      await apiJson<{ status: string }>("/pilot-interest", {
        method: "POST",
        json: payload,
      });
      setStatus("ok");
      form.reset();
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setErrorMessage(
          "Too many requests from this network. Please try again in a little while.",
        );
      } else if (err instanceof ApiError && err.status === 422) {
        setErrorMessage(
          "Some fields were not accepted. Double-check email and role, then try again.",
        );
      } else {
        setErrorMessage(
          "We could not record your submission. Please try again, or check back in a few minutes.",
        );
      }
      setStatus("error");
    }
  };

  if (status === "ok") {
    return (
      <div
        role="status"
        data-testid="pilot-success"
        className="space-y-2 text-sm"
      >
        <p className="font-semibold">Got it — submission received.</p>
        <p className="text-[color:var(--color-nbu-text-muted)]">
          We will reach out when a pilot slot opens. No automated email signup,
          no sales follow-up.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" aria-label="Request pilot access">
      <Field label="Full name" htmlFor="full_name">
        <input
          id="full_name"
          name="full_name"
          type="text"
          required
          minLength={1}
          maxLength={120}
          autoComplete="name"
          className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-transparent px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--color-nbu-text)]"
        />
      </Field>
      <Field label="Email" htmlFor="email">
        <input
          id="email"
          name="email"
          type="email"
          required
          autoComplete="email"
          className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-transparent px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--color-nbu-text)]"
        />
      </Field>
      <Field label="Role" htmlFor="role">
        <select
          id="role"
          name="role"
          required
          defaultValue="head_coach"
          className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-transparent px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--color-nbu-text)]"
        >
          {ROLE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Organization (optional)" htmlFor="organization">
        <input
          id="organization"
          name="organization"
          type="text"
          maxLength={160}
          autoComplete="organization"
          className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-transparent px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--color-nbu-text)]"
        />
      </Field>
      <Field label="Anything else? (optional)" htmlFor="message">
        <textarea
          id="message"
          name="message"
          rows={4}
          maxLength={2000}
          className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-transparent px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--color-nbu-text)]"
        />
      </Field>
      {errorMessage ? (
        <p role="alert" className="text-sm text-[color:var(--color-nbu-error)]">
          {errorMessage}
        </p>
      ) : null}
      <button
        type="submit"
        disabled={status === "submitting"}
        data-testid="pilot-submit"
        className="rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90 disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--color-nbu-text)]"
      >
        {status === "submitting" ? "Sending..." : "Request pilot access"}
      </button>
    </form>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <label htmlFor={htmlFor} className="block space-y-1 text-sm">
      <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
        {label}
      </span>
      {children}
    </label>
  );
}

function emptyToNull(value: FormDataEntryValue | null): string | null {
  if (value === null) return null;
  const trimmed = String(value).trim();
  return trimmed ? trimmed : null;
}
