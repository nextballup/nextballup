import Link from "next/link";
import { CreateTeamForm } from "./create-team-form";

export default function NewTeamPage() {
  return (
    <section className="space-y-6">
      <nav aria-label="Breadcrumb" className="text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
        <Link href="/teams" className="hover:underline">
          ← All teams
        </Link>
      </nav>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Create team</h1>
        <p className="mt-1 text-sm text-[color:var(--color-nbu-text-muted)]">
          Only coaches can create teams. You become head coach automatically.
        </p>
      </div>
      <CreateTeamForm />
    </section>
  );
}
