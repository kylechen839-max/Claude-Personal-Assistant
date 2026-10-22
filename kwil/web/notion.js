// "Connect Notion" in an invited person's menu (kwil/notion_web.py).
// app.js calls onState(state) after each /api/state: the button is shown
// only to a family member or beta tester (never the owner, whose Notion is
// NOTION_TOKEN on his own machines; the routes give the owner 404), and only
// when /api/notion/status says it is set up. Tapping it opens Notion's
// consent page; Notion returns to /notion/callback, which saves the sign-in
// and goes back to /. Once connected it shows their workspace and the
// Assignments database found (as plain text, never HTML), or how to share
// it, and "Disconnect Notion". Its text is translated by i18n.js on a
// Chinese page. Loaded before app.js.
"use strict";

(function (root) {
  let setup = null;

  function create(win, doc) {
    const btn = doc.getElementById("notion-connect");
    const unlink = doc.getElementById("notion-disconnect");
    const hint = doc.getElementById("notion-status");
    const detail = doc.getElementById("notion-detail");
    if (!btn) return null;
    let member = false;

    function hideAll() {
      btn.hidden = true;
      if (unlink) unlink.hidden = true;
      if (hint) hint.hidden = true;
      if (detail) detail.hidden = true;
    }

    async function refresh() {
      if (!member) { hideAll(); return; }
      try {
        const res = await win.fetch("/api/notion/status",
                                    { credentials: "same-origin", cache: "no-store" });
        if (!res.ok) { hideAll(); return; }  // off, or not an invited person
        const st = await res.json();
        btn.hidden = !st.enabled;
        btn.textContent = st.connected ? "Reconnect Notion" : "Connect Notion";
        if (unlink) unlink.hidden = !st.connected;
        if (hint) {
          hint.hidden = !st.connected;
          hint.textContent = st.database ? "Notion connected."
            : "Notion is connected, but I couldn't find exactly one Assignments database. " +
              "Tap \"Reconnect Notion\" and share only your Assignments database.";
        }
        if (detail) {
          const parts = [st.workspace, st.database]
            .filter((p) => typeof p === "string" && p).map((p) => p.slice(0, 100));
          detail.hidden = !(st.connected && parts.length);
          detail.textContent = parts.join(" · ");  // their own titles: text only
        }
      } catch (e) { /* offline: leave it as it is */ }
    }

    btn.addEventListener("click", async () => {
      btn.disabled = true;
      if (hint) { hint.hidden = false; hint.textContent = "Opening Notion…"; }
      try {
        const res = await win.fetch("/api/notion/connect", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" }, body: "{}",
        });
        const body = res.ok ? await res.json() : null;
        if (body && typeof body.url === "string" &&
            body.url.startsWith("https://api.notion.com/v1/oauth/authorize?")) {
          win.location.href = body.url;  // back to / after signing in
          return;
        }
      } catch (e) { /* shown below */ }
      btn.disabled = false;
      if (hint) hint.textContent = "Couldn't start the Notion sign-in.";
    });
    if (unlink) {
      unlink.addEventListener("click", async () => {
        const ask = "Disconnect Notion? I won't see your assignments any more.";
        if (typeof win.confirm === "function" && !win.confirm(translated(win, ask))) return;
        unlink.disabled = true;
        let ok = false;
        try {
          const res = await win.fetch("/api/notion/disconnect", {
            method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/json" }, body: "{}",
          });
          ok = res.ok;
        } catch (e) { /* shown below */ }
        unlink.disabled = false;
        if (ok) await refresh();
        if (hint) {
          hint.hidden = false;
          hint.textContent = ok ? "Notion disconnected." : "Couldn't disconnect. Try again.";
        }
      });
    }
    const menu = doc.querySelector("details.menu");
    if (menu) menu.addEventListener("toggle", () => { if (menu.open) refresh(); });

    return {
      show(isMember) {
        const was = member;
        member = Boolean(isMember);
        if (member !== was || !member) refresh();
      },
      refresh,
    };
  }

  // confirm() text isn't in the page, so i18n.js's observer can't see it
  function translated(win, text) {
    const i18n = win.KwilI18n;
    return i18n && win.document && i18n.isActive(win.document) ? i18n.translate(text) : text;
  }

  function onState(state, win = root, doc = root.document) {
    if (!doc) return;
    if (!setup) setup = create(win, doc);
    if (setup) setup.show(state && state.owner === false);
  }

  root.KwilNotion = { onState, create };
})(typeof window !== "undefined" ? window : globalThis);
