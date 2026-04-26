/**
 * TypeScript mirrors of the backend response shapes the frontend relies on.
 *
 * These are intentionally hand-written (rather than generated) so future
 * backend drift produces a compile error here before it produces a runtime
 * error in a component. If you add a field to a Python schema, add it here
 * and let the compiler point you at the downstream consumer.
 */

export type UserRole = "coach" | "player" | "admin";

export type TeamRole =
  | "head_coach"
  | "assistant_coach"
  | "manager"
  | "player"
  | "captain";

export type GameType =
  | "scrimmage"
  | "preseason"
  | "regular_season"
  | "tournament"
  | "playoff"
  | "practice"
  | "film_exchange";

export type GameStatus =
  | "scheduled"
  | "uploading"
  | "processing"
  | "completed"
  | "failed";

export type VideoStatus =
  | "pending_upload"
  | "uploading"
  | "uploaded"
  | "transcoding"
  | "queued"
  | "processing"
  | "processed"
  | "failed";

export type DemoPreviewStatus =
  | "idle"
  | "queued"
  | "running"
  | "completed"
  | "failed";

export type PlaybackStatus =
  | "uploading"
  | "queued"
  | "transcoding"
  | "ready_for_playback"
  | "analysis_pending"
  | "analysis_running"
  | "failed";

export type ProcessingStageStatus = {
  status: string;
  progress_percent?: number | null;
  completed_at?: string | null;
  error_message?: string | null;
};

export type TeamMembershipSummary = {
  id: string;
  name: string;
  role_in_team: TeamRole;
};

export type UserPublic = {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  teams: TeamMembershipSummary[];
};

export type Sport = "basketball" | "volleyball";
export type TeamLevel =
  | "youth"
  | "aau_club"
  | "middle_school"
  | "high_school"
  | "juco"
  | "college_d3"
  | "college_d2"
  | "college_d1"
  | "professional"
  | "international";
export type InstitutionType =
  | "none"
  | "k12_school"
  | "college"
  | "club"
  | "academy"
  | "professional";

export type TeamListEntry = {
  id: string;
  name: string;
  sport: Sport;
  level: TeamLevel;
  institution: string | null;
  institution_type: InstitutionType;
  season: string;
  invite_code: string | null;
  my_team_role: TeamRole;
  member_count: number;
  game_count: number;
};

export type TeamListResponse = {
  teams: TeamListEntry[];
};

export type TeamMember = {
  user_id: string;
  full_name: string;
  role: UserRole;
  team_role: TeamRole;
  jersey_number: number | null;
  joined_at: string;
};

export type TeamDetailResponse = {
  id: string;
  name: string;
  sport: Sport;
  level: TeamLevel;
  institution: string | null;
  institution_type: InstitutionType;
  season: string;
  invite_code: string | null;
  my_team_role: TeamRole;
  members: TeamMember[];
  member_count: number;
};

export type TeamCreatedResponse = {
  id: string;
  name: string;
  sport: Sport;
  level: TeamLevel;
  institution: string | null;
  institution_type: InstitutionType;
  season: string;
  invite_code: string;
  created_at: string;
  member_count: number;
};

export type CreateInviteResponse = {
  invite_code: string;
  invite_url: string;
  expires_at: string;
  remaining_uses: number;
  role: TeamRole;
};

export type JoinTeamResponse = {
  id: string;
  name: string;
  sport: Sport;
  level: TeamLevel;
  institution: string | null;
  institution_type: InstitutionType;
  season: string;
  invite_code: string | null;
  membership: TeamMember;
};

export type GameSummary = {
  id: string;
  team_id: string;
  opponent_name: string | null;
  game_type: GameType;
  date: string; // YYYY-MM-DD
  time: string | null; // HH:MM:SS
  location: string | null;
  is_home: boolean;
  status: GameStatus;
  score_team: number | null;
  score_opponent: number | null;
  notes: string | null;
  periods: number;
  period_length_minutes: number;
  shot_clock_enabled: boolean;
  shot_clock_seconds: number | null;
  created_at: string;
};

export type GameListResponse = {
  games: GameSummary[];
  total: number;
  page: number;
  per_page: number;
  has_next: boolean;
};

