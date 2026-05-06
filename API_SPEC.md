# NextBallUp API Specification

## Base URL

```
Development: http://127.0.0.1:8000/api/v1
Production:  https://api.nextballup.com/v1
```

**Implemented API surface in the current repo:** health, internal metrics,
auth, teams, games, videos, and a small admin surface (`/admin/audit/logs`).

Sections later in this document that describe `/users`, `/events`, `/players`,
`/metrics`, `/clips`, `/scouting`, `/search`, `/alerts`, and `/notes` are
**planned product/API targets**, not current runnable endpoints, unless and
until the codebase adds those routers.

## Authentication

Authentication is cookie-first. `/auth/register`, `/auth/login`, and
`/auth/refresh` set two httpOnly cookies (access + refresh) plus a readable
CSRF cookie. The browser never sees the JWT itself — the cookies carry it.

Tokens are RS256 JWTs. Access tokens expire in 15 minutes. Refresh tokens
expire in 7 days. Logout bumps a per-user `session_version` so every
outstanding token stops validating even if it was copied elsewhere.

### Cookies set on successful auth

| Cookie | httpOnly | Purpose |
|---|---|---|
| `nbu_access_token` (or `__Host-nbu_access_token` when `COOKIE_HOST_PREFIX` + `COOKIE_SECURE` are enabled) | yes | Short-lived bearer for request auth |
| `nbu_refresh_token` | yes | Refresh-only JWT, path-scoped to `/api/v1/auth/refresh` |
| `nbu_csrf_token` (or `__Host-…`) | **no** | Double-submit CSRF token the browser mirrors into `X-CSRF-Token` on mutating requests |

### Non-browser / service clients

Service clients can still use the bearer-header path. Read the access
cookie off the `Set-Cookie` header of `/auth/login` and send it back as
`Authorization: Bearer …` on subsequent calls. Bearer-authenticated
mutations are CSRF-exempt; cookie-authenticated mutations must echo the
CSRF cookie into the `X-CSRF-Token` header or they are rejected with
`403 CSRF_FAILED`.

### Access token payload

```json
{
  "sub": "uuid",
  "role": "coach | player | admin",
  "team_ids": ["uuid", "uuid"],
  "sv": 1,
  "type": "access",
  "iat": 1714000000,
  "exp": 1714000900
}
```

---

## Health Checks

### GET `/health`

Returns 200 if the API process is running. No dependency checks.

**Response: 200**
```json
{ "status": "ok", "version": "0.1.0" }
```

### GET `/health/ready`

Returns 200 only if database and Redis are reachable.

**Response: 200**
```json
{ "status": "ready", "database": "ok", "redis": "ok", "storage": "ok" }
```

**Response: 503** (if any dependency is down)
```json
{ "status": "not_ready", "database": "ok", "redis": "timeout", "storage": "ok" }
```

If Redis and storage are not configured in the current environment, local/dev
setups may return `"not_configured"` for those fields while still returning 200.
Production environments should configure and require all three.

### GET `/health/live`

Kubernetes liveness probe. Returns 200 if the process is not deadlocked.

**Response: 200**
```json
{ "status": "alive" }
```

### GET `/api/v1/_metrics`

Internal Prometheus-format operational metrics. Disabled unless
`OBSERVABILITY_METRICS_ENABLED=true`; when enabled, callers must provide the
opaque shared secret in `X-Metrics-Token`. Failed scrapes are audit logged.
Metrics intentionally use aggregate operational labels only, not team/user
identifiers.

**Response: 200** — `text/plain; version=0.0.4`

Worker metrics are emitted by each Celery worker process on a separate
loopback-only listener when `OBSERVABILITY_WORKER_METRICS_ENABLED=true`.
Infrastructure should scrape those worker endpoints directly from the worker
host/container. The API endpoint is not a cross-process worker metrics proxy.

---

## Auth Routes — `/auth`

### GET `/auth/registration/status`

Return the deployment's registration posture without leaking invite codes or
allowlisted emails. Public pages use this to render the correct registration
UI; the backend remains authoritative.

**Response: 200**
```json
{
  "mode": "invite_only",
  "invite_code_required": true,
  "is_open_to_public": false
}
```

`mode` is one of `open`, `invite_only`, `allowlist`, or `disabled`.

### POST `/auth/register`

Create a new user account. User chooses role at registration. Deployments can
gate this endpoint with `REGISTRATION_MODE`; staging/production refuse to boot
with public-open registration.

**Request:**
```json
{
  "email": "coach@example.com",
  "password": "min8chars!",
  "full_name": "Mike Johnson",
  "role": "coach",
  "phone": "+15551234567",       // optional
  "institution": "Lincoln High", // optional, free text
  "invite_code": "PILOT-CODE-AAAA" // required only when mode=invite_only
}
```

**Validation:**
- `role` must be `"coach"` or `"player"` (admin is internal only)
- `email` must be unique, valid format
- `password` minimum 8 characters, at least 1 number and 1 uppercase letter
- `phone` and `institution` are optional, max-length-bounded free-text fields
- `invite_code` is optional in open/allowlist/disabled modes and required in
  `invite_only` mode

**Current scope note:**
- COPPA / parental-consent onboarding is **not** implemented in this phase.
- Do not represent this registration flow as legally complete for under-13 users yet.

**Response: 201**
```json
{
  "id": "uuid",
  "email": "coach@example.com",
  "full_name": "Mike Johnson",
  "role": "coach",
  "created_at": "2026-05-01T00:00:00Z"
}
```

