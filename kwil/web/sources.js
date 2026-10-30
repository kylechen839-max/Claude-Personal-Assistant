// A reply's sources (citations.py) under its bubble: the web pages a web
// search cited, as small numbered superscripts in the text and a list of
// numbered links (title and domain), and Kwil's own data it answered from,
// as one line: "Sources: Chat on Tue 28 Oct · PHYS 361 syllabus.pdf". A
// chat opens its transcript (history.js), a project file its project
// (projects.js); a course file is only named.
//
// Everything here came from a web page, a file or a saved chat: it is
// drawn with textContent only, links are http(s) only (safeUrl: a
// javascript: or data: URL is dropped, never drawn), open in a new tab with
// rel="noopener noreferrer", and nothing gets an inline handler (the CSP).
// Bounded as the server bounds it. app.js and history.js call
// render(doc, bubble, text, citations, links). Loaded before app.js.
"use strict";

(function (root) {
  const MAX_SOURCES = 20;
  const MAX_TITLE = 200;
  const MAX_URL = 2000;
  const MAX_MARKS = 50;
  const KINDS = ["web", "chat", "project_file", "course_file", "attachment"];
  const TEXT = { sources: "Sources", source: "Source" };

  // `url` if it is an http(s) address with a host; else null.
  function safeUrl(url) {
    if (typeof url !== "string" || url.length > MAX_URL || /[\s\u0000-\u001f\u007f]/.test(url)) return null;
    let parsed;
    try { parsed = new URL(url); } catch (e) { return null; }
    if ((parsed.protocol !== "https:" && parsed.protocol !== "http:") || !parsed.hostname) return null;
    return url;
  }

  function domain(url) {
    try {
      const host = new URL(url).hostname;
      return host.startsWith("www.") ? host.slice(4) : host;
    } catch (e) { return ""; }
  }

  function clip(text, n) {
    const s = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
    return s.length <= n ? s : `${s.slice(0, n - 1)}…`;
  }

  // The server's {sources, marks}, checked again: unknown kinds and unsafe
  // links dropped (the marks renumbered to match), everything bounded.
  function normalize(cites) {
    const sources = [];
    const renumber = new Map();  // the server's number -> ours
    const list = cites && Array.isArray(cites.sources) ? cites.sources : [];
    list.forEach((s, i) => {
      if (sources.length >= MAX_SOURCES || !s || typeof s !== "object" || !KINDS.includes(s.kind)) return;
      const item = { kind: s.kind, title: clip(s.title, MAX_TITLE) };
      if (s.kind === "web") {
        item.url = safeUrl(s.url);
        if (!item.url) return;
        item.title = item.title || domain(item.url);
      } else if (s.kind === "attachment") {  // a file sent with a message: named only
        if (!item.title) return;
      } else {
        if (typeof s.ref !== "string" || !item.title) return;
        item.ref = s.ref;
        if (typeof s.project === "string") item.project = s.project;
      }
      sources.push(item);
      renumber.set(i + 1, sources.length);
    });
    const marks = [];
    for (const m of cites && Array.isArray(cites.marks) ? cites.marks : []) {
      if (marks.length >= MAX_MARKS) break;
      if (!Array.isArray(m) || !Number.isInteger(m[0]) || m[0] < 0 || !Array.isArray(m[1])) continue;
      const refs = m[1].map((n) => renumber.get(n)).filter((n) => n !== undefined);
      if (refs.length) marks.push([m[0], [...new Set(refs)].slice(0, 5)]);
    }
    marks.sort((a, b) => a[0] - b[0]);
    return { sources, marks };
  }

  // The reply's text with a superscript after each cited part. Marks count
  // UTF-16 code units (the server's citations.utf16_len, the same as
  // `length` here); one that would split an emoji's surrogate pair moves
  // past its second half.
  function drawText(doc, bubble, text, marks) {
    let at = 0;
    for (const [offset, refs] of marks) {
      let end = Math.min(Math.max(offset, at), text.length);
      if (end > 0 && end < text.length && /[\uD800-\uDBFF]/.test(text[end - 1])
          && /[\uDC00-\uDFFF]/.test(text[end])) end += 1;
      if (end > at) bubble.appendChild(doc.createTextNode(text.slice(at, end)));
      at = end;
      const sup = doc.createElement("sup");
      sup.className = "cite-ref";
      sup.textContent = refs.join(",");
      sup.setAttribute("aria-label", `${TEXT.source} ${refs.join(", ")}`);
      bubble.appendChild(sup);
    }
    if (at < text.length) bubble.appendChild(doc.createTextNode(text.slice(at)));
  }

  function webRow(doc, s, n) {
    const a = doc.createElement("a");
    a.className = "source source-web";
    a.href = s.url;  // checked by safeUrl
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    const num = doc.createElement("span");
    num.className = "source-n";
    num.textContent = String(n);
    const title = doc.createElement("span");
    title.className = "source-title";
    title.textContent = s.title;
    const host = doc.createElement("span");
    host.className = "source-domain";
    host.textContent = domain(s.url);
    a.appendChild(num);
    a.appendChild(title);
    a.appendChild(host);
    return a;
  }

  function ownItem(doc, s, links) {
    const open = s.kind === "chat" ? links.openConversation && (() => links.openConversation(s.ref))
      : s.kind === "project_file" && s.project ? links.openProject && (() => links.openProject(s.project))
        : null;
    const node = doc.createElement(open ? "button" : "span");
    node.className = "source source-own";
    node.textContent = s.title;
    if (open) {
      node.type = "button";
      node.addEventListener("click", open);
    }
    return node;
  }

  // Draw `text` into `bubble` with its citations ({sources, marks}).
  // `links`: {openConversation(id), openProject(id)}. Returns the list
  // element (or null when there is nothing to show).
  function render(doc, bubble, text, cites, links = {}) {
    const { sources, marks } = normalize(cites);
    text = String(text == null ? "" : text);
    bubble.textContent = "";
    // superscripts number the web pages only (1, 2, ... in the list's
    // order); Kwil's own sources are named in the line instead
    const shown = new Map();
    sources.forEach((s, i) => { if (s.kind === "web") shown.set(i + 1, shown.size + 1); });
    drawText(doc, bubble, text, marks
      .map(([at, refs]) => [at, refs.map((n) => shown.get(n)).filter((n) => n !== undefined)])
      .filter(([, refs]) => refs.length));
    if (!sources.length) return null;
    const box = doc.createElement("div");
    box.className = "sources";
    const head = doc.createElement("span");
    head.className = "sources-head";
    head.textContent = TEXT.sources;
    box.appendChild(head);
    const web = doc.createElement("div");
    web.className = "sources-web";
    const own = doc.createElement("div");
    own.className = "sources-own";
    sources.forEach((s, i) => {
      if (s.kind === "web") web.appendChild(webRow(doc, s, shown.get(i + 1)));
      else {
        if (own.children.length) {
          const dot = doc.createElement("span");
          dot.className = "source-sep";
          dot.setAttribute("aria-hidden", "true");
          dot.textContent = "·";
          own.appendChild(dot);
        }
        own.appendChild(ownItem(doc, s, links));
      }
    });
    if (own.children.length) box.appendChild(own);
    if (web.children.length) box.appendChild(web);
    bubble.appendChild(box);
    return box;
  }

  // Text for the phone's own voice: never a web address.
  function speakable(text) {
    return String(text == null ? "" : text)
      .replace(/\[([^\]\n]{1,200})\]\((?:https?:\/\/|www\.)[^)\s]*\)/gi, "$1")
      .replace(/(?:https?:\/\/|www\.)\S+/gi, "")
      .replace(/[ \t]{2,}/g, " ");
  }

  root.KwilSources = { safeUrl, domain, normalize, render, speakable, TEXT };
})(typeof window !== "undefined" ? window : globalThis);
