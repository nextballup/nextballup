import { describe, expect, it } from "vitest";
import { render, screen, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { VideoPlaybackView } from "@/app/(app)/videos/[videoId]/video-playback-view";
import type { VideoDetailResponse } from "@/lib/contract";
import { server } from "./setup";

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

function baseVideo(overrides: Partial<VideoDetailResponse> = {}): VideoDetailResponse {
  return {
    id: "v1",
    game_id: "g1",
    status: "processed",
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
    thumbnail_url: null,
    playback_url: null,
    playback_token: null,
    playback_format: null,
    token_expires_at: null,
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
