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
  static overReportBytes = 0;
  static delayLoad = false;
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
      {
        lengthComputable: true,
        loaded: 100 + MockXHR.overReportBytes,
        total: 100,
      } as ProgressEvent,
    );
    this.status = 200;
    if (MockXHR.delayLoad) {
      window.setTimeout(() => this.onload?.(), 25);
    } else {
      this.onload?.();
    }
  }
  abort() {
    this.onabort?.();
  }
}

/**
 * Multipart mock that echoes per-part ETag headers. The real S3/MinIO flow
 * requires the ETag header to be exposed; the UI surfaces an explicit error
 * if the header is missing, so we keep this branching visible in tests.
 */
class MultipartMockXHR {
  static calls: Array<{ url: string; partNumber?: number }> = [];
  static withheldEtag = false;
  static overReportBytes = 0;
  static delayLoad = false;
  upload = { onprogress: null as null | ((e: ProgressEvent) => void) };
  status = 0;
  onload: null | (() => void) = null;
  onerror: null | (() => void) = null;
  onabort: null | (() => void) = null;
  private _url = "";
  private _etag = "";
  open(_method: string, url: string) {
    this._url = url;
    const match = /partNumber=(\d+)/.exec(url);
    const partNumber = match ? Number(match[1]) : undefined;
    MultipartMockXHR.calls.push({ url, partNumber });
    this._etag = `etag-${partNumber ?? "single"}`;
  }
  setRequestHeader() {}
  getResponseHeader(name: string): string | null {
    if (name.toLowerCase() === "etag" && !MultipartMockXHR.withheldEtag) {
      return `"${this._etag}"`;
    }
    return null;
  }
  send(body: Blob) {
    const size = body.size;
    this.upload.onprogress?.(
      {
        lengthComputable: true,
        loaded: size + MultipartMockXHR.overReportBytes,
        total: size,
      } as ProgressEvent,
    );
    this.status = 200;
    if (MultipartMockXHR.delayLoad) {
      window.setTimeout(() => this.onload?.(), 25);
    } else {
      this.onload?.();
    }
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

  it("includes the selected privacy consent id when uploading team film", async () => {
    const originalXHR = window.XMLHttpRequest;
    window.XMLHttpRequest = MockXHR as unknown as typeof XMLHttpRequest;
    let initiated: Record<string, unknown> | null = null;
    server.use(
      http.get("/api/v1/teams/t1/privacy-consents", () =>
        HttpResponse.json({
          consents: [
            {
              id: "consent-1",
              team_id: "t1",
              recorded_by: "u1",
              label: "Club tournament waiver",
              consent_source: "written_permission",
              covers_video_uploads: true,
              covers_cv_processing: true,
              commercial_ml_training_allowed: false,
              minors_authorized: true,
              athlete_pii_authorized: true,
              evidence_uri: "r2://evidence/waiver.pdf",
              evidence_sha256: null,
              effective_at: "2026-05-06T00:00:00Z",
              expires_at: null,
              revoked_at: null,
              is_active: true,
              created_at: "2026-05-06T00:00:00Z",
            },
          ],
          total: 1,
        }),
      ),
      http.post("/api/v1/videos/upload", async ({ request }) => {
        initiated = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          {
            id: "consented-video",
            upload_method: "PUT",
            upload_url: "https://signed.storage.test/consented-video",
            upload_headers: { "Content-Type": "video/mp4" },
            upload_id: null,
            part_size_bytes: null,
            part_urls: null,
            expires_at: new Date(Date.now() + 3_600_000).toISOString(),
          },
          { status: 201 },
        );
      }),
      http.post("/api/v1/videos/consented-video/complete", () =>
        HttpResponse.json({
          id: "consented-video",
          status: "queued",
          estimated_processing_minutes: 45,
          job_id: "job-1",
        }),
      ),
    );

    const user = userEvent.setup({ applyAccept: false });
    render(<UploadFlow gameId="g1" teamId="t1" />);
    expect(await screen.findByLabelText(/privacy consent evidence/i)).toHaveValue(
      "consent-1",
    );
    const file = new File([new Uint8Array(200)], "clip.mp4", {
      type: "video/mp4",
    });
    await user.upload(screen.getByLabelText(/Video file/i), file);
    await user.click(screen.getByRole("button", { name: /start upload/i }));
    await waitFor(() => expect(initiated).not.toBeNull());
    expect(initiated).toMatchObject({
      game_id: "g1",
      privacy_consent_id: "consent-1",
    });

    window.XMLHttpRequest = originalXHR;
  });

