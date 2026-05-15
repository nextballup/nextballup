import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { VideoPlaybackView } from "@/app/(app)/videos/[videoId]/video-playback-view";
import type {
  GenerateDemoPreviewResponse,
  VideoDetailResponse,
} from "@/lib/contract";
import { server } from "./setup";

const refresh = vi.fn();
const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh }),
}));

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

function baseVideo(overrides: Partial<VideoDetailResponse> = {}): VideoDetailResponse {
  const status = overrides.status ?? "processed";
  return {
    id: "v1",
    game_id: "g1",
    status,
    playback_status:
      overrides.playback_status ??
      (status === "processed"
        ? "ready_for_playback"
        : status === "failed"
          ? "failed"
          : "transcoding"),
    filename: "x.mp4",
    file_size_bytes: 100,
    duration_seconds: 10,
    width: null,
    height: null,
    fps: null,
    codec: null,
    camera_position: null,
    camera_height: null,
    checksum_sha256: null,
    storage_etag: null,
    storage_output_sha256: null,
    privacy_consent_id: null,
    raw_retention_expires_at: null,
    raw_deleted_at: null,
    thumbnail_url: null,
    playback_url: null,
    playback_token: null,
    playback_format: null,
    token_expires_at: null,
    demo_preview_enabled: false,
    demo_preview_status: "idle",
    demo_preview_url: null,
    demo_preview_generated_at: null,
    demo_preview_error_message: null,
    processing: { transcode: "completed" },
    created_at: "2026-04-15T00:00:00Z",
    ...overrides,
  };
}

