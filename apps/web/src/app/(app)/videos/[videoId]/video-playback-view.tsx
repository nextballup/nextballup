"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { CancelUploadButton } from "@/components/cancel-upload-button";
import { apiJson, apiVoid } from "@/lib/api-client";
import { ApiError } from "@/lib/errors";
import {
  CV_PIPELINE_STAGES,
  type GenerateDemoPreviewResponse,
  IMPLEMENTED_PIPELINE_STAGES,
  VIDEO_TERMINAL_STATUSES,
  type UserRole,
  type VideoClipProposalsResponse,
  type VideoDetailResponse,
  type VideoStatusResponse,
} from "@/lib/contract";
import { PLAYBACK_STATUS_LABELS } from "@/lib/video-status";

const POLL_INTERVAL_MS = 3_000;
const WORKER_HEARTBEAT_STALE_MS = 5 * 60 * 1_000;
// Refresh the video detail (and the signed playback URL it carries) a bit
// before token_expires_at — 30 s of slack gives the user time to start
// playback on a slow connection without the URL expiring mid-stream.
const TOKEN_REFRESH_SLACK_MS = 30_000;

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
      if (
        VIDEO_TERMINAL_STATUSES.includes(current.status) &&
        !isDemoPreviewActive(current.demo_preview_status)
      ) {
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
      <PendingUploadRecoveryPanel video={video} viewerRole={viewerRole} />
      <ActiveProcessingRecoveryPanel
        video={video}
        status={statusQuery.data}
        viewerRole={viewerRole}
      />
      <FailedVideoRecoveryPanel video={video} viewerRole={viewerRole} />
      <DemoPreviewPanel video={video} viewerRole={viewerRole} />
      <ClipProposalPanel
        video={video}
        status={statusQuery.data}
        viewerRole={viewerRole}
      />
      <MetadataPanel video={video} />
      <ProcessingPanel
        status={statusQuery.data}
        video={video}
      />
    </div>
  );
}

function FailedVideoRecoveryPanel({
  video,
  viewerRole,
}: {
  video: VideoDetailResponse;
  viewerRole: UserRole | null;
}) {
  const canRecover = viewerRole === "coach" || viewerRole === "admin";
  const transcodeFailed =
    video.status === "failed" && video.processing.transcode === "failed";
  if (video.status !== "failed" || !canRecover) {
    return null;
  }
  return (
    <section className="space-y-3 rounded-lg border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] p-4 text-sm">
      <div className="space-y-1">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
          Video recovery
        </h2>
        <p className="text-[color:var(--color-nbu-text-muted)]">
          Retry processing checks the original upload and queues transcoding
          again. Delete video removes the failed upload record and known
          storage objects.
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        {transcodeFailed ? (
          <RequeueButton videoId={video.id} stage="transcode" />
        ) : null}
        <DeleteVideoButton videoId={video.id} gameId={video.game_id} />
      </div>
    </section>
  );
}

function PendingUploadRecoveryPanel({
  video,
  viewerRole,
}: {
  video: VideoDetailResponse;
  viewerRole: UserRole | null;
}) {
  const queryClient = useQueryClient();
  const canCancel = viewerRole === "coach" || viewerRole === "admin";
  if (video.status !== "pending_upload" || !canCancel) {
    return null;
  }
  return (
    <section className="space-y-2 rounded-lg border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] p-4 text-sm">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
        Upload not finalized
      </h2>
      <p className="text-[color:var(--color-nbu-text-muted)]">
        This upload is still reserving team quota. If the browser upload is no
        longer running, cancel it to release the slot and start a fresh upload.
      </p>
      <CancelUploadButton
        videoId={video.id}
        onCancelled={() => {
          queryClient.invalidateQueries({ queryKey: ["video", video.id] });
          queryClient.invalidateQueries({ queryKey: ["video-status", video.id] });
        }}
      />
    </section>
  );
}

