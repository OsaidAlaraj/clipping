"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError, money, PLATFORM_LABEL } from "@/lib/api";
import { useData } from "@/lib/data";
import { useLang } from "@/lib/i18n";
import { setSoundOn, useGesture, useSound } from "@/lib/sound";
import { clock } from "@/lib/transcript";
import { Head } from "./More";
import { ListSkeleton } from "./Skeleton";

// Owner note 13: the accepted clips as a scroll of reels, with the numbers collected for each (instead of likes and comments).
// Grouped and filtered by campaign name. The filtering and sorting happen on the server (GET /api/library).

export type LibraryClip = {
  id: string; campaign_id: string; campaign_name: string; hook_text: string | null; platform: string | null; status: string;
  updated_at: string; duration_s: number | null; post_url: string | null; posted_at: string | null;
  views_h48: number | null; views_d7: number | null; watch_time_s: number | null; net_usd: number | null; payout_status: string | null;
  video_url: string | null; cover_url: string | null;
};
type LibraryData = { clips: LibraryClip[]; campaigns: { id: string; name: string; count: number }[]; total: number; sort: string };

const SORTS = ["newest", "views", "earned", "campaign"] as const;
type Sort = (typeof SORTS)[number];

const count = (value: number | null | undefined) => (value === null || value === undefined ? "—" : Number(value).toLocaleString());

/** One reel: plays while it is on screen, with the sound choice shared by every player. */
function Reel({ clip, label }: { clip: LibraryClip; label: { mute: string; soundOn: string; openPost: string } }) {
  const video = useRef<HTMLVideoElement>(null);
  const soundOn = useSound();
  const gesture = useGesture();
  const [blocked, setBlocked] = useState(false);
  const [visible, setVisible] = useState(false);
  const audible = soundOn && !blocked;

  // Play while at least 60% of the reel is on screen; pause when it scrolls away.
  useEffect(() => {
    const frame = video.current?.closest(".reel");
    if (!frame || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.intersectionRatio >= 0.6), { threshold: [0, 0.6] });
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const el = video.current;
    if (!el || !clip.video_url) return;
    if (!visible) { el.pause(); return; }
    el.muted = !soundOn;
    setBlocked(false);
    el.play()?.catch?.(() => {
      // The browser refused sound before the first touch: play muted and show the tap.
      el.muted = true;
      setBlocked(true);
      el.play()?.catch?.(() => undefined);
    });
  }, [visible, soundOn, gesture, clip.video_url]);

  return (
    <article className="reel" aria-label={clip.campaign_name}>
      <div className="reel-head">
        <strong dir="auto">{clip.campaign_name}</strong>
        <span className="small muted">{clip.platform ? PLATFORM_LABEL[clip.platform] ?? clip.platform : "No platform"}{clip.posted_at ? ` · ${new Date(clip.posted_at).toLocaleDateString()}` : ""}</span>
        {clip.hook_text && <span className="small" dir="auto">{clip.hook_text}</span>}
      </div>
      {clip.video_url ? (
        <div className="reel-media">
          <video ref={video} src={clip.video_url} poster={clip.cover_url ?? undefined} muted={!audible} loop playsInline preload="metadata" />
          <button className="chip reel-sound" aria-pressed={audible} onClick={() => setSoundOn(!audible)}>{audible ? label.mute : label.soundOn}</button>
        </div>
      ) : (
        <div className="reel-media reel-empty"><span className="small muted">No video file</span></div>
      )}
      <dl className="reel-stats">
        <div><dt className="small muted">Views, 48 h</dt><dd className="num">{count(clip.views_h48)}</dd></div>
        <div><dt className="small muted">Views, 7 days</dt><dd className="num">{count(clip.views_d7)}</dd></div>
        <div><dt className="small muted">Watch time</dt><dd className="num">{clip.watch_time_s ? clock(Number(clip.watch_time_s)) : "—"}</dd></div>
        <div><dt className="small muted">Earned</dt><dd className="num">{clip.net_usd !== null && clip.net_usd !== undefined ? money(clip.net_usd) : "—"}</dd></div>
      </dl>
      <div className="reel-foot">
        <span className="badge">{clip.payout_status ?? clip.status.replaceAll("_", " ")}</span>
        {clip.post_url && <a className="small" href={clip.post_url} target="_blank" rel="noreferrer">{label.openPost}</a>}
      </div>
    </article>
  );
}

export default function Library() {
  const { t } = useLang();
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [sort, setSort] = useState<Sort>("newest");

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(q), 250); // typing does not send a request per letter
    return () => clearTimeout(timer);
  }, [q]);

  const params = new URLSearchParams({ sort });
  if (debounced.trim()) params.set("q", debounced.trim());
  if (picked.length) params.set("campaign_ids", picked.join(","));
  const { data, error } = useData<LibraryData>(`/api/library?${params.toString()}`);

  const labels = { mute: t("mute"), soundOn: t("soundOn"), openPost: t("openPost") };
  const toggle = (id: string) => setPicked((list) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]));
  const message = error instanceof ApiError ? error.message : error ? "No connection. Try again when you are online." : "";

  return (
    <div className="page library">
      <Head title={t("libraryTitle")} back="/profile" />
      <div className="lib-controls stack">
        <input type="search" className="input" aria-label={t("searchClips")} placeholder={t("searchClips")} value={q} dir="auto"
          onChange={(event) => setQ(event.target.value)} />
        <div className="row between wrap">
          <label className="small muted">
            {t("sortBy")}{" "}
            <select className="input" aria-label={t("sortBy")} value={sort} onChange={(event) => setSort(event.target.value as Sort)}>
              <option value="newest">{t("sortNewest")}</option>
              <option value="views">{t("sortViews")}</option>
              <option value="earned">{t("sortEarned")}</option>
              <option value="campaign">{t("sortCampaign")}</option>
            </select>
          </label>
          {data && <span className="small muted">{data.clips.length} / {data.total}</span>}
        </div>
        <div className="lib-chips" role="group" aria-label={t("campaigns")}>
          <button className="chip" aria-pressed={picked.length === 0} onClick={() => setPicked([])}>{t("allCampaigns")}</button>
          {(data?.campaigns ?? []).map((campaign) => (
            <button key={campaign.id} className="chip" aria-pressed={picked.includes(campaign.id)} onClick={() => toggle(campaign.id)}>
              <span dir="auto">{campaign.name}</span> <span className="muted">{campaign.count}</span>
            </button>
          ))}
        </div>
      </div>
      {message && <p className="error" role="alert">{message}</p>}
      {!data && !message && <ListSkeleton title={t("libraryTitle")} />}
      {data && data.clips.length === 0 && (
        <div className="empty"><h1>{data.total === 0 ? t("libraryEmpty") : t("libraryNoMatch")}</h1></div>
      )}
      <div className="reel-list">
        {data?.clips.map((clip) => <Reel key={clip.id} clip={clip} label={labels} />)}
      </div>
    </div>
  );
}
