import { headers } from "next/headers";

/**
 * Runs synchronously in <head> before first paint. Reads the saved theme
 * preference (or falls back to system) and stamps it on <html> so the page
 * never flashes the wrong palette during hydration. The script is small,
 * has no dependencies, and is carries the per-request CSP nonce so it
 * remains executable under the strict CSP set by middleware.
 */
const SCRIPT = `
(function () {
  try {
    var stored = window.localStorage.getItem("nbu-theme");
    var theme = stored === "light" || stored === "dark" ? stored : null;
    if (theme) {
      document.documentElement.setAttribute("data-theme", theme);
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
  } catch (_) {
    /* localStorage may be blocked (private mode, embedded contexts);
       falling through means the CSS prefers-color-scheme default applies. */
  }
})();
`;

export async function ThemeScript() {
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <script
      nonce={nonce}
      // eslint-disable-next-line react/no-danger -- trusted static string, CSP-nonced.
      dangerouslySetInnerHTML={{ __html: SCRIPT }}
    />
  );
}
