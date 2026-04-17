import Link from "next/link";
import { JoinTeamForm } from "./join-team-form";

export default async function JoinTeamPage({
  searchParams,
}: {
  searchParams: Promise<{ code?: string }>;
}) {
  const params = await searchParams;
  return (
    <section className="space-y-6">
      <nav aria-label="Breadcrumb" className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
        <Link href="/teams" className="hover:underline">
          ← All teams
        </Link>
      </nav>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Join a team</h1>
        <p className="mt-1 text-sm text-[color:var(--color-nbu-text-muted)]">
          Paste the invite code your coach shared. Players need a jersey
          number; coaches joining with a staff invite can leave it blank.
        </p>
      </div>
      <JoinTeamForm initialCode={params.code ?? ""} />
    </section>
  );
}
