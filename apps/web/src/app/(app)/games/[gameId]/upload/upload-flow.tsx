"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { apiJson } from "@/lib/api-client";
import { ApiError } from "@/lib/errors";
import type {
  CompleteUploadResponse,
  CreateUploadResponse,
} from "@/lib/contract";

const SINGLE_PUT_LIMIT = 1_073_741_824; // 1 GB — multipart kicks in above this.
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
  | { kind: "uploading"; percent: number }
  | { kind: "finalizing" }
  | { kind: "done"; videoId: string }
  | { kind: "error"; message: string };

export function UploadFlow({ gameId }: { gameId: string }) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const xhrRef = useRef<XMLHttpRequest | null>(null);
  const [cameraPosition, setCameraPosition] = useState<string>("sideline");
  const [cameraHeight, setCameraHeight] = useState<string>("elevated");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  useEffect(
    () => () => {
      xhrRef.current?.abort();
    },
    [],
  );

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const file = inputRef.current?.files?.[0];
    if (!file) {
      setPhase({ kind: "error", message: "Select a file first." });
      return;
    }
    if (file.size > SINGLE_PUT_LIMIT) {
      setPhase({
        kind: "error",
        message:
          "Files over 1 GB use multipart upload — not supported in the browser UI yet.",
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
          camera_position: cameraPosition,
          camera_height: cameraHeight,
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

    if (presign.upload_method !== "PUT" || !presign.upload_url) {
      setPhase({
        kind: "error",
        message:
          "The backend returned a multipart upload; browser UI only supports single-PUT today.",
      });
      return;
    }

    setPhase({ kind: "uploading", percent: 0 });
    const success = await uploadViaPut({
      url: presign.upload_url,
      headers: presign.upload_headers ?? { "Content-Type": contentType },
      file,
      xhrRef,
      onProgress: (percent) => setPhase({ kind: "uploading", percent }),
    });
    if (!success) {
      setPhase({ kind: "error", message: "Direct storage upload failed." });
      return;
    }

    setPhase({ kind: "finalizing" });
    try {
      const complete = await apiJson<CompleteUploadResponse>(
        `/videos/${presign.id}/complete`,
        {
          method: "POST",
          json: {},
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
        {phase.kind === "uploading" && (
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
  switch (err.code) {
    case "STORAGE_NOT_CONFIGURED":
      return "Object storage isn't configured on this environment. Ask an admin to set the S3 values.";
    case "INVALID_CONTENT_TYPE":
      return "Unsupported file type. Use MP4, MOV, or MKV.";
    case "FILE_TOO_LARGE":
      return "File exceeds the 10 GB limit.";
    case "INVALID_FILE_SIZE":
      return "That file size is not valid.";
    case "GAME_NOT_FOUND":
      return "That game is no longer available.";
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
    phase.kind === "finalizing"
  );
}

function phaseButtonLabel(phase: Phase): string {
  switch (phase.kind) {
    case "presigning":
      return "Preparing upload…";
    case "uploading":
      return `Uploading ${phase.percent}%`;
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
      return "Uploading directly to storage…";
    case "finalizing":
      return "Finalizing and queuing for processing…";
    default:
      return "";
  }
}

function uploadViaPut({
  url,
  headers,
  file,
  xhrRef,
  onProgress,
}: {
  url: string;
  headers: Record<string, string>;
  file: File;
  xhrRef: React.MutableRefObject<XMLHttpRequest | null>;
  onProgress: (percent: number) => void;
}): Promise<boolean> {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhrRef.current = xhr;
    xhr.open("PUT", url, true);
    for (const [key, value] of Object.entries(headers)) {
      xhr.setRequestHeader(key, value);
    }
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onload = () => {
      resolve(xhr.status >= 200 && xhr.status < 300);
    };
    xhr.onerror = () => resolve(false);
    xhr.onabort = () => resolve(false);
    xhr.send(file);
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
