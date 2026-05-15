import type { Metadata } from "next";
import { MarketingHeader } from "@/components/marketing/header";
import { MarketingFooter } from "@/components/marketing/sections";
import { PilotInterestForm } from "./pilot-interest-form";

export const metadata: Metadata = {
  title: "Request pilot access",
  description:
    "Tell us about your team and we will reach out when a NextBallUp alpha pilot slot opens. Invite-only; not production analytics.",
  alternates: { canonical: "/pilot" },
  robots: { index: true, follow: true },
};

export default function PilotPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <MarketingHeader />
      <main className="flex-1">
        <section className="border-b border-[color:var(--color-nbu-border)]">
          <div className="mx-auto w-full max-w-3xl px-4 py-16 sm:px-6">
            <p className="text-xs font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
              Pilot access
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">
              Tell us a little about your team.
            </h1>
            <p className="mt-3 text-base text-[color:var(--color-nbu-text-muted)]">
              Pilots are invite-only. We do not share your submission, run
              advertising trackers, or sign you up for a mailing list.
            </p>
            <div className="mt-8 rounded-lg border border-[color:var(--color-nbu-border)] p-5">
              <PilotInterestForm />
            </div>
            <p className="mt-6 text-xs text-[color:var(--color-nbu-text-muted)]">
              Submissions are logged for triage only. NextBallUp alpha output is
              review-only and is not production analytics.
            </p>
          </div>
        </section>
      </main>
      <MarketingFooter />
    </div>
  );
}
