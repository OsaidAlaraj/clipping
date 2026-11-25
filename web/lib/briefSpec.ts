// The campaign spec the brief extractor returns, and the pure helpers the review screen uses. Words for both languages are
// kept here (not in i18n.tsx) so the screen restyles and translates in one place.

export type Lang = "en" | "ar";
export type SpecItem = {
  id: string; layer: string; key: string; value: any; source_quote: string | null; confidence: number;
  status: "stated" | "unclear" | "conflict"; origin: string; dismissed?: boolean;
  alternatives?: { value: any; source_quote: string | null; origin: string }[];
};
export type SpecLink = { url: string; text: string; kind: string; fetch: { status: string; note: string } };
export type Spec = { version: number; items: SpecItem[]; links: SpecLink[]; warnings: string[]; blocking: string[]; cost_usd?: number };

export const LAYERS = ["identity", "payout", "platforms", "audience", "content", "length", "captions", "assets", "sources", "hook",
  "edit_style", "account", "deadlines", "submission", "performance", "enforcement", "rejection", "priority"] as const;

const TEXT = {
  en: {
    layers: {
      identity: "Campaign", payout: "Payout", platforms: "Platforms", audience: "Language and audience", content: "Content",
      length: "Clip length", captions: "Captions", assets: "Logo and assets", sources: "Footage sources", hook: "Hook rules",
      edit_style: "Editing style", account: "Account requirements", deadlines: "Deadlines", submission: "Submitting clips",
      performance: "Performance minimums", enforcement: "Strikes", rejection: "Why clips get rejected", priority: "Cut these first",
    } as Record<string, string>,
    keys: {
      marketplace: "Marketplace", name: "Name", campaign_url: "Campaign page", model: "Model", rate_per_1k: "Pay per 1,000 views ($)",
      min_payout: "Minimum payout ($)", min_qualifying_views: "Views before payout starts", max_per_clip: "Max per clip ($)",
      max_per_creator: "Max per creator ($)", max_per_platform: "Max per platform ($)", budget_total: "Total budget ($)",
      budget_used_pct: "Budget used (%)", payment_methods: "Payment methods", allowed: "Allowed", language: "Language", tier: "Audience tier",
      min_share: "Minimum audience share", forbidden_regions: "Audience not wanted", allowed_categories: "Wanted content", forbidden: "Not allowed",
      safety_flag: "Safety flag", min_s: "Minimum seconds", max_s: "Maximum seconds", required_hashtags: "Required hashtag",
      required_mentions: "Required mention", disclosure_policy: "Ad label (#ad)", copy_rules: "Caption wording", logo: "Logo",
      logo_guidelines: "Logo guidelines", logo_placement: "Logo placement", logo_type: "Logo type", whitelist: "Allowed source",
      forbidden_class: "Forbidden footage", constraints: "Rule", guidance: "Guidance", requirements: "Requirement", deadline: "Deadline",
      within_minutes: "Submit within (minutes)", duplicates_rejected: "Duplicates rejected", min_engagement_pct: "Minimum engagement (%)",
      min_views: "Minimum views", strikes: "Strike rule", reasons: "Reason", content: "Priority",
    } as Record<string, string>,
    status: { stated: "Stated", unclear: "Not stated", conflict: "Two readings" },
    unclearTitle: "Needs your answer", notApplicable: "Not applicable", answerHere: "Your answer", notStated: "The brief does not say",
    useThis: "Use this", ruleReading: "Read by rules", aiReading: "Read by AI", fromBrief: "From the brief", edit: "Edit", remove: "Remove", restore: "Put back",
    save: "Done", links: "Links in the brief", pasteHere: "Paste its text here", addText: "Add the text", needsPaste: "Needs your help",
    kinds: { logo_asset_folder: "Logo or asset folder", guidelines_doc: "Guidelines page", content_bank_video: "Source video", campaign_page: "Campaign page",
      submission_page: "Submission page", social_profile: "Social profile", other: "Other link" } as Record<string, string>,
    linkStatus: { classified_only: "Not opened", fetched: "Read", pasted: "Text added", needs_paste: "Needs a paste", pending: "Not read" } as Record<string, string>,
    willHappen: "What will happen", confirm: "Create the campaign", confirmBlocked: "Answer the red items first", reading: "Reading…",
    understood: "What I understood", itemsFound: "items found", needAnswers: "need your answer", linksKept: "links kept",
    adOff: "The brief says never to use #ad: the ad label will be switched off for this campaign. You can switch it back on.",
    adRequired: "The brief requires the ad label: it stays on.",
    logoForbidden: "No logo of any kind may be added to clips.", logoRequired: "A provided logo is required. Add the logo file from the link yourself.",
    whitelist: "Footage must come from the brief's list. Anything else is refused unless you override.",
    minLength: "Clips will be at least {n} seconds.", hashtags: "These go into every caption: {n}.",
    stopDate: "Content stops on the date the brief gives.", accounts: "You will get a warning when an account may not meet the brief.",
  },
  ar: {
    layers: {
      identity: "الحملة", payout: "الدفع", platforms: "المنصّات", audience: "اللغة والجمهور", content: "المحتوى",
      length: "طول المقطع", captions: "الوصف", assets: "الشعار والملفات", sources: "مصادر الفيديو", hook: "قواعد العنوان",
      edit_style: "أسلوب المونتاج", account: "شروط الحساب", deadlines: "المواعيد", submission: "تسليم المقاطع",
      performance: "الحد الأدنى للأداء", enforcement: "المخالفات", rejection: "أسباب رفض المقاطع", priority: "ابدأ بهذه",
    } as Record<string, string>,
    keys: {
      marketplace: "المنصّة", name: "الاسم", campaign_url: "صفحة الحملة", model: "نموذج الدفع", rate_per_1k: "الأجر لكل 1000 مشاهدة ($)",
      min_payout: "أقل مبلغ دفع ($)", min_qualifying_views: "مشاهدات قبل بدء الدفع", max_per_clip: "الحد الأعلى للمقطع ($)",
      max_per_creator: "الحد الأعلى للصانع ($)", max_per_platform: "الحد الأعلى للمنصّة ($)", budget_total: "الميزانية ($)",
      budget_used_pct: "المستهلك من الميزانية (%)", payment_methods: "طرق الدفع", allowed: "المسموح", language: "اللغة", tier: "فئة الجمهور",
      min_share: "أقل نسبة جمهور", forbidden_regions: "جمهور غير مرغوب", allowed_categories: "المحتوى المطلوب", forbidden: "غير مسموح",
      safety_flag: "تنبيه أمان", min_s: "أقل عدد ثوانٍ", max_s: "أكثر عدد ثوانٍ", required_hashtags: "وسم مطلوب",
      required_mentions: "إشارة مطلوبة", disclosure_policy: "وسم الإعلان (#ad)", copy_rules: "صياغة الوصف", logo: "الشعار",
      logo_guidelines: "إرشادات الشعار", logo_placement: "مكان الشعار", logo_type: "نوع الشعار", whitelist: "مصدر مسموح",
      forbidden_class: "فيديو ممنوع", constraints: "قاعدة", guidance: "إرشاد", requirements: "شرط", deadline: "الموعد النهائي",
      within_minutes: "التسليم خلال (دقيقة)", duplicates_rejected: "المكرر مرفوض", min_engagement_pct: "أقل تفاعل (%)",
      min_views: "أقل مشاهدات", strikes: "قاعدة المخالفة", reasons: "السبب", content: "الأولوية",
    } as Record<string, string>,
    status: { stated: "مذكور", unclear: "غير مذكور", conflict: "قراءتان" },
    unclearTitle: "بحاجة لإجابتك", notApplicable: "لا ينطبق", answerHere: "إجابتك", notStated: "الإعلان لا يذكر ذلك",
    useThis: "استخدم هذه", ruleReading: "قراءة القواعد", aiReading: "قراءة الذكاء الاصطناعي", fromBrief: "من الإعلان", edit: "تعديل", remove: "حذف", restore: "إرجاع",
    save: "تم", links: "الروابط في الإعلان", pasteHere: "الصق نص الصفحة هنا", addText: "أضف النص", needsPaste: "نحتاج مساعدتك",
    kinds: { logo_asset_folder: "مجلد الشعار أو الملفات", guidelines_doc: "صفحة الإرشادات", content_bank_video: "فيديو مصدر", campaign_page: "صفحة الحملة",
      submission_page: "صفحة التسليم", social_profile: "حساب اجتماعي", other: "رابط آخر" } as Record<string, string>,
    linkStatus: { classified_only: "لم يُفتح", fetched: "تمت القراءة", pasted: "أُضيف النص", needs_paste: "يحتاج لصق", pending: "لم يُقرأ" } as Record<string, string>,
    willHappen: "ما الذي سيحدث", confirm: "أنشئ الحملة", confirmBlocked: "أجب عن البنود الحمراء أولاً", reading: "جارٍ القراءة…",
    understood: "ما فهمته", itemsFound: "بنداً", needAnswers: "تحتاج إجابتك", linksKept: "رابطاً محفوظاً",
    adOff: "الإعلان يمنع وسم #ad: سيُطفأ وسم الإعلان لهذه الحملة. يمكنك إعادته.",
    adRequired: "الإعلان يشترط وسم الإعلان: يبقى مفعّلاً.",
    logoForbidden: "ممنوع إضافة أي شعار على المقاطع.", logoRequired: "الشعار المرفق مطلوب. أضف ملف الشعار بنفسك من الرابط.",
    whitelist: "الفيديو يجب أن يكون من قائمة الإعلان. أي مصدر آخر يُرفض إلا إذا تجاوزت ذلك.",
    minLength: "المقاطع لا تقل عن {n} ثانية.", hashtags: "تدخل في كل وصف: {n}.",
    stopDate: "يتوقف المحتوى في التاريخ الذي يذكره الإعلان.", accounts: "ستصلك تحذيرات عندما قد لا يطابق الحساب شروط الإعلان.",
  },
};

