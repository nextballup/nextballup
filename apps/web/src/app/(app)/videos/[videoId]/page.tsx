import Link from "next/link";
import { notFound } from "next/navigation";
import { serverApiOptional } from "@/lib/api-server";
import type { UserPublic, VideoDetailResponse } from "@/lib/contract";
import { VideoPlaybackView } from "./video-playback-view";

export default async function VideoDetailPage({
  params,
}: {
  params: Promise<{ videoId: string }>;
}) {
  const { videoId } = await params;
  const [video, user] = await Promise.all([
    serverApiOptional<VideoDetailResponse>(`/videos/${videoId}`, {
      nullOnStatuses: [403, 404],
    }),
    serverApiOptional<UserPublic>("/auth/me"),
  ]);
  if (!video) {
    notFound();
  }
  return (
    <section className="space-y-4">
      <nav
        aria-label="Breadcrumb"
        className="flex flex-wrap gap-1 text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]"
      >
        <Link href="/games" className="hover:underline">
          All games
        </Link>
        <span>/</span>
        <Link href={`/games/${video.game_id}`} className="hover:underline">
          Game
        </Link>
        <span>/</span>
        <span className="text-[color:var(--color-nbu-text)]">Video</span>
      </nav>
      <h1 className="text-2xl font-semibold tracking-tight">{video.filename}</h1>
      <VideoPlaybackView
        initialVideo={video}
        viewerRole={user?.role ?? null}
      />
    </section>
  );
}
