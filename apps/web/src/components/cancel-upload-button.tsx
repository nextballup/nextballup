"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { apiVoid } from "@/lib/api-client";
import { ApiError } from "@/lib/errors";

type CancelUploadButtonProps = {
  videoId: string;
  label?: string;
  compact?: boolean;
  onCancelled?: () => void;
};

export function CancelUploadButton({
  videoId,
  label = "Cancel upload",
  compact = false,
  onCancelled,
}: CancelUploadButtonProps) {
  const router = useRouter();
  const [isCancelling, setIsCancelling] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleCancel() {
    setIsCancelling(true);
    setErrorMessage(null);
    try {
      await apiVoid(`/videos/${videoId}/cancel-upload`, { method: "POST" });
      onCancelled?.();
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMessage(err.message);
      } else {
        setErrorMessage("Unable to cancel this upload.");
      }
    } finally {
      setIsCancelling(false);
    }
  }

  return (
    <div className={compact ? "flex flex-col items-end gap-1" : "space-y-1"}>
      <button
        type="button"
        onClick={handleCancel}
        disabled={isCancelling}
        className={
          compact
            ? "rounded-md border border-[color:var(--color-nbu-border)] px-2 py-1 text-xs font-medium transition hover:bg-[color:var(--color-nbu-surface)] disabled:opacity-50"
            : "rounded-md border border-[color:var(--color-nbu-border)] px-3 py-1.5 text-sm font-medium transition hover:bg-[color:var(--color-nbu-surface)] disabled:opacity-50"
        }
      >
        {isCancelling ? "Cancelling..." : label}
      </button>
      {errorMessage ? (
        <span
          role="alert"
          className={
            compact
              ? "max-w-[12rem] text-right text-xs text-[color:var(--color-nbu-error)]"
              : "block text-sm text-[color:var(--color-nbu-error)]"
          }
        >
          {errorMessage}
        </span>
      ) : null}
    </div>
  );
}
