"use client";

// Words for the posting screens, English and Arabic side by side. `{name}` marks a value filled in by `f`.
// The platform names and the server's check labels have Arabic forms too; anything unknown falls back to what the server sent.

import { PLATFORM_LABEL } from "@/lib/api";
import { useLang } from "@/lib/i18n";

const EN = {
  title: "Post this clip",
  pickTitle: "Where should it go?",
  viaApi: "Posts for you",
  expiring: "Sign-in ends soon",
  byHand: "You post it by hand",
  noneChosen: "Choose at least one platform.",
  checklistTitle: "Before you post",
  checklistHint: "One checklist for every platform you chose.",
  serverLocked: "Checked for you",
  checkDone: "done",
  checkNotDone: "not done",
  accountOk: "Your own active account, on a platform the campaign allows",
  fileOk: "File matches the version you accepted",
  briefOk: "Brief re-checked and unchanged for this batch",
  recheck: "I re-read the brief: unchanged",
  tickAccount: "I am posting from the right account, on the right platform",
  tickDisclosure: "The paid-partnership toggle is on (set it by hand in the platform app)",
  tickCaption: "The caption is pasted, starting with the disclosure",
  tickHashtags: "Hashtags, tags and mentions are checked",
  confirm: "Confirm and post ({n})",
  confirming: "Sending…",
  confirmHint: "One tap. Posts are spaced out inside your posting hours. Nothing is sent until you tap.",
  fixLocked: "Fix the locked items first.",
  tickAll: "Tick every item to continue.",
  requestSent: "Sending your request…",
  waitingConnection: "Waiting for a connection",
  savedOffline: "Saved on this device. It is sent as soon as you are back online.",
  loadFailed: "Could not load the posting options. Check your connection.",
  refused: "Not posted. Nothing was sent.",
  statusTitle: "Posting status",
  scheduled: "Goes out at {time}",
  waitingYou: "Waiting for you to post it",
  sending: "Sending…",
  processing: "{platform} is processing the video",
  published: "Posted",
  draft: "Saved as a draft",
  private: "Uploaded privately",
  needs_check: "Needs a check",
  failed: "Not posted",
  cancelled: "Cancelled",
  cancel: "Cancel",
  cancelledToast: "Cancelled. Nothing was sent.",
  cancelFailed: "Could not cancel it. {why}",
  noConnection: "No connection.",
  youCancelled: "You cancelled it.",
  finishTitle: "Finish in the app",
  finishDraft: "Open {platform}, add the caption, post it, then paste the public link below.",
  finishPrivate: "Open {platform}, make the video public, then paste the public link below.",
  checkBody: "We could not confirm whether it posted. Open {platform}. If the video is there, paste its link below. If it is not, tap It did not post.",
  didNotPost: "It did not post",
  openPost: "Open the post",
  countdown: "Submit the link to the marketplace within",
  countdownOver: "The 30 minutes are over. Submit the link now if the marketplace still takes it.",
  manualTitle: "Post it by hand in {platform}",
  share: "Share the video",
  download: "Download the video",
  downloadStarted: "Download started.",
  shareFailed: "Could not share the video. Download it instead.",
  copyCaption: "Copy caption",
  copyHashtags: "Copy hashtags",
  copied: "Copied.",
  copyBlocked: "Copying is blocked here. Select the text below and copy it yourself.",
  noHashtags: "This clip has no hashtags.",
  linkLabel: "Link to the public post",
  saveLink: "Save the link",
  savingLink: "Saving…",
  linkBad: "Paste the full link to the post. It starts with https://",
  linkWrongHost: "That is not a {platform} link. Paste the link to the {platform} post.",
  linkSaved: "Link saved. Posted.",
} as const;

export type PostKey = keyof typeof EN;

