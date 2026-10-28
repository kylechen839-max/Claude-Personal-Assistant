// "Connections" in the Settings menu: one 52px row per service (Google,
// Notion, Canvas, Home Assistant, Spotify, an invited person's Anthropic
// key), connected ones first, then the ones still to connect (two lists in
// one grouped card). Each row is a real button: the service's icon, its
// name, a one-line subtitle, and on the right either a status dot and word
// ("Connected", "Sign-in expired") or the plain action "Connect". Tapping it
// opens that service's bottom sheet (a modal <dialog>: the explanation, the
// forms and Disconnect), one at a time; Close, Esc, a tap outside it or
// closing the menu shuts it. The sheet's contents are the service's own
// controls, kept by its own script: i18n.js (Google), notion.js, student.js
// (Canvas), connect.js (the owner's Notion and Home Assistant), spotify.js
// and family.js (the key). Those scripts call set(id, {state, status,
// action, detail, sub, alert}) after each status check:
//   state  "connected" | "attention" (connected, but needs you: listed
//          first) | "available" | "off" (hidden: not for this person)
//   status, action, sub  fixed English phrases (i18n.js translates them)
//   detail someone's own text (a workspace, a school) shown as the
//          subtitle: text only, never HTML
//   alert  true: the status in the warning style without moving the row
//          (a beta tester's missing key, still with the rows to connect)
// The rows are in index.html; a service the page doesn't know gets a row
// built the same way, with a letter for its icon. Loaded first, before the
// scripts that use it. DESIGN_SYSTEM.md has the look.
"use strict";

