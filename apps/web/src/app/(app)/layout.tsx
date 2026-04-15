import { redirect } from "next/navigation";
import { serverApiOptional } from "@/lib/api-server";
import type { UserPublic } from "@/lib/contract";
import { AppShell } from "./app-shell";

export default async function AppLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // Session bootstrap: if `/auth/me` can't validate the httpOnly cookie, send
  // the user to /login before they ever see the authenticated UI. This also
  // means we never render partial server components that would downstream
  // call the API and get 401s.
  const user = await serverApiOptional<UserPublic>("/auth/me");
  if (!user) {
    redirect("/login");
  }
  return <AppShell user={user}>{children}</AppShell>;
}
