import type { Metadata, Viewport } from "next";

import Shell from "@/components/Shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "Clipping Review",
  description: "Review, queue and track clips for paid clipping campaigns.",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "Clipping", statusBarStyle: "black-translucent" },
  icons: { icon: "/icon-192.png", apple: "/apple-touch-icon.png" },
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