Three `Set-Cookie` headers ride along on success: the access cookie,
the refresh cookie, and the CSRF cookie. The access + CSRF cookies may be
prefixed with `__Host-` when `COOKIE_HOST_PREFIX=true` and
`COOKIE_SECURE=true`. The refresh cookie intentionally stays unprefixed so
it can be scoped narrowly to `/api/v1/auth/refresh`.

**Errors:** 403 registration disabled / invite required / invite invalid /
not allowlisted · 409 email exists · 422 validation failed · 429 rate limited

### POST `/auth/login`

**Request:**
```json
{
  "email": "coach@example.com",
  "password": "min8chars!"
}
```

**Response: 200**
```json
{
  "user": {
    "id": "uuid",
    "email": "coach@example.com",
    "full_name": "Mike Johnson",
    "role": "coach",
    "teams": [
      { "id": "uuid", "name": "Lincoln Varsity", "role_in_team": "head_coach" }
    ]
  }
}
```

Access, refresh, and CSRF cookies are set on the response. JWTs are never
included in the response body.

**Errors:** 401 invalid credentials · 429 rate limited (5/min)

### POST `/auth/password/forgot`

Request a password-reset email. The response is intentionally generic and
does not reveal whether the email belongs to an active account.

**Request:**
```json
{
  "email": "coach@example.com"
}
```

**Response: 202**
```json
{
  "requested_at": "2026-05-01T00:00:00Z",
  "delivery": "ses"
}
```

The stored reset token is SHA-256 hashed, single-use, supersedes any
older unused reset tokens for the same user, and expires after
`PASSWORD_RESET_TOKEN_TTL_MINUTES`.

**Errors:** 422 validation failed · 429 rate limited

### POST `/auth/password/reset`

Consume a password-reset token and set a new password. On success, the
server bumps `session_version`, revokes refresh sessions with reason
`password_reset`, marks pending reset tokens used, and clears auth/CSRF
cookies on the response.

**Request:**
```json
{
  "token": "opaque-reset-token-from-email",
  "new_password": "NewPassword1!"
}
```

**Response: 200**
```json
{ "reset_at": "2026-05-01T00:00:00Z" }
```

**Errors:** 400 invalid/expired token · 409 token already used · 422 weak password

### POST `/auth/refresh`

Rotate the access + refresh cookies. Cookie-only: the refresh JWT is read
exclusively from the `nbu_refresh_token` cookie, never from the request
body. Passing a legacy `{"refresh_token": "…"}` body is rejected with
`422` so a stolen token cannot be replayed through the JSON contract.

**Request:**
```json
{}
```

**Response: 200**
```json
{ "refreshed_at": "2026-11-16T00:00:00Z" }
```

New access + refresh cookies are attached to the response; the old
refresh cookie is invalidated by rotation.

**Errors:** 401 missing/expired refresh cookie · 422 legacy body fields

### POST `/auth/logout`

Invalidates the current session by rotating a server-side session version, which
causes previously issued access and refresh tokens to stop working.

**Response: 204** No content

### GET `/auth/me`

Returns current user profile.

**Response: 200** — Same shape as `user` object in login response.

### GET `/auth/me/export`

GDPR Art. 15 self-serve access: returns every row keyed to the caller
(profile, active + inactive memberships, videos they uploaded, audit
events where they were the actor, refresh sessions, verification and
reset-token metadata, MFA enrollment summary, owned billing accounts,
member-team usage events, recorded privacy consents, and attributed CSP
reports) in a single JSON bundle. Secret material and token hashes are
not returned.

**Response: 200**
```json
{
  "exported_at": "2026-05-01T00:00:00Z",
  "user": { "id": "uuid", "email": "coach@example.com", "full_name": "Mike Johnson", "role": "coach" },
  "team_memberships": [ ... ],
  "videos_uploaded": [ ... ],
  "audit_events": [ ... ]
}
```

Tenant-owned rows that the caller can merely *see* (teammates' videos,
team-wide audit events) are intentionally **not** included; those belong
in a tenant-owner export we don't expose yet.

### DELETE `/auth/me`

GDPR Art. 17 self-serve erasure: anonymizes the user row rather than
hard-deleting so FKs on audits and videos stay valid. Personal fields
(name, phone, institution, avatar, player biometrics, parental consent
flags) are scrubbed, the email is rewritten to a deterministic
nonresolvable address, the password is replaced with a bcrypt-invalid
sentinel, `session_version` is bumped so outstanding tokens fail, and
every membership is deactivated.

**Response: 200**
```json
{ "deleted_at": "2026-05-01T00:00:00Z", "user_id": "uuid" }
```

Auth + CSRF cookies are cleared on the response.

---

## Users — `/users`

### PATCH `/users/{user_id}`

Update own profile. Users can only update themselves (enforced by auth).

**Request (partial update):**
```json
{
  "full_name": "Michael Johnson",
  "phone": "+15559876543",
  "avatar_url": "https://...",
  "height_inches": 74,
  "weight_lbs": 195,
  "position": "SG",
  "graduation_year": 2027,
  "handedness": "right"
}
```

**Notes:**
- `height_inches`, `weight_lbs`, `position`, `graduation_year`, `handedness` are only settable when `role == "player"`
- `position` enum: `"PG" | "SG" | "SF" | "PF" | "C" | "G" | "F" | "UTIL"`

**Response: 200** — Updated user object

### GET `/users/{user_id}`

Get a user's public profile. Accessible by teammates and coaches on the same team.

