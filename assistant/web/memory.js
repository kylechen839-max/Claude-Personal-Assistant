// Memory screen (memory_web.py): what the assistant saved to Kyle's
// long-term memory (conversation summaries and notes), newest first and
// grouped by week, with a badge on notes saved while a web page or email
// was in the conversation. Each one can be deleted, and so can a whole
// week: the page asks first (with the count) and sends exactly the ids it
// shows, so a note saved in the meantime is never deleted unseen.
// The owner's only: "Memory" in the settings menu shows when /api/state
// says {"owner": true, "memory": true}. Text goes in with textContent only.
// app.js calls configure({ request, show }) and onState(state) on every
// /api/state (request goes through the failover router when there is one).
// Loaded before app.js.
"use strict";

(function (root) {
  let request = null;   // app.js's request(url, options) -> Response
  let showView = null;  // app.js's show(view)

  function configure(opts = {}) {
    if (typeof opts.request === "function") request = opts.request;
    if (typeof opts.show === "function") showView = opts.show;
  }

  const KIND = { summary: "Summary", note: "Note" };
  const UNTRUSTED_HINT = "Saved while a web page or email was in the conversation";

  function dayLabel(iso) {  // "2026-10-13" -> "Oct 13" in this phone's language
    const [y, m, d] = String(iso).split("-").map(Number);
    const day = new Date(y, m - 1, d);
    return day.toLocaleDateString([], { month: "short", day: "numeric",
      year: y === new Date().getFullYear() ? undefined : "numeric" });
  }

  function weekLabel(week, data) {
    if (!week) return "Date unknown";
    if (week === data.this_week) return "This week";
    if (week === data.last_week) return "Last week";
    return `Week of ${dayLabel(week)}`;
  }

  function deleteWeekLabel(week, data) {
    if (!week) return "Delete all with no date";
    return week === data.this_week ? "Delete all from this week" : "Delete all from that week";
  }

  function deleteWeekQuestion(week, data, count) {
    const what = `${count} ${count === 1 ? "note" : "notes"}`;
    if (!week) return `Delete all ${what} with no date? This can't be undone.`;
    if (week === data.this_week) return `Delete all ${what} from this week? This can't be undone.`;
    if (week === data.last_week) return `Delete all ${what} from last week? This can't be undone.`;
    return `Delete all ${what} from the week of ${dayLabel(week)}? This can't be undone.`;
  }

  function whenLabel(note) {
    if (!note.created_at) return "Date unknown";
    return new Date(note.created_at).toLocaleString([], { weekday: "short", month: "short",
      day: "numeric", hour: "numeric", minute: "2-digit" });
  }

  function setup(win, doc) {
    const $ = (id) => doc.getElementById(id);
    const view = $("memory-view"), list = $("memory-list"), status = $("memory-status");
    const openBtn = $("memory-open"), back = $("memory-back");
    if (!view || !list || !openBtn) return null;
    let data = null;
    let seq = 0;

    function el(tag, cls, text) {
      const e = doc.createElement(tag);
      if (cls) e.className = cls;
      if (text !== undefined) e.textContent = text;
      return e;
    }

    async function call(url, options = {}) {
      const res = request ? await request(url, options)
        : await win.fetch(url, { credentials: "same-origin", ...options });
      let body = {};
      try { body = await res.json(); } catch (e) { /* not JSON */ }
      if (!res.ok) throw new Error(body.error || `Error ${res.status}`);
      return body;
    }
    const post = (url, payload) => call(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}) });

    function noteNode(note) {
      const row = el("div", "mem-item");
      row.dataset.id = note.id;
      row.setAttribute("role", "listitem");
      const top = el("div", "mem-top");
      top.appendChild(el("span", "mem-when", whenLabel(note)));
      top.appendChild(el("span", "badge", KIND[note.kind] || KIND.summary));
      if (note.untrusted) {
        const flag = el("span", "badge mem-untrusted", "Untrusted");
        flag.setAttribute("title", UNTRUSTED_HINT);
        top.appendChild(flag);
      }
      const del = el("button", "mem-delete danger", "Delete");
      del.type = "button";
      del.addEventListener("click", () => deleteOne(note));
      top.appendChild(del);
      row.appendChild(top);
      row.appendChild(el("p", "mem-text", note.text));
      return row;
    }

    function render() {
      list.textContent = "";
      const weeks = (data && data.weeks) || [];
      status.textContent = weeks.length ? (data.truncated ? "Showing the newest 1000 only." : "")
        : "Nothing saved yet.";
      for (const group of weeks) {
        const section = el("section", "conv-group mem-group");
        section.dataset.week = group.week || "unknown";
        const head = el("div", "mem-head");
        head.appendChild(el("h2", "", weekLabel(group.week, data)));
        const all = el("button", "mem-delete-week danger", deleteWeekLabel(group.week, data));
        all.type = "button";
        all.addEventListener("click", () => deleteWeek(group));
        head.appendChild(all);
        section.appendChild(head);
        const items = el("div", "conv-items");
        items.setAttribute("role", "list");
        for (const note of group.notes) items.appendChild(noteNode(note));
        section.appendChild(items);
        list.appendChild(section);
      }
    }

    async function load() {
      const mine = ++seq;
      list.setAttribute("aria-busy", "true");
      status.textContent = "Loading…";
      try {
        const body = await call("/api/memory");
        if (mine !== seq) return;
        data = body;
        render();
      } catch (e) {
        if (mine === seq) status.textContent = e.message || String(e);
      } finally {
        if (mine === seq) list.setAttribute("aria-busy", "false");
      }
    }

    async function deleteOne(note) {
      if (!win.confirm("Delete this memory? This can't be undone.")) return;
      try {
        // POST, not DELETE: with failover this goes cross-origin, and the
        // backends' preflight allows only GET and POST
        await post(`/api/memory/${encodeURIComponent(note.id)}/delete`);
        await load();
        status.textContent = "Deleted.";
      } catch (e) { status.textContent = e.message || String(e); }
    }

    async function deleteWeek(group) {
      const ids = group.notes.map((n) => n.id);
      if (!ids.length || !win.confirm(deleteWeekQuestion(group.week, data, ids.length))) return;
      try {
        const body = await post("/api/memory/delete-week", { week: group.week || "unknown", ids });
        await load();
        status.textContent = `Deleted ${body.deleted}.`;
      } catch (e) { status.textContent = e.message || String(e); }
    }

    openBtn.addEventListener("click", () => {
      const menu = doc.querySelector("details.menu");
      if (menu) menu.open = false;
      if (showView) showView("memory-view"); else view.hidden = false;
      return load();
    });
    if (back) back.addEventListener("click", () => {
      if (showView) showView("app"); else view.hidden = true;
    });

    return {
      enable(on) { openBtn.hidden = !on; if (!on && !view.hidden && showView) showView("app"); },
      load,
      get data() { return data; },
    };
  }

  let screen = null;
  // From app.js after each /api/state: only the owner, with memory on.
  function onState(state, win = root, doc = root.document) {
    if (!doc) return;
    if (!screen) screen = setup(win, doc);
    if (screen) screen.enable(Boolean(state && state.owner && state.memory));
  }

  root.HeyClaudeMemory = { configure, onState, setup, weekLabel, deleteWeekQuestion, KIND };
})(typeof window !== "undefined" ? window : globalThis);
