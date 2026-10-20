// "Connect Spotify" in the owner's menu (assistant/spotify_web.py).
// app.js calls onState(state) after each /api/state: the button is shown
// only to the owner, and only when /api/spotify/status says it is set up
// ([spotify] enabled, SPOTIFY_CLIENT_ID, [webapp] public_origin). A family
// member's session gets 404 from those routes, so it never shows for them.
// Tapping it opens Spotify's consent page; Spotify returns to
// /spotify/callback, which saves the sign-in and goes back to /.
// Its text is translated by i18n.js on a Chinese page. Loaded before app.js.
"use strict";

(function (root) {
  let setup = null;

  function create(win, doc) {
    const btn = doc.getElementById("spotify-connect");
    const hint = doc.getElementById("spotify-status");
    if (!btn) return null;
    let owner = false;

    async function refresh() {
      if (!owner) { btn.hidden = true; if (hint) hint.hidden = true; return; }
      try {
        const res = await win.fetch("/api/spotify/status",
                                    { credentials: "same-origin", cache: "no-store" });
        if (!res.ok) { btn.hidden = true; return; }  // off, or not the owner
        const st = await res.json();
        btn.hidden = !st.enabled;
        btn.textContent = st.connected ? "Reconnect Spotify" : "Connect Spotify";
        if (hint) {
          hint.hidden = !(st.enabled && st.connected);
          hint.textContent = "Spotify connected.";
        }
      } catch (e) { /* offline: leave it as it is */ }
    }

    btn.addEventListener("click", async () => {
      btn.disabled = true;
      if (hint) { hint.hidden = false; hint.textContent = "Opening Spotify…"; }
      try {
        const res = await win.fetch("/api/spotify/connect", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" }, body: "{}",
        });
        const body = res.ok ? await res.json() : null;
        if (body && typeof body.url === "string" &&
            body.url.startsWith("https://accounts.spotify.com/")) {
          win.location.href = body.url;  // back to / after signing in
          return;
        }
      } catch (e) { /* shown below */ }
      btn.disabled = false;
      if (hint) hint.textContent = "Couldn't start the Spotify sign-in.";
    });
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

  root.HeyClaudeSpotify = { onState, create };
})(typeof window !== "undefined" ? window : globalThis);
