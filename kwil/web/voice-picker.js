// "Reply voice" in the settings menu (kwil/voice_picker.py): everyone
// signed in (the owner, or a family member) picks the voice the server
// reads their replies with, from the voices that server has in their
// language. "Preview" plays a short sample; ElevenLabs voices carry a
// "paid" tag (they cost money per character). The choice is saved on the
// server, per person, and used from the next reply on.
// app.js calls configure({ request, player }) first thing and onState(state)
// on every /api/state (state.voice_picker: the server offers it). Calls go
// through that request() (with failover: the router, to the active backend).
"use strict";

(function (root) {
  let request = null;  // app.js's request(url, options) -> Response
  let player = null;   // app.js's Audio element (unlocked on iOS by a tap)

  function configure(opts = {}) {
    if (typeof opts.request === "function") request = opts.request;
    if (opts.player) player = opts.player;
  }

  function label(v) { return v.paid ? `${v.name} · paid` : v.name; }

  function setup(win, doc) {
    const $ = (id) => doc.getElementById(id);
    const box = $("voice-picker");
    if (!box) return null;
    const select = $("voice-select"), paid = $("voice-paid"), previewBtn = $("voice-preview");
    const status = $("voice-status"), customRow = $("voice-custom-row");
    const customIn = $("voice-custom"), customBtn = $("voice-custom-use");
    let voices = [];
    let current = "";
    let shown = false;
    let objectUrl = null;

    async function call(url, options = {}) {
      const res = request ? await request(url, options)
        : await win.fetch(url, { credentials: "same-origin", ...options });
      if (!res.ok) {
        let message = `Error ${res.status}`;
        try { message = (await res.json()).error || message; } catch (e) { /* not JSON */ }
        throw new Error(message);
      }
      return res;
    }
    const post = (url, data) => call(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data) });

    function selected() { return voices.find((v) => v.id === select.value) || null; }
    function showPaid() { const v = selected(); paid.hidden = !(v && v.paid); }

    function render() {
      select.textContent = "";
      for (const v of voices) {
        const opt = doc.createElement("option");
        opt.value = v.id;
        opt.textContent = label(v);  // names are text, never HTML
        select.appendChild(opt);
      }
      select.value = current;
      showPaid();
    }

    async function refresh() {
      if (!shown) return;
      try {
        const body = await (await call("/api/voices")).json();
        voices = Array.isArray(body.voices) ? body.voices : [];
        current = typeof body.current === "string" ? body.current : "";
        if (customRow) customRow.hidden = !body.custom;
        render();
      } catch (e) { /* offline: keep the list */ }
    }

    async function choose(voice) {
      status.textContent = "";
      try {
        const body = await (await post("/api/voices", { voice })).json();
        current = body.current;
        status.textContent = "Saved.";
        return true;
      } catch (e) {
        select.value = current;  // back to what the server has
        showPaid();
        status.textContent = e.message;
        return false;
      }
    }

    async function preview() {
      status.textContent = "";
      previewBtn.disabled = true;
      try {
        const blob = await (await post("/api/voices/preview", { voice: select.value })).blob();
        if (objectUrl) win.URL.revokeObjectURL(objectUrl);
        objectUrl = win.URL.createObjectURL(blob);
        const audio = player || new win.Audio();
        audio.src = objectUrl;
        await audio.play();
      } catch (e) {
        status.textContent = "Couldn't play the preview.";
      } finally {
        previewBtn.disabled = false;
      }
    }

    select.addEventListener("change", () => { showPaid(); choose(select.value); });
    previewBtn.addEventListener("click", preview);
    if (customBtn) {
      customBtn.addEventListener("click", async () => {
        const id = customIn.value.trim();
        if (!/^[A-Za-z0-9]{8,64}$/.test(id)) { status.textContent = "unknown voice"; return; }
        if (await choose(`elevenlabs:${id}`)) { customIn.value = ""; await refresh(); }
      });
    }
    const menu = doc.querySelector("details.menu");
    if (menu) menu.addEventListener("toggle", () => { if (menu.open) refresh(); });

    return {
      show(on) {
        const was = shown;
        shown = Boolean(on);
        box.hidden = !shown;
        if (shown && !was) refresh();  // then each time the menu opens
      },
      refresh,
    };
  }

  let picker = null;
  function onState(state, win = root, doc = root.document) {
    if (!doc) return;
    if (!picker) picker = setup(win, doc);
    if (picker) picker.show(Boolean(state && state.voice_picker));
  }

  root.KwilVoicePicker = { configure, onState, setup, label };
})(typeof window !== "undefined" ? window : globalThis);
