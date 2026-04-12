# NextBallUp — Product Requirements Document

## Product Vision

NextBallUp transforms basketball game and practice footage into "hidden impact" intelligence that coaches and recruiters cannot get from box scores. Upload a video, get back auto-tagged events, player tendencies, spatial metrics, and predictive features — packaged into workflows that save coaches 10+ hours per week of film study.

## Target Users

### Coach (primary buyer)

A high school, AAU/club, or college coach who currently films games on a phone or camera, manually reviews film, and keeps stats in a spreadsheet or Hudl. They want to answer questions like "Which off-ball cuts create the highest expected points?" and "Which defenders shrink the floor without getting steals or blocks?" without spending 4 hours per game on manual breakdown.

**Coach can:**
- Register as coach, create teams, invite players and assistant coaches
- Upload game/practice video
- View auto-generated stats, events, and clips
- Explore hidden impact dashboards (Spatial IQ, conversion rates, predictive features)
- Correct auto-detected events (human-in-the-loop)
- Generate scouting reports
- Configure alerts for tactical patterns
- Manage team roster and settings
- Export data (CSV events, clip playlists, reports)

### Player (secondary user, recruited by coach)

A player who joins their coach's team via invite code. They want to see their own performance data, shooting charts, tendency profiles, and highlight clips for recruiting.

**Player can:**
- Register as player, join teams via invite code
- View their own player profile (stats, shooting chart, tendency card)
- View team-level dashboards for teams they belong to
- Watch their own clips and share highlight reels
- Update their profile (position, height, graduation year)
- Cannot upload video, create teams, edit events, or access opponent scouting

### Admin (internal only)

NextBallUp staff who manage the platform.

**Admin can:** Everything a coach can, plus manage users, view system health, manage processing queues.

---

## Feature Specifications

### F1: Registration and Authentication

**F1.1: Role-Based Registration**

User visits `/register` and sees two options: "I'm a Coach" and "I'm a Player." Each leads to a role-specific registration form.

Coach registration collects: email, password, full name, phone (optional), institution (optional).

Player registration collects: email, password, full name, position (optional), height (optional), graduation year (optional), handedness (optional).

Both forms validate email uniqueness, password strength (8+ chars, 1 number, 1 uppercase), and return JWT tokens on success. The user is immediately logged in after registration.

**F1.2: Login and Session Management**

Email + password login. JWT access token (15 min) + refresh token (7 days). Refresh happens automatically when access token expires (transparent to user). Logout invalidates refresh token.

**F1.3: Team Invite Join Flow**

A player or assistant coach receives a link like `app.nextballup.com/join/LVB-2026-X7K9`. If not logged in, they register first then the invite is applied. If already logged in, clicking the link adds them to the team. Players must provide a jersey number when joining. Invite codes have max uses and expiration dates.

### F2: Team Management

**F2.1: Create Team**

Coach fills out: team name, sport (basketball), level (youth through professional), institution name, institution type, season, city, state, conference. On creation, an invite code is auto-generated and the coach becomes head_coach.

**F2.2: Roster Management**

Coach sees a roster table: player name, jersey number, position, role (player/captain/manager), joined date. Coach can change jersey numbers, set positions, assign captain role, and remove players. Removing a player does not delete their account — it removes them from the team.

**F2.3: Team Settings**

Coach can update team name, season, conference. Coach can regenerate invite codes (old ones deactivate). Coach can add assistant coaches.

**F2.4: Multi-Team Support**

Both coaches and players can belong to multiple teams (e.g., school team + AAU). A team switcher in the sidebar sets the active team context. All data views filter by the active team.

### F3: Game Management

**F3.1: Create Game**

Coach creates a game with: opponent name, game type (scrimmage/preseason/regular/tournament/playoff/practice), date, time, location, home/away, periods, period length, notes. Games start in "scheduled" status.

**F3.2: Set Lineup**

Before or after upload, coach sets the game lineup from the team roster. Each lineup entry has: player, jersey number, position, starter (boolean). Minutes played is computed after processing.

**F3.3: Game Detail View**

Shows: score, opponent, date, location, lineup, videos with processing status, event count, and links to film room, stats, and metrics views.

### F4: Video Upload and Processing

**F4.1: Upload Flow**