describe("VideoPlaybackView", () => {
  it("shows the 'not available yet' block when playback fields are null", async () => {
    server.use(
      http.get("/api/v1/videos/v1", () =>
        HttpResponse.json(baseVideo({ status: "processing" })),
      ),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processing",
          stage: "transcode",
          progress_percent: 50,
          stages: {
            transcode: { status: "running", progress_percent: 50 },
            detection: { status: "pending" },
          },
        }),
      ),
    );
    await act(async () => {
      render(
        wrap(
          <VideoPlaybackView
            initialVideo={baseVideo({ status: "processing" })}
            viewerRole="coach"
          />,
        ),
      );
    });
    expect(
      screen.getByText(/Playback not available yet/i),
    ).toBeInTheDocument();
  });

  it("shows active worker heartbeat when transcode remains at 50 percent", async () => {
    server.use(
      http.get("/api/v1/videos/v1", () =>
        HttpResponse.json(baseVideo({ status: "processing" })),
      ),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processing",
          playback_status: "transcoding",
          stage: "transcode",
          progress_percent: 50,
          stages: {
            transcode: {
              status: "running",
              progress_percent: 50,
              heartbeat_at: new Date().toISOString(),
            },
          },
        }),
      ),
    );

    await act(async () => {
      render(
        wrap(
          <VideoPlaybackView
            initialVideo={baseVideo({ status: "processing" })}
            viewerRole="coach"
          />,
        ),
      );
    });

    expect(await screen.findByText(/Worker heartbeat active/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Media transcode can stay at 50%/i),
    ).toBeInTheDocument();
  });

  it("warns when a running transcode has no recent worker heartbeat", async () => {
    server.use(
      http.get("/api/v1/videos/v1", () =>
        HttpResponse.json(baseVideo({ status: "processing" })),
      ),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processing",
          playback_status: "transcoding",
          stage: "transcode",
          progress_percent: 50,
          stages: {
            transcode: {
              status: "running",
              progress_percent: 50,
              heartbeat_at: new Date(Date.now() - 10 * 60 * 1_000).toISOString(),
            },
          },
        }),
      ),
    );

    await act(async () => {
      render(
        wrap(
          <VideoPlaybackView
            initialVideo={baseVideo({ status: "processing" })}
            viewerRole="coach"
          />,
        ),
      );
    });

    expect(await screen.findByText(/No worker heartbeat for 10m/i)).toBeInTheDocument();
  });

  it("lets coaches cancel a stuck running transcode", async () => {
    let cancelled = false;
    server.use(
      http.get("/api/v1/videos/v1", () =>
        HttpResponse.json(
          baseVideo({
            status: "processing",
            processing: { transcode: "running" },
          }),
        ),
      ),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processing",
          playback_status: "transcoding",
          stage: "transcode",
          progress_percent: 50,
          stages: {
            transcode: {
              status: "running",
              progress_percent: 50,
              heartbeat_at: new Date(Date.now() - 10 * 60 * 1_000).toISOString(),
            },
          },
        }),
      ),
      http.post("/api/v1/videos/v1/processing/cancel", async ({ request }) => {
        cancelled = true;
        expect(await request.json()).toEqual({ stage: "transcode" });
        return HttpResponse.json({
          job_id: "j1",
          stage: "transcode",
          status: "failed",
          cancelled_at: "2026-05-07T00:00:00Z",
        });
      }),
    );

    await act(async () => {
      render(
        wrap(
          <VideoPlaybackView
            initialVideo={baseVideo({
              status: "processing",
              processing: { transcode: "running" },
            })}
            viewerRole="coach"
          />,
        ),
      );
    });

    fireEvent.click(await screen.findByTestId("cancel-processing-transcode"));

    await waitFor(() => {
      expect(cancelled).toBe(true);
    });
  });

  it("lets coaches cancel a stuck pending upload from the detail page", async () => {
    const video = baseVideo({
      status: "pending_upload",
      playback_status: "uploading",
      processing: { transcode: "pending" },
    });
    let cancelled = false;
    server.use(
      http.get("/api/v1/videos/v1", () => HttpResponse.json(video)),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "pending_upload",
          playback_status: "uploading",
          stage: null,
          progress_percent: 0,
          stages: { transcode: { status: "pending" } },
        }),
      ),
      http.post("/api/v1/videos/v1/cancel-upload", () => {
        cancelled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    await act(async () => {
      render(wrap(<VideoPlaybackView initialVideo={video} viewerRole="coach" />));
    });

    expect(screen.getByText("Upload not finalized")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /cancel upload/i }));

    await waitFor(() => {
      expect(cancelled).toBe(true);
    });
    expect(refresh).toHaveBeenCalled();
  });

  it("explains processed-but-missing artifacts without implying raw passthrough playback", async () => {
    const video = baseVideo({
      status: "processed",
      filename: "iphone_clip.mov",
      playback_url: null,
      playback_format: null,
      processing: { transcode: "completed" },
    });
    server.use(
      http.get("/api/v1/videos/v1", () => HttpResponse.json(video)),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processed",
          stage: null,
          progress_percent: 100,
          stages: { transcode: { status: "completed" } },
        }),
      ),
    );
    await act(async () => {
      render(wrap(<VideoPlaybackView initialVideo={video} viewerRole="coach" />));
    });
    expect(
      screen.getByText(/Playback not available for this upload yet/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/stays stored privately and is not served directly/i),
    ).toBeInTheDocument();
  });

  it("surfaces failed transcode errors instead of saying the worker is still processing", async () => {
    const video = baseVideo({
      status: "failed",
      playback_status: "failed",
      processing: { transcode: "failed" },
    });
    server.use(
      http.get("/api/v1/videos/v1", () => HttpResponse.json(video)),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "failed",
          playback_status: "failed",
          stage: null,
          progress_percent: 0,
          stages: {
            transcode: {
              status: "failed",
              progress_percent: 0,
              error_message: "[processing.transcode_failed] Video transcoding failed",
            },
          },
        }),
      ),
    );

    await act(async () => {
      render(wrap(<VideoPlaybackView initialVideo={video} viewerRole="coach" />));
    });

    expect(screen.getByText("Processing failed.")).toBeInTheDocument();
    expect(screen.queryByText(/worker is still processing/i)).not.toBeInTheDocument();
    expect(
      await screen.findByText(/\[processing\.transcode_failed\] Video transcoding failed/i),
    ).toBeInTheDocument();
  });

  it("renders a video element with the signed mp4 URL when processed", async () => {
    const video = baseVideo({
      status: "processed",
      playback_url: "https://signed.example/v1.mp4?sig=abc",
      playback_format: "mp4",
      playback_token: "tok",
      token_expires_at: new Date(Date.now() + 3_600_000).toISOString(),
    });
    server.use(
      http.get("/api/v1/videos/v1", () => HttpResponse.json(video)),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processed",
          stage: null,
          progress_percent: 100,
          stages: { transcode: { status: "completed" } },
        }),
      ),
    );
    await act(async () => {
      render(wrap(<VideoPlaybackView initialVideo={video} viewerRole="coach" />));
    });
    const player = await waitFor(() => screen.getByTestId("video-player"));
    expect(player.tagName).toBe("VIDEO");
    expect(player.getAttribute("src")).toBe(
      "https://signed.example/v1.mp4?sig=abc",
    );
  });

  it("renders the friendly playback status label", async () => {
    const video = baseVideo({
      status: "processed",
      playback_status: "ready_for_playback",
    });
    server.use(
      http.get("/api/v1/videos/v1", () => HttpResponse.json(video)),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processed",
          playback_status: "ready_for_playback",
          stage: null,
          progress_percent: 100,
          stages: { transcode: { status: "completed" } },
        }),
      ),
    );
    await act(async () => {
      render(wrap(<VideoPlaybackView initialVideo={video} viewerRole="coach" />));
    });

    expect(screen.getByText("Ready for playback")).toBeInTheDocument();
    expect(screen.queryByText("processed")).not.toBeInTheDocument();
  });

  it("labels unimplemented CV stages honestly instead of showing 'pending'", async () => {
    server.use(
      http.get("/api/v1/videos/v1", () =>
        HttpResponse.json(baseVideo({ status: "processing" })),
      ),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processing",
          stage: "transcode",
          progress_percent: 50,
          stages: {
            transcode: { status: "running", progress_percent: 50 },
            detection: { status: "pending" },
            tracking: { status: "pending" },
            court_mapping: { status: "pending" },
            events: { status: "pending" },
            metrics: { status: "pending" },
          },
        }),
      ),
    );
    await act(async () => {
      render(
        wrap(
          <VideoPlaybackView
            initialVideo={baseVideo({ status: "processing" })}
            viewerRole="coach"
          />,
        ),
      );
    });
    // The CV stages block must appear with honest copy, not "pending".
    const block = await screen.findByTestId("upcoming-cv-stages");
    expect(block.textContent).toMatch(/not yet implemented|not implemented yet/i);
    // Each downstream stage is listed but labelled "not implemented yet".
    expect(block.textContent).toMatch(/detection/);
    expect(block.textContent).toMatch(/tracking/);
    expect(block.textContent).toMatch(/court_mapping/);
    expect(block.textContent).toMatch(/events/);
    expect(block.textContent).toMatch(/metrics/);
    // And the transcode stage stays in the implemented grid, not relabelled.
    expect(screen.getByText("transcode")).toBeInTheDocument();
  });

  it("shows clip proposals once event extraction has completed", async () => {
    const sourceEventId = "00000000-0000-0000-0000-000000000001";
    const video = baseVideo({
      status: "processed",
      processing: { transcode: "completed", events: "completed" },
    });
    server.use(
      http.get("/api/v1/videos/v1", () => HttpResponse.json(video)),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processed",
          playback_status: "ready_for_playback",
          stage: null,
          progress_percent: 100,
          stages: {
            transcode: { status: "completed" },
            events: { status: "completed" },
          },
        }),
      ),
      http.get("/api/v1/videos/v1/clip-proposals", () =>
        HttpResponse.json({
          video_id: "v1",
          total: 1,
          proposals: [
            {
              id: `event:${sourceEventId}`,
              source_event_id: sourceEventId,
              event_type: "shot_made",
              label: "Made shot",
              reason: "Alpha made shot candidate at 00:12. Coach review required.",
              start_time_ms: 8_000,
              end_time_ms: 19_000,
              review_status: "needs_review",
              created_at: "2026-05-11T00:00:00Z",
            },
          ],
        }),
      ),
    );

    await act(async () => {
      render(wrap(<VideoPlaybackView initialVideo={video} viewerRole="coach" />));
    });

    expect(await screen.findByText("Made shot")).toBeInTheDocument();
    expect(screen.getByText(/Not analytics; export is not implemented/)).toBeInTheDocument();
    expect(screen.getByText(/0:08 - 0:19/)).toBeInTheDocument();
    expect(screen.getByText("Needs review")).toBeInTheDocument();
    expect(screen.getByText("1 for review")).toBeInTheDocument();
    expect(screen.queryByText("84%")).not.toBeInTheDocument();
  });

  it("shows all alpha candidates and jumps playback to a candidate window", async () => {
    const video = baseVideo({
      status: "processed",
      playback_url: "https://signed.example/v1.mp4?sig=abc",
      playback_format: "mp4",
      playback_token: "tok",
      token_expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      duration_seconds: 120,
      processing: { transcode: "completed", events: "completed" },
    });
    const proposals = Array.from({ length: 6 }, (_, index) => ({
      id: `event:00000000-0000-0000-0000-00000000000${index + 1}`,
      source_event_id: `00000000-0000-0000-0000-00000000000${index + 1}`,
      event_type: index === 5 ? "rebound" : "shot_attempt",
      label: index === 5 ? "Rebound" : `Shot attempt ${index + 1}`,
      reason: "Alpha candidate at 00:12. Coach review required.",
      start_time_ms: 8_000 + index * 1_000,
      end_time_ms: 14_000 + index * 1_000,
      review_status: "needs_review",
      created_at: "2026-05-11T00:00:00Z",
    }));
    server.use(
      http.get("/api/v1/videos/v1", () => HttpResponse.json(video)),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processed",
          playback_status: "ready_for_playback",
          stage: null,
          progress_percent: 100,
          stages: {
            transcode: { status: "completed" },
            events: { status: "completed" },
          },
        }),
      ),
      http.get("/api/v1/videos/v1/clip-proposals", () =>
        HttpResponse.json({ video_id: "v1", total: proposals.length, proposals }),
      ),
    );

    await act(async () => {
      render(wrap(<VideoPlaybackView initialVideo={video} viewerRole="coach" />));
    });

    const player = (await screen.findByTestId("video-player")) as HTMLVideoElement;
    expect(await screen.findByText("Shot attempt 1")).toBeInTheDocument();
    expect(screen.getByText("Shot attempt 5")).toBeInTheDocument();
    expect(screen.getAllByText("Rebound").length).toBeGreaterThan(0);
    expect(screen.queryByText(/Showing top/)).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "Jump" })[0]);
    expect(player.currentTime).toBe(8);

    fireEvent.click(screen.getByRole("button", { name: /Rebounds 1/ }));
    expect(screen.queryByText("Shot attempt 1")).not.toBeInTheDocument();
    expect(screen.getAllByText("Rebound").length).toBeGreaterThan(0);
  });

  it("lets coaches approve candidates and create manual tags", async () => {
    const sourceEventId = "00000000-0000-0000-0000-000000000001";
    const video = baseVideo({
      status: "processed",
      playback_url: "https://signed.example/v1.mp4?sig=abc",
      playback_format: "mp4",
      playback_token: "tok",
      token_expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      duration_seconds: 120,
      processing: { transcode: "completed", events: "completed" },
    });
    const reviewRequest = vi.fn();
    const manualRequest = vi.fn();
    server.use(
      http.get("/api/v1/videos/v1", () => HttpResponse.json(video)),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processed",
          playback_status: "ready_for_playback",
          stage: null,
          progress_percent: 100,
          stages: {
            transcode: { status: "completed" },
            events: { status: "completed" },
          },
        }),
      ),
      http.get("/api/v1/videos/v1/clip-proposals", () =>
        HttpResponse.json({
          video_id: "v1",
          total: 1,
          proposals: [
            {
              id: `event:${sourceEventId}`,
              source_event_id: sourceEventId,
              event_type: "shot_attempt",
              label: "Shot attempt",
              reason: "Alpha shot attempt candidate at 00:12. Coach review required.",
              start_time_ms: 8_000,
              end_time_ms: 14_000,
              review_status: "needs_review",
              created_at: "2026-05-11T00:00:00Z",
            },
          ],
        }),
      ),
      http.patch(
        `/api/v1/videos/v1/events/${sourceEventId}/review`,
        async ({ request }) => {
          reviewRequest(await request.json());
          return HttpResponse.json({
            id: sourceEventId,
            event_type: "shot_attempt",
            event_time_ms: 12_000,
            output_frame: 360,
            period: null,
            game_clock_ms: null,
            shot_clock_enabled: false,
            shot_clock_ms: null,
            primary_track_key: null,
            confidence: null,
            review_status: "approved",
            created_at: "2026-05-11T00:00:00Z",
          });
        },
      ),
      http.post("/api/v1/videos/v1/events", async ({ request }) => {
        manualRequest(await request.json());
        return HttpResponse.json(
          {
            id: "00000000-0000-0000-0000-000000000099",
            event_type: "rebound",
            event_time_ms: 42_000,
            output_frame: 1260,
            period: null,
            game_clock_ms: null,
            shot_clock_enabled: false,
            shot_clock_ms: null,
            primary_track_key: null,
            confidence: null,
            review_status: "needs_review",
            created_at: "2026-05-11T00:00:00Z",
          },
          { status: 201 },
        );
      }),
    );

    await act(async () => {
      render(wrap(<VideoPlaybackView initialVideo={video} viewerRole="coach" />));
    });

    const player = (await screen.findByTestId("video-player")) as HTMLVideoElement;
    player.currentTime = 42;
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(reviewRequest).toHaveBeenCalledWith({ review_status: "approved" }),
    );

    fireEvent.change(screen.getByLabelText("Manual tag"), {
      target: { value: "rebound" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add tag at current time" }));
    await waitFor(() =>
      expect(manualRequest).toHaveBeenCalledWith({
        event_type: "rebound",
        event_time_ms: 42_000,
      }),
    );
  });

  it("hides clip proposals from players", async () => {
    const proposalRequest = vi.fn();
    server.use(
      http.get("/api/v1/videos/v1", () =>
        HttpResponse.json(
          baseVideo({
            status: "processed",
            processing: { transcode: "completed", events: "completed" },
          }),
        ),
      ),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processed",
          playback_status: "ready_for_playback",
          stage: null,
          progress_percent: 100,
          stages: {
            transcode: { status: "completed" },
            events: { status: "completed" },
          },
        }),
      ),
      http.get("/api/v1/videos/v1/clip-proposals", () => {
        proposalRequest();
        return HttpResponse.json({ video_id: "v1", total: 0, proposals: [] });
      }),
    );

    await act(async () => {
      render(
        wrap(
          <VideoPlaybackView
            initialVideo={baseVideo({
              status: "processed",
              processing: { transcode: "completed", events: "completed" },
            })}
            viewerRole="player"
          />,
        ),
      );
    });

    expect(screen.queryByTestId("clip-proposal-panel")).not.toBeInTheDocument();
    expect(proposalRequest).not.toHaveBeenCalled();
  });

  it("hides clip proposals before event extraction completes", async () => {
    server.use(
      http.get("/api/v1/videos/v1", () =>
        HttpResponse.json(
          baseVideo({
            status: "processed",
            processing: { transcode: "completed", events: "pending" },
          }),
        ),
      ),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processed",
          playback_status: "ready_for_playback",
          stage: null,
          progress_percent: 100,
          stages: {
            transcode: { status: "completed" },
            events: { status: "pending" },
          },
        }),
      ),
    );

    await act(async () => {
      render(
        wrap(
          <VideoPlaybackView
            initialVideo={baseVideo({
              status: "processed",
              processing: { transcode: "completed", events: "pending" },
            })}
            viewerRole="coach"
          />,
        ),
      );
    });

    expect(screen.queryByTestId("clip-proposal-panel")).not.toBeInTheDocument();
  });

  it("surfaces an HLS manifest URL as the video source", async () => {
    const video = baseVideo({
      status: "processed",
      playback_url: "https://signed.example/v1/manifest.m3u8?sig=zzz",
      playback_format: "hls",
      playback_token: "tok",
      token_expires_at: new Date(Date.now() + 3_600_000).toISOString(),
    });
    server.use(
      http.get("/api/v1/videos/v1", () => HttpResponse.json(video)),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processed",
          stage: null,
          progress_percent: 100,
          stages: { transcode: { status: "completed" } },
        }),
      ),
    );
    await act(async () => {
      render(wrap(<VideoPlaybackView initialVideo={video} viewerRole="coach" />));
    });
    const player = await waitFor(() => screen.getByTestId("video-player"));
    // The hls.js path sets src asynchronously; the fallback ("canPlayType"
    // returned empty string in our setup) keeps the src on the element once
    // hls.js rejects via Hls.isSupported()=false in jsdom.
    await waitFor(() => {
      expect(player.getAttribute("src")).toBe(
        "https://signed.example/v1/manifest.m3u8?sig=zzz",
      );
    });
  });

  it("shows a playback error message when the video element fails", async () => {
    const video = baseVideo({
      status: "processed",
      playback_url: "https://signed.example/v1.mp4?sig=abc",
      playback_format: "mp4",
      playback_token: "tok",
      token_expires_at: new Date(Date.now() + 3_600_000).toISOString(),
    });
    server.use(
      http.get("/api/v1/videos/v1", () => HttpResponse.json(video)),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processed",
          stage: null,
          progress_percent: 100,
          stages: { transcode: { status: "completed" } },
        }),
      ),
    );
    await act(async () => {
      render(wrap(<VideoPlaybackView initialVideo={video} viewerRole="coach" />));
    });

    fireEvent.error(await screen.findByTestId("video-player"));

    expect(await screen.findByTestId("playback-error")).toHaveTextContent(
      /refresh the page/i,
    );
  });

  it("hides failed-video recovery controls from player viewers", async () => {
    server.use(
      http.get("/api/v1/videos/v1", () =>
        HttpResponse.json(baseVideo({ status: "failed" })),
      ),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "failed",
          stage: "transcode",
          progress_percent: 0,
          stages: { transcode: { status: "failed" } },
        }),
      ),
    );
    await act(async () => {
      render(
        wrap(
          <VideoPlaybackView
            initialVideo={baseVideo({ status: "failed" })}
            viewerRole="player"
          />,
        ),
      );
    });
    expect(screen.queryByTestId("requeue-transcode")).not.toBeInTheDocument();
    expect(screen.queryByTestId("delete-video")).not.toBeInTheDocument();
  });

  it("shows retry and delete recovery controls for failed transcode when viewer is coach", async () => {
    server.use(
      http.get("/api/v1/videos/v1", () =>
        HttpResponse.json(baseVideo({ status: "failed", processing: { transcode: "failed" } })),
      ),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "failed",
          stage: "transcode",
          progress_percent: 0,
          stages: { transcode: { status: "failed" } },
        }),
      ),
    );
    await act(async () => {
      render(
        wrap(
          <VideoPlaybackView
            initialVideo={baseVideo({ status: "failed", processing: { transcode: "failed" } })}
            viewerRole="coach"
          />,
        ),
      );
    });
    const button = await screen.findByTestId("requeue-transcode");
    expect(button).toBeInTheDocument();
    expect(button.textContent).toMatch(/retry processing/i);
    expect(screen.getByTestId("delete-video")).toBeInTheDocument();
  });

  it("deletes a failed video from the recovery panel", async () => {
    const video = baseVideo({
      status: "failed",
      processing: { transcode: "failed" },
    });
    let deleted = false;
    server.use(
      http.get("/api/v1/videos/v1", () => HttpResponse.json(video)),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "failed",
          stage: "transcode",
          progress_percent: 0,
          stages: { transcode: { status: "failed" } },
        }),
      ),
      http.delete("/api/v1/videos/v1", () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    await act(async () => {
      render(wrap(<VideoPlaybackView initialVideo={video} viewerRole="coach" />));
    });

    fireEvent.click(await screen.findByTestId("delete-video"));

    await waitFor(() => {
      expect(deleted).toBe(true);
    });
    expect(push).toHaveBeenCalledWith("/games/g1");
    expect(refresh).toHaveBeenCalled();
  });

  it("shows the local demo preview controls when the backend enables them", async () => {
    const video = baseVideo({
      demo_preview_enabled: true,
      status: "processed",
    });
    server.use(
      http.get("/api/v1/videos/v1", () => HttpResponse.json(video)),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processed",
          stage: null,
          progress_percent: 100,
          stages: { transcode: { status: "completed" } },
        }),
      ),
    );
    await act(async () => {
      render(wrap(<VideoPlaybackView initialVideo={video} viewerRole="coach" />));
    });
    expect(screen.getByTestId("demo-preview-panel")).toBeInTheDocument();
    expect(screen.getByText("Alpha detector preview")).toBeInTheDocument();
    expect(screen.getByText("Review only. Not production analytics.")).toBeInTheDocument();
    expect(screen.getByTestId("generate-demo-preview")).toBeInTheDocument();
  });

  it("lets a coach cancel a stuck local demo preview", async () => {
    const queued = baseVideo({
      demo_preview_enabled: true,
      demo_preview_status: "queued",
      status: "processed",
    });
    const failed = baseVideo({
      demo_preview_enabled: true,
      demo_preview_status: "failed",
      demo_preview_error_message:
        "Alpha detector preview was cancelled. Fix the local worker setup, then generate again.",
      status: "processed",
    });
    let current = queued;
    let cancelCount = 0;
    server.use(
      http.get("/api/v1/videos/v1", () => HttpResponse.json(current)),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processed",
          stage: null,
          progress_percent: 100,
          stages: { transcode: { status: "completed" } },
        }),
      ),
      http.delete("/api/v1/videos/v1/demo-preview", async () => {
        cancelCount += 1;
        current = failed;
        return HttpResponse.json<GenerateDemoPreviewResponse>({
          status: "failed",
          preview_url: null,
          generated_at: null,
        });
      }),
    );
    await act(async () => {
      render(wrap(<VideoPlaybackView initialVideo={queued} viewerRole="coach" />));
    });

    fireEvent.click(await screen.findByTestId("cancel-demo-preview"));

    await waitFor(() => {
      expect(cancelCount).toBe(1);
      expect(screen.getByText(/was cancelled/i)).toBeInTheDocument();
    });
  });

  it("keeps alpha detector preview copy scoped to review rather than accuracy claims", async () => {
    const video = baseVideo({
      demo_preview_enabled: true,
      status: "processed",
    });
    server.use(
      http.get("/api/v1/videos/v1", () => HttpResponse.json(video)),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processed",
          stage: null,
          progress_percent: 100,
          stages: { transcode: { status: "completed" } },
        }),
      ),
    );
    await act(async () => {
      render(wrap(<VideoPlaybackView initialVideo={video} viewerRole="coach" />));
    });
    const panel = screen.getByTestId("demo-preview-panel");
    expect(panel.textContent).toMatch(/Alpha detector preview/);
    expect(panel.textContent).toMatch(/Review only/);
    expect(panel.textContent).toMatch(/Not production analytics/);
    expect(panel.textContent).not.toMatch(/tracking accuracy/i);
    expect(panel.textContent).not.toMatch(/event accuracy/i);
    expect(panel.textContent).not.toMatch(/metrics accuracy/i);
  });

  it("generates a local demo preview and renders the returned mp4", async () => {
    const initial = baseVideo({
      demo_preview_enabled: true,
      status: "processed",
    });
    const refreshed = baseVideo({
      demo_preview_enabled: true,
      demo_preview_status: "completed",
      status: "processed",
      demo_preview_url: "/api/v1/videos/v1/demo-preview/artifact",
      demo_preview_generated_at: "2026-04-19T12:00:00Z",
    });
    let postCount = 0;
    server.use(
      http.get("/api/v1/videos/v1", () => HttpResponse.json(refreshed)),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processed",
          stage: null,
          progress_percent: 100,
          stages: { transcode: { status: "completed" } },
        }),
      ),
      http.post("/api/v1/videos/v1/demo-preview", async () => {
        postCount += 1;
        return HttpResponse.json<GenerateDemoPreviewResponse>({
          status: "queued",
          preview_url: null,
          generated_at: null,
        }, { status: 202 });
      }),
    );
    await act(async () => {
      render(wrap(<VideoPlaybackView initialVideo={initial} viewerRole="coach" />));
    });
    await act(async () => {
      screen.getByTestId("generate-demo-preview").click();
    });
    await waitFor(() => {
      const players = screen.getAllByTestId("video-player");
      const demoPlayer = players.find(
        (player) =>
          player.getAttribute("src") ===
          "/api/v1/videos/v1/demo-preview/artifact?v=2026-04-19T12%3A00%3A00Z",
      );
      expect(demoPlayer).toBeDefined();
    });
    expect(postCount).toBe(1);
  });

  it("renders alpha preview generated time without locale-specific hydration text", async () => {
    const video = baseVideo({
      demo_preview_enabled: true,
      demo_preview_status: "completed",
      status: "processed",
      demo_preview_url: "/api/v1/videos/v1/demo-preview/artifact",
      demo_preview_generated_at: "2026-04-19T12:00:00Z",
    });
    server.use(
      http.get("/api/v1/videos/v1", () => HttpResponse.json(video)),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processed",
          stage: null,
          progress_percent: 100,
          stages: { transcode: { status: "completed" } },
        }),
      ),
    );
    await act(async () => {
      render(wrap(<VideoPlaybackView initialVideo={video} viewerRole="coach" />));
    });

    expect(screen.getByText("Last generated: 2026-04-19 12:00:00 UTC")).toBeInTheDocument();
  });

  it("does not show the requeue button for a running stage, even to an admin", async () => {
    server.use(
      http.get("/api/v1/videos/v1", () =>
        HttpResponse.json(baseVideo({ status: "processing" })),
      ),
      http.get("/api/v1/videos/v1/status", () =>
        HttpResponse.json({
          status: "processing",
          stage: "transcode",
          progress_percent: 30,
          stages: { transcode: { status: "running", progress_percent: 30 } },
        }),
      ),
    );
    await act(async () => {
      render(
        wrap(
          <VideoPlaybackView
            initialVideo={baseVideo({ status: "processing" })}
            viewerRole="admin"
          />,
        ),
      );
    });
    // Running stages aren't accepted by the backend (409); surfacing a button
    // for them would only produce an error on click.
    await waitFor(() => {
      expect(screen.queryByTestId("requeue-transcode")).not.toBeInTheDocument();
    });
  });
});