**Response: 200**
```json
{
  "id": "uuid",
  "full_name": "Michael Johnson",
  "role": "player",
  "position": "SG",
  "height_inches": 74,
  "graduation_year": 2027,
  "teams": [
    { "id": "uuid", "name": "Lincoln Varsity", "jersey_number": 23 }
  ],
  "avatar_url": "https://..."
}
```

**Access control:** Only visible to users who share at least one team. Admins see all.

---

## Teams — `/teams`

### POST `/teams`

Create a new team. Only coaches can create teams.

**Request:**
```json
{
  "name": "Lincoln Varsity Boys",
  "sport": "basketball",
  "level": "high_school",
  "institution": "Lincoln High School",
  "institution_type": "high_school",
  "season": "2026-2027",
  "city": "Houston",
  "state": "TX",
  "conference": "District 18-6A"
}
```

**Validation:**
- `sport` enum: `"basketball"` (expand later: `"volleyball"`)
- `level` enum: `"youth" | "aau_club" | "middle_school" | "high_school" | "juco" | "college_d3" | "college_d2" | "college_d1" | "professional" | "international"`
- `institution_type` enum: `"none" | "k12_school" | "college" | "club" | "academy" | "professional"`
- Creator becomes `head_coach` of the team automatically

**Response: 201**
```json
{
  "id": "uuid",
  "name": "Lincoln Varsity Boys",
  "sport": "basketball",
  "level": "high_school",
  "institution": "Lincoln High School",
  "institution_type": "high_school",
  "season": "2026-2027",
  "invite_code": "LVB-2026-X7K9",
  "created_at": "2026-05-01T00:00:00Z",
  "member_count": 1
}
```

### GET `/teams`

List teams the caller currently has an **active** membership in. Players
get `invite_code: null`; coaches and admins see the live invite code.

**Response: 200**
```json
{
  "teams": [
    {
      "id": "uuid",
      "name": "Lincoln Varsity Boys",
      "sport": "basketball",
      "level": "high_school",
      "institution": "Lincoln High School",
      "institution_type": "k12_school",
      "season": "2026-2027",
      "invite_code": "LVB-2026-X7K9",
      "my_team_role": "head_coach",
      "member_count": 15,
      "game_count": 12
    }
  ]
}
```

### GET `/teams/{team_id}/members`

Flat member roster. Same access control as `GET /teams/{team_id}`.

**Response: 200**
```json
{
  "members": [
    {
      "user_id": "uuid",
      "full_name": "Mike Johnson",
      "role": "coach",
      "team_role": "head_coach",
      "jersey_number": null,
      "joined_at": "2026-05-01T00:00:00Z"
    }
  ],
  "total": 15
}
```

### GET `/teams/{team_id}`

Full team detail with roster.

**Response: 200**
```json
{
  "id": "uuid",
  "name": "Lincoln Varsity Boys",
  "sport": "basketball",
  "level": "high_school",
  "institution": "Lincoln High School",
  "season": "2026-2027",
  "invite_code": "LVB-2026-X7K9",
  "members": [
    {
      "user_id": "uuid",
      "full_name": "Mike Johnson",
      "role": "coach",
      "team_role": "head_coach",
      "joined_at": "2026-05-01T00:00:00Z"
    },
    {
      "user_id": "uuid",
      "full_name": "James Williams",
      "role": "player",
      "team_role": "player",
      "jersey_number": 23,
      "position": "SG",
      "joined_at": "2026-05-03T00:00:00Z"
    }
  ]
}
```

**Access control:** Must be a member of the team.

### DELETE `/teams/{team_id}`

Soft-delete a team. Head coaches and assistant coaches on that team only.
The row is hidden from normal team/list/detail flows, while immutable audit
context remains available to admin export paths that explicitly include
deleted records.

**Response: 204** — no body. Repeating the request is a no-op.

### POST `/teams/{team_id}/invite`

Generate or refresh team invite. Coaches only.

**Request:**
```json
{
  "role": "player",
  "max_uses": 20,
  "expires_in_days": 30
}
```

**Response: 201**
```json
{
  "invite_code": "LVB-2026-X7K9",
  "invite_url": "https://app.nextballup.com/join/LVB-2026-X7K9",
  "expires_at": "2026-06-01T00:00:00Z",
  "remaining_uses": 20
}
```

### POST `/teams/join`

Join a team via invite code. Any authenticated user.

**Request:**
```json
{
  "invite_code": "LVB-2026-X7K9",
  "jersey_number": 23
}
```

**Validation:**
- `jersey_number` required if user role is `player`, optional if `coach`
- `jersey_number` must be unique within the team
- Invite must not be expired or at max uses

**Response: 200** — Team object with updated membership

### DELETE `/teams/{team_id}/members/{user_id}`

Remove a member. Only head_coach or assistant_coach can remove members. Cannot remove self if sole head_coach.

**Response: 204** No content

### PATCH `/teams/{team_id}/members/{user_id}`

Update member details (jersey number, team_role).

**Request:**
```json
{
  "jersey_number": 10,
  "team_role": "captain"
}
```

**`team_role` enum:** `"head_coach" | "assistant_coach" | "manager" | "player" | "captain"`

**Response: 200** — Updated member object

---

## Games — `/games`

### POST `/games`

Create a game record. Coaches only.

**Request:**
```json
{
  "team_id": "uuid",
  "opponent_name": "Jefferson Eagles",
  "game_type": "regular_season",
  "date": "2026-11-15",
  "time": "19:00",
  "location": "Lincoln High Gym",
  "is_home": true,
  "periods": 4,
  "period_length_minutes": 8,
  "notes": "District opener"
}
```

