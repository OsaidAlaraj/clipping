"use client";

import { createContext, useContext, useEffect, useState } from "react";

// English ships first. Arabic labels cover navigation and the review actions; the layout mirrors
// right-to-left (the action rail moves to the left) while swipe meanings stay the same.
const LABELS = {
  en: {
    home: "Home", feed: "Feed", queue: "Queue", more: "More",
    accept: "Accept", reject: "Reject", hooks: "Hooks", edit: "Edit", later: "Review later",
    undo: "Undo", filtered: "Filtered", rescue: "Rescue", rejected: "Rejected", stopHere: "Stop here", soundOn: "Tap for sound", muted: "Muted",
    reviewLater: "Review Later", ledger: "Ledger", alerts: "Alerts", settings: "Settings",
    explore: "Explore", review: "Review", profile: "Profile", create: "New", addCampaign: "Add campaign", addVideo: "Add video",
  },
  ar: {
    home: "الرئيسية", feed: "المراجعة", queue: "قائمة النشر", more: "المزيد",
    accept: "قبول", reject: "رفض", hooks: "العناوين", edit: "تعديل", later: "مراجعة لاحقاً",
    undo: "تراجع", filtered: "المستبعدة", rescue: "إنقاذ", rejected: "تم الرفض", stopHere: "توقف هنا", soundOn: "اضغط لتشغيل الصوت", muted: "صامت",
    reviewLater: "للمراجعة لاحقاً", ledger: "السجل المالي", alerts: "التنبيهات", settings: "الإعدادات",
    explore: "استكشاف", review: "المراجعة", profile: "ملفي", create: "جديد", addCampaign: "إضافة حملة", addVideo: "إضافة فيديو",
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
