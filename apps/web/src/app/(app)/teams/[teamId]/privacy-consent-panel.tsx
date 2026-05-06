"use client";

import { useEffect, useState, type FormEvent } from "react";
import { apiJson } from "@/lib/api-client";
import { ApiError, isEmailVerificationRequiredError } from "@/lib/errors";
import type {
  TeamPrivacyConsentListResponse,
  TeamPrivacyConsentResponse,
} from "@/lib/contract";

const CONSENT_SOURCES = [
  "written_permission",
  "registration_terms",
  "league_policy",
  "school_policy",
  "other",
];

function consentErrorMessage(error: ApiError): string {
  if (isEmailVerificationRequiredError(error)) {
    return "Verify your email before recording consent evidence.";
  }
  if (error.status === 403) {
    return "You need coach access on this team to record consent evidence.";
  }
  return error.message;
}

export function PrivacyConsentPanel({ teamId }: { teamId: string }) {
  const [consents, setConsents] = useState<TeamPrivacyConsentResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [label, setLabel] = useState("Team filming permission");
  const [consentSource, setConsentSource] = useState("written_permission");
  const [evidenceUri, setEvidenceUri] = useState("");
  const [coversVideoUploads, setCoversVideoUploads] = useState(true);
  const [coversCvProcessing, setCoversCvProcessing] = useState(true);
  const [minorsAuthorized, setMinorsAuthorized] = useState(false);
  const [athletePiiAuthorized, setAthletePiiAuthorized] = useState(true);
  const [commercialMlTrainingAllowed, setCommercialMlTrainingAllowed] =
    useState(false);
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiJson<TeamPrivacyConsentListResponse>(
      `/teams/${teamId}/privacy-consents`,
      {
        method: "GET",
        cache: "no-store",
      },
    )
      .then((response) => {
        if (!cancelled) {
          setConsents(response.consents);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError("Consent records are unavailable.");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [teamId]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setSuccess(null);
    try {
      const created = await apiJson<TeamPrivacyConsentResponse>(
        `/teams/${teamId}/privacy-consents`,
        {
          method: "POST",
          json: {
            label: label.trim(),
            consent_source: consentSource,
            evidence_uri: evidenceUri.trim(),
            covers_video_uploads: coversVideoUploads,
            covers_cv_processing: coversCvProcessing,
            minors_authorized: minorsAuthorized,
            athlete_pii_authorized: athletePiiAuthorized,
            commercial_ml_training_allowed: commercialMlTrainingAllowed,
            notes: notes.trim() || null,
          },
        },
      );
      setConsents((current) => [created, ...current]);
      setSuccess("Consent evidence saved.");
      setEvidenceUri("");
      setNotes("");
      setCommercialMlTrainingAllowed(false);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(consentErrorMessage(err));
      } else {
        setError("Could not save consent evidence.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  const activeConsents = consents.filter((consent) => consent.is_active);

  return (
    <section
      aria-labelledby="privacy-consent-heading"
      className="space-y-4 rounded-lg border border-[color:var(--color-nbu-border)] p-4"
    >
      <div>
        <h2
          id="privacy-consent-heading"
          className="text-sm font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]"
        >
          Privacy consent evidence
        </h2>
        <p className="mt-1 text-xs text-[color:var(--color-nbu-text-muted)]">
          Record the permission reference needed before sensitive youth/K-12
          film is uploaded or processed.
        </p>
      </div>

      <div className="space-y-2">
        {loading ? (
          <p role="status" className="text-sm text-[color:var(--color-nbu-text-muted)]">
            Loading consent records.
          </p>
        ) : activeConsents.length > 0 ? (
          <ul className="divide-y divide-[color:var(--color-nbu-border)] rounded-md border border-[color:var(--color-nbu-border)] text-sm">
            {activeConsents.map((consent) => (
              <li key={consent.id} className="px-3 py-2">
                <div className="font-medium">{consent.label}</div>
                <div className="text-xs text-[color:var(--color-nbu-text-muted)]">
                  {consent.consent_source.replaceAll("_", " ")}
                  {consent.minors_authorized ? " · minors authorized" : ""}
                  {consent.commercial_ml_training_allowed
                    ? " · commercial training allowed"
                    : ""}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-[color:var(--color-nbu-text-muted)]">
            No active consent evidence recorded.
          </p>
        )}
      </div>

      <form
        onSubmit={handleSubmit}
        aria-label="Record privacy consent"
        className="grid gap-3 sm:grid-cols-2"
      >
        <label className="space-y-1 text-xs sm:col-span-2">
          <span className="uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Label
          </span>
          <input
            required
            maxLength={120}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 text-sm outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        <label className="space-y-1 text-xs">
          <span className="uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Source
          </span>
          <select
            value={consentSource}
            onChange={(e) => setConsentSource(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 text-sm outline-none focus:border-[color:var(--color-nbu-text)]"
          >
            {CONSENT_SOURCES.map((source) => (
              <option key={source} value={source}>
                {source.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-xs">
          <span className="uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Evidence reference
          </span>
          <input
            required
            maxLength={1024}
            value={evidenceUri}
            onChange={(e) => setEvidenceUri(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 text-sm outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        <fieldset className="grid gap-2 text-sm sm:col-span-2 sm:grid-cols-2">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={coversVideoUploads}
              onChange={(e) => setCoversVideoUploads(e.target.checked)}
            />
            <span>Covers video uploads</span>
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={coversCvProcessing}
              onChange={(e) => setCoversCvProcessing(e.target.checked)}
            />
            <span>Covers CV processing</span>
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={minorsAuthorized}
              onChange={(e) => setMinorsAuthorized(e.target.checked)}
            />
            <span>Minor athletes authorized</span>
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={athletePiiAuthorized}
              onChange={(e) => setAthletePiiAuthorized(e.target.checked)}
            />
            <span>Athlete PII authorized</span>
          </label>
          <label className="flex items-center gap-2 sm:col-span-2">
            <input
              type="checkbox"
              checked={commercialMlTrainingAllowed}
              onChange={(e) => setCommercialMlTrainingAllowed(e.target.checked)}
            />
            <span>Commercial ML training allowed</span>
          </label>
        </fieldset>
        <label className="space-y-1 text-xs sm:col-span-2">
          <span className="uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Notes
          </span>
          <textarea
            rows={2}
            maxLength={2000}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-2 text-sm outline-none focus:border-[color:var(--color-nbu-text)]"
          />
        </label>
        {error && (
          <p role="alert" className="text-sm text-[color:var(--color-nbu-error)] sm:col-span-2">
            {error}
          </p>
        )}
        {success && !error && (
          <p role="status" className="text-sm text-green-600 sm:col-span-2">
            {success}
          </p>
        )}
        <div className="sm:col-span-2">
          <button
            type="submit"
            disabled={submitting}
            className="rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "Saving..." : "Save consent evidence"}
          </button>
        </div>
      </form>
    </section>
  );
}
