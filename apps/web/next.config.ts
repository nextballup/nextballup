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
const apiUpstream = process.env.API_UPSTREAM_URL ?? "http://localhost:8000";
const isDev = process.env.NODE_ENV !== "production";
const securityHeaders = [
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      "base-uri 'self'",
      "font-src 'self' data: https:",
      "form-action 'self'",
      "frame-ancestors 'none'",
      "img-src 'self' data: blob: https:",
      "media-src 'self' blob: https:",
      "object-src 'none'",
      `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
      "style-src 'self' 'unsafe-inline'",
      `connect-src 'self' https:${isDev ? " ws: wss:" : ""}`,
      "worker-src 'self' blob:",
    ].join("; "),
  },
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
