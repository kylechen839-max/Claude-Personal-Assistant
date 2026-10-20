// Family invites on the cloud server (assistant/family.py).
// - Someone opening an invite link (https://<site>/#invite=hcinv....): the
//   token is read from the #fragment (never sent to a server by the
//   browser), cleared from the address bar, and POSTed once to
//   /api/invite/accept, which signs this phone in as that person.
// - The owner's menu: "Invite someone" (name, language) makes a one-time
//   link and opens the share sheet (or copies it); the list shows who is
//   invited or active, with "New link" and "Remove".
// app.js calls configure({ request }) and acceptInvite() first thing, and
// onState(state) on every /api/state. The owner's calls go through that
// request() (with failover: the router, signed in with a bearer token, to
// whichever backend is active); the invite link's address comes from the
// server (its own public address), never this page's. Loaded before app.js.
"use strict";

(function (root) {
  const ACCOUNT_KEY = "account";
  // app.js's request(url, options) -> Response; until configured, a plain
  // same-origin fetch (cookie sign-in, no failover)
  let request = null;

  function configure(opts = {}) {
    if (typeof opts.request === "function") request = opts.request;
  }

  // The invite token in a location's #fragment, or null.
  function readInvite(loc) {
    const m = String((loc && loc.hash) || "").match(/[#&]invite=([^&]+)/);
    if (!m) return null;
    try { return decodeURIComponent(m[1]); } catch (e) { return null; }
  }

  function storage(win) {
    try { return win.localStorage || null; } catch (e) { return null; }
  }

  // A family member's phone: no failover router (that is the owner's Pi/PC).
  function isFamilyAccount(win) {
    const s = storage(win);
    try { return Boolean(s && s.getItem(ACCOUNT_KEY) === "family"); } catch (e) { return false; }
  }

  // True when an invite was accepted (the page is reloading); false when
  // there was none, or it failed (the message is in #login-error).
  async function acceptInvite(win) {
    const token = readInvite(win.location);
    if (!token) return false;
    // out of the address bar (and history) before anything else
    win.history.replaceState(null, "", win.location.pathname || "/");
    let message = "This invite link has expired or was already used. Ask for a new one.";
    try {
      const res = await win.fetch("/api/invite/accept", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ invite: token }),
      });
      if (res.ok) {
        const s = storage(win);
        try { if (s) s.setItem(ACCOUNT_KEY, "family"); } catch (e) { /* private mode */ }
        win.location.replace("/");  // the page again, in their language
        return true;
      }
      if (res.status === 429) message = "too many attempts; try again later";
    } catch (e) {
      message = "Couldn't reach the server. Open the link again.";
    }
    const err = win.document && win.document.getElementById("login-error");
    if (err) err.textContent = message;
    return false;
  }

  // --- the owner's menu ---
  function setupOwner(win, doc) {
    const $ = (id) => doc.getElementById(id);
    const box = $("family");
    if (!box) return null;
    const openBtn = $("invite-open"), form = $("invite-form"), nameIn = $("invite-name");
    const langIn = $("invite-language"), tzIn = $("invite-tz"), status = $("invite-status");
    const linkIn = $("invite-link"), list = $("family-list");
    let shown = false;

    async function call(url, options = {}) {
      const res = request ? await request(url, options)
        : await win.fetch(url, { credentials: "same-origin", ...options });
      let body = {};
      try { body = await res.json(); } catch (e) { /* not JSON */ }
      if (!res.ok) throw new Error(body.error || `Error ${res.status}`);
      return body;
    }
    const post = (url, data) => call(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data || {}) });

    async function share(url) {
      linkIn.value = url;
      linkIn.hidden = false;
      const nav = win.navigator || {};
      if (typeof nav.share === "function") {
        try { await nav.share({ url }); status.textContent = "Invite link ready."; return; }
        catch (e) { if (e && e.name === "AbortError") { status.textContent = "Invite link ready."; return; } }
      }
      try {
        await nav.clipboard.writeText(url);
        status.textContent = "Invite link copied.";
      } catch (e) {
        status.textContent = "Copy the invite link below.";
      }
    }

    function row(user) {
      const li = doc.createElement("li");
      const label = doc.createElement("span");
      label.textContent = user.name;
      const state = doc.createElement("span");
      state.className = "hint";
      state.textContent = user.status === "active" ? "Active" : "Invited";
      const again = doc.createElement("button");
      again.type = "button";
      again.textContent = "New link";
      again.addEventListener("click", async () => {
        try { await share((await post(`/api/users/${encodeURIComponent(user.id)}/invite`)).url); }
        catch (e) { status.textContent = e.message; }
      });
      const remove = doc.createElement("button");
      remove.type = "button";
      remove.className = "danger";
      remove.textContent = "Remove";
      remove.addEventListener("click", async () => {
        if (!win.confirm(`Remove ${user.name}? This deletes their conversations.`)) return;
        try {
          // with a body: the server requires a Content-Length on every unsafe request
          await call(`/api/users/${encodeURIComponent(user.id)}`, {
            method: "DELETE", headers: { "Content-Type": "application/json" }, body: "{}" });
          refresh();
        } catch (e) { status.textContent = e.message; }
      });
      li.append(label, " ", state, " ", again, " ", remove);
      return li;
    }

    async function refresh() {
      if (!shown) return;
      try {
        const body = await call("/api/users");
        list.textContent = "";
        for (const u of body.users || []) list.appendChild(row(u));
      } catch (e) { /* offline: keep the list */ }
    }

    openBtn.addEventListener("click", () => {
      form.hidden = !form.hidden;
      if (!form.hidden && tzIn && !tzIn.value) {
        try { tzIn.value = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { /* none */ }
      }
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      status.textContent = "";
      try {
        const body = await post("/api/invites", {
          name: nameIn.value.trim(), language: langIn.value, timezone: tzIn ? tzIn.value.trim() : "" });
        nameIn.value = "";
        form.hidden = true;
        await share(body.url);
        refresh();
      } catch (err) { status.textContent = err.message; }
    });
    const menu = doc.querySelector("details.menu");
    if (menu) menu.addEventListener("toggle", () => { if (menu.open) refresh(); });

    return {
      show(on) {
        shown = Boolean(on);
        box.hidden = !shown;
        if (shown) refresh();
      },
      refresh,
    };
  }

  let owner = null;
  // From app.js after each /api/state: the owner's menu only for the owner
  // with [family] on; a family member's page hides the owner-only tabs.
  function onState(state, win = root, doc = root.document) {
    if (!doc) return;
    const app = doc.getElementById("app");
    if (app && app.dataset) app.dataset.account = state.owner === false ? "family" : "owner";
    const s = storage(win);
    if (state.owner) {
      try { if (s) s.removeItem(ACCOUNT_KEY); } catch (e) { /* ignore */ }
    }
    if (!owner) owner = setupOwner(win, doc);
    if (owner) owner.show(Boolean(state.owner && state.family));
  }

  root.HeyClaudeFamily = { readInvite, acceptInvite, isFamilyAccount, onState, setupOwner, configure };
})(typeof window !== "undefined" ? window : globalThis);
