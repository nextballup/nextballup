import { ApiError, toApiError } from "./errors";

const PUBLIC_API_BASE = "/api/v1";
const CSRF_HEADER = "X-CSRF-Token";
const CSRF_COOKIE_NAMES = ["__Host-nbu_csrf_token", "nbu_csrf_token"];
const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const CSRF_OPTIONAL_PATHS = new Set([
  "/auth/login",
  "/auth/register",
  "/auth/refresh",
  "/auth/password/forgot",
  "/auth/password/reset",
  "/auth/email/verify/confirm",
  // Marketing pilot-interest is unauthenticated; the public marketing site
  // does not share auth/CSRF cookies with the gated product host.
  "/pilot-interest",
]);

function readCsrfCookie(): string | null {
  if (typeof document === "undefined") {
    return null;
  }
  const raw = document.cookie;
  if (!raw) {
    return null;
  }
  for (const chunk of raw.split(";")) {
    const [rawName, ...rest] = chunk.trim().split("=");
    if (!rawName || rest.length === 0) continue;
    if (CSRF_COOKIE_NAMES.includes(rawName)) {
      const value = rest.join("=");
      if (value) return decodeURIComponent(value);
    }
  }
  return null;
}

/**
 * Browser-side API helper. Always uses `credentials: "include"` so httpOnly
 * auth cookies ride along, and always speaks same-origin (the Next.js
 * `rewrites` in next.config.ts proxies /api/v1/* to the upstream backend) so
 * the browser treats the request as first-party.
 *
 * Deliberately no public env override here: letting the browser talk straight
 * to an arbitrary API origin would break the audited same-origin cookie model
 * and re-open CORS / cross-site cookie failure modes we explicitly removed.
 *
 * We deliberately do not try to read or cache the JWT — the cookie is
 * httpOnly and the browser handles attachment for us. The only "state" this
 * module keeps is an in-flight refresh promise so 401s don't fan out into a
 * thundering herd of /auth/refresh calls.
 */
type RequestInitExtras = {
  json?: unknown;
  // Routes that should never trigger auto-refresh on 401 (login, refresh
  // itself, logout). These paths surface the 401 directly to the caller.
  noRefreshOn401?: boolean;
};

let refreshInFlight: Promise<boolean> | null = null;

async function doRefresh(): Promise<boolean> {
  try {
    // /auth/refresh is intentionally CSRF-exempt server-side (the refresh
    // cookie is HttpOnly and the call is idempotent enough to not need the
    // double-submit guard); no need to echo the CSRF header here.
    const response = await fetch(`${PUBLIC_API_BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    return response.ok;
  } catch {
    return false;
  }
}

function beginRefresh(): Promise<boolean> {
  if (refreshInFlight === null) {
    refreshInFlight = doRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

export async function apiFetch(
  path: string,
  init: RequestInit & RequestInitExtras = {},
): Promise<Response> {
  const { json, noRefreshOn401, headers, body, method, ...rest } = init;
  const baseHeaders = new Headers(headers);
  let finalBody = body;
  if (json !== undefined) {
    baseHeaders.set("Content-Type", "application/json");
    finalBody = JSON.stringify(json);
  }
  const resolvedMethod = (method ?? "GET").toUpperCase();
  const buildHeaders = () => {
    const requestHeaders = new Headers(baseHeaders);
    if (MUTATING_METHODS.has(resolvedMethod) && !requestHeaders.has(CSRF_HEADER)) {
      const csrf = readCsrfCookie();
      if (csrf) {
        requestHeaders.set(CSRF_HEADER, csrf);
      } else if (
        typeof console !== "undefined" &&
        !CSRF_OPTIONAL_PATHS.has(path)
      ) {
        console.warn(
          "CSRF cookie is missing for a mutating request; the backend will reject this request until the session is refreshed.",
        );
      }
    }
    return requestHeaders;
  };
  const doRequest = () =>
    fetch(`${PUBLIC_API_BASE}${path}`, {
      ...rest,
      method: resolvedMethod,
      headers: buildHeaders(),
      body: finalBody,
      credentials: "include",
    });

  let response = await doRequest();
  if (response.status === 401 && !noRefreshOn401) {
    const refreshed = await beginRefresh();
    if (refreshed) {
      response = await doRequest();
    }
  }
  return response;
}

export async function apiJson<T>(
  path: string,
  init: RequestInit & RequestInitExtras = {},
): Promise<T> {
  const response = await apiFetch(path, init);
  if (!response.ok) {
    throw await toApiError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export async function apiVoid(
  path: string,
  init: RequestInit & RequestInitExtras = {},
): Promise<void> {
  const response = await apiFetch(path, init);
  if (!response.ok) {
    throw await toApiError(response);
  }
}

export { ApiError };
