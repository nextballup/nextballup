import type { Metadata } from "next";
import { ThemeScript } from "@/components/theme-script";
import { AppProviders } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://nextballup.com"),
  title: {
    default: "NextBallUp — AI-assisted basketball film review for coaches",
    template: "%s · NextBallUp",
  },
  description:
    "Upload game film, get browser playback, and review alpha detector candidates before they ever turn into stats. Built for coaches; pilot access only.",
  openGraph: {
    title: "NextBallUp — AI-assisted basketball film review for coaches",
    description:
      "Upload film, review alpha detector candidates, and confirm what counts. Pilot access only — not production analytics.",
    url: "https://nextballup.com",
    siteName: "NextBallUp",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "NextBallUp",
    description:
      "AI-assisted basketball film review for coaches. Pilot access only.",
  },
  robots: { index: true, follow: true },
};

// The CSP middleware emits a per-request script nonce. Static prerendered HTML
// cannot receive that nonce, so force dynamic rendering to keep hydration
// scripts aligned with the response CSP.
export const dynamic = "force-dynamic";

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head>
        <ThemeScript />
      </head>
      <body className="min-h-screen antialiased">
        <AppProviders>{children}</AppProviders>
      </body>
    </html>
  );
}
