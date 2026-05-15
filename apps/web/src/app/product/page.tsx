import type { Metadata } from "next";
import { MarketingHeader } from "@/components/marketing/header";
import {
  MarketingFooter,
  PilotCallToAction,
  WorkflowSection,
} from "@/components/marketing/sections";

export const metadata: Metadata = {
  title: "Product",
  description:
    "How NextBallUp turns a coach's game film into a reviewable queue of alpha detector candidates. Upload, browser playback, candidate review, manual tags — five honest steps.",
  alternates: { canonical: "/product" },
};

export default function ProductPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <MarketingHeader />
      <main id="main" className="flex-1">
        <WorkflowSection />
        <PilotCallToAction />
      </main>
      <MarketingFooter />
    </div>
  );
}
