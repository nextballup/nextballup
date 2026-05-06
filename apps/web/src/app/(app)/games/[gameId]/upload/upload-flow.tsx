"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { apiJson } from "@/lib/api-client";
import { ApiError, isEmailVerificationRequiredError } from "@/lib/errors";
import type {
  CompleteUploadResponse,
  CreateUploadResponse,
  TeamPrivacyConsentListResponse,
  TeamPrivacyConsentResponse,
} from "@/lib/contract";

const SINGLE_PUT_LIMIT = 1_073_741_824; // Backend caps single PUT at 1 GB.
const MAX_UPLOAD_SIZE_BYTES = 10 * 1024 ** 3; // Backend cap (see settings.max_upload_size_bytes).
const MULTIPART_CONCURRENCY = 3;
// Files above this threshold skip client-side SHA-256 to avoid browser OOM:
// WebCrypto's `subtle.digest` has no incremental API, so hashing requires the
// whole file resident in memory. The backend still verifies object size +
// S3 ETag at /complete and again at transcode, so the integrity floor is
// preserved even without a client checksum. Keep the threshold conservative.
const CHECKSUM_MAX_BYTES = 2 * 1024 ** 3;
// Chunk size for the streaming-read progress UX. Any size works for the
// subsequent single `subtle.digest` call; we read in chunks only so the
// progress bar can advance during the memory-allocation phase.
const CHECKSUM_READ_CHUNK = 8 * 1024 * 1024;
const ALLOWED_CONTENT_TYPES = new Set([
  "video/mp4",
  "video/quicktime",
  "video/x-matroska",
]);
const CONTENT_TYPE_BY_EXTENSION: Record<string, string> = {
  mp4: "video/mp4",
  mov: "video/quicktime",
  mkv: "video/x-matroska",
};

type Phase =
  | { kind: "idle" }
  | { kind: "presigning" }
  | { kind: "uploading"; percent: number; label?: string }
  | { kind: "hashing"; percent: number }
  | { kind: "finalizing" }
  | { kind: "done"; videoId: string }
  | { kind: "error"; message: string };

type XhrPool = { active: Set<XMLHttpRequest>; aborted: boolean };

function consentSupportsUpload(consent: TeamPrivacyConsentResponse): boolean {
  return (
    consent.is_active &&
    consent.covers_video_uploads &&
    consent.covers_cv_processing &&
    consent.athlete_pii_authorized
  );
}

