import { redirect } from "next/navigation";

export default async function JoinByCodeRedirect({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const { code } = await params;
  redirect(`/teams/join?code=${encodeURIComponent(code)}`);
}
