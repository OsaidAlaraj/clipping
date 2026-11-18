"use client";

import { createContext, useContext, useEffect, useState } from "react";

// English ships first. Arabic labels cover navigation and the review actions; the layout mirrors
// right-to-left (the action rail moves to the left) while swipe meanings stay the same.
const LABELS = {
  en: {
    home: "Home", feed: "Feed", queue: "Queue", more: "More",
    accept: "Accept", reject: "Reject", hooks: "Hooks", edit: "Edit", later: "Review later",
    undo: "Undo", filtered: "Filtered", rescue: "Rescue", rejected: "Rejected", accepted: "Accepted", fullScreen: "Full screen", hideText: "Hide text", showText: "Show text", parked: "Saved for later", stopHere: "Stop here", soundOn: "Tap for sound", muted: "Muted",
    reviewLater: "Review Later", ledger: "Ledger", alerts: "Alerts", settings: "Settings",
    explore: "Explore", review: "Review", profile: "Profile", create: "New", addCampaign: "Add campaign", addVideo: "Add video",
    marketRejected: "The marketplace rejected this clip", marketReasonLabel: "What did the marketplace say?",
    marketReasonHint: "Paste its words. If it names a tag or a phrase, the brand's next clips are checked for it.",
    saveReason: "Save the reason", saving: "Saving…", brandSaved: "Saved. The brand's next clips will be checked:",
    brandReminder: "Saved as a reminder. It was too general to check, so it never blocks a clip.", reasonNeeded: "Write the reason the marketplace gave.",
    brandNote: "Brand note", close: "Close",
  },
  ar: {
    home: "الرئيسية", feed: "المراجعة", queue: "قائمة النشر", more: "المزيد",
    accept: "قبول", reject: "رفض", hooks: "العناوين", edit: "تعديل", later: "مراجعة لاحقاً",
    undo: "تراجع", filtered: "المستبعدة", rescue: "إنقاذ", rejected: "تم الرفض", accepted: "تم القبول", fullScreen: "ملء الشاشة", hideText: "إخفاء النص", showText: "إظهار النص", parked: "حُفظ للمراجعة لاحقاً", stopHere: "توقف هنا", soundOn: "اضغط لتشغيل الصوت", muted: "صامت",
    reviewLater: "للمراجعة لاحقاً", ledger: "السجل المالي", alerts: "التنبيهات", settings: "الإعدادات",
    explore: "استكشاف", review: "المراجعة", profile: "ملفي", create: "جديد", addCampaign: "إضافة حملة", addVideo: "إضافة فيديو",
    marketRejected: "المنصّة رفضت هذا المقطع", marketReasonLabel: "ماذا قالت المنصّة؟",
    marketReasonHint: "الصق كلامها. إذا ذكرت وسماً أو عبارة، تُفحص مقاطع هذه العلامة القادمة بحثاً عنها.",
    saveReason: "احفظ السبب", saving: "جارٍ الحفظ…", brandSaved: "تم الحفظ. ستُفحص مقاطع هذه العلامة القادمة:",
    brandReminder: "حُفظ كتذكير. السبب عام جداً للفحص، لذلك لا يمنع أي مقطع.", reasonNeeded: "اكتب السبب الذي ذكرته المنصّة.",
    brandNote: "ملاحظة العلامة", close: "إغلاق",
  },
} as const;

export type Lang = keyof typeof LABELS;
export type LabelKey = keyof (typeof LABELS)["en"];

const LangContext = createContext<{ lang: Lang; setLang: (lang: Lang) => void }>({ lang: "en", setLang: () => undefined });

export function LangProvider({ children, initial }: { children: React.ReactNode; initial?: Lang }) {
  const [lang, setLangState] = useState<Lang>(initial ?? "en");

  useEffect(() => {
    if (initial) return;
    try {
      const stored = localStorage.getItem("clipping_lang");
      if (stored === "ar" || stored === "en") setLangState(stored);
    } catch {
      /* storage unavailable */
    }
  }, [initial]);

  useEffect(() => {
    try {
      const theme = localStorage.getItem("clipping_theme");
      if (theme === "dark") document.documentElement.dataset.theme = "dark";
      else delete document.documentElement.dataset.theme;
    } catch {
      /* storage unavailable */
    }
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
  }, [lang]);

  const setLang = (next: Lang) => {
    setLangState(next);
    try {
      localStorage.setItem("clipping_lang", next);
    } catch {
      /* storage unavailable */
    }
  };
  return <LangContext.Provider value={{ lang, setLang }}>{children}</LangContext.Provider>;
}

export function useLang() {
  const { lang, setLang } = useContext(LangContext);
  return { lang, setLang, rtl: lang === "ar", t: (key: LabelKey) => LABELS[lang][key] };
}

const fmt = (n: number) => Math.round(n).toLocaleString("en-US");

/** The one-line "chance to reach the minimum" for a clip. English is the server's own sentence; Arabic is built from the numbers
 *  (so both say the same thing): the chance, the minimum, where the numbers come from, and that a weak clip is shown last. */
export function qualityText(q: { p_clear: number | null; label: string; basis: string; weak?: boolean; min_views?: number | null }, lang: Lang): string {
  if (lang === "en") return q.label;
  if (q.p_clear === null || !q.min_views) return "لا يوجد حدّ أدنى للمشاهدات في هذه الحملة";
  const pct = q.p_clear < 0.01 ? "أقل من 1%" : q.p_clear > 0.99 ? "أكثر من 99%" : `${Math.round(q.p_clear * 100)}%`;
  const basis = q.basis === "your posts" ? "من منشوراتك" : "تقدير: لا يوجد سجل مشاهدات بعد";
  const text = `احتمال بلوغ ${fmt(q.min_views)} مشاهدة: حوالي ${pct} (${basis})`;
  return q.weak ? `ضعيف: ${text}. يظهر في الآخر.` : text;
}
