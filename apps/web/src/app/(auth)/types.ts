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
