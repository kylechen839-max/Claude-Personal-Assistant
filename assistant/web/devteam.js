// The PC hub (assistant/devhub.py): one window per coordinator session,
// auto-tiled, with maximize (Esc restores) and hide/restore. Kyle's message
// goes to the server, which queues it into the session with the Claude Code
// CLI; replies, the pickup and the presence row come back from the Notion
// inbox through /api/devteam/state. Everything the server sends is shown
// as text (createElement and textContent only); replies are markdown with
// http(s) links only. Tested in tests/js/devhub.test.mjs.
(() => {
  const LAYOUT_KEY = "heyClaudeHub.layout.v1";
  const LAYOUT_MAX = 8;
  const LAYOUT_RAW_MAX = 16e3;
  const CHAT_MAX = 12000;
  const MD_BUDGET = 2000;   // elements per rendered message; the rest is plain text
  const MD_DEPTH = 6;       // list nesting
  const MD_QUOTE_DEPTH = 4;
  const MD_COLS = 20;
  const MIN_W = 360;
  const GAP = 10;
  const MAX_COLS = 4;
  const HUES = 8;
  const POLL_DEFAULT = 4;
  const QUEUED = "Queued · waiting for the coordinator to pick it up";
  const STEP_MAX = 200;

  const $ = (id) => document.getElementById(id);
  const $wins = $("wins");
  const $count = $("count");
  const $tray = $("tray");
  const $traySum = $("tray-sum");
  const $trayList = $("tray-list");
  const $showAll = $("showall");
  const $login = $("login");
  const $token = $("token");
  const $loginBtn = $("login-go");
  const $loginHint = $("login-hint");
  const $note = $("note");
  const $live = $("live");

  let windows = null;        // the last /api/devteam/state windows
  let pollSeconds = POLL_DEFAULT;
  let maxText = CHAT_MAX;
  let layout = null;         // session -> {h: 1}
  let maximized = null;      // session of the maximized window (memory only)
  let timer = null;
  let polling = false;
  const tiles = Object.create(null);    // session -> window, kept while hidden
  const local = Object.create(null);    // session -> Kyle's message in flight

  // ---- coercion: every field from the server may be missing or wrong ----
  function text(v, max, lines) {
    let s = "";
    if (typeof v === "string") s = v.trim();
    else if (typeof v === "number" && Number.isFinite(v)) s = String(v);
    else if (typeof v === "boolean") s = String(v);
    s = lines ? s.replace(/\r\n?/g, "\n").replace(/[\u0000-\u0008\u000b-\u001f\u007f]+/g, " ")
      : s.replace(/[\u0000-\u001f\u007f]+/g, " ");
    return s.length > max ? s.slice(0, max - 1) + "…" : s;
  }
  function el(tag, cls, content) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (content !== undefined) e.textContent = content;
    return e;
  }
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  function setText(e, v) { if (e.textContent !== v) e.textContent = v; }
  function announce(msg) { $live.textContent = msg; }
  function focusEl(e) { try { e.focus(); } catch (_) { /* not focusable */ } }
  function since(s) {
    const m = Math.max(0, Math.floor((Number(s) || 0) / 60));
    if (m < 1) return "just now";
    return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`;
  }
  function hueOf(id) {
    let h = 0;
    for (let i = 0; i < id.length; i += 1) h = (h * 31 + id.charCodeAt(i)) >>> 0;
    return h % HUES;
  }

  // ---- the presence pill and the delivery stages ----
  function presenceLine(p) {
    const st = p && typeof p === "object" ? p.state : "";
    const what = text(p && p.text, 80);
    if (st === "task") return {state: "task", text: `In task${what ? `: ${what}` : ""} · ${since(p.age)}`};
    if (st === "maybe") return {state: "maybe", text: `In task (no update for ${since(p.age)})`};
    if (st === "idle") return {state: "idle", text: "Idle"};
    return {state: "unknown", text: "Status unknown"};
  }

  // Under Kyle's latest message: sending, not sent, queued, working,
  // blocked; nothing once it is answered.
  function stageLine(m) {
    const st = m ? m.stage : "";
    if (st === "sending") return {stage: "sending", text: "Sending…", spin: true};
    if (st === "not sent") return {stage: "failed", text: "Not sent: " + (text(m.error, 300) || "unknown error")};
    if (st === "queued") return {stage: "queued", text: QUEUED, spin: true};
    if (st === "working") {
      const step = text(m.step, STEP_MAX);
      return {stage: "working", text: "Coordinator · working" + (step ? `: ${step}` : ""), spin: true};
    }
    if (st === "blocked") return {stage: "failed", text: "Blocked: see the coordinator's reply"};
    return null;
  }

  // ---- layout: hidden windows, per viewer, in localStorage ----
  function loadLayout() {
    const out = Object.create(null);
    let raw = null;
    try { raw = window.localStorage.getItem(LAYOUT_KEY); } catch (_) { raw = null; }
    if (typeof raw !== "string" || raw.length > LAYOUT_RAW_MAX) return out;
    let saved = null;
    try { saved = JSON.parse(raw); } catch (_) { return out; }
    const tilesIn = saved && saved.v === 1 && saved.tiles && typeof saved.tiles === "object"
      && !Array.isArray(saved.tiles) ? saved.tiles : null;
    if (!tilesIn) return out;
    let n = 0;
    for (const id of Object.keys(tilesIn)) {
      if (n >= LAYOUT_MAX) break;
      const v = tilesIn[id];
      if (id.length > 220 || !v || typeof v !== "object" || v.h !== 1) continue;
      out[id] = {h: 1};
      n += 1;
    }
    return out;
  }
  function saveLayout() {
    const out = Object.create(null);
    const known = windows ? windows.map((w) => w.session) : Object.keys(layout);
    let n = 0;
    for (const id of known) {
      if (n >= LAYOUT_MAX) break;
      if (layout[id] && layout[id].h === 1 && !has(out, id)) { out[id] = {h: 1}; n += 1; }
    }
    layout = out;
    try { window.localStorage.setItem(LAYOUT_KEY, JSON.stringify({v: 1, tiles: out})); } catch (_) { /* storage blocked */ }
  }
  function isHidden(id) { return !!(layout && layout[id] && layout[id].h === 1); }
  function hiddenIds() { return windows ? windows.map((w) => w.session).filter(isHidden) : []; }

  function setHidden(t, hide) {
    if (layout === null) layout = loadLayout();
    if (hide) {
      layout[t.id] = {h: 1};
      t.seenReplies = t.replies;
      if (maximized === t.id) maximized = null;
    } else {
      delete layout[t.id];
      t.stick = true;
    }
    saveLayout();
    render();
    if (hide) {
      announce(`Hid ${t.title}. It is under Hidden in the toolbar.`);
      focusEl($traySum);
    } else {
      announce(`Restored ${t.title}.`);
      focusEl(t.input);
    }
  }
  function showAll() {
    if (layout === null) layout = loadLayout();
    const n = hiddenIds().length;
    for (const id of Object.keys(layout)) delete layout[id];
    saveLayout();
    render();
    announce(n ? `Showing all ${windows ? windows.length : 0} windows.` : "Nothing was hidden.");
  }

  // ---- maximize: one window fills the workspace ----
  function setMax(t) {
    const prev = maximized;
    maximized = t && maximized !== t.id ? t.id : null;
    render();
    if (maximized) { focusEl(t.restore); announce(`Maximized ${t.title}. Esc or Restore goes back.`); }
    else if (prev && tiles[prev]) { focusEl(tiles[prev].max); announce("Restored."); }
  }

  // ---- windows: built once per session and updated in place ----
  function makeTile(w) {
    const id = w.session;
    const t = {id, el: el("article", "tile"), head: el("div", "tile-head"), chip: el("span", "chip"),
      h2: el("h2"), pres: el("span", "presence"), presText: el("span", "ptext"), qbadge: el("span", "qbadge", "Queued"),
      acts: el("div", "wacts"), open: el("a", "", "Open session"), clear: el("button", "clear", "Clear"),
      hide: el("button", "hide", "–"), max: el("button", "max", "⤢"), restore: el("button", "restore", "Restore"),
      log: el("div", "log"), empty: el("p", "empty", "No messages yet. Type below to message the coordinator."),
      dline: el("div", "dline"), dspin: el("span", "spin", "✻"), dtext: el("span", "dtext"),
      form: el("div", "composer"), input: el("textarea"), send: el("button", "send", "↑"), hint: el("p", "hint"),
      bubbles: Object.create(null), title: "", replies: 0, seenReplies: 0, stick: true, draft: ""};
    t.el.dataset.session = id;
    t.el.dataset.hue = String(hueOf(id));
    for (const b of [t.clear, t.hide, t.max, t.restore, t.send]) b.type = "button";
    t.chip.setAttribute("aria-hidden", "true");
    const pdot = el("span", "pdot");
    pdot.setAttribute("aria-hidden", "true");
    t.pres.setAttribute("role", "status");
    t.pres.append(pdot, t.presText);
    t.open.href = String(w.url || "");
    t.open.target = "_blank";
    t.open.rel = "noopener noreferrer";
    t.qbadge.hidden = true;
    t.restore.hidden = true;
    t.acts.append(t.open, t.clear, t.hide, t.max);
    t.head.append(t.chip, t.h2, t.pres, t.qbadge, t.acts, t.restore);
    t.log.setAttribute("role", "log");
    t.log.setAttribute("aria-label", "Conversation with the coordinator");
    t.dline.setAttribute("role", "status");
    t.dspin.setAttribute("aria-hidden", "true");
    t.dline.append(t.dspin, t.dtext);
    t.input.rows = 2;
    t.input.placeholder = "Message the coordinator…";
    t.input.setAttribute("aria-label", "Message the coordinator");
    t.send.setAttribute("aria-label", "Send");
    t.send.title = "Send (Enter; Shift+Enter for a new line)";
    t.form.append(t.input, t.send);
    t.hint.setAttribute("aria-live", "polite");
    t.hint.hidden = true;
    t.el.append(t.head, t.log, t.form, t.hint);
    t.input.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
      if (e.preventDefault) e.preventDefault();
      send(t);
    });
    t.send.addEventListener("click", () => send(t));
    t.hide.addEventListener("click", () => setHidden(t, true));
    t.max.addEventListener("click", () => setMax(t));
    t.restore.addEventListener("click", () => setMax(null));
    t.clear.addEventListener("click", () => clearWindow(t));
    return t;
  }

  function bubble(m) {
    const b = el("div", "msg");
    b.dataset.from = m.from;
    if (m.status === "blocked") b.dataset.kind = "blocked";
    b.append(m.from === "kyle" ? el("div", "plain", m.text) : renderMarkdown(m.text));
    return b;
  }

  function nearBottom(e) { return e.scrollHeight - e.scrollTop - e.clientHeight <= 40; }

  function fillTile(t, w) {
    const tail = w.session.replace(/^[a-z]+_/, "");
    t.title = `Coordinator ${tail.slice(0, 8)}`;
    setText(t.h2, t.title);
    t.hide.setAttribute("aria-label", `Hide ${t.title}`);
    t.hide.title = "Hide (restore it from Hidden in the toolbar)";
    t.max.setAttribute("aria-label", `Maximize ${t.title}`);
    t.max.title = "Maximize (Esc restores)";
    t.restore.setAttribute("aria-label", `Restore ${t.title}`);
    t.clear.setAttribute("aria-label", `Clear the conversation in ${t.title}`);
    const pr = presenceLine(w.presence);
    t.pres.dataset.state = pr.state;
    setText(t.presText, pr.text);
    const msgs = (Array.isArray(w.messages) ? w.messages : []).filter((m) => m && typeof m === "object").map((m) => ({
      from: m.from === "kyle" ? "kyle" : "coordinator",
      text: m.from === "kyle" ? text(m.text, CHAT_MAX, true) : text(m.text, CHAT_MAX, true),
      at: text(m.at, 40), stage: text(m.stage, 20), error: m.error, step: m.step, status: text(m.status, 20),
    }));
    const mine = local[w.session];
    if (mine) msgs.push(mine);
    t.replies = msgs.filter((m) => m.from === "coordinator").length;
    let lastKyle = null;
    for (const m of msgs) if (m.from === "kyle") lastKyle = m;
    t.blocked = !!(lastKyle && lastKyle.stage === "blocked");
    t.qbadge.hidden = !(w.queued || (mine && mine.stage === "queued"));
    const list = [];
    const next = Object.create(null);
    msgs.forEach((m, i) => {
      const key = i + "\u0001" + m.from + "\u0001" + m.at + "\u0001" + m.text;
      const b = t.bubbles[key] || {el: bubble(m)};
      next[key] = b;
      list.push(b.el);
      if (m === lastKyle) {
        const line = stageLine(m);
        if (line) {
          t.dline.dataset.stage = line.stage;
          t.dspin.hidden = !line.spin;
          setText(t.dtext, line.text);
          list.push(t.dline);
        }
      }
    });
    t.bubbles = next;
    if (!list.length) list.push(t.empty);
    const kids = t.log.children;
    const same = kids.length === list.length && list.every((e, i) => kids[i] === e);
    if (!same) {
      const stick = t.stick || nearBottom(t.log);
      t.log.replaceChildren(...list);
      if (stick) t.log.scrollTop = t.log.scrollHeight;
    } else if (t.stick) t.log.scrollTop = t.log.scrollHeight;
    t.stick = false;
    t.send.disabled = !!(mine && mine.stage === "sending");
    t.input.maxLength = maxText;
  }

  function cols(n, width) {
    let c = Math.max(1, Math.min(MAX_COLS, Math.ceil(Math.sqrt(Math.max(1, n)))));
    if (typeof width === "number" && Number.isFinite(width) && width > 0) {
      c = Math.min(c, Math.max(1, Math.floor((width + GAP) / (MIN_W + GAP))));
    }
    return c;
  }

  function trayEntry(t) {
    const b = el("button", "tray-item");
    b.type = "button";
    const chip = el("span", "chip");
    chip.setAttribute("aria-hidden", "true");
    b.dataset.hue = t.el.dataset.hue;
    b.append(chip, el("span", "", t.title));
    let mark = "";
    if (t.blocked) mark = "blocked";
    else if (t.replies > t.seenReplies) mark = "new reply";
    if (mark) b.append(el("span", "mark", mark));
    b.setAttribute("aria-label", `Restore ${t.title}${mark ? ` (${mark})` : ""}`);
    b.addEventListener("click", () => setHidden(t, false));
    return b;
  }

  function render() {
    if (windows === null) return;
    if (layout === null) layout = loadLayout();
    const shown = [];
    const hidden = [];
    for (const w of windows) {
      const t = tiles[w.session] || (tiles[w.session] = makeTile(w));
      fillTile(t, w);
      (isHidden(w.session) ? hidden : shown).push(t);
    }
    for (const id of Object.keys(tiles)) if (!windows.some((w) => w.session === id)) delete tiles[id];
    if (maximized !== null && !shown.some((t) => t.id === maximized)) maximized = null;
    let width = 0;
    try { width = $wins.getBoundingClientRect().width; } catch (_) { width = 0; }
    $wins.dataset.cols = String(cols(shown.length, width));
    if (maximized !== null) $wins.dataset.max = ""; else delete $wins.dataset.max;
    for (const t of shown) {
      const isMax = t.id === maximized;
      if (isMax) t.el.dataset.max = ""; else delete t.el.dataset.max;
      t.max.hidden = isMax;
      t.restore.hidden = !isMax;
    }
    const kids = $wins.children;
    if (!(kids.length === shown.length && shown.every((t, i) => kids[i] === t.el))) $wins.replaceChildren(...shown.map((t) => t.el));
    $tray.hidden = !hidden.length;
    setText($traySum, `Hidden (${hidden.length})`);
    $trayList.replaceChildren(...hidden.map(trayEntry));
    const n = shown.length;
    setText($count, windows.length ? `${n} window${n === 1 ? "" : "s"}${hidden.length ? ` · ${hidden.length} hidden` : ""}`
      : "No coordinator sessions set ([devteam] hub_sessions).");
  }

  // ---- talking to the server ----
  async function api(path, body) {
    const opts = {credentials: "same-origin", headers: {}};
    if (body !== undefined) {
      opts.method = "POST";
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(path, opts);
    let data = {};
    try { data = await r.json(); } catch (_) { data = {}; }
    return {status: r.status, data: data && typeof data === "object" ? data : {}};
  }

  function showLogin(on) {
    $login.hidden = !on;
    $wins.hidden = on;
    if (on) focusEl($token);
  }

  async function poll() {
    if (polling) return;
    polling = true;
    try {
      const {status, data} = await api("/api/devteam/state");
      if (status === 401) { showLogin(true); return; }
      if (status !== 200) { setText($note, "The hub isn't answering. Is Hey Claude's web app running?"); $note.hidden = false; return; }
      showLogin(false);
      windows = (Array.isArray(data.windows) ? data.windows : [])
        .filter((w) => w && typeof w.session === "string" && /^[a-z]+_[A-Za-z0-9]+$/.test(w.session)).slice(0, LAYOUT_MAX);
      const p = Number(data.poll_seconds);
      if (Number.isFinite(p) && p >= 1) pollSeconds = p;
      const mt = Number(data.max_text);
      if (Number.isFinite(mt) && mt > 0) maxText = mt;
      const err = text(data.notion_error, 300);
      setText($note, err);
      $note.hidden = !err;
      render();
    } catch (_) {
      setText($note, "The hub isn't answering. Is Hey Claude's web app running?");
      $note.hidden = false;
    } finally {
      polling = false;
    }
  }

  // Polls only while the page is visible.
  function schedule() {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    if (document.hidden) return;
    timer = setTimeout(async () => { timer = null; await poll(); schedule(); }, pollSeconds * 1000);
  }

  // Only the Send button and Enter call this; one message in flight per window.
  async function send(t) {
    const draft = t.input.value;
    const body = draft.trim();
    if (!body || (local[t.id] && local[t.id].stage === "sending")) return;
    local[t.id] = {from: "kyle", text: body, at: new Date(Date.now()).toISOString(), stage: "sending"};
    t.input.value = "";   // cleared at once; a failure puts the draft back
    t.hint.hidden = true;
    t.stick = true;
    render();
    let res = null;
    try { res = await api("/api/devteam/send", {session: t.id, text: body}); } catch (_) { res = null; }
    delete local[t.id];
    const ok = res && res.status === 200;
    if (!ok) {
      if (!t.input.value) t.input.value = draft;
      const why = !res ? "The hub isn't answering." : res.status === 409 ? "One message at a time: wait for the last one."
        : res.status === 401 ? "Signed out. Sign in again." : text(res.data.error, 300) || "Not sent.";
      setText(t.hint, why);
      t.hint.hidden = false;
      t.hint.dataset.level = "bad";
      if (res && res.status === 401) showLogin(true);
    }
    await poll();
    render();
  }

  async function clearWindow(t) {
    let sure = false;
    try { sure = window.confirm(`Delete the conversation in ${t.title} from this PC? The coordinator's session keeps its own copy.`); } catch (_) { sure = false; }
    if (!sure) return;
    await api("/api/devteam/clear", {session: t.id});
    t.bubbles = Object.create(null);
    await poll();
    announce("Conversation cleared.");
  }

  async function login() {
    const token = $token.value;
    if (!token) return;
    let res = null;
    try { res = await api("/api/login", {token}); } catch (_) { res = null; }
    if (res && res.status === 200) {
      $token.value = "";
      $loginHint.hidden = true;
      await poll();
      return;
    }
    setText($loginHint, res && res.status === 429 ? "Too many tries. Wait a minute." : "That token didn't work.");
    $loginHint.hidden = false;
  }

  // ---- markdown: a linear scanner; createElement and textContent only ----
  // Blocks: headings (h3-h6), ``` fences, > quotes, -/*/+ and 1. lists
  // nested by indent (up to MD_DEPTH), pipe tables (up to MD_COLS columns)
  // and rules. Inline: `code`, **bold**, *em*/_em_ and [text](http(s) URL).
  // Past MD_BUDGET elements the rest of the message is plain text.
  const LIST_RE = /^([ \t]*)([-*+]|\d{1,9}[.)])[ \t]+(.*)$/;

  function renderMarkdown(src) {
    const root = el("div", "md");
    const lines = text(src, CHAT_MAX, true).split("\n");
    mdBlocks(lines, root, {n: 0}, 0);
    return root;
  }

  function mk(st, tag, cls, content) { st.n += 1; return el(tag, cls, content); }

  function indentOf(s) {
    let w = 0;
    for (let i = 0; i < s.length; i += 1) {
      if (s[i] === " ") w += 1; else if (s[i] === "\t") w += 4; else break;
    }
    return w;
  }
  function isRule(line) {
    const s = line.replace(/[ \t]/g, "");
    return s.length >= 3 && (/^-+$/.test(s) || /^\*+$/.test(s) || /^_+$/.test(s));
  }
  function isTableSep(line) { return line.includes("|") && line.includes("-") && /^[ \t|:-]+$/.test(line); }
  function isFence(line) { return /^ {0,3}(`{3,}|~{3,})/.exec(line); }
  function isHeading(line) { return /^ {0,3}(#{1,6})[ \t]+/.exec(line); }
  function isQuote(line) { return /^ {0,3}>/.test(line); }
  function startsBlock(lines, i) {
    const line = lines[i];
    return !!(isFence(line) || isHeading(line) || isQuote(line) || isRule(line) || LIST_RE.test(line)
      || (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])));
  }

  function cellsOf(line) {
    let s = line.trim();
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
    const out = [];
    let cur = "";
    for (let i = 0; i < s.length; i += 1) {
      if (s[i] === "\\" && s[i + 1] === "|") { cur += "|"; i += 1; continue; }
      if (s[i] === "|") {
        if (out.length >= MD_COLS - 1) { cur += s.slice(i); break; }
        out.push(cur.trim()); cur = ""; continue;
      }
      cur += s[i];
    }
    out.push(cur.trim());
    return out.slice(0, MD_COLS);
  }

  function mdBlocks(lines, out, st, depth) {
    let i = 0;
    while (i < lines.length) {
      if (st.n >= MD_BUDGET) { out.append(mk(st, "p", "md-rest", lines.slice(i).join("\n"))); return; }
      const line = lines[i];
      if (!line.trim()) { i += 1; continue; }
      let m = isFence(line);
      if (m) {
        let j = i + 1;
        while (j < lines.length && !lines[j].trimStart().startsWith(m[1])) j += 1;
        const pre = mk(st, "pre");
        const lang = line.slice(m[0].length).trim().split(/\s/)[0];
        if (/^[A-Za-z0-9_+#.-]{1,20}$/.test(lang)) pre.dataset.lang = lang;  // shown by CSS attr(), as text
        pre.append(mk(st, "code", "", lines.slice(i + 1, j).join("\n")));
        out.append(pre);
        i = j + 1;
        continue;
      }
      m = isHeading(line);
      if (m) {
        let body = line.slice(m[0].length);
        let end = body.length;
        while (end > 0 && (body[end - 1] === "#" || body[end - 1] === " " || body[end - 1] === "\t")) end -= 1;
        body = body.slice(0, end);
        const h = mk(st, "h" + Math.min(6, m[1].length + 2));
        inline(body, h, st, 0);
        out.append(h);
        i += 1;
        continue;
      }
      if (isRule(line)) { out.append(mk(st, "hr")); i += 1; continue; }
      if (isQuote(line)) {
        const inner = [];
        while (i < lines.length && isQuote(lines[i])) {
          inner.push(lines[i].replace(/^ {0,3}> ?/, ""));
          i += 1;
        }
        const q = mk(st, "blockquote");
        if (depth + 1 >= MD_QUOTE_DEPTH) q.append(mk(st, "p", "md-rest", inner.join("\n")));
        else mdBlocks(inner, q, st, depth + 1);
        out.append(q);
        continue;
      }
      if (LIST_RE.test(line)) { i = mdList(lines, i, out, st); continue; }
      if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) { i = mdTable(lines, i, out, st); continue; }
      const p = mk(st, "p");
      let first = true;
      while (i < lines.length && lines[i].trim() && (first || !startsBlock(lines, i))) {
        if (st.n >= MD_BUDGET) { out.append(p, mk(st, "p", "md-rest", lines.slice(i).join("\n"))); return; }
        if (!first) p.append(mk(st, "br"));
        inline(lines[i].trim(), p, st, 0);
        first = false;
        i += 1;
      }
      out.append(p);
    }
  }

  function mdList(lines, i, out, st) {
    const stack = [];  // {indent, ordered, list, li, parent}
    const open = (parent, ordered, indent, m) => {
      const list = mk(st, ordered ? "ol" : "ul");
      const n = ordered ? parseInt(m[2], 10) : 1;
      if (ordered && n !== 1 && Number.isFinite(n)) list.setAttribute("start", String(n));
      parent.append(list);
      return {indent, ordered, list, li: null, parent};
    };
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) {
        let j = i + 1;
        while (j < lines.length && !lines[j].trim()) j += 1;
        if (j < lines.length && !isRule(lines[j]) && (LIST_RE.test(lines[j]) || /^[ \t]{2,}\S/.test(lines[j]))) { i = j; continue; }
        break;
      }
      const m = isRule(line) ? null : LIST_RE.exec(line);
      if (!m) {
        const top = stack[stack.length - 1];
        if (!top || !top.li || !/^[ \t]{2,}\S/.test(line) || st.n >= MD_BUDGET) break;
        top.li.append(mk(st, "br"));
        inline(line.trim(), top.li, st, 0);
        i += 1;
        continue;
      }
      if (st.n >= MD_BUDGET) break;
      const indent = indentOf(m[1]);
      const ordered = /\d/.test(m[2]);
      while (stack.length > 1 && indent < stack[stack.length - 1].indent) stack.pop();
      let top = stack[stack.length - 1];
      if (!top) stack.push(top = open(out, ordered, indent, m));
      else if (indent > top.indent && top.li && stack.length < MD_DEPTH) stack.push(top = open(top.li, ordered, indent, m));
      else if (top.ordered !== ordered && indent <= top.indent) {
        stack.pop();
        stack.push(top = open(top.parent, ordered, top.indent, m));
      }
      // Deeper than MD_DEPTH: the item joins the deepest list (flattened).
      const li = mk(st, "li");
      inline(m[3], li, st, 0);
      top.list.append(li);
      top.li = li;
      i += 1;
    }
    return i;
  }

  function mdTable(lines, i, out, st) {
    const head = cellsOf(lines[i]);
    const wrap = mk(st, "div", "tablewrap");
    const table = mk(st, "table");
    const thead = mk(st, "thead");
    const tr = mk(st, "tr");
    for (const c of head) { const th = mk(st, "th"); inline(c, th, st, 0); tr.append(th); }
    thead.append(tr);
    table.append(thead);
    const tbody = mk(st, "tbody");
    i += 2;
    while (i < lines.length && lines[i].trim() && lines[i].includes("|") && st.n < MD_BUDGET) {
      const cells = cellsOf(lines[i]);
      const row = mk(st, "tr");
      for (let k = 0; k < head.length; k += 1) {
        const td = mk(st, "td");
        if (k < cells.length) inline(cells[k], td, st, 0);
        row.append(td);
      }
      tbody.append(row);
      i += 1;
    }
    table.append(tbody);
    wrap.append(table);
    out.append(wrap);
    return i;
  }

  // indexOf with a memo per token: scanning left to right, each token's
  // next occurrence is searched for once, so hostile input stays linear.
  function finder(s) {
    const memo = Object.create(null);
    return (tok, from) => {
      const m = memo[tok];
      if (m && from >= m.from && (m.at === -1 || m.at >= from)) return m.at;
      const at = s.indexOf(tok, from);
      memo[tok] = {from, at};
      return at;
    };
  }

  function inline(s, out, st, depth) {
    const find = finder(s);
    let buf = "";
    const flush = () => { if (buf) { out.append(mk(st, "span", "", buf)); buf = ""; } };
    let i = 0;
    while (i < s.length) {
      if (st.n >= MD_BUDGET) { buf += s.slice(i); break; }
      const ch = s[i];
      if (ch === "\\" && i + 1 < s.length && "\\`*_[]()#>|~-".includes(s[i + 1])) { buf += s[i + 1]; i += 2; continue; }
      if (ch === "`") {
        let k = i;
        while (s[k] === "`") k += 1;
        const tick = s.slice(i, k);
        const end = find(tick, k);
        if (end < 0) { buf += tick; i = k; continue; }
        flush();
        out.append(mk(st, "code", "", s.slice(k, end)));
        i = end + tick.length;
        continue;
      }
      if (ch === "*" && s[i + 1] === "*") {
        const end = depth < 3 ? find("**", i + 2) : -1;
        if (end > i + 2) {
          flush();
          const b = mk(st, "strong");
          inline(s.slice(i + 2, end), b, st, depth + 1);
          out.append(b);
          i = end + 2;
          continue;
        }
        buf += "**";
        i += 2;
        continue;
      }
      if (ch === "*" || ch === "_") {
        const opens = depth < 3 && i + 1 < s.length && !/\s/.test(s[i + 1])
          && (ch === "*" || i === 0 || !/[A-Za-z0-9]/.test(s[i - 1]));
        const end = opens ? find(ch, i + 1) : -1;
        if (end > i + 1 && !/\s/.test(s[end - 1]) && (ch === "*" || !/[A-Za-z0-9]/.test(s[end + 1] || ""))) {
          flush();
          const em = mk(st, "em");
          inline(s.slice(i + 1, end), em, st, depth + 1);
          out.append(em);
          i = end + 1;
          continue;
        }
        buf += ch;
        i += 1;
        continue;
      }
      if (ch === "[") {
        const close = find("]", i + 1);
        const paren = close > i && s[close + 1] === "(" ? find(")", close + 2) : -1;
        if (paren > close) {
          const label = s.slice(i + 1, close);
          const url = s.slice(close + 2, paren).trim();
          if (/^https?:\/\//i.test(url) && url.length <= 2048 && !/\s/.test(url)) {
            flush();
            const a = mk(st, "a", "", label || url);
            a.href = url;
            a.target = "_blank";
            a.rel = "noopener noreferrer";
            out.append(a);
            i = paren + 1;
            continue;
          }
        }
        buf += ch;
        i += 1;
        continue;
      }
      buf += ch;
      i += 1;
    }
    flush();
  }


  $loginBtn.addEventListener("click", login);
  $token.addEventListener("keydown", (e) => { if (e.key === "Enter") { if (e.preventDefault) e.preventDefault(); login(); } });
  $showAll.addEventListener("click", showAll);
  document.addEventListener("keydown", (e) => {
    if (!e || e.key !== "Escape" || maximized === null || e.defaultPrevented) return;
    if (e.preventDefault) e.preventDefault();
    setMax(null);
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); schedule(); });
  try { window.addEventListener("resize", render); } catch (_) { /* no window events */ }

  // For tests/js/devhub.test.mjs. Read-only.
  window.DevHub = Object.freeze({renderMarkdown, presenceLine, stageLine, hiddenIds, setHidden: (id, h) => tiles[id] && setHidden(tiles[id], h),
    poll, LAYOUT_KEY, LAYOUT_MAX});

  poll().then(schedule);
})();
