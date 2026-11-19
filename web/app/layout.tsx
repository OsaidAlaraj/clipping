import type { Metadata, Viewport } from "next";

import Shell from "@/components/Shell";
import "./globals.css";

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
    <html lang="en" dir="ltr">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
