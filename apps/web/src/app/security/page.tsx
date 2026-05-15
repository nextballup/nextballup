import type { Metadata } from "next";
import { MarketingHeader } from "@/components/marketing/header";
import {
  MarketingFooter,
  PilotCallToAction,
  SecuritySection,
} from "@/components/marketing/sections";

export const metadata: Metadata = {
  title: "Security & privacy",
  description:
    "Restricted access, private storage, coach review required, audit on every change, no public athlete exposure. NextBallUp's defaults for school and club film.",
  alternates: { canonical: "/security" },
};

export default function SecurityPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <MarketingHeader />
      <main id="main" className="flex-1">
        <SecuritySection />
        <PilotCallToAction />
      </main>
      <MarketingFooter />
    </div>
  );
}