**Validation:**
- `game_type` enum: `"scrimmage" | "preseason" | "regular_season" | "tournament" | "playoff" | "practice" | "film_exchange"`
- `periods` default 4, range 1-10
- User must be coach on the specified team

**Response: 201**
```json
{
  "id": "uuid",
  "team_id": "uuid",
  "opponent_name": "Jefferson Eagles",
  "game_type": "regular_season",
  "date": "2026-11-15",
  "time": "19:00",
  "location": "Lincoln High Gym",
  "is_home": true,
  "status": "scheduled",
  "created_at": "2026-05-01T00:00:00Z"
}
```

### GET `/games`

List games for current user's teams.

**Query params:** `?team_id=uuid&status=completed&game_type=regular_season&from=2026-11-01&to=2026-12-31&page=1&per_page=20`

**`status` enum:** `"scheduled" | "uploading" | "processing" | "completed" | "failed"`

**Response: 200** — Paginated list of game objects

### GET `/games/{game_id}`

Full game detail including score, lineup, and processing status.

**Response: 200**
```json
{
  "id": "uuid",
  "team_id": "uuid",
  "opponent_name": "Jefferson Eagles",
  "game_type": "regular_season",
  "date": "2026-11-15",
  "status": "completed",
  "score": { "team": 67, "opponent": 54 },
  "lineup": [
    { "user_id": "uuid", "full_name": "James Williams", "jersey_number": 23, "position": "SG", "starter": true, "minutes": 28.5 }
  ],
  "videos": [
    { "id": "uuid", "status": "processed", "duration_seconds": 5400, "uploaded_at": "2026-11-15T22:00:00Z" }
  ],
  "processing": {
    "status": "completed",
    "events_detected": 342,
    "possessions_segmented": 128,
    "tracking_quality": 0.87
  }
}
```

### PATCH `/games/{game_id}`

Update game details (score, status, notes, periods).

**Terminal-status rule:** if the game is already in `completed` or
`failed`, non-admin callers can still PATCH fields *without* changing
`status` (or by passing the same `status` value as the current one). Any
attempt to transition a terminal game to a non-terminal status from a
non-admin caller is rejected with `403 GAME_TERMINAL_STATUS`. Admins can
reopen a terminal game by PATCHing a non-terminal status.

### GET `/games/{game_id}/videos`

List videos attached to a game. Any active team member may read; this
endpoint deliberately does **not** issue signed playback URLs, so it is
safe for every tenant role. Use `GET /videos/{video_id}` for playback
delivery.

**Response: 200**
```json
{
  "videos": [
    {
      "id": "uuid",
      "filename": "lincoln_vs_jefferson_full.mp4",
      "status": "processed",
      "file_size_bytes": 4294967296,
      "duration_seconds": 5400,
      "camera_position": "sideline",
      "camera_height": "elevated",
      "created_at": "2026-11-15T22:00:00Z"
    }
  ],
  "total": 1
}
```

### POST `/games/{game_id}/lineup`

Set the game lineup from team roster.

**Request:**
```json
{
  "entries": [
    { "user_id": "uuid", "jersey_number": 23, "position": "SG", "starter": true },
    { "user_id": "uuid", "jersey_number": 10, "position": "PG", "starter": true }
  ]
}
```

---

## Videos — `/videos`

### POST `/videos/upload`

Initiate a video upload. Returns a presigned URL for direct-to-storage upload.

**Request:**
```json
{
  "game_id": "uuid",
  "filename": "lincoln_vs_jefferson_full.mp4",
  "file_size_bytes": 4294967296,
  "content_type": "video/mp4",
  "camera_position": "sideline",
  "camera_height": "elevated"
}
```

**Validation:**
- `content_type` must be `video/mp4`, `video/quicktime`, or `video/x-matroska`
- `file_size_bytes` max 10737418240 (10GB)
- `camera_position` enum: `"sideline" | "baseline" | "elevated_corner" | "broadcast" | "other"`
- Files over 1GB use multipart upload (see below)

**Response: 201** (for files under 1GB — single presigned PUT)
```json
{
  "id": "uuid",
  "upload_url": "https://storage.nextballup.com/uploads/...",
  "upload_method": "PUT",
  "upload_headers": { "Content-Type": "video/mp4" },
  "expires_at": "2026-11-15T23:00:00Z"
}
```

**Response: 201** (for files over 1GB — multipart upload)
```json
{
  "id": "uuid",
  "upload_method": "MULTIPART",
  "upload_id": "multipart-upload-id",
  "part_size_bytes": 104857600,
  "part_urls": [
    { "part_number": 1, "url": "https://storage.nextballup.com/uploads/...?partNumber=1&uploadId=..." },
    { "part_number": 2, "url": "https://storage.nextballup.com/uploads/...?partNumber=2&uploadId=..." }
  ],
  "expires_at": "2026-11-15T23:00:00Z"
}
```

Client uploads each 100MB part in parallel via presigned PUT, then calls complete with ETags.

### POST `/videos/{video_id}/complete`

Signal upload complete. The server HEADs the uploaded object and
validates the declared size *before* flipping the video to `queued`; a
missing object or size mismatch is rejected with `409 INVALID_VIDEO_STATE`
and never creates a processing job.

**Request (single PUT upload):**
```json
{ "checksum_sha256": "abc123..." }
```

**Request (multipart upload):**
```json
{
  "checksum_sha256": "abc123...",
  "parts": [
    { "part_number": 1, "etag": "\"etag-1\"" },
    { "part_number": 2, "etag": "\"etag-2\"" }
  ]
}
```

**Idempotency:** calling `/complete` again after a successful completion
returns the existing transcode job id rather than creating a duplicate.
A duplicate call against a pending upload that is still in
`PENDING_UPLOAD` proceeds normally.

