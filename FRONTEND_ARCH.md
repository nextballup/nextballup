# NextBallUp Frontend Architecture

## Stack

- **Framework**: Next.js 15 (App Router)
- **Language**: TypeScript (strict mode)
- **Styling**: Tailwind CSS 4 + CSS variables for theming
- **State**: Zustand (client state) + TanStack Query v5 (server state)
- **Forms**: React Hook Form + Zod validation
- **Video Player**: Video.js with HLS.js plugin
- **Charts**: Recharts (shot charts, metrics dashboards)
- **Court Visualizations**: D3.js (court diagrams, spatial overlays, shot charts)
- **Auth**: Custom FastAPI-issued JWTs stored in httpOnly cookies (not localStorage). Do not add NextAuth/Auth.js as a parallel auth system.
- **Package Manager**: pnpm

## App Router Structure

```
apps/web/src/
├── app/
│   ├── layout.tsx                    # Root layout (providers, nav shell)
│   ├── page.tsx                      # Landing / marketing page
│   ├── (auth)/                       # Auth group (no nav shell)
│   │   ├── login/page.tsx
│   │   ├── register/page.tsx         # Role selection (coach/player)
│   │   ├── register/coach/page.tsx   # Coach registration form
│   │   ├── register/player/page.tsx  # Player registration form
│   │   └── join/[code]/page.tsx      # Team invite acceptance
│   ├── (dashboard)/                  # Authenticated group (with nav shell)
│   │   ├── layout.tsx                # Bottom tabs (mobile) + slim sidebar (desktop)
│   │   ├── home/page.tsx             # Feed: game summaries, alerts, activity (Tab 1: Home)
│   │   ├── games/                    # Tab 2: Games (film)
│   │   │   ├── page.tsx              # All games list (feed-style cards)
│   │   │   ├── new/page.tsx          # Create game (coach only)
│   │   │   └── [gameId]/
│   │   │       ├── page.tsx          # Game detail (Instagram "post" layout: video top, tabs below)
│   │   │       ├── upload/page.tsx   # Video upload (gradient progress ring)
│   │   │       ├── film/page.tsx     # Film room (reel-style on mobile, side-by-side desktop)
│   │   │       ├── events/page.tsx   # Events list with swipe actions
│   │   │       ├── stats/page.tsx    # Box score + advanced stats
│   │   │       └── metrics/page.tsx  # Hidden impact dashboards
│   │   ├── search/page.tsx           # Tab 3: Cross-entity search
│   │   ├── clips/                    # Tab 4: Clips
│   │   │   ├── page.tsx              # Clip library (3-column grid, Instagram profile style)
│   │   │   └── playlists/
│   │   │       ├── page.tsx          # Playlists as "Guides"
│   │   │       └── [playlistId]/page.tsx
│   │   ├── profile/                  # Tab 5: Profile
│   │   │   ├── page.tsx              # Own profile (Instagram profile layout)
│   │   │   └── settings/page.tsx     # User settings, dark/light toggle
│   │   ├── players/                  # Accessed via search or team roster
│   │   │   └── [playerId]/
│   │   │       ├── page.tsx          # Player profile (Instagram profile layout)
│   │   │       ├── shooting/page.tsx # Shot chart (gradient dots)
│   │   │       ├── tendencies/page.tsx
│   │   │       └── clips/page.tsx    # Player clip grid
│   │   ├── teams/
│   │   │   ├── page.tsx              # Team list
│   │   │   ├── new/page.tsx          # Create team (coach only)
│   │   │   └── [teamId]/
│   │   │       ├── page.tsx          # Team overview + roster
│   │   │       ├── roster/page.tsx
│   │   │       └── settings/page.tsx
│   │   ├── metrics/
│   │   │   ├── spatial-iq/page.tsx
│   │   │   ├── conversion/page.tsx
│   │   │   ├── predictive/page.tsx
│   │   │   └── lineups/page.tsx      # Lineup combination analysis (V1)
│   │   ├── scouting/
│   │   │   ├── page.tsx
│   │   │   └── generate/page.tsx
│   │   └── alerts/
│   │       ├── page.tsx
│   │       └── new/page.tsx
│   └── api/                          # Optional BFF proxy routes only; no NextAuth/Auth.js
│       └── auth/
│           ├── refresh/route.ts
│           └── logout/route.ts
├── components/
│   ├── ui/                    # Generic UI primitives
│   │   ├── Button.tsx
│   │   ├── Input.tsx
│   │   ├── Select.tsx
│   │   ├── Modal.tsx
│   │   ├── Tabs.tsx
│   │   ├── Badge.tsx
│   │   ├── Card.tsx
│   │   ├── Skeleton.tsx       # Loading skeletons
│   │   ├── Toast.tsx
│   │   └── DataTable.tsx      # Sortable/filterable table
│   ├── auth/
│   │   ├── RoleSelector.tsx   # Coach vs Player toggle
│   │   ├── LoginForm.tsx
│   │   └── RegisterForm.tsx
│   ├── team/
│   │   ├── TeamCard.tsx
│   │   ├── RosterTable.tsx
│   │   ├── InviteDialog.tsx
│   │   └── JoinTeamForm.tsx
│   ├── game/
│   │   ├── GameCard.tsx
│   │   ├── GameForm.tsx
│   │   ├── LineupEditor.tsx
│   │   ├── ScoreDisplay.tsx
│   │   └── ProcessingStatus.tsx  # Real-time processing indicator
│   ├── video/
│   │   ├── VideoUploader.tsx     # Drag-drop + progress
│   │   ├── VideoPlayer.tsx       # HLS player with event markers
│   │   ├── EventTimeline.tsx     # Scrollable event strip below player
│   │   └── ClipSelector.tsx      # Set start/end for clip creation
│   ├── player/
│   │   ├── PlayerCard.tsx
│   │   ├── PlayerProfile.tsx
│   │   ├── TendencyCard.tsx
│   │   └── ShotChart.tsx         # D3 court with shot dots
│   ├── metrics/
│   │   ├── SpatialIQGauge.tsx    # Radial gauge for composite score
│   │   ├── ConversionFunnel.tsx  # Action chain → outcome visualization
│   │   ├── CourtHeatmap.tsx      # D3 court with density overlay
│   │   ├── SpacingDiagram.tsx    # Real-time spacing visualization
│   │   └── MetricCompare.tsx     # Side-by-side player comparison
│   ├── clips/
│   │   ├── ClipCard.tsx
│   │   ├── ClipGrid.tsx
│   │   └── PlaylistBuilder.tsx
│   ├── notes/
│   │   ├── NoteInput.tsx          # Inline text input with @mention autocomplete
│   │   ├── NoteList.tsx           # Collapsible note section for events/clips
│   │   ├── NoteMarker.tsx         # Timestamp marker on video seekbar
│   │   ├── NoteBadge.tsx          # Author avatar + role badge + time
│   │   └── MentionAutocomplete.tsx # Team member search for @mentions
│   ├── scouting/
│   │   ├── ReportBuilder.tsx
│   │   └── ReportViewer.tsx
│   └── layout/
│       ├── BottomTabs.tsx        # Mobile bottom tab bar (5 tabs, gradient active indicator)
│       ├── SlimSidebar.tsx       # Desktop sidebar (72px collapsed, 240px expanded)
│       ├── Topbar.tsx            # Notification bell + team name
│       ├── StoriesRow.tsx        # Horizontal scroll of recent games with gradient rings
│       ├── BottomSheet.tsx       # Mobile bottom sheet for filters/actions
│       └── TeamSwitcher.tsx      # Long-press avatar to switch active team
├── stores/
│   ├── authStore.ts           # User, tokens, role
│   ├── teamStore.ts           # Active team context
│   ├── gameStore.ts           # Active game context
│   ├── videoStore.ts          # Video playback state (current time, playing)
│   └── filterStore.ts         # Shared filter state (date range, player, event type)
├── hooks/
│   ├── useAuth.ts             # Auth state + guards
│   ├── useTeam.ts             # Team data fetching
│   ├── useGames.ts            # Game list + detail queries
│   ├── useEvents.ts           # Event list with filters
│   ├── useMetrics.ts          # Metrics queries
│   ├── useVideoUpload.ts     # Upload state machine (presign → upload → complete)
│   ├── useProcessingStatus.ts # WebSocket hook for processing updates
│   └── useCourtDimensions.ts  # Responsive court SVG sizing
├── lib/
│   ├── api.ts                 # Axios/fetch wrapper with auth interceptor
│   ├── constants.ts           # Court dimensions, zone definitions
│   ├── courtGeometry.ts       # Court coordinate ↔ SVG coordinate mapping
│   ├── formatters.ts          # Number, date, time formatters
│   └── validators.ts          # Zod schemas matching API contracts
└── types/
    ├── api.ts                 # Generated or hand-written API types
    ├── user.ts
    ├── team.ts
    ├── game.ts
    ├── event.ts
    ├── metrics.ts
    └── video.ts
```

