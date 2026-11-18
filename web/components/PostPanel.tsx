"use client";

// The posting screen inside the Queue sheet: pick platforms, see ONE combined checklist, confirm with ONE tap, then watch each platform.
// It answers at once (pending cards while the request is in flight), keeps what the owner ticked if the server refuses, and saves the tap
// on the device when there is no connection. Nothing is sent to a platform until the owner confirms here.

import { useEffect, useRef, useState } from "react";

import { api, ApiError, Clip, newActionId, NetworkError, OUTBOX_EVENT, PostOption, PostTarget, readOutbox, sendOrQueue } from "@/lib/api";
import { useData } from "@/lib/data";
import { showToast } from "@/lib/toast";
import { usePostText } from "./PostText";
import { TargetCard } from "./PostStatus";

const LIVE = ["scheduled", "sending", "processing"];   // states that still change by themselves, so the screen keeps asking
const ENDED = ["cancelled", "failed"];                  // states that no longer hold the platform

type Checks = Map<string, { ok: boolean; label: string }>;

/** The server checks of every chosen platform as one list: an item is done only when it is done for all of them. */
function combineServer(chosen: PostOption[]): Checks {
  const out: Checks = new Map();
  for (const option of chosen) {
    for (const [name, item] of Object.entries(option.checklist.server)) {
      const seen = out.get(name);
      out.set(name, { label: seen?.label ?? item.label, ok: (seen?.ok ?? true) && item.ok });
    }
  }
  return out;
}

