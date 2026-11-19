/** The API runs on the Python backend. The app proxies /api to it, so the phone only needs one address. */
const api = process.env.CLIPPING_API_URL || "http://127.0.0.1:8787";

export default {
  // A second build can live beside the dev one (for measuring a production build without disturbing the running dev server).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  reactStrictMode: true,
  // The dev-only "N Issue" badge covered the Home icon on the phone. Problems still show in the console.
  devIndicators: false,
  // The phone reaches the laptop through Tailscale (https://<laptop>.<tailnet>.ts.net).
  allowedDevOrigins: ["*.ts.net", "*.trycloudflare.com"],
  // Phone uploads are proxied through /api when the app is opened through Tailscale or a tunnel.
  experimental: { proxyClientMaxBodySize: "2gb" },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/sw.js", headers: [{ key: "Cache-Control", value: "no-cache" }, { key: "Service-Worker-Allowed", value: "/" }] }];
  },
};
