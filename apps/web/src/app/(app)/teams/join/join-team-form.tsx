"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { ACTIVE_TEAM_COOKIE } from "@/lib/active-team";
import { apiJson } from "@/lib/api-client";
import { ApiError } from "@/lib/errors";
import type { JoinTeamResponse } from "@/lib/contract";

function translateJoinError(err: ApiError): string {
  switch (err.code) {
    case "INVITE_NOT_FOUND":
      return "That invite code doesn't match any team.";
    case "INVITE_EXPIRED":
      return "That invite has expired. Ask your coach for a fresh one.";
    case "INVITE_EXHAUSTED":
      return "That invite has reached its usage limit.";
    case "INVITE_INACTIVE":
      return "That invite is no longer active.";
    case "INVITE_ROLE_MISMATCH":
      return "This invite is for a different account type (coach vs player).";
    case "JERSEY_NUMBER_REQUIRED":
      return "Players must set a jersey number to join.";
    case "JERSEY_NUMBER_TAKEN":
      return "Jersey number is already taken on this team. Pick another.";
    case "ALREADY_MEMBER":
      return "You are already a member of this team.";
    case "RATE_LIMITED":
      return "Too many join attempts. Wait a minute and try again.";
    default:
      return err.message;
  }
}

export function JoinTeamForm({ initialCode }: { initialCode: string }) {
  const router = useRouter();
  const [inviteCode, setInviteCode] = useState(initialCode.toUpperCase());
  const [jerseyNumber, setJerseyNumber] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    const parsedJersey =
      jerseyNumber === "" ? null : Number.parseInt(jerseyNumber, 10);
    if (jerseyNumber !== "" && Number.isNaN(parsedJersey)) {
      setError("Jersey number must be a number.");
      setSubmitting(false);
      return;
    }
    try {
      const joined = await apiJson<JoinTeamResponse>("/teams/join", {
        method: "POST",
        json: {
          invite_code: inviteCode.trim(),
          jersey_number: parsedJersey,
        },
      });
      document.cookie = `${ACTIVE_TEAM_COOKIE}=${encodeURIComponent(joined.id)}; Path=/; Max-Age=${60 * 60 * 24 * 30}; SameSite=Lax`;
      router.refresh();
      router.replace(`/teams/${joined.id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(translateJoinError(err));
      } else {
        setError("Could not join team.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-label="Join team"
      className="space-y-4 rounded-lg border border-[color:var(--color-nbu-border)] p-4"
    >
      <label className="block space-y-1 text-sm">
        <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
          Invite code
        </span>
        <input
          required
          minLength={4}
          maxLength={20}
          autoComplete="off"
          value={inviteCode}
          onChange={(e) => setInviteCode(e.target.value.toUpperCase())}
          className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 font-mono outline-none focus:border-[color:var(--color-nbu-text)]"
        />
      </label>
      <label className="block space-y-1 text-sm">
        <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
          Jersey number{" "}
          <span className="font-normal text-[color:var(--color-nbu-text-muted)]">
            (players only)
          </span>
        </span>
        <input
          type="number"
          min={0}
          max={99}
          value={jerseyNumber}
          onChange={(e) => setJerseyNumber(e.target.value)}
          className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 font-mono outline-none focus:border-[color:var(--color-nbu-text)]"
        />
      </label>
      {error && (
        <p role="alert" className="text-sm text-[color:var(--color-nbu-error)]">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={submitting}
        className="rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90 disabled:opacity-50"
      >
        {submitting ? "Joining…" : "Join team"}
      </button>
    </form>
  );
}
