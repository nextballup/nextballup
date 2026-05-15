import type { Metadata } from "next";
import { MarketingHeader } from "@/components/marketing/header";
import {
  MarketingFooter,
  PilotCallToAction,
  UseCasesSection,
} from "@/components/marketing/sections";

export const metadata: Metadata = {
  title: "Use cases",
  description:
    "Who NextBallUp is built for: high-school programs, club teams, skills trainers, and small coaching staffs. Honest scope — pilots only, no logo wall.",
  alternates: { canonical: "/use-cases" },
};

export default function UseCasesPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <MarketingHeader />
      <main id="main" className="flex-1">
        <UseCasesSection />
        <PilotCallToAction />
      </main>
      <MarketingFooter />
    </div>
  );
}
