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
      <div>
        <Link
          href={`/games/${game.id}`}
          className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)] hover:underline"
        >
          ← Back to game
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">Upload video</h1>
        <p className="text-sm text-[color:var(--color-nbu-text-muted)]">
          Up to 10 GB. MP4, MOV, or MKV. Files over 1 GB use multipart upload —
          those are not supported in the browser UI yet, but you can still
          upload them from the CLI.
        </p>
      </div>
      <UploadFlow gameId={game.id} />
    </section>
  );
}