export function UploadFlow({
  gameId,
  teamId,
}: {
  gameId: string;
  teamId?: string;
}) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement | null>(null);
  // We used to keep a single-xhr ref. Multipart needs to abort every in-flight
  // part when the component unmounts, so we track them in a set.
  const xhrPoolRef = useRef<XhrPool>({ active: new Set(), aborted: false });
  const [cameraPosition, setCameraPosition] = useState<string>("sideline");
  const [cameraHeight, setCameraHeight] = useState<string>("elevated");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [consents, setConsents] = useState<TeamPrivacyConsentResponse[]>([]);
  const [selectedConsentId, setSelectedConsentId] = useState("");
  const [consentLoadError, setConsentLoadError] = useState<string | null>(null);

  useEffect(
    () => () => {
      xhrPoolRef.current.aborted = true;
      for (const xhr of xhrPoolRef.current.active) {
        try {
          xhr.abort();
        } catch {
          /* ignore */
        }
      }
    },
    [],
  );

  useEffect(() => {
    if (!teamId) return;
    let cancelled = false;
    apiJson<TeamPrivacyConsentListResponse>(
      `/teams/${teamId}/privacy-consents`,
      {
        method: "GET",
        cache: "no-store",
      },
    )
      .then((response) => {
        if (cancelled) return;
        const uploadConsents = response.consents.filter(consentSupportsUpload);
        setConsents(uploadConsents);
        setSelectedConsentId((current) => current || uploadConsents[0]?.id || "");
        setConsentLoadError(null);
      })
      .catch(() => {
        if (!cancelled) {
          setConsentLoadError("Consent records are unavailable.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [teamId]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const file = inputRef.current?.files?.[0];
    if (!file) {
      setPhase({ kind: "error", message: "Select a file first." });
      return;
    }
    if (file.size > MAX_UPLOAD_SIZE_BYTES) {
      setPhase({
        kind: "error",
        message: "File exceeds the 10 GB upload limit.",
      });
      return;
    }
    const contentType = resolveUploadContentType(file);
    if (!contentType) {
      setPhase({
        kind: "error",
        message: `Unsupported file type "${file.type || file.name}". Use MP4, MOV, or MKV.`,
      });
      return;
    }

    let checksum: string | undefined;
    if (file.size <= CHECKSUM_MAX_BYTES && typeof crypto?.subtle?.digest === "function") {
      setPhase({ kind: "hashing", percent: 0 });
      try {
        const computedChecksum = await computeSha256Hex(file, (percent) =>
          setPhase({ kind: "hashing", percent }),
        );
        checksum = computedChecksum ?? undefined;
      } catch {
        checksum = undefined;
      }
    }

    setPhase({ kind: "presigning" });
    let presign: CreateUploadResponse;
    try {
      presign = await apiJson<CreateUploadResponse>("/videos/upload", {
        method: "POST",
        json: {
          game_id: gameId,
          filename: file.name,
          file_size_bytes: file.size,
          content_type: contentType,
          ...(checksum ? { checksum_sha256: checksum } : {}),
          camera_position: cameraPosition,
          camera_height: cameraHeight,
          ...(selectedConsentId ? { privacy_consent_id: selectedConsentId } : {}),
        },
      });
    } catch (err) {
      setPhase({
        kind: "error",
        message:
          err instanceof ApiError
            ? describeInitiationError(err)
            : "Could not start upload.",
      });
      return;
    }

    setPhase({ kind: "uploading", percent: 0 });
    let completePayload: Record<string, unknown> = checksum
      ? { checksum_sha256: checksum }
      : {};

    if (presign.upload_method === "PUT") {
      if (!presign.upload_url) {
        setPhase({
          kind: "error",
          message: "Backend returned a PUT response without an upload URL.",
        });
        return;
      }
      if (file.size > SINGLE_PUT_LIMIT) {
        // Shouldn't happen — the backend's threshold should have upgraded us
        // to MULTIPART. Surface the inconsistency explicitly rather than
        // trying to PUT a 2 GB body and letting the browser hang.
        setPhase({
          kind: "error",
          message:
            "Files over 1 GB require multipart upload but the backend did not return one.",
        });
        return;
      }
      const ok = await uploadViaPut({
        url: presign.upload_url,
        headers: presign.upload_headers ?? { "Content-Type": contentType },
        file,
        pool: xhrPoolRef.current,
        onProgress: (percent) =>
          setPhase({ kind: "uploading", percent, label: "Uploading to storage" }),
      });
      if (!ok.success) {
        setPhase({
          kind: "error",
          message: ok.message ?? "Direct storage upload failed.",
        });
        return;
      }
    } else if (presign.upload_method === "MULTIPART") {
      if (!presign.part_urls || !presign.part_size_bytes) {
        setPhase({
          kind: "error",
          message:
            "Backend returned multipart upload without part URLs or part size.",
        });
        return;
      }
      const multipart = await uploadMultipart({
        file,
        partSize: presign.part_size_bytes,
        parts: presign.part_urls,
        contentType,
        pool: xhrPoolRef.current,
        onProgress: (percent, label) =>
          setPhase({ kind: "uploading", percent, label }),
      });
      if (!multipart.success) {
        setPhase({
          kind: "error",
          message: multipart.message ?? "Multipart upload failed.",
        });
        return;
      }
      completePayload = { parts: multipart.parts };
    } else {
      setPhase({
        kind: "error",
        message: `Unsupported upload method "${presign.upload_method}".`,
      });
      return;
    }

    setPhase({ kind: "finalizing" });
    try {
      const complete = await apiJson<CompleteUploadResponse>(
        `/videos/${presign.id}/complete`,
        {
          method: "POST",
          json: completePayload,
        },
      );
      setPhase({ kind: "done", videoId: complete.id });
      router.refresh();
    } catch (err) {
      setPhase({
        kind: "error",
        message:
          err instanceof ApiError
            ? err.message
            : "The storage upload succeeded but completion failed.",
      });
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-label="Upload video"
      className="space-y-4 rounded-lg border border-[color:var(--color-nbu-border)] p-4"
    >
      <label className="block space-y-1 text-sm">
        <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
          Video file
        </span>
        <input
          ref={inputRef}
          type="file"
          accept="video/mp4,video/quicktime,video/x-matroska"
          className="block w-full text-sm file:mr-3 file:rounded-md file:border file:border-[color:var(--color-nbu-border)] file:bg-[color:var(--color-nbu-surface)] file:px-3 file:py-1.5 file:text-sm"
          disabled={isBusy(phase)}
        />
      </label>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Camera position
          </span>
          <select
            value={cameraPosition}
            onChange={(e) => setCameraPosition(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
            disabled={isBusy(phase)}
          >
            <option value="sideline">sideline</option>
            <option value="baseline">baseline</option>
            <option value="elevated_corner">elevated_corner</option>
            <option value="broadcast">broadcast</option>
            <option value="other">other</option>
          </select>
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Camera height
          </span>
          <select
            value={cameraHeight}
            onChange={(e) => setCameraHeight(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
            disabled={isBusy(phase)}
          >
            <option value="floor">floor</option>
            <option value="elevated">elevated</option>
            <option value="overhead">overhead</option>
          </select>
        </label>
      </div>
      {teamId && (
        <div className="space-y-2 rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] p-3 text-sm">
          {consents.length > 0 ? (
            <label className="block space-y-1">
              <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
                Privacy consent evidence
              </span>
              <select
                value={selectedConsentId}
                onChange={(e) => setSelectedConsentId(e.target.value)}
                className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-bg)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
                disabled={isBusy(phase)}
              >
                {consents.map((consent) => (
                  <option key={consent.id} value={consent.id}>
                    {consent.label}
                    {consent.minors_authorized ? " · minors authorized" : ""}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <p className="text-[color:var(--color-nbu-text-muted)]">
              Youth/K-12 uploads may need consent evidence recorded on the team
              page before upload.
            </p>
          )}
          {consentLoadError && (
            <p role="alert" className="text-xs text-[color:var(--color-nbu-error)]">
              {consentLoadError}
            </p>
          )}
          <a href={`/teams/${teamId}`} className="text-xs font-medium underline">
            Manage consent evidence
          </a>
        </div>
      )}

      <button
        type="submit"
        disabled={isBusy(phase)}
        className="rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90 disabled:opacity-50"
      >
        {phaseButtonLabel(phase)}
      </button>

      <UploadPhaseFeedback phase={phase} />
    </form>
  );
}

function UploadPhaseFeedback({ phase }: { phase: Phase }) {
  if (phase.kind === "idle") return null;
  if (phase.kind === "error") {
    return (
      <p role="alert" className="text-sm text-[color:var(--color-nbu-error)]">
        {phase.message}
      </p>
    );
  }
  if (phase.kind === "done") {
    return (
      <p role="status" className="text-sm text-green-600">
        Upload complete.{" "}
        <a
          href={`/videos/${phase.videoId}`}
          className="font-medium underline"
        >
          Track processing status →
        </a>
      </p>
    );
  }
  return (
    <div role="status" className="space-y-1 text-sm">
      <div className="flex items-center justify-between">
        <span>{phaseBodyLabel(phase)}</span>
        {(phase.kind === "uploading" || phase.kind === "hashing") && (
          <span className="font-mono text-xs">{phase.percent}%</span>
        )}
      </div>
      <div
        className="h-1.5 overflow-hidden rounded-full bg-[color:var(--color-nbu-surface)]"
        aria-hidden
      >
        <div
          className="h-full nbu-gradient-bg transition-all"
          style={{
            width:
              phase.kind === "uploading"
                ? `${phase.percent}%`
                : phase.kind === "hashing"
                  ? `${phase.percent}%`
                  : phase.kind === "presigning"
                    ? "8%"
                    : "95%",
          }}
        />
      </div>
    </div>
  );
}

function describeInitiationError(err: ApiError): string {
  if (isEmailVerificationRequiredError(err)) {
    return "Verify your email before uploading video.";
  }
  switch (err.code) {
    case "STORAGE_NOT_CONFIGURED":
      return "Object storage isn't configured on this environment. Ask an admin to set the S3 values.";
    case "INVALID_CONTENT_TYPE":
      return "Unsupported file type. Use MP4, MOV, or MKV.";
    case "CONTENT_TYPE_EXTENSION_MISMATCH":
      return "Filename extension doesn't match the file type. Rename the file or use a supported format.";
    case "INVALID_FILENAME":
      return "Filename contains unsupported characters. Rename the file and try again.";
    case "FILE_TOO_LARGE":
      return "File exceeds the 10 GB limit.";
    case "FILE_TOO_SMALL":
      return "File is too small to be a valid recording.";
    case "INVALID_FILE_SIZE":
      return "That file size is not valid.";
    case "GAME_NOT_FOUND":
      return "That game is no longer available.";
    case "PRIVACY_CONSENT_REQUIRED":
      return "Record or select current privacy consent evidence before uploading this team film.";
    case "PRIVACY_CONSENT_INVALID":
      return "Selected privacy consent evidence is not current or does not cover this upload.";
    case "FORBIDDEN":
      return "You need coach permissions on this team to upload video.";
    default:
      return err.message;
  }
}

function isBusy(phase: Phase): boolean {
  return (
    phase.kind === "presigning" ||
    phase.kind === "uploading" ||
    phase.kind === "hashing" ||
    phase.kind === "finalizing"
  );
}

function phaseButtonLabel(phase: Phase): string {
  switch (phase.kind) {
    case "presigning":
      return "Preparing upload…";
    case "uploading":
      return `Uploading ${phase.percent}%`;
    case "hashing":
      return "Verifying integrity…";
    case "finalizing":
      return "Finalizing…";
    case "done":
      return "Upload another";
    default:
      return "Start upload";
  }
}

function phaseBodyLabel(phase: Phase): string {
  switch (phase.kind) {
    case "presigning":
      return "Requesting a secure upload URL…";
    case "uploading":
      return phase.label ?? "Uploading directly to storage…";
    case "hashing":
      return "Computing SHA-256 checksum…";
    case "finalizing":
      return "Finalizing and queuing for processing…";
    default:
      return "";
  }
}

type PutResult = { success: true } | { success: false; message?: string };

function clampPercent(value: number): number {
  return Math.max(0, Math.min(100, value));
}

function uploadViaPut({
  url,
  headers,
  file,
  pool,
  onProgress,
}: {
  url: string;
  headers: Record<string, string>;
  file: Blob;
  pool: XhrPool;
  onProgress: (percent: number) => void;
}): Promise<PutResult> {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    pool.active.add(xhr);
    xhr.open("PUT", url, true);
    for (const [key, value] of Object.entries(headers)) {
      xhr.setRequestHeader(key, value);
    }
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(clampPercent(Math.round((event.loaded / event.total) * 100)));
      }
    };
    xhr.onload = () => {
      pool.active.delete(xhr);
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve({ success: true });
      } else {
        resolve({
          success: false,
          message: `Storage rejected upload (HTTP ${xhr.status}).`,
        });
      }
    };
    xhr.onerror = () => {
      pool.active.delete(xhr);
      resolve({ success: false, message: "Network error during upload." });
    };
    xhr.onabort = () => {
      pool.active.delete(xhr);
      resolve({ success: false, message: "Upload was cancelled." });
    };
    xhr.send(file);
  });
}

type MultipartResult =
  | {
      success: true;
      parts: Array<{ part_number: number; etag: string }>;
    }
  | { success: false; message?: string };

type PartUploadResult =
  | { success: true; etag: string }
  | { success: false; message: string };

async function uploadMultipart({
  file,
  partSize,
  parts,
  contentType,
  pool,
  onProgress,
}: {
  file: File;
  partSize: number;
  parts: ReadonlyArray<{ part_number: number; url: string }>;
  contentType: string;
  pool: XhrPool;
  onProgress: (percent: number, label: string) => void;
}): Promise<MultipartResult> {
  if (parts.length === 0) {
    return { success: false, message: "No parts returned by backend." };
  }
  // Track per-part loaded bytes so aggregate progress updates are smooth
  // rather than step-function per completed part.
  const loadedByIndex = new Array<number>(parts.length).fill(0);
  const totalBytes = file.size;
  const sortedParts = [...parts].sort((a, b) => a.part_number - b.part_number);

  const report = () => {
    const loaded = loadedByIndex.reduce((a, b) => a + b, 0);
    const percent = clampPercent(Math.round((loaded / totalBytes) * 100));
    onProgress(percent, `Uploading ${sortedParts.length} parts to storage`);
  };

  const results = new Array<{ part_number: number; etag: string } | null>(
    sortedParts.length,
  ).fill(null);

  let nextIndex = 0;
  let failure: string | null = null;

  async function runWorker(): Promise<void> {
    while (true) {
      if (failure || pool.aborted) return;
      const i = nextIndex++;
      if (i >= sortedParts.length) return;
      const part = sortedParts[i];
      const start = (part.part_number - 1) * partSize;
      const end = Math.min(start + partSize, file.size);
      const slice = file.slice(start, end, contentType);

      const outcome = await uploadPart({
        url: part.url,
        body: slice,
        pool,
        onProgress: (loaded) => {
          loadedByIndex[i] = Math.max(0, Math.min(loaded, slice.size));
          report();
        },
      });
      if (!outcome.success) {
        failure = outcome.message;
        return;
      }
      loadedByIndex[i] = slice.size;
      report();
      results[i] = { part_number: part.part_number, etag: outcome.etag };
    }
  }

  const workers = Array.from(
    { length: Math.min(MULTIPART_CONCURRENCY, sortedParts.length) },
    () => runWorker(),
  );
  await Promise.all(workers);

  if (failure) {
    return { success: false, message: failure };
  }
  if (pool.aborted) {
    return { success: false, message: "Upload was cancelled." };
  }
  const completed = results.filter(
    (r): r is { part_number: number; etag: string } => r !== null,
  );
  if (completed.length !== sortedParts.length) {
    return { success: false, message: "Not all parts finished uploading." };
  }
  return { success: true, parts: completed };
}

function uploadPart({
  url,
  body,
  pool,
  onProgress,
}: {
  url: string;
  body: Blob;
  pool: XhrPool;
  onProgress: (loaded: number) => void;
}): Promise<PartUploadResult> {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    pool.active.add(xhr);
    xhr.open("PUT", url, true);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(event.loaded);
      }
    };
    xhr.onload = () => {
      pool.active.delete(xhr);
      if (xhr.status >= 200 && xhr.status < 300) {
        // S3/MinIO return the part ETag in the response header; the whole
        // complete-multipart request depends on echoing it back.
        const etag = xhr.getResponseHeader("ETag");
        if (!etag) {
          resolve({
            success: false,
            message:
              "Storage did not expose the ETag header. Ask an admin to add ETag to CORS ExposeHeaders.",
          });
          return;
        }
        resolve({ success: true, etag: etag.replace(/^"|"$/g, "") });
      } else {
        resolve({
          success: false,
          message: `Storage rejected part (HTTP ${xhr.status}).`,
        });
      }
    };
    xhr.onerror = () => {
      pool.active.delete(xhr);
      resolve({ success: false, message: "Network error during part upload." });
    };
    xhr.onabort = () => {
      pool.active.delete(xhr);
      resolve({ success: false, message: "Part upload was cancelled." });
    };
    xhr.send(body);
  });
}

function resolveUploadContentType(file: File): string | null {
  const declared = file.type.trim().toLowerCase();
  if (declared && ALLOWED_CONTENT_TYPES.has(declared)) {
    return declared;
  }
  const extension = file.name.split(".").pop()?.trim().toLowerCase();
  if (!extension) {
    return null;
  }
  return CONTENT_TYPE_BY_EXTENSION[extension] ?? null;
}

async function computeSha256Hex(
  file: File,
  onProgress: (percent: number) => void,
): Promise<string | null> {
  if (file.size > CHECKSUM_MAX_BYTES) {
    return null;
  }
  // Stream the file into memory in chunks so the progress bar advances while
  // the browser copies bytes out of the File handle. We still pass a single
  // buffer to subtle.digest because WebCrypto has no incremental digest API.
  const total = file.size;
  const buffer = new Uint8Array(total);
  let offset = 0;
  while (offset < total) {
    const end = Math.min(offset + CHECKSUM_READ_CHUNK, total);
    const chunk = new Uint8Array(await file.slice(offset, end).arrayBuffer());
    buffer.set(chunk, offset);
    offset = end;
    onProgress(total === 0 ? 100 : Math.round((offset / total) * 95));
  }
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  onProgress(100);
  const bytes = new Uint8Array(digest);
  let hex = "";
  for (let i = 0; i < bytes.length; i += 1) {
    hex += bytes[i].toString(16).padStart(2, "0");
  }
  return hex;
}