Coach opens a game → clicks "Upload Video" → sees a drag-drop zone. Selecting a file triggers: (1) POST to `/videos/upload` to get a presigned URL, (2) direct PUT to storage with progress bar, (3) POST to `/videos/{id}/complete` to trigger processing. Max 10GB. Accepted formats: MP4, MOV, MKV.

Coach can upload multiple videos per game (e.g., different camera angles). Each processes independently.

**F4.2: Processing Pipeline Visualization**

After upload completes, the game page shows a pipeline status widget:

```
[Transcode] → [Detect] → [Track] → [Court Map] → [Events] → [Metrics]
    ✓           ✓         ◉ 45%       ○            ○           ○
```

Each stage shows: pending (○), running with percent (◉), complete (✓), or failed (✗). Updates come via WebSocket. On failure, show a human-readable error ("Court lines not detected — try a wider camera angle") and a "Retry" button.

**F4.3: Processing Stages (Backend)**

1. **Transcode**: Convert to H.264 mezzanine (1080p, 30fps). Generate HLS segments. Extract thumbnail.
2. **Detect**: Run RF-DETR on all frames. Output bounding boxes for players, ball, hoop, referees.
3. **Track**: Run BoT-SORT (standalone MIT repo). Assign persistent track IDs to players. Run SmolVLM2 for jersey number OCR. Cluster team colors.
4. **Court Map**: Detect court keypoints (corners, lane markings, center circle, hoops). Compute homography matrix per segment. Map all detections to court coordinates.
5. **Events**: Detect possession boundaries. Classify events (shots, passes, turnovers, rebounds, screens, cuts). Assign tactical tags (P&R, DHO, zone, etc.). Link events to actors.
6. **Metrics**: Compute box score stats, shooting zones, spatial IQ components, conversion rates, player tendencies, and predictive features (shot quality, pass risk).

### F5: Film Room

The core product experience. Video player synced with an event timeline.

**F5.1: Video Player**

HLS playback with signed URLs. Standard controls (play/pause, seek, speed 0.5x-2x, fullscreen). Frame-by-frame stepping with arrow keys. Keyboard: J (back 5s), K (pause/play), L (forward 5s).

**F5.2: Event Timeline**

A scrollable strip alongside the video (right side on desktop, below on mobile). Each event is a card showing: event type icon, timestamp, actors (jersey numbers), outcome, tactical tags. Clicking an event seeks the video to 3 seconds before the event. Events are color-coded: green for scores, red for turnovers, blue for defensive events, gray for neutral.

**F5.3: Event Filtering**

Filter the timeline by: event type, player, period, outcome, tactical tag. Filters persist during playback. "Show only my events" shortcut for players.

**F5.4: Event Correction**

Coach clicks an event → edit modal appears → can change event type, outcome, actors, tactical tags. Can add a correction note. Corrected events are flagged and used as training signal for model improvement.

**F5.5: Clip Creation**

Coach drags to select a time range on the video seekbar → "Create Clip" button → enters title and tags → clip is generated in the background. Alternatively, right-click an event → "Create Clip" generates a clip from 3s before to 3s after the event.

### F6: Player Profiles

**F6.1: Profile Overview**

Shows: photo/avatar, name, jersey, position, height, graduation year, box score averages across games, shooting percentages, minutes per game. For coaches: visible for all players on their teams. For players: visible for their own profile and teammates.

**F6.2: Shot Chart**

Half-court D3 visualization. Each shot is a dot positioned at court coordinates. Color: green for makes, red for misses. Size: proportional to shot quality (bigger = higher quality attempt). Overlaid zone FG% labels. Toggle: all games, single game, date range. Toggle: 2-point only, 3-point only, all.

**F6.3: Tendency Card**

A structured card showing:
- Drive direction distribution (left / right / straight pie chart)
- Shot selection under pressure (pull-up / step-back / floater / pass-out)
- Catch-and-shoot readiness (average ms from catch to release)
- Off-ball movement frequency (movements per possession)
- Primary play-type breakdown (P&R ball handler, spot-up, iso, cut, off-screen — with PPP for each)
- Defensive tendencies (closeout speed, contest rate, help rotation frequency)

### F7: Hidden Impact Dashboards

The premium differentiator. These answer questions traditional stats cannot.

**F7.1: Spatial IQ Dashboard**

