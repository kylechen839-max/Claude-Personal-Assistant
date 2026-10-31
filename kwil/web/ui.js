// Small touch behaviours the phone page should have, like other apps:
// - the settings menu closes on a tap anywhere outside it (and on Escape);
// - it is a full-screen Settings page: opening it moves focus to its title,
//   makes the chat behind it inert (#app's parts outside the header, so
//   VoiceOver and Tab stay on the page; the tab bar is outside #app), and Done (like Escape) closes it and gives focus back to the gear (on a
//   phone, to the tab bar's Settings: the header's gear is hidden there; on
//   a desktop, to the rail's Settings);
// - swiping down while the keyboard is open closes the keyboard (not when
//   the swipe scrolls a message long enough to scroll inside its field).
// The desktop's Ctrl+K / Cmd+K switcher (KwilUI.switcher): go to a screen or
// a past conversation from the keyboard. Voice mode as a full-window overlay
// (KwilUI.voice, called by voice.js): the page behind it is inert, Escape
// ends it, focus goes to the orb and comes back to the Voice button.
// And the owner's health banner (state.health from /api/state, written by
// the watchdog, kwil/watch.py): "Mom's app is down since 06:13".
"use strict";

(function (root) {
  function closeMenuOutside(doc) {
    const menu = doc.querySelector("details.menu");
    if (!menu) return null;
    const find = (sel) => (typeof menu.querySelector === "function" ? menu.querySelector(sel) : null);
    const byId = (id) => (typeof doc.getElementById === "function" ? doc.getElementById(id) : null);
    const focus = (el) => { if (el && typeof el.focus === "function") el.focus(); };
    // the phone's tab bar (app.css shows it below 48rem): its Settings tab
    // stands in for the gear there
    const phone = () => typeof root.matchMedia === "function"
      && root.matchMedia("(max-width: 47.99rem)").matches;
    const tabbar = () => byId("tabbar");
    // laid out (html.kb-open hides the bar with CSS, not [hidden])
    const rendered = (el) => typeof el.getClientRects !== "function" || el.getClientRects().length > 0;
    // The keyboard is up, so the bar isn't drawn and can't take focus: put
    // the keyboard away and focus the tab once the bar is back (the
    // viewport's next resize, or a frame after kb-open clears; about 2 s at
    // most). With neither available, the gear as before.
    const focusWhenShown = (tab) => {
      const active = doc.activeElement;
      if (active && typeof active.blur === "function") active.blur();
      const vv = root.visualViewport;
      const raf = typeof root.requestAnimationFrame === "function" ? root.requestAnimationFrame.bind(root) : null;
      const canResize = vv && typeof vv.addEventListener === "function";
      if (!raf && !canResize) { focus(find("summary")); return; }
      let done = false, frames = 0;
      const finish = () => {
        done = true;
        if (canResize && typeof vv.removeEventListener === "function") vv.removeEventListener("resize", onResize);
      };
      const tryNow = () => {
        if (done) return true;
        if (menu.open) { finish(); return true; }  // opened again meanwhile: its title has focus
        if (rendered(tab)) { finish(); focus(tab); return true; }
        return false;
      };
      const tick = () => { if (!tryNow() && ++frames < 120) raf(tick); else if (!done) finish(); };
      const onResize = () => {
        if (tryNow()) return;
        if (raf) raf(tick); else if (++frames >= 5) finish();  // a few resizes at most
      };
      if (canResize) vv.addEventListener("resize", onResize);
      if (raf) raf(tick);
    };
    // the chat behind the open page, and the header around the menu: out of
    // reach for VoiceOver and Tab
    const behind = (on) => {
      if (typeof doc.querySelectorAll !== "function") return;
      for (const el of doc.querySelectorAll("#app > :not(header), #app > header > :not(details.menu)")) el.inert = on;
    };
    // close the page and hand focus back to the gear (VoiceOver lands there),
    // on a phone to the tab bar's Settings
    // the desktop rail (app.css shows it from 48rem): its Settings item
    // stands in for the gear there
    const desktop = () => typeof root.matchMedia === "function"
      && root.matchMedia("(min-width: 48rem)").matches;
    const close = () => {
      menu.open = false;
      behind(false);
      const rail = byId("rail"), item = byId("rail-settings");
      if (rail && !rail.hidden && item && !item.hidden && desktop()) { focus(item); return; }
      const tab = byId("tab-settings"), bar = tabbar();
      if (!(tab && !tab.hidden && bar && !bar.hidden && phone())) focus(find("summary"));
      else if (rendered(tab)) focus(tab);
      else focusWhenShown(tab);
    };
    // a tap in the tab bar or the rail is its own (app.js): Settings stays open for it
    const inBar = (target) => [tabbar(), byId("rail")].some((bar) =>
      !!bar && typeof bar.contains === "function" && bar.contains(target));
    const onDown = (e) => {
      if (menu.open && !menu.contains(e.target) && !inBar(e.target)) menu.open = false;
    };
    const onKey = (e) => {
      // with a Connections sheet open (connections.js), Escape closes only it
      const sheet = find("dialog[open]");
      if (e.key === "Escape" && menu.open && !sheet) close();
    };
    const onToggle = () => {
      behind(!!menu.open);
      if (menu.open) focus(byId("settings-title"));
    };
    const done = byId("settings-done");
    if (done && typeof done.addEventListener === "function") done.addEventListener("click", close);
    if (typeof menu.addEventListener === "function") menu.addEventListener("toggle", onToggle);
    doc.addEventListener("pointerdown", onDown, true);
    doc.addEventListener("keydown", onKey);
    return { onDown, onKey, onToggle, close };
  }

  const SWIPE_PX = 40;  // a deliberate downward swipe, not a tap or a scroll jitter

  function swipeDownHidesKeyboard(doc) {
    let startY = null;
    const typing = () => {
      const el = doc.activeElement;
      return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA");
    };
    // the composer's field once its text is taller than it (about 6 lines)
    const scrollsText = (target) => {
      const field = typeof doc.getElementById === "function" ? doc.getElementById("text") : null;
      if (!field || !target) return false;
      const inside = target === field || (typeof field.contains === "function" && field.contains(target));
      return inside && field.scrollHeight > field.clientHeight;
    };
    const onStart = (e) => {
      startY = e.touches && e.touches.length === 1 && !scrollsText(e.target) ? e.touches[0].clientY : null;
    };
    const onMove = (e) => {
      if (startY === null || !e.touches || !e.touches.length || !typing()) return;
      if (e.touches[0].clientY - startY > SWIPE_PX) {
        doc.activeElement.blur();
        startY = null;
      }
    };
    doc.addEventListener("touchstart", onStart, { passive: true });
    doc.addEventListener("touchmove", onMove, { passive: true });
    return { onStart, onMove };
  }

  function clock(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  // The banner's lines for state.health (null: nothing to show). Only
  // names, times and fixed reasons come from the server; never chat text.
  function healthLines(health, fmt) {
    fmt = fmt || clock;
    if (!health) return [];
    const lines = [];
    for (const i of health.issues || []) {
      const down = /^healthz\b/.test(i.what || "");
      const since = fmt(i.since);
      lines.push(`${i.label} ${down ? "is down" : "isn't working right"}`
        + (since ? ` since ${since}` : "") + (i.what ? ` (${i.what})` : ""));
    }
    if (health.stale) {
      const at = fmt(health.checked);
      lines.push("Health checks haven't run" + (at ? ` since ${at}` : " yet") + ".");
    }
    for (const e of health.events || []) {
      const at = fmt(e.at);
      lines.push((at ? `${at}: ` : "") + e.text);
    }
    return lines;
  }

  function renderHealth(doc, health, fmt) {
    const el = doc.getElementById("health");
    if (!el) return [];
    const lines = healthLines(health, fmt);
    el.textContent = lines.join("\n");
    el.hidden = !lines.length;
    return lines;
  }

  // --- the Ctrl+K / Cmd+K switcher (#switcher, a combobox over a listbox) ---
  // Its commands each click the control that is already on the page, and are
  // listed only while that control is (not [hidden]); with a query, up to 6
  // past conversations from the History search come after them.
  const COMMANDS = [
    { label: "Chat", id: "rail-chat" },
    { label: "New chat", run: (app) => app.newChat(null), when: (app) => !!app && app.idle() },
    { label: "Today", id: "rail-today" },
    { label: "History", id: "rail-history" },
    { label: "Settings", id: "rail-settings", keepMenu: true },
    { label: "Memory", id: "memory-open" },
    { label: "Projects", id: "projects-open" },
    { label: "Reminders", id: "reminders-open" },
    { label: "Start voice conversation", id: "voice-open" },
  ];
  const MAX_CONVERSATIONS = 6;
  const SEARCH_MS = 150;  // the debounce before asking the server

  function isMac(win) {
    const nav = win.navigator || {};
    const platform = (nav.userAgentData && nav.userAgentData.platform) || nav.platform || "";
    return /mac|iphone|ipad|ipod/i.test(platform);
  }

  function switcher(doc, win) {
    win = win || root;
    const byId = (id) => (typeof doc.getElementById === "function" ? doc.getElementById(id) : null);
    const dlg = byId("switcher"), input = byId("switcher-input"), list = byId("switcher-list");
    const status = byId("switcher-status");
    if (!dlg || !input || !list || typeof dlg.addEventListener !== "function") return null;
    const mac = isMac(win);
    const i18n = () => win.KwilI18n;
    const say = (text) => (i18n() && i18n().isActive(doc) ? i18n().translate(text) : text);
    const app = () => win.KwilApp || null;
    const shown = (el) => !!el && !el.hidden;
    const menu = () => (typeof doc.querySelector === "function" ? doc.querySelector("details.menu") : null);
    let options = [];   // [{label, kind, act}]
    let active = -1;
    let conversations = [];
    let returnTo = null;
    let timer = null, seq = 0, controller = null, pending = false;

    // another dialog (a sheet, a Connections sheet), the sign-in, voice mode
    const blocked = () => {
      const open = typeof doc.querySelectorAll === "function" ? [...doc.querySelectorAll("dialog[open]")] : [];
      return open.some((d) => d !== dlg) || shown(byId("login")) || shown(byId("voice"));
    };
    const query = () => String(input.value || "").trim();
    const commands = (q) => {
      const needle = q.toLowerCase();
      const out = [];
      for (const c of COMMANDS) {
        if (c.id ? !shown(byId(c.id)) : !c.when(app())) continue;
        const label = say(c.label);
        if (needle && !label.toLowerCase().includes(needle)) continue;
        out.push({ label, kind: "", act: () => {
          if (!c.keepMenu) { const m = menu(); if (m && m.open) m.open = false; }
          if (c.run) c.run(app()); else byId(c.id).click();
        } });
      }
      return out;
    };
    function setActive(i) {
      active = options.length ? (i + options.length) % options.length : -1;
      Array.from(list.children).forEach((opt, n) => {
        opt.setAttribute("aria-selected", String(n === active));
        if (n === active && typeof opt.scrollIntoView === "function") opt.scrollIntoView({ block: "nearest" });
      });
      if (active >= 0) input.setAttribute("aria-activedescendant", `switcher-opt-${active}`);
      else if (typeof input.removeAttribute === "function") input.removeAttribute("aria-activedescendant");
    }
    function render(keep) {
      const q = query();
      const was = active;
      options = commands(q);
      if (q) {
        for (const c of conversations) {
          options.push({ label: String(c.title || ""), kind: say("Conversation"), act: () => {
            const m = menu(); if (m && m.open) m.open = false;
            const history = win.KwilHistory;
            if (history && typeof history.openTranscript === "function") history.openTranscript(c.id);
          } });
        }
      }
      list.textContent = "";
      options.forEach((o, n) => {
        const li = doc.createElement("li");
        li.id = `switcher-opt-${n}`;
        li.className = "switcher-opt";
        li.setAttribute("role", "option");
        const label = doc.createElement("span");
        label.className = "switcher-label";
        label.textContent = o.label;
        li.appendChild(label);
        if (o.kind) {
          const kind = doc.createElement("span");
          kind.className = "switcher-kind";
          kind.textContent = o.kind;
          li.appendChild(kind);
        }
        // a click picks it; the press itself leaves focus in the field
        li.addEventListener("mousedown", (e) => e.preventDefault());
        li.addEventListener("click", () => pick(n));
        list.appendChild(li);
      });
      input.setAttribute("aria-expanded", String(options.length > 0));
      setActive(keep && was >= 0 && was < options.length ? was : 0);
      if (status) status.textContent = !options.length && q && !pending ? say("No matches") : "";
    }
    // the History search (history.js uses the same route): the latest query only
    function stopSearch() {
      clearTimeout(timer);
      timer = null;
      seq++;
      pending = false;
      if (controller) { controller.abort(); controller = null; }
    }
    async function search(q) {
      const my = ++seq;
      if (controller) controller.abort();
      const Abort = win.AbortController;
      controller = typeof Abort === "function" ? new Abort() : null;
      const a = app();
      if (!a || typeof a.request !== "function") { pending = false; render(true); return; }
      try {
        const params = new URLSearchParams({ q, limit: String(MAX_CONVERSATIONS) });
        const res = await a.request(`/api/conversations?${params}`, controller ? { signal: controller.signal } : {});
        const data = await res.json();
        if (my !== seq || !dlg.open) return;
        conversations = res.ok && Array.isArray(data.conversations) ? data.conversations.slice(0, MAX_CONVERSATIONS) : [];
      } catch (e) {
        if (my !== seq) return;
        conversations = [];  // offline or aborted: the commands still work
      }
      pending = false;
      render(true);
    }
    function onInput() {
      stopSearch();
      conversations = [];
      const q = query();
      if (q) {
        pending = true;
        timer = setTimeout(() => { timer = null; search(q); }, SEARCH_MS);
      }
      render(false);
    }
    function open() {
      if (dlg.open || blocked()) return;
      returnTo = doc.activeElement || null;
      input.value = "";
      conversations = [];
      stopSearch();
      render(false);
      if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.open = true;
      if (typeof input.focus === "function") input.focus();
    }
    function close(restore = true) {
      if (!dlg.open) return;
      stopSearch();
      if (typeof dlg.close === "function") dlg.close(); else dlg.open = false;
      const back = returnTo;
      returnTo = null;
      if (restore && back && typeof back.focus === "function") back.focus();
    }
    function pick(n) {
      const o = options[n];
      if (!o) return;
      close(false);
      o.act();
    }
    function onKey(e) {
      const key = e.key;
      if (key === "ArrowDown" || key === "ArrowUp") setActive(active + (key === "ArrowDown" ? 1 : -1));
      else if (key === "Home") setActive(0);
      else if (key === "End") setActive(options.length - 1);
      else if (key === "Enter") pick(active);
      else if (key === "Escape") {
        // only the switcher: not the Settings page or a screen behind it
        if (typeof e.stopPropagation === "function") e.stopPropagation();
        close();
      } else return;
      e.preventDefault();
    }
    // Ctrl+K (Cmd+K on a Mac), even while typing a message; again closes it
    function onShortcut(e) {
      if (e.altKey || e.shiftKey || !(e.key === "k" || e.key === "K")) return;
      if (!(mac ? e.metaKey : e.ctrlKey)) return;
      if (dlg.open) { e.preventDefault(); close(); return; }
      if (blocked()) return;
      e.preventDefault();
      open();
    }
    input.addEventListener("input", onInput);
    input.addEventListener("keydown", onKey);
    // the dialog's own Escape and a tap on the backdrop: close like Escape
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
    dlg.addEventListener("click", (e) => { if (e.target === dlg) close(); });
    doc.addEventListener("keydown", onShortcut, true);
    const search_ = byId("rail-search");
    if (search_ && typeof search_.addEventListener === "function") {
      search_.addEventListener("click", open);
      const key = byId("rail-search-key");
      if (key) key.textContent = mac ? "\u2318K" : "Ctrl K";
      search_.setAttribute("aria-keyshortcuts", mac ? "Meta+K" : "Control+K");
    }
    return { open, close, onShortcut, onKey, onInput, pick,
      options: () => options.map((o) => o.label), active: () => active, isMac: mac };
  }

  // --- voice mode over the whole window (voice.js calls opened/closed) ---
  function voiceOverlay(doc) {
    const byId = (id) => (typeof doc.getElementById === "function" ? doc.getElementById(id) : null);
    const view = byId("voice");
    if (!view) return null;
    const focus = (el) => { if (el && typeof el.focus === "function") el.focus(); };
    // every screen, the rail and the tab bar: out of reach for Tab and VoiceOver
    const behind = (on) => {
      if (typeof doc.querySelectorAll !== "function") return;
      for (const el of doc.querySelectorAll("body > main, #rail, #tabbar")) el.inert = on;
    };
    const opened = () => {
      behind(true);
      focus(byId("voice-orb"));
    };
    const closed = () => {
      behind(false);
      const voiceOpen = byId("voice-open");
      focus(voiceOpen && !voiceOpen.hidden ? voiceOpen : byId("text"));
    };
    const onKey = (e) => {
      if (e.key !== "Escape" || view.hidden) return;
      const open = typeof doc.querySelector === "function" ? doc.querySelector("dialog[open]") : null;
      if (open) return;
      const end = byId("voice-end");
      if (!end || typeof end.click !== "function") return;
      e.preventDefault();
      // not also the screen behind it (history.js's Escape is Back)
      if (typeof e.stopImmediatePropagation === "function") e.stopImmediatePropagation();
      end.click();
    };
    doc.addEventListener("keydown", onKey);
    return { opened, closed, onKey };
  }

  const api = { closeMenuOutside, swipeDownHidesKeyboard, SWIPE_PX, healthLines,
    renderHealth, switcher, voiceOverlay, COMMANDS };
  root.KwilUI = api;
  if (typeof root.document !== "undefined" && root.document.addEventListener) {
    api.menu = closeMenuOutside(root.document);
    api.swipe = swipeDownHidesKeyboard(root.document);
    api.switcher = switcher(root.document, root);
    api.voice = voiceOverlay(root.document);
  }
})(typeof window !== "undefined" ? window : globalThis);
