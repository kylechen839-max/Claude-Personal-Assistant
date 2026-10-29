// Conversation history screen: search, browse by day, open a transcript,
// pin / rename / archive / export / delete, and continue a conversation.
// Uses $, els, show, request, addMessage and setStatus from app.js, and
// window.KwilApp for the chat pane's binding.
"use strict";

const hist = {
  view: $("history-view"), list: $("history-list"), search: $("history-search"),
  filters: $("history-filters"), status: $("history-status"), more: $("history-more"),
  open: $("open-history"), back: $("history-back"),
  tView: $("transcript-view"), tTitle: $("transcript-title"), tMeta: $("transcript-meta"),
  tSummary: $("transcript-summary"), tLog: $("transcript-log"), tBack: $("transcript-back"),
  tActions: $("transcript-actions"), cont: $("continue"), contHint: $("continue-hint"),
  sheet: $("sheet"), sheetTitle: $("sheet-title"), sheetPin: $("sheet-pin"),
  sheetArchive: $("sheet-archive"), rename: $("rename-dialog"), renameInput: $("rename-input"),
  del: $("delete-dialog"), delText: $("delete-text"),
  incognito: $("incognito"), incognitoRow: $("incognito-row"), badge: $("incognito-badge"),
};

// Each source's glyph is a line icon in app.css (.conv-icon[data-source]),
// never an emoji.
const SOURCES = {
  wake: { label: "Spoken to the PC" },
  text: { label: "Typed" },
  web: { label: "Phone" },
  voice: { label: "Phone voice mode" },
};
const PAGE = 20;
let filter = "";
let query = "";
let nextOffset = 0;
let seq = 0;            // ignore responses to older searches
let controller = null;  // aborts the previous search
let items = new Map();  // id -> conversation shown in the list
let current = null;     // the conversation open in the transcript view
let incognitoOn = false;

// --- state from /api/state (called by app.js) ---
function onHistoryState(state) {
  hist.open.hidden = !state.history;
  hist.incognitoRow.hidden = false;
  setIncognito(Boolean(state.incognito));
}

function setIncognito(on) {
  incognitoOn = on;
  hist.incognito.checked = on;
  hist.badge.hidden = !on;
}

hist.incognito.addEventListener("change", async () => {
  const want = hist.incognito.checked;
  try {
    const res = await request("/api/incognito", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on: want }),
    });
    if (!res.ok) throw new Error((await res.json()).error || `Error ${res.status}`);
    setIncognito((await res.json()).on);
    if (want) window.KwilApp.bindPane(null);  // nothing typed goes into a saved one
    els.log.textContent = "";
    addMessage("note", want ? "Incognito: nothing from now on is saved."
                            : "Incognito off: conversations are saved again.");
  } catch (e) {
    setIncognito(!want);
    addMessage("note", e.message || String(e));
  }
});

// --- dates ---
function dayStart(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }

function groupOf(ts) {
  const days = Math.round((dayStart(new Date()) - dayStart(new Date(ts * 1000))) / 86400000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  return days < 7 ? "This week" : "Older";
}

function timeLabel(ts) {
  const d = new Date(ts * 1000);
  const group = groupOf(ts);
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (group === "Today" || group === "Yesterday") return time;
  if (group === "This week") return `${d.toLocaleDateString([], { weekday: "short" })} ${time}`;
  return d.toLocaleDateString([], { month: "short", day: "numeric",
    year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
}

function longDate(ts) {
  return new Date(ts * 1000).toLocaleString([], { weekday: "long", month: "long",
    day: "numeric", hour: "numeric", minute: "2-digit" });
}

// --- building the list (textContent only: no HTML from the server) ---
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function snippetNode(parts) {
  const p = el("p", "conv-snippet");
  for (const [text, match] of parts || []) p.appendChild(match ? el("mark", "", text) : document.createTextNode(text));
  return p;
}

function convNode(c) {
  const row = el("div", "conv");
  row.dataset.id = c.id;
  row.setAttribute("role", "listitem");
  const src = SOURCES[c.source] || { label: c.source };

  const main = el("button", "conv-main");
  main.type = "button";
  const icon = el("span", "conv-icon");
  icon.dataset.source = SOURCES[c.source] ? c.source : "other";
  icon.setAttribute("role", "img");
  icon.setAttribute("aria-label", src.label);
  const body = el("span", "conv-body");
  const top = el("span", "conv-top");
  const title = el("span", "conv-title", c.title);
  top.appendChild(title);
  if (c.pinned) {
    const pin = el("span", "conv-pin");
    pin.setAttribute("role", "img");
    pin.setAttribute("aria-label", "Pinned");
    top.appendChild(pin);
  }
  top.appendChild(el("span", "conv-time", timeLabel(c.updated_at)));
  body.appendChild(top);
  if (c.snippet && c.snippet.length) body.appendChild(snippetNode(c.snippet));
  main.append(icon, body);
  main.setAttribute("aria-label", `${c.title}, ${src.label}, ${timeLabel(c.updated_at)}` +
    (c.pinned ? ", pinned" : ""));
  main.addEventListener("click", () => {
    if (row.dataset.suppress) { delete row.dataset.suppress; return; }
    if (row.classList.contains("open")) { row.classList.remove("open"); return; }
    openTranscript(c.id);
  });

  const moreBtn = el("button", "conv-more icon-btn");
  moreBtn.type = "button";
  moreBtn.setAttribute("aria-label", `Actions for ${c.title}`);
  moreBtn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h.01M12 12h.01M19 12h.01"/></svg>';
  moreBtn.addEventListener("click", () => openSheet(c.id));

  // Revealed by swiping left.
  const actions = el("div", "conv-actions");
  actions.setAttribute("aria-hidden", "true");
  for (const [act, label, cls] of [["pin", c.pinned ? "Unpin" : "Pin", ""],
                                   ["rename", "Rename", ""], ["delete", "Delete", "danger"]]) {
    const b = el("button", cls, label);
    b.type = "button";
    b.tabIndex = -1;
    b.addEventListener("click", () => { row.classList.remove("open"); doAction(act, c.id); });
    actions.appendChild(b);
  }
  row.append(main, moreBtn, actions);
  addGestures(row, c.id);
  return row;
}

// Swipe left for actions, long-press for the action sheet.
function addGestures(row, id) {
  let x0 = 0, y0 = 0, timer = null, dragging = false;
  row.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    x0 = e.clientX; y0 = e.clientY; dragging = false;
    clearTimeout(timer);
    timer = setTimeout(() => { row.dataset.suppress = "1"; openSheet(id); }, 550);
  });
  row.addEventListener("pointermove", (e) => {
    const dx = e.clientX - x0, dy = e.clientY - y0;
    if (Math.abs(dx) > 10 || Math.abs(dy) > 10) clearTimeout(timer);
    if (Math.abs(dx) > 30 && Math.abs(dx) > Math.abs(dy) * 1.5) dragging = true;
    if (dragging) {
      if (dx < -50) { closeSwipes(row); row.classList.add("open"); }
      if (dx > 40) row.classList.remove("open");
    }
  });
  const end = () => {
    clearTimeout(timer);
    if (dragging) row.dataset.suppress = "1";
    dragging = false;
  };
  row.addEventListener("pointerup", end);
  row.addEventListener("pointercancel", end);
  row.addEventListener("contextmenu", (e) => e.preventDefault());
}

function closeSwipes(except) {
  hist.list.querySelectorAll(".conv.open").forEach((r) => { if (r !== except) r.classList.remove("open"); });
}

function render(convs, append) {
  if (!append) { hist.list.textContent = ""; items = new Map(); }
  for (const c of convs) {
    items.set(c.id, c);
    // When searching, results stay in relevance order under one heading.
    const group = query ? "Results" : groupOf(c.updated_at);
    let section = hist.list.lastElementChild;
    if (!section || section.dataset.group !== group) {
      section = el("section", "conv-group");
      section.dataset.group = group;
      const h = el("h2", "", group);
      const list = el("div", "conv-items");
      list.setAttribute("role", "list");
      section.append(h, list);
      hist.list.appendChild(section);
    }
    section.lastElementChild.appendChild(convNode(c));
  }
}

async function load(append) {
  const my = ++seq;
  if (controller) controller.abort();
  controller = new AbortController();
  const params = new URLSearchParams({ limit: PAGE, offset: append ? nextOffset : 0 });
  if (query) params.set("q", query);
  if (filter === "pinned") params.set("pinned", "true");
  else if (filter === "archived") params.set("archived", "true");
  else if (filter) params.set("source", filter);
  hist.list.setAttribute("aria-busy", "true");
  if (!append) hist.status.textContent = "Loading…";
  try {
    const res = await request(`/api/conversations?${params}`, { signal: controller.signal });
    const data = await res.json();
    if (my !== seq) return;
    if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
    render(data.conversations, append);
    nextOffset = data.next_offset;
    hist.more.hidden = nextOffset === null;
    hist.status.textContent = items.size ? "" :
      (query ? `No conversations match “${query}”.` :
        filter ? "Nothing here." : "No saved conversations yet.");
  } catch (e) {
    if (e.name === "AbortError" || my !== seq) return;
    hist.status.textContent = e.message || String(e);
  } finally {
    if (my === seq) hist.list.setAttribute("aria-busy", "false");
  }
}

// Load the next page when the "Load more" button scrolls into view.
if ("IntersectionObserver" in window) {
  new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting) && !hist.more.hidden && nextOffset !== null) load(true);
  }).observe(hist.more);
}
hist.more.addEventListener("click", () => load(true));