A composite score (0-100) with sub-components:
- **Spacing Quality** (0-100): Average nearest-teammate distance, floor balance score, paint packing avoidance.
- **Advantage Creation** (0-100): Average defender displacement caused by the player's movement (in feet), screens that create open shots, drives that collapse defense.
- **Decision Latency** (milliseconds): How quickly the player makes the right read after an advantage is created. Lower = better. Shows distribution histogram.
- **Off-Ball Value** (0-100): Value of screens set, cuts made, relocations after ball reversal. Quantifies "gravity" — does the player's movement create scoring chances for teammates?

Each sub-component includes a team rank and league-level benchmark (when available).

**F7.2: Conversion Rate Explorer**

Coaches select an action chain (e.g., "Pick & Roll → Drive → Kick Out") and see: number of occurrences, outcome distribution (3PM, 3PA miss, midrange make/miss, turnover, foul drawn, secondary pass), expected points per play, and comparison to team average.

Filterable by: player (ball handler), date range, opponent. Shows trend over time (line chart).

**F7.3: Predictive Features**

Per-shot: Expected FG% based on defender distance, shot clock, dribbles before shot, touch time, whether contested. Show the shot quality vs actual result (lucky makes, unlucky misses).

Per-pass: Turnover probability based on pass distance, defenders in lane, receiver openness.

Team-level: show aggregate lucky/unlucky shooting regression signals.

### F8: Clips and Playlists

**F8.1: Clip Library**

All clips across games for the active team. Filter by: player, event type, tags, game, date range. Grid view with thumbnails. Click to play.

**F8.2: Playlists**

Coach creates playlists by dragging clips. Playlists have: title, description, visibility (private, team, shared link). Shared link playlists are accessible without login (for recruiting).

### F9: Scouting Reports

**F9.1: Report Types**

- **Player Profile Report**: One player, all their data in a PDF. Sections: overview, shooting, tendencies, defense, top clips.
- **Opponent Breakdown**: Aggregate tendencies for a scouted team across multiple games.
- **Game Summary**: One game, both teams, key events, score flow.
- **Recruiting Brief**: Compact one-page player summary designed for college coaches.

**F9.2: Generation Flow**

Coach selects report type → configures parameters (player, games, sections) → clicks Generate → async job creates PDF → download link appears. Reports include embedded QR codes linking to clip playlists.

### F10: Alerts

**F10.1: Alert Configuration**

Coach defines alerts based on tactical patterns detected by the CV pipeline. Examples:
- "Opponent switches late on Spain P&R" (switch_timing_ms > 1500)
- "Player rejects side P&R more than 60% of the time"
- "Defensive low-man rotation timing drifts above 1.2s"

Alerts trigger when a matching event is detected during processing.

**F10.2: Alert Delivery**

In-app notification bell + optional email digest. Triggered alerts link to the specific event/clip.

### F11: Search

Cross-entity search powered by PostgreSQL full-text search (pg_trgm). Query matches against: event descriptions, tactical tags, player names, game opponents, clip titles. Results link to the source entity.

### F14: Team Notes (collaboration)

Team-scoped annotations on events, clips, games, and possessions. These are coaching tools, not social features.

**F14.1: Note Creation**

Coach or player taps the note icon on any event card, clip, game page, or possession in the timeline. A text input appears inline (not a modal — feels lightweight). Notes support @mentions by typing `@` and selecting a team member from autocomplete. Notes can be anchored to a specific video timestamp (clicking "note" while watching video anchors to the current timestamp).

**F14.2: Minor Safeguarding**

When a player-authored note @mentions a minor (under 18), the note is held in `pending_review` state and not visible to the minor until a coach approves it. Coach-authored notes are delivered immediately regardless of whether they mention minors — coaches are trusted adults. This prevents the notes feature from becoming an unmoderated messaging channel between minors.

**F14.3: Note Display**

Notes appear as a collapsible section below events and clips. Pinned notes (coach-pinnable) appear first. Notes show author avatar, name, role badge (coach/player), timestamp, and body text. @mentions are highlighted in brand blue and link to the mentioned player's profile.

**F14.4: Film Room Notes**

