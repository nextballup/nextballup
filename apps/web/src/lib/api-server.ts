import { cookies } from "next/headers";
import { ApiError, toApiError } from "./errors";

/**
 * Server-side API helper used by server components during SSR. The browser's
 * cookies are read via Next.js's `cookies()` helper and forwarded verbatim
 * to the backend so `/auth/me` and other authenticated routes see the same
 * session the browser sees. We talk to the backend over `API_UPSTREAM_URL`
 * directly (bypassing the Next.js rewrite) because the rewrite only runs for
 * browser-originated requests.
 *
 * Auth-cookie forwarding is explicit: we never echo arbitrary headers from
 * the incoming request, so a compromised user can't influence upstream
 * behavior beyond what their session already allows.
 */
const UPSTREAM = process.env.API_UPSTREAM_URL ?? "http://localhost:8000";
const API_PATH = "/api/v1";
type ServerApiOptionalInit = RequestInit & {
  nullOnStatuses?: ReadonlyArray<number>;
};

async function buildCookieHeader(): Promise<string | undefined> {
  const store = await cookies();
  const parts: string[] = [];
  for (const cookie of store.getAll()) {
    // Forward our auth/CSRF cookies in both the raw and __Host-prefixed form
    // so deployments that toggle the prefix flag don't drop the SSR session.
    if (cookie.name.startsWith("nbu_") || cookie.name.startsWith("__Host-nbu_")) {
      parts.push(`${cookie.name}=${cookie.value}`);
    }
  }
  return parts.length > 0 ? parts.join("; ") : undefined;
}

export async function serverApiJson<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const cookieHeader = await buildCookieHeader();
  const headers = new Headers(init.headers);
  if (cookieHeader) {
    headers.set("Cookie", cookieHeader);
  }
  const response = await fetch(`${UPSTREAM}${API_PATH}${path}`, {
    ...init,
    headers,
    // Server-to-server; we don't want Next.js caching authenticated data.
    cache: "no-store",
  });
  if (!response.ok) {
    throw await toApiError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export async function serverApiOptional<T>(
  path: string,
  init: ServerApiOptionalInit = {},
): Promise<T | null> {
  const { nullOnStatuses = [], ...requestInit } = init;
  const effectiveNullStatuses = [...new Set([401, 404, ...nullOnStatuses])];
  try {
    return await serverApiJson<T>(path, requestInit);
  } catch (error) {
    if (error instanceof ApiError && effectiveNullStatuses.includes(error.status)) {
      return null;
    }
    throw error;
  }
}

export { ApiError };
