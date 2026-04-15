import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { UploadFlow } from "@/app/(app)/games/[gameId]/upload/upload-flow";
import { server } from "./setup";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: () => {} }),
}));

class MockXHR {
  upload = { onprogress: null as null | ((e: ProgressEvent) => void) };
  status = 0;
  onload: null | (() => void) = null;
  onerror: null | (() => void) = null;
  onabort: null | (() => void) = null;
  open(_method: string, _url: string) {}
  setRequestHeader() {}
  send() {
    this.upload.onprogress?.(
      { lengthComputable: true, loaded: 50, total: 100 } as ProgressEvent,
    );
    this.upload.onprogress?.(
      { lengthComputable: true, loaded: 100, total: 100 } as ProgressEvent,
    );
    this.status = 200;
    this.onload?.();
  }
  abort() {
    this.onabort?.();
  }
}

describe("UploadFlow", () => {
  it("presigns, PUTs, and completes a single-PUT upload end to end", async () => {
    const originalXHR = window.XMLHttpRequest;
    window.XMLHttpRequest = MockXHR as unknown as typeof XMLHttpRequest;
    let initiated: unknown = null;
    server.use(
      http.post("/api/v1/videos/upload", async ({ request }) => {
        initiated = await request.json();
        return HttpResponse.json(
          {
            id: "new-video",
            upload_method: "PUT",
            upload_url: "https://signed.storage.test/new-video",
            upload_headers: { "Content-Type": "video/mp4" },
            upload_id: null,
            part_size_bytes: null,
            part_urls: null,
            expires_at: new Date(Date.now() + 3_600_000).toISOString(),
          },
          { status: 201 },
        );
      }),
      http.post("/api/v1/videos/new-video/complete", () =>
        HttpResponse.json({
          id: "new-video",
          status: "queued",
          estimated_processing_minutes: 45,
          job_id: "job-1",
        }),
      ),
    );

    const user = userEvent.setup({ applyAccept: false });
    render(<UploadFlow gameId="g1" />);
    const file = new File([new Uint8Array(200)], "clip.mp4", {
      type: "video/mp4",
    });
    await user.upload(screen.getByLabelText(/Video file/i), file);
    await user.click(screen.getByRole("button", { name: /start upload/i }));
    await waitFor(() => expect(initiated).not.toBeNull());
    expect(initiated).toMatchObject({
      game_id: "g1",
      content_type: "video/mp4",
    });
    await screen.findByText(/Upload complete/i);

    window.XMLHttpRequest = originalXHR;
  });

  it("surfaces a storage-not-configured error with a recoverable message", async () => {
    server.use(
      http.post("/api/v1/videos/upload", () =>
        HttpResponse.json(
          {
            error: {
              code: "STORAGE_NOT_CONFIGURED",
              message: "Object storage is not configured",
            },
          },
          { status: 503 },
        ),
      ),
    );
    const user = userEvent.setup();
    render(<UploadFlow gameId="g1" />);
    const file = new File([new Uint8Array(10)], "clip.mp4", {
      type: "video/mp4",
    });
    await user.upload(screen.getByLabelText(/Video file/i), file);
    await user.click(screen.getByRole("button", { name: /start upload/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/storage isn't configured/i);
  });

  it("blocks multipart-sized files client-side (no browser UI for multipart yet)", async () => {
    let called = false;
    server.use(
      http.post("/api/v1/videos/upload", () => {
        called = true;
        return HttpResponse.json({}, { status: 500 });
      }),
    );
    const user = userEvent.setup();
    render(<UploadFlow gameId="g1" />);
    // Fake a 2 GB file without actually allocating 2 GB of memory.
    const file = new File([new Uint8Array(10)], "clip.mp4", {
      type: "video/mp4",
    });
    Object.defineProperty(file, "size", { value: 2 * 1024 * 1024 * 1024 });
    await user.upload(screen.getByLabelText(/Video file/i), file);
    await user.click(screen.getByRole("button", { name: /start upload/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/multipart upload/i);
    expect(called).toBe(false);
  });

  it("prompts the user to pick a file if the submit button is pressed with nothing selected", async () => {
    const user = userEvent.setup();
    render(<UploadFlow gameId="g1" />);
    await user.click(screen.getByRole("button", { name: /start upload/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/select a file/i);
  });

  it("infers MKV content type from the filename when the browser leaves file.type empty", async () => {
    const originalXHR = window.XMLHttpRequest;
    window.XMLHttpRequest = MockXHR as unknown as typeof XMLHttpRequest;
    let initiated: unknown = null;
    server.use(
      http.post("/api/v1/videos/upload", async ({ request }) => {
        initiated = await request.json();
        return HttpResponse.json(
          {
            id: "new-video",
            upload_method: "PUT",
            upload_url: "https://signed.storage.test/new-video",
            upload_headers: { "Content-Type": "video/x-matroska" },
            upload_id: null,
            part_size_bytes: null,
            part_urls: null,
            expires_at: new Date(Date.now() + 3_600_000).toISOString(),
          },
          { status: 201 },
        );
      }),
      http.post("/api/v1/videos/new-video/complete", () =>
        HttpResponse.json({
          id: "new-video",
          status: "queued",
          estimated_processing_minutes: 45,
          job_id: "job-1",
        }),
      ),
    );

    const user = userEvent.setup();
    render(<UploadFlow gameId="g1" />);
    const file = new File([new Uint8Array(200)], "clip.mkv", {
      type: "",
    });
    const input = screen.getByLabelText(/Video file/i);
    fireEvent.change(input, { target: { files: [file] } });
    await user.click(screen.getByRole("button", { name: /start upload/i }));
    await waitFor(() => expect(initiated).not.toBeNull());
    expect(initiated).toMatchObject({
      game_id: "g1",
      content_type: "video/x-matroska",
    });
    await screen.findByText(/Upload complete/i);

    window.XMLHttpRequest = originalXHR;
  });
});