  it("surfaces privacy consent upload gates with an actionable message", async () => {
    server.use(
      http.post("/api/v1/videos/upload", () =>
        HttpResponse.json(
          {
            error: {
              code: "PRIVACY_CONSENT_REQUIRED",
              message:
                "A current athlete/guardian privacy consent record is required for this upload",
            },
          },
          { status: 403 },
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
    expect(alert.textContent).toMatch(/privacy consent evidence/i);
  });

  it("clamps single-PUT progress when storage over-reports bytes", async () => {
    const originalXHR = window.XMLHttpRequest;
    MockXHR.overReportBytes = 10_000;
    MockXHR.delayLoad = true;
    window.XMLHttpRequest = MockXHR as unknown as typeof XMLHttpRequest;
    server.use(
      http.post("/api/v1/videos/upload", () =>
        HttpResponse.json(
          {
            id: "single-over-report-video",
            upload_method: "PUT",
            upload_url: "https://signed.storage.test/single-over-report-video",
            upload_headers: { "Content-Type": "video/mp4" },
            upload_id: null,
            part_size_bytes: null,
            part_urls: null,
            expires_at: new Date(Date.now() + 3_600_000).toISOString(),
          },
          { status: 201 },
        ),
      ),
      http.post("/api/v1/videos/single-over-report-video/complete", () =>
        HttpResponse.json({
          id: "single-over-report-video",
          status: "queued",
          estimated_processing_minutes: 45,
          job_id: "job-1",
        }),
      ),
    );

    const user = userEvent.setup({ applyAccept: false });
    render(<UploadFlow gameId="g1" />);
    const file = new File([new Uint8Array(128)], "over-report.mp4", {
      type: "video/mp4",
    });
    await user.upload(screen.getByLabelText(/Video file/i), file);
    await user.click(screen.getByRole("button", { name: /start upload/i }));

    await screen.findByText("100%");
    expect(screen.queryByText(/-\d+%/)).not.toBeInTheDocument();
    expect(screen.queryByText(/(?:10[1-9]|[2-9]\d{2,})%/)).not.toBeInTheDocument();
    await screen.findByText(/Upload complete/i);

    MockXHR.overReportBytes = 0;
    MockXHR.delayLoad = false;
    window.XMLHttpRequest = originalXHR;
  });

  it("rejects files above the hard 10 GB upload cap without hitting the API", async () => {
    let called = false;
    server.use(
      http.post("/api/v1/videos/upload", () => {
        called = true;
        return HttpResponse.json({}, { status: 500 });
      }),
    );
    const user = userEvent.setup();
    render(<UploadFlow gameId="g1" />);
    // Fake an 11 GB file without actually allocating 11 GB of memory.
    const file = new File([new Uint8Array(10)], "clip.mp4", {
      type: "video/mp4",
    });
    Object.defineProperty(file, "size", { value: 11 * 1024 * 1024 * 1024 });
    await user.upload(screen.getByLabelText(/Video file/i), file);
    await user.click(screen.getByRole("button", { name: /start upload/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/10 GB/i);
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

  it("uploads a multipart file part-by-part and sends ETags back to /complete", async () => {
    const originalXHR = window.XMLHttpRequest;
    MultipartMockXHR.calls = [];
    MultipartMockXHR.withheldEtag = false;
    MultipartMockXHR.overReportBytes = 0;
    MultipartMockXHR.delayLoad = false;
    window.XMLHttpRequest = MultipartMockXHR as unknown as typeof XMLHttpRequest;

    let completionBody: unknown = null;
    server.use(
      http.post("/api/v1/videos/upload", () =>
        HttpResponse.json(
          {
            id: "big-video",
            upload_method: "MULTIPART",
            upload_url: null,
            upload_headers: null,
            upload_id: "mpid-42",
            part_size_bytes: 64,
            part_urls: [
              { part_number: 1, url: "https://signed/1?partNumber=1" },
              { part_number: 2, url: "https://signed/2?partNumber=2" },
            ],
            expires_at: new Date(Date.now() + 3_600_000).toISOString(),
          },
          { status: 201 },
        ),
      ),
      http.post("/api/v1/videos/big-video/complete", async ({ request }) => {
        completionBody = await request.json();
        return HttpResponse.json({
          id: "big-video",
          status: "queued",
          estimated_processing_minutes: 45,
          job_id: "job-1",
        });
      }),
    );

    const user = userEvent.setup({ applyAccept: false });
    render(<UploadFlow gameId="g1" />);
    // Build a small "file" but claim it's large enough to be multipart on the
    // backend. We don't test the size threshold client-side because the
    // backend is the source of truth — the UI just respects whatever method
    // the backend chose.
    const file = new File([new Uint8Array(128)], "big.mp4", {
      type: "video/mp4",
    });
    await user.upload(screen.getByLabelText(/Video file/i), file);
    await user.click(screen.getByRole("button", { name: /start upload/i }));
    await screen.findByText(/Upload complete/i);

    // Each part must have been PUT to its signed URL.
    const parts = MultipartMockXHR.calls.filter((c) => c.partNumber != null);
    expect(parts.map((c) => c.partNumber).sort()).toEqual([1, 2]);

    // /complete payload must carry the right ETags keyed to part numbers.
    expect(completionBody).toMatchObject({
      parts: [
        { part_number: 1, etag: "etag-1" },
        { part_number: 2, etag: "etag-2" },
      ],
    });

    window.XMLHttpRequest = originalXHR;
  });

  it("computes a SHA-256 checksum and sends it through initiation and completion", async () => {
    const originalXHR = window.XMLHttpRequest;
    window.XMLHttpRequest = MockXHR as unknown as typeof XMLHttpRequest;

    let completionBody: Record<string, unknown> | null = null;
    let initiationBody: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/v1/videos/upload", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        initiationBody = body;
        return HttpResponse.json(
          {
            id: "checksum-video",
            upload_method: "PUT",
            upload_url: "https://signed.storage.test/checksum-video",
            upload_headers: {
              "Content-Type": "video/mp4",
              "x-amz-meta-nbu-sha256": String(body.checksum_sha256),
            },
            upload_id: null,
            part_size_bytes: null,
            part_urls: null,
            expires_at: new Date(Date.now() + 3_600_000).toISOString(),
          },
          { status: 201 },
        );
      }),
      http.post("/api/v1/videos/checksum-video/complete", async ({ request }) => {
        completionBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          id: "checksum-video",
          status: "queued",
          estimated_processing_minutes: 45,
          job_id: "job-1",
        });
      }),
    );

    const user = userEvent.setup({ applyAccept: false });
    render(<UploadFlow gameId="g1" />);
    // Deterministic body so we can assert the known SHA-256 of 8 zero bytes.
    const file = new File([new Uint8Array(8)], "clip.mp4", {
      type: "video/mp4",
    });
    await user.upload(screen.getByLabelText(/Video file/i), file);
    await user.click(screen.getByRole("button", { name: /start upload/i }));
    await screen.findByText(/Upload complete/i);

    // SHA-256 of eight 0x00 bytes.
    expect(initiationBody).toMatchObject({
      checksum_sha256:
        "af5570f5a1810b7af78caf4bc70a660f0df51e42baf91d4de5b2328de0e83dfc",
    });
    expect(completionBody).toMatchObject({
      checksum_sha256:
        "af5570f5a1810b7af78caf4bc70a660f0df51e42baf91d4de5b2328de0e83dfc",
    });

    window.XMLHttpRequest = originalXHR;
  });

  it("skips client-side SHA-256 for files above the browser-memory threshold", async () => {
    // Any file big enough to cross the 2 GB checksum threshold is already
    // well above the 1 GB single-PUT limit, so the only realistic exerciser
    // of the skip path is the multipart flow.
    const originalXHR = window.XMLHttpRequest;
    MultipartMockXHR.calls = [];
    MultipartMockXHR.withheldEtag = false;
    MultipartMockXHR.overReportBytes = 0;
    MultipartMockXHR.delayLoad = false;
    window.XMLHttpRequest = MultipartMockXHR as unknown as typeof XMLHttpRequest;

    let completionBody: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/v1/videos/upload", () =>
        HttpResponse.json(
          {
            id: "huge-video",
            upload_method: "MULTIPART",
            upload_url: null,
            upload_headers: null,
            upload_id: "mpid-huge",
            part_size_bytes: 64,
            part_urls: [
              { part_number: 1, url: "https://signed/1?partNumber=1" },
            ],
            expires_at: new Date(Date.now() + 3_600_000).toISOString(),
          },
          { status: 201 },
        ),
      ),
      http.post("/api/v1/videos/huge-video/complete", async ({ request }) => {
        completionBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          id: "huge-video",
          status: "queued",
          estimated_processing_minutes: 45,
          job_id: "job-1",
        });
      }),
    );

