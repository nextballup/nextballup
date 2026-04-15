import "@testing-library/jest-dom/vitest";
import { beforeAll, afterEach, afterAll } from "vitest";
import { setupServer } from "msw/node";

export const server = setupServer();

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});

// jsdom doesn't implement MediaSource; the HLS branch imports hls.js which
// expects window, but our tests only exercise the mp4 (native <video>) path.
// A narrow shim keeps the import graph happy without pulling in browser APIs.
if (typeof window !== "undefined" && !(window as Window & { MediaSource?: unknown }).MediaSource) {
  (window as Window & { MediaSource?: unknown }).MediaSource = class {
    static isTypeSupported() {
      return false;
    }
  };
}

// Also stub HTMLMediaElement methods that jsdom leaves as no-ops.
Object.defineProperty(HTMLMediaElement.prototype, "canPlayType", {
  configurable: true,
  value: () => "",
});
