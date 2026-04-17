"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { apiJson } from "@/lib/api-client";
import { ApiError } from "@/lib/errors";
import {
  CV_PIPELINE_STAGES,
  IMPLEMENTED_PIPELINE_STAGES,
  VIDEO_TERMINAL_STATUSES,
  type UserRole,
  type VideoDetailResponse,
  type VideoStatusResponse,
} from "@/lib/contract";

const POLL_INTERVAL_MS = 3_000;
// Refresh the video detail (and the signed playback URL it carries) a bit
// before token_expires_at — 30 s of slack gives the user time to start
// playback on a slow connection without the URL expiring mid-stream.
const TOKEN_REFRESH_SLACK_MS = 30_000;

// Stages where the admin requeue endpoint will accept input. Non-terminal
// states (running/pending) are rejected by the backend with 409, so don't
// even offer the button for those.
const REQUEUEABLE_STAGE_STATES = new Set(["failed", "completed"]);

export function VideoPlaybackView({
  initialVideo,
  viewerRole,
}: {
  initialVideo: VideoDetailResponse;
  viewerRole: UserRole | null;
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
      <ProcessingPanel
        status={statusQuery.data}
        video={video}
        viewerRole={viewerRole}
      />
    </div>
  );
}

function PlaybackPanel({ video }: { video: VideoDetailResponse }) {
  if (!video.playback_url || !video.playback_format) {
    const processedWithoutArtifact = video.status === "processed";
    return (
      <div className="rounded-lg border border-dashed border-[color:var(--color-nbu-border)] px-4 py-8 text-center text-sm text-[color:var(--color-nbu-text-muted)]">
        <p className="font-medium">
          {processedWithoutArtifact
            ? "Playback not available for this upload yet."
            : "Playback not available yet."}
        </p>
        {processedWithoutArtifact ? (
          <p className="mt-1">
            This upload finished processing, but its sanitized playback artifact
            is unavailable. For privacy and compatibility, the original upload
            stays stored privately and is not served directly to the browser.
          </p>
        ) : (
          <p className="mt-1">
            The worker is still processing this upload. Status polls
            automatically — you will see a player here as soon as the
            mezzanine output is ready.
          </p>
        )}
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
  viewerRole,
}: {
  status: VideoStatusResponse | undefined;
  video: VideoDetailResponse;
  viewerRole: UserRole | null;
}) {
  const stages = status?.stages ?? fallbackStages(video);
  const implemented = Object.entries(stages).filter(([stage]) =>
    IMPLEMENTED_PIPELINE_STAGES.has(stage),
  );
  const upcoming = Object.keys(stages).filter(
    (stage) =>
      !IMPLEMENTED_PIPELINE_STAGES.has(stage) && CV_PIPELINE_STAGES.includes(stage),
  );
  const isAdmin = viewerRole === "admin";
  return (
    <div className="space-y-3 rounded-lg border border-[color:var(--color-nbu-border)] p-4 text-sm">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
        Processing pipeline
      </h2>
      <ul className="grid gap-2 sm:grid-cols-3">
        {implemented.map(([stage, detail]) => {
          const statusValue = detail.status;
          const progress =
            "progress_percent" in detail ? detail.progress_percent : undefined;
          const canRequeue =
            isAdmin && REQUEUEABLE_STAGE_STATES.has(statusValue);
          return (
            <li
              key={stage}
              className="flex flex-col gap-1 rounded-md border border-[color:var(--color-nbu-border)] px-3 py-2"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{stage}</span>
                <span className="font-mono text-xs text-[color:var(--color-nbu-text-muted)]">
                  {statusValue}
                  {typeof progress === "number" ? ` ${progress}%` : ""}
                </span>
              </div>
              {canRequeue && (
                <RequeueButton videoId={video.id} stage={stage} />
              )}
            </li>
          );
        })}
      </ul>
      {upcoming.length > 0 && (
        <div
          data-testid="upcoming-cv-stages"
          className="space-y-2 rounded-md border border-dashed border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2"
        >
          <div className="text-xs font-medium uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Computer-vision stages (coming later)
          </div>
          <p className="text-xs text-[color:var(--color-nbu-text-muted)]">
            Detection, tracking, court mapping, event extraction, and derived
            metrics are not yet implemented in this phase. They appear below
            so the pipeline view stays stable as real CV work lands.
          </p>
          <ul className="grid gap-2 sm:grid-cols-3">
            {upcoming.map((stage) => (
              <li
                key={stage}
                className="flex items-center justify-between gap-2 rounded-md border border-[color:var(--color-nbu-border)] px-3 py-2 opacity-70"
              >
                <span className="font-medium">{stage}</span>
                <span className="font-mono text-xs text-[color:var(--color-nbu-text-muted)]">
                  not implemented yet
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function RequeueButton({
  videoId,
  stage,
}: {
  videoId: string;
  stage: string;
}) {
  const queryClient = useQueryClient();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: () =>
      apiJson(`/videos/${videoId}/processing/requeue`, {
        method: "POST",
        json: { stage },
      }),
    onSuccess: () => {
      setErrorMessage(null);
      // Invalidate both the video detail and status queries so the next poll
      // picks up the new PENDING row without a full page refresh.
      queryClient.invalidateQueries({ queryKey: ["video", videoId] });
      queryClient.invalidateQueries({ queryKey: ["video-status", videoId] });
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setErrorMessage(err.message);
      } else {
        setErrorMessage("Unable to requeue stage.");
      }
    },
  });
  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending}
        data-testid={`requeue-${stage}`}
        className="self-start rounded-md border border-[color:var(--color-nbu-border)] px-2 py-0.5 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)] disabled:opacity-50"
      >
        {mutation.isPending ? "Requeuing…" : "Requeue stage (admin)"}
      </button>
      {errorMessage && (
        <span role="alert" className="text-xs text-[color:var(--color-nbu-error)]">
          {errorMessage}
        </span>
      )}
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
    // Lazy import hls.js so non-HLS users don't pay for the bundle.
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
