import Link from "next/link";
import { CandidateReviewMockup } from "./candidate-review-mockup";

export function HeroSection() {
  return (
    <section className="border-b border-[color:var(--color-nbu-border)]">
      <div className="mx-auto grid w-full max-w-6xl gap-10 px-4 py-16 sm:px-6 lg:grid-cols-[1.05fr_1fr] lg:items-center lg:py-24">
        <div className="space-y-6">
          <p className="inline-flex items-center gap-2 rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Alpha · pilot access only
          </p>
          <h1 className="text-balance text-4xl font-semibold tracking-tight sm:text-5xl">
            AI-assisted basketball film review,
            <span className="text-[color:var(--color-nbu-text-muted)]"> built around the coach.</span>
          </h1>
          <p className="max-w-xl text-base text-[color:var(--color-nbu-text-muted)] sm:text-lg">
            Upload game film, get browser playback, and triage alpha detector
            candidates before they ever turn into stats. The coach decides what
            counts.
          </p>
          <div className="flex flex-wrap gap-3">
            <Link
              href="/pilot"
              data-testid="hero-pilot-cta"
              className="rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2.5 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--color-nbu-text)]"
            >
              Request pilot access
            </Link>
            <Link
              href="/product"
              className="rounded-md border border-[color:var(--color-nbu-border)] px-4 py-2.5 text-sm font-medium transition hover:border-[color:var(--color-nbu-text)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--color-nbu-text)]"
            >
              See how it works
            </Link>
          </div>
          <p className="text-xs text-[color:var(--color-nbu-text-muted)]">
            Alpha output is review-only. Not production analytics, not
            recruiting data.
          </p>
        </div>
        <div>
          <CandidateReviewMockup />
        </div>
      </div>
    </section>
  );
}

const WORKFLOW_STEPS: ReadonlyArray<{ title: string; body: string }> = [
  {
    title: "1. Upload game or practice film",
    body: "Drop in MP4/MOV/MKV. Files stay private to your team; raw uploads are never served straight to the browser.",
  },
  {
    title: "2. Browser playback",
    body: "A sanitized mezzanine MP4 is generated for in-browser review. The original upload stays gated behind signed URLs.",
  },
  {
    title: "3. Alpha detector preview",
    body: "An on-demand detector overlay surfaces candidate moments. It is explicitly a preview, not a finished tracker.",
  },
  {
    title: "4. Review candidate moments",
    body: "Filter by event type, review status, or source. Approved and rejected candidates stay reachable through filters and history.",
  },
  {
    title: "5. Coach confirms, rejects, or tags",
    body: "Every state change is auditable. Manual tags created during playback live next to the detector candidates in the same list.",
  },
];