function ActiveProcessingRecoveryPanel({
  video,
  status,
  viewerRole,
}: {
  video: VideoDetailResponse;
  status: VideoStatusResponse | undefined;
  viewerRole: UserRole | null;
}) {
  const canRecover = viewerRole === "coach" || viewerRole === "admin";
  const transcode = status?.stages.transcode;
  const transcodeRunning =
    video.status === "processing" &&
    (transcode?.status === "running" || video.processing.transcode === "running");
  if (!canRecover || !transcodeRunning) {
    return null;
  }
  return (
    <section className="space-y-3 rounded-lg border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] p-4 text-sm">
      <div className="space-y-1">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
          Processing recovery
        </h2>
        <p className="text-[color:var(--color-nbu-text-muted)]">
          This transcode is still marked running. Cancel processing marks this
          attempt failed so you can retry processing or delete the video.
        </p>
      </div>
      <CancelProcessingButton videoId={video.id} stage="transcode" />
    </section>
  );
}

function DemoPreviewPanel({
  video,
  viewerRole,
}: {
  video: VideoDetailResponse;
  viewerRole: UserRole | null;
}) {
  const queryClient = useQueryClient();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const canRequest = viewerRole === "coach" || viewerRole === "admin";
  const previewStatus = video.demo_preview_status;
  const previewBusy = isDemoPreviewActive(previewStatus);
  const mutation = useMutation<GenerateDemoPreviewResponse>({
    mutationFn: async () =>
      apiJson<GenerateDemoPreviewResponse>(`/videos/${video.id}/demo-preview`, {
        method: "POST",
        json: {},
      }),
    onSuccess: () => {
      setErrorMessage(null);
      queryClient.refetchQueries({ queryKey: ["video", video.id] });
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setErrorMessage(err.message);
      } else {
        setErrorMessage("Unable to generate the alpha detector preview.");
      }
    },
  });
  const cancelMutation = useMutation<GenerateDemoPreviewResponse>({
    mutationFn: async () =>
      apiJson<GenerateDemoPreviewResponse>(`/videos/${video.id}/demo-preview`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      setErrorMessage(null);
      queryClient.invalidateQueries({ queryKey: ["video", video.id] });
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setErrorMessage(err.message);
      } else {
        setErrorMessage("Unable to cancel the alpha detector preview.");
      }
    },
  });
  const previewUrl = video.demo_preview_url;
  const generatedAt = video.demo_preview_generated_at;
  const previewRenderUrl = previewUrl ? withVersionParam(previewUrl, generatedAt) : null;
  const resolvedErrorMessage =
    errorMessage ??
    (previewStatus === "failed" ? video.demo_preview_error_message : null);
  useEffect(() => {
    if (previewUrl && generatedAt) {
      setErrorMessage(null);
    }
  }, [generatedAt, previewUrl]);
  if (!video.demo_preview_enabled && !previewUrl) {
    return null;
  }
  return (
    <section
      data-testid="demo-preview-panel"
      className="space-y-3 rounded-lg border border-[color:var(--color-nbu-border)] p-4 text-sm"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Alpha detector preview
          </h2>
          <p className="text-xs text-[color:var(--color-nbu-text-muted)]">
            Review only. Not production analytics.
          </p>
          {generatedAt ? (
            <p className="text-xs text-[color:var(--color-nbu-text-muted)]">
              Last generated: {formatUtcDateTime(generatedAt)}
            </p>
          ) : null}
        </div>
        {video.demo_preview_enabled && canRequest ? (
          <div className="flex flex-wrap gap-2 sm:justify-end">
            <button
              type="button"
              onClick={() => mutation.mutate()}
              disabled={mutation.isPending || previewBusy || video.status !== "processed"}
              data-testid="generate-demo-preview"
              className="self-start rounded-md border border-[color:var(--color-nbu-border)] px-3 py-1 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)] disabled:opacity-50"
            >
              {mutation.isPending
                ? "Queueing preview…"
                : previewBusy
                  ? "Generating detector preview…"
                  : previewUrl
                    ? "Regenerate detector preview"
                    : "Generate detector preview"}
            </button>
            {previewBusy ? (
              <button
                type="button"
                onClick={() => cancelMutation.mutate()}
                disabled={cancelMutation.isPending}
                data-testid="cancel-demo-preview"
                className="self-start rounded-md border border-[color:var(--color-nbu-error)] px-3 py-1 text-xs font-medium text-[color:var(--color-nbu-error)] transition hover:bg-[color:var(--color-nbu-surface)] disabled:opacity-50"
              >
                {cancelMutation.isPending ? "Cancelling…" : "Cancel preview"}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      {previewBusy ? (
        <div className="space-y-1 text-xs text-[color:var(--color-nbu-text-muted)]">
          <p>
            The preview job is queued in the worker and will appear automatically
            when rendering finishes.
          </p>
          <p>
            If the local worker logs show a setup error, cancel this preview,
            fix the worker env, then generate again.
          </p>
        </div>
      ) : null}
      {previewRenderUrl ? (
        <VideoPlayer key={previewRenderUrl} url={previewRenderUrl} format="mp4" />
      ) : (
        <div className="rounded-md border border-dashed border-[color:var(--color-nbu-border)] px-3 py-5 text-xs text-[color:var(--color-nbu-text-muted)]">
          Generate a detector overlay from the processed mezzanine for internal
          alpha review.
        </div>
      )}
      {resolvedErrorMessage ? (
        <p role="alert" className="text-xs text-[color:var(--color-nbu-error)]">
          {resolvedErrorMessage}
        </p>
      ) : null}
    </section>
  );
}

function ClipProposalPanel({
  video,
  status,
  viewerRole,
}: {
  video: VideoDetailResponse;
  status: VideoStatusResponse | undefined;
  viewerRole: UserRole | null;
}) {
  const canReview = viewerRole === "coach" || viewerRole === "admin";
  const eventStageStatus = status?.stages.events?.status ?? video.processing.events;
  const canLoadProposals =
    video.status === "processed" && eventStageStatus === "completed";
  const proposalsQuery = useQuery<VideoClipProposalsResponse>({
    queryKey: ["video-clip-proposals", video.id],
    enabled: canReview && canLoadProposals,
    staleTime: 15_000,
    refetchInterval: (query) =>
      canReview && canLoadProposals && (query.state.data?.proposals.length ?? 0) === 0
        ? POLL_INTERVAL_MS
        : false,
    queryFn: async () =>
      apiJson<VideoClipProposalsResponse>(`/videos/${video.id}/clip-proposals`),
  });
  const proposals = proposalsQuery.data?.proposals ?? [];
  const visibleProposals = proposals.slice(0, 5);

  if (!canReview || !canLoadProposals) {
    return null;
  }

  return (
    <section
      data-testid="clip-proposal-panel"
      className="space-y-3 rounded-lg border border-[color:var(--color-nbu-border)] p-4 text-sm"
    >
      <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Clip proposals
          </h2>
          <p className="text-xs text-[color:var(--color-nbu-text-muted)]">
            Alpha detector candidates for coach review. Not analytics; export is
            not implemented.
          </p>
        </div>
        <span className="self-start rounded-md border border-[color:var(--color-nbu-border)] px-2 py-0.5 font-mono text-xs text-[color:var(--color-nbu-text-muted)]">
          {proposalsQuery.isLoading ? "loading" : `${proposals.length} for review`}
        </span>
      </div>
      {proposalsQuery.isLoading ? (
        <div className="rounded-md border border-dashed border-[color:var(--color-nbu-border)] px-3 py-4 text-xs text-[color:var(--color-nbu-text-muted)]">
          Loading clip proposals...
        </div>
      ) : proposalsQuery.isError ? (
        <p role="alert" className="text-xs text-[color:var(--color-nbu-error)]">
          Clip proposals are unavailable.
        </p>
      ) : proposals.length === 0 ? (
        <div className="rounded-md border border-dashed border-[color:var(--color-nbu-border)] px-3 py-4 text-xs text-[color:var(--color-nbu-text-muted)]">
          No alpha candidates surfaced for this video.
        </div>
      ) : (
        <div className="space-y-2">
          {proposals.length > visibleProposals.length ? (
            <p className="text-xs text-[color:var(--color-nbu-text-muted)]">
              Showing top {visibleProposals.length} of {proposals.length}.
            </p>
          ) : null}
          <ul className="divide-y divide-[color:var(--color-nbu-border)] rounded-md border border-[color:var(--color-nbu-border)]">
            {visibleProposals.map((proposal) => (
              <li
                key={proposal.id}
                className="grid gap-2 px-3 py-2 sm:grid-cols-[1fr_auto]"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="font-medium">{proposal.label}</span>
                    <span className="font-mono text-xs text-[color:var(--color-nbu-text-muted)]">
                      {formatClipTime(proposal.start_time_ms)} -{" "}
                      {formatClipTime(proposal.end_time_ms)}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-[color:var(--color-nbu-text-muted)]">
                    {proposal.reason}
                  </p>
                </div>
                <div className="flex items-start gap-2 sm:justify-end">
                  <span className="rounded-md border border-[color:var(--color-nbu-border)] px-2 py-0.5 font-mono text-xs text-[color:var(--color-nbu-text-muted)]">
                    {formatReviewStatus(proposal.review_status)}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function PlaybackPanel({ video }: { video: VideoDetailResponse }) {
  if (!video.playback_url || !video.playback_format) {
    const processedWithoutArtifact = video.status === "processed";
    const processingFailed = video.status === "failed";
    return (
      <div className="rounded-lg border border-dashed border-[color:var(--color-nbu-border)] px-4 py-8 text-center text-sm text-[color:var(--color-nbu-text-muted)]">
        <p className="font-medium">
          {processingFailed
            ? "Processing failed."
            : processedWithoutArtifact
              ? "Playback not available for this upload yet."
              : "Playback not available yet."}
        </p>
        {processingFailed ? (
          <p className="mt-1">
            This upload reached the processing pipeline, but the worker could
            not create a browser-safe playback file. Check the failed stage
            below for the recorded error.
          </p>
        ) : processedWithoutArtifact ? (
          <p className="mt-1">
            This upload finished processing, but its sanitized playback artifact
            is unavailable. For privacy and compatibility, the original upload
            stays stored privately and is not served directly to the browser.
          </p>
        ) : (
          <p className="mt-1">
            {video.status === "pending_upload"
              ? "The original browser upload has not been finalized yet. If it is stuck, cancel it to release quota and start a fresh upload."
              : "The worker is still processing this upload. Status polls automatically; you will see a player here as soon as the mezzanine output is ready."}
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
        <dd className="mt-1 font-medium">
          {PLAYBACK_STATUS_LABELS[video.playback_status] ?? video.playback_status}
        </dd>
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
  const implemented = Object.entries(stages).filter(([stage]) =>
    IMPLEMENTED_PIPELINE_STAGES.has(stage),
  );
  const upcoming = Object.keys(stages).filter(
    (stage) =>
      !IMPLEMENTED_PIPELINE_STAGES.has(stage) && CV_PIPELINE_STAGES.includes(stage),
  );
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
          const errorMessage = detail.error_message;
          const activity = processingStageActivity(detail);
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
              {errorMessage ? (
                <p
                  role="alert"
                  className="rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-2 py-1 text-xs text-[color:var(--color-nbu-error)]"
                >
                  {errorMessage}
                </p>
              ) : null}
              {activity ? (
                <p
                  className={
                    activity.isStale
                      ? "text-xs text-[color:var(--color-nbu-error)]"
                      : "text-xs text-[color:var(--color-nbu-text-muted)]"
                  }
                >
                  {activity.text}
                </p>
              ) : null}
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
        {mutation.isPending ? "Retrying…" : "Retry processing"}
      </button>
      {errorMessage && (
        <span role="alert" className="text-xs text-[color:var(--color-nbu-error)]">
          {errorMessage}
        </span>
      )}
    </div>
  );
}

function CancelProcessingButton({
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
      apiJson(`/videos/${videoId}/processing/cancel`, {
        method: "POST",
        json: { stage },
      }),
    onSuccess: () => {
      setErrorMessage(null);
      queryClient.invalidateQueries({ queryKey: ["video", videoId] });
      queryClient.invalidateQueries({ queryKey: ["video-status", videoId] });
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setErrorMessage(err.message);
      } else {
        setErrorMessage("Unable to cancel processing.");
      }
    },
  });
  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending}
        data-testid={`cancel-processing-${stage}`}
        className="self-start rounded-md border border-[color:var(--color-nbu-border)] px-2 py-0.5 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)] disabled:opacity-50"
      >
        {mutation.isPending ? "Cancelling..." : "Cancel processing"}
      </button>
      {errorMessage && (
        <span role="alert" className="text-xs text-[color:var(--color-nbu-error)]">
          {errorMessage}
        </span>
      )}
    </div>
  );
}

function DeleteVideoButton({
  videoId,
  gameId,
}: {
  videoId: string;
  gameId: string;
}) {
  const router = useRouter();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: () =>
      apiVoid(`/videos/${videoId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      setErrorMessage(null);
      router.push(`/games/${gameId}`);
      router.refresh();
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setErrorMessage(err.message);
      } else {
        setErrorMessage("Unable to delete video.");
      }
    },
  });
  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending}
        data-testid="delete-video"
        className="self-start rounded-md border border-[color:var(--color-nbu-error)] px-2 py-0.5 text-xs font-medium text-[color:var(--color-nbu-error)] transition hover:bg-[color:var(--color-nbu-surface)] disabled:opacity-50"
      >
        {mutation.isPending ? "Deleting…" : "Delete video"}
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

function processingStageActivity(
  detail: VideoStatusResponse["stages"][string],
): { text: string; isStale: boolean } | null {
  if (detail.status !== "running") return null;

  const heartbeatAt = detail.heartbeat_at ? Date.parse(detail.heartbeat_at) : NaN;
  if (!Number.isNaN(heartbeatAt)) {
    const elapsedMs = Math.max(0, Date.now() - heartbeatAt);
    if (elapsedMs > WORKER_HEARTBEAT_STALE_MS) {
      return {
        text: `No worker heartbeat for ${formatDuration(elapsedMs)}.`,
        isStale: true,
      };
    }
    const suffix =
      detail.progress_percent === 50
        ? " Media transcode can stay at 50% until playback output is uploaded."
        : "";
    return {
      text: `Worker heartbeat active ${formatElapsed(elapsedMs)}.${suffix}`,
      isStale: false,
    };
  }

  const startedAt = detail.started_at ? Date.parse(detail.started_at) : NaN;
  if (Number.isNaN(startedAt)) return null;
  return {
    text: `Worker started ${formatElapsed(Math.max(0, Date.now() - startedAt))}.`,
    isStale: false,
  };
}

function formatElapsed(elapsedMs: number): string {
  const seconds = Math.floor(elapsedMs / 1_000);
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}

function formatDuration(elapsedMs: number): string {
  const seconds = Math.floor(elapsedMs / 1_000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h`;
}

function formatClipTime(valueMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(valueMs / 1_000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function formatReviewStatus(value: string): string {
  switch (value) {
    case "needs_review":
      return "Needs review";
    case "machine_only":
      return "Detector only";
    case "approved":
      return "Approved";
    case "rejected":
      return "Rejected";
    default:
      return value.replace(/_/g, " ");
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)}GB`;
}

function formatUtcDateTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const year = parsed.getUTCFullYear();
  const month = String(parsed.getUTCMonth() + 1).padStart(2, "0");
  const day = String(parsed.getUTCDate()).padStart(2, "0");
  const hours = String(parsed.getUTCHours()).padStart(2, "0");
  const minutes = String(parsed.getUTCMinutes()).padStart(2, "0");
  const seconds = String(parsed.getUTCSeconds()).padStart(2, "0");
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds} UTC`;
}

function withVersionParam(url: string, generatedAt: string | null): string {
  if (!generatedAt) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}v=${encodeURIComponent(generatedAt)}`;
}

function isDemoPreviewActive(status: string): boolean {
  return status === "queued" || status === "running";
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
  const [playbackError, setPlaybackError] = useState<string | null>(null);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    setPlaybackError(null);
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
      hls.on(Hls.Events.ERROR, (_event, data) => {
        const detail = data.details ? ` (${data.details})` : "";
        setPlaybackError(
          data.fatal
            ? `Playback failed while loading the signed video stream${detail}. Refresh the page to request a fresh playback URL.`
            : `Playback is having trouble loading part of the stream${detail}.`,
        );
      });
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
    <div className="space-y-2">
      <video
        ref={videoRef}
        controls
        playsInline
        poster={poster}
        onError={() => {
          setPlaybackError(
            "Playback failed while loading the signed video stream. Refresh the page to request a fresh playback URL.",
          );
        }}
        className="aspect-video w-full rounded-lg bg-black"
        data-testid="video-player"
      />
      {playbackError && (
        <p
          role="alert"
          data-testid="playback-error"
          className="text-sm text-[color:var(--color-nbu-error)]"
        >
          {playbackError}
        </p>
      )}
    </div>
  );
}
