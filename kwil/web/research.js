// Deep research's card in the chat (research.py). While a job runs, a small
// card above the composer says "Researching… (3/10 searches)" with Cancel,
// and GET /api/research is asked again every few seconds. When it's ready:
// "Research ready", Open report (its History transcript, history.js, which
// draws the report with its sources, sources.js) and, when the person's
// Notion is connected, Save to Notion. A notification's tap
// (/#research=<conversation id>, sw.js) opens the report too.
//
// Everything from the server (the question, a link) goes in with
// textContent; a Notion link must be http(s). A finished card can be
// dismissed (the ids are remembered in localStorage, at most MAX_DISMISSED).
// app.js calls configure({ request }), onState(state) on every /api/state and
// onDone(event) after each turn (Kwil may have just started one). Loaded
// before app.js.
"use strict";

(function (root) {
  const POLL_MS = 4000;
  const MAX_CARDS = 3;
  const MAX_DISMISSED = 30;
  const DISMISSED_KEY = "kwil-research-dismissed";
  const CID = /^[A-Za-z0-9_-]{16,32}$/;
  const ACTIVE = ["queued", "planning", "researching", "writing"];
  const TEXT = {
    queued: "Research waiting to start",
    planning: "Planning the research…",
    writing: "Writing the report…",
    done: "Research ready",
    partial: "Research ready (partial)",
    failed: "Research couldn't finish",
    restarted: "Research stopped: the server restarted",
    noKey: "Add your Anthropic API key in Settings to use research.",
    cancelled: "Research cancelled",
    elsewhere: "Research is running on another server",
    cancel: "Cancel",
    cancelAsk: "Cancel this research? What it found so far isn't kept.",
    open: "Open report",
    notion: "Save to Notion",
    saving: "Saving…",
    saved: "Saved to Notion",
    dismiss: "Dismiss",
  };

  let request = null;           // app.js's request(url, options) -> Response
  let openConversation = null;  // id -> opens its transcript (history.js)

  function configure(opts = {}) {
    if (typeof opts.request === "function") request = opts.request;
    if (typeof opts.openConversation === "function") openConversation = opts.openConversation;
  }

  function isActive(job) { return Boolean(job) && ACTIVE.includes(job.status); }

  // The card's one line for a job.
  function statusText(job) {
    switch (job && job.status) {
      case "queued": return TEXT.queued;
      case "planning": return TEXT.planning;
      case "researching": {
        const n = Number.isInteger(job.searches) ? job.searches : 0;
        const max = Number.isInteger(job.max_searches) ? job.max_searches : 0;
        return `Researching… (${n}/${max} searches)`;
      }
      case "writing": return TEXT.writing;
      case "done": return TEXT.done;
      case "partial": return TEXT.partial;
      case "cancelled": return TEXT.cancelled;
      case "elsewhere": return TEXT.elsewhere;
      default:
        return job && job.error === "restarted" ? TEXT.restarted
          : job && job.error === "no_key" ? TEXT.noKey : TEXT.failed;
    }
  }

  // "/#research=<id>" (a notification's tap) -> the conversation id, or null.
  function conversationOf(url) {
    const m = /#research=([^&#]*)$/.exec(String(url == null ? "" : url));
    return m && CID.test(m[1]) ? m[1] : null;
  }

  function httpUrl(url) {
    if (typeof url !== "string" || url.length > 2000 || /[\s\u0000-\u001f]/.test(url)) return null;
    try {
      const u = new URL(url);
      return (u.protocol === "https:" || u.protocol === "http:") && u.hostname ? url : null;
    } catch (e) { return null; }
  }

  function setup(win, doc) {
    const box = doc.getElementById("research-cards");
    if (!box) return null;
    let enabled = false, jobs = [], timer = null, loading = false;
    const store = (() => { try { return win.localStorage || null; } catch (e) { return null; } })();
    let dismissed = [];
    try { dismissed = JSON.parse((store && store.getItem(DISMISSED_KEY)) || "[]"); } catch (e) { dismissed = []; }
    if (!Array.isArray(dismissed)) dismissed = [];

    function open(cid) {
      if (!CID.test(String(cid || ""))) return;
      const fn = openConversation || root.openTranscript;  // history.js
      if (typeof fn === "function") fn(cid);
    }

    async function call(url, options) {
      const res = request ? await request(url, options)
        : await win.fetch(url, { credentials: "same-origin", ...options });
      let body = {};
      try { body = await res.json(); } catch (e) { /* not JSON */ }
      if (!res.ok) throw new Error(body.error || `Error ${res.status}`);
      return body;
    }

    function post(url) {
      return call(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    }

    function dismiss(id) {
      dismissed = [...dismissed.filter((x) => x !== id), id].slice(-MAX_DISMISSED);
      try { if (store) store.setItem(DISMISSED_KEY, JSON.stringify(dismissed)); } catch (e) { /* private mode */ }
      render();
    }

    function button(cls, label, onClick) {
      const b = doc.createElement("button");
      b.type = "button";
      b.className = cls;
      b.textContent = label;
      b.addEventListener("click", onClick);
      return b;
    }

    function card(job) {
      const node = doc.createElement("div");
      node.className = "research-card";
      node.setAttribute("data-status", String(job.status || ""));
      const head = doc.createElement("div");
      head.className = "research-head";
      const state = doc.createElement("span");
      state.className = "research-state";
      state.textContent = statusText(job);
      head.appendChild(state);
      if (!isActive(job)) {
        const x = button("icon-btn research-dismiss", "", () => dismiss(job.id));
        x.setAttribute("aria-label", TEXT.dismiss);
        x.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
        head.appendChild(x);
      }
      node.appendChild(head);
      const q = doc.createElement("p");
      q.className = "research-question";
      q.textContent = String(job.question || "");
      node.appendChild(q);
      const actions = doc.createElement("div");
      actions.className = "research-actions";
      if (isActive(job)) {
        actions.appendChild(button("btn plain research-cancel", TEXT.cancel, async (e) => {
          if (typeof win.confirm === "function" && !win.confirm(TEXT.cancelAsk)) return;
          e.currentTarget.disabled = true;
          try { await post(`/api/research/${encodeURIComponent(job.id)}/cancel`); } catch (err) { /* the next poll shows it */ }
          load();
        }));
      } else if ((job.status === "done" || job.status === "partial") && job.conversation_id) {
        actions.appendChild(button("btn secondary research-open", TEXT.open, () => open(job.conversation_id)));
        const link = httpUrl(job.notion_url);
        if (link) {
          const a = doc.createElement("a");
          a.className = "btn plain research-notion";
          a.href = link;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.textContent = TEXT.saved;
          actions.appendChild(a);
        } else if (job.notion) {
          actions.appendChild(button("btn plain research-notion", TEXT.notion, async (e) => {
            const b = e.currentTarget;
            b.disabled = true;
            b.textContent = TEXT.saving;
            try {
              const body = await post(`/api/research/${encodeURIComponent(job.id)}/notion`);
              job.notion_url = body.url || "";
              job.notion = false;
              render();
            } catch (err) {
              b.disabled = false;
              b.textContent = err.message || String(err);
            }
          }));
        }
      }
      if (actions.children.length) node.appendChild(actions);
      return node;
    }

    function render() {
      box.textContent = "";
      const shown = jobs.filter((j) => isActive(j) || !dismissed.includes(j.id)).slice(0, MAX_CARDS);
      for (const j of shown) box.appendChild(card(j));
      box.hidden = !enabled || !shown.length;
    }

    function schedule() {
      if (timer !== null) win.clearTimeout(timer);
      timer = null;
      if (enabled && jobs.some(isActive)) timer = win.setTimeout(load, POLL_MS);
    }

    async function load() {
      if (!enabled || loading) return;
      loading = true;
      try {
        const body = await call("/api/research");
        jobs = Array.isArray(body.jobs) ? body.jobs.filter((j) => j && typeof j.id === "string") : [];
      } catch (e) {
        /* offline or signed out: keep what is shown, try again */
      } finally {
        loading = false;
      }
      render();
      schedule();
    }

    return {
      enable(on) {
        enabled = Boolean(on);
        if (enabled) load(); else { jobs = []; render(); schedule(); }
      },
      load, render, open,
      get jobs() { return jobs; },
      get polling() { return timer !== null; },
    };
  }

  let card = null;
  let hashDone = false;
  function ensure(win, doc) {
    if (!card && doc) {
      card = setup(win, doc);
      if (card && win.addEventListener) {
        win.addEventListener("hashchange", () => {
          const cid = conversationOf(win.location && win.location.hash);
          if (cid) card.open(cid);
        });
        const nav = win.navigator;
        if (nav && nav.serviceWorker && nav.serviceWorker.addEventListener) {
          nav.serviceWorker.addEventListener("message", (e) => {  // sw.js: a notification tapped
            const cid = e.data && e.data.type === "open" ? conversationOf(e.data.url) : null;
            if (cid) card.open(cid);
          });
        }
      }
    }
    return card;
  }

  // From app.js after each /api/state ({"research": true} when this person
  // may start research).
  function onState(state, win = root, doc = root.document) {
    const c = ensure(win, doc);
    if (!c) return;
    c.enable(Boolean(state && state.research));
    if (!hashDone && win.location) {  // opened from a notification
      hashDone = true;
      const cid = conversationOf(win.location.hash);
      if (cid) {
        if (win.history && win.history.replaceState) win.history.replaceState(null, "", "/");
        c.open(cid);
      }
    }
  }

  // From app.js after each turn: Kwil may have started a job.
  function onDone(ev, win = root, doc = root.document) {
    const c = ensure(win, doc);
    if (c) c.load();
  }

  root.KwilResearch = { configure, onState, onDone, setup, statusText, conversationOf, TEXT };
})(typeof window !== "undefined" ? window : globalThis);
