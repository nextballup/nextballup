import Link from "next/link";
import { redirect } from "next/navigation";
import { serverApiJson } from "@/lib/api-server";
import { ApiError } from "@/lib/errors";
import type { GameListResponse, GameStatus } from "@/lib/contract";

const PER_PAGE = 20;

const STATUS_LABELS: Record<GameStatus, string> = {
  scheduled: "Scheduled",
  uploading: "Uploading",
  processing: "Processing",
  completed: "Completed",
  failed: "Failed",
};

export default async function GamesPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string; status?: string }>;
}) {
  const params = await searchParams;
  const page = Math.max(1, Number.parseInt(params.page ?? "1", 10) || 1);
  const statusFilter = params.status as GameStatus | undefined;

  const query = new URLSearchParams({
    page: String(page),
    per_page: String(PER_PAGE),
  });
  if (statusFilter) {
    query.set("status", statusFilter);
  }

  let list: GameListResponse;
  try {
    list = await serverApiJson<GameListResponse>(`/games?${query.toString()}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      redirect("/login");
    }
    throw error;
  }

  return (
    <section className="space-y-6">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Games</h1>
          <p className="text-sm text-[color:var(--color-nbu-text-muted)]">
            {list.total} game{list.total === 1 ? "" : "s"}
            {statusFilter ? ` · ${STATUS_LABELS[statusFilter]}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <nav aria-label="Filter by status" className="flex flex-wrap gap-1 text-xs">
            <Link
              href="/games"
              className={`rounded-full border px-3 py-1 transition ${
                !statusFilter
                  ? "border-[color:var(--color-nbu-text)]"
                  : "border-[color:var(--color-nbu-border)] text-[color:var(--color-nbu-text-muted)]"
              }`}
            >
              All
            </Link>
            {(Object.entries(STATUS_LABELS) as [GameStatus, string][]).map(
              ([value, label]) => (
                <Link
                  key={value}
                  href={`/games?status=${value}`}
                  className={`rounded-full border px-3 py-1 transition ${
                    statusFilter === value
                      ? "border-[color:var(--color-nbu-text)]"
                      : "border-[color:var(--color-nbu-border)] text-[color:var(--color-nbu-text-muted)]"
                  }`}
                >
                  {label}
                </Link>
              ),
            )}
          </nav>
          <Link
            href="/games/new"
            className="rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90"
          >
            + Create game
          </Link>
        </div>
      </header>

      {list.games.length === 0 ? (
        <div className="space-y-3 rounded-md border border-dashed border-[color:var(--color-nbu-border)] px-4 py-8 text-center text-sm text-[color:var(--color-nbu-text-muted)]">
          <p>No games yet.</p>
          <p>
            <Link href="/games/new" className="font-medium underline">
              Create a game
            </Link>{" "}
            or{" "}
            <Link href="/teams" className="font-medium underline">
              manage your teams
            </Link>
            .
          </p>
        </div>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {list.games.map((game) => (
            <li key={game.id}>
              <Link
                href={`/games/${game.id}`}
                className="flex flex-col gap-2 rounded-lg border border-[color:var(--color-nbu-border)] p-4 transition hover:border-[color:var(--color-nbu-text)]"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">
                    {game.is_home ? "vs" : "@"}{" "}
                    {game.opponent_name ?? "(opponent TBD)"}
                  </span>
                  <span className="rounded-full border border-[color:var(--color-nbu-border)] px-2 py-0.5 text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
                    {STATUS_LABELS[game.status]}
                  </span>
                </div>
                <div className="flex items-center justify-between text-xs text-[color:var(--color-nbu-text-muted)]">
                  <span>{game.date}</span>
                  <span>{game.location ?? ""}</span>
                </div>
                {game.score_team != null && game.score_opponent != null && (
                  <div className="font-mono text-sm">
                    {game.score_team} – {game.score_opponent}
                  </div>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}

      {(page > 1 || list.has_next) && (
        <nav
          aria-label="Pagination"
          className="flex items-center justify-between text-sm"
        >
          {page > 1 ? (
            <Link
              href={{
                pathname: "/games",
                query: {
                  page: String(page - 1),
                  ...(statusFilter ? { status: statusFilter } : {}),
                },
              }}
              className="rounded-md border border-[color:var(--color-nbu-border)] px-3 py-1.5 text-sm transition hover:border-[color:var(--color-nbu-text)]"
            >
              ← Previous
            </Link>
          ) : (
            <span />
          )}
          <span className="text-[color:var(--color-nbu-text-muted)]">
            Page {list.page} of {Math.max(1, Math.ceil(list.total / list.per_page))}
          </span>
          {list.has_next ? (
            <Link
              href={{
                pathname: "/games",
                query: {
                  page: String(page + 1),
                  ...(statusFilter ? { status: statusFilter } : {}),
                },
              }}
              className="rounded-md border border-[color:var(--color-nbu-border)] px-3 py-1.5 text-sm transition hover:border-[color:var(--color-nbu-text)]"
            >
              Next →
            </Link>
          ) : (
            <span />
          )}
        </nav>
      )}
    </section>
  );
}