In the film room, notes anchored to timestamps appear as markers on the video seekbar (distinct from event markers). Clicking a note marker seeks the video and highlights the note in the sidebar. Coaches can leave time-anchored notes while watching film — the primary coaching workflow.

**F14.5: What this is NOT**

Notes are not DMs, not a chat system, not threaded conversations. There are no replies to notes — if a coach needs to elaborate, they create a new note. There is no "seen" indicator, no typing indicator, no real-time presence. Notes are asynchronous annotations, not synchronous communication.

### What we never build

These features conflict with NextBallUp's identity as an analytics tool for coaches and create liability with minor athletes:

- **Public likes/counts** on player performance data — creates social pressure around minors' athletics
- **Following/followers** — builds a social graph that competes with Instagram rather than Hudl
- **Resharing/reposting** — uncontrolled viral distribution of minor athlete footage
- **Public comment threads** — moderation burden with zero analytics value
- **Reactions/emoji responses** — trivializes coaching feedback

---

## Basketball Domain Conventions (from expert audit)

These conventions govern how events, possessions, and metrics behave. They are not optional — coaches will judge the product's credibility by whether these match established basketball analysis standards.

**Score context on every possession.** Every possession record must carry `score_differential` (team minus opponent at possession start) and `game_context` (normal / clutch / garbage_time). Clutch is defined as final 5 minutes of regulation or overtime with score differential ≤5. Garbage time is final 3 minutes with differential >15. All dashboards must offer context filtering: "show tendencies when leading," "when trailing," "in close games."

**Film exchange workflow.** College programs upload opponent film for scouting. Add `game_type: "film_exchange"` where opponent players are tracked by jersey number only (no user_id linkage). Events tagged with `offense_team: "opponent"` go through the same tendency/metrics pipeline, building an opponent profile. This doubles the value of every uploaded game.

**Opponent modeling from your own film.** Even in regular games, the opponent is visible. During event detection, tag every event with `offense_team: "team" | "opponent"` and mirror the tendency/metrics pipeline for the opponent side. A single uploaded game should produce insights on both teams.

**Situation tagging.** Many coaching decisions depend on situation type. Tag possessions with `situation_type`: halfcourt, transition, after_timeout (ATO), baseline_out_of_bounds (BLOB), sideline_out_of_bounds (SLOB), press_break, free_throw_alignment, end_of_period. ATO sets are the #1 scouting target because they reveal a team's most rehearsed plays. Detectable from game clock + dead ball event patterns.

**Fatigue-aware tendency splits.** Track cumulative minutes played within each game on player records. Expose at minimum: "first half tendencies" vs "fourth quarter tendencies." Shot selection, defensive effort, and decision speed all degrade with fatigue — showing this is a differentiator no current competitor offers at the sub-NBA level.

**Possession edge cases (hardcoded conventions):**
- Offensive rebound = continuation of existing possession (shot clock resets, possession does not)
- And-one free throw = part of same possession
- Technical free throws = excluded from possessions entirely, do not count in PPP
- End-of-period heaves = tagged `end_of_period`, excluded from shooting statistics by default
- Team color seed: require coach to label one frame with "home" and "away" team during upload to seed the clustering algorithm, eliminating the most common team identification failure

---

## Non-Functional Requirements

### Performance
- Video upload: support resumable uploads for files > 1GB
- Processing: target < 2 hours for a full 2-hour game on a single GPU
- API response times: p95 < 200ms for read endpoints, < 500ms for write
- Film room: video start time < 2s, event timeline render < 500ms

### Reliability
- Processing pipeline: each stage is idempotent and retryable
- Failed stages can be retried individually without re-running the entire pipeline
- Video originals are never deleted automatically — only archived

### Security
- All video URLs are signed with 1-hour expiration
- No cross-team data access (enforced at query level, not just API level)
- Rate limiting on auth and upload endpoints
- RBAC: coach-only operations are enforced in API middleware

### Scalability
- Database: partition player_tracks and ball_tracks by video_id for query performance
- Storage: lifecycle policies move raw video to cold storage after 30 days
- Compute: Celery workers can scale horizontally per stage

### Privacy
- Biometric consent flag must be true before storing pose keypoints
- FERPA: if institution_type is k12_school, restrict data export and disable shared links
- COPPA: block registration for users under 13 without parental consent workflow
- Data deletion: when a player leaves a team, their profile data stays but tracking data is anonymized (player_id set to null on tracks)

