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

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
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

  it("hides the admin requeue control from non-admin viewers", async () => {
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
            viewerRole="coach"
          />,
        ),
      );
    });
    expect(screen.queryByTestId("requeue-transcode")).not.toBeInTheDocument();
  });

  it("shows the admin requeue control for failed stages when viewer is admin", async () => {
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
            viewerRole="admin"
          />,
        ),
      );
    });
    const button = await screen.findByTestId("requeue-transcode");
    expect(button).toBeInTheDocument();
    expect(button.textContent).toMatch(/requeue/i);
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
    expect(screen.getByTestId("generate-demo-preview")).toBeInTheDocument();
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
