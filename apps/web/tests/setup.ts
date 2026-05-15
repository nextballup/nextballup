import "@testing-library/jest-dom/vitest";
import { webcrypto } from "node:crypto";
import { beforeAll, afterEach, afterAll } from "vitest";
import { setupServer } from "msw/node";

export const server = setupServer();

// jsdom ships a partial WebCrypto (RNG only — no SubtleCrypto). Node has a
// full implementation; bind it so the upload flow's SHA-256 hashing path is
// actually covered by the suite rather than silently skipped. Both the
// jsdom `window.crypto` and the Node globalThis.crypto need the patch
// because resolution depends on which scope the caller uses.
for (const target of [globalThis, typeof window !== "undefined" ? window : null]) {
  if (!target) continue;
  if (typeof (target as { crypto?: Crypto }).crypto?.subtle?.digest === "function") {
    continue;
  }
  Object.defineProperty(target, "crypto", {
    value: webcrypto,
    configurable: true,
  });
}


// jsdom's Blob doesn't implement arrayBuffer() (and its Blob can't be fed
// through Response either, because Blob.stream() is also missing). Polyfill
// via FileReader, which jsdom does ship.
if (typeof Blob.prototype.arrayBuffer !== "function") {
  Object.defineProperty(Blob.prototype, "arrayBuffer", {
    configurable: true,
    value: function arrayBuffer(this: Blob): Promise<ArrayBuffer> {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as ArrayBuffer);
        reader.onerror = () => reject(reader.error);
        reader.readAsArrayBuffer(this);
      });
    },
  });
}

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
Object.defineProperty(HTMLMediaElement.prototype, "play", {
  configurable: true,
  value: () => Promise.resolve(),
});
Object.defineProperty(HTMLMediaElement.prototype, "pause", {
  configurable: true,
  value: () => undefined,
});
