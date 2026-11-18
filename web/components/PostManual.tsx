"use client";

// Posting by hand: get the video onto the phone (share sheet) or the computer (download), copy the caption and hashtags,
// then paste the public link back. Every step has a fallback so a blocked clipboard or a browser without file sharing never strands the owner.

import { useEffect, useRef, useState } from "react";

import { api, ApiError, Clip, NetworkError, PostTarget } from "@/lib/api";
import { showToast } from "@/lib/toast";
import { usePostText } from "./PostText";

/** Put text on the clipboard. False when the browser refuses (insecure page, permission denied) or has no clipboard. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (!navigator.clipboard?.writeText) return false;
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export type ShareResult = "shared" | "downloaded" | "dismissed" | "failed";

/** A phone-like device (touch first) whose browser can share files. A desktop browser may also have the Web Share API, but the owner
 *  there wants the file in Downloads, so it is only used on a touch device. */
export function canShareFiles(): boolean {
  return typeof navigator !== "undefined" && typeof navigator.canShare === "function" && typeof navigator.share === "function"
    && !!window.matchMedia?.("(pointer: coarse)").matches;
}

/** Offer the video to the share sheet (so it lands in TikTok, Instagram or YouTube) when the device can share files;
 *  otherwise download it. The video is fetched only once file sharing is known to exist, so a desktop never pulls it for nothing. */
export async function shareOrDownload(videoUrl: string, caption: string, name: string): Promise<ShareResult> {
  if (canShareFiles()) {
    try {
      const response = await fetch(videoUrl);
      if (!response.ok) throw new Error("video not available");
      const blob = await response.blob();
      const file = new File([blob], name, { type: blob.type.startsWith("video/") ? blob.type : "video/mp4" });
      if (navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], text: caption });
        return "shared";
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return "dismissed";   // the owner closed the share sheet
      // anything else: fall through to the download
    }
  }
  const link = document.createElement("a");
  link.href = videoUrl;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  return "downloaded";
}

function CopyButton({ text, label, empty }: { text: string; label: string; empty?: string }) {
  const { t } = usePostText();
  const [blocked, setBlocked] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { area.current?.select(); }, [blocked]);
  return (
    <>
      <button type="button" className="btn" disabled={!text} title={!text ? empty : undefined}
        onClick={async () => { if (await copyText(text)) { setBlocked(false); showToast(t("copied")); } else setBlocked(true); }}>
        {label}
      </button>
      {blocked && (
        <div className="post-copy field">
          <span className="small muted">{t("copyBlocked")}</span>
          <textarea ref={area} className="input" readOnly dir="auto" value={text} aria-label={label} onFocus={(event) => event.currentTarget.select()} />
        </div>
      )}
    </>
  );
}

/** Link to the public post, checked here for the obvious mistakes and by the server for the platform's own address. */
function LinkField({ target, onLinked }: { target: PostTarget; onLinked: (target: PostTarget) => void }) {
  const { t, f, platform } = usePostText();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const id = `post-link-${target.id}`;

  const save = async () => {
    if (!/^https?:\/\/\S+$/i.test(url.trim())) return setError(t("linkBad"));
    setError("");
    setBusy(true);
    try {
      const done = await api<PostTarget>(`/api/targets/${target.id}/link`, { body: { url: url.trim() } });
      showToast(t("linkSaved"));
      onLinked(done);
    } catch (e) {
      setError(e instanceof ApiError ? (e.code === "link_required" ? f("linkWrongHost", { platform: platform(target.platform) }) : e.message)
        : e instanceof NetworkError ? t("noConnection") : t("linkBad"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="field">
      <label htmlFor={id}>{t("linkLabel")}</label>
      <div className="post-link-row">
        <input id={id} className="input" inputMode="url" autoComplete="off" spellCheck={false} dir="ltr" placeholder="https://" value={url} disabled={busy} aria-invalid={!!error}
          onChange={(event) => { setUrl(event.target.value); setError(""); }} />
        <button type="button" className="btn primary" disabled={busy || !url.trim()} onClick={save}>{busy ? t("savingLink") : t("saveLink")}</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}

/** The hand-posting tools for one target. `withFile` adds Share or Download (a target the worker will not send); a draft or a private
 *  upload only needs the caption and the link. */
export function PostManual({ clip, target, withFile, onLinked }: { clip: Clip; target: PostTarget; withFile: boolean; onLinked: (target: PostTarget) => void }) {
  const { t, rtl } = usePostText();
  const [busy, setBusy] = useState(false);
  const canShare = canShareFiles();
  const hashtags = (clip.hashtags ?? []).join(" ");

  const send = async () => {
    if (!clip.video_url) return;
    setBusy(true);
    try {
      const result = await shareOrDownload(clip.video_url, clip.caption_text ?? "", `clip-${clip.id}.mp4`);
      if (result === "downloaded") showToast(t(canShare ? "shareFailed" : "downloadStarted"), { tone: canShare ? "error" : "info" });   // sharing was possible but failed
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack" dir={rtl ? "rtl" : "ltr"}>
      <div className="post-actions">
        {withFile && clip.video_url && <button type="button" className="btn dark" disabled={busy} onClick={send}>{canShare ? t("share") : t("download")}</button>}
        <CopyButton text={clip.caption_text ?? ""} label={t("copyCaption")} />
        <CopyButton text={hashtags} label={t("copyHashtags")} empty={t("noHashtags")} />
      </div>
      <LinkField target={target} onLinked={onLinked} />
    </div>
  );
}
