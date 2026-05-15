import type { Metadata } from "next";
import { MarketingHeader } from "@/components/marketing/header";
import {
  FaqSection,
  MarketingFooter,
  PilotCallToAction,
} from "@/components/marketing/sections";

export const metadata: Metadata = {
  title: "FAQ",
  description:
    "Straight answers about the NextBallUp alpha — what the detector does, where film goes, why pilots are invite-only, and how we handle athlete privacy.",
  alternates: { canonical: "/faq" },
};

export default function FaqPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <MarketingHeader />
      <main id="main" className="flex-1">
        <FaqSection />
        <PilotCallToAction />
      </main>
      <MarketingFooter />
    </div>
  );
}
