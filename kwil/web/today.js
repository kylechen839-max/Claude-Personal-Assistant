// The Today screen (today.py): one read-only look at the signed-in
// person's day, when /api/state says {"today": true}. The header's
// calendar button (#open-today) or /#today opens it, and only then is
// /api/today read (no polling). Sections, in order: the brief (opens
// /#brief), today's and tomorrow's events (primary calendar; "Ask Kwil"),
// what's due by tomorrow and overdue (deadlines and Notion; opens
// Settings), today's reminders (opens /#reminders), the latest research
// (a finished one opens its conversation), and study items to review
// ("Ask Kwil").
// "Ask Kwil" starts a new chat with a question in the composer and
// focuses it; it never sends. A row marked `outside` (an invite, every
// deadline, anything made while outside text was in the conversation)
// never puts its title in that question: it says which item by kind and
// time instead ("What's my 3 PM event today about?").
// Text goes in with textContent only; fixed words are translated here for
// a Chinese page (i18n.js), since lines are put together from parts.
// app.js calls configure({ request, show, newChat, prefill }) and
// onState(state) on every /api/state. Loaded before app.js.
"use strict";

(function (root) {
  let request = null;   // app.js's request(url, options) -> Response
  let showView = null;  // app.js's show(view)
  let newChat = null;   // app.js's newChat(project)
  let prefill = null;   // app.js: put text in the composer, focus it

  function configure(opts = {}) {
    if (typeof opts.request === "function") request = opts.request;
    if (typeof opts.show === "function") showView = opts.show;
    if (typeof opts.newChat === "function") newChat = opts.newChat;
    if (typeof opts.prefill === "function") prefill = opts.prefill;
  }

  const TEXT = {
    title: "Today",
    tomorrow: "Tomorrow",
    brief: "Brief",
    due: "Due",
    reminders: "Reminders",
    research: "Research",
    review: "To review",
    ask: "Ask Kwil",
    loading: "Loading…",
    offline: "Couldn't load Today. Check your connection.",
    retry: "Retry",
    calendarOff: "Connect Google to see your calendar here.",
    connectGoogle: "Connect Google",
    calendarReconnect: "Google needs you to sign in again.",
    reconnectGoogle: "Reconnect Google",
    calendarError: "Couldn't reach your calendar just now.",
    noEventsToday: "Nothing on your calendar today.",
    noEventsTomorrow: "Nothing on your calendar tomorrow.",
    nothingDue: "Nothing due by tomorrow.",
    dueError: "Couldn't load what's due just now.",
    notionReconnect: "Notion needs you to sign in again.",
    openSettings: "Open Settings",
    noReminders: "No reminders today.",
    sectionError: "Couldn't load this just now.",
    overdue: "Overdue",
    allDay: "All day",
    task: "Task",
    researching: "Researching",
    ready: "Ready",
    stopped: "Didn't finish",
    missed: "Missed {n} times",
    // "Ask Kwil": an outside item by kind and time, never its title
    askEventToday: "What's my {time} event today about?",
    askEventTomorrow: "What's my {time} event tomorrow about?",
    askAllDayToday: "What's my all-day event today about?",
    askAllDayTomorrow: "What's my all-day event tomorrow about?",
    askOwnToday: "Tell me about “{title}” at {time} today.",
    askOwnTomorrow: "Tell me about “{title}” at {time} tomorrow.",
    askOwnAllDayToday: "Tell me about “{title}” today.",
    askOwnAllDayTomorrow: "Tell me about “{title}” tomorrow.",
    askStudy: "Quiz me on {title}.",
    askStudyOutside: "Quiz me on item {n} of my review list.",
  };
  const URL_HASH = "#today";
  const SECTIONS = ["brief", "today", "tomorrow", "due", "reminders", "research", "study"];

  // "Ask Kwil"'s question for a row (exported for tests): `outside` rows
  // never carry their title, nor anything else from the item's text.
  function question(kind, item, tr = (s) => s) {
    const fill = (key, values) => tr(TEXT[key]).replace(/\{(\w+)\}/g,
      (all, name) => (name in values ? String(values[name]) : all));
    if (kind === "event") {
      const day = item.day === "tomorrow" ? "Tomorrow" : "Today";
      if (item.outside) {
        return item.all_day || !item.start ? fill(`askAllDay${day}`, {})
          : fill(`askEvent${day}`, { time: item.start });
      }
      return item.all_day || !item.start ? fill(`askOwnAllDay${day}`, { title: item.title })
        : fill(`askOwn${day}`, { title: item.title, time: item.start });
    }
    if (kind === "study") {
      return item.outside ? fill("askStudyOutside", { n: item.position })
        : fill("askStudy", { title: item.title });
    }
    return "";
  }

  function setup(win, doc) {
    const $ = (id) => doc.getElementById(id);
    const view = $("today-view"), openBtn = $("open-today"), back = $("today-back");
    const title = $("today-title"), status = $("today-status"), retry = $("today-retry");
    if (!view || !openBtn) return null;
    const lists = Object.fromEntries(SECTIONS.map((k) => [k, $(`today-${k}`)]));
    const notes = Object.fromEntries(SECTIONS.map((k) => [k, $(`today-${k}-note`)]));
    const secs = Object.fromEntries(SECTIONS.map((k) => [k, $(`today-sec-${k}`)]));
    let data = null, seq = 0, on = false, opened = false;

    const i18n = root.KwilI18n || win.KwilI18n;
    const zh = () => Boolean(i18n && typeof i18n.isActive === "function" && i18n.isActive(doc));
    const tr = (s) => (zh() ? i18n.translate(s) : s);

    function el(tag, cls, text) {
      const e = doc.createElement(tag);
      if (cls) e.className = cls;
      if (text !== undefined) e.textContent = text;
      return e;
    }

    function busy(yes) {
      for (const node of [status, view]) if (node) node.setAttribute("aria-busy", yes ? "true" : "false");
    }

    async function call(url) {
      const res = request ? await request(url)
        : await win.fetch(url, { credentials: "same-origin" });
      let body = {};
      try { body = await res.json(); } catch (e) { /* not JSON */ }
      if (!res.ok) throw new Error(body.error || `Error ${res.status}`);
      return body;
    }

    // ---------- where a row goes ----------
    function leave() {
      if (win.history && win.location && win.location.hash === URL_HASH) {
        win.history.replaceState(null, "", win.location.pathname);
      }
    }
    function goHash(hash) {  // brief.js / reminders.js open on their hash
      leave();
      if (!win.location) return;
      if (win.location.hash === hash && win.history) win.history.replaceState(null, "", win.location.pathname);
      win.location.hash = hash;
    }
    function settings(rowId) {
      leave();
      if (showView) showView("app");
      const menu = doc.querySelector("details.menu");
      if (menu) menu.open = true;
      const row = rowId ? $(rowId) : null;
      if (row && !row.hidden && typeof row.click === "function") row.click();
    }
    function ask(text) {
      leave();
      if (newChat) newChat(null);
      if (prefill) prefill(text); else if (showView) showView("app");
    }
    function transcript(id) {
      leave();
      const open = root.openTranscript || win.openTranscript;
      if (typeof open === "function") open(id);
    }

    // ---------- rows ----------
    function item(row) {  // a list item holding one row (a button keeps its role)
      const li = el("div", "today-item");
      li.setAttribute("role", "listitem");
      li.appendChild(row);
      return li;
    }
    function texts(row, main, sub, subCls) {
      const box = el("span", "today-text");
      box.appendChild(el("span", "today-main", main || ""));
      if (sub) box.appendChild(el("span", `today-sub${subCls ? ` ${subCls}` : ""}`, sub));
      row.appendChild(box);
      return box;
    }
    function opener(main, sub, go, subCls) {
      const row = el("button", "row today-row");
      row.type = "button";
      texts(row, main, sub, subCls);
      row.appendChild(el("span", "row-chevron"));
      row.lastChild.setAttribute("aria-hidden", "true");
      row.addEventListener("click", go);
      return item(row);
    }
    function asker(main, sub, text) {
      const row = el("div", "row today-row");
      texts(row, main, sub);
      const btn = el("button", "btn plain today-ask", tr(TEXT.ask));
      btn.type = "button";
      btn.addEventListener("click", () => ask(text));
      row.appendChild(btn);
      return item(row);
    }
    function plainRow(main, sub, dot) {
      const row = el("div", "row today-row");
      const box = texts(row, main, "");
      if (dot) box.appendChild(dot);
      else if (sub) box.appendChild(el("span", "today-sub", sub));
      return item(row);
    }
    const join = (...parts) => parts.filter(Boolean).join(" · ");

    // ---------- sections ----------
    function note(key, text, action, go) {
      const box = notes[key];
      if (!box) return;
      box.textContent = "";
      box.hidden = !text;
      if (!text) return;
      box.appendChild(el("span", "today-note-text", tr(text)));
      if (action) {
        const btn = el("button", "btn plain today-action", tr(action));
        btn.type = "button";
        btn.addEventListener("click", go);
        box.appendChild(btn);
      }
    }
    function fill(key, rows) {
      const list = lists[key];
      if (!list) return;
      list.textContent = "";
      for (const r of rows) list.appendChild(r);
      list.hidden = !rows.length;
    }
    function shown(key, yes) { if (secs[key]) secs[key].hidden = !yes; }

    function dayHeads(d) {
      const date = (iso) => {
        try {
          const lang = zh() ? "zh-CN" : "en-US";
          return new win.Date(`${iso}T12:00:00`).toLocaleDateString(lang,
            { weekday: "long", month: "long", day: "numeric" });
        } catch (e) { return ""; }
      };
      for (const [key, iso] of [["today", d.date], ["tomorrow", d.tomorrow]]) {
        const sub = $(`today-${key}-date`);
        if (sub) sub.textContent = iso ? date(iso) : "";
      }
    }

    function eventRow(e) {
      const time = e.all_day || !e.start ? tr(TEXT.allDay)
        : e.end ? `${e.start} – ${e.end}` : e.start;
      return asker(e.title, join(time, e.location), question("event", e, tr));
    }

    function render() {
      const d = data || {};
      dayHeads(d);
      // the brief: its text only
      const brief = d.brief || { state: "off", items: [] };
      const text = brief.items && brief.items[0] && brief.items[0].text;
      fill("brief", text ? [opener(text, "", () => goHash("#brief"))] : []);
      note("brief", brief.state === "error" ? TEXT.sectionError : "");
      shown("brief", Boolean(text) || brief.state === "error");

      // the calendar, under the two date headings
      const cal = d.calendar || { state: "off", items: [] };
      const events = cal.items || [];
      const ok = cal.state === "ok";
      fill("today", ok ? events.filter((e) => e.day !== "tomorrow").map(eventRow) : []);
      fill("tomorrow", ok ? events.filter((e) => e.day === "tomorrow").map(eventRow) : []);
      if (cal.state === "off") note("today", TEXT.calendarOff, TEXT.connectGoogle, () => settings("conn-google-head"));
      else if (cal.state === "reconnect") note("today", TEXT.calendarReconnect, TEXT.reconnectGoogle, () => settings("conn-google-head"));
      else if (cal.state === "error") note("today", TEXT.calendarError, TEXT.retry, load);
      else note("today", events.some((e) => e.day !== "tomorrow") ? "" : TEXT.noEventsToday);
      note("tomorrow", ok && !events.some((e) => e.day === "tomorrow") ? TEXT.noEventsTomorrow : "");
      shown("today", true);
      shown("tomorrow", ok);

      // due: the deadline store's, then Notion's; each opens Settings
      const dl = d.deadlines || { state: "off", items: [] };
      const nt = d.notion || { state: "off", items: [] };
      const dueRows = [];
      for (const x of dl.items || []) {
        const when = x.day === "overdue" ? tr(TEXT.overdue)
          : `${tr(x.day === "today" ? TEXT.title : TEXT.tomorrow)} ${x.time || ""}`.trim();
        dueRows.push(opener(x.title, join(x.course, when), () => settings("conn-canvas-head"),
          x.day === "overdue" ? "today-late" : ""));
      }
      for (const x of nt.items || []) {
        dueRows.push(opener(x.title, join(x.course, x.overdue ? tr(TEXT.overdue) : x.due),
          () => settings("conn-notion-head"), x.overdue ? "today-late" : ""));
      }
      fill("due", dueRows.slice(0, 10));
      if (dl.state === "error" || nt.state === "error") note("due", TEXT.dueError);
      else if (nt.state === "reconnect") note("due", TEXT.notionReconnect, TEXT.openSettings, () => settings("conn-notion-head"));
      else note("due", dueRows.length ? "" : TEXT.nothingDue);
      shown("due", dl.state !== "off" || nt.state !== "off");

      // today's reminders
      const rm = d.reminders || { state: "off", items: [] };
      fill("reminders", (rm.items || []).map((r) => opener(r.title,
        join(r.when, r.task ? tr(TEXT.task) : ""), () => goHash("#reminders"))));
      note("reminders", rm.state === "error" ? TEXT.sectionError
        : (rm.items || []).length ? "" : TEXT.noReminders);
      shown("reminders", rm.state !== "off");

      // research: a running one shows a dot; a finished one opens its chat
      const rs = d.research || { state: "off", items: [] };
      fill("research", (rs.items || []).map((j) => {
        const done = j.status === "done" || j.status === "partial";
        const dot = el("span", `state ${j.running ? "today-running" : done ? "on" : "off"}`,
          tr(j.running ? TEXT.researching : done ? TEXT.ready : TEXT.stopped));
        if (done && j.conversation_id) {
          const li = opener(j.title, "", () => transcript(j.conversation_id));
          li.firstChild.firstChild.appendChild(dot);
          return li;
        }
        return plainRow(j.title, "", dot);
      }));
      note("research", rs.state === "error" ? TEXT.sectionError : "");
      shown("research", (rs.items || []).length > 0 || rs.state === "error");

      // study mode's most missed concepts
      const st = d.study || { state: "off", items: [] };
      fill("study", (st.items || []).map((m, i) => asker(m.title,
        join(m.course, m.count > 1 ? tr(TEXT.missed).replace("{n}", String(m.count)) : ""),
        question("study", { ...m, position: i + 1 }, tr))));
      note("study", st.state === "error" ? TEXT.sectionError : "");
      shown("study", (st.items || []).length > 0 || st.state === "error");
    }

    async function load() {
      const me = ++seq;
      busy(true);
      if (status) status.textContent = tr(TEXT.loading);
      if (retry) retry.hidden = true;
      try {
        const body = await call("/api/today");
        if (me !== seq) return;
        data = body;
        render();
        if (status) status.textContent = "";
      } catch (e) {
        if (me !== seq) return;
        if (status) status.textContent = tr(TEXT.offline);
        if (retry) retry.hidden = false;
      } finally {
        if (me === seq) busy(false);
      }
    }

    function open() {
      if (!on) return null;
      const menu = doc.querySelector("details.menu");
      if (menu) menu.open = false;
      if (showView) showView("today-view"); else view.hidden = false;
      if (title && typeof title.focus === "function") title.focus();
      return load();
    }

    openBtn.addEventListener("click", open);
    if (retry) retry.addEventListener("click", load);
    if (back) back.addEventListener("click", () => {
      leave();
      if (showView) showView("app"); else view.hidden = true;
    });
    if (win.addEventListener) {
      win.addEventListener("hashchange", () => { if (win.location.hash === URL_HASH) open(); });
    }

    return {
      enable(yes) {
        on = Boolean(yes);
        openBtn.hidden = !on;
        if (!on && !view.hidden && showView) showView("app");
        // the first /api/state after the app opened at /#today
        if (on && !opened) {
          opened = true;
          if (win.location && win.location.hash === URL_HASH) return open();
        }
        return null;
      },
      load, open,
      get data() { return data; },
    };
  }

  let screen = null;
  function onState(state, win = root, doc = root.document) {
    if (!doc) return null;
    if (!screen) screen = setup(win, doc);
    return screen ? screen.enable(Boolean(state && state.today)) : null;
  }

  root.KwilToday = { configure, onState, setup, question, TEXT };
})(typeof window !== "undefined" ? window : globalThis);