let debounce = null;
hist.search.addEventListener("input", () => {
  clearTimeout(debounce);
  debounce = setTimeout(() => { query = hist.search.value.trim(); load(false); }, 250);
});
hist.search.addEventListener("search", () => {  // the clear (x) button / Enter
  clearTimeout(debounce);
  query = hist.search.value.trim();
  load(false);
});

hist.filters.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-filter]");
  if (!b) return;
  filter = b.dataset.filter;
  hist.filters.querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
  load(false);
});

function openHistory() {
  show("history-view");
  $("history-title").focus({ preventScroll: true });
  load(false);
}

hist.open.addEventListener("click", openHistory);
hist.back.addEventListener("click", () => { show("app"); els.text.focus({ preventScroll: true }); });

// --- transcript ---
async function openTranscript(id) {
  try {
    const res = await request(`/api/conversations/${encodeURIComponent(id)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
    current = data;
  } catch (e) {
    hist.status.textContent = e.message || String(e);
    return;
  }
  const src = SOURCES[current.source] || { label: current.source };
  hist.tTitle.textContent = current.title;
  hist.tMeta.textContent = `${src.label} · ${longDate(current.started_at)} · ` +
    `${current.message_count} messages${current.pinned ? " · pinned" : ""}`;
  hist.tSummary.textContent = current.summary || "";
  hist.tSummary.hidden = !current.summary;
  hist.tLog.textContent = "";
  for (const m of current.messages) hist.tLog.appendChild(messageNode(m));
  hist.cont.disabled = incognitoOn;
  hist.contHint.textContent = incognitoOn ? "Turn incognito off to continue it." :
    current.active ? "This is the conversation you're in now." : "";
  show("transcript-view");
  hist.tView.scrollTop = 0;
  hist.tTitle.focus({ preventScroll: true });
}

function messageNode(m) {
  if (m.kind === "note") return el("div", "msg note", m.text);
  if (m.kind === "confirm") {
    return el("div", "msg note", m.role === "assistant" ? `Asked: ${m.text}` : `Answered: ${m.text}`);
  }
  const wrap = el("div", `msg ${m.role === "user" ? "me" : "claude"}`, m.text);
  if (m.interrupted) wrap.appendChild(el("span", "tag", " (interrupted)"));
  if (m.tools && m.tools.length) {
    const t = el("span", "tools", m.tools.map((x) =>
      `${x.tool.replace(/_/g, " ")}${x.status === "ok" ? "" : ` (${x.status})`}`).join(", "));
    wrap.prepend(t);
  }
  return wrap;
}

hist.tBack.addEventListener("click", () => {
  show("history-view");
  load(false);  // titles, pins and order may have changed
});
hist.tActions.addEventListener("click", () => current && openSheet(current.id));

// Continue: read-only. The chat pane shows the conversation and is bound to
// it (app.js); the first message typed there continues it on the server
// (POST /api/turn with its conversation_id), even after it ended.
hist.cont.addEventListener("click", async () => {
  if (!current) return;
  hist.cont.disabled = true;
  try {
    const res = await request(`/api/conversations/${encodeURIComponent(current.id)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
    window.KwilApp.bindPane(data.id);
    els.log.textContent = "";
    const recent = data.messages.filter((m) => m.kind === "chat").slice(-6);
    if (data.messages.length > recent.length) addMessage("note", "…");
    for (const m of recent) addMessage(m.role === "user" ? "me" : "claude", m.text);
    addMessage("note", `Continuing “${data.title}”. Go ahead.`);
    show("app");
    setStatus("");
    els.text.focus({ preventScroll: true });
  } catch (e) {
    hist.contHint.textContent = e.message || String(e);
  } finally {
    hist.cont.disabled = incognitoOn;
  }
});

// --- actions ---
let sheetId = null;
function openSheet(id) {
  const c = items.get(id) || (current && current.id === id ? current : null);
  if (!c) return;
  sheetId = id;
  hist.sheetTitle.textContent = c.title;
  hist.sheetPin.textContent = c.pinned ? "Unpin" : "Pin";
  hist.sheetArchive.textContent = c.archived ? "Unarchive" : "Archive";
  hist.sheet.querySelector('[value="open"]').hidden = !hist.tView.hidden && current && current.id === id;
  if (typeof hist.sheet.showModal === "function") hist.sheet.showModal();
}

hist.sheet.addEventListener("close", () => {
  const act = hist.sheet.returnValue;
  hist.sheet.returnValue = "";
  if (sheetId && act && act !== "cancel") doAction(act, sheetId);
});

function find(id) {
  return items.get(id) || (current && current.id === id ? current : null);
}

async function patch(id, body) {
  const res = await request(`/api/conversations/${encodeURIComponent(id)}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
  if (items.has(id)) items.set(id, { ...items.get(id), ...data });
  if (current && current.id === id) Object.assign(current, data);
  return data;
}

async function doAction(act, id) {
  const c = find(id);
  if (!c) return;
  try {
    if (act === "open") openTranscript(id);
    else if (act === "pin") { await patch(id, { pinned: !c.pinned }); refresh(id); }
    else if (act === "archive") { await patch(id, { archived: !c.archived }); refresh(id); }
    else if (act === "rename") { askRename(id); }
    else if (act === "export") {
      const a = el("a");
      a.href = `/api/conversations/${encodeURIComponent(id)}/export?format=md`;
      a.download = "";
      document.body.appendChild(a);
      a.click();
      a.remove();
    } else if (act === "delete") { askDelete(id); }
  } catch (e) {
    announce(e.message || String(e));
  }
}

function announce(text) {
  if (!hist.tView.hidden) hist.contHint.textContent = text; else hist.status.textContent = text;
}

function refresh(id) {
  if (!hist.tView.hidden && current && current.id === id) {
    hist.tTitle.textContent = current.title;
    openTranscript(id);
  } else {
    load(false);
  }
}

let renameId = null;
function askRename(id) {
  renameId = id;
  hist.renameInput.value = find(id).title;
  hist.rename.returnValue = "";
  hist.rename.showModal();
  hist.renameInput.select();
}
hist.rename.addEventListener("close", async () => {
  const title = hist.renameInput.value.trim();
  if (hist.rename.returnValue !== "save" || !title || !renameId) return;
  try { await patch(renameId, { title }); refresh(renameId); } catch (e) { announce(e.message); }
});

let deleteId = null;
function askDelete(id) {
  deleteId = id;
  hist.delText.textContent = `“${find(id).title}” and its memory summary will be ` +
    "deleted. This can't be undone.";
  hist.del.returnValue = "";
  hist.del.showModal();
  hist.del.querySelector(".cancel").focus();  // the safe choice is the default
}
hist.del.addEventListener("close", async () => {
  if (hist.del.returnValue !== "delete" || !deleteId) return;
  const id = deleteId;
  deleteId = null;
  try {
    // With a body: the server requires a Content-Length on every unsafe request.
    const res = await request(`/api/conversations/${encodeURIComponent(id)}`, {
      method: "DELETE", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
    items.delete(id);
    const shown = window.KwilApp.pane().cid === id;
    window.KwilApp.forgetConversation(id);  // its queued messages and binding
    if (shown) els.log.textContent = "";
    if (current && current.id === id) {
      if (current.active) els.log.textContent = "";
      current = null;
      show("history-view");
    }
    await load(false);
    if (items.size) hist.status.textContent = "Deleted.";
  } catch (e) {
    announce(e.message || String(e));
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!hist.tView.hidden && !document.querySelector("dialog[open]")) hist.tBack.click();
  else if (!hist.view.hidden && !document.querySelector("dialog[open]")) hist.back.click();
});