**Response: 200**
```json
{
  "id": "uuid",
  "status": "queued",
  "estimated_processing_minutes": 45,
  "job_id": "uuid"
}
```

**Errors:** 404 video · 409 video in wrong state / object missing / size
mismatch · 422 missing `parts` for a multipart upload · 503 storage
unavailable

### GET `/videos/{video_id}`

Video detail with playback URLs.

**Response: 200**
```json
{
  "id": "uuid",
  "game_id": "uuid",
  "status": "processed",
  "duration_seconds": 5400,
  "resolution": "1920x1080",
  "fps": 30,
  "playback_url": "https://cdn.nextballup.com/hls/uuid/manifest.m3u8",
  "playback_token": "signed-token",
  "token_expires_at": "2026-11-16T00:00:00Z",
  "thumbnail_url": "https://cdn.nextballup.com/thumbs/uuid.jpg",
  "processing": {
    "transcode": "completed",
    "detection": "completed",
    "tracking": "completed",
    "court_mapping": "completed",
    "events": "completed",
    "metrics": "completed"
  }
}
```

The `playback_url` presigned URL lifetime is capped to the shorter of
`PLAYBACK_URL_EXPIRES_SECONDS` and `PLAYBACK_TOKEN_EXPIRE_SECONDS`, so a
presigned URL can never outlive its matching token.

### POST `/videos/{video_id}/playback/verify`

Live revocation check. The client holds a `playback_token` from
`/videos/{video_id}` and periodically asks the server to re-validate it.
Returns `200` + expiry when the token is still good; any `4xx` means
drop the stream.

**Request:**
```json
{ "token": "eyJ..." }
```

**Response: 200**
```json
{ "video_id": "uuid", "expires_at": "2026-11-16T00:00:00Z" }
```

Rejects with `401` if the token is expired/malformed, belongs to a
different user, scopes a different video, or if `session_version` has
advanced (logout, account delete, team removal).

### POST `/videos/{video_id}/processing/requeue`

Admin-only. Resets a terminal (`FAILED` or `COMPLETED`) processing job
back to `PENDING` so the beat dispatcher re-runs it. Rejects `RUNNING`
and `PENDING` jobs with `409` so operators can't race a live worker.

**Request:**
```json
{ "stage": "transcode" }
```

**Response: 200**
```json
{
  "job_id": "uuid",
  "stage": "transcode",
  "status": "pending",
  "requeued_at": "2026-11-16T00:00:00Z"
}
```

**Errors:** 403 non-admin caller · 404 video or stage not found · 409
job is not in a terminal state · 422 unknown stage

### GET `/videos/{video_id}/status`

Lightweight polling endpoint for processing status.

**Response: 200**
```json
{
  "status": "processing",
  "stage": "tracking",
  "progress_percent": 45,
  "stages": {
    "transcode": { "status": "completed", "completed_at": "..." },
    "detection": { "status": "completed", "completed_at": "..." },
    "tracking": { "status": "running", "progress_percent": 45 },
    "court_mapping": { "status": "pending" },
    "events": { "status": "pending" },
    "metrics": { "status": "pending" }
  }
}
```

### WebSocket `/ws/videos/{video_id}/status`

Real-time processing updates pushed to client.

**Messages (server → client):**
```json
{ "type": "stage_update", "stage": "tracking", "status": "completed" }
{ "type": "progress", "stage": "events", "percent": 72 }
{ "type": "complete", "events_detected": 342 }
{ "type": "error", "stage": "court_mapping", "message": "Court lines not detected — try a wider angle" }
```

---

## Events — `/events`

### GET `/events`

List auto-detected events with filtering.

**Query params:** `?game_id=uuid&event_type=shot_attempt&player_id=uuid&period=2&possession_outcome=score&page=1&per_page=50`

**`event_type` enum:** `"shot_attempt" | "shot_make" | "shot_miss" | "three_point_attempt" | "free_throw" | "rebound_offensive" | "rebound_defensive" | "assist" | "potential_assist" | "hockey_assist" | "turnover" | "steal" | "block" | "deflection" | "foul" | "charge_drawn" | "pass" | "dribble_drive" | "screen_set" | "cut" | "closeout" | "help_rotation" | "fast_break" | "transition" | "box_out" | "loose_ball_recovery"`

**Response: 200**
```json
{
  "events": [
    {
      "id": "uuid",
      "game_id": "uuid",
      "event_type": "shot_attempt",
      "timestamp_seconds": 342.5,
      "period": 2,
      "game_clock": "5:18",
      "court_x": 23.5,
      "court_y": 12.0,
      "actors": [
        { "player_id": "uuid", "role": "shooter", "jersey_number": 23 },
        { "player_id": "uuid", "role": "closest_defender", "jersey_number": 5 }
      ],
      "outcome": "make",
      "points": 2,
      "shot_quality": 0.62,
      "defender_distance_ft": 4.2,
      "confidence": 0.91,
      "clip_url": "https://cdn.nextballup.com/clips/uuid.mp4",
      "possession_id": "uuid",
      "tactical_tags": ["pick_and_roll", "ball_handler_pull_up"]
    }
  ],
  "total": 342,
  "page": 1,
  "per_page": 50
}
```

### PATCH `/events/{event_id}`

Coach correction of auto-detected event (human-in-the-loop).

**Request:**
```json
{
  "event_type": "shot_make",
  "actors": [
    { "player_id": "uuid", "role": "shooter" }
  ],
  "corrected": true,
  "correction_note": "Was labeled as miss but went in off glass"
}
```

