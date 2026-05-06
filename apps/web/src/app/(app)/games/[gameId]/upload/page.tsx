import Link from "next/link";
import { notFound } from "next/navigation";
import { serverApiOptional } from "@/lib/api-server";
import type { GameSummary } from "@/lib/contract";
import { UploadFlow } from "./upload-flow";

export default async function GameUploadPage({
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
      <nav aria-label="Breadcrumb" className="flex flex-wrap gap-1 text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
        <Link href="/games" className="hover:underline">
          All games
        </Link>
        <span>/</span>
        <Link href={`/games/${game.id}`} className="hover:underline">
          Game
        </Link>
        <span>/</span>
        <span className="text-[color:var(--color-nbu-text)]">Upload</span>
      </nav>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Upload video</h1>
        <p className="text-sm text-[color:var(--color-nbu-text-muted)]">
          Up to 10 GB. MP4, MOV, or MKV. Files over 1 GB upload directly to
          storage in parts so a dropped connection only retries the failed
          part, not the whole file.
        </p>
      </div>
      <UploadFlow gameId={game.id} teamId={game.team_id} />
    </section>
  );
}