(function (root) {
  const ORDER = ["google", "notion", "canvas", "home_assistant", "spotify", "anthropic"];
  const STATES = ["attention", "connected", "available", "off"];
  const ID = /^[a-z][a-z0-9_]{0,40}$/;

  // [{id, state}] -> {connected: [ids], available: [ids]}: "attention"
  // rows first, then connected, each in ORDER (unknown ids after, by name);
  // "off" (or no state yet) is in neither.
  function group(rows) {
    const rank = (id) => (ORDER.includes(id) ? ORDER.indexOf(id) : ORDER.length);
    const sorted = (Array.isArray(rows) ? rows : [])
      .filter((r) => r && typeof r.id === "string")
      .sort((a, b) => rank(a.id) - rank(b.id) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    const ids = (state) => sorted.filter((r) => r.state === state).map((r) => r.id);
    return { connected: [...ids("attention"), ...ids("connected")], available: ids("available") };
  }

  // What the row's right side shows: the action word for a service still to
  // connect (unless flagged), else the status with its dot's tone.
  function look(state, info) {
    const alert = Boolean(info.alert) || state === "attention";
    const action = state === "available" && !alert && Boolean(info.action);
    return { alert, action, tone: alert ? "warn" : state === "connected" ? "on" : "off" };
  }

  function create(win, doc) {
    const section = doc.getElementById("connections");
    const lists = { connected: doc.getElementById("conn-connected"),
                    available: doc.getElementById("conn-available") };
    if (!section || !lists.connected || !lists.available) return null;
    const known = {};  // id -> state (a handful of services)

    function make(tag, cls, text) {
      const el = doc.createElement(tag);
      if (cls) el.className = cls;
      if (text !== undefined) el.textContent = text;  // never HTML
      return el;
    }
    const part = (li, cls) => li.querySelector("." + cls);

    // the same markup as the rows in index.html, a letter for the icon
    function build(id, name) {
      const label = String(name || id).slice(0, 40);
      const letter = label.charAt(0).toUpperCase();
      const li = make("li", "conn-row");
      li.id = `conn-${id}`;
      li.dataset.conn = id;
      li.hidden = true;
      const head = make("button", "row conn-head");
      head.type = "button";
      head.id = `conn-${id}-head`;
      head.setAttribute("aria-haspopup", "dialog");
      head.setAttribute("aria-expanded", "false");
      head.setAttribute("aria-controls", `conn-${id}-body`);
      const icon = make("span", "conn-icon", letter);
      icon.setAttribute("aria-hidden", "true");
      const text = make("span", "conn-text");
      text.append(make("span", "conn-name", label), make("span", "conn-sub", ""));
      const action = make("span", "conn-action value connect", "");
      action.hidden = true;
      head.append(icon, text, make("span", "conn-state state off", ""), action);
      const body = make("dialog", "conn-body conn-sheet");
      body.id = `conn-${id}-body`;
      body.setAttribute("aria-labelledby", `conn-${id}-title`);
      body.hidden = true;
      const grab = make("div", "conn-grab");
      grab.setAttribute("aria-hidden", "true");
      const top = make("div", "conn-sheet-head");
      const big = make("span", "conn-icon lg", letter);
      big.setAttribute("aria-hidden", "true");
      const title = make("h2", "", label);
      title.id = `conn-${id}-title`;
      const close = make("button", "icon-btn conn-close");
      close.type = "button";
      close.setAttribute("aria-label", "Close");
      top.append(big, title, close);
      body.append(grab, top);
      li.append(head, body);
      lists.available.appendChild(li);
      wire(li);
      return li;
    }

    // Esc (the dialog's own close) keeps the row's state right
    function wire(li) {
      const body = part(li, "conn-body");
      if (!body || li.dataset.wired) return;
      li.dataset.wired = "1";
      body.addEventListener("close", () => {
        if (!body.hidden) toggle(li.dataset.conn, false);
      });
    }

    // a service's row: the page's own, or a new one when `name` is given
    function row(id, name) {
      if (typeof id !== "string" || !ID.test(id)) return null;
      const li = doc.getElementById(`conn-${id}`);
      if (li) { wire(li); return li; }
      return name === undefined ? null : build(id, name);
    }

    function rows() {
      return [...lists.connected.children, ...lists.available.children];
    }

    // open one row's sheet (closing any other), or close it; toggle(null)
    // closes all
    function toggle(id, open) {
      for (const li of rows()) {
        const head = part(li, "conn-head");
        const body = part(li, "conn-body");
        if (!head || !body) continue;
        const was = head.getAttribute("aria-expanded") === "true";
        const on = li.dataset.conn === id && !li.hidden && (open === undefined ? !was : Boolean(open));
        if (on === was && body.hidden === !on) continue;
        head.setAttribute("aria-expanded", on ? "true" : "false");
        body.hidden = !on;
        if (on && typeof body.showModal === "function" && !body.open) body.showModal();
        if (!on) {
          if (typeof body.close === "function" && body.open) body.close();
          if (was && !li.hidden && typeof head.focus === "function") head.focus();  // back to its row
        }
      }
    }

    // the rows into their groups, in order; moves a row only when the order
    // changed, and gives focus back if a moved row had it
    function arrange() {
      const groups = group(Object.keys(known).map((id) => ({ id, state: known[id] })));
      const active = doc.activeElement;
      for (const key of ["connected", "available"]) {
        const want = groups[key];
        const have = [...lists[key].children].filter((li) => want.includes(li.dataset.conn))
          .map((li) => li.dataset.conn);
        const inPlace = have.length === want.length && have.every((id, i) => id === want[i]);
        if (!inPlace) {
          for (const id of want) lists[key].appendChild(row(id));
        }
        lists[key].hidden = want.length === 0;
        const label = doc.getElementById(`conn-${key}-label`);  // for screen readers
        if (label) label.hidden = want.length === 0;
      }
      section.hidden = groups.connected.length + groups.available.length === 0;
      if (active && doc.activeElement !== active && typeof active.focus === "function" &&
          section.contains(active)) active.focus();
    }

    function set(id, info = {}) {
      const li = row(id, info.name);
      if (!li) return null;
      const state = STATES.includes(info.state) ? info.state : "off";
      const shown = look(state, info);
      const sub = part(li, "conn-sub");
      if (li.dataset.sub === undefined) li.dataset.sub = sub.textContent;  // the page's own line
      const detail = typeof info.detail === "string" ? info.detail.trim().slice(0, 100) : "";
      sub.textContent = detail || (typeof info.sub === "string" && info.sub) || li.dataset.sub;
      const status = part(li, "conn-state");
      status.textContent = String(info.status || "");
      status.className = `conn-state state ${shown.tone}`;
      status.hidden = shown.action || !info.status;
      const action = part(li, "conn-action");
      action.textContent = shown.action ? String(info.action) : "";
      action.hidden = !shown.action;
      li.dataset.state = state;
      li.dataset.alert = shown.alert ? "1" : "";
      li.hidden = state === "off";
      if (li.hidden) toggle(id, false);
      known[id] = state;
      arrange();
      return li;
    }

    // a service's sheet (connect.js fills it), its row made if needed
    function body(id, name) {
      const li = row(id, name);
      return li ? part(li, "conn-body") : null;
    }

    section.addEventListener("click", (e) => {
      const t = e.target;
      if (!t || typeof t.closest !== "function") return;
      const li = t.closest(".conn-row");
      if (!li || !li.dataset) return;
      const sheet = t.closest(".conn-sheet");
      if (!sheet) {
        if (t.closest(".conn-head")) toggle(li.dataset.conn);
        return;
      }
      if (t.closest(".conn-close")) { toggle(li.dataset.conn, false); return; }
      // a tap on the backdrop lands on the dialog itself, outside its box
      if (t === sheet && typeof sheet.getBoundingClientRect === "function") {
        const r = sheet.getBoundingClientRect();
        if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) {
          toggle(li.dataset.conn, false);
        }
      }
    });
    for (const li of rows()) wire(li);
    // closing the menu closes any sheet
    const menu = doc.querySelector("details.menu");
    if (menu) menu.addEventListener("toggle", () => { if (!menu.open) toggle(null, false); });

    return { set, body, row, toggle, arrange, state: (id) => known[id] || "off" };
  }

  let setup;  // undefined: not looked for yet; null: no Connections section
  function controller() {
    if (setup === undefined && root.document) setup = create(root, root.document);
    return setup || null;
  }

  root.KwilConnections = {
    ORDER, group, look, create,
    set: (id, info) => { const c = controller(); return c ? c.set(id, info) : null; },
    body: (id, name) => { const c = controller(); return c ? c.body(id, name) : null; },
    toggle: (id, open) => { const c = controller(); if (c) c.toggle(id, open); },
  };
})(typeof window !== "undefined" ? window : globalThis);
