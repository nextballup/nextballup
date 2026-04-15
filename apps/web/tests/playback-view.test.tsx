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
          />,
        ),
      );
    });
    expect(
      screen.getByText(/Playback not available yet/i),
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
      render(wrap(<VideoPlaybackView initialVideo={video} />));
    });
    const player = await waitFor(() => screen.getByTestId("video-player"));
    expect(player.tagName).toBe("VIDEO");
    expect(player.getAttribute("src")).toBe(
      "https://signed.example/v1.mp4?sig=abc",
    );
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
      render(wrap(<VideoPlaybackView initialVideo={video} />));
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
});