export function WorkflowSection() {
  return (
    <section
      id="workflow"
      aria-labelledby="workflow-heading"
      className="border-b border-[color:var(--color-nbu-border)]"
    >
      <div className="mx-auto w-full max-w-6xl px-4 py-16 sm:px-6">
        <div className="max-w-2xl space-y-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Workflow
          </p>
          <h2 id="workflow-heading" className="text-3xl font-semibold tracking-tight sm:text-4xl">
            One pass through the film, five honest steps.
          </h2>
          <p className="text-base text-[color:var(--color-nbu-text-muted)]">
            No black-box pipeline. Each step is observable in the product, and
            the coach has the final say.
          </p>
        </div>
        <ol className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {WORKFLOW_STEPS.map((step) => (
            <li
              key={step.title}
              className="rounded-lg border border-[color:var(--color-nbu-border)] p-5"
            >
              <h3 className="text-sm font-semibold">{step.title}</h3>
              <p className="mt-2 text-sm text-[color:var(--color-nbu-text-muted)]">
                {step.body}
              </p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

const USE_CASES: ReadonlyArray<{ heading: string; body: string }> = [
  {
    heading: "High-school programs",
    body: "Archive game film across a season, surface candidate moments for the next practice, and keep film private to staff.",
  },
  {
    heading: "Club and AAU teams",
    body: "Run a season's worth of film through one review surface; manual coach tags live next to detector candidates without losing context.",
  },
  {
    heading: "Skills trainers",
    body: "Tag working sets at the moments they happen during playback and revisit them later, without any third-party tracker watching.",
  },
  {
    heading: "Small staffs",
    body: "No editor handoff required. Candidate review is the workflow — approve, reject, or add a tag in the same panel.",
  },
];

export function UseCasesSection() {
  return (
    <section
      id="use-cases"
      aria-labelledby="use-cases-heading"
      className="border-b border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)]"
    >
      <div className="mx-auto w-full max-w-6xl px-4 py-16 sm:px-6">
        <div className="max-w-2xl space-y-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Use cases
          </p>
          <h2 id="use-cases-heading" className="text-3xl font-semibold tracking-tight sm:text-4xl">
            Built for the coaches actually pressing play.
          </h2>
          <p className="text-base text-[color:var(--color-nbu-text-muted)]">
            We do not claim widespread adoption, recruiting databases, or
            verified team logos. Pilot programs are invite-only and explicitly
            scoped.
          </p>
        </div>
        <div className="mt-10 grid gap-4 sm:grid-cols-2">
          {USE_CASES.map((useCase) => (
            <div
              key={useCase.heading}
              className="rounded-lg border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-bg)] p-5"
            >
              <h3 className="text-sm font-semibold">{useCase.heading}</h3>
              <p className="mt-2 text-sm text-[color:var(--color-nbu-text-muted)]">
                {useCase.body}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

const SECURITY_POINTS: ReadonlyArray<{ heading: string; body: string }> = [
  {
    heading: "Restricted access",
    body: "Pilot environments sit behind invite-only authentication. The public marketing site does not share auth cookies with the product.",
  },
  {
    heading: "Private storage",
    body: "Uploads live in tenant-scoped object storage. Browsers never receive direct download URLs to original files; playback uses short-lived signed links.",
  },
  {
    heading: "Coach review required",
    body: "Detector output is labeled \"Alpha candidate\" and stays in the needs-review queue until a coach confirms or rejects it.",
  },
  {
    heading: "Audit on every change",
    body: "Manual tag creation, review status changes, and admin actions are all written to an append-only audit log.",
  },
  {
    heading: "No public athlete exposure",
    body: "We do not publish team rosters, athlete pages, or scrapeable profiles. Marketing pages carry no third-party trackers.",
  },
  {
    heading: "Honest scope",
    body: "Tenant isolation is enforced in Postgres via row-level security in addition to the app-layer guards. Detector lineage strings and storage keys never leave the server.",
  },
];

export function SecuritySection() {
  return (
    <section
      id="security"
      aria-labelledby="security-heading"
      className="border-b border-[color:var(--color-nbu-border)]"
    >
      <div className="mx-auto w-full max-w-6xl px-4 py-16 sm:px-6">
        <div className="max-w-2xl space-y-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Security &amp; privacy
          </p>
          <h2 id="security-heading" className="text-3xl font-semibold tracking-tight sm:text-4xl">
            Defaults that make sense for school and club film.
          </h2>
          <p className="text-base text-[color:var(--color-nbu-text-muted)]">
            We approach this as if your athletes&apos; families would read every line
            of the policy &mdash; because eventually they will.
          </p>
        </div>
        <ul className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {SECURITY_POINTS.map((point) => (
            <li
              key={point.heading}
              className="rounded-lg border border-[color:var(--color-nbu-border)] p-5"
            >
              <h3 className="text-sm font-semibold">{point.heading}</h3>
              <p className="mt-2 text-sm text-[color:var(--color-nbu-text-muted)]">
                {point.body}
              </p>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

const FAQ: ReadonlyArray<{ question: string; answer: string }> = [
  {
    question: "Is this production-grade analytics?",
    answer:
      "No. The current alpha detector emits candidate moments only; it is not a verified tracker, scoring engine, or recruiting database. Every candidate is labeled review-only and requires a coach to confirm.",
  },
  {
    question: "Whose camera angles does the detector work best on?",
    answer:
      "The alpha was trained on a specific window of game film. Camera angles, lighting, and framing different from that window can degrade output. We surface this honestly in the review UI rather than hiding it behind a confidence score.",
  },
  {
    question: "Where does my film go?",
    answer:
      "Uploads land in tenant-scoped object storage, transcoded once to a browser-safe mezzanine MP4 for playback, and gated behind short-lived signed URLs. Originals are never served directly to the browser.",
  },
  {
    question: "Can I sign up today?",
    answer:
      "Public registration is closed. Pilot access is invite-only; submit the form on the pilot page and we will get back to you when a slot opens.",
  },
  {
    question: "How do you handle athlete privacy?",
    answer:
      "There are no public athlete profiles, no scrapeable rosters, and no third-party trackers on the marketing site. Film stays scoped to the team that uploaded it.",
  },
];

export function FaqSection() {
  return (
    <section
      id="faq"
      aria-labelledby="faq-heading"
      className="border-b border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)]"
    >
      <div className="mx-auto w-full max-w-3xl px-4 py-16 sm:px-6">
        <div className="space-y-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            FAQ
          </p>
          <h2 id="faq-heading" className="text-3xl font-semibold tracking-tight sm:text-4xl">
            Straight answers about the alpha.
          </h2>
        </div>
        <dl className="mt-10 divide-y divide-[color:var(--color-nbu-border)] rounded-lg border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-bg)]">
          {FAQ.map((item) => (
            <div key={item.question} className="px-5 py-4">
              <dt className="text-sm font-semibold">{item.question}</dt>
              <dd className="mt-2 text-sm text-[color:var(--color-nbu-text-muted)]">
                {item.answer}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}

const HOMEPAGE_TEASERS: ReadonlyArray<{
  href: string;
  eyebrow: string;
  title: string;
  body: string;
}> = [
  {
    href: "/product",
    eyebrow: "Product",
    title: "Upload, review, tag — in that order.",
    body: "A five-step workflow that keeps the coach in the loop and the model honest. Upload film, get playback, surface candidates, confirm or reject.",
  },
  {
    href: "/use-cases",
    eyebrow: "Use cases",
    title: "Built for the coaches actually pressing play.",
    body: "High-school programs, club and AAU teams, skills trainers, and small staffs that don't have an editor sitting next to them.",
  },
  {
    href: "/security",
    eyebrow: "Security & privacy",
    title: "Defaults that make sense for school and club film.",
    body: "Restricted access, private storage, coach review required, audit on every change. No public athlete pages, no third-party trackers.",
  },
  {
    href: "/faq",
    eyebrow: "FAQ",
    title: "Straight answers about the alpha.",
    body: "What works today, what doesn't, where the model breaks down, and how invite-only pilot access actually works.",
  },
];

export function HomepageTeasers() {
  return (
    <section
      aria-labelledby="teasers-heading"
      className="border-b border-[color:var(--color-nbu-border)]"
    >
      <div className="mx-auto w-full max-w-6xl px-4 py-16 sm:px-6">
        <div className="max-w-2xl space-y-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            What&apos;s inside
          </p>
          <h2
            id="teasers-heading"
            className="text-3xl font-semibold tracking-tight sm:text-4xl"
          >
            Pick the part you care about.
          </h2>
        </div>
        <div className="mt-10 grid gap-4 sm:grid-cols-2">
          {HOMEPAGE_TEASERS.map((teaser) => (
            <Link
              key={teaser.href}
              href={teaser.href}
              data-testid={`teaser-${teaser.href.replace(/^\/+/, "")}`}
              className="group block rounded-lg border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-bg)] p-5 transition hover:border-[color:var(--color-nbu-text)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--color-nbu-text)]"
            >
              <p className="text-[10px] font-semibold uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
                {teaser.eyebrow}
              </p>
              <h3 className="mt-2 text-lg font-semibold tracking-tight">
                {teaser.title}
              </h3>
              <p className="mt-2 text-sm text-[color:var(--color-nbu-text-muted)]">
                {teaser.body}
              </p>
              <p className="mt-3 text-xs font-medium text-[color:var(--color-nbu-text)] transition group-hover:underline">
                Learn more →
              </p>
            </Link>
          ))}
        </div>
      </div>
    </section>
  );
}

export function PilotCallToAction({
  eyebrow = "Ready to try the alpha?",
  body = "Pilot access is invite-only. Tell us about your team and we will reach out when a slot opens.",
}: {
  eyebrow?: string;
  body?: string;
} = {}) {
  return (
    <section
      aria-labelledby="pilot-cta-heading"
      className="border-b border-[color:var(--color-nbu-border)]"
    >
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-4 py-12 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div className="max-w-2xl space-y-1">
          <h2
            id="pilot-cta-heading"
            className="text-2xl font-semibold tracking-tight sm:text-3xl"
          >
            {eyebrow}
          </h2>
          <p className="text-sm text-[color:var(--color-nbu-text-muted)]">{body}</p>
        </div>
        <Link
          href="/pilot"
          data-testid="cta-pilot"
          className="self-start rounded-md bg-[color:var(--color-nbu-text)] px-4 py-2.5 text-sm font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--color-nbu-text)] sm:self-auto"
        >
          Request pilot access
        </Link>
      </div>
    </section>
  );
}

export function MarketingFooter() {
  return (
    <footer className="border-t border-[color:var(--color-nbu-border)]">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-3 px-4 py-8 text-xs text-[color:var(--color-nbu-text-muted)] sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <p>© {new Date().getUTCFullYear()} NextBallUp. Alpha · pilot access only.</p>
        <p>Not affiliated with any league, school, or third-party platform.</p>
      </div>
    </footer>
  );
}
