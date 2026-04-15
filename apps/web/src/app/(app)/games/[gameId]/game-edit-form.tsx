"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { apiJson } from "@/lib/api-client";
import { ApiError } from "@/lib/errors";
import {
  GAME_TERMINAL_STATUSES,
  type GameStatus,
  type GameSummary,
} from "@/lib/contract";

const STATUS_OPTIONS: GameStatus[] = [
  "scheduled",
  "uploading",
  "processing",
  "completed",
  "failed",
];

function parseScore(value: string): number | null {
  if (value === "") return null;
  const parsed = Number.parseInt(value, 10);
  return Number.isNaN(parsed) ? null : parsed;
}

export function GameEditForm({ game }: { game: GameSummary }) {
  const router = useRouter();
  const [scoreTeam, setScoreTeam] = useState(
    game.score_team != null ? String(game.score_team) : "",
  );
  const [scoreOpponent, setScoreOpponent] = useState(
    game.score_opponent != null ? String(game.score_opponent) : "",
  );
  const [status, setStatus] = useState<GameStatus>(game.status);
  const [notes, setNotes] = useState(game.notes ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const wasTerminal = GAME_TERMINAL_STATUSES.includes(game.status);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setSuccess(null);
    try {
      const updated = await apiJson<GameSummary>(`/games/${game.id}`, {
        method: "PATCH",
        json: {
          score_team: parseScore(scoreTeam),
          score_opponent: parseScore(scoreOpponent),
          status,
          notes: notes || null,
        },
      });
      setSuccess(`Saved at ${new Date().toLocaleTimeString()}`);
      setScoreTeam(
        updated.score_team != null ? String(updated.score_team) : "",
      );
      setScoreOpponent(
        updated.score_opponent != null ? String(updated.score_opponent) : "",
      );
      setStatus(updated.status);
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === "GAME_TERMINAL_STATUS") {
          setError(
            "Cannot change status of a completed or failed game. Ask an admin to reopen it.",
          );
        } else {
          setError(err.message);
        }
      } else {
        setError("Unable to update game.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-label="Update game"
      className="space-y-4 rounded-lg border border-[color:var(--color-nbu-border)] p-4"
    >
      <h2 className="text-sm font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
        Update game
      </h2>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Score (us)
          </span>
          <input
            type="number"
            min={0}
            max={999}
            value={scoreTeam}
            onChange={(e) => setScoreTeam(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 font-mono outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Score (opponent)
          </span>
          <input
            type="number"
            min={0}
            max={999}
            value={scoreOpponent}
            onChange={(e) => setScoreOpponent(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 font-mono outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
      </div>

      <label className="block space-y-1 text-sm">
        <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
          Status
        </span>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value as GameStatus)}
          className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
        >
          {STATUS_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
        {wasTerminal && (
          <span className="block text-xs text-[color:var(--color-nbu-text-muted)]">
            This game is{" "}
            <span className="font-medium">{game.status}</span>. You can resubmit
            the same status to adjust other fields, but coaches cannot move it
            back to scheduled/processing; an admin has to reopen it.
          </span>
        )}
      </label>

      <label className="block space-y-1 text-sm">
        <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
          Notes
        </span>
        <textarea
          rows={3}
          maxLength={2000}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
        />
      </label>

      {error && (
        <p role="alert" className="text-sm text-[color:var(--color-nbu-error)]">
          {error}
        </p>
      )}
      {success && !error && (
        <p role="status" className="text-sm text-green-600">
          {success}
        </p>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90 disabled:opacity-50"
      >
        {submitting ? "Saving…" : "Save changes"}
      </button>
    </form>
  );
}
