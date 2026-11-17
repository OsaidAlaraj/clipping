/** The API runs on the Python backend. The app proxies /api to it, so the phone only needs one address. */
const api = process.env.CLIPPING_API_URL || "http://127.0.0.1:8787";

export default {
  reactStrictMode: true,
  // The phone reaches the laptop through Tailscale (https://<laptop>.<tailnet>.ts.net).
  allowedDevOrigins: ["*.ts.net"],
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/sw.js", headers: [{ key: "Cache-Control", value: "no-cache" }, { key: "Service-Worker-Allowed", value: "/" }] }];
  },
};
