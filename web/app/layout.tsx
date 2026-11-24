import type { Metadata, Viewport } from "next";

import { IBM_Plex_Sans_Arabic, Inter, Space_Grotesk } from "next/font/google";

import Shell from "@/components/Shell";
import "./globals.css";
import "./ui.css";

// Fonts are fetched once at build or dev start and served from the app itself; offline they fall back to the system font.
const inter = Inter({ subsets: ["latin"], variable: "--f-text", display: "swap" });
const grotesk = Space_Grotesk({ subsets: ["latin"], variable: "--f-display", display: "swap" });
const plex = IBM_Plex_Sans_Arabic({ subsets: ["arabic"], weight: ["400", "500", "600", "700"], variable: "--f-arabic", display: "swap" });

export const metadata: Metadata = {
  title: "ClipMind",
  description: "Review, queue and track clips for paid clipping campaigns.",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "ClipMind", statusBarStyle: "black-translucent" },
  icons: { icon: [{ url: "/logo-mark.svg", type: "image/svg+xml" }, { url: "/icon-192.png", sizes: "192x192" }], apple: "/apple-touch-icon.png" },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover", themeColor: "#0b0d10" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" dir="ltr" className={`${inter.variable} ${grotesk.variable} ${plex.variable}`} suppressHydrationWarning>
      {/* Browser extensions such as Grammarly add attributes to <body> before React loads; this hides only that attribute mismatch. */}
      <body suppressHydrationWarning>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