export const words = (lang: Lang) => TEXT[lang];

export const live = (spec: Spec) => spec.items.filter((i) => !i.dismissed);
export const itemsOf = (spec: Spec, layer: string) => spec.items.filter((i) => i.layer === layer);
export const keyName = (key: string) => key.split(".")[1];

/** Unclear items the owner must answer before the campaign can be created. */
export function blockingKeys(spec: Spec): string[] {
  return spec.items.filter((i) => i.status === "unclear" && (spec.blocking ?? []).includes(i.key) && !i.dismissed).map((i) => i.key);
}

/** The text in an item's edit box for its value, and back. Lists are comma separated; numbers stay numbers. */
export function valueToText(value: any): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.map((v) => (typeof v === "object" ? JSON.stringify(v) : String(v))).join(", ");
  if (typeof value === "object") return Object.entries(value).filter(([, v]) => v !== null && v !== "").map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`).join(" · ");
  return String(value);
}

export function textToValue(text: string, previous: any): any {
  const t = text.trim();
  if (Array.isArray(previous)) return t ? t.split(/\s*,\s*/).filter(Boolean) : [];
  if (typeof previous === "number" || (previous === null && /^-?\d+(\.\d+)?$/.test(t))) return t === "" ? null : Number(t);
  if (typeof previous === "boolean") return /^(true|yes|1)$/i.test(t);
  return t === "" ? null : t;
}

/** One change to one item; every other item is untouched. An owner edit counts as stated. */
export function editItem(spec: Spec, id: string, patch: Partial<SpecItem>): Spec {
  const items = spec.items.map((i) => {
    if (i.id !== id) return i;
    const next = { ...i, ...patch };
    if ("value" in patch) { next.status = "stated"; next.origin = "owner"; delete next.alternatives; }
    return next;
  });
  const out = { ...spec, items };
  return { ...out, blocking: blockingKeys(out) };
}

/** Choose one reading of a conflict: the item takes that value and quote and is stated. */
export function resolveConflict(spec: Spec, id: string, choice: "rule" | "alt", altIndex = 0): Spec {
  const item = spec.items.find((i) => i.id === id);
  if (!item || item.status !== "conflict") return spec;
  const pick = choice === "rule" ? { value: item.value, source_quote: item.source_quote } : (item.alternatives ?? [])[altIndex];
  if (!pick) return spec;
  return editItem(spec, id, { value: pick.value, source_quote: pick.source_quote, origin: "owner", status: "stated" });
}

/** Number of items by state, for the header line. Dismissed items are not counted. */
export function counts(spec: Spec) {
  const items = live(spec);
  return { found: items.filter((i) => i.status !== "unclear").length, unclear: blockingKeys(spec).length, conflicts: items.filter((i) => i.status === "conflict").length };
}

/** What confirming will change, in plain words (the server records the same decisions). */
export function previewDecisions(spec: Spec, lang: Lang): string[] {
  const w = words(lang);
  const val = (key: string) => live(spec).filter((i) => i.key === key && i.status !== "unclear" && i.value !== null).map((i) => i.value);
  const out: string[] = [];
  const policy = val("captions.disclosure_policy")[0];
  if (policy === "forbidden") out.push(w.adOff);
  if (policy === "required") out.push(w.adRequired);
  const logo = val("assets.logo")[0];
  if (logo?.requirement === "forbidden") out.push(w.logoForbidden);
  if (logo?.requirement === "required") out.push(w.logoRequired);
  if (val("sources.whitelist").length) out.push(w.whitelist);
  const min = val("length.min_s")[0];
  if (min) out.push(w.minLength.replace("{n}", String(min)));
  const tags = [...val("captions.required_hashtags"), ...val("captions.required_mentions")];
  if (tags.length) out.push(w.hashtags.replace("{n}", tags.join(" ")));
  if (val("deadlines.deadline").length) out.push(w.stopDate);
  if (val("account.requirements").length || val("audience.min_share").length) out.push(w.accounts);
  return out;
}
