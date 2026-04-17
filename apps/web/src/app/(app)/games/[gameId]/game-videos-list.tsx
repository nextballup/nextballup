import Link from "next/link";
import { serverApiOptional } from "@/lib/api-server";
import type { VideoListResponse, VideoStatus } from "@/lib/contract";

const STATUS_LABELS: Record<VideoStatus, string> = {
  pending_upload: "Awaiting upload",
  uploading: "Uploading",
  uploaded: "Uploaded",
  transcoding: "Transcoding",
  queued: "Queued",
  processing: "Processing",
  processed: "Ready",
  failed: "Failed",
};

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

export async function GameVideosList({ gameId }: { gameId: string }) {
  const list = await serverApiOptional<VideoListResponse>(
    `/games/${gameId}/videos`,
    { nullOnStatuses: [403, 404] },
  );

  const videos = list?.videos ?? [];

  return (
    <section
      aria-labelledby="videos-heading"
      className="space-y-3 rounded-lg border border-[color:var(--color-nbu-border)] p-4"
    >
      <div className="flex items-center justify-between gap-3">
        <h2
          id="videos-heading"
          className="text-sm font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]"
        >
          Videos · {videos.length}
        </h2>
        <Link
          href={`/games/${gameId}/upload`}
          className="rounded-md border border-[color:var(--color-nbu-border)] px-3 py-1.5 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)]"
        >
          + Upload
        </Link>
      </div>
      {videos.length === 0 ? (
        <p className="rounded-md border border-dashed border-[color:var(--color-nbu-border)] px-4 py-6 text-center text-sm text-[color:var(--color-nbu-text-muted)]">
          No videos uploaded yet. Upload film to start the processing pipeline.
        </p>
      ) : (
        <ul className="divide-y divide-[color:var(--color-nbu-border)]">
          {videos.map((video) => (
            <li
              key={video.id}
              className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm"
            >
              <div className="min-w-0">
                <Link
                  href={`/videos/${video.id}`}
                  className="truncate font-medium hover:underline"
                >
                  {video.filename}
                </Link>
                <div className="mt-0.5 text-xs text-[color:var(--color-nbu-text-muted)]">
                  {formatBytes(video.file_size_bytes)}
                  {video.duration_seconds != null
                    ? ` · ${Math.round(video.duration_seconds)}s`
                    : ""}
                  {video.camera_position ? ` · ${video.camera_position}` : ""}
                </div>
              </div>
              <span className="rounded-full border border-[color:var(--color-nbu-border)] px-2 py-0.5 text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
                {STATUS_LABELS[video.status] ?? video.status}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
