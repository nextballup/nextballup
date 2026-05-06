import type { UserPublic, UserRole } from "@/lib/contract";

/**
 * Auth responses are intentionally token-free: the backend delivers the
 * access + refresh tokens exclusively through httpOnly cookies, so the
 * browser never needs them echoed back. These types mirror the pruned
 * server-side shapes in `packages/core/src/nextballup_core/schemas/auth.py`.
 */
export type LoginResponse = {
  user: UserPublic;
};

export type RegisterResponse = {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  created_at: string;
};

export type RefreshResponse = {
  refreshed_at: string;
};

export type PasswordResetRequestResponse = {
  requested_at: string;
  delivery: string;
};

export type PasswordResetConfirmResponse = {
  reset_at: string;
};

export type RequestEmailVerificationResponse = {
  requested_at: string;
  expires_at: string;
  delivery: string;
};

export type ConfirmEmailVerificationResponse = {
  confirmed_at: string;
  is_verified: boolean;
};

export type EmailVerificationStatusResponse = {
  is_verified: boolean;
  pending_request: boolean;
  last_requested_at: string | null;
  last_confirmed_at: string | null;
};

export type RegistrationMode = "open" | "invite_only" | "allowlist" | "disabled";

export type RegistrationStatusResponse = {
  mode: RegistrationMode;
  invite_code_required: boolean;
  is_open_to_public: boolean;
};
