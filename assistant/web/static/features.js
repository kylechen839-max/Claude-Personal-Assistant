// Lecture recorder and homework tutor screens for the Hey Claude phone page.
// Adds "Chat / Lecture / Tutor" tabs to the signed-in app; app.js keeps
// running the chat. Server text is never parsed as HTML: everything is built
// with DOM calls, and maths is drawn by the vendored KaTeX (katex.render).
"use strict";

(function () {
  const app = document.getElementById("app");
  if (!app) return;

  // ---------- helpers ----------
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, "");
      else if (v !== false && v != null) el.setAttribute(k, v);
    }
    for (const c of children.flat()) {
      if (c != null) el.append(c instanceof Node ? c : String(c));
    }
    return el;
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // With failover (app.js sets window.heyClaudeFailover) requests go to the
  // active backend with a bearer token; otherwise same-origin with the cookie.
  const failover = () => window.heyClaudeFailover && window.heyClaudeFailover.request
    ? window.heyClaudeFailover : null;
  async function api(url, options = {}) {
    const f = failover();
    const res = f ? await f.request(url, options)
                  : await fetch(url, { credentials: "same-origin", ...options });
    let body = {};
    try { body = await res.json(); } catch (e) { /* not JSON */ }
    if (res.status === 401) location.reload();  // signed out: show the login
    return { ok: res.ok, status: res.status, body };
  }
  const postJSON = (url, data) => api(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data || {}) });
  const mb = (n) => (n / 1e6).toFixed(1) + " MB";
  const clock = (s) => {
    s = Math.floor(s);
    const hh = Math.floor(s / 3600), mm = Math.floor(s / 60) % 60, ss = s % 60;
    return (hh ? hh + ":" + String(mm).padStart(2, "0") : mm) + ":" + String(ss).padStart(2, "0");
  };

  // ---------- maths: $...$ and $$...$$ drawn with KaTeX ----------
  function renderMath(container, text) {
    container.textContent = "";
    const re = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$(?![\s$])([^$\n]*?[^\s\\$])\$(?!\d)/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) container.append(text.slice(last, m.index));
      const display = m[1] != null || m[2] != null;
      const expr = m[1] ?? m[2] ?? m[3] ?? m[4];
      const span = h(display ? "div" : "span", { class: display ? "math display" : "math" });
      try {
        window.katex.render(expr, span, { displayMode: display, throwOnError: false });
      } catch (e) { span.textContent = expr; }
      container.append(span);
      last = re.lastIndex;
    }
    if (last < text.length) container.append(text.slice(last));
  }

  // ---------- tabs ----------
  const panels = {};
  const tabBar = h("nav", { class: "tabs", role: "tablist" });
  function selectTab(name) {
    app.dataset.tab = name;
    for (const b of tabBar.children) b.setAttribute("aria-selected", String(b.dataset.tab === name));
    try { sessionStorage.setItem("tab", name); } catch (e) { /* ignore */ }
    if (name === "lecture") lecture.refresh();
  }
  for (const [name, label] of [["chat", "Chat"], ["lecture", "Lecture"], ["tutor", "Tutor"]]) {
    tabBar.append(h("button", { type: "button", role: "tab", "data-tab": name,
                               onclick: () => selectTab(name) }, label));
  }
  app.querySelector("header").after(tabBar);

  // ============================================================ lecture
  const lecture = (() => {
    const course = h("select", { id: "lec-course", "aria-label": "Course" },
                     h("option", { value: "" }, "Course (optional)"));
    const startBtn = h("button", { type: "button", class: "record" }, "Start recording");
    const elapsed = h("p", { class: "elapsed", "aria-live": "off" }, "0:00");
    const upStatus = h("p", { class: "status" });
    const jobsList = h("div", { class: "jobs" });
    const panel = h("section", { id: "lecture-panel", class: "panel" },
      h("p", { class: "reminder" },
        "Ask your professor before recording a lecture. Some classes and schools don't allow it."),
      course, elapsed, startBtn, upStatus,
      h("h2", {}, "Recent lectures"), jobsList);
    panels.lecture = panel;

    let recorder = null, stream = null, wakeLock = null, startedAt = 0, timer = null;
    let upload = null;  // {id, chunkBytes, base, blobs: [], sent, stopped, done}

    async function keepAwake() {
      try { if ("wakeLock" in navigator) wakeLock = await navigator.wakeLock.request("screen"); }
      catch (e) { /* not allowed (battery saver); recording still works */ }
    }
    document.addEventListener("visibilitychange", () => {
      if (recorder && document.visibilityState === "visible") keepAwake();
    });

    function pickMime() {
      for (const t of ["audio/webm;codecs=opus", "audio/mp4", "audio/webm", "audio/ogg;codecs=opus"]) {
        if (window.MediaRecorder && MediaRecorder.isTypeSupported(t)) return t;
      }
      return "";
    }

    function showUpload(note) {
      if (!upload) { upStatus.textContent = note || ""; return; }
      const pending = upload.blobs.reduce((n, b) => n + b.size, 0);
      upStatus.textContent = `Uploaded ${mb(upload.sent)}` +
        (pending ? `, ${mb(pending)} waiting` : "") + (note ? ` (${note})` : "");
    }

    // Upload whatever is buffered, from the server's offset, retrying with
    // backoff after network errors. Runs as one loop per recording.
    async function pump() {
      let delay = 1000;
      while (upload && !upload.done) {
        const pending = upload.blobs.reduce((n, b) => n + b.size, 0);
        if (!pending) {
          if (upload.stopped) { await finish(); return; }
          await sleep(500);
          continue;
        }
        const all = new Blob(upload.blobs);
        const chunk = all.slice(0, Math.min(upload.chunkBytes, all.size));
        let res;
        try {
          res = await api(`/api/lecture/${upload.id}/chunk?offset=${upload.base}`,
                          { method: "POST", headers: { "Content-Type": "application/octet-stream" },
                            body: chunk });
        } catch (e) {
          showUpload(`offline, retrying in ${Math.round(delay / 1000)} s`);
          await sleep(delay);
          delay = Math.min(delay * 2, 30000);
          continue;
        }
        if (res.ok || res.status === 409) {
          const received = res.body.received;
          if (typeof received !== "number" || received < upload.base) {
            showUpload("the server lost part of the recording");
            upload.done = true;
            return;
          }
          upload.blobs = [all.slice(received - upload.base)];
          upload.base = upload.sent = received;
          delay = 1000;
          showUpload();
        } else {
          showUpload(res.status === 404
            ? "the server changed (failover), so this recording stopped there"
            : res.body.error || "upload refused");
          if (res.status === 413 || res.status === 404) {
            stop();
            upload.done = true;
            if (failover() && failover().endTurn) failover().endTurn();
            return;
          }
          await sleep(delay);
          delay = Math.min(delay * 2, 30000);
        }
      }
    }

    async function finish() {
      for (let delay = 1000; ; delay = Math.min(delay * 2, 30000)) {
        let res;
        try { res = await postJSON(`/api/lecture/${upload.id}/finish`, { total: upload.sent }); }
        catch (e) { showUpload("offline, retrying"); await sleep(delay); continue; }
        if (res.ok) break;
        if (res.status === 409) { upload.base = upload.sent = res.body.received; return pump(); }
        showUpload(res.body.error || "could not finish"); return;
      }
      upload.done = true;
      if (failover() && failover().endTurn) failover().endTurn();
      try { localStorage.removeItem("lectureUpload"); } catch (e) { /* ignore */ }
      upload = null;
      showUpload("Uploaded. Transcribing on the server...");
      refresh();
    }

    async function start() {
      const mime = pickMime();
      if (!window.MediaRecorder) { upStatus.textContent = "This browser can't record audio."; return; }
      try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
      catch (e) { upStatus.textContent = "Microphone not allowed."; return; }
      const res = await postJSON("/api/lecture/start", {
        course: course.value, mime, started_at: new Date().toISOString() });
      if (!res.ok) {
        upStatus.textContent = res.body.error || "Couldn't start.";
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      upload = { id: res.body.id, chunkBytes: res.body.chunk_bytes || 4e6, base: 0,
                 blobs: [], sent: 0, stopped: false, done: false };
      try { localStorage.setItem("lectureUpload", upload.id); } catch (e) { /* ignore */ }
      // The recording lives on one backend: don't switch while it uploads.
      if (failover() && failover().beginTurn) failover().beginTurn();
      recorder = new MediaRecorder(stream, { ...(mime ? { mimeType: mime } : {}),
                                             audioBitsPerSecond: 32000 });
      recorder.ondataavailable = (e) => { if (e.data && e.data.size) upload.blobs.push(e.data); };
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        if (upload) upload.stopped = true;
      };
      recorder.start(10000);  // a piece every 10 s, uploaded as it comes
      startedAt = Date.now();
      timer = setInterval(() => { elapsed.textContent = clock((Date.now() - startedAt) / 1000); }, 500);
      startBtn.textContent = "Stop recording";
      startBtn.classList.add("recording");
      course.disabled = true;
      keepAwake();
      pump();
    }

    function stop() {
      if (recorder && recorder.state !== "inactive") recorder.stop();
      recorder = null;
      clearInterval(timer);
      startBtn.textContent = "Start recording";
      startBtn.classList.remove("recording");
      course.disabled = false;
      if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
      showUpload("finishing upload");
    }

    startBtn.addEventListener("click", () => (recorder ? stop() : start()));
    window.addEventListener("beforeunload", (e) => {
      if (recorder || (upload && !upload.done)) { e.preventDefault(); e.returnValue = ""; }
    });

    // ----- jobs -----
    let polling = null;
    function jobView(job) {
      const pct = Math.round((job.progress || 0) * 100);
      const words = { queued: "Waiting", transcribing: `Transcribing ${pct}%`,
                      summarizing: "Summarizing", saving: "Saving to Notion",
                      done: "Done", failed: "Failed" };
      const when = job.started_at ? new Date(job.started_at).toLocaleString() : "";
      const box = h("div", { class: `job ${job.status}` },
        h("div", { class: "job-head" },
          h("strong", {}, job.title || job.course || "Lecture"),
          h("span", {}, words[job.status] || job.status)),
        h("p", { class: "hint" }, [job.course, when].filter(Boolean).join(" · ")));
      if (job.status === "transcribing") {
        box.append(h("progress", { max: "100", value: String(pct) }));
      }
      if (job.notion_url && /^https:\/\//.test(job.notion_url)) {
        box.append(h("a", { href: job.notion_url, target: "_blank", rel: "noopener noreferrer" },
                     "Open the notes in Notion"));
      }
      if (job.status === "failed") {
        box.append(h("p", { class: "error" }, job.error || "Something went wrong."),
                   h("button", { type: "button", onclick: async () => {
                     await postJSON(`/api/lecture/${job.id}/retry`); refresh(); } }, "Try again"));
      }
      const offered = (job.candidates || []).filter((c) => c.name);
      if (offered.length) {
        box.append(h("p", { class: "hint" }, "These look like assignments. Add any to Notion?"));
        for (const c of offered) {
          const added = (job.added || []).includes(c.index);
          const btn = h("button", { type: "button", disabled: added }, added ? "Added" : "Add");
          btn.addEventListener("click", async () => {
            btn.disabled = true;
            const res = await postJSON(`/api/lecture/${job.id}/assignments`, { index: c.index });
            btn.textContent = res.ok ? "Added" : "Failed";
            if (!res.ok) btn.disabled = false;
          });
          box.append(h("div", { class: "candidate" },
            h("span", {}, c.name + (c.due ? ` (due ${c.due})` : "")), btn));
        }
      }
      return box;
    }

    async function refresh() {
      let res;
      try { res = await api("/api/lectures"); } catch (e) { return; }
      if (!res.ok) return;
      const jobs = res.body.jobs || [];
      jobsList.replaceChildren(...(jobs.length ? jobs.map(jobView)
                                    : [h("p", { class: "hint" }, "No lectures yet.")]));
      const active = jobs.some((j) => !["done", "failed"].includes(j.status));
      clearTimeout(polling);
      if (active && app.dataset.tab === "lecture") polling = setTimeout(refresh, 3000);
    }

    let coursesLoaded = false;
    async function loadCourses() {
      if (coursesLoaded) return;
      const res = await api("/api/lecture/courses").catch(() => null);
      if (!res || !res.ok) return;
      coursesLoaded = true;
      for (const name of res.body.courses || []) course.append(h("option", { value: name }, name));
      try {
        const last = localStorage.getItem("lectureCourse");
        if (last) course.value = last;
      } catch (e) { /* ignore */ }
    }
    course.addEventListener("change", () => {
      try { localStorage.setItem("lectureCourse", course.value); } catch (e) { /* ignore */ }
    });

    return { panel, refresh: () => { loadCourses(); refresh(); } };
  })();

  // ============================================================ tutor
  const tutor = (() => {
    const camera = h("input", { type: "file", accept: "image/*", capture: "environment", hidden: true });
    const gallery = h("input", { type: "file", accept: "image/jpeg,image/png,image/webp,image/*", hidden: true });
    const preview = h("img", { class: "preview", alt: "The problem", hidden: true });
    const question = h("textarea", { rows: "2", placeholder: "What are you stuck on? (optional)" });
    const full = h("input", { type: "checkbox" });
    const speak = h("input", { type: "checkbox" });
    const send = h("button", { type: "button", class: "send" }, "Ask");
    const thread = h("div", { class: "thread", "aria-live": "polite" });
    const status = h("p", { class: "status" });
    const panel = h("section", { id: "tutor-panel", class: "panel" },
      h("div", { class: "row" },
        h("button", { type: "button", onclick: () => camera.click() }, "Take photo"),
        h("button", { type: "button", onclick: () => gallery.click() }, "Choose photo")),
      camera, gallery, preview, thread, status, question,
      h("label", {}, full, " Show the full solution"),
      h("label", {}, speak, " Read explanations aloud"),
      send);
    panels.tutor = panel;
    try { speak.checked = localStorage.getItem("tutorSpeak") !== "0"; } catch (e) { speak.checked = true; }
    speak.addEventListener("change", () => {
      try { localStorage.setItem("tutorSpeak", speak.checked ? "1" : "0"); } catch (e) { /* ignore */ }
    });

    let photo = null;  // resized JPEG Blob waiting to be sent with the next question
    const player = new Audio();
    let blobUrl = null;
    async function playAudio(url) {
      const f = failover();
      if (!f) { player.src = url; player.play().catch(() => {}); return; }
      try {  // another origin and a bearer token: fetch it, then play the blob
        const r = await f.request(url);
        if (!r.ok) return;
        if (blobUrl) URL.revokeObjectURL(blobUrl);
        blobUrl = URL.createObjectURL(await r.blob());
        player.src = blobUrl;
        player.play().catch(() => {});
      } catch (e) { /* no audio; the text is on screen */ }
    }

    // Shrink to <= 1600 px and re-encode as JPEG (drops EXIF, saves data),
    // with the chat's attachment code (/attachments.js).
    async function resize(file) {
      const { blob, canvas } = await window.HeyClaudeAttach.shrinkImage(file);
      preview.src = canvas.toDataURL("image/jpeg", 0.6);
      preview.hidden = false;
      return blob;
    }

    async function picked(input) {
      const file = input.files && input.files[0];
      input.value = "";
      if (!file) return;
      status.textContent = "Preparing the photo...";
      try {
        photo = await resize(file);
        thread.replaceChildren();
        status.textContent = "Photo ready. Ask a question, or just tap Ask.";
      } catch (e) {
        status.textContent = "That photo couldn't be opened. Try a JPEG or PNG.";
      }
    }
    camera.addEventListener("change", () => picked(camera));
    gallery.addEventListener("change", () => picked(gallery));

    function bubble(kind, text) {
      const div = h("div", { class: `msg ${kind}` });
      if (kind === "claude") renderMath(div, text); else div.textContent = text;
      thread.append(div);
      div.scrollIntoView({ block: "end", behavior: "smooth" });
    }

    send.addEventListener("click", async () => {
      if (!photo && !thread.children.length) { status.textContent = "Take or choose a photo first."; return; }
      if (speak.checked) { player.src = "/silence.wav"; player.play().catch(() => {}); }  // iOS unlock
      const form = new FormData();
      if (photo) form.append("image", photo, "problem.jpg");
      const q = question.value.trim();
      form.append("question", q);
      if (full.checked) form.append("full", "1");
      if (speak.checked) form.append("speak", "1");
      bubble("me", q || (photo ? "(photo)" : "Next step?"));
      question.value = "";
      send.disabled = true;
      status.textContent = "Thinking...";
      try {
        const res = await api("/api/tutor", { method: "POST", body: form });
        if (!res.ok) { status.textContent = res.body.error || "Something went wrong."; return; }
        photo = null;
        full.checked = false;
        status.textContent = "";
        bubble("claude", res.body.text);
        if (speak.checked && res.body.audio) playAudio(res.body.audio);
      } catch (e) {
        status.textContent = "Couldn't reach the server.";
      } finally {
        send.disabled = false;
      }
    });

    return { panel, renderMath };
  })();

  // ---------- mount ----------
  const footer = app.querySelector("footer");
  footer.after(lecture.panel, tutor.panel);
  window.heyClaudeMath = renderMath;  // for other screens (e.g. voice mode)
  let first = "chat";
  try { first = sessionStorage.getItem("tab") || "chat"; } catch (e) { /* ignore */ }
  selectTab(first);
})();
