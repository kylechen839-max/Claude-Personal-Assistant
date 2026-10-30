// Study mode in the chat header (study.py): when /api/state says
// {"study_mode": true}, a study button (a book) turns it on
// (POST /api/study {"on": true}); while it is on, a small "Studying PHYS 361"
// pill takes the button's place, and its × turns it off ({"on": false}).
// The server applies a change from the next turn. Kwil itself turns it on
// ("quiz me on PHYS 361": start_study) or off ("stop studying"), so every
// /api/state and every turn's "done" event ({"study": {"course", "topic"}},
// none = off) redraws the pill. The course goes in with textContent only.
// app.js calls configure({ request, note }), onState(state) on every
// /api/state and onDone(event) for each turn's "done". Loaded before app.js.
"use strict";

(function (root) {
  let request = null;  // app.js's request(url, options) -> Response
  let note = null;     // app.js's addMessage("note", text)

  function configure(opts = {}) {
    if (typeof opts.request === "function") request = opts.request;
    if (typeof opts.note === "function") note = opts.note;
  }

  const TEXT = {
    studying: "Studying",
    start: "Study mode",
    stop: "Stop studying",
    on: "Study mode on. Say what to study, or ask Kwil to quiz you.",
    off: "Study mode off.",
  };

  // "Studying PHYS 361" (the course, else the topic), or just "Studying"
  function label(study) {
    const name = study && (study.course || study.topic);
    return name ? `${TEXT.studying} ${name}` : TEXT.studying;
  }

  function setup(win, doc) {
    const $ = (id) => doc.getElementById(id);
    const toggle = $("study-toggle"), pill = $("study-pill");
    const text = $("study-pill-text"), stop = $("study-stop");
    if (!toggle || !pill || !text || !stop) return null;
    let enabled = false, current = null, sending = false;
    const say = (t) => { if (note) note(t); };

    function render(study) {
      current = study || null;
      const on = enabled && current !== null;
      pill.hidden = !on;
      toggle.hidden = !enabled || on;
      toggle.setAttribute("aria-pressed", String(on));
      text.textContent = on ? label(current) : "";
    }

    async function set(on) {
      if (sending) return;
      sending = true;
      try {
        const options = { method: "POST", headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ on }) };
        const res = request ? await request("/api/study", options)
          : await win.fetch("/api/study", { credentials: "same-origin", ...options });
        let body = {};
        try { body = await res.json(); } catch (e) { /* not JSON */ }
        if (!res.ok) throw new Error(body.error || `Error ${res.status}`);
        render(body.study || null);
        say(on ? TEXT.on : TEXT.off);
      } catch (e) {
        say(e.message || String(e));
      } finally {
        sending = false;
      }
    }

    toggle.addEventListener("click", () => set(true));
    stop.addEventListener("click", () => set(false));

    return {
      enable(on) { enabled = Boolean(on); render(current); },
      render,
      get study() { return current; },
    };
  }

  let header = null;
  function ensure(win, doc) {
    if (!header && doc) header = setup(win, doc);
    return header;
  }

  // From app.js after each /api/state.
  function onState(state, win = root, doc = root.document) {
    const h = ensure(win, doc);
    if (!h) return;
    h.enable(Boolean(state && state.study_mode));
    h.render((state && state.study) || null);
  }

  // From app.js for each turn's "done": Kwil may have turned it on or off.
  function onDone(ev, win = root, doc = root.document) {
    const h = ensure(win, doc);
    if (h && ev) h.render(ev.study || null);
  }

  root.KwilStudy = { configure, onState, onDone, setup, label, TEXT };
})(typeof window !== "undefined" ? window : globalThis);
