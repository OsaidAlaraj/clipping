/** The API runs on the Python backend. The app proxies /api to it, so the phone only needs one address. */
const api = process.env.CLIPPING_API_URL || "http://127.0.0.1:8787";

export default {
  // A second build can live beside the dev one (for measuring a production build without disturbing the running dev server).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  reactStrictMode: true,
  // The phone reaches the laptop through Tailscale (https://<laptop>.<tailnet>.ts.net).
  allowedDevOrigins: ["*.ts.net", "*.trycloudflare.com"],
  // Phone uploads are proxied through /api when the app is opened through Tailscale or a tunnel.
  experimental: { proxyClientMaxBodySize: "2gb" },
  // Old addresses keep working: one address per screen.
  async redirects() {
    return [
      { source: "/more", destination: "/profile", permanent: true },
      { source: "/more/add", destination: "/new", permanent: true },
      { source: "/more/alerts", destination: "/alerts", permanent: true },
      { source: "/more/ledger", destination: "/ledger", permanent: true },
      { source: "/more/settings", destination: "/settings", permanent: true },
      { source: "/more/review-later", destination: "/review-later", permanent: true },
      { source: "/feed", destination: "/review", permanent: true },
    ];
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/sw.js", headers: [{ key: "Cache-Control", value: "no-cache" }, { key: "Service-Worker-Allowed", value: "/" }] }];
  },
};
