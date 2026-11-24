// A tiny fake of the ClipMind API for looking at the web app without the real backend or database.
// Run:  node scripts/mock-api.mjs 8791      then start the web app with CLIPPING_API_URL=http://127.0.0.1:8791
// It never touches the owner's API (8787). Data is fixed and invented.
import http from "node:http";

const port = Number(process.argv[2] || 8791);
const H = (h) => h * 1;
const campaigns = [
  { id: "c1", name: "Topps Summer Football", status: "approved", status_reason: null, marketplace: "content_rewards", niche: "sport", rate_per_1k: 1.2, fee_known: true, fee_pct: 10, est_net_rate_per_1k: 1.08, budget_left_pct: 62, hours_to_deadline: 140, compliance_status: "pass", platforms: ["tiktok", "instagram_reels"], clips_ready: 7, max_payout_per_clip: 150, unclear_fields: [], min_views_to_qualify: 1000, language: "en" },
  { id: "c2", name: "Reach Podcast Clips", status: "approved", status_reason: null, marketplace: "clipster", niche: "podcast", rate_per_1k: 0.8, fee_known: true, fee_pct: 12, est_net_rate_per_1k: 0.7, budget_left_pct: 18, hours_to_deadline: 30, compliance_status: "pass", platforms: ["youtube_shorts"], clips_ready: 0, max_payout_per_clip: null, unclear_fields: [], language: "ar" },
  { id: "c3", name: "Clipster Gaming Week", status: "review", status_reason: "Rules read, waiting for you", marketplace: "clipster", niche: "gaming", rate_per_1k: 1.5, fee_known: false, est_net_rate_per_1k: 1.3, budget_left_pct: null, hours_to_deadline: 300, compliance_status: "review", platforms: ["tiktok"], clips_ready: 0, unclear_fields: ["disclosure"], language: "en" },
];
const listing = (i, over = {}) => ({ id: `l${i}`, title: ["Football moments", "Podcast highlights", "Gaming montage", "Business tips", "Fitness shorts"][i % 5], brand: ["Topps", "Reach", "Vyro", "Evangelist", "Clipstake"][i % 5], board_slug: "content-rewards", agency: null, campaign_type: "clipping", pay_model: "cpm", platforms: ["tiktok", "instagram_reels"], niche: ["sport", "podcast", "gaming", "business", "fitness"][i % 5], rate_per_1k: 0.8 + i * 0.15, net_per_1k: 0.7 + i * 0.12, budget_left: 5000 - i * 300, budget_total: 8000, drain_p50: null, alpha_score: 70 - i, open_join: true, audience: null, board_fee_pct: 10, brief_grade: "B", new_at: null, campaign_id: null, page_url: "https://example.com", brief_url: null, min_payout: 10, max_payout: 200, payout_window_days: 14, rank: { modelled: true, score: 80 - i * 3, reasons: ["Fits your podcast niche", "Pays within 14 days"], account: { id: "a1", handle: "osaid.clips", platform: "tiktok" } }, ...over });
const net = { this_week: { with_pending_per_hour: 11.4 }, month_to_date: { with_pending_per_hour: 9.8, paid_usd: 214.5, hours: 21.5, fee_unknown: false }, confidence: "medium" };
const progress = { active: true, items: [{ id: "p1", source_id: "s1", campaign_id: "c1", name: "Match review ep 12", video: null, is_practice: false, state: "active", stage: "moments", clips_done: 0, clips_planned: 6, eta_s: 240, step_pct: 40, detail: "Choosing the best moments", error: null, note: null }] };
const state = { empty: false };
const routes = {
  "/api/health": () => ({ ok: true }),
  "/api/counts": () => ({ to_review: state.empty ? 0 : 7, alerts: 2 }),
  "/api/home": () => state.empty ? { stories: [], today: { hours_logged: 0, hours_cap: 4, clips_accepted: 0, posts_done: 0, posts_due: 0, to_review: 0 }, net_per_hour: { this_week: { with_pending_per_hour: null }, month_to_date: { with_pending_per_hour: null, paid_usd: 0, hours: 0 }, confidence: "low" }, attention: [], campaigns: [], kill_switch: { active: false } }
    : { stories: campaigns.filter((c) => c.status === "approved").map((c) => ({ id: c.id, name: c.name, budget_left_pct: c.budget_left_pct, hours_to_deadline: c.hours_to_deadline, clips_ready: c.clips_ready })), today: { hours_logged: 1.5, hours_cap: 4, clips_accepted: 3, posts_done: 2, posts_due: 1, to_review: 7 }, net_per_hour: net, attention: [{ id: "al1", severity: "warn", message: "TikTok connection expires in 3 days." }, { id: "al2", severity: "block", message: "Campaign Reach Podcast Clips ends in 30 hours." }], campaigns, kill_switch: { active: false } },
  "/api/progress": () => (state.empty ? { items: [], active: false } : progress),
  "/api/me": () => ({ niches: [], all_niches: ["sport", "podcast", "gaming", "business", "fitness"] }),
  "/api/listings": () => ({ listings: Array.from({ length: 12 }, (_, i) => listing(i)), market: {}, last_refresh: new Date().toISOString() }),
  "/api/reference": () => ({ adapters: [], accounts: [{ id: "a1", platform: "tiktok", handle: "osaid.clips", status: "active", status_note: null, niche: "podcast", language: "ar" }, { id: "a2", platform: "youtube_shorts", handle: "osaid.shorts", status: "active", status_note: null, niche: "podcast", language: "en" }], categories: [{ name: "podcast" }, { name: "sport" }, { name: "gaming" }], marketplaces: [{ slug: "content_rewards", name: "Content Rewards" }, { slug: "clipster", name: "Clipster" }] }),
  "/api/queue": () => ({ kill_switch: false, items: [] }),
  "/api/connections": () => ({ vault_ready: true, platforms: [{ platform: "tiktok", label: "TikTok" }, { platform: "youtube_shorts", label: "YouTube" }], connections: [] }),
  "/api/transcripts/accuracy": () => [],
  "/api/settings": () => ({ settings: [], kill_switch: { active: false, reason: null } }),
  "/api/alerts": () => ({ alerts: [{ id: "al1", severity: "warn", message: "TikTok connection expires in 3 days.", created_at: new Date().toISOString() }, { id: "al2", severity: "block", message: "Campaign Reach Podcast Clips ends in 30 hours.", created_at: new Date().toISOString() }] }),
  "/api/ledger": () => ({ net_per_hour: { confidence: "medium", clips_clearing_threshold: 4, clips_posted: 9, overall: { paid_per_hour: 8.2, with_pending_per_hour: 11.4, hours: 21.5, paid_usd: 214.5, pending_usd: 60 } }, weekly: { median_net_per_hour: 9.1, decision: "iterate" }, rows: [] }),
  "/api/feed": () => ({ clips: [], filtered: [] }),
  "/api/auth/status": () => ({ signed_in: true, enrolled: true }),
};
http.createServer((req, res) => {
  const url = new URL(req.url, "http://x");
  const path = url.pathname;
  if (path === "/__empty") { state.empty = url.searchParams.get("on") === "1"; res.end("ok"); return; }
  let body = null;
  if (routes[path]) body = routes[path]();
  else if (path.startsWith("/api/campaigns/") && req.method === "GET") body = { campaign: { ...campaigns[0], id: path.split("/")[3] }, version: { version_no: 2, compliance_status: "pass", hard_blocks: [], unclear_fields: [], fee_pct: 10, rules_json: { payout: { model: "cpm", rate_per_1k: 1.2, min_views_to_qualify: 1000, max_payout_per_clip: 150, budget_total: 5000, quotes: { rate_per_1k: "$1.20 per 1,000 qualified views" } }, platforms_allowed: ["tiktok", "instagram_reels"], music_policy: "licensed_only", disclosure: { required_by_brief: true }, content_rules: [{ text: "Show the product in the first 3 seconds", requirement: "must", quote: "The product must be visible within the first 3 seconds." }, { text: "No profanity", requirement: "must", quote: "Keep it family friendly." }] } }, sources: [{ id: "s1", language: "en", duration_s: 1860 }] };
  else if (path.startsWith("/api/")) body = {};
  res.setHeader("content-type", "application/json");
  res.end(JSON.stringify(body ?? {}));
}).listen(port, "127.0.0.1", () => console.log(`mock api on ${port}`));
