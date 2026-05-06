const EMAIL_VERIFICATION_RETRY_STORAGE_KEY_PREFIX =
  "nbu_email_verification_retry_needed";

export function emailVerificationRetryStorageKey(email: string): string {
  return `${EMAIL_VERIFICATION_RETRY_STORAGE_KEY_PREFIX}:${email.trim().toLowerCase()}`;
}

export function markEmailVerificationRetryNeeded(email: string): void {
  try {
    window.sessionStorage.setItem(emailVerificationRetryStorageKey(email), "1");
  } catch {
    // Storage can be disabled in private or hardened browser contexts. The
    // banner still works without this extra registration-time hint.
  }
}

export function clearEmailVerificationRetryNeeded(email: string): void {
  try {
    window.sessionStorage.removeItem(emailVerificationRetryStorageKey(email));
  } catch {
    // Best-effort UX state only.
  }
}

export function emailVerificationRetryNeeded(email: string): boolean {
  try {
    return window.sessionStorage.getItem(emailVerificationRetryStorageKey(email)) === "1";
  } catch {
    return false;
  }
}
