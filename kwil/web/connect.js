// The owner's own sign-ins in Connections (kwil/owner_connect.py).
// app.js calls onState(state) after each /api/state: shown only to the
// owner (a family member's session gets 404 from these routes), as one row
// per service /api/connect/status lists, through connections.js (it shows
// the row's status line and action word; this fills the details the row
// expands to). A service whose token is in the server's environment says
// "Set on the server." and has no buttons; one this server can't offer
// ("setup") has no row; otherwise Connect (Home Assistant: after typing its
// public https address) or Disconnect (asked first). Connect opens the
// service's sign-in in this tab (what an iPhone Home Screen app handles
// best); the service returns to /notion/callback or /ha/callback, which
// saves the sign-in and goes back to /. Everything is built with
// textContent only (a workspace name is someone's text). Its text is
// translated by i18n.js on a Chinese page. Loaded after connections.js,
// before app.js.
"use strict";

(function (root) {
  let setup = null;

  // Where each service's sign-in may go (anything else is refused).
  const SIGN_IN = {
    notion: (url) => url.startsWith("https://api.notion.com/v1/oauth/authorize?"),
    home_assistant: (url) => /^https:\/\/[^/?#@\s]+\/auth\/authorize\?/.test(url),
  };
  const ASK = {
    notion: "Disconnect Notion? I won't see your assignments any more.",
    home_assistant: "Disconnect Home Assistant? I won't control your home any more.",
  };

  // The sentence in the details, when there is more to say than the row's
  // status line; "" when not.
  function stateText(svc) {
    if (svc.managed === "env") return "Set on the server.";
    if (svc.managed === "app") {
      return svc.problem === "reconnect" ? "The sign-in stopped working. Connect again." : "";
    }
    if (svc.problem === "setup") return "Not set up on this server.";
    if (svc.problem === "key") return "This server can't store sign-ins yet (USER_DATA_KEY).";
    return "";
  }

  // The service's row in Connections: state, status line, action word.
  function rowOf(svc) {
    if (svc.managed === "env") return { state: "connected", status: "Set on the server", action: "Manage" };
    if (svc.managed === "app") {
      return svc.problem === "reconnect"
        ? { state: "attention", status: "Sign-in expired", action: "Reconnect" }
        : { state: "connected", status: "Connected", action: "Manage",
            detail: typeof svc.detail === "string" ? svc.detail : "" };
    }
    if (svc.problem === "setup") return { state: "off" };  // nothing to do here
    if (svc.problem === "key") return { state: "available", status: "Not available" };
    return { state: "available", status: "Not connected", action: "Connect" };
  }

  function create(win, doc) {
    const rows = win.KwilConnections;
    if (!rows) return null;
    let owner = false;
    const parts = {};  // service id -> {box, note} in its row's details (a handful)
    let shown = [];    // the ids with a row showing

    function make(tag, cls, text) {
      const el = doc.createElement(tag);
      if (cls) el.className = cls;
      if (text !== undefined) el.textContent = text;  // never HTML
      return el;
    }

    function button(text, onClick, cls) {
      const b = make("button", cls || "", text);
      b.type = "button";
      b.addEventListener("click", () => onClick(b));
      return b;
    }

    // the service's own box and message line in its row's details
    function partsOf(svc) {
      if (parts[svc.id]) return parts[svc.id];
      const body = rows.body(svc.id, String(svc.name || svc.id));
      if (!body) return null;
      const box = make("div", "connector");
      box.dataset.service = svc.id;
      const note = make("p", "hint", "");
      note.setAttribute("role", "status");
      note.hidden = true;
      body.appendChild(box);
      body.appendChild(note);
      parts[svc.id] = { box, note };
      return parts[svc.id];
    }

    function say(svc, text) {
      const p = parts[svc.id];
      if (!p) return;
      p.note.hidden = !text;
      p.note.textContent = text || "";
    }

    async function post(path, body) {
      const res = await win.fetch(path, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
      });
      return { ok: res.ok, status: res.status, body: res.ok ? await res.json() : null };
    }

    async function connect(svc, input, btn) {
      const url = input ? String(input.value || "").trim() : undefined;
      if (input && !url) { say(svc, "Type your Home Assistant's public https address."); return; }
      btn.disabled = true;
      say(svc, "Opening the sign-in…");
      try {
        const r = await post(`/api/connect/${encodeURIComponent(svc.id)}/start`,
                             url === undefined ? {} : { url });
        const page = r.body && r.body.url;
        const allowed = SIGN_IN[svc.id];
        if (typeof page === "string" && allowed && allowed(page)) {
          win.location.href = page;  // back to / after signing in
          return;
        }
        if (input && r.status === 400) {
          btn.disabled = false;
          say(svc, "Check the address: it must be your Home Assistant's public https address.");
          return;
        }
      } catch (e) { /* shown below */ }
      btn.disabled = false;
      say(svc, "Couldn't start the sign-in.");
    }

    async function disconnect(svc, btn) {
      const ask = ASK[svc.id] || "Disconnect this service?";
      if (typeof win.confirm === "function" && !win.confirm(ask)) return;
      btn.disabled = true;
      let ok = false;
      try {
        ok = (await post(`/api/connect/${encodeURIComponent(svc.id)}/disconnect`)).ok;
      } catch (e) { /* shown below */ }
      btn.disabled = false;
      if (ok) await refresh();
      say(svc, ok ? "Disconnected." : "Couldn't disconnect. Try again.");
    }

    // the details a service's row expands to
    function fill(svc) {
      const p = partsOf(svc);
      if (!p) return;
      const out = [];
      const why = stateText(svc);
      if (why) out.push(make("p", "hint", why));
      if (svc.managed === "app" && typeof svc.detail === "string" && svc.detail) {
        out.push(make("p", "hint", svc.detail.slice(0, 100)));  // in full here; text only
      }
      if (svc.managed !== "env") {  // the server's own setting: no buttons
        const actions = make("div", "conn-actions");
        if (svc.managed === "app") {
          if (svc.problem === "reconnect" && !svc.needs_url) {
            actions.appendChild(button("Connect", (b) => connect(svc, null, b), "primary"));
          }
          actions.appendChild(button("Disconnect", (b) => disconnect(svc, b), "danger"));
        } else if (!svc.problem) {
          let input = null;
          if (svc.needs_url) {
            input = make("input");
            input.type = "url";
            input.inputMode = "url";
            input.autocapitalize = "off";
            input.spellcheck = false;
            input.maxLength = 300;
            input.placeholder = "https://example.ui.nabu.casa";
            input.setAttribute("aria-label", "Home Assistant's public https address");
            out.push(input);
          }
          actions.appendChild(button("Connect", (b) => connect(svc, input, b), "primary"));
        }
        if (actions.childNodes.length) out.push(actions);
        if (svc.needs_url && svc.managed === "none" && !svc.problem) {
          out.push(make("p", "hint",
            "Kwil reaches it over the internet: use Nabu Casa's address or a tunnel."));
        }
      }
      p.box.replaceChildren(...out);
    }

    function hide(ids) {
      for (const id of ids) rows.set(id, { state: "off" });
    }

    async function refresh() {
      if (!owner) { hide(shown); shown = []; return; }
      try {
        const res = await win.fetch("/api/connect/status",
                                    { credentials: "same-origin", cache: "no-store" });
        if (!res.ok) { hide(shown); shown = []; return; }  // off, or not the owner
        const st = await res.json();
        const services = (Array.isArray(st && st.services) ? st.services : [])
          .filter((s) => s && typeof s.id === "string");
        const ids = services.map((s) => s.id);
        hide(shown.filter((id) => !ids.includes(id)));  // no longer listed
        shown = [];
        for (const svc of services) {
          const info = rowOf(svc);
          if (info.state !== "off") fill(svc);
          if (rows.set(svc.id, { name: String(svc.name || svc.id), ...info })) shown.push(svc.id);
        }
      } catch (e) { /* offline: leave it as it is */ }
    }

    const menu = doc.querySelector("details.menu");
    if (menu) menu.addEventListener("toggle", () => { if (menu.open) refresh(); });

    return {
      show(isOwner) {
        const was = owner;
        owner = Boolean(isOwner);
        if (owner !== was || !owner) refresh();
      },
      refresh,
    };
  }

  function onState(state, win = root, doc = root.document) {
    if (!doc) return;
    if (!setup) setup = create(win, doc);
    if (setup) setup.show(state && state.owner === true);
  }

  root.KwilConnect = { onState, create, stateText, rowOf };
})(typeof window !== "undefined" ? window : globalThis);
