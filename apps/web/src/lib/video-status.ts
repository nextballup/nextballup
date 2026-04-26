import type { PlaybackStatus } from "@/lib/contract";

export const PLAYBACK_STATUS_LABELS: Record<PlaybackStatus, string> = {
  uploading: "Uploading",
  queued: "Queued",
  transcoding: "Transcoding",
  ready_for_playback: "Ready for playback",
  analysis_pending: "Analysis pending",
  analysis_running: "Analysis running",
  failed: "Failed",
};
