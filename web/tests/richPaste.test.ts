import { describe, expect, it } from "vitest";

import { cleanUrl, countLinks, htmlToMarkdown, pasteToMarkdown } from "@/lib/richPaste";

// Hand-written fixtures shaped like what each source puts on the clipboard.
const NOTION = `<meta charset="utf-8"><div class="notion-page-content"><h2>Campaign rules</h2>
<ul><li>Add the <a href="https://drive.google.com/drive/folders/abc?usp=sharing">logo</a> to every clip</li><li><strong>Never</strong> post <code>#ad</code></li></ul>
<script>track()</script><style>.x{color:red}</style></div>`;
const DOCS = `<meta charset="utf-8"><b style="font-weight:normal;" id="docs-internal-guid-1"><p dir="ltr"><span style="font-weight:700;">Payout:</span><span style="font-weight:400;"> $2 per 1K views, see the </span>
<a href="https://www.google.com/url?q=https://example.com/guide%3Futm_source%3Dx&amp;sa=D&amp;source=editors"><span style="color:#1155cc">guide</span></a></p>
<ul><li><p><span>One strike and you are out</span></p></li></ul></b>`;
const DISCORD = `<div>✅ Allowed</div><div>* POST ENGLISH CS2 CONTENT<br>* 10s MINIMUM LENGTH</div><div><a href="https://f.io/5jc4M546" onclick="x()" data-tracking="1">LOGO</a></div>`;
const MARKETPLACE = `<article><h1>Topps x ClipFarm</h1><p>CPM <b>$1.00</b> / 1K<br>Max per clip $400</p><p><a href="https://www.youtube.com/watch?v=abc&utm_campaign=z&fbclid=1">He Has Aaron Judge Cards</a></p><a href="mailto:a@b.c">mail</a> <a href="#top">top</a></article>`;

describe("htmlToMarkdown", () => {
  it("keeps Notion links, lists, headings and bold, and drops scripts and styles", () => {
    const md = htmlToMarkdown(NOTION);
    expect(md).toContain("## Campaign rules");
    expect(md).toContain("[logo](https://drive.google.com/drive/folders/abc?usp=sharing)");
    expect(md).toMatch(/^\* +Add the/m);
    expect(md).toContain("**Never**");
    expect(md).not.toContain("track()");
    expect(md).not.toContain("color:red");
  });
  it("reads Google Docs style bold, not the normal-weight wrapper, and unwraps google redirect links", () => {
    const md = htmlToMarkdown(DOCS);
    expect(md).toContain("**Payout:**");
    expect(md).not.toContain("**$2");
    expect(md).toContain("[guide](https://example.com/guide?utm_source=x)".replace("?utm_source=x", ""));
    expect(md).toMatch(/^\* +One strike/m);
  });
  it("keeps Discord line breaks and drops handler attributes", () => {
    const md = htmlToMarkdown(DISCORD);
    expect(md.split("\n").filter(Boolean)).toContain("✅ Allowed");
    expect(md).toContain("[LOGO](https://f.io/5jc4M546)");
    expect(md).not.toContain("onclick");
    expect(md).toMatch(/POST ENGLISH CS2 CONTENT\n/);
  });
  it("strips tracking parameters, drops mailto and anchor links but keeps their words", () => {
    const md = htmlToMarkdown(MARKETPLACE);
    expect(md).toContain("# Topps x ClipFarm");
    expect(md).toContain("[He Has Aaron Judge Cards](https://www.youtube.com/watch?v=abc)");
    expect(md).not.toContain("mailto");
    expect(md).toMatch(/mail top/);
  });
});

describe("cleanUrl and countLinks", () => {
  it("removes utm and click ids only", () => {
    expect(cleanUrl("https://x.com/a?utm_source=1&id=7&fbclid=2")).toBe("https://x.com/a?id=7");
    expect(cleanUrl("not a url")).toBe("not a url");
  });
  it("counts distinct markdown and bare links", () => {
    expect(countLinks("[a](https://x.com/1) and https://y.com/2. [again](https://x.com/1)")).toBe(2);
    expect(countLinks("no links")).toBe(0);
  });
});

describe("pasteToMarkdown", () => {
  it("uses the HTML when present and counts the links kept", () => {
    const r = pasteToMarkdown(DISCORD, "plain");
    expect(r.rich).toBe(true);
    expect(r.links).toBe(1);
  });
  it("falls back to the plain text", () => {
    const r = pasteToMarkdown("", "just text https://a.com/x");
    expect(r).toEqual({ text: "just text https://a.com/x", links: 1, rich: false });
  });
  it("falls back when the HTML has no content", () => {
    expect(pasteToMarkdown("<meta charset='utf-8'>", "hello").text).toBe("hello");
  });
});