    const user = userEvent.setup({ applyAccept: false });
    render(<UploadFlow gameId="g1" />);
    const file = new File([new Uint8Array(64)], "huge.mp4", {
      type: "video/mp4",
    });
    // Lie about the size to cross the client-side checksum threshold without
    // allocating gigabytes of test memory.
    Object.defineProperty(file, "size", { value: 3 * 1024 ** 3 });
    await user.upload(screen.getByLabelText(/Video file/i), file);
    await user.click(screen.getByRole("button", { name: /start upload/i }));
    await screen.findByText(/Upload complete/i);

    // Above CHECKSUM_MAX_BYTES the client must *not* attempt to hash — the
    // backend's size + ETag check is the integrity floor for these uploads.
    expect(completionBody).not.toHaveProperty("checksum_sha256");

    window.XMLHttpRequest = originalXHR;
  });

  it("surfaces a clear error when storage hides the ETag header", async () => {
    const originalXHR = window.XMLHttpRequest;
    MultipartMockXHR.calls = [];
    MultipartMockXHR.withheldEtag = true;
    MultipartMockXHR.overReportBytes = 0;
    MultipartMockXHR.delayLoad = false;
    window.XMLHttpRequest = MultipartMockXHR as unknown as typeof XMLHttpRequest;

    server.use(
      http.post("/api/v1/videos/upload", () =>
        HttpResponse.json(
          {
            id: "hidden-etag-video",
            upload_method: "MULTIPART",
            upload_url: null,
            upload_headers: null,
            upload_id: "mpid-hidden",
            part_size_bytes: 64,
            part_urls: [
              { part_number: 1, url: "https://signed/1?partNumber=1" },
            ],
            expires_at: new Date(Date.now() + 3_600_000).toISOString(),
          },
          { status: 201 },
        ),
      ),
    );

    const user = userEvent.setup({ applyAccept: false });
    render(<UploadFlow gameId="g1" />);
    const file = new File([new Uint8Array(64)], "big.mp4", {
      type: "video/mp4",
    });
    await user.upload(screen.getByLabelText(/Video file/i), file);
    await user.click(screen.getByRole("button", { name: /start upload/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/ETag/i);

    window.XMLHttpRequest = originalXHR;
  });

  it("clamps multipart progress when a storage client over-reports part bytes", async () => {
    const originalXHR = window.XMLHttpRequest;
    MultipartMockXHR.calls = [];
    MultipartMockXHR.withheldEtag = false;
    MultipartMockXHR.overReportBytes = 10_000;
    MultipartMockXHR.delayLoad = true;
    window.XMLHttpRequest = MultipartMockXHR as unknown as typeof XMLHttpRequest;
    server.use(
      http.post("/api/v1/videos/upload", () =>
        HttpResponse.json(
          {
            id: "over-report-video",
            upload_method: "MULTIPART",
            upload_url: null,
            upload_headers: null,
            upload_id: "mpid-over-report",
            part_size_bytes: 64,
            part_urls: [
              { part_number: 1, url: "https://signed/1?partNumber=1" },
              { part_number: 2, url: "https://signed/2?partNumber=2" },
            ],
            expires_at: new Date(Date.now() + 3_600_000).toISOString(),
          },
          { status: 201 },
        ),
      ),
      http.post("/api/v1/videos/over-report-video/complete", () =>
        HttpResponse.json({
          id: "over-report-video",
          status: "queued",
          estimated_processing_minutes: 45,
          job_id: "job-1",
        }),
      ),
    );

    const user = userEvent.setup({ applyAccept: false });
    render(<UploadFlow gameId="g1" />);
    const file = new File([new Uint8Array(128)], "over-report.mp4", {
      type: "video/mp4",
    });
    await user.upload(screen.getByLabelText(/Video file/i), file);
    await user.click(screen.getByRole("button", { name: /start upload/i }));

    await screen.findByText("100%");
    expect(screen.queryByText(/-\d+%/)).not.toBeInTheDocument();
    expect(screen.queryByText(/(?:10[1-9]|[2-9]\d{2,})%/)).not.toBeInTheDocument();
    await screen.findByText(/Upload complete/i);

    MultipartMockXHR.overReportBytes = 0;
    MultipartMockXHR.delayLoad = false;
    window.XMLHttpRequest = originalXHR;
  });
});