---

## Player Profiles — `/players`

### GET `/players/{player_id}/profile`

Full player profile with aggregated stats and tendency data.

**Query params:** `?team_id=uuid&season=2026-2027&game_ids=uuid,uuid&date_from=2026-11-01&date_to=2026-12-31`

**Response: 200**
```json
{
  "player_id": "uuid",
  "full_name": "James Williams",
  "jersey_number": 23,
  "position": "SG",
  "games_analyzed": 12,
  "possessions_analyzed": 847,
  "box_score_averages": {
    "points": 18.3,
    "rebounds": 4.2,
    "assists": 3.1,
    "steals": 1.4,
    "blocks": 0.3,
    "turnovers": 2.1,
    "minutes": 28.5
  },
  "shooting": {
    "fg_pct": 0.482,
    "three_pct": 0.371,
    "ft_pct": 0.845,
    "efg_pct": 0.544,
    "ts_pct": 0.582,
    "shot_zones": [
      { "zone": "paint", "attempts": 84, "makes": 52, "pct": 0.619, "avg_quality": 0.71 },
      { "zone": "midrange", "attempts": 31, "makes": 12, "pct": 0.387, "avg_quality": 0.38 },
      { "zone": "three_left_corner", "attempts": 18, "makes": 8, "pct": 0.444, "avg_quality": 0.42 },
      { "zone": "three_right_wing", "attempts": 24, "makes": 9, "pct": 0.375, "avg_quality": 0.39 },
      { "zone": "three_top_key", "attempts": 15, "makes": 4, "pct": 0.267, "avg_quality": 0.33 }
    ]
  },
  "tendency_card": {
    "drive_direction": { "left": 0.62, "right": 0.31, "straight": 0.07 },
    "shot_selection_under_pressure": { "pull_up": 0.45, "step_back": 0.28, "floater": 0.15, "pass_out": 0.12 },
    "catch_and_shoot_readiness_ms": 480,
    "off_ball_movement_frequency": 3.2,
    "screen_usage_rate": 0.34,
    "transition_involvement_rate": 0.28
  }
}
```

### GET `/players/{player_id}/tendencies`

Detailed tendency breakdowns — the "hidden impact" data.

**Query params:** Same filtering as profile.

**Response: 200**
```json
{
  "player_id": "uuid",
  "sample_size": { "games": 12, "possessions": 847 },
  "offensive_tendencies": {
    "primary_actions": [
      { "action": "pick_and_roll_ball_handler", "frequency": 0.24, "ppp": 0.92, "turnover_rate": 0.11 },
      { "action": "spot_up", "frequency": 0.21, "ppp": 1.14, "turnover_rate": 0.03 },
      { "action": "isolation", "frequency": 0.15, "ppp": 0.78, "turnover_rate": 0.18 },
      { "action": "cut", "frequency": 0.12, "ppp": 1.31, "turnover_rate": 0.02 },
      { "action": "off_screen", "frequency": 0.09, "ppp": 0.95, "turnover_rate": 0.05 }
    ],
    "ball_handling": {
      "avg_dribbles_per_touch": 2.8,
      "avg_seconds_per_touch": 3.1,
      "passes_per_possession": 1.4
    }
  },
  "defensive_tendencies": {
    "avg_closeout_speed_mph": 8.7,
    "avg_contest_distance_ft": 3.1,
    "help_rotation_rate": 0.42,
    "recovery_time_after_help_ms": 1200,
    "on_ball_fg_pct_allowed": 0.39,
    "floor_shrink_score": 72
  }
}
```

---

## Metrics — `/metrics`

### GET `/metrics/spatial-iq`

Spatial IQ composite and sub-components.

**Query params:** `?team_id=uuid&game_id=uuid&player_id=uuid`

**Response: 200**
```json
{
  "team_id": "uuid",
  "game_id": "uuid",
  "players": [
    {
      "player_id": "uuid",
      "jersey_number": 23,
      "spatial_iq_composite": 78,
      "components": {
        "spacing_quality": 82,
        "advantage_creation": 71,
        "decision_latency_ms": 620,
        "off_ball_value": 75,
        "floor_stretching": 80
      },
      "details": {
        "avg_nearest_teammate_ft": 14.2,
        "avg_defender_displacement_ft": 3.8,
        "time_to_decision_after_advantage_ms": 620,
        "screens_set_per_possession": 0.4,
        "cuts_per_possession": 0.3,
        "relocations_per_possession": 0.8
      }
    }
  ]
}
```

### GET `/metrics/conversion-rates`

Conversion rate metrics — outcome probabilities conditional on action state.

**Query params:** `?team_id=uuid&game_id=uuid&player_id=uuid&action_type=pick_and_roll`

**Response: 200**
```json
{
  "conversion_rates": [
    {
      "action_chain": "pick_and_roll → drive → kick_out",
      "occurrences": 34,
      "outcomes": {
        "three_point_make": 0.24,
        "three_point_miss": 0.32,
        "midrange_make": 0.09,
        "midrange_miss": 0.06,
        "turnover": 0.09,
        "foul_drawn": 0.12,
        "secondary_pass": 0.08
      },
      "expected_points_per_play": 1.08,
      "league_benchmark_eppp": 0.95
    }
  ]
}
```

### GET `/metrics/predictive`

Predictive features — shot quality, pass risk, next-action likelihood.

**Query params:** `?game_id=uuid&event_id=uuid`

