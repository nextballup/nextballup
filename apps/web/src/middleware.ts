import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Per-request CSP nonce. Next.js auto-propagates the `x-nonce` request header
 * to the inline <script> tags it injects during streaming SSR. We still keep
 * `'self'` as an explicit script source because `strict-dynamic` causes
 * modern browsers to ignore host allow-lists, which can block framework
 * chunks that Next serves from `/_next/static/*` without a nonce.
 *
 * We keep `'unsafe-inline'` in `style-src` intentionally: Tailwind's runtime
 * CSS injection and third-party libraries (hls.js player chrome) rely on it,
 * and `style-src` XSS is far less catastrophic than `script-src` XSS —
 * taking it to a nonce-only model would be a multi-day refactor for low
 * marginal gain.
 */
function base64Nonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

const isDev = process.env.NODE_ENV !== "production";
const devLocalStorageOrigins = isDev
  ? ["http://127.0.0.1:*", "http://localhost:*"]
  : [];

export function middleware(request: NextRequest) {
  const nonce = base64Nonce();
  const cspReportUrl = new URL("/api/v1/_csp-report", request.url).toString();
  const csp = [
    "default-src 'self'",
    "base-uri 'self'",
    "font-src 'self' data: https:",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "img-src 'self' data: blob: https:",
    `media-src 'self' blob: https:${devLocalStorageOrigins.length ? ` ${devLocalStorageOrigins.join(" ")}` : ""}`,
    "object-src 'none'",
    // Keep `_next/static/*` chunks executable via `'self'` while preserving
    // nonces for framework inline scripts. Avoid `strict-dynamic` here: it
    // makes browsers ignore `'self'`, which breaks hydration when an external
    // framework chunk lacks a nonce.
    `script-src 'self' 'nonce-${nonce}' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    `connect-src 'self' https:${isDev ? ` ws: wss: ${devLocalStorageOrigins.join(" ")}` : ""}`,
    "worker-src 'self' blob:",
    "report-uri /api/v1/_csp-report",
    "report-to csp-endpoint",
  ].join("; ");

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("content-security-policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  response.headers.set(
    "Report-To",
    JSON.stringify({
      group: "csp-endpoint",
      max_age: 10886400,
      endpoints: [{ url: cspReportUrl }],
    }),
  );
  return response;
}

// Skip CSP for static assets — adds overhead with no security win, and
// Next's image/optimization pipelines don't stream through middleware cleanly.
export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|api/v1).*)",
  ],
};
