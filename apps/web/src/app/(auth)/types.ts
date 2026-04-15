import type { UserPublic } from "@/lib/contract";

export type LoginResponse = {
  access_token: string;
  refresh_token: string;
  user: UserPublic;
};

export type RegisterResponse = {
  id: string;
  email: string;
  full_name: string;
  role: "coach" | "player" | "admin";
  created_at: string;
  access_token: string;
  refresh_token: string;
};
