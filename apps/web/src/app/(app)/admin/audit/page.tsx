import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { serverApiJson, serverApiOptional } from "@/lib/api-server";
import { ApiError } from "@/lib/errors";
import type { AuditLogPage, UserPublic } from "@/lib/contract";

// Shown items cap — the backend enforces a hard ceiling; we only pass a
// modest page size so operator sessions don't accidentally pin the audit
// table in memory.
const PAGE_SIZE = 50;

export default async function AuditLogPage_({
  searchParams,
}: {
  searchParams: Promise<{
    action?: string;
    team_id?: string;
    actor_user_id?: string;
    resource_type?: string;
    cursor?: string;
  }>;
}) {
  // Admin gate — coach/player roles shouldn't even know the route exists.
  // We short-circuit with notFound() (404) rather than a 403 banner so the
  // surface is invisible to unauthorized viewers.
  const user = await serverApiOptional<UserPublic>("/auth/me");
  if (!user) {
    redirect("/login");
  }
  if (user.role !== "admin") {
    notFound();
  }

  const params = await searchParams;
  const query = new URLSearchParams();
  query.set("limit", String(PAGE_SIZE));
  if (params.action) query.set("action", params.action);
  if (params.team_id) query.set("team_id", params.team_id);
  if (params.actor_user_id) query.set("actor_user_id", params.actor_user_id);
  if (params.resource_type) query.set("resource_type", params.resource_type);
  if (params.cursor) query.set("cursor", params.cursor);

  let page: AuditLogPage;
  try {
    page = await serverApiJson<AuditLogPage>(
      `/admin/audit/logs?${query.toString()}`,
    );
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      redirect("/login");
    }
    throw error;
  }

  const buildQuery = (overrides: Record<string, string | undefined>) => {
    const next = new URLSearchParams();
    for (const [key, value] of [
      ["action", params.action],
      ["team_id", params.team_id],
      ["actor_user_id", params.actor_user_id],
      ["resource_type", params.resource_type],
    ] as const) {
      if (value) next.set(key, value);
    }
    for (const [key, value] of Object.entries(overrides)) {
      if (value === undefined) {
        next.delete(key);
      } else {
        next.set(key, value);
      }
    }
    return next.toString();
  };

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Audit log</h1>
        <p className="text-sm text-[color:var(--color-nbu-text-muted)]">
          Append-only record of state-changing events across every tenant.
          Used for SOC 2 evidence, GDPR subject-access requests, and incident
          review. Rows are immutable at the database level.
        </p>
      </header>

      <form
        action="/admin/audit"
        method="get"
        className="grid gap-3 rounded-lg border border-[color:var(--color-nbu-border)] p-4 sm:grid-cols-2 lg:grid-cols-4"
      >
        <label className="block space-y-1 text-xs">
          <span className="uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Action
          </span>
          <input
            name="action"
            defaultValue={params.action ?? ""}
            placeholder="e.g. videos.upload.complete"
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-1.5 text-sm"
          />
        </label>
        <label className="block space-y-1 text-xs">
          <span className="uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Team id
          </span>
          <input
            name="team_id"
            defaultValue={params.team_id ?? ""}
            placeholder="UUID"
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-1.5 text-sm font-mono"
          />
        </label>
        <label className="block space-y-1 text-xs">
          <span className="uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Actor user id
          </span>
          <input
            name="actor_user_id"
            defaultValue={params.actor_user_id ?? ""}
            placeholder="UUID"
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-1.5 text-sm font-mono"
          />
        </label>
        <label className="block space-y-1 text-xs">
          <span className="uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
            Resource type
          </span>
          <input
            name="resource_type"
            defaultValue={params.resource_type ?? ""}
            placeholder="video, team, game, …"
            className="w-full rounded-md border border-[color:var(--color-nbu-border)] bg-[color:var(--color-nbu-surface)] px-3 py-1.5 text-sm"
          />
        </label>
        <div className="flex items-end gap-2 sm:col-span-2 lg:col-span-4">
          <button
            type="submit"
            className="rounded-md bg-[color:var(--color-nbu-text)] px-4 py-1.5 text-xs font-medium text-[color:var(--color-nbu-bg)] transition hover:opacity-90"
          >
            Apply filters
          </button>
          <Link
            href="/admin/audit"
            className="rounded-md border border-[color:var(--color-nbu-border)] px-4 py-1.5 text-xs transition hover:border-[color:var(--color-nbu-text)]"
          >
            Clear
          </Link>
        </div>
      </form>

      {page.items.length === 0 ? (
        <div className="rounded-md border border-dashed border-[color:var(--color-nbu-border)] px-4 py-8 text-center text-sm text-[color:var(--color-nbu-text-muted)]">
          No audit rows match these filters.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-[color:var(--color-nbu-border)]">
          <table className="w-full min-w-[900px] text-left text-sm">
            <thead className="bg-[color:var(--color-nbu-surface)] text-xs uppercase tracking-wide text-[color:var(--color-nbu-text-muted)]">
              <tr>
                <th className="px-3 py-2">When (UTC)</th>
                <th className="px-3 py-2">Action</th>
                <th className="px-3 py-2">Actor</th>
                <th className="px-3 py-2">Team</th>
                <th className="px-3 py-2">Resource</th>
                <th className="px-3 py-2">Request</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((row) => (
                <tr
                  key={row.id}
                  className="border-t border-[color:var(--color-nbu-border)] align-top"
                >
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">
                    {row.created_at.replace("T", " ").slice(0, 19)}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{row.action}</td>
                  <td className="px-3 py-2">
                    <div className="font-mono text-xs">
                      {row.actor_email ?? "—"}
                    </div>
                    {row.actor_user_id && (
                      <div className="font-mono text-[10px] text-[color:var(--color-nbu-text-muted)]">
                        {row.actor_user_id}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">
                    {row.team_id ?? "—"}
                  </td>
                  <td className="px-3 py-2">
                    <div className="font-mono text-xs">
                      {row.resource_type ?? "—"}
                    </div>
                    {row.resource_id && (
                      <div className="font-mono text-[10px] text-[color:var(--color-nbu-text-muted)]">
                        {row.resource_id}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <div className="font-mono text-[10px] text-[color:var(--color-nbu-text-muted)]">
                      {row.request_id ?? "—"}
                    </div>
                    {row.ip_address && (
                      <div className="font-mono text-[10px] text-[color:var(--color-nbu-text-muted)]">
                        ip: {row.ip_address}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <nav
        aria-label="Pagination"
        className="flex items-center justify-between text-sm"
      >
        {params.cursor ? (
          <Link
            href={`/admin/audit?${buildQuery({ cursor: undefined })}`}
            className="rounded-md border border-[color:var(--color-nbu-border)] px-3 py-1.5 text-sm transition hover:border-[color:var(--color-nbu-text)]"
          >
            ← First page
          </Link>
        ) : (
          <span />
        )}
        {page.next_cursor ? (
          <Link
            href={`/admin/audit?${buildQuery({ cursor: page.next_cursor })}`}
            className="rounded-md border border-[color:var(--color-nbu-border)] px-3 py-1.5 text-sm transition hover:border-[color:var(--color-nbu-text)]"
          >
            Next →
          </Link>
        ) : (
          <span />
        )}
      </nav>
    </section>
  );
}
