"use client";

import { useState, type FormEvent } from "react";
import { apiJson } from "@/lib/api-client";
import { ApiError } from "@/lib/errors";
import type { CreateInviteResponse, TeamRole } from "@/lib/contract";

const INVITE_ROLES: TeamRole[] = [
  "player",
  "assistant_coach",
  "manager",
];

/**
 * Coach-focused invite surface. Non-coaches don't see the "generate invite"
 * button because the backend will 403 the POST — we optimistically render
 * the controls and surface the 403 message if the caller isn't authorized,
 * rather than duplicating authz on the client.
 *
 * `defaultInviteCode` is the team's always-on code from the Team row; the
 * generated invites listed below it are time-bounded and role-scoped.
 */
export function InvitePanel({
  teamId,
  defaultInviteCode,
}: {
  teamId: string;
  defaultInviteCode: string;
}) {
  const [role, setRole] = useState<TeamRole>("player");
  const [maxUses, setMaxUses] = useState(20);
  const [expiresInDays, setExpiresInDays] = useState(14);
  const [issuing, setIssuing] = useState(false);
  const [generated, setGenerated] = useState<CreateInviteResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIssuing(true);
    setError(null);
    setCopied(null);
    try {
      const response = await apiJson<CreateInviteResponse>(
        `/teams/${teamId}/invite`,
        {
          method: "POST",
          json: {
            role,
            max_uses: maxUses,
            expires_in_days: expiresInDays,
          },
        },
      );
      setGenerated(response);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(
          err.status === 403
            ? "You need coach access on this team to issue invites."
            : err.message,
        );
      } else {
        setError("Could not create invite.");
      }
    } finally {
      setIssuing(false);
    }
  }

  async function copy(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(label);
      window.setTimeout(() => setCopied(null), 2_000);
    } catch {
      setCopied(null);
    }
  }

  return (
    <section
      aria-labelledby="invite-heading"
      className="space-y-4 rounded-lg border border-[color:var(--color-nbu-border)] p-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2
            id="invite-heading"
            className="text-sm font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]"
          >
            Team invite
          </h2>
          <p className="mt-1 text-xs text-[color:var(--color-nbu-text-muted)]">
            Share the code with players to let them join. Generate a scoped
            invite below for assistant coaches or time-bounded access.
          </p>
        </div>
      </div>

      <div
        data-testid="default-invite-code"
        className="flex items-center justify-between gap-3 rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2"
      >
        <div>
          <div className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Default team code
          </div>
          <div className="mt-0.5 font-mono text-base font-medium">
            {defaultInviteCode}
          </div>
        </div>
        <button
          type="button"
          onClick={() => copy(defaultInviteCode, "default")}
          className="rounded-md border border-[color:var(--color-nbu-border)] px-3 py-1.5 text-xs transition hover:border-[color:var(--color-nbu-text)]"
        >
          {copied === "default" ? "Copied" : "Copy"}
        </button>
      </div>

      <form
        onSubmit={handleSubmit}
        aria-label="Generate team invite"
        className="grid gap-3 sm:grid-cols-4"
      >
        <label className="space-y-1 text-xs sm:col-span-2">
          <span className="uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Role
          </span>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as TeamRole)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 text-sm outline-none focus:border-[color:var(--color-nbu-text)]"
          >
            {INVITE_ROLES.map((r) => (
              <option key={r} value={r}>
                {r.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-xs">
          <span className="uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Max uses
          </span>
          <input
            type="number"
            min={1}
            max={1000}
            value={maxUses}
            onChange={(e) =>
              setMaxUses(Math.max(1, Number.parseInt(e.target.value, 10) || 1))
            }
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 text-sm outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        <label className="space-y-1 text-xs">
          <span className="uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Expires in (days)
          </span>
          <input
            type="number"
            min={1}
            max={365}
            value={expiresInDays}
            onChange={(e) =>
              setExpiresInDays(
                Math.max(1, Number.parseInt(e.target.value, 10) || 1),
              )
            }
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 text-sm outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        <div className="flex items-end sm:col-span-4">
          <button
            type="submit"
            disabled={issuing}
            className="rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90 disabled:opacity-50"
          >
            {issuing ? "Creating…" : "Generate invite"}
          </button>
        </div>
      </form>

      {error && (
        <p role="alert" className="text-sm text-[color:var(--color-nbu-error)]">
          {error}
        </p>
      )}

      {generated && (
        <div
          data-testid="generated-invite"
          className="space-y-2 rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] p-3 text-sm"
        >
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
                New invite code
              </div>
              <div className="mt-0.5 font-mono text-base font-medium">
                {generated.invite_code}
              </div>
            </div>
            <button
              type="button"
              onClick={() => copy(generated.invite_code, "generated-code")}
              className="rounded-md border border-[color:var(--color-nbu-border)] px-3 py-1.5 text-xs transition hover:border-[color:var(--color-nbu-text)]"
            >
              {copied === "generated-code" ? "Copied" : "Copy code"}
            </button>
          </div>
          <div className="flex items-center justify-between gap-3">
            <div className="truncate text-xs text-[color:var(--color-nbu-text-muted)]">
              {generated.invite_url}
            </div>
            <button
              type="button"
              onClick={() => copy(generated.invite_url, "generated-url")}
              className="shrink-0 rounded-md border border-[color:var(--color-nbu-border)] px-3 py-1.5 text-xs transition hover:border-[color:var(--color-nbu-text)]"
            >
              {copied === "generated-url" ? "Copied" : "Copy link"}
            </button>
          </div>
          <div className="text-xs text-[color:var(--color-nbu-text-muted)]">
            Role: {generated.role.replaceAll("_", " ")} · {generated.remaining_uses}{" "}
            use{generated.remaining_uses === 1 ? "" : "s"} · expires{" "}
            {new Date(generated.expires_at).toLocaleString()}
          </div>
        </div>
      )}
    </section>
  );
}
