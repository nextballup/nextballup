"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { apiJson } from "@/lib/api-client";
import {
  VIDEO_TERMINAL_STATUSES,
  type VideoDetailResponse,
  type VideoStatusResponse,
} from "@/lib/contract";

const POLL_INTERVAL_MS = 3_000;
// Refresh the video detail (and the signed playback URL it carries) a bit
// before token_expires_at — 30 s of slack gives the user time to start
// playback on a slow connection without the URL expiring mid-stream.
const TOKEN_REFRESH_SLACK_MS = 30_000;

export function VideoPlaybackView({
  initialVideo,
}: {
  initialVideo: VideoDetailResponse;
}) {
  const videoQuery = useQuery<VideoDetailResponse>({
    queryKey: ["video", initialVideo.id],
    initialData: initialVideo,
    staleTime: 0,
    refetchInterval: (query) => {
      const current = query.state.data;
      if (!current) return POLL_INTERVAL_MS;
      if (VIDEO_TERMINAL_STATUSES.includes(current.status)) {
        return false;
      }
      return POLL_INTERVAL_MS;
    },
    queryFn: async () =>
      apiJson<VideoDetailResponse>(`/videos/${initialVideo.id}`),
  });

  const statusQuery = useQuery<VideoStatusResponse>({
    queryKey: ["video-status", initialVideo.id],
    refetchInterval: (query) => {
      const current = query.state.data;
      if (
        current &&
        VIDEO_TERMINAL_STATUSES.includes(current.status)
      ) {
        return false;
      }
      return POLL_INTERVAL_MS;
    },
    queryFn: async () =>
      apiJson<VideoStatusResponse>(`/videos/${initialVideo.id}/status`),
  });

  const video = videoQuery.data ?? initialVideo;

  // Re-fetch the video (and its signed URL) shortly before token expiry so
  // long-form playback doesn't 403 mid-seek.
  useEffect(() => {
    if (!video.token_expires_at || !video.playback_url) return;
    const expiresAt = Date.parse(video.token_expires_at);
    if (Number.isNaN(expiresAt)) return;
    const delay = Math.max(
      5_000,
      expiresAt - Date.now() - TOKEN_REFRESH_SLACK_MS,
    );
    const handle = window.setTimeout(() => {
      videoQuery.refetch();
    }, delay);
    return () => window.clearTimeout(handle);
  }, [video.token_expires_at, video.playback_url, videoQuery]);

  return (
    <div className="space-y-4">
      <PlaybackPanel video={video} />
      <MetadataPanel video={video} />
      <ProcessingPanel status={statusQuery.data} video={video} />
    </div>
  );
}

function PlaybackPanel({ video }: { video: VideoDetailResponse }) {
  if (!video.playback_url || !video.playback_format) {
    return (
      <div className="rounded-lg border border-dashed border-[color:var(--color-nbu-border)] px-4 py-8 text-center text-sm text-[color:var(--color-nbu-text-muted)]">
        <p className="font-medium">Playback not available yet.</p>
        <p className="mt-1">
          The worker is still processing this upload. Status polls automatically
          — you will see a player here as soon as the mezzanine output is ready.
        </p>
      </div>
    );
  }
  return (
    <VideoPlayer
      url={video.playback_url}
      format={video.playback_format}
      poster={video.thumbnail_url ?? undefined}
    />
  );
}

function MetadataPanel({ video }: { video: VideoDetailResponse }) {
  return (
    <dl className="grid grid-cols-2 gap-4 rounded-lg border border-[color:var(--color-nbu-border)] p-4 text-sm sm:grid-cols-4">
      <div>
        <dt className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
          Status
        </dt>
        <dd className="mt-1 font-medium">{video.status}</dd>
      </div>
      <div>
        <dt className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
          Format
        </dt>
        <dd className="mt-1 font-mono">{video.playback_format ?? "—"}</dd>
      </div>
      <div>
        <dt className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
          Size
        </dt>
        <dd className="mt-1 font-mono">
          {video.file_size_bytes != null
            ? formatBytes(video.file_size_bytes)
            : "—"}
        </dd>
      </div>
      <div>
        <dt className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
          Duration
        </dt>
        <dd className="mt-1 font-mono">
          {video.duration_seconds != null
            ? `${Math.round(video.duration_seconds)}s`
            : "—"}
        </dd>
      </div>
    </dl>
  );
}

function ProcessingPanel({
  status,
  video,
}: {
  status: VideoStatusResponse | undefined;
  video: VideoDetailResponse;
}) {
  const stages = status?.stages ?? fallbackStages(video);
  return (
    <div className="rounded-lg border border-[color:var(--color-nbu-border)] p-4 text-sm">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
        Processing pipeline
      </h2>
      <ul className="grid gap-2 sm:grid-cols-3">
        {Object.entries(stages).map(([stage, detail]) => {
          const statusValue = detail.status;
          const progress =
            "progress_percent" in detail ? detail.progress_percent : undefined;
          return (
            <li
              key={stage}
              className="flex items-center justify-between gap-2 rounded-md border border-[color:var(--color-nbu-border)] px-3 py-2"
            >
              <span className="font-medium">{stage}</span>
              <span className="font-mono text-xs text-[color:var(--color-nbu-text-muted)]">
                {statusValue}
                {typeof progress === "number" ? ` ${progress}%` : ""}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function fallbackStages(
  video: VideoDetailResponse,
): Record<string, VideoStatusResponse["stages"][string]> {
  return Object.fromEntries(
    Object.entries(video.processing).map(([stage, statusValue]) => [
      stage,
      { status: statusValue },
    ]),
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)}GB`;
}

// ---- Player ---------------------------------------------------------------

function VideoPlayer({
  url,
  format,
  poster,
}: {
  url: string;
  format: string;
  poster?: string;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    if (format !== "hls") {
      // mp4 / anything that native <video> handles — just set src.
      el.src = url;
      return;
    }
    if (el.canPlayType("application/vnd.apple.mpegurl")) {
      // Safari / iOS handle HLS natively
      el.src = url;
      return;
    }
    let hlsInstance: { destroy: () => void } | null = null;
    let cancelled = false;
    // Lazy import hls.js so non-HLS users (the Phase 5 passthrough mp4 case)
    // don't pay for the bundle.
    import("hls.js").then(({ default: Hls }) => {
      if (cancelled || !videoRef.current) return;
      if (!Hls.isSupported()) {
        videoRef.current.src = url;
        return;
      }
      const hls = new Hls({ enableWorker: true });
      hls.loadSource(url);
      hls.attachMedia(videoRef.current);
      hlsInstance = hls;
    });
    return () => {
      cancelled = true;
      hlsInstance?.destroy();
    };
  }, [url, format]);

  return (
    <video
      ref={videoRef}
      controls
      playsInline
      poster={poster}
      className="aspect-video w-full rounded-lg bg-black"
      data-testid="video-player"
    />
  );
}
