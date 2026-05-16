"use client";

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import { CancelUploadButton } from "@/components/cancel-upload-button";
import { apiJson, apiVoid } from "@/lib/api-client";
import { ApiError } from "@/lib/errors";
import {
  CV_PIPELINE_STAGES,
  type GenerateDemoPreviewResponse,
  IMPLEMENTED_PIPELINE_STAGES,
  VIDEO_TERMINAL_STATUSES,
  type CreateVideoEventRequest,
  type ReviewStatus,
  type UpdateVideoEventReviewRequest,
  type UserRole,
  type VideoDetailResponse,
  type VideoEventSource,
  type VideoEventSourceFilter,
  type VideoEventSummary,
  type VideoEventType,
  type VideoEventsResponse,
  type VideoStatusResponse,
} from "@/lib/contract";
import { PLAYBACK_STATUS_LABELS } from "@/lib/video-status";

const POLL_INTERVAL_MS = 3_000;
const WORKER_HEARTBEAT_STALE_MS = 5 * 60 * 1_000;
// Refresh the video detail (and the signed playback URL it carries) a bit
// before token_expires_at — 30 s of slack gives the user time to start
// playback on a slow connection without the URL expiring mid-stream.
const TOKEN_REFRESH_SLACK_MS = 30_000;

const EVENT_TYPE_LABELS: Record<VideoEventType, string> = {
  shot_attempt: "Shot attempt",
  shot_made: "Made shot",
  rebound: "Rebound",
  pass: "Pass",
};

const EVENT_TYPE_OPTIONS: Array<{ value: VideoEventType; label: string }> = [
  { value: "shot_attempt", label: EVENT_TYPE_LABELS.shot_attempt },
  { value: "shot_made", label: EVENT_TYPE_LABELS.shot_made },
  { value: "rebound", label: EVENT_TYPE_LABELS.rebound },
  { value: "pass", label: EVENT_TYPE_LABELS.pass },
];

const MANUAL_TAG_SHORTCUTS: Record<string, VideoEventType> = {
  "1": "shot_attempt",
  "2": "shot_made",
  "3": "rebound",
  "4": "pass",
};

const EVENT_TYPE_FILTERS: Array<{ value: VideoEventType | "all"; label: string }> = [
  { value: "all", label: "All" },
  { value: "shot_attempt", label: "Shots" },
  { value: "rebound", label: "Rebounds" },
  { value: "pass", label: "Passes" },
  { value: "shot_made", label: "Made shots" },
];

type ReviewStatusFilter = ReviewStatus | "all";

const REVIEW_STATUS_FILTERS: Array<{ value: ReviewStatusFilter; label: string }> = [
  { value: "needs_review", label: "Needs review" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "machine_only", label: "Detector only" },
  { value: "all", label: "All" },
];

const SOURCE_FILTERS: Array<{ value: VideoEventSourceFilter; label: string }> = [
  { value: "all", label: "All sources" },
  { value: "alpha_model", label: "Alpha model" },
  { value: "manual", label: "Manual tags" },
];

const SOURCE_LABELS: Record<VideoEventSource, string> = {
  alpha_model: "Alpha model",
  manual: "Manual tag",
};

const EVENT_TYPE_THEME: Record<
  VideoEventType,
  {
    dotClass: string;
    markerClass: string;
    rowBorderClass: string;
  }
> = {
  shot_attempt: {
    dotClass: "bg-sky-500",
    markerClass: "bg-sky-500 hover:bg-sky-400 focus-visible:ring-sky-500",
    rowBorderClass: "border-l-sky-500",
  },
  shot_made: {
    dotClass: "bg-emerald-500",
    markerClass:
      "bg-emerald-500 hover:bg-emerald-400 focus-visible:ring-emerald-500",
    rowBorderClass: "border-l-emerald-500",
  },
  rebound: {
    dotClass: "bg-amber-500",
    markerClass: "bg-amber-500 hover:bg-amber-400 focus-visible:ring-amber-500",
    rowBorderClass: "border-l-amber-500",
  },
  pass: {
    dotClass: "bg-fuchsia-500",
    markerClass:
      "bg-fuchsia-500 hover:bg-fuchsia-400 focus-visible:ring-fuchsia-500",
    rowBorderClass: "border-l-fuchsia-500",
  },
};