---

## MVP Scope (Version 0.1)

The MVP includes F1 through F5 plus basic F6 (profile overview + shot chart). Specifically:

**In MVP:**
- Registration (coach/player) and auth
- Team creation, invite codes, join flow, roster management
- Game creation and lineup management
- Video upload with presigned URLs
- Processing pipeline: transcode, detect, track, court map, events, basic metrics
- Film room: video player + event timeline with filtering
- Basic player profile: box score averages, shot chart
- Event correction (human-in-the-loop)
- Basic clip creation from events

**Post-MVP (V1):**
- Tendency cards (F6.3)
- Spatial IQ dashboard — **ship as component charts first** (spacing plot, advantage creation events, decision latency distribution). Introduce composite score (0-100) only after validating formula against expert coach rankings on 20+ games. Premature composite scores that coaches disagree with destroy trust permanently.
- Conversion rate explorer (F7.2)
- Predictive features (F7.3) — expand shot quality model with: shot type (layup/floater/mid-range/three), movement before shot (catch-and-shoot vs off-dribble vs post-up), nearest help defender distance, shooter fatigue proxy (minutes played), transition vs half-court
- **Defensive metrics (F7.4) — delayed from MVP intentionally.** Defensive stats require accurate defender-to-offensive-player matchup assignment, which depends on perfect tracking. At 85% tracking accuracy, 15% of defensive assignments will be wrong, making floor shrink score and on-ball FG% allowed unreliable. Ship offensive metrics first — they're more forgiving because the ball handler/shooter is usually the most clearly tracked player.
- Playlists and shared links (F8.2) — **shared links must expire (30-day max), require player consent before generation, and include watermark with generating coach identity**
- Scouting reports (F9) — opponent breakdown requires multi-game aggregation pipeline: coach uploads 3+ opponent games → cross-game tendency aggregation → auto-report with opponent player tendencies, team play frequency, spacing patterns, transition tendencies, press break structure
- Alerts (F10)
- Search (F11)
- **Lineup analysis (F12)**: "Which 5-player combinations produce the best offensive rating?" Dashboard showing lineup combinations with: net rating, offensive/defensive rating, minutes played together, sample size. Filterable by game, date range, opponent. Data derived from lineup_entries + possession-level metrics.
- **Practice mode (F13)**: When game_type is "practice," use simplified processing: skip opponent tracking, skip tactical tags, focus on shooting form analysis, play installation execution, and individual movement quality. Practice footage may represent 60%+ of uploads for some programs.

**Post-V1:**
- Volleyball expansion
- Real-time (live) processing
- Mobile native app
- Partner APIs/SDKs

---

## Metric Sequencing (from expert audit)

Ship metrics in this order based on accuracy requirements and coach value:

**Phase 1 (MVP):** Box scores and shooting analysis. High accuracy, immediate value. Coaches can verify against their own manual stats. Trust-building phase.

**Phase 2 (V1 early):** Tendency cards and conversion rates. Medium difficulty, high differentiation. Drive direction, shot selection, play-type PPP — coaches can't get this anywhere else at this price point.

**Phase 3 (V1 late):** Spatial IQ components (NOT composite yet) and predictive features. Ship spacing plots, advantage creation events, and decision latency distributions as individual visualizations. Let coaches build intuition.

**Phase 4 (V2):** Spatial IQ composite score, defensive metrics, lineup analysis. Only after tracking quality is validated across 50+ games from 10+ gyms and the component metrics have been reviewed by coaching advisors.

---

## Missing Event Types (from expert audit)

The following events were identified as high-value "hidden impact" stats missing from the initial spec:

- **Deflections**: Ball deflected by defender without gaining possession. Huge hidden impact stat — correlates strongly with defensive effectiveness.
- **Potential assists**: Pass that led to a shot attempt, regardless of make/miss. More stable than assists for evaluating playmaking.
- **Hockey assists**: Pass to the assister. Identifies secondary playmakers.
- **Charges drawn**: Offensive foul drawn by defender. High-value defensive play.
- **Box-outs**: Blocking out on rebounds. Detectable from tracking data (defender between offensive player and basket at rebound time).
- **Loose ball recoveries**: Gaining possession of a live ball not in anyone's control.
