// "Connected services" in the owner's menu (kwil/owner_connect.py).
// app.js calls onState(state) after each /api/state: the section is shown
// only to the owner (a family member's session gets 404 from these routes),
// with one row per service /api/connect/status lists. A service whose token
// is in the server's environment says "Set on the server." and has no
// buttons; otherwise Connect (Home Assistant: after typing its public https
// address) or Disconnect (asked first). Connect opens the service's sign-in
// in this tab (what an iPhone Home Screen app handles best); the service
// returns to /notion/callback or /ha/callback, which saves the sign-in and
// goes back to /. Rows are built with textContent only (a workspace name is
// someone's text). Its text is translated by i18n.js on a Chinese page.
// Loaded before app.js.
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

  function stateText(svc) {
    if (svc.managed === "env") return "Set on the server.";
    if (svc.managed === "app") {
      return svc.problem === "reconnect" ? "The sign-in stopped working. Connect again."
        : "Connected.";
    }
    if (svc.problem === "setup") return "Not set up on this server.";
    if (svc.problem === "key") return "This server can't store sign-ins yet (USER_DATA_KEY).";
    return "Not connected.";
  }

  function create(win, doc) {
    const section = doc.getElementById("connectors");
    const list = doc.getElementById("connector-list");
    const note = doc.getElementById("connectors-status");
    if (!section || !list) return null;
    let owner = false;

    function say(text) {
      if (!note) return;
      note.hidden = !text;
      note.textContent = text || "";
    }

    function make(tag, cls, text) {
      const el = doc.createElement(tag);
      if (cls) el.className = cls;
      if (text !== undefined) el.textContent = text;  // never HTML
      return el;
    }

    function button(text, onClick) {
      const b = make("button", "", text);
      b.type = "button";
      b.addEventListener("click", () => onClick(b));
      return b;
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
      if (input && !url) { say("Type your Home Assistant's public https address."); return; }
      btn.disabled = true;
      say("Opening the sign-in…");
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
          say("Check the address: it must be your Home Assistant's public https address.");
          return;
        }
      } catch (e) { /* shown below */ }
      btn.disabled = false;
      say("Couldn't start the sign-in.");
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
      say(ok ? "Disconnected." : "Couldn't disconnect. Try again.");
    }

    function row(svc) {
      const box = make("div", "connector");
      box.dataset.service = svc.id;
      const head = make("div", "voice-picker-row");
      head.appendChild(make("span", "connector-name", String(svc.name || svc.id)));
      head.appendChild(make("span", "hint", stateText(svc)));
      box.appendChild(head);
      if (svc.managed === "app" && typeof svc.detail === "string" && svc.detail) {
        box.appendChild(make("p", "hint", svc.detail.slice(0, 100)));  // text only
      }
      if (svc.managed === "env") return box;  // the server's own setting: no buttons
      const actions = make("div", "voice-picker-row");
      if (svc.managed === "app") {
        if (svc.problem === "reconnect" && !svc.needs_url) {
          actions.appendChild(button("Connect", (b) => connect(svc, null, b)));
        }
        actions.appendChild(button("Disconnect", (b) => disconnect(svc, b)));
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
          actions.appendChild(input);
        }
        actions.appendChild(button("Connect", (b) => connect(svc, input, b)));
      }
      if (actions.childNodes.length) box.appendChild(actions);
      if (svc.needs_url && svc.managed === "none" && !svc.problem) {
        box.appendChild(make("p", "hint",
          "Kwil reaches it over the internet: use Nabu Casa's address or a tunnel."));
      }
      return box;
    }

    async function refresh() {
      if (!owner) { section.hidden = true; return; }
      try {
        const res = await win.fetch("/api/connect/status",
                                    { credentials: "same-origin", cache: "no-store" });
        if (!res.ok) { section.hidden = true; return; }  // off, or not the owner
        const st = await res.json();
        const services = Array.isArray(st && st.services) ? st.services : [];
        list.replaceChildren(...services.filter((s) => s && typeof s.id === "string").map(row));
        section.hidden = services.length === 0;
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

  root.KwilConnect = { onState, create, stateText };
})(typeof window !== "undefined" ? window : globalThis);