**Response: 200**
```json
{
  "event_id": "uuid",
  "shot_quality": {
    "expected_fg_pct": 0.62,
    "difficulty_factors": {
      "defender_distance_ft": 4.2,
      "shot_clock_remaining": 8,
      "dribbles_before_shot": 2,
      "touch_time_seconds": 1.8,
      "contested": false
    }
  },
  "pass_risk": {
    "turnover_probability": 0.08,
    "pass_distance_ft": 18.5,
    "defenders_in_lane": 1,
    "receiver_openness": 0.72
  }
}
```

---

## Clips — `/clips`

### POST `/clips`

Generate a clip from a video by time range or event.

**Request:**
```json
{
  "video_id": "uuid",
  "start_seconds": 340.0,
  "end_seconds": 348.0,
  "title": "Williams pull-up J",
  "tags": ["shooting", "midrange"],
  "event_ids": ["uuid"]
}
```

**Response: 201**
```json
{
  "id": "uuid",
  "status": "generating",
  "estimated_seconds": 10
}
```

### GET `/clips`

List clips with filtering.

**Query params:** `?game_id=uuid&player_id=uuid&tags=shooting,defense&page=1&per_page=20`

### POST `/clips/playlists`

Create a playlist of clips.

**Request:**
```json
{
  "title": "Williams — Shot Selection vs Zone",
  "description": "All shot attempts against zone defense",
  "clip_ids": ["uuid", "uuid", "uuid"],
  "team_id": "uuid",
  "visibility": "team"
}
```

**`visibility` enum:** `"private" | "team" | "shared_link"`

---

## Scouting — `/scouting`

### POST `/scouting/reports`

Generate a scouting report (PDF or structured data).

**Request:**
```json
{
  "team_id": "uuid",
  "report_type": "player_profile",
  "player_id": "uuid",
  "game_ids": ["uuid", "uuid"],
  "format": "pdf",
  "sections": ["overview", "shooting", "tendencies", "defense", "clips"]
}
```

**`report_type` enum:** `"player_profile" | "opponent_breakdown" | "game_summary" | "lineup_analysis" | "recruiting_brief"`

**Response: 202**
```json
{
  "report_id": "uuid",
  "status": "generating",
  "estimated_seconds": 30
}
```

### GET `/scouting/reports/{report_id}`

**Response: 200**
```json
{
  "id": "uuid",
  "status": "completed",
  "download_url": "https://cdn.nextballup.com/reports/uuid.pdf",
  "expires_at": "2026-11-16T12:00:00Z",
  "data": { ... }
}
```

---

## Search — `/search`

### GET `/search`

Cross-entity search across games, events, players, clips.

**Query params:** `?q=pick and roll left wing&team_id=uuid&entity_types=events,clips&page=1&per_page=20`

**Response: 200**
```json
{
  "results": [
    {
      "entity_type": "event",
      "id": "uuid",
      "relevance_score": 0.92,
      "summary": "P&R left wing — Williams drives, kick to corner 3 (make)",
      "game": { "id": "uuid", "date": "2026-11-15", "opponent": "Jefferson Eagles" },
      "timestamp_seconds": 342.5,
      "clip_url": "https://..."
    }
  ],
  "total": 47
}
```

---

## Alerts — `/alerts`

### POST `/alerts`

Create a coach-configured alert.

**Request:**
```json
{
  "team_id": "uuid",
  "name": "Late switch on Spain P&R",
  "description": "Alert when opponent switches late on Spain pick & roll",
  "conditions": {
    "event_type": "pick_and_roll",
    "sub_type": "spain",
    "metric": "switch_timing_ms",
    "operator": "gt",
    "threshold": 1500
  },
  "delivery": ["in_app", "email"],
  "active": true
}
```

### GET `/alerts`

List alerts for a team.

### GET `/alerts/triggered`

List triggered alert instances with linked events/clips.

---

## Notes — `/notes`

Team-scoped collaboration on events and clips. These are coaching annotations, not social comments. Visible only to team members. Called "notes" in the UI, never "comments."

### POST `/notes`

Attach a note to an event, clip, or game.

**Request:**
```json
{
  "target_type": "event",
  "target_id": "uuid",
  "body": "Watch the help rotation here — too slow by 0.5s. @james_williams needs to anticipate the drive earlier.",
  "mentions": ["uuid"],
  "timestamp_seconds": 342.5
}
```

**Validation:**
- `target_type` enum: `"event" | "clip" | "game" | "possession"`
- `body` max 1000 characters
- `mentions` optional array of user_ids (must be members of the same team)
- `timestamp_seconds` optional — anchors the note to a specific video timestamp (for game-level notes)
- User must be a member of the team that owns the target
- **Safeguarding**: if any mentioned user is a minor (date_of_birth indicates under 18), the note is flagged for the head_coach to review before the minor sees it. Coaches' notes are delivered immediately; player-to-player notes involving minors are held for coach approval.

**Response: 201**
```json
{
  "id": "uuid",
  "target_type": "event",
  "target_id": "uuid",
  "author": {
    "id": "uuid",
    "full_name": "Mike Johnson",
    "role": "coach",
    "avatar_url": "https://..."
  },
  "body": "Watch the help rotation here — too slow by 0.5s. @james_williams needs to anticipate the drive earlier.",
  "mentions": [
    { "user_id": "uuid", "full_name": "James Williams" }
  ],
  "timestamp_seconds": 342.5,
  "created_at": "2026-11-16T08:30:00Z",
  "pending_review": false
}
```

### GET `/notes`

List notes for a target.

**Query params:** `?target_type=event&target_id=uuid&game_id=uuid&author_id=uuid&page=1&per_page=50`

