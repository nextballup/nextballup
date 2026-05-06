"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { ACTIVE_TEAM_COOKIE } from "@/lib/active-team";
import { apiJson } from "@/lib/api-client";
import { ApiError, isEmailVerificationRequiredError } from "@/lib/errors";
import type {
  InstitutionType,
  Sport,
  TeamCreatedResponse,
  TeamLevel,
} from "@/lib/contract";

const LEVELS: TeamLevel[] = [
  "youth",
  "aau_club",
  "middle_school",
  "high_school",
  "juco",
  "college_d3",
  "college_d2",
  "college_d1",
  "professional",
  "international",
];

const INSTITUTION_TYPES: InstitutionType[] = [
  "none",
  "k12_school",
  "college",
  "club",
  "academy",
  "professional",
];

const SPORTS: Sport[] = ["basketball", "volleyball"];

function currentSeason(): string {
  const now = new Date();
  const year = now.getUTCFullYear();
  const month = now.getUTCMonth();
  // Approximate: northern-hemisphere high-school/college seasons roll over
  // mid-year. July onward → YYYY-(YYYY+1); before → (YYYY-1)-YYYY.
  if (month >= 6) {
    return `${year}-${year + 1}`;
  }
  return `${year - 1}-${year}`;
}

function createTeamErrorMessage(err: ApiError): string {
  if (isEmailVerificationRequiredError(err)) {
    return "Verify your email before creating a team.";
  }
  if (err.status === 403) {
    return "Only coach accounts can create teams.";
  }
  return err.message;
}

export function CreateTeamForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [sport, setSport] = useState<Sport>("basketball");
  const [level, setLevel] = useState<TeamLevel>("high_school");
  const [institution, setInstitution] = useState("");
  const [institutionType, setInstitutionType] =
    useState<InstitutionType>("k12_school");
  const [season, setSeason] = useState(currentSeason());
  const [city, setCity] = useState("");
  const [state, setState] = useState("");
  const [conference, setConference] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const created = await apiJson<TeamCreatedResponse>("/teams", {
        method: "POST",
        json: {
          name: name.trim(),
          sport,
          level,
          institution: institution.trim() || null,
          institution_type: institutionType,
          season: season.trim(),
          city: city.trim() || null,
          state: state.trim() || null,
          conference: conference.trim() || null,
        },
      });
      // Make the new team the active team immediately — the picker cookie is
      // UX state only (see lib/active-team.ts), so writing it client-side is
      // safe.
      document.cookie = `${ACTIVE_TEAM_COOKIE}=${encodeURIComponent(created.id)}; Path=/; Max-Age=${60 * 60 * 24 * 30}; SameSite=Lax`;
      router.replace(`/teams/${created.id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(createTeamErrorMessage(err));
      } else {
        setError("Could not create team.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-label="Create team"
      className="space-y-4 rounded-lg border border-[color:var(--color-nbu-border)] p-4"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block space-y-1 text-sm sm:col-span-2">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Team name
          </span>
          <input
            required
            minLength={1}
            maxLength={255}
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Sport
          </span>
          <select
            value={sport}
            onChange={(e) => setSport(e.target.value as Sport)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          >
            {SPORTS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Level
          </span>
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value as TeamLevel)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          >
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {l.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Season
          </span>
          <input
            required
            placeholder="2026-2027"
            value={season}
            onChange={(e) => setSeason(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 font-mono outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Institution type
          </span>
          <select
            value={institutionType}
            onChange={(e) => setInstitutionType(e.target.value as InstitutionType)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          >
            {INSTITUTION_TYPES.map((t) => (
              <option key={t} value={t}>
                {t.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="block space-y-1 text-sm sm:col-span-2">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Institution{" "}
            <span className="font-normal text-[color:var(--color-nbu-text-muted)]">
              (optional)
            </span>
          </span>
          <input
            maxLength={255}
            value={institution}
            onChange={(e) => setInstitution(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            City
          </span>
          <input
            maxLength={100}
            value={city}
            onChange={(e) => setCity(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            State
          </span>
          <input
            maxLength={10}
            value={state}
            onChange={(e) => setState(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        <label className="block space-y-1 text-sm sm:col-span-2">
          <span className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Conference{" "}
            <span className="font-normal text-[color:var(--color-nbu-text-muted)]">
              (optional)
            </span>
          </span>
          <input
            maxLength={255}
            value={conference}
            onChange={(e) => setConference(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
      </div>

      {error && (
        <p role="alert" className="text-sm text-[color:var(--color-nbu-error)]">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90 disabled:opacity-50"
      >
        {submitting ? "Creating…" : "Create team"}
      </button>
    </form>
  );
}