## Design System — Instagram-Inspired

NextBallUp's UI follows Instagram's UX patterns — content-first, feed-driven, stories-style navigation, bottom tabs on mobile, smooth transitions — adapted for basketball analytics. The color palette replaces Instagram's warm gradient with a cool athletic identity: green/lime for energy and success, blue for information and depth, purple for premium/brand moments.

### Color Palette

```css
:root {
  /* Brand gradient (replaces Instagram's orange→pink→purple) */
  --nbu-gradient: linear-gradient(135deg, #22c55e, #3b82f6, #8b5cf6);
  --nbu-gradient-hover: linear-gradient(135deg, #16a34a, #2563eb, #7c3aed);

  /* Primary — Green/Lime (energy, action, positive outcomes — replaces Instagram orange) */
  --nbu-green-50: #f0fdf4;
  --nbu-green-100: #dcfce7;
  --nbu-green-200: #bbf7d0;
  --nbu-green-400: #4ade80;
  --nbu-green-500: #22c55e;
  --nbu-green-600: #16a34a;
  --nbu-green-700: #15803d;

  /* Secondary — Blue (data, depth, information — replaces Instagram red/pink) */
  --nbu-blue-50: #eff6ff;
  --nbu-blue-100: #dbeafe;
  --nbu-blue-200: #bfdbfe;
  --nbu-blue-400: #60a5fa;
  --nbu-blue-500: #3b82f6;
  --nbu-blue-600: #2563eb;
  --nbu-blue-700: #1d4ed8;

  /* Accent — Purple (premium features, brand moments — same as Instagram) */
  --nbu-purple-50: #faf5ff;
  --nbu-purple-100: #f3e8ff;
  --nbu-purple-200: #e9d5ff;
  --nbu-purple-400: #c084fc;
  --nbu-purple-500: #a855f7;
  --nbu-purple-600: #9333ea;
  --nbu-purple-700: #7c3aed;

  /* Semantic */
  --nbu-success: #22c55e;      /* Green (same as primary — makes scored, positive events) */
  --nbu-warning: #eab308;
  --nbu-error: #ef4444;
  --nbu-info: #3b82f6;         /* Blue (same as secondary) */

  /* Light mode surfaces */
  --nbu-bg: #ffffff;
  --nbu-surface: #fafafa;
  --nbu-surface-elevated: #ffffff;
  --nbu-border: #e5e5e5;
  --nbu-border-light: #f0f0f0;
  --nbu-text: #0a0a0a;
  --nbu-text-muted: #737373;
  --nbu-text-dim: #a3a3a3;

  /* Dark mode surfaces (auto-applied via prefers-color-scheme) */
  --nbu-bg-dark: #000000;
  --nbu-surface-dark: #0a0a0a;
  --nbu-surface-elevated-dark: #171717;
  --nbu-border-dark: #262626;
  --nbu-text-dark: #fafafa;
  --nbu-text-muted-dark: #a3a3a3;
}
```

