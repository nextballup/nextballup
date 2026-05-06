import type { NextConfig } from "next";

/**
 * Keeping the frontend same-origin with the backend solves two audited
 * guarantees at once:
 *   1. httpOnly auth cookies are never cross-site — no SameSite=Lax fetch
 *      gymnastics, so CSRF attack surface stays the same as any same-origin
 *      cookie-auth app.
 *   2. The browser never sees the backend's raw origin, so users can't paste
 *      internal URLs into shared links.
 *
 * `API_UPSTREAM_URL` lets deployments point the rewrite at whatever
 * service-discovery URL they use (Kubernetes Service, Docker Compose DNS,
 * etc.). Local default matches `docker compose up -d`.
 */
function resolveApiUpstream(): string {
  if (process.env.API_UPSTREAM_URL) return process.env.API_UPSTREAM_URL;
  if (process.env.API_UPSTREAM_HOSTPORT) {
    return `http://${process.env.API_UPSTREAM_HOSTPORT}`;
  }
  if (
    process.env.NODE_ENV === "production" &&
    process.env.ALLOW_LOCAL_API_UPSTREAM !== "true"
  ) {
    throw new Error(
      "API_UPSTREAM_URL or API_UPSTREAM_HOSTPORT must be set for production frontend builds.",
    );
  }
  return "http://localhost:8000";
}

const apiUpstream = resolveApiUpstream();
// CSP is emitted per-request by `src/middleware.ts` so it can include a
// cryptographically-random nonce that Next's runtime scripts pick up. The
// static headers below are everything CSP-adjacent that does NOT need
// per-request variance.
const securityHeaders = [
  {
    key: "Referrer-Policy",
    value: "strict-origin-when-cross-origin",
  },
  {
    key: "X-Content-Type-Options",
    value: "nosniff",
  },
  {
    key: "X-Frame-Options",
    value: "DENY",
  },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=()",
  },
  {
    key: "Cross-Origin-Opener-Policy",
    value: "same-origin",
  },
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  },
];

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${apiUpstream}/api/v1/:path*`,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },
  eslint: {
    // Lint only on explicit `pnpm lint`, never during `next build`. CI runs
    // lint and build as separate steps so a lint warning doesn't block
    // artifact creation.
    ignoreDuringBuilds: true,
  },
};

export default nextConfig;
