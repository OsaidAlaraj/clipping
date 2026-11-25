"use client";

import { createContext, useContext, useEffect, useState } from "react";

// English ships first. Arabic labels cover navigation and the review actions; the layout mirrors
// right-to-left (the action rail moves to the left) while swipe meanings stay the same.
const LABELS = {
  en: {
    home: "Home", feed: "Feed", queue: "Queue", more: "More",
    accept: "Accept", reject: "Reject", hooks: "Hooks", edit: "Edit", later: "Review later",
    undo: "Undo", filtered: "Filtered", rescue: "Rescue", rejected: "Rejected", accepted: "Accepted", fullScreen: "Full screen", hideText: "Hide text", showText: "Show text", parked: "Saved for later", stopHere: "Stop here", shuffle: "Shuffle", libraryTitle: "Accepted clips", searchClips: "Search clips", sortBy: "Sort", sortNewest: "Newest", sortViews: "Most views", sortEarned: "Most earned", sortCampaign: "Campaign A to Z", campaigns: "Campaigns", allCampaigns: "All", libraryEmpty: "No accepted clips yet", libraryNoMatch: "No clips match", openPost: "Open the post", soundOn: "Tap for sound", mute: "Mute", muted: "Muted",
    reviewLater: "Review Later", ledger: "Ledger", alerts: "Alerts", settings: "Settings",
    explore: "Explore", review: "Review", profile: "Profile", create: "Add", addCampaign: "Add campaign", addVideo: "Add video",
    marketRejected: "The marketplace rejected this clip", marketReasonLabel: "What did the marketplace say?",
    marketReasonHint: "Paste its words. If it names a tag or a phrase, the brand's next clips are checked for it.",
    saveReason: "Save the reason", saving: "Saving…", brandSaved: "Saved. The brand's next clips will be checked:",
    brandReminder: "Saved as a reminder. It was too general to check, so it never blocks a clip.", reasonNeeded: "Write the reason the marketplace gave.",
    brandNote: "Brand note", close: "Close", back: "Back",
    acceptPost: "Accept and post", swipePostsTo: "Swipe right posts to", postsTo: "Posts to", postNow: "now", postIn: "in", minShort: "min",
    manualReady: "Ready to post manually", copyCaption: "Copy caption", copied: "Copied", downloadVideo: "Download video",
    practiceNoPost: "Accepted. Practice clips are never posted.", noAccountsToPost: "Accepted. No account to post to.",
    confirming: "Confirming…", swipeUndone: "Undone. The posts were cancelled.", manualWord: "manual", noConnection: "No connection. Nothing was posted.",
    gateBlocked: "Quality check failed: cannot post by swipe. Fix the caption or language first.",
    briefStale: "The brief check is older than 6 hours. Re-check it, then swipe again.", recheckBrief: "Re-check brief",
    briefOpenFirst: "Open the brief and read it, then confirm it is unchanged.", briefUnchanged: "I re-read it: unchanged",
    briefRechecked: "Brief re-checked. Swipe right again to post.", briefChanged: "The brief changed. Re-approve the campaign before posting.",
  },
  ar: {
    home: "الرئيسية", feed: "المراجعة", queue: "قائمة النشر", more: "المزيد",
    accept: "قبول", reject: "رفض", hooks: "العناوين", edit: "تعديل", later: "مراجعة لاحقاً",
    undo: "تراجع", filtered: "المستبعدة", rescue: "إنقاذ", rejected: "تم الرفض", accepted: "تم القبول", fullScreen: "ملء الشاشة", hideText: "إخفاء النص", showText: "إظهار النص", parked: "حُفظ للمراجعة لاحقاً", stopHere: "توقف هنا", shuffle: "اخلط", libraryTitle: "الكليبات المقبولة", searchClips: "ابحث في الكليبات", sortBy: "ترتيب", sortNewest: "الأحدث", sortViews: "الأكثر مشاهدات", sortEarned: "الأعلى ربحاً", sortCampaign: "الحملة أ إلى ي", campaigns: "الحملات", allCampaigns: "الكل", libraryEmpty: "ما في كليبات مقبولة بعد", libraryNoMatch: "ما في كليبات تطابق", openPost: "افتح البوست", soundOn: "اضغط لتشغيل الصوت", mute: "كتم", muted: "صامت",
    reviewLater: "للمراجعة لاحقاً", ledger: "السجل المالي", alerts: "التنبيهات", settings: "الإعدادات",
    explore: "استكشاف", review: "المراجعة", profile: "ملفي", create: "إضافة", addCampaign: "إضافة حملة", addVideo: "إضافة فيديو",
    marketRejected: "المنصّة رفضت هذا المقطع", marketReasonLabel: "ماذا قالت المنصّة؟",
    marketReasonHint: "الصق كلامها. إذا ذكرت وسماً أو عبارة، تُفحص مقاطع هذه العلامة القادمة بحثاً عنها.",
    saveReason: "احفظ السبب", saving: "جارٍ الحفظ…", brandSaved: "تم الحفظ. ستُفحص مقاطع هذه العلامة القادمة:",
    brandReminder: "حُفظ كتذكير. السبب عام جداً للفحص، لذلك لا يمنع أي مقطع.", reasonNeeded: "اكتب السبب الذي ذكرته المنصّة.",
    brandNote: "ملاحظة العلامة", close: "إغلاق", back: "رجوع",
    acceptPost: "قبول ونشر", swipePostsTo: "السحب لليمين ينشر على", postsTo: "سيُنشر على", postNow: "الآن", postIn: "بعد", minShort: "دقيقة",
    manualReady: "جاهز للنشر يدوياً", copyCaption: "انسخ الوصف", copied: "تم النسخ", downloadVideo: "نزّل الفيديو",
    practiceNoPost: "تم القبول. مقاطع التجربة لا تُنشر أبداً.", noAccountsToPost: "تم القبول. لا يوجد حساب للنشر عليه.",
    confirming: "جارٍ التأكيد…", swipeUndone: "تم التراجع. أُلغيت المنشورات.", manualWord: "يدوي", noConnection: "لا يوجد اتصال. لم يُنشر شيء.",
    gateBlocked: "فشل فحص الجودة: لا يمكن النشر بالسحب. أصلح الوصف أو اللغة أولاً.",
    briefStale: "مضى على فحص الملخّص أكثر من 6 ساعات. أعد فحصه ثم اسحب مجدداً.", recheckBrief: "أعد فحص الملخّص",
    briefOpenFirst: "افتح الملخّص واقرأه، ثم أكّد أنه لم يتغيّر.", briefUnchanged: "قرأته من جديد: لم يتغيّر",
    briefRechecked: "أُعيد فحص الملخّص. اسحب لليمين مجدداً للنشر.", briefChanged: "تغيّر الملخّص. أعد اعتماد الحملة قبل النشر.",
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
  return {
    lang, setLang, rtl: lang === "ar", t: (key: LabelKey) => LABELS[lang][key],
    /** Text that sits next to the code that shows it: tr("English", "العربية"). */
    tr: (en: string, ar: string) => (lang === "ar" ? ar : en),
  };
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