### Typography

- **Display/Headings**: "Plus Jakarta Sans" (Google Fonts, variable weight) — geometric, modern, Instagram-like
- **Body**: "Plus Jakarta Sans" at 400 weight — clean and readable
- **Mono/Stats**: "JetBrains Mono" (stats tables, shot percentages, metric values)

### Instagram-Style UI Patterns

**Bottom Tab Navigation (mobile)**: 5 tabs — Home (feed), Games (film), Search, Clips, Profile. Active tab icon filled with brand gradient. Tab bar has 1px top border, frosted glass effect (`backdrop-filter: blur(20px)`). On desktop, converts to a slim left sidebar (72px collapsed, 240px expanded) with the same 5 sections plus team switcher.

```
┌──────────────────────────────┐
│  [Content Area]              │
│                              │
│                              │
├──────────────────────────────┤
│  🏠    🎬    🔍    🎞️    👤  │  ← Bottom tabs (mobile)
└──────────────────────────────┘
```

**Stories Row (horizontal scroll at top of Home)**: Recent games appear as circular thumbnails with gradient ring borders (green→blue→purple) when unviewed (new events/processing complete). Tapping opens the game detail. Processing games show an animated ring (like Instagram's upload ring). Watched/reviewed games have a gray ring.

```typescript
// components/layout/StoriesRow.tsx
// - Horizontal scroll with snap points
// - Each "story" is a game: circular thumbnail (court photo or team logo)
// - Gradient ring = new events since last viewed
// - Animated gradient ring = currently processing
// - Gray ring = reviewed
// - Tapping opens game → film room or latest stats
```

**Feed-Based Home**: Home page is a vertical scroll feed of cards, not a dashboard grid. Cards include: game summaries (score + top performer + key stat), processing status updates, new clip highlights, alert triggers, team activity. Each card has: header (game/team context), content (stat, clip thumbnail, or chart), and action row (like, clip, share, comment-style quick actions). Feels like scrolling an Instagram feed but every card is basketball intelligence.

**Game Detail as "Post"**: A game detail page follows Instagram's post layout: full-width video player at top (like a photo), stats and events below (like comments/description), swipeable tabs for Film / Stats / Metrics / Clips (like Instagram's profile grid/reels/tagged tabs).

