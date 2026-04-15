import Link from "next/link";
import { notFound } from "next/navigation";
import { serverApiOptional } from "@/lib/api-server";
import type { VideoDetailResponse } from "@/lib/contract";
import { VideoPlaybackView } from "./video-playback-view";

export default async function VideoDetailPage({
  params,
}: {
  params: Promise<{ videoId: string }>;
}) {
  const { videoId } = await params;
  const video = await serverApiOptional<VideoDetailResponse>(`/videos/${videoId}`, {
    nullOnStatuses: [403, 404],
  });
  if (!video) {
    notFound();
  }
  return (
    <section className="space-y-4">
      <Link
        href={`/games/${video.game_id}`}
        className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)] hover:underline"
      >
        ← Back to game
      </Link>
      <h1 className="text-2xl font-semibold tracking-tight">{video.filename}</h1>
      <VideoPlaybackView initialVideo={video} />
    </section>
  );
}
