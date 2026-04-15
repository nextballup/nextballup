import Link from "next/link";
import { notFound } from "next/navigation";
import { serverApiOptional } from "@/lib/api-server";
import type { GameSummary } from "@/lib/contract";
import { GameEditForm } from "./game-edit-form";

export default async function GameDetailPage({
  params,
}: {
  params: Promise<{ gameId: string }>;
}) {
  const { gameId } = await params;
  const game = await serverApiOptional<GameSummary>(`/games/${gameId}`, {
    nullOnStatuses: [403, 404],
  });
  if (!game) {
    notFound();
  }

  return (
    <section className="space-y-6">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <Link
            href="/games"
            className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)] hover:underline"
          >
            ← All games
          </Link>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight">
            {game.is_home ? "vs" : "@"} {game.opponent_name ?? "(opponent TBD)"}
          </h1>
          <p className="text-sm text-[color:var(--color-nbu-text-muted)]">
            {game.date}
            {game.time ? ` · ${game.time.slice(0, 5)}` : ""}
            {game.location ? ` · ${game.location}` : ""}
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            href={`/games/${game.id}/upload`}
            className="rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90"
          >
            Upload video
          </Link>
        </div>
      </header>

      <dl className="grid grid-cols-2 gap-4 rounded-lg border border-[color:var(--color-nbu-border)] p-4 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Status
          </dt>
          <dd className="mt-1 font-medium">{game.status}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Type
          </dt>
          <dd className="mt-1 font-medium">{game.game_type.replaceAll("_", " ")}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Periods
          </dt>
          <dd className="mt-1 font-mono">
            {game.periods} × {game.period_length_minutes}m
          </dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Score
          </dt>
          <dd className="mt-1 font-mono">
            {game.score_team ?? "—"} : {game.score_opponent ?? "—"}
          </dd>
        </div>
      </dl>

      <GameEditForm game={game} />
    </section>
  );
}