const REVIEW_STATUS_BADGE_CLASS: Record<ReviewStatus, string> = {
  needs_review:
    "rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]",
  approved:
    "rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[color:var(--color-nbu-text)]",
  rejected:
    "rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[color:var(--color-nbu-error)]",
  machine_only:
    "rounded-md border border-dashed border-[color:var(--color-nbu-border)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]",
};

const EVENTS_PAGE_SIZE = 50;
const DEFAULT_CLIP_PRE_MS = 4_000;
const DEFAULT_CLIP_POST_MS = 6_000;
const MAX_REVIEW_WINDOW_MS = 60_000;

const EMPTY_SUMMARY: VideoEventsResponse["summary"] = {
  total: 0,
  needs_review: 0,
  approved: 0,
  rejected: 0,
  machine_only: 0,
  alpha_model_source: 0,
  manual_source: 0,
};

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
  const playbackVideoRef = useRef<HTMLVideoElement | null>(null);
  const [candidateTimelineEvents, setCandidateTimelineEvents] = useState<
    VideoEventSummary[]
  >([]);

  const jumpToPlaybackTime = (timeMs: number) => {
    const el = playbackVideoRef.current;
    if (!el) return;
    el.currentTime = Math.max(0, timeMs / 1_000);
    el.scrollIntoView?.({ block: "center", behavior: "smooth" });
    try {
      const playResult = el.play();
      if (playResult && "catch" in playResult) {
        void playResult.catch(() => undefined);
      }
    } catch {
      // Browser autoplay policies can still block playback; seeking is enough.
    }
  };

  const getCurrentPlaybackTimeMs = () =>
    Math.max(0, Math.round((playbackVideoRef.current?.currentTime ?? 0) * 1_000));

  const handleCandidateTimelineEventsChange = useCallback(
    (events: VideoEventSummary[]) => {
      setCandidateTimelineEvents(events);
    },
    [],
  );

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

  const showCandidateReview = shouldShowCandidateReview(
    video,
    statusQuery.data,
    viewerRole,
  );

  useEffect(() => {
    if (!showCandidateReview) {
      setCandidateTimelineEvents([]);
    }
  }, [showCandidateReview]);

  return (
    <div className="space-y-4">
      {showCandidateReview ? (
        <div
          data-testid="playback-review-row"
          className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:items-start"
        >
          <PlaybackPanel
            video={video}
            videoRef={playbackVideoRef}
            timelineEvents={candidateTimelineEvents}
            onJumpToTime={jumpToPlaybackTime}
          />
          <CandidateReviewPanel
            video={video}
            status={statusQuery.data}
            viewerRole={viewerRole}
            onJumpToTime={jumpToPlaybackTime}
            getCurrentPlaybackTimeMs={getCurrentPlaybackTimeMs}
            onTimelineEventsChange={handleCandidateTimelineEventsChange}
          />
        </div>
      ) : (
        <PlaybackPanel video={video} videoRef={playbackVideoRef} />
      )}
      <PendingUploadRecoveryPanel video={video} viewerRole={viewerRole} />
      <ActiveProcessingRecoveryPanel
        video={video}
        status={statusQuery.data}
        viewerRole={viewerRole}
      />
      <FailedVideoRecoveryPanel video={video} viewerRole={viewerRole} />
      <DemoPreviewPanel video={video} viewerRole={viewerRole} />
      <MetadataPanel video={video} />
      <ProcessingPanel
        status={statusQuery.data}
        video={video}
      />
    </div>
  );
}

