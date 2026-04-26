"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { apiJson } from "@/lib/api-client";
import { ApiError } from "@/lib/errors";
import type { GameSummary, GameType, TeamListEntry } from "@/lib/contract";

const GAME_TYPES: GameType[] = [
  "scrimmage",
  "preseason",
  "regular_season",
  "tournament",
  "playoff",
  "practice",
  "film_exchange",
];

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

export function CreateGameForm({
  teams,
  defaultTeamId,
}: {
  teams: TeamListEntry[];
  defaultTeamId: string | null;
}) {
  const router = useRouter();
  const [teamId, setTeamId] = useState(defaultTeamId ?? teams[0]?.id ?? "");
  const [opponent, setOpponent] = useState("");
  const [gameType, setGameType] = useState<GameType>("regular_season");
  const [date, setDate] = useState(todayISO());
  const [time, setTime] = useState("");
  const [location, setLocation] = useState("");
  const [isHome, setIsHome] = useState(true);
  const [periods, setPeriods] = useState(4);
  const [periodLength, setPeriodLength] = useState(8);
  const [shotClockEnabled, setShotClockEnabled] = useState(false);
  const [shotClockSeconds, setShotClockSeconds] = useState(30);
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const created = await apiJson<GameSummary>("/games", {
        method: "POST",
        json: {
          team_id: teamId,
          opponent_name: opponent.trim() || null,
          game_type: gameType,
          date,
          time: time || null,
          location: location.trim() || null,
          is_home: isHome,
          periods,
          period_length_minutes: periodLength,
          shot_clock_enabled: shotClockEnabled,
          shot_clock_seconds: shotClockEnabled ? shotClockSeconds : null,
          notes: notes.trim() || null,
        },
      });
      router.replace(`/games/${created.id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(
          err.status === 403
            ? "You need coach access on this team to create games."
            : err.message,
        );
      } else {
        setError("Could not create game.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-label="Create game"
      className="space-y-4 rounded-lg border border-[color:var(--color-nbu-border)] p-4"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block space-y-1 text-sm sm:col-span-2">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Team
          </span>
          <select
            required
            value={teamId}
            onChange={(e) => setTeamId(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          >
            {teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} · {t.season}
              </option>
            ))}
          </select>
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Opponent
          </span>
          <input
            maxLength={255}
            value={opponent}
            onChange={(e) => setOpponent(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Game type
          </span>
          <select
            value={gameType}
            onChange={(e) => setGameType(e.target.value as GameType)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          >
            {GAME_TYPES.map((t) => (
              <option key={t} value={t}>
                {t.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Date
          </span>
          <input
            required
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 font-mono outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Tip-off{" "}
            <span className="font-normal text-[color:var(--color-nbu-text-muted)]">
              (optional)
            </span>
          </span>
          <input
            type="time"
            value={time}
            onChange={(e) => setTime(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 font-mono outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        <label className="block space-y-1 text-sm sm:col-span-2">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Location
          </span>
          <input
            maxLength={255}
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        <label className="flex items-center gap-2 text-sm sm:col-span-2">
          <input
            type="checkbox"
            checked={isHome}
            onChange={(e) => setIsHome(e.target.checked)}
          />
          <span>Home game</span>
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Periods
          </span>
          <input
            type="number"
            min={1}
            max={10}
            value={periods}
            onChange={(e) => setPeriods(Number.parseInt(e.target.value, 10) || 4)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 font-mono outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Period length (min)
          </span>
          <input
            type="number"
            min={1}
            max={60}
            value={periodLength}
            onChange={(e) =>
              setPeriodLength(Number.parseInt(e.target.value, 10) || 8)
            }
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 font-mono outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={shotClockEnabled}
            onChange={(e) => setShotClockEnabled(e.target.checked)}
          />
          <span>Shot clock</span>
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Shot clock (sec)
          </span>
          <input
            type="number"
            min={1}
            max={35}
            disabled={!shotClockEnabled}
            value={shotClockSeconds}
            onChange={(e) =>
              setShotClockSeconds(Number.parseInt(e.target.value, 10) || 30)
            }
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 font-mono outline-none focus:border-[color:var(--color-nbu-text)] disabled:opacity-50"
          />
        </label>
        <label className="block space-y-1 text-sm sm:col-span-2">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Notes{" "}
            <span className="font-normal text-[color:var(--color-nbu-text-muted)]">
              (optional)
            </span>
          </span>
          <textarea
            rows={3}
            maxLength={2000}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
      </div>

      {error && (
        <p role="alert" className="text-sm text-[color:var(--color-nbu-error)]">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={submitting || !teamId}
        className="rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90 disabled:opacity-50"
      >
        {submitting ? "Creating…" : "Create game"}
      </button>
    </form>
  );
}
