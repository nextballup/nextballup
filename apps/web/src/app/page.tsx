import { MarketingHeader } from "@/components/marketing/header";
import {
  FaqSection,
  HeroSection,
  MarketingFooter,
  SecuritySection,
  UseCasesSection,
  WorkflowSection,
} from "@/components/marketing/sections";

export default function LandingPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-[color:var(--color-nbu-text)] focus:px-3 focus:py-1.5 focus:text-sm focus:text-[color:var(--color-nbu-bg)]"
      >
        Skip to content
      </a>
      <MarketingHeader />
      <main id="main" className="flex-1">
        <HeroSection />
        <WorkflowSection />
        <UseCasesSection />
        <SecuritySection />
        <FaqSection />
      </main>
      <MarketingFooter />
    </div>
  );
}