export default function PostPanel({ clip, onChanged, onUnavailable, pollMs = 4000 }: {
  clip: Clip; onChanged: () => void; onUnavailable?: () => void; pollMs?: number;
}) {
  const { t, f, platform, tick, serverLabel, blocked, rtl } = usePostText();
  const options = useData<{ platforms: PostOption[] }>(`/api/clips/${clip.id}/post-options`);
  const status = useData<{ targets: PostTarget[] }>(`/api/clips/${clip.id}/post-status`);
  const [off, setOff] = useState<Record<string, boolean>>({});       // platforms the owner switched off (everything allowed starts on)
  const [ticks, setTicks] = useState<Record<string, boolean>>({});
  const [pending, setPending] = useState<string[] | null>(null);     // platforms of a request that is on its way
  const [queued, setQueued] = useState<{ id: string; platforms: string[] } | null>(null);   // a tap saved on the device (offline)
  const [error, setError] = useState("");

  const targets = status.data?.targets ?? [];
  const held = new Set([...targets.filter((x) => !ENDED.includes(x.state)).map((x) => x.platform)]);
  const platforms = options.data?.platforms ?? [];
  const pickable = platforms.filter((o) => !held.has(o.platform) && !o.existing_target);
  const chosen = pickable.filter((o) => o.allowed && !off[o.platform]);
  const server = combineServer(chosen);
  const tickNames = [...new Set(chosen.flatMap((o) => o.checklist.owner_ticks))];
  const warnings = [...new Set(chosen.flatMap((o) => o.warnings))];
  const serverOk = [...server.values()].every((item) => item.ok);
  const ticksOk = tickNames.every((name) => ticks[name]);
  const ready = chosen.length > 0 && serverOk && ticksOk && !pending && !queued;
  const briefStale = server.get("brief_rechecked")?.ok === false;

  useEffect(() => { if (options.error && !options.data) onUnavailable?.(); }, [options.error, options.data, onUnavailable]);

  // Live status: ask again while anything is on its way; stop once every target has settled.
  const active = targets.some((x) => LIVE.includes(x.state));
  const { reload: reloadStatus } = status;
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => { reloadStatus(); }, pollMs);
    return () => clearInterval(timer);
  }, [active, pollMs, reloadStatus]);

  // The Queue lists clips still to post, so it refreshes the moment one of this clip's posts goes public.
  const published = targets.filter((x) => x.state === "published").length;
  const seenPublished = useRef<number | null>(null);
  useEffect(() => {
    if (seenPublished.current !== null && published > seenPublished.current) onChanged();
    seenPublished.current = published;
  }, [published, onChanged]);

  // A tap saved offline is sent by the outbox when the connection is back; then this screen shows what the server made.
  const { reload: reloadOptions } = options;
  useEffect(() => {
    if (!queued) return;
    const check = () => {
      if (readOutbox().some((item) => item.id === queued.id)) return;
      setQueued(null);
      reloadStatus();
      reloadOptions();
      onChanged();
    };
    window.addEventListener(OUTBOX_EVENT, check);
    return () => window.removeEventListener(OUTBOX_EVENT, check);
  }, [queued, reloadStatus, reloadOptions, onChanged]);

  const replaceTarget = (done: PostTarget) => status.mutate((cur) => ({ targets: (cur?.targets ?? []).map((x) => (x.id === done.id ? done : x)) }));

  const confirm = async () => {
    if (!ready) return;
    const wanted = chosen.map((o) => o.platform);
    const requestId = newActionId();
    const body = { platforms: wanted, ticks: Object.fromEntries(wanted.map((p) => [p, Object.fromEntries(tickNames.map((n) => [n, true]))])), request_id: requestId };
    setError("");
    setPending(wanted);
    try {
      const result = await sendOrQueue<{ targets: PostTarget[] }>(`/api/clips/${clip.id}/post`, body, requestId);
      if ("queued" in result) {
        setQueued({ id: requestId, platforms: wanted });
      } else {
        status.mutate((cur) => ({ targets: [...(cur?.targets ?? []).filter((x) => !result.targets.some((n) => n.id === x.id)), ...result.targets] }));
        setTicks({});
        reloadOptions();
        reloadStatus();
        onChanged();
      }
    } catch (e) {
      setError(`${t("refused")} ${e instanceof ApiError ? e.message : ""}`.trim());
    } finally {
      setPending(null);
    }
  };

  const cancel = async (target: PostTarget) => {
    const before = status.data;
    status.mutate((cur) => ({ targets: (cur?.targets ?? []).map((x) => (x.id === target.id ? { ...x, state: "cancelled", reason: t("youCancelled") } : x)) }));
    try {
      await api(`/api/targets/${target.id}/cancel`, { body: {} });
      showToast(t("cancelledToast"));
      reloadOptions();
      onChanged();
    } catch (e) {
      if (before) status.mutate(() => before);          // it was already on its way: show it as it is
      showToast(f("cancelFailed", { why: e instanceof ApiError ? e.message : t("noConnection") }), { tone: "error" });
      reloadStatus();
    }
  };

  const recheckBrief = async () => {
    setError("");
    try {
      await api(`/api/campaigns/${clip.campaign_id}/recheck`, { body: { unchanged_confirmed: true } });
      await reloadOptions();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("noConnection"));
    }
  };

  if (!options.data && !options.error) {
    return (
      <section className="post" aria-busy="true" aria-label={t("title")} dir={rtl ? "rtl" : "ltr"}>
        {[0, 1, 2].map((n) => <i key={n} className="sk" style={{ height: 56, borderRadius: 18 }} />)}
      </section>
    );
  }
  if (!options.data) return <p className="error" role="alert">{t("loadFailed")}</p>;

  const waiting = pending ?? queued?.platforms ?? null;
  const why = chosen.length === 0 ? t("noneChosen") : !serverOk ? t("fixLocked") : !ticksOk ? t("tickAll") : "";

  return (
    <section className="post" aria-label={t("title")} dir={rtl ? "rtl" : "ltr"}>
      {(targets.length > 0 || waiting) && (
        <div className="stack" aria-live="polite">
          <h2>{t("statusTitle")}</h2>
          {waiting?.map((p) => (
            <article className="post-target" key={`wait-${p}`} aria-busy="true" aria-label={platform(p)}>
              <div className="row between wrap"><strong>{platform(p)}</strong><span className="badge info">{queued ? t("waitingConnection") : t("requestSent")}</span></div>
              <i className="sk" style={{ height: 12, width: "60%" }} />
            </article>
          ))}
          {queued && <p className="banner" role="status">{t("savedOffline")}</p>}
          {targets.map((target) => <TargetCard key={target.id} clip={clip} target={target} onCancel={cancel} onLinked={(done) => { replaceTarget(done); reloadOptions(); }} />)}
        </div>
      )}

      {pickable.length > 0 && !queued && (
        <div className="stack">
          <h2>{t("pickTitle")}</h2>
          {pickable.map((o) => {
            const on = o.allowed && !off[o.platform];
            const mode = o.manual_only ? ["byHand", "review"] as const : o.connection_state === "expiring" ? ["expiring", "review"] as const : ["viaApi", "pass"] as const;
            return (
              <label key={o.platform} className={`post-pick${o.allowed ? "" : " off"}`}>
                <input type="checkbox" checked={on} disabled={!o.allowed || !!pending} onChange={() => setOff({ ...off, [o.platform]: on })} />
                <span className="post-pick-main">
                  <strong>{platform(o.platform)}</strong>
                  <span className="small muted" dir="auto">{o.account ? `@${o.account.handle}` : ""}</span>
                  {o.blocked_reason && <span className="small">{blocked(o.blocked_reason)}</span>}
                </span>
                {o.allowed && <span className={`badge ${mode[1]}`}>{t(mode[0])}</span>}
              </label>
            );
          })}
        </div>
      )}

      {chosen.length > 0 && !queued && (
        <div className="stack">
          <h2>{t("checklistTitle")}</h2>
          <p className="small muted">{t("checklistHint")}</p>
          <div>
            {[...server].map(([name, item]) => (
              <div className="check" key={name}>
                <span className={`state ${item.ok ? "ok" : "no"}`} aria-hidden="true">{item.ok ? "✓" : "!"}</span>
                <span>{serverLabel(name, item.label)}<span className="sr-only">: {t(item.ok ? "checkDone" : "checkNotDone")}</span></span>
              </div>
            ))}
            {tickNames.map((name) => (
              <label className="check" key={name}>
                <input type="checkbox" checked={!!ticks[name]} disabled={!!pending} onChange={(event) => setTicks({ ...ticks, [name]: event.target.checked })} />
                <span>{tick(name)}</span>
              </label>
            ))}
          </div>
          {briefStale && <button type="button" className="btn" onClick={recheckBrief}>{t("recheck")}</button>}
          {warnings.map((warning) => <div className="banner warn" key={warning} dir="auto">{warning}</div>)}
          <button type="button" className="btn primary block" disabled={!ready} onClick={confirm}>
            {pending ? t("confirming") : f("confirm", { n: chosen.length })}
          </button>
          <p className="small muted">{!ready && !pending && why ? why : t("confirmHint")}</p>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