export type CreateUploadResponse = {
  id: string;
  upload_method: "PUT" | "MULTIPART";
  upload_url: string | null;
  upload_headers: Record<string, string> | null;
  upload_id: string | null;
  part_size_bytes: number | null;
  part_urls:
    | Array<{
        part_number: number;
        url: string;
      }>
    | null;
  expires_at: string;
};

export type CompleteUploadResponse = {
  id: string;
  status: VideoStatus;
  estimated_processing_minutes: number;
  job_id: string;
};

export type VideoDetailResponse = {
  id: string;
  game_id: string;
  status: VideoStatus;
  playback_status: PlaybackStatus;
  filename: string;
  file_size_bytes: number | null;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  codec: string | null;
  camera_position: string | null;
  camera_height: string | null;
  checksum_sha256: string | null;
  storage_etag: string | null;
  storage_output_sha256: string | null;
  privacy_consent_id: string | null;
  raw_retention_expires_at: string | null;
  raw_deleted_at: string | null;
  thumbnail_url: string | null;
  playback_url: string | null;
  playback_token: string | null;
  playback_format: string | null; // "hls" | "mp4"
  token_expires_at: string | null;
  demo_preview_enabled: boolean;
  demo_preview_status: DemoPreviewStatus;
  demo_preview_url: string | null;
  demo_preview_generated_at: string | null;
  demo_preview_error_message: string | null;
  processing: Record<string, string>;
  created_at: string;
};

export type ReviewStatus =
  | "machine_only"
  | "needs_review"
  | "approved"
  | "rejected";

export type VideoEventType =
  | "shot_attempt"
  | "shot_made"
  | "rebound"
  | "pass";

export type VideoEventSummary = {
  id: string;
  event_type: VideoEventType;
  event_time_ms: number;
  output_frame: number;
  period: number | null;
  game_clock_ms: number | null;
  shot_clock_enabled: boolean;
  shot_clock_ms: number | null;
  primary_track_key: string | null;
  confidence: number | null;
  review_status: ReviewStatus;
  created_at: string;
};

export type VideoEventsResponse = {
  video_id: string;
  shot_clock_enabled: boolean;
  shot_clock_seconds: number | null;
  events: VideoEventSummary[];
  total: number;
};

export type GenerateDemoPreviewResponse = {
  status: DemoPreviewStatus;
  preview_url: string | null;
  generated_at: string | null;
};

export type VideoStatusResponse = {
  status: VideoStatus;
  playback_status: PlaybackStatus;
  stage: string | null;
  progress_percent: number;
  stages: Record<string, ProcessingStageStatus>;
};

export type VideoListItem = {
  id: string;
  filename: string;
  status: VideoStatus;
  playback_status: PlaybackStatus;
  file_size_bytes: number | null;
  duration_seconds: number | null;
  camera_position: string | null;
  camera_height: string | null;
  created_at: string;
};

export type VideoListResponse = {
  videos: VideoListItem[];
  total: number;
};

export type ApiErrorBody = {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
  request_id?: string;
};

export type AuditLogEntry = {
  id: string;
  created_at: string;
  action: string;
  actor_user_id: string | null;
  actor_email: string | null;
  resource_type: string | null;
  resource_id: string | null;
  team_id: string | null;
  ip_address: string | null;
  user_agent: string | null;
  request_id: string | null;
  extra: Record<string, unknown> | null;
};

export type AuditLogPage = {
  items: AuditLogEntry[];
  next_cursor: string | null;
};

export const VIDEO_TERMINAL_STATUSES: ReadonlyArray<VideoStatus> = [
  "processed",
  "failed",
];

export const GAME_TERMINAL_STATUSES: ReadonlyArray<GameStatus> = [
  "completed",
  "failed",
];

/**
 * Pipeline stages the backend currently executes. `transcode` is the only
 * stage the Phase 6 worker actually drives — the rest are reserved for
 * downstream CV phases but exist in the response shape for forward
 * compatibility. Calling the downstream stages "pending" in the UI gives
 * coaches the false impression they are merely queued; we label them
 * separately.
 */
export const IMPLEMENTED_PIPELINE_STAGES: ReadonlySet<string> = new Set([
  "transcode",
]);

export const CV_PIPELINE_STAGES: ReadonlyArray<string> = [
  "detection",
  "tracking",
  "court_mapping",
  "events",
  "metrics",
];