const AR: Record<PostKey, string> = {
  title: "انشر هذا المقطع",
  pickTitle: "أين تريد نشره؟",
  viaApi: "ننشر عنك",
  expiring: "الربط ينتهي قريباً",
  byHand: "تنشره بيدك",
  noneChosen: "اختر منصة واحدة على الأقل.",
  checklistTitle: "قبل النشر",
  checklistHint: "قائمة واحدة لكل المنصات التي اخترتها.",
  serverLocked: "تم فحصها لك",
  checkDone: "تم",
  checkNotDone: "لم يتم",
  accountOk: "حسابك الفعّال، على منصة تسمح بها الحملة",
  fileOk: "الملف هو النسخة التي قبلتها",
  briefOk: "أُعيد فحص الملخّص ولم يتغيّر لهذه الدفعة",
  recheck: "قرأت الملخّص من جديد: لم يتغيّر",
  tickAccount: "أنشر من الحساب الصحيح وعلى المنصة الصحيحة",
  tickDisclosure: "مفتاح الشراكة المدفوعة مفعّل (فعّله بيدك داخل تطبيق المنصة)",
  tickCaption: "ألصقت الوصف، ويبدأ بإفصاح الإعلان",
  tickHashtags: "راجعت الوسوم والإشارات",
  confirm: "أكّد وانشر ({n})",
  confirming: "جارٍ الإرسال…",
  confirmHint: "ضغطة واحدة. تُوزَّع المنشورات داخل ساعات نشرك. لا يُرسل شيء قبل أن تضغط.",
  fixLocked: "أصلح البنود المقفلة أولاً.",
  tickAll: "علّم كل البنود للمتابعة.",
  requestSent: "جارٍ إرسال طلبك…",
  waitingConnection: "بانتظار الاتصال",
  savedOffline: "حُفظ على هذا الجهاز. يُرسل فور عودة الاتصال.",
  loadFailed: "تعذّر تحميل خيارات النشر. تحقق من الاتصال.",
  refused: "لم يُنشر. لم يُرسل شيء.",
  statusTitle: "حالة النشر",
  scheduled: "يُنشر عند {time}",
  waitingYou: "بانتظار أن تنشره بيدك",
  sending: "جارٍ الإرسال…",
  processing: "{platform} تعالج الفيديو",
  published: "تم النشر",
  draft: "حُفظ كمسوّدة",
  private: "رُفع بشكل خاص",
  needs_check: "يحتاج فحصاً",
  failed: "لم يُنشر",
  cancelled: "أُلغي",
  cancel: "إلغاء",
  cancelledToast: "أُلغي. لم يُرسل شيء.",
  cancelFailed: "تعذّر الإلغاء. {why}",
  noConnection: "لا يوجد اتصال.",
  youCancelled: "ألغيته أنت.",
  finishTitle: "أكمله داخل التطبيق",
  finishDraft: "افتح {platform}، أضف الوصف وانشره، ثم الصق الرابط العام أدناه.",
  finishPrivate: "افتح {platform}، اجعل الفيديو عاماً، ثم الصق الرابط العام أدناه.",
  checkBody: "لم نتأكد إن كان قد نُشر. افتح {platform}. إن كان الفيديو موجوداً فالصق رابطه أدناه. وإن لم يكن فاضغط لم يُنشر.",
  didNotPost: "لم يُنشر",
  openPost: "افتح المنشور",
  countdown: "أرسل الرابط إلى المنصّة خلال",
  countdownOver: "انتهت الدقائق الثلاثون. أرسل الرابط الآن إن كانت المنصّة ما زالت تقبله.",
  manualTitle: "انشره بيدك في {platform}",
  share: "شارك الفيديو",
  download: "نزّل الفيديو",
  downloadStarted: "بدأ التنزيل.",
  shareFailed: "تعذّرت مشاركة الفيديو. نزّله بدلاً من ذلك.",
  copyCaption: "انسخ الوصف",
  copyHashtags: "انسخ الوسوم",
  copied: "تم النسخ.",
  copyBlocked: "النسخ ممنوع هنا. حدّد النص أدناه وانسخه بنفسك.",
  noHashtags: "لا وسوم في هذا المقطع.",
  linkLabel: "رابط المنشور العام",
  saveLink: "احفظ الرابط",
  savingLink: "جارٍ الحفظ…",
  linkBad: "الصق الرابط الكامل للمنشور. يبدأ بـ https://",
  linkWrongHost: "هذا ليس رابط {platform}. الصق رابط منشور {platform}.",
  linkSaved: "حُفظ الرابط. تم النشر.",
};

const PLATFORM_AR: Record<string, string> = { youtube_shorts: "يوتيوب", instagram_reels: "إنستغرام", tiktok: "تيك توك" };
const TICK_KEY: Record<string, PostKey> = {
  account_and_platform_correct: "tickAccount", disclosure_toggle_on: "tickDisclosure", caption_pasted: "tickCaption", hashtags_and_tags_checked: "tickHashtags",
};
const SERVER_KEY: Record<string, PostKey> = { account_owned_and_allowed: "accountOk", file_hash_matches_approved: "fileOk", brief_rechecked: "briefOk" };

/** The server's English reasons a platform cannot be chosen, in Arabic (matched by their stable wording). */
const BLOCKED_AR: [string, string][] = [
  ["does not allow", "الحملة لا تسمح بهذه المنصة."],
  ["no active", "ليس لديك حساب فعّال على هذه المنصة."],
  ["already going", "هذا المقطع في الطريق إلى هذه المنصة."],
];

export function usePostText() {
  const { lang, rtl } = useLang();
  const words: Record<PostKey, string> = lang === "ar" ? AR : EN;
  const t = (key: PostKey) => words[key];
  /** A string with `{name}` slots filled in. */
  const f = (key: PostKey, values: Record<string, string | number>) => words[key].replace(/\{(\w+)\}/g, (_, name) => String(values[name] ?? ""));
  const platform = (id: string) => (lang === "ar" ? PLATFORM_AR[id] : undefined) ?? PLATFORM_LABEL[id] ?? id;
  const tick = (name: string) => (TICK_KEY[name] ? words[TICK_KEY[name]] : name);
  const serverLabel = (name: string, fallback: string) => (SERVER_KEY[name] ? words[SERVER_KEY[name]] : fallback);
  const blocked = (reason: string) => (lang === "ar" ? BLOCKED_AR.find(([part]) => reason.includes(part))?.[1] : undefined) ?? reason;
  const time = (iso: string) => new Date(iso).toLocaleTimeString(lang === "ar" ? "ar" : undefined, { hour: "2-digit", minute: "2-digit" });
  return { lang, rtl, t, f, platform, tick, serverLabel, blocked, time };
}
