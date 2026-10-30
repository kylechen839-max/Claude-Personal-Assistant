// Projects (projects_web.py): one space per class, like ChatGPT and Claude
// Projects. When /api/state says {"projects": true}, "Projects" shows in
// the settings menu and opens the list: each project (name, how many files
// and memories), "New project", and "Suggested from your courses" (Canvas,
// deadlines, Notion; Create makes one in a tap, the same one twice makes
// one). A project's page: "New chat in this project" (the one primary),
// its instructions (a textarea and Save; trusted, Kwil follows them in its
// conversations), its files (Add file: one PDF or text file at a time;
// Delete asks first), its memories (Delete asks first), its conversations
// (open one in History), Rename and Delete project (asks; its chats stay).
// The chat header shows the project the chat is in (#project-pill: its
// name opens its page, × leaves it: the next message starts a chat in no
// project). app.js owns the pane: newChat(project) starts a chat in a
// project (or none) and onDone / onState keep the pill right. Text goes in
// with textContent only.
// app.js calls configure({ request, show, newChat, openConversation }),
// onState(state) on every /api/state, and pill(project) whenever the
// pane's project changes. Loaded before app.js.
"use strict";

(function (root) {
  let request = null;      // app.js's request(url, options) -> Response
  let showView = null;     // app.js's show(view)
  let newChat = null;      // app.js: a fresh pane in {id, name} (or null: none)
  let openConversation = null;  // history.js's openTranscript(id)

  function configure(opts = {}) {
    if (typeof opts.request === "function") request = opts.request;
    if (typeof opts.show === "function") showView = opts.show;
    if (typeof opts.newChat === "function") newChat = opts.newChat;
    if (typeof opts.openConversation === "function") openConversation = opts.openConversation;
  }

  const TEXT = {
    empty: "No projects yet. Make one per class: its own instructions, files and chats.",
    suggestionsEmpty: "Connect Canvas or add deadlines, and your courses show here.",
    create: "Create",
    open: "Open",
    newTitle: "New project",
    renameTitle: "Rename project",
    saved: "Saved.",
    deleteAsk: "Delete this project, its files and its memories? Its chats stay in History. This can't be undone.",
    deleted: "Project deleted.",
    fileAsk: "Delete this file from the project? This can't be undone.",
    memoryAsk: "Delete this memory? This can't be undone.",
    uploading: "Adding…",
    added: "Added.",
    filesEmpty: "No files yet. Add a syllabus, notes or handouts (PDF or text).",
    memoriesEmpty: "Nothing remembered here yet. Say “remember …” in one of this project's chats.",
    chatsEmpty: "No chats yet.",
    untitled: "New conversation",
    leave: "Leave project",
    loading: "Loading…",
  };
  // "3 files · 1 memory" (i18n.js has patterns for these)
  const files = (n) => (n === 1 ? "1 file" : `${n} files`);
  const memories = (n) => (n === 1 ? "1 memory" : `${n} memories`);
  const summary = (p) => `${files(Number(p.files) || 0)} · ${memories(Number(p.memories) || 0)}`;
  const size = (bytes) => (bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1e3))} KB`);

  function setup(win, doc) {
    const $ = (id) => doc.getElementById(id);
    const openBtn = $("projects-open"), listView = $("projects-view"), pageView = $("project-view");
    if (!openBtn || !listView || !pageView) return null;
    const list = $("projects-list"), empty = $("projects-empty"), status = $("projects-status");
    const suggestions = $("projects-suggestions"), suggestionsEmpty = $("projects-suggestions-empty");
    const newBtn = $("projects-new"), listBack = $("projects-back");
    const title = $("project-title"), pageBack = $("project-back");
    const chatBtn = $("project-chat"), instructions = $("project-instructions");
    const count = $("project-instructions-count"), saveBtn = $("project-instructions-save");
    const fileList = $("project-files"), filesEmpty = $("project-files-empty");
    const addFile = $("project-add-file"), picker = $("project-file-input");
    const memList = $("project-memories"), memEmpty = $("project-memories-empty");
    const chats = $("project-chats"), chatsEmpty = $("project-chats-empty");
    const renameBtn = $("project-rename"), deleteBtn = $("project-delete");
    const pageStatus = $("project-status");
    const pillEl = $("project-pill"), pillText = $("project-pill-text"), leave = $("project-leave");
    const nameSheet = $("project-name-dialog"), nameTitle = $("project-name-title");
    const nameInput = $("project-name-input");
    let enabled = false, current = null, limits = null, seq = 0;
    const say = (el, text) => { if (el) el.textContent = text || ""; };

    function el(tag, cls, text) {
      const e = doc.createElement(tag);
      if (cls) e.className = cls;
      if (text !== undefined) e.textContent = text;
      return e;
    }
    function button(cls, text, onClick) {
      const b = el("button", cls, text);
      b.type = "button";
      b.addEventListener("click", onClick);
      return b;
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
    const path = (id) => `/api/projects/${encodeURIComponent(id)}`;

    function closeMenu() {
      const menu = doc.querySelector ? doc.querySelector("details.menu") : null;
      if (menu) menu.open = false;
    }
    const go = (view) => { if (showView) showView(view); };

    // ---------- the list ----------
    function projectRow(p) {
      const row = button("row project-row", undefined, () => openProject(p.id));
      row.dataset.id = p.id;
      const text = el("span", "conn-text");
      text.appendChild(el("span", "conn-name", p.name));
      text.appendChild(el("span", "conn-sub", summary(p)));
      row.appendChild(text);
      row.appendChild(el("span", "row-chevron"));
      return row;
    }

    function suggestionRow(s) {
      const row = el("div", "row project-suggestion");
      row.dataset.course = s.course;
      const text = el("span", "conn-text");
      text.appendChild(el("span", "conn-name", s.name));
      row.appendChild(text);
      if (s.project) {
        row.appendChild(button("btn plain", TEXT.open, () => openProject(s.project)));
      } else {
        row.appendChild(button("btn plain", TEXT.create, async () => {
          try {
            const body = await post("/api/projects", { name: s.name, course: s.course });
            await openProject(body.project.id);
          } catch (e) { say(status, e.message || String(e)); }
        }));
      }
      return row;
    }

    async function loadList() {
      const me = ++seq;
      say(status, TEXT.loading);
      try {
        const body = await call("/api/projects");
        if (me !== seq) return;
        limits = body.limits || null;
        list.textContent = "";
        for (const p of body.projects || []) list.appendChild(projectRow(p));
        list.hidden = !(body.projects || []).length;
        say(empty, (body.projects || []).length ? "" : TEXT.empty);
        if (empty) empty.hidden = Boolean((body.projects || []).length);
        say(status, "");
      } catch (e) {
        if (me === seq) say(status, e.message || String(e));
        return;
      }
      try {
        const body = await call("/api/projects/suggestions");
        if (me !== seq || !suggestions) return;
        suggestions.textContent = "";
        const found = body.suggestions || [];
        for (const s of found) suggestions.appendChild(suggestionRow(s));
        suggestions.hidden = !found.length;
        say(suggestionsEmpty, found.length ? "" : TEXT.suggestionsEmpty);
        if (suggestionsEmpty) suggestionsEmpty.hidden = Boolean(found.length);
      } catch (e) { /* suggestions are a bonus */ }
    }

    async function openList() {
      closeMenu();
      go("projects-view");
      return loadList();
    }

    // The name sheet (#project-name-dialog): resolves with the typed name,
    // or null for Cancel / Esc / an empty name.
    function askName(heading, value) {
      return new Promise((resolve) => {
        if (!nameSheet || !nameInput || typeof nameSheet.showModal !== "function") {
          resolve(null);
          return;
        }
        say(nameTitle, heading);
        nameInput.value = value || "";
        nameSheet.returnValue = "";
        const done = () => {
          nameSheet.removeEventListener("close", done);
          const name = String(nameInput.value || "").trim();
          resolve(nameSheet.returnValue === "save" && name ? name : null);
        };
        nameSheet.addEventListener("close", done);
        nameSheet.showModal();
        if (nameInput.select) nameInput.select();
      });
    }

    async function create() {
      const name = await askName(TEXT.newTitle, "");
      if (!name) return;
      try {
        const body = await post("/api/projects", { name });
        await openProject(body.project.id);
      } catch (e) { say(status, e.message || String(e)); }
    }

    // ---------- one project ----------
    function counter() {
      if (count && instructions) {
        const max = (limits && limits.instructions) || 4000;
        count.textContent = `${instructions.value.length} / ${max}`;
      }
    }

    function fileRow(f) {
      const row = el("div", "mem-item project-file");
      row.dataset.id = f.id;
      row.setAttribute("role", "listitem");
      const top = el("div", "mem-top");
      top.appendChild(el("span", "mem-text project-file-name", f.name));
      top.appendChild(el("span", "mem-when", `${f.kind === "pdf" ? "PDF" : "Text"} · ${size(f.size)}`));
      top.appendChild(button("mem-delete danger", "Delete", async () => {
        if (!win.confirm(TEXT.fileAsk)) return;
        try {
          await post(`${path(current.project.id)}/files/${encodeURIComponent(f.id)}/delete`);
          await openProject(current.project.id);
          say(pageStatus, "Deleted.");
        } catch (e) { say(pageStatus, e.message || String(e)); }
      }));
      row.appendChild(top);
      return row;
    }

    function memoryRow(m) {
      const row = el("div", "mem-item");
      row.dataset.id = m.id;
      row.setAttribute("role", "listitem");
      const top = el("div", "mem-top");
      top.appendChild(el("span", "mem-when", new Date(m.created).toLocaleDateString()));
      if (m.untrusted) top.appendChild(el("span", "badge mem-untrusted", "Untrusted"));
      top.appendChild(button("mem-delete danger", "Delete", async () => {
        if (!win.confirm(TEXT.memoryAsk)) return;
        try {
          await post(`/api/memories/${encodeURIComponent(m.id)}/delete`);
          await openProject(current.project.id);
          say(pageStatus, "Deleted.");
        } catch (e) { say(pageStatus, e.message || String(e)); }
      }));
      row.appendChild(top);
      row.appendChild(el("p", "mem-text", m.text));
      return row;
    }

    function chatRow(c) {
      const row = button("row project-chat-row", undefined, () => {
        if (openConversation) openConversation(c.id);
      });
      row.dataset.id = c.id;
      const text = el("span", "conn-text");
      text.appendChild(el("span", "conn-name", c.title || TEXT.untitled));
      text.appendChild(el("span", "conn-sub", new Date(c.updated_at * 1000).toLocaleString([], {
        month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })));
      row.appendChild(text);
      row.appendChild(el("span", "row-chevron"));
      return row;
    }

    function fill(target, emptyEl, items, make, emptyText) {
      if (!target) return;
      target.textContent = "";
      for (const item of items) target.appendChild(make(item));
      target.hidden = !items.length;
      if (emptyEl) {
        emptyEl.textContent = items.length ? "" : emptyText;
        emptyEl.hidden = Boolean(items.length);
      }
    }

    async function openProject(id) {
      const me = ++seq;
      closeMenu();
      go("project-view");
      say(pageStatus, TEXT.loading);
      try {
        const body = await call(path(id));
        if (me !== seq) return;
        current = body;
        say(title, body.project.name);
        if (instructions) {
          instructions.value = body.project.instructions || "";
          instructions.maxLength = (limits && limits.instructions) || 4000;
        }
        counter();
        fill(fileList, filesEmpty, body.files || [], fileRow, TEXT.filesEmpty);
        fill(memList, memEmpty, body.memories || [], memoryRow, TEXT.memoriesEmpty);
        fill(chats, chatsEmpty, body.conversations || [], chatRow, TEXT.chatsEmpty);
        say(pageStatus, "");
      } catch (e) {
        if (me === seq) say(pageStatus, e.message || String(e));
      }
    }

    async function upload(file) {
      if (!current || !file) return;
      const form = new win.FormData();
      form.append("file", file, file.name);
      say(pageStatus, TEXT.uploading);
      try {
        await call(`${path(current.project.id)}/files`, { method: "POST", body: form });
        await openProject(current.project.id);
        say(pageStatus, TEXT.added);
      } catch (e) { say(pageStatus, e.message || String(e)); }
    }

    // ---------- the chat header's pill ----------
    function pill(project) {
      if (!pillEl || !pillText) return;
      const on = enabled && Boolean(project && project.name);
      pillEl.hidden = !on;
      pillText.textContent = on ? project.name : "";
      pillEl.dataset.id = on ? project.id : "";
    }

    // ---------- wiring ----------
    openBtn.addEventListener("click", openList);
    if (listBack) listBack.addEventListener("click", () => go("app"));
    if (pageBack) pageBack.addEventListener("click", openList);
    if (newBtn) newBtn.addEventListener("click", create);
    if (chatBtn) chatBtn.addEventListener("click", () => {
      if (!current) return;
      if (newChat) newChat({ id: current.project.id, name: current.project.name });
      go("app");
    });
    if (instructions) instructions.addEventListener("input", counter);
    if (saveBtn) saveBtn.addEventListener("click", async () => {
      if (!current) return;
      try {
        const body = await post(`${path(current.project.id)}/update`,
          { instructions: instructions.value });
        current.project = body.project;
        instructions.value = body.project.instructions;
        counter();
        say(pageStatus, TEXT.saved);
      } catch (e) { say(pageStatus, e.message || String(e)); }
    });
    if (addFile && picker) {
      addFile.addEventListener("click", () => { picker.value = ""; picker.click(); });
      picker.addEventListener("change", () => upload(picker.files && picker.files[0]));
    }
    if (renameBtn) renameBtn.addEventListener("click", async () => {
      if (!current) return;
      const name = await askName(TEXT.renameTitle, current.project.name);
      if (!name) return;
      try {
        const body = await post(`${path(current.project.id)}/update`, { name });
        current.project = body.project;
        say(title, body.project.name);
        say(pageStatus, TEXT.saved);
      } catch (e) { say(pageStatus, e.message || String(e)); }
    });
    if (deleteBtn) deleteBtn.addEventListener("click", async () => {
      if (!current || !win.confirm(TEXT.deleteAsk)) return;
      try {
        await post(`${path(current.project.id)}/delete`, { confirm: true });
        const gone = current.project.id;
        current = null;
        if (pillEl && pillEl.dataset.id === gone && newChat) newChat(null);
        await openList();
        say(status, TEXT.deleted);
      } catch (e) { say(pageStatus, e.message || String(e)); }
    });
    if (pillText) pillText.addEventListener("click", () => {
      if (pillEl && pillEl.dataset.id) openProject(pillEl.dataset.id);
    });
    if (leave) leave.addEventListener("click", () => { if (newChat) newChat(null); });

    return {
      enable(on) {
        enabled = Boolean(on);
        openBtn.hidden = !enabled;
        if (!enabled) {
          pill(null);
          if (showView && (!listView.hidden || !pageView.hidden)) showView("app");
        }
      },
      pill,
      openList,
      openProject,
      get current() { return current; },
    };
  }

  let screen = null;
  function ensure(win, doc) {
    if (!screen && doc) screen = setup(win, doc);
    return screen;
  }

  // From app.js after each /api/state.
  function onState(state, win = root, doc = root.document) {
    const s = ensure(win, doc);
    if (s) s.enable(Boolean(state && state.projects));
  }

  // From app.js whenever the chat pane's project changes ({id, name} or null).
  function pill(project, win = root, doc = root.document) {
    const s = ensure(win, doc);
    if (s) s.pill(project || null);
  }

  root.KwilProjects = { configure, onState, pill, setup, summary, TEXT };
})(typeof window !== "undefined" ? window : globalThis);
