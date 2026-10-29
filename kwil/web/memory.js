// Memory screen (memory_web.py), two parts:
// - Everyone's own memory (memory_store.py), when /api/state says
//   {"memories": true}: "Your instructions" (a textarea and Save; Kwil
//   follows them from the next conversation), "Things Kwil remembered"
//   (each can be edited in place or deleted, after a yes), "Pause memory"
//   (a switch: nothing saved or used while on), "Export" (a JSON file) and
//   "Forget all" (asks first).
// - The owner's Pinecone notes ([memory] backend = "pinecone"), when it
//   says {"owner": true, "memory": true}: conversation summaries and notes,
//   newest first and grouped by week, with a badge on notes saved while a
//   web page or email was in the conversation. Each one can be deleted, and
//   so can a whole week: the page asks first (with the count) and sends
//   exactly the ids it shows, so a note saved in the meantime is never
//   deleted unseen.
// "Memory" in the settings menu shows when either is on. Text goes in with
// textContent only.
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

  const LOCAL = {
    saved: "Saved.",
    empty: "Nothing remembered yet. Say “remember …” and Kwil saves it here.",
    forgetAll: "Forget everything Kwil remembered, and your instructions? This can't be undone.",
    forgot: "Forgot everything.",
    paused: "Memory paused.",
    resumed: "Memory on.",
    exported: "Export started.",
  };

  function setup(win, doc) {
    const $ = (id) => doc.getElementById(id);
    const view = $("memory-view"), list = $("memory-list"), status = $("memory-status");
    const openBtn = $("memory-open"), back = $("memory-back");
    if (!view || !list || !openBtn) return null;
    const local = $("memory-local"), items = $("memory-items"), itemsEmpty = $("memory-items-empty");
    const instructions = $("memory-instructions"), count = $("memory-instructions-count");
    const saveBtn = $("memory-instructions-save"), pause = $("memory-pause");
    const exportBtn = $("memory-export"), forgetBtn = $("memory-forget-all");
    const localStatus = $("memory-local-status"), notesHead = $("memory-notes-head");
    let data = null, mine = null;
    let seq = 0, localSeq = 0;
    let localOn = false, notesOn = false;
    const say = (text) => { if (localStatus) localStatus.textContent = text; };

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

    // ---------- everyone's own memory ----------
    function counter() {
      if (count && instructions) {
        const max = (mine && mine.limits && mine.limits.instructions) || 4000;
        count.textContent = `${instructions.value.length} / ${max}`;
      }
    }

    function memoryNode(m) {
      const row = el("div", "mem-item");
      row.dataset.id = m.id;
      row.setAttribute("role", "listitem");
      const top = el("div", "mem-top");
      top.appendChild(el("span", "mem-when", whenLabel({ created_at: m.created })));
      if (m.untrusted) {
        const flag = el("span", "badge mem-untrusted", "Untrusted");
        flag.setAttribute("title", UNTRUSTED_HINT);
        top.appendChild(flag);
      }
      const edit = el("button", "mem-edit", "Edit");
      edit.type = "button";
      const del = el("button", "mem-delete danger", "Delete");
      del.type = "button";
      del.addEventListener("click", () => forgetOne(m));
      top.appendChild(edit);
      top.appendChild(del);
      row.appendChild(top);
      const text = el("p", "mem-text", m.text);
      row.appendChild(text);
      let editing = false;
      edit.addEventListener("click", () => {  // edit in place: a field, Save, Cancel
        if (editing) return;
        editing = true;
        const box = el("div", "mem-edit-box");
        const field = el("textarea", "mem-edit-field");
        field.value = m.text;
        field.maxLength = (mine && mine.limits && mine.limits.memory) || 500;
        field.setAttribute("aria-label", "Edit this memory");
        const ok = el("button", "btn secondary mem-edit-save", "Save");
        ok.type = "button";
        const cancel = el("button", "btn plain mem-edit-cancel", "Cancel");
        cancel.type = "button";
        box.appendChild(field); box.appendChild(ok); box.appendChild(cancel);
        text.hidden = true;
        row.appendChild(box);
        cancel.addEventListener("click", () => renderLocal());
        ok.addEventListener("click", async () => {
          try {
            await post(`/api/memories/${encodeURIComponent(m.id)}/update`, { text: field.value });
            await loadLocal();
            say(LOCAL.saved);
          } catch (e) { say(e.message || String(e)); }
        });
      });
      return row;
    }

    function renderLocal() {
      if (!items || !mine) return;
      items.textContent = "";
      for (const m of mine.memories || []) items.appendChild(memoryNode(m));
      if (itemsEmpty) {
        itemsEmpty.textContent = (mine.memories || []).length ? "" : LOCAL.empty;
        itemsEmpty.hidden = Boolean((mine.memories || []).length);
      }
      if (pause) pause.checked = Boolean(mine.paused);
    }

    async function loadLocal() {
      const me = ++localSeq;
      const body = await call("/api/memories");
      if (me !== localSeq) return;
      mine = body;
      if (instructions) {
        instructions.value = body.instructions || "";
        if (body.limits) instructions.maxLength = body.limits.instructions;
      }
      counter();
      renderLocal();
    }

    async function forgetOne(m) {
      if (!win.confirm("Delete this memory? This can't be undone.")) return;
      try {
        await post(`/api/memories/${encodeURIComponent(m.id)}/delete`);
        await loadLocal();
        say("Deleted.");
      } catch (e) { say(e.message || String(e)); }
    }

    if (instructions) instructions.addEventListener("input", counter);
    if (saveBtn) saveBtn.addEventListener("click", async () => {
      try {
        const body = await post("/api/memories/instructions", { text: instructions.value });
        instructions.value = body.instructions;
        counter();
        say(LOCAL.saved);
      } catch (e) { say(e.message || String(e)); }
    });
    if (pause) pause.addEventListener("change", async () => {
      const want = Boolean(pause.checked);
      try {
        await post("/api/memories/pause", { paused: want });
        if (mine) mine.paused = want;
        say(want ? LOCAL.paused : LOCAL.resumed);
      } catch (e) {
        pause.checked = !want;
        say(e.message || String(e));
      }
    });
    if (forgetBtn) forgetBtn.addEventListener("click", async () => {
      if (!win.confirm(LOCAL.forgetAll)) return;
      try {
        await post("/api/memories/forget-all", { confirm: true });
        await loadLocal();
        say(LOCAL.forgot);
      } catch (e) { say(e.message || String(e)); }
    });
    if (exportBtn) exportBtn.addEventListener("click", async () => {
      try {
        const body = await call("/api/memories/export");
        // a file save, not a navigation (as family.js's "Download my data")
        const blob = new win.Blob([JSON.stringify(body, null, 2)], { type: "application/json" });
        const url = win.URL.createObjectURL(blob);
        const a = doc.createElement("a");
        a.href = url;
        a.download = "kwil-memory.json";
        if (doc.body) doc.body.appendChild(a);
        a.click();
        if (a.remove) a.remove();
        win.setTimeout(() => win.URL.revokeObjectURL(url), 60000);
        say(LOCAL.exported);
      } catch (e) { say(e.message || String(e)); }
    });

    // ---------- the owner's Pinecone notes ----------
    async function load() {
      if (local) local.hidden = !localOn;
      if (notesHead) notesHead.hidden = !(localOn && notesOn);
      if (localOn) {
        say("");
        try { await loadLocal(); } catch (e) { say(e.message || String(e)); }
      }
      if (!notesOn) {
        list.textContent = "";
        status.textContent = "";
        return;
      }
      const ticket = ++seq;
      list.setAttribute("aria-busy", "true");
      status.textContent = "Loading…";
      try {
        const body = await call("/api/memory");
        if (ticket !== seq) return;
        data = body;
        render();
      } catch (e) {
        if (ticket === seq) status.textContent = e.message || String(e);
      } finally {
        if (ticket === seq) list.setAttribute("aria-busy", "false");
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
      enable(own, notes) {
        localOn = Boolean(own);
        notesOn = Boolean(notes);
        const on = localOn || notesOn;
        openBtn.hidden = !on;
        if (!on && !view.hidden && showView) showView("app");
      },
      load,
      get data() { return data; },
      get mine() { return mine; },
    };
  }

  let screen = null;
  // From app.js after each /api/state: everyone's own memory ("memories"),
  // and the owner's Pinecone notes ("owner" and "memory").
  function onState(state, win = root, doc = root.document) {
    if (!doc) return;
    if (!screen) screen = setup(win, doc);
    if (screen) screen.enable(Boolean(state && state.memories),
      Boolean(state && state.owner && state.memory));
  }

  root.KwilMemory = { configure, onState, setup, weekLabel, deleteWeekQuestion, KIND, LOCAL };
})(typeof window !== "undefined" ? window : globalThis);