**Player Profile as Instagram Profile**: Player profile page mirrors Instagram's profile layout: avatar circle with gradient ring (indicating new data), name + position + team, 3 stat boxes in a row (PPG / Spatial IQ / TS% — like Instagram's posts/followers/following), then a tab grid: Overview (tendency card), Shooting (shot chart), Clips (grid of thumbnails), Reports.

```
┌──────────────────────────────┐
│       ○ Avatar (gradient)    │
│    James Williams · SG       │
│    Lincoln Varsity · #23     │
│                              │
│   18.3 PPG │ 78 SIQ │ .582  │
│  ─────────────────────────── │
│  [Overview] [Shooting] [Clips]│
│                              │
│  ┌──────┐ ┌──────┐ ┌──────┐ │
│  │ clip │ │ clip │ │ clip │ │
│  └──────┘ └──────┘ └──────┘ │
└──────────────────────────────┘
```

**Gradient Usage**: The green→blue→purple gradient appears on: story rings (unviewed games), the primary CTA button, the processing progress ring, the Spatial IQ gauge fill, shot chart make-dots (green→blue gradient by shot quality), and the team invite link share button. Gradient is never used on text or backgrounds — only on interactive elements and data visualization accents.

**Card Design**: White cards (dark mode: #171717) with 1px border (#e5e5e5 / #262626), 16px border-radius, no shadow. Content-first: media bleeds to card edges, text has 16px padding. Interaction: long-press on a clip card shows a preview popup (like Instagram's peek). Double-tap on a game card bookmarks it for quick access.

**Transitions**: Page transitions use shared element animations — tapping a game card in the feed expands the thumbnail into the full video player (like Instagram opening a post from the grid). Tab switches use horizontal slide. Bottom sheets slide up from the bottom on mobile for filters, event correction, and clip creation.

**Notification Bell**: Top-right of the screen, Instagram-style. Red dot for unread. Notification list shows: alert triggers, processing completions, team invites, correction requests. Each notification links to the relevant game/event.

### Key UI Patterns (updated)

**Role-Aware UI**: Coaches see the full feed (upload buttons, edit actions, scouting tools). Players see a personal feed (their games, their clips, their profile) — similar to how Instagram shows different options for personal vs business accounts. The navigation stays identical; the content and available actions change.

**Team Context**: Team switcher in the sidebar (desktop) or profile tab (mobile) — styled like Instagram's account switcher. Long-press on avatar to switch teams. Active team shown as a subtle label below the app logo.

**Film Room**: The core UX is still video player + event timeline, but styled as a "reel-style" fullscreen experience on mobile. Swipe up/down to navigate between events (like scrolling reels). On desktop, side-by-side layout with the timeline as a scrollable strip. Events are pills with gradient-colored left borders by type (green for scoring, blue for defensive, purple for tactical).

**Shot Chart**: Half-court SVG with the brand gradient applied to data: makes are green dots, misses are muted gray, shot quality maps from green (high) → blue (medium) → purple (low quality but attempted). Zone overlays use the same gradient stops.

**Processing Status**: Full-screen animated ring (like Instagram's story upload) with the brand gradient rotating around a basketball icon. Stage labels appear below as they complete. Feels native to the Instagram mental model — "your content is uploading."

**Clip Cards in Grid**: 3-column grid on desktop, 3-column on mobile (like Instagram's profile grid). Each cell is a square thumbnail from the clip. Hover/long-press shows a preview with the play description. Playlists are presented as "Guides" — a cover image with a title, description, and ordered list of clips.

### Dark Mode

Dark mode is the default (matches Instagram's dark mode). Pure black background (#000000) for OLED screens. Surface cards are #0a0a0a with #262626 borders. The brand gradient (green→blue→purple) pops dramatically on dark backgrounds. All text uses #fafafa primary and #a3a3a3 muted. Light mode is clean white (#ffffff) with #e5e5e5 borders. Toggle in settings, respects system preference.

### Micro-Interactions

- **Double-tap** on a game card → bookmark for quick access (heart animation, green)
- **Long-press** on a clip → preview popup with play details
- **Swipe left** on an event in the timeline → create clip
- **Swipe right** on an event → flag for correction
- **Pull-to-refresh** on feed → check for new processing results
- **Haptic feedback** on mobile for all interactive gestures
- All animations use `cubic-bezier(0.25, 0.46, 0.45, 0.94)` (Instagram's easing curve)
- Motion respects `prefers-reduced-motion`

## API Client

```typescript
// lib/api.ts
import axios from "axios";

const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1",
  withCredentials: true,
});

// Request interceptor: attach token
api.interceptors.request.use((config) => {
  const token = getAccessToken(); // from cookie
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// Response interceptor: auto-refresh on 401
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401 && !error.config._retry) {
      error.config._retry = true;
      const newToken = await refreshToken();
      error.config.headers.Authorization = `Bearer ${newToken}`;
      return api(error.config);
    }
    return Promise.reject(error);
  }
);

export default api;
```

## State Management

```typescript
// stores/teamStore.ts
import { create } from "zustand";
import { persist } from "zustand/middleware";

interface TeamState {
  activeTeamId: string | null;
  setActiveTeam: (teamId: string) => void;
}

export const useTeamStore = create<TeamState>()(
  persist(
    (set) => ({
      activeTeamId: null,
      setActiveTeam: (teamId) => set({ activeTeamId: teamId }),
    }),
    { name: "nbu-team" }
  )
);
```

## Video Player Integration

Use Video.js with `@videojs/http-streaming` for HLS. Overlay event markers on the seekbar.

```typescript
// components/video/VideoPlayer.tsx key features:
// 1. Accept HLS manifest URL with signed token
// 2. Render event markers on the progress bar
// 3. Expose onTimeUpdate callback for syncing with EventTimeline
// 4. Support keyboard shortcuts (J/K/L, arrows, space)
// 5. Picture-in-picture support for reviewing while browsing events
```

## Responsive Breakpoints

```
sm: 640px   — Mobile (single column)
md: 768px   — Tablet (sidebar collapses)
lg: 1024px  — Desktop (sidebar + content)
xl: 1280px  — Wide desktop (film room side-by-side)
2xl: 1536px — Ultra-wide (3-column layouts)
```

Film room layout: below `xl`, video and timeline stack vertically. Above `xl`, side-by-side.
