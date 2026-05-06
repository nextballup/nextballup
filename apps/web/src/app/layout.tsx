import type { Metadata } from "next";
import { AppProviders } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "NextBallUp",
  description: "Video archive and review platform for basketball teams.",
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
      <body className="min-h-screen antialiased">
        <AppProviders>{children}</AppProviders>
      </body>
    </html>
  );
}
