"use client";

import { useEffect, useState } from "react";

export function useRetryAfterGate() {
  const [retryUntilMs, setRetryUntilMs] = useState<number | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const remainingMs = retryUntilMs === null ? 0 : Math.max(0, retryUntilMs - nowMs);
  const retryAfterSeconds = Math.ceil(remainingMs / 1000);

  useEffect(() => {
    if (retryUntilMs === null) return;
    setNowMs(Date.now());
    const handle = window.setInterval(() => {
      setNowMs(Date.now());
    }, 250);
    return () => window.clearInterval(handle);
  }, [retryUntilMs]);

  useEffect(() => {
    if (retryUntilMs !== null && remainingMs === 0) {
      setRetryUntilMs(null);
    }
  }, [remainingMs, retryUntilMs]);

  return {
    retryAfterSeconds,
    retryBlocked: retryAfterSeconds > 0,
    startRetryAfter: (retryAfterMs: number | undefined) => {
      if (retryAfterMs !== undefined && retryAfterMs > 0) {
        const nextNow = Date.now();
        setNowMs(nextNow);
        setRetryUntilMs(nextNow + retryAfterMs);
      }
    },
  };
}