**Response: 200**
```json
{
  "notes": [
    {
      "id": "uuid",
      "target_type": "event",
      "target_id": "uuid",
      "author": { "id": "uuid", "full_name": "Mike Johnson", "role": "coach" },
      "body": "Watch the help rotation here...",
      "mentions": [],
      "timestamp_seconds": 342.5,
      "created_at": "2026-11-16T08:30:00Z",
      "is_pinned": false
    }
  ],
  "total": 12
}
```

**Access control:** Only team members can see notes. Players cannot see notes on games they don't appear in.

### PATCH `/notes/{note_id}`

Edit own note. Only the author can edit.

**Request:**
```json
{
  "body": "Updated observation about the help rotation."
}
```

### DELETE `/notes/{note_id}`

Delete own note, or any note if head_coach.

**Response: 204**

### POST `/notes/{note_id}/pin`

Pin a note to the top of its target's note list. Coaches only.

**Response: 200**

---

## Messaging Roadmap (not in MVP)

Messaging is sequenced across three releases:

**MVP (now):** Team-scoped notes on events/clips/games (specified above). Coach-to-team annotations. @mentions for player-specific feedback with minor safeguarding. No direct messaging.

**V1:** Coach-to-team announcements. One-to-many, coach-initiated only. Simpler compliance — coach broadcasts to entire team, no private channels. Appears as a pinned card in the feed.

**V2 (post product-market fit, 50+ paying teams):** Full direct messaging with compliance infrastructure: COPPA-compliant messaging consent for minors, content moderation pipeline, CSAM mandatory reporting to NCMEC, message retention and e-discovery support, end-to-end encryption decision. Budget: $20K-$40K legal review + 6-8 weeks engineering for compliance layer alone. Do not build before hiring trust & safety counsel.

**Never build:** Public likes/counts on player performance (ethical risk with minors), following/followers (social graph turns analytics tool into social network), resharing/reposting mechanics (uncontrolled distribution of minor athlete footage), public comment threads (moderation burden with no analytics value).

---

## Admin — `/admin` (operator-only, implemented)

Cross-tenant surfaces that only `UserRole.admin` may call. Coach- and
player-tier users receive `403 FORBIDDEN` regardless of team membership.
These endpoints bypass normal tenant scoping because their whole purpose is
cross-tenant incident review and compliance evidence.

### GET `/admin/audit/logs`

Paginated view over the append-only audit log. The rows are immutable at the
database level (a trigger refuses any `UPDATE`/`DELETE`), so this endpoint is
strictly read-only.

**Query params**

| Name | Type | Notes |
|---|---|---|
| `team_id` | uuid | Filter by the tenant the row belongs to |
| `actor_user_id` | uuid | Filter by the user who triggered the action |
| `action` | string | Exact match on the dot-namespaced action identifier (e.g. `videos.upload.complete`) |
| `resource_type` | string | e.g. `video`, `team`, `processing_job` |
| `resource_id` | uuid | Filter by the specific resource touched |
| `from_ts` | ISO-8601 | Inclusive lower bound on `created_at` |
| `to_ts` | ISO-8601 | Exclusive upper bound on `created_at` |
| `limit` | int, 1–200 | Page size (default 50) |
| `cursor` | opaque string | Echoed from a prior `next_cursor` response |

**Response**

```json
{
  "items": [
    {
      "id": "…",
      "created_at": "2026-04-16T20:58:55.000Z",
      "action": "videos.upload.complete",
      "actor_user_id": "…",
      "actor_email": "coach@example.com",
      "resource_type": "video",
      "resource_id": "…",
      "team_id": "…",
      "ip_address": "10.0.0.1",
      "user_agent": "…",
      "request_id": "…",
      "extra": {"checksum_sha256": "…"}
    }
  ],
  "next_cursor": "…"
}
```

Rows are returned newest-first (`created_at DESC, id DESC`); echo
`next_cursor` back on the next request to page forward. `next_cursor` is
`null` when there are no more rows.

## Common Patterns

### Pagination

All list endpoints use offset pagination at MVP:

```json
{
  "data": [...],
  "total": 342,
  "page": 1,
  "per_page": 20,
  "has_next": true
}
```

Default `per_page` is 20, max is 100. At scale, high-volume endpoints (events, notes) may switch to cursor-based keyset pagination on `(created_at, id)` — this will be a non-breaking addition (cursor param alongside page param).

### CSRF — double-submit on cookie-authenticated mutations

When the caller is authenticated via cookies, all mutating methods
(`POST`, `PUT`, `PATCH`, `DELETE`) must echo the `nbu_csrf_token` cookie
back in the `X-CSRF-Token` header. A missing or mismatched header is
rejected with `403 CSRF_FAILED` before the router runs.

Bearer-authenticated requests are CSRF-exempt by construction — a
cross-origin browser attacker cannot set `Authorization` headers. The
following paths are also exempt so the auth bootstrap still works:
`/auth/login`, `/auth/register`, `/auth/refresh`,
`/auth/password/forgot`, `/auth/password/reset`, and
`/_csp-report`.

### Error responses

```json
{
  "error": {
    "code": "TEAM_NOT_FOUND",
    "message": "Team with ID uuid not found",
    "details": {}
  }
}
```

Standard HTTP codes: 400 bad request, 401 unauthorized, 403 forbidden (wrong role/team), 404 not found, 409 conflict, 422 validation error, 429 rate limited, 500 internal error.

### Rate limits

- Auth endpoints: 5 requests/minute per IP
- Upload endpoints: 10 requests/hour per user
- Read endpoints: 100 requests/minute per user
- Search: 30 requests/minute per user

Headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`