function shouldShowCandidateReview(
  video: VideoDetailResponse,
  status: VideoStatusResponse | undefined,
  viewerRole: UserRole | null,
): boolean {
  if (viewerRole !== "coach" && viewerRole !== "admin") return false;
  const eventStageStatus = status?.stages.events?.status ?? video.processing.events;
  return video.status === "processed" && eventStageStatus === "completed";
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

function CandidateReviewPanel({
  video,
  status,
  viewerRole,
  onJumpToTime,
  getCurrentPlaybackTimeMs,
  onTimelineEventsChange,
}: {
  video: VideoDetailResponse;
  status: VideoStatusResponse | undefined;
  viewerRole: UserRole | null;
  onJumpToTime: (timeMs: number) => void;
  getCurrentPlaybackTimeMs: () => number;
  onTimelineEventsChange: (events: VideoEventSummary[]) => void;
}) {
  const queryClient = useQueryClient();
  const [reviewStatusFilter, setReviewStatusFilter] =
    useState<ReviewStatusFilter>("needs_review");
  const [eventTypeFilter, setEventTypeFilter] = useState<VideoEventType | "all">(
    "all",
  );
  const [sourceFilter, setSourceFilter] = useState<VideoEventSourceFilter>("all");
  const [searchText, setSearchText] = useState("");
  const [manualEventType, setManualEventType] =
    useState<VideoEventType>("shot_attempt");
  const [manualPreSeconds, setManualPreSeconds] = useState(
    formatClipTimeInput(DEFAULT_CLIP_PRE_MS),
  );
  const [manualPostSeconds, setManualPostSeconds] = useState(
    formatClipTimeInput(DEFAULT_CLIP_POST_MS),
  );
  const [mutationError, setMutationError] = useState<string | null>(null);

  const isVisible = shouldShowCandidateReview(video, status, viewerRole);

  const queryKey = [
    "video-events",
    video.id,
    reviewStatusFilter,
    eventTypeFilter,
    sourceFilter,
  ] as const;

  const eventsQuery = useInfiniteQuery<VideoEventsResponse>({
    queryKey,
    enabled: isVisible,
    initialPageParam: undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    staleTime: 10_000,
    refetchInterval: (query) =>
      isVisible && (query.state.data?.pages[0]?.summary.total ?? 0) === 0
        ? POLL_INTERVAL_MS
        : false,
    queryFn: async ({ pageParam }) =>
      apiJson<VideoEventsResponse>(
        buildEventsUrl({
          videoId: video.id,
          cursor: typeof pageParam === "string" ? pageParam : null,
          reviewStatus: reviewStatusFilter,
          eventType: eventTypeFilter,
          source: sourceFilter,
        }),
      ),
  });

  const pages = useMemo(
    () => eventsQuery.data?.pages ?? [],
    [eventsQuery.data?.pages],
  );
  const events = useMemo(() => pages.flatMap((page) => page.events), [pages]);
  const summary = pages[0]?.summary ?? EMPTY_SUMMARY;
  const filteredTotal = pages[0]?.total ?? events.length;

  const visibleEvents = useMemo(() => {
    const needle = searchText.trim().toLowerCase();
    if (!needle) return events;
    return events.filter((event) => buildSearchIndex(event).includes(needle));
  }, [events, searchText]);

  useEffect(() => {
    onTimelineEventsChange(isVisible ? visibleEvents : []);
  }, [isVisible, onTimelineEventsChange, visibleEvents]);

  const reviewMutation = useMutation({
    mutationFn: ({
      event,
      review_status,
      clip_start_time_ms,
      clip_end_time_ms,
    }: {
      event: VideoEventSummary;
      review_status: ReviewStatus;
      clip_start_time_ms: number;
      clip_end_time_ms: number;
    }) =>
      apiJson(`/videos/${video.id}/events/${event.id}/review`, {
        method: "PATCH",
        json: {
          review_status,
          clip_start_time_ms,
          clip_end_time_ms,
        } satisfies UpdateVideoEventReviewRequest,
      }),
    onSuccess: () => {
      setMutationError(null);
      queryClient.invalidateQueries({ queryKey: ["video-events", video.id] });
    },
    onError: (err) => {
      setMutationError(
        err instanceof ApiError ? err.message : "Unable to update candidate review.",
      );
    },
  });

  const manualTagMutation = useMutation({
    mutationFn: (payload: CreateVideoEventRequest) =>
      apiJson(`/videos/${video.id}/events`, {
        method: "POST",
        json: payload,
      }),
    onSuccess: () => {
      setMutationError(null);
      queryClient.invalidateQueries({ queryKey: ["video-events", video.id] });
    },
    onError: (err) => {
      setMutationError(
        err instanceof ApiError ? err.message : "Unable to add manual tag.",
      );
    },
  });

  const submitManualTag = useCallback(
    (eventType: VideoEventType) => {
      const eventTimeMs = getCurrentPlaybackTimeMs();
      const resolvedWindow = manualClipWindow(
        eventTimeMs,
        video.duration_seconds,
        manualPreSeconds,
        manualPostSeconds,
      );
      if (typeof resolvedWindow === "string") {
        setMutationError(resolvedWindow);
        return;
      }
      const [clipStartTimeMs, clipEndTimeMs] = resolvedWindow;
      setManualEventType(eventType);
      manualTagMutation.mutate({
        event_type: eventType,
        event_time_ms: eventTimeMs,
        clip_start_time_ms: clipStartTimeMs,
        clip_end_time_ms: clipEndTimeMs,
      });
    },
    [
      getCurrentPlaybackTimeMs,
      manualPostSeconds,
      manualPreSeconds,
      manualTagMutation,
      video.duration_seconds,
    ],
  );

  useEffect(() => {
    if (!isVisible) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      const eventType = MANUAL_TAG_SHORTCUTS[event.key];
      if (
        !eventType ||
        event.repeat ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        manualTagMutation.isPending ||
        isEditableTarget(event.target)
      ) {
        return;
      }
      event.preventDefault();
      submitManualTag(eventType);
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isVisible, manualTagMutation.isPending, submitManualTag]);

  if (!isVisible) {
    return null;
  }

  const reviewStatusCounts: Record<ReviewStatusFilter, number> = {
    needs_review: summary.needs_review,
    approved: summary.approved,
    rejected: summary.rejected,
    machine_only: summary.machine_only,
    all: summary.total,
  };

  return (
    <section
      data-testid="candidate-review-panel"
      className="space-y-3 rounded-lg border border-[color:var(--color-nbu-border)] p-4 text-sm"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Alpha candidates
          </h2>
          <p className="text-xs text-[color:var(--color-nbu-text-muted)]">
            Review only. Not production analytics. Export reviewed windows for
            coach and editing workflows.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 sm:justify-end">
          <a
            href={candidateExportHref(video.id, "csv")}
            className="rounded-md border border-[color:var(--color-nbu-border)] px-2 py-1 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)]"
          >
            Export approved CSV
          </a>
          <a
            href={candidateExportHref(video.id, "sportscode_xml")}
            className="rounded-md border border-[color:var(--color-nbu-border)] px-2 py-1 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)]"
          >
            Sportscode XML
          </a>
          <a
            href={candidateExportHref(video.id, "package_zip")}
            className="rounded-md border border-[color:var(--color-nbu-border)] px-2 py-1 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)]"
          >
            Package
          </a>
          <a
            href={candidateExportHref(video.id, "json")}
            className="rounded-md border border-[color:var(--color-nbu-border)] px-2 py-1 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)]"
          >
            Manifest JSON
          </a>
          <span className="rounded-md border border-[color:var(--color-nbu-border)] px-2 py-0.5 font-mono text-xs text-[color:var(--color-nbu-text-muted)]">
            {eventsQuery.isLoading
              ? "loading"
              : `${filteredTotal} of ${summary.total} candidates`}
          </span>
        </div>
      </div>

      <div className="space-y-2 rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Status
          </span>
          {REVIEW_STATUS_FILTERS.map((filter) => (
            <button
              key={filter.value}
              type="button"
              data-testid={`candidate-status-filter-${filter.value}`}
              onClick={() => setReviewStatusFilter(filter.value)}
              className={
                reviewStatusFilter === filter.value
                  ? "rounded-md border border-[color:var(--color-nbu-text)] px-2 py-1 text-xs font-medium"
                  : "rounded-md border border-[color:var(--color-nbu-border)] px-2 py-1 text-xs text-[color:var(--color-nbu-text-muted)] transition hover:border-[color:var(--color-nbu-text)]"
              }
            >
              {filter.label}{" "}
              <span className="font-mono">{reviewStatusCounts[filter.value]}</span>
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Type
          </span>
          {EVENT_TYPE_FILTERS.map((filter) => (
            <button
              key={filter.value}
              type="button"
              data-testid={`candidate-type-filter-${filter.value}`}
              onClick={() => setEventTypeFilter(filter.value)}
              className={
                eventTypeFilter === filter.value
                  ? "rounded-md border border-[color:var(--color-nbu-text)] px-2 py-1 text-xs font-medium"
                  : "rounded-md border border-[color:var(--color-nbu-border)] px-2 py-1 text-xs text-[color:var(--color-nbu-text-muted)] transition hover:border-[color:var(--color-nbu-text)]"
              }
            >
              {filter.label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Source
          </span>
          {SOURCE_FILTERS.map((filter) => (
            <button
              key={filter.value}
              type="button"
              data-testid={`candidate-source-filter-${filter.value}`}
              onClick={() => setSourceFilter(filter.value)}
              className={
                sourceFilter === filter.value
                  ? "rounded-md border border-[color:var(--color-nbu-text)] px-2 py-1 text-xs font-medium"
                  : "rounded-md border border-[color:var(--color-nbu-border)] px-2 py-1 text-xs text-[color:var(--color-nbu-text-muted)] transition hover:border-[color:var(--color-nbu-text)]"
              }
            >
              {filter.label}{" "}
              <span className="font-mono">
                {filter.value === "all"
                  ? summary.total
                  : filter.value === "alpha_model"
                    ? summary.alpha_model_source
                    : summary.manual_source}
              </span>
            </button>
          ))}
        </div>
        <div className="flex flex-col gap-2 pt-1">
          <label className="flex flex-col gap-1 text-xs text-[color:var(--color-nbu-text-muted)]">
            Filter loaded candidates
            <input
              type="search"
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              data-testid="candidate-search"
              placeholder="Loaded type, timestamp, or status"
              className="rounded-md border border-[color:var(--color-nbu-border)] bg-transparent px-2 py-1 text-sm text-[color:var(--color-nbu-text)]"
            />
          </label>
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex min-w-0 flex-col gap-1 text-xs text-[color:var(--color-nbu-text-muted)]">
              Manual tag
              <select
                value={manualEventType}
                onChange={(event) =>
                  setManualEventType(event.target.value as VideoEventType)
                }
                className="rounded-md border border-[color:var(--color-nbu-border)] bg-transparent px-2 py-1 text-sm text-[color:var(--color-nbu-text)]"
              >
                {EVENT_TYPE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-[color:var(--color-nbu-text-muted)]">
              Pre-roll (s)
              <input
                type="number"
                min="0"
                max="60"
                step="0.5"
                value={manualPreSeconds}
                onChange={(event) => setManualPreSeconds(event.target.value)}
                data-testid="manual-tag-pre-roll"
                className="w-20 rounded-md border border-[color:var(--color-nbu-border)] bg-transparent px-2 py-1 font-mono text-sm text-[color:var(--color-nbu-text)]"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-[color:var(--color-nbu-text-muted)]">
              Post-roll (s)
              <input
                type="number"
                min="0"
                max="60"
                step="0.5"
                value={manualPostSeconds}
                onChange={(event) => setManualPostSeconds(event.target.value)}
                data-testid="manual-tag-post-roll"
                className="w-20 rounded-md border border-[color:var(--color-nbu-border)] bg-transparent px-2 py-1 font-mono text-sm text-[color:var(--color-nbu-text)]"
              />
            </label>
            <button
              type="button"
              disabled={manualTagMutation.isPending}
              onClick={() => submitManualTag(manualEventType)}
              className="rounded-md border border-[color:var(--color-nbu-border)] px-2 py-1 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)] disabled:opacity-50"
            >
              {manualTagMutation.isPending ? "Adding..." : "Add tag at playhead"}
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
              Quick tag
            </span>
            {EVENT_TYPE_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                disabled={manualTagMutation.isPending}
                title={`Create ${option.label.toLowerCase()} at the current playhead`}
                data-testid={`manual-quick-tag-${option.value}`}
                onClick={() => submitManualTag(option.value)}
                className="inline-flex items-center gap-1.5 rounded-md border border-[color:var(--color-nbu-border)] px-2 py-1 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)] disabled:opacity-50"
              >
                <span
                  aria-hidden="true"
                  className={`h-2 w-2 rounded-full ${EVENT_TYPE_THEME[option.value].dotClass}`}
                />
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {mutationError ? (
        <p role="alert" className="text-xs text-[color:var(--color-nbu-error)]">
          {mutationError}
        </p>
      ) : null}

      {eventsQuery.isLoading ? (
        <div className="rounded-md border border-dashed border-[color:var(--color-nbu-border)] px-3 py-4 text-xs text-[color:var(--color-nbu-text-muted)]">
          Loading alpha candidates...
        </div>
      ) : eventsQuery.isError ? (
        <p role="alert" className="text-xs text-[color:var(--color-nbu-error)]">
          Alpha candidates are unavailable.
        </p>
      ) : summary.total === 0 ? (
        <div className="rounded-md border border-dashed border-[color:var(--color-nbu-border)] px-3 py-4 text-xs text-[color:var(--color-nbu-text-muted)]">
          No alpha candidates surfaced for this video.
        </div>
      ) : (
        <div className="space-y-2">
          {visibleEvents.length === 0 ? (
            <div className="rounded-md border border-dashed border-[color:var(--color-nbu-border)] px-3 py-4 text-xs text-[color:var(--color-nbu-text-muted)]">
              No loaded candidates match the current filter.
            </div>
          ) : (
            <ul
              data-testid="candidate-review-list"
              className="max-h-[36rem] divide-y divide-[color:var(--color-nbu-border)] overflow-y-auto rounded-md border border-[color:var(--color-nbu-border)]"
            >
              {visibleEvents.map((event) => (
                <CandidateEventRow
                  key={event.id}
                  event={event}
                  isReviewPending={reviewMutation.isPending}
                  onJumpToTime={onJumpToTime}
                  onReview={(payload) => reviewMutation.mutate(payload)}
                />
              ))}
            </ul>
          )}
          {eventsQuery.hasNextPage ? (
            <button
              type="button"
              data-testid="candidate-load-more"
              disabled={eventsQuery.isFetchingNextPage}
              onClick={() => eventsQuery.fetchNextPage()}
              className="self-start rounded-md border border-[color:var(--color-nbu-border)] px-3 py-1 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)] disabled:opacity-50"
            >
              {eventsQuery.isFetchingNextPage ? "Loading more..." : "Load more"}
            </button>
          ) : null}
        </div>
      )}
    </section>
  );
}

function CandidateEventRow({
  event,
  isReviewPending,
  onJumpToTime,
  onReview,
}: {
  event: VideoEventSummary;
  isReviewPending: boolean;
  onJumpToTime: (timeMs: number) => void;
  onReview: (payload: {
    event: VideoEventSummary;
    review_status: ReviewStatus;
    clip_start_time_ms: number;
    clip_end_time_ms: number;
  }) => void;
}) {
  const [startValue, setStartValue] = useState(formatClipTimeInput(event.clip_start_time_ms));
  const [endValue, setEndValue] = useState(formatClipTimeInput(event.clip_end_time_ms));
  const [windowError, setWindowError] = useState<string | null>(null);

  useEffect(() => {
    setStartValue(formatClipTimeInput(event.clip_start_time_ms));
    setEndValue(formatClipTimeInput(event.clip_end_time_ms));
    setWindowError(null);
  }, [event.id, event.clip_start_time_ms, event.clip_end_time_ms]);

  const submitReview = (review_status: ReviewStatus) => {
    const clip_start_time_ms = parseClipTimeInput(startValue);
    const clip_end_time_ms = parseClipTimeInput(endValue);
    if (clip_start_time_ms === null || clip_end_time_ms === null) {
      setWindowError("Enter a valid clip window.");
      return;
    }
    if (clip_start_time_ms >= clip_end_time_ms) {
      setWindowError("Clip start must be before clip end.");
      return;
    }
    if (
      event.event_time_ms < clip_start_time_ms ||
      event.event_time_ms > clip_end_time_ms
    ) {
      setWindowError("The candidate timestamp must stay inside the clip window.");
      return;
    }
    setWindowError(null);
    onReview({ event, review_status, clip_start_time_ms, clip_end_time_ms });
  };

  return (
    <li
      data-testid="candidate-row"
      className={`grid gap-3 border-l-4 px-3 py-2 ${EVENT_TYPE_THEME[event.event_type].rowBorderClass}`}
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span
            aria-hidden="true"
            className={`h-2.5 w-2.5 rounded-full ${EVENT_TYPE_THEME[event.event_type].dotClass}`}
          />
          <span className="font-medium">{EVENT_TYPE_LABELS[event.event_type]}</span>
          <span className="font-mono text-xs text-[color:var(--color-nbu-text-muted)]">
            Moment {formatClipTime(event.event_time_ms)}
          </span>
          <span className={REVIEW_STATUS_BADGE_CLASS[event.review_status]}>
            {formatReviewStatus(event.review_status)}
          </span>
          <span className="rounded-md border border-dashed border-[color:var(--color-nbu-border)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            {SOURCE_LABELS[event.source]}
          </span>
        </div>
        <p className="mt-1 text-xs text-[color:var(--color-nbu-text-muted)]">
          Clip window {formatClipWindow(event.clip_start_time_ms, event.clip_end_time_ms)}
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
          Start
          <input
            type="number"
            min="0"
            step="0.5"
            value={startValue}
            onChange={(input) => setStartValue(input.target.value)}
            data-testid="candidate-window-start"
            className="w-20 rounded-md border border-[color:var(--color-nbu-border)] bg-transparent px-2 py-1 font-mono text-xs text-[color:var(--color-nbu-text)]"
          />
        </label>
        <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
          End
          <input
            type="number"
            min="0"
            step="0.5"
            value={endValue}
            onChange={(input) => setEndValue(input.target.value)}
            data-testid="candidate-window-end"
            className="w-20 rounded-md border border-[color:var(--color-nbu-border)] bg-transparent px-2 py-1 font-mono text-xs text-[color:var(--color-nbu-text)]"
          />
        </label>
        <button
          type="button"
          onClick={() => onJumpToTime(event.clip_start_time_ms)}
          className="rounded-md border border-[color:var(--color-nbu-border)] px-2 py-1 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)]"
        >
          Jump
        </button>
        <button
          type="button"
          disabled={isReviewPending || event.review_status === "approved"}
          onClick={() => submitReview("approved")}
          className="rounded-md border border-[color:var(--color-nbu-border)] px-2 py-1 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)] disabled:opacity-50"
        >
          Approve
        </button>
        <button
          type="button"
          disabled={isReviewPending || event.review_status === "rejected"}
          onClick={() => submitReview("rejected")}
          className="rounded-md border border-[color:var(--color-nbu-border)] px-2 py-1 text-xs font-medium transition hover:border-[color:var(--color-nbu-text)] disabled:opacity-50"
        >
          Reject
        </button>
      </div>
      {windowError ? (
        <p role="alert" className="text-xs text-[color:var(--color-nbu-error)]">
          {windowError}
        </p>
      ) : null}
    </li>
  );
}

function buildEventsUrl({
  videoId,
  cursor,
  reviewStatus,
  eventType,
  source,
}: {
  videoId: string;
  cursor: string | null;
  reviewStatus: ReviewStatusFilter;
  eventType: VideoEventType | "all";
  source: VideoEventSourceFilter;
}): string {
  const params = new URLSearchParams();
  params.set("limit", String(EVENTS_PAGE_SIZE));
  if (cursor) params.set("cursor", cursor);
  if (reviewStatus !== "all") params.append("review_status", reviewStatus);
  if (eventType !== "all") params.append("event_type", eventType);
  if (source !== "all") params.set("source", source);
  return `/videos/${videoId}/events?${params.toString()}`;
}

type CandidateExportFormat = "csv" | "json" | "sportscode_xml" | "package_zip";

function candidateExportHref(videoId: string, format: CandidateExportFormat): string {
  const params = new URLSearchParams({
    format,
    review_status: "approved",
  });
  return `/api/v1/videos/${videoId}/events/export?${params.toString()}`;
}

function buildSearchIndex(event: VideoEventSummary): string {
  return [
    EVENT_TYPE_LABELS[event.event_type],
    formatClipTime(event.event_time_ms),
    formatClipWindow(event.clip_start_time_ms, event.clip_end_time_ms),
    formatReviewStatus(event.review_status),
    SOURCE_LABELS[event.source],
  ]
    .join(" ")
    .toLowerCase();
}

function PlaybackPanel({
  video,
  videoRef,
  timelineEvents = [],
  onJumpToTime,
}: {
  video: VideoDetailResponse;
  videoRef: RefObject<HTMLVideoElement | null>;
  timelineEvents?: VideoEventSummary[];
  onJumpToTime?: (timeMs: number) => void;
}) {
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
      videoRef={videoRef}
      timelineEvents={timelineEvents}
      durationSeconds={video.duration_seconds}
      onJumpToTime={onJumpToTime}
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

function formatClipWindow(startMs: number, endMs: number): string {
  return `${formatClipTime(startMs)}-${formatClipTime(endMs)}`;
}

function formatClipTimeInput(valueMs: number): string {
  const seconds = valueMs / 1_000;
  return Number.isInteger(seconds) ? String(seconds) : seconds.toFixed(1);
}

function parseClipTimeInput(value: string): number | null {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  return Math.round(seconds * 1_000);
}

function manualClipWindow(
  eventTimeMs: number,
  durationSeconds: number | null | undefined,
  preSecondsValue: string,
  postSecondsValue: string,
): [number, number] | string {
  const preMs = parseClipTimeInput(preSecondsValue);
  const postMs = parseClipTimeInput(postSecondsValue);
  if (preMs === null || postMs === null) {
    return "Enter valid pre-roll and post-roll values.";
  }
  if (preMs + postMs <= 0) {
    return "Manual tag window must be longer than zero seconds.";
  }
  if (preMs + postMs > MAX_REVIEW_WINDOW_MS) {
    return "Manual tag window cannot exceed 60 seconds.";
  }
  const durationMs =
    durationSeconds !== null && durationSeconds !== undefined
      ? Math.round(durationSeconds * 1_000)
      : null;
  const start = Math.max(0, eventTimeMs - preMs);
  const end =
    durationMs === null ? eventTimeMs + postMs : Math.min(eventTimeMs + postMs, durationMs);
  if (start >= end) {
    return "Manual tag window must start before it ends.";
  }
  return [start, end];
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tagName = target.tagName.toLowerCase();
  return (
    target.isContentEditable ||
    tagName === "input" ||
    tagName === "select" ||
    tagName === "textarea"
  );
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
  videoRef: externalVideoRef,
  timelineEvents = [],
  durationSeconds,
  onJumpToTime,
}: {
  url: string;
  format: string;
  poster?: string;
  videoRef?: RefObject<HTMLVideoElement | null>;
  timelineEvents?: VideoEventSummary[];
  durationSeconds?: number | null;
  onJumpToTime?: (timeMs: number) => void;
}) {
  const internalVideoRef = useRef<HTMLVideoElement | null>(null);
  const videoRef = externalVideoRef ?? internalVideoRef;
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
  }, [url, format, videoRef]);

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
      {onJumpToTime ? (
        <CandidateTimeline
          events={timelineEvents}
          durationSeconds={durationSeconds}
          onJumpToTime={onJumpToTime}
        />
      ) : null}
    </div>
  );
}

function CandidateTimeline({
  events,
  durationSeconds,
  onJumpToTime,
}: {
  events: VideoEventSummary[];
  durationSeconds: number | null | undefined;
  onJumpToTime: (timeMs: number) => void;
}) {
  const durationMs =
    durationSeconds !== null && durationSeconds !== undefined
      ? Math.max(1, Math.round(durationSeconds * 1_000))
      : null;
  if (durationMs === null || events.length === 0) return null;

  return (
    <div
      data-testid="candidate-timeline"
      className="space-y-2 rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2"
    >
      <div className="relative h-10 overflow-hidden rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-background)]">
        {events.map((event) => {
          const start = Math.min(
            Math.max(0, event.clip_start_time_ms),
            Math.max(0, durationMs - 1),
          );
          const end = Math.min(
            Math.max(start + 1, event.clip_end_time_ms),
            durationMs,
          );
          const left = (start / durationMs) * 100;
          const width = Math.max(0.75, ((end - start) / durationMs) * 100);
          const safeWidth = Math.min(width, 100 - left);
          const laneIndex = EVENT_TYPE_OPTIONS.findIndex(
            (option) => option.value === event.event_type,
          );
          const top = 7 + Math.max(0, laneIndex) * 7;
          return (
            <button
              key={event.id}
              type="button"
              title={`${EVENT_TYPE_LABELS[event.event_type]} ${formatClipWindow(event.clip_start_time_ms, event.clip_end_time_ms)}`}
              aria-label={`${EVENT_TYPE_LABELS[event.event_type]} at ${formatClipTime(event.event_time_ms)}`}
              data-testid="candidate-timeline-marker"
              onClick={() => onJumpToTime(event.event_time_ms)}
              className={`absolute h-1.5 rounded-full opacity-80 outline-none transition focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-nbu-surface)] ${EVENT_TYPE_THEME[event.event_type].markerClass}`}
              style={{
                left: `${left}%`,
                top,
                minWidth: "0.75rem",
                width: `${safeWidth}%`,
              }}
            />
          );
        })}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-[color:var(--color-nbu-text-muted)]">
        {EVENT_TYPE_OPTIONS.map((option) => (
          <span key={option.value} className="inline-flex items-center gap-1">
            <span
              aria-hidden="true"
              className={`h-2 w-2 rounded-full ${EVENT_TYPE_THEME[option.value].dotClass}`}
            />
            {option.label}
          </span>
        ))}
      </div>
    </div>
  );
}
