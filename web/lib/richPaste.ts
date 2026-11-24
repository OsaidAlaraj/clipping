// Rich paste: the clipboard's HTML (Notion, Google Docs, Discord, marketplace pages) becomes Markdown that keeps
// hyperlinks, lists, headings, bold and line breaks. Scripts, styles and tracking attributes are dropped. A plain
// textarea loses every link; this keeps them so the brief extractor can classify and read them.
// Reuses turndown (MIT, the mature HTML to Markdown converter). Pure function: no DOM access outside the parser.
import TurndownService from "turndown";

const TRACKING = /^(utm_[a-z]+|fbclid|gclid|mc_eid|mc_cid|igshid|ref_src)$/i;
const DROP = ["script", "style", "meta", "link", "noscript", "svg", "iframe", "template", "head", "button", "input", "select", "textarea"];

/** Strip tracking parameters and unwrap Google's redirect links (https://www.google.com/url?q=REAL). */
export function cleanUrl(raw: string): string {
  let href = (raw || "").trim();
  if (!/^https?:\/\//i.test(href)) return href;
  try {
    let url = new URL(href);
    if (/(^|\.)google\.com$/i.test(url.hostname) && url.pathname === "/url") {
      const target = url.searchParams.get("q") || url.searchParams.get("url");
      if (target) url = new URL(target);
    }
    for (const key of [...url.searchParams.keys()]) if (TRACKING.test(key)) url.searchParams.delete(key);
    href = url.toString();
  } catch { /* keep the link as it was */ }
  return href;
}

const weight = (node: Element) => /font-weight\s*:\s*(bold|[6-9]00)/i.exec(node.getAttribute("style") || "");
const normal = (node: Element) => /font-weight\s*:\s*(normal|[1-5]00)/i.test(node.getAttribute("style") || "");

function service(): TurndownService {
  const td = new TurndownService({ headingStyle: "atx", bulletListMarker: "*", codeBlockStyle: "fenced", emDelimiter: "_", linkStyle: "inlined" });
  td.remove(DROP as any);
  // Google Docs wraps everything in <b style="font-weight:normal"> and marks real bold on spans: read the style, not the tag.
  td.addRule("docsBold", {
    filter: (node) => ["B", "STRONG", "SPAN"].includes(node.nodeName),
    replacement: (content, node) => {
      const el = node as Element;
      const bold = el.nodeName === "SPAN" ? Boolean(weight(el)) : !normal(el);
      return bold && content.trim() ? `**${content.trim()}**` : content;
    },
  });
  td.addRule("cleanLink", {
    filter: (node) => node.nodeName === "A" && Boolean((node as Element).getAttribute("href")),
    replacement: (content, node) => {
      const href = cleanUrl((node as Element).getAttribute("href") || "");
      const text = content.replace(/\s+/g, " ").trim();
      if (!text) return href;
      if (!/^https?:/i.test(href)) return text;   // mailto:, #anchors, javascript: are not brief links
      return text === href ? href : `[${text}](${href})`;
    },
  });
  // Keep a line break for <br> and for block <div>s (Discord and Notion use divs as lines).
  td.addRule("lineDiv", { filter: ["div"], replacement: (content) => (content.trim() ? `\n${content.trim()}\n` : "") });
  return td;
}

export function htmlToMarkdown(html: string): string {
  const td = service();
  const md = td.turndown(html)
    .replace(/00a0/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return md;
}

/** Number of distinct http(s) links in Markdown text: `[text](url)` and bare URLs. */
export function countLinks(markdown: string): number {
  const found = new Set<string>();
  for (const m of markdown.matchAll(/\]\((https?:\/\/[^\s)]+)\)/g)) found.add(m[1]);
  const bare = markdown.replace(/\]\((https?:\/\/[^\s)]+)\)/g, "");
  for (const m of bare.matchAll(/https?:\/\/[^\s)\]>]+/g)) found.add(m[0].replace(/[.,;:!?]+$/, ""));
  return found.size;
}

/** What to put in the box for one paste: Markdown when the clipboard has HTML with real content, else the plain text. */
export function pasteToMarkdown(html: string | undefined, text: string | undefined): { text: string; links: number; rich: boolean } {
  const plain = text ?? "";
  if (html && html.trim()) {
    try {
      const md = htmlToMarkdown(html);
      // A plain-text paste wrapped in <meta><span> gives the same words: take the HTML only when it adds links or structure.
      if (md) return { text: md, links: countLinks(md), rich: true };
    } catch { /* fall back to the plain text */ }
  }
  return { text: plain, links: countLinks(plain), rich: false };
}
