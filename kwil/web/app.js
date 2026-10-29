// Kwil phone page: record with the phone's mic, send to the PC, show
// and play the reply. The server streams NDJSON events for each turn.
"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  login: $("login"), app: $("app"), loginForm: $("login-form"), token: $("token"),
  loginError: $("login-error"), log: $("log"), status: $("status"),
  confirm: $("confirm"), question: $("confirm-question"), yes: $("yes"), no: $("no"),
  textForm: $("text-form"), text: $("text"), stop: $("stop"), queue: $("queue"), mic: $("mic"), micHint: $("mic-hint"), micMeter: $("mic-meter"),
  phoneVoice: $("phone-voice"), mute: $("mute"), logout: $("logout"),
  badge: $("badge"), backends: $("backends"), backendMode: $("backend-mode"),
  urlPi: $("url-pi"), urlPc: $("url-pc"), urlCloud: $("url-cloud"),
  backendsSave: $("backends-save"), backendStatus: $("backend-status"),
};

let busy = false;
let confirming = false;
let stopping = false;  // Stop was pressed; the turn is ending
let live = null;  // the reply bubble that streamed text is going into
// Messages sent while Kwil is still answering: shown as "Queued" bubbles
// and sent in order, one turn at a time, once the reply is done. Kept in
// this page's memory only (never stored), a few at most.
const MAX_QUEUE = 3;
const queue = [];  // {text, el, cid, epoch}
// The chat pane: `cid` is the stored conversation it shows (history.js's
// Continue binds it; null for a new chat). Every turn typed into it carries
// that id, so the server continues that conversation however long ago it
// ended. `epoch` changes with every binding: a turn's late text never lands
// in a pane opened after it was sent.
const pane = { cid: null, epoch: 0 };
let confirmEpoch = 0;  // the pane the open yes/no question was asked in
const player = new Audio();
let audioUnlocked = false;

// --- settings (this phone only) ---
function loadSetting(el, key) {
  try { el.checked = localStorage.getItem(key) === "1"; } catch (e) { /* private mode */ }
  el.addEventListener("change", () => {
    try { localStorage.setItem(key, el.checked ? "1" : "0"); } catch (e) { /* ignore */ }
  });
}
loadSetting(els.phoneVoice, "phoneVoice");
loadSetting(els.mute, "mute");
if (!("speechSynthesis" in window)) els.phoneVoice.disabled = true;

// --- views ---
function show(view) {
  els.login.hidden = view !== "login";
  els.app.hidden = view !== "app";
  for (const id of ["history-view", "transcript-view", "memory-view", "brief-view"]) {  // history.js, memory.js, brief.js
    const el = $(id);
    if (el) el.hidden = view !== id;
  }
}

function addMessage(kind, text) {
  const div = document.createElement("div");
  div.className = `msg ${kind}`;
  div.textContent = text;
  els.log.appendChild(div);
  els.log.scrollTop = els.log.scrollHeight;
  return div;
}

function setStatus(text) { els.status.textContent = text || ""; }

function setBusy(value) {
  busy = value;
  // The text box stays usable while Kwil answers: Send then queues.
  els.mic.disabled = value && !confirming;
  if (!value) stopping = false;
  const hide = !value || confirming;
  if (hide && !els.stop.hidden && document.activeElement === els.stop) els.text.focus();
  els.stop.hidden = hide;
  els.stop.disabled = stopping;
}

// --- queued messages ---
function enqueue(text, cid = pane.cid, epoch = pane.epoch) {
  if (queue.length >= MAX_QUEUE) {
    addMessage("note", `You can queue up to ${MAX_QUEUE} messages.`);
    return false;
  }
  const el = document.createElement("div");
  el.className = "msg me queued";
  const words = document.createElement("span");
  words.textContent = text;
  const tag = document.createElement("span");
  tag.className = "queued-tag";
  tag.textContent = "Queued";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "queued-remove";
  remove.setAttribute("aria-label", "Remove queued message");
  remove.textContent = "×";
  const item = { text, el, cid, epoch };  // sent into, and drawn in, the pane it was typed in
  remove.addEventListener("click", () => { unqueue(item); els.text.focus(); });
  el.appendChild(words);
  el.appendChild(tag);
  el.appendChild(remove);
  queue.push(item);
  els.queue.appendChild(el);
  els.queue.hidden = false;
  return true;
}

function unqueue(item) {
  const i = queue.indexOf(item);
  if (i >= 0) queue.splice(i, 1);
  item.el.remove();
  els.queue.hidden = queue.length === 0;
}

function clearQueue() { while (queue.length) unqueue(queue[0]); }

// Bind the chat pane to a stored conversation (or none: a new chat). Called
// by history.js; the caller redraws #log. Queued messages keep the pane
// they were typed in.
function bindPane(cid) {
  pane.cid = cid || null;
  pane.epoch += 1;
  live = null;
}

// The conversation ended on the server ("goodbye"): the pane stays as it
// is (same epoch), but what is typed next, queued messages included, starts
// a new conversation instead of resuming the ended one.
function conversationEnded(cid) {
  for (const item of queue) if (item.cid === cid) item.cid = null;
  if (pane.cid === cid) pane.cid = null;
}

// A conversation was deleted (or the server says it is gone): drop its
// queued messages, and unbind the pane if it showed it.
function forgetConversation(cid) {
  for (const item of queue.filter((q) => q.cid === cid)) unqueue(item);
  if (pane.cid === cid) bindPane(null);
}

// The next queued message, once nothing is running or being asked.
function sendQueued() {
  if (busy || confirming || !queue.length) return;
  if (!els.login.hidden) { clearQueue(); return; }  // signed out: nothing goes
  const item = queue[0];
  unqueue(item);
  sendMessage(item.text, item.cid, item.epoch);
}

// Files picked with "+" (attachments.js) go with the next message, never
// with a yes/no answer.
const tray = () => (window.KwilAttach && window.KwilAttach.tray) || null;
const pendingFiles = () => !confirming && tray() !== null && tray().count() > 0;

function setConfirm(question, epoch = pane.epoch) {
  confirming = Boolean(question);
  if (confirming) confirmEpoch = epoch;
  els.confirm.hidden = !confirming;
  els.question.textContent = question || "";
  els.text.placeholder = confirming ? 'Type "yes" or "no"' : "Type a message";
  setBusy(busy);
}

// --- audio out ---
function unlockAudio() {
  // iOS only lets a page play sound it started from a tap; play a tiny
  // silent clip on the first tap so later replies can play.
  if (audioUnlocked) return;
  audioUnlocked = true;
  player.src = "/silence.wav";
  player.play().catch(() => { audioUnlocked = false; });
}

function stopSpeaking() {
  player.pause();
  if ("speechSynthesis" in window) speechSynthesis.cancel();
}

function speakInBrowser(text) {
  if (!("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

let audioUrl = null;  // object URL of the last reply fetched with a token

function speak(text, url) {
  if (els.mute.checked) return;
  if (els.phoneVoice.checked || !url) { speakInBrowser(text); return; }
  if (router) {
    // another origin and a bearer token: fetch it, then play the blob
    request(url)
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error("no audio"))))
      .then((blob) => {
        if (audioUrl) URL.revokeObjectURL(audioUrl);
        audioUrl = URL.createObjectURL(blob);
        player.src = audioUrl;
        return player.play();
      })
      .catch(() => speakInBrowser(text));
    return;
  }
  player.src = url;
  player.play().catch(() => speakInBrowser(text));
}

// --- failover: Pi, then PC, then cloud (FAILOVER.md) ---
// Only when the server says it is part of a failover setup (/healthz has a
// "role"). The app then holds a bearer token (IndexedDB) instead of a
// cookie, and backend-router.js picks the backend for every request.
let router = null;
let routerMod = null;
let tokens = null;

function readJSON(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) || fallback; } catch (e) { return fallback; }
}
function writeJSON(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode */ }
}

async function detectFailover() {
  let on = readJSON("failover", false);
  try {
    const res = await fetch("/healthz", { cache: "no-store", credentials: "omit" });
    on = Boolean((await res.json()).role);
    writeJSON("failover", on);
  } catch (e) { /* the cloud is down: the cached answer decides */ }
  if (!on) return;
  routerMod = await import("/backend-router.js");
  tokens = routerMod.tokenStore();
  router = new routerMod.BackendRouter({
    backends: currentBackends(),
    getToken: async () => { const t = await tokens.get("token"); return t && t.access_token; },
    override: readJSON("backendOverride", "auto"),
    onChange: () => renderBackends(),
  });
  els.backends.hidden = false;
  els.badge.hidden = false;
  const user = readJSON("backendSettings", {});
  els.urlPi.value = user.pi || "";
  els.urlPc.value = user.pc || "";
  els.urlCloud.value = user.cloud || "";
  els.backendMode.value = router.override;
  router.start();
  window.addEventListener("online", () => router.select());
  // for the voice view (voice.js): connect to the active backend, token first
  window.kwilFailover = {
    wsUrl: (path) => router.wsUrl(path),
    token: async () => { const t = await tokens.get("token"); return t && t.access_token; },
    beginTurn: () => router.beginTurn(),
    endTurn: () => router.endTurn(),
    // for the lecture and tutor screens (static/features.js)
    request: (path, opts) => router.request(path, opts),
  };
}

function currentBackends() {
  return routerMod.mergeBackends(readJSON("backendsCache", []),
    readJSON("backendSettings", {}), location.origin);
}

function renderBackends() {
  if (!router) return;
  const st = router.status();
  els.badge.textContent = st.badge;
  els.badge.dataset.role = st.active || "offline";
  els.backendStatus.textContent = "";
  for (const b of st.backends) {
    const li = document.createElement("li");
    let state = b.healthy ? "healthy" : (b.reason || "not checked");
    if (!b.compatible) state = b.reason;
    li.textContent = `${b.label}: ${state}${b.role === st.active ? " (in use)" : ""}`;
    els.backendStatus.appendChild(li);
  }
}

async function saveToken(data) {
  await tokens.set("token", { access_token: data.access_token, expires_at: data.expires_at,
                              sid: data.sid });
}

async function refreshTokenIfOld() {
  const t = await tokens.get("token");
  if (!t || t.expires_at * 1000 - Date.now() > 7 * 24 * 3600 * 1000) return;
  try {
    const res = await router.request("/api/auth/refresh", { method: "POST", idempotent: true });
    if (res.ok) await saveToken(await res.json());
  } catch (e) { /* next time */ }
}

async function updateBackendList() {
  try {
    const res = await router.request("/api/backends");
    if (!res.ok) return;
    const before = JSON.stringify(router.backends);
    writeJSON("backendsCache", (await res.json()).backends || []);
    router.setBackends(currentBackends());
    if (JSON.stringify(router.backends) !== before) await router.select();  // e.g. first sign-in
    renderBackends();
  } catch (e) { /* keep the cached list */ }
}

els.backendMode.addEventListener("change", () => {
  writeJSON("backendOverride", els.backendMode.value);
  router.setOverride(els.backendMode.value);
  renderBackends();
});

els.backendsSave.addEventListener("click", async () => {
  const user = {};
  for (const [role, input] of [["pi", els.urlPi], ["pc", els.urlPc], ["cloud", els.urlCloud]]) {
    const v = input.value.trim();
    if (!v) continue;
    const url = routerMod.normalizeUrl(v);
    if (!url) { addMessage("note", `${role.toUpperCase()}: use an https:// address with no path.`); return; }
    user[role] = url;
  }
  writeJSON("backendSettings", user);
  router.setBackends(currentBackends());
  await router.select();
  renderBackends();
});

// --- talking to the server ---
async function request(url, options = {}) {
  if (router) {
    let res;
    try {
      res = await router.request(url, options);
    } catch (e) {
      if (e.name === "NoBackend") throw new Error("Can't reach the Pi, the PC or the cloud right now.");
      throw e;  // NeedsUserRetry: "Pi didn't answer; tap to try again"
    } finally {
      renderBackends();
    }
    if (res.status === 401) {
      await tokens.delete("token");
      show("login");
      throw new Error("Please sign in.");
    }
    return res;
  }
  const res = await fetch(url, { credentials: "same-origin", ...options });
  if (res.status === 401) { show("login"); throw new Error("Please sign in."); }
  return res;
}

// Typed text answers the open question only in the pane it was asked in.
const answersHere = () => confirming && confirmEpoch === pane.epoch;

async function readEvents(res, epoch = pane.epoch, cid = null) {
  if (!res.ok) {
    let message = `Error ${res.status}`;
    try { message = (await res.json()).error || message; } catch (e) { /* not JSON */ }
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (line) handleEvent(JSON.parse(line), epoch, cid);
    }
  }
}

// `epoch`: the pane the turn was sent from. Its text (delta, transcript,
// reply) is drawn only while that pane is still shown; a question, errors
// and "done" always are.
function handleEvent(ev, epoch = pane.epoch, cid = null) {
  const here = epoch === pane.epoch;
  if (!here && ["delta", "transcript", "reply"].includes(ev.type)) {
    if (ev.type === "reply") setConfirm(null);
    return;
  }
  if (ev.type !== "delta" && ev.type !== "reply") live = null;
  switch (ev.type) {
    case "delta":
      if (!live) live = addMessage("claude", "");
      live.textContent += ev.text;
      els.log.scrollTop = els.log.scrollHeight;
      break;
    case "transcript":
      addMessage("me", ev.text || "(nothing heard)");
      setStatus("Thinking…");
      break;
    case "tool":
      setStatus(`Working: ${ev.name.replace(/_/g, " ")}…`);
      break;
    case "status":
      addMessage("note", ev.text);
      break;
    case "confirm":
      addMessage("claude", ev.question);
      setConfirm(ev.question, epoch);
      setStatus("");
      speak(`${ev.question} Please say yes or no.`, ev.audio);
      break;
    case "reply":
      setConfirm(null);
      if (ev.stopped) {  // the Stop button: what came so far, not spoken
        // Its words were all streamed already (an earlier tool round's
        // too, into their own bubble): keep them, add none.
        if (live && !live.textContent) live.remove();
        live = null;
        addMessage("note", "Stopped.");
        setStatus("");
        break;
      }
      if (live) { live.textContent = ev.text; live = null; } else addMessage("claude", ev.text);
      setStatus("");
      speak(ev.text, ev.audio);
      if (ev.ended) addMessage("note", "Conversation ended.");
      if (ev.ended && cid) conversationEnded(cid);
      break;
    case "error":
      addMessage("note", ev.message);
      if (ev.gone && cid) forgetConversation(cid);  // deleted elsewhere
      break;
    case "done":
      // A bound turn that left no conversation (it ended): unbind. A fresh
      // pane never binds to the id here, so its idle timeout still holds.
      if (here && cid && ev.conversation === null) conversationEnded(cid);
      setConfirm(null);
      setStatus("");
      break;
  }
}

async function send(body, sentFiles, cid = pane.cid, from = pane.epoch) {
  // body: {text} | FormData with "audio" and/or "text" and "attachment"s.
  // sentFiles: called once the server took the turn (clears the chips).
  // cid: the conversation it goes into (the pane's when it was typed); a
  // yes/no answer belongs to its question's turn and never carries one.
  // from: the pane it was typed in (a queued message's may be gone).
  const answering = confirming;
  const question = els.question.textContent;
  const asked = confirmEpoch;
  const epoch = answering ? asked : from;  // an answer's turn: its question's pane
  const url = answering ? "/api/confirm" : "/api/turn";
  const into = answering ? null : cid;
  let options;
  if (body instanceof FormData) {
    if (into) body.append("conversation_id", into);
    options = { method: "POST", body };
  } else {
    const payload = answering ? { answer: body.text } : { text: body.text };
    if (into) payload.conversation_id = into;
    options = { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload) };
    if (epoch === pane.epoch) addMessage("me", body.text);
  }
  // Answered: text typed from now on is a new message (queued), not
  // another answer. A failed answer brings the question back.
  if (answering) setConfirm(null);
  setBusy(true);
  setStatus(body instanceof FormData && body.has("audio") ? "Listening back…" : "Thinking…");
  if (router) router.beginTurn();  // no backend switch until the reply is done
  try {
    const res = await request(url, options);
    if (res.ok && sentFiles) sentFiles();
    await readEvents(res, epoch, into);
  } catch (e) {
    addMessage("note", e.message || String(e));
    setStatus("");
    if (into && e.status === 404) forgetConversation(into);  // deleted elsewhere
    // the question is gone on the server (409: expired or answered
    // elsewhere): don't bring it back, the queue may go
    if (answering && !confirming && e.status !== 409) setConfirm(question, asked);
  } finally {
    if (router) router.endTurn();
    setBusy(false);
    sendQueued();
  }
}

async function refreshState() {
  if (router && !(await tokens.get("token"))) { show("login"); return; }
  if (router) { refreshTokenIfOld(); updateBackendList(); }
  try {
    const res = await request("/api/state");
    const state = await res.json();
    const elsewhere = ["history-view", "transcript-view", "memory-view", "brief-view"].some((id) => $(id) && !$(id).hidden);
    if (!elsewhere) show("app");
    if (typeof onHistoryState === "function") onHistoryState(state);  // history.js
    if (window.KwilFamily) window.KwilFamily.onState(state);  // family.js
    if (window.KwilMemory) window.KwilMemory.onState(state);  // memory.js
    if (window.KwilSpotify) window.KwilSpotify.onState(state);  // spotify.js
    if (window.KwilNotion) window.KwilNotion.onState(state);  // notion.js
    if (window.KwilConnect) window.KwilConnect.onState(state);  // connect.js
    if (window.KwilVoicePicker) window.KwilVoicePicker.onState(state);  // voice-picker.js
    if (window.KwilBrief) window.KwilBrief.onState(state);  // brief.js
    if (window.KwilUI) window.KwilUI.renderHealth(document, state.health);  // ui.js
    setConfirm(state.confirm);
    if (!state.busy && !state.confirm) sendQueued();  // e.g. the question expired
    if (state.busy && !state.confirm) {
      setBusy(true);
      setStatus("Thinking…");
      try { await readEvents(await request("/api/events")); } finally { setBusy(false); sendQueued(); }
    }
  } catch (e) {
    /* 401 already showed the login */
  }
}

// --- login ---
els.loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  await login(els.token.value);
});

async function login(token) {
  els.loginError.textContent = "";
  if (router) {
    // trade WEB_TOKEN for an access token; WEB_TOKEN itself is not kept
    try {
      const old = await tokens.get("token");
      const res = await router.request("/api/auth/token", {
        method: "POST", idempotent: true, headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token.trim(), sid: old && old.sid }),
      });
      if (!res.ok) {
        let message = "Sign-in failed.";
        try { message = (await res.json()).error || message; } catch (err) { /* ignore */ }
        els.loginError.textContent = message;
        show("login");
        return;
      }
      await saveToken(await res.json());
      els.token.value = "";
      show("app");
      refreshState();
    } catch (e) {
      els.loginError.textContent = e.name === "NoBackend"
        ? "Can't reach the Pi, the PC or the cloud right now." : (e.message || String(e));
      show("login");
    } finally {
      renderBackends();
    }
    return;
  }
  const res = await fetch("/api/login", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: token.trim() }),
  });
  if (res.ok) {
    els.token.value = "";
    show("app");
    refreshState();
  } else {
    let message = "Sign-in failed.";
    try { message = (await res.json()).error || message; } catch (err) { /* ignore */ }
    els.loginError.textContent = message;
    show("login");
  }
}

async function signOut(path) {
  if (router) {
    try { await request(path, { method: "POST" }); } catch (e) { /* signed out anyway */ }
    await tokens.delete("token");
    els.log.textContent = "";
    clearQueue();
    show("login");
    return;
  }
  await fetch(path, { method: "POST", credentials: "same-origin" });
  els.log.textContent = "";
  clearQueue();
  show("login");
}
els.logout.addEventListener("click", () => signOut("/api/logout"));
$("logout-all").addEventListener("click", () => {
  if (confirm("Sign out on every phone and browser?")) signOut("/api/logout-all");
});

// --- text ---
els.textForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = els.text.value.trim();
  // Still answering, or asked a yes/no question in another pane: queued.
  if ((busy || confirming) && !answersHere()) {
    // Still answering: the message waits its turn. Picked files stay in
    // the tray and go with the next message that is sent.
    if (text && enqueue(text)) { unlockAudio(); els.text.value = ""; }
    return;
  }
  if (!text && !pendingFiles()) return;
  unlockAudio();
  els.text.value = "";
  sendMessage(text);
});

// A typed message (or a yes/no answer while asked), with any picked files,
// into conversation `cid` (the pane's when it was typed).
async function sendMessage(text, cid = pane.cid, epoch = pane.epoch) {
  if (!pendingFiles()) {
    send({ text }, undefined, cid, epoch);
    return;
  }
  setBusy(true);  // no second send while photos are still being shrunk
  await tray().whenReady();
  setBusy(false);
  if (!tray().count() && !text) { sendQueued(); return; }  // the only photo couldn't be opened
  const form = new FormData();
  if (text) form.append("text", text);
  tray().appendTo(form);
  if (epoch === pane.epoch) addMessage("me", [text, tray().summary()].filter(Boolean).join("\n"));
  send(form, () => tray().clear(), cid, epoch);
}

// Stop: the server ends the turn where it is (POST /api/stop), its stream
// ends as usual and the next queued message goes. If the stop fails, the
// reply just runs to its end: the server never runs two turns at once.
els.stop.addEventListener("click", async () => {
  if (!busy || confirming || stopping) return;
  stopping = true;
  els.stop.disabled = true;
  stopSpeaking();
  try { await request("/api/stop", { method: "POST" }); } catch (e) { /* runs to its end */ }
});

els.yes.addEventListener("click", () => { unlockAudio(); send({ text: "yes" }); });
els.no.addEventListener("click", () => { unlockAudio(); send({ text: "no" }); });

// --- microphone ---
const MAX_RECORD_MS = 30000;
const HOLD_MS = 450;
let stream = null;
let recorder = null;
let chunks = [];
let pressedAt = 0;
let releaseStops = false;
let maxTimer = null;
let starting = false;  // waiting for the mic permission / stream

function pickMime() {
  const types = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus", "audio/webm"];
  if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return "";
  return types.find((t) => MediaRecorder.isTypeSupported(t)) || "";
}

function extensionFor(mime) {
  if (mime.includes("mp4")) return "m4a";
  if (mime.includes("ogg")) return "ogg";
  return "webm";
}

// Called straight from the button's tap handler (iOS only grants the mic
// from a user gesture, also in a home-screen app).
function getStream() {
  return navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
}

function releaseStream() {
  // Stop the tracks after every recording so the mic indicator goes away.
  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
}

// Level meter while recording, so you can see it picking up sound. Its own
// small context at the mic's sample rate (Safari gives a context at another
// rate silence); purely visual, so any failure just hides it.
let meter = null;
function startMeter(s) {
  if (!els.micMeter) return;
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const rate = (s.getAudioTracks()[0]?.getSettings?.() || {}).sampleRate;
    let ac;
    try { ac = rate ? new Ctx({ sampleRate: rate }) : new Ctx(); } catch (e) { ac = new Ctx(); }
    ac.resume().catch(() => {});
    const analyser = ac.createAnalyser();
    analyser.fftSize = 512;
    const silent = ac.createGain();
    silent.gain.value = 0; // Safari only runs a graph that reaches the output
    ac.createMediaStreamSource(s).connect(analyser);
    analyser.connect(silent);
    silent.connect(ac.destination);
    const data = new Uint8Array(analyser.fftSize);
    meter = { ac, raf: 0 };
    els.micMeter.hidden = false;
    const tick = () => {
      if (!meter) return;
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) { const v = (data[i] - 128) / 128; sum += v * v; }
      const level = Math.min(1, Math.sqrt(sum / data.length) * 4);
      els.micMeter.style.setProperty("--mic", level.toFixed(3));
      els.micMeter.classList.toggle("hearing", level > 0.12);
      meter.raf = requestAnimationFrame(tick);
    };
    tick();
  } catch (e) {
    stopMeter();
  }
}

function stopMeter() {
  if (meter) {
    cancelAnimationFrame(meter.raf);
    meter.ac.close().catch(() => {});
    meter = null;
  }
  if (els.micMeter) {
    els.micMeter.hidden = true;
    els.micMeter.classList.remove("hearing");
    els.micMeter.style.setProperty("--mic", "0");
  }
}

function micHelp(e) {
  if (e && (e.name === "NotAllowedError" || e.name === "SecurityError")) {
    return "Microphone access is blocked. On iPhone: Settings → Safari → Microphone → " +
      "Allow (or in Safari, aA → Website Settings → Microphone), then reopen the app. " +
      "On Android: tap the lock icon by the address → Permissions → Microphone.";
  }
  return `Microphone unavailable: ${(e && (e.message || e.name)) || e}`;
}

async function startRecording() {
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    addMessage("note", "This browser can't record. It needs HTTPS (see the README's Tailscale steps).");
    return;
  }
  let s;
  starting = true;
  try {
    const pending = getStream();  // ask before any await: still inside the tap
    stopSpeaking();
    s = await pending;
  } catch (e) {
    addMessage("note", micHelp(e));
    return;
  } finally {
    starting = false;
  }
  stream = s;
  const mime = pickMime();
  chunks = [];
  recorder = new MediaRecorder(s, mime ? { mimeType: mime } : undefined);
  recorder.addEventListener("dataavailable", (e) => { if (e.data.size) chunks.push(e.data); });
  recorder.addEventListener("stop", () => {
    const type = recorder.mimeType || mime || "audio/webm";
    const blob = new Blob(chunks, { type });
    recorder = null;
    stopMeter();
    els.mic.classList.remove("recording");
    els.micHint.textContent = "Hold to talk, or tap to start and stop";
    releaseStream();
    if (blob.size < 1000) { setStatus("Too short. Try again."); return; }
    const form = new FormData();
    form.append("audio", blob, `speech.${extensionFor(type)}`);
    if (pendingFiles()) {  // push-to-talk takes the pending files along
      tray().whenReady().then(() => {
        tray().appendTo(form);
        if (tray().count()) addMessage("me", tray().summary());
        send(form, () => tray().clear());
      });
      return;
    }
    send(form);
  });
  recorder.start();
  startMeter(s);
  els.mic.classList.add("recording");
  els.micHint.textContent = "Listening… release or tap to send";
  maxTimer = setTimeout(stopRecording, MAX_RECORD_MS);
  if (releaseStops) stopRecording();  // released before the mic was ready
}

function stopRecording() {
  clearTimeout(maxTimer);
  releaseStops = false;
  if (recorder && recorder.state !== "inactive") recorder.stop();
}

els.mic.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  if (els.mic.disabled) return;
  unlockAudio();
  if (recorder) { stopRecording(); return; }  // second tap of tap-to-toggle
  if (starting) { releaseStops = true; return; }
  pressedAt = Date.now();
  releaseStops = false;
  startRecording();
});

function onRelease() {
  if (!pressedAt) return;
  const held = Date.now() - pressedAt;
  pressedAt = 0;
  if (held < HOLD_MS) return;  // a tap: keep recording until the next tap
  if (recorder) stopRecording(); else releaseStops = true;
}
els.mic.addEventListener("pointerup", onRelease);
els.mic.addEventListener("pointercancel", onRelease);
els.mic.addEventListener("contextmenu", (e) => e.preventDefault());

// --- start ---
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState !== "visible") return;
  if (router) { await router.select(); renderBackends(); }
  if (!els.app.hidden && !busy) refreshState();
});

// For history.js and voice.js: the chat pane's binding, and requests that
// go through the failover router like the page's own.
window.KwilApp = {
  bindPane,
  forgetConversation,
  pane: () => ({ cid: pane.cid, epoch: pane.epoch }),
  request,
};

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

(async function start() {
  // A family invite link ("#invite=", family.js): sign in as that person.
  const family = window.KwilFamily;
  if (family) family.configure({ request });  // the owner's invites: through the router too
  if (window.KwilMemory) window.KwilMemory.configure({ request, show });  // memory.js
  if (window.KwilVoicePicker) window.KwilVoicePicker.configure({ request, player });
  if (window.KwilBrief) window.KwilBrief.configure({ request, show, player });  // brief.js
  if (family && await family.acceptInvite(window)) return;  // the page reloads
  // A family member's phone talks to this server only (no Pi/PC failover).
  if (!(family && family.isFamilyAccount(window))) {
    try { await detectFailover(); } catch (e) { router = null; }
  }
  if (router) { await router.select(); renderBackends(); }
  // A sign-in link may carry the token after "#token=" (never sent to the server).
  const match = location.hash.match(/token=([^&]+)/);
  if (match) {
    history.replaceState(null, "", location.pathname);
    login(decodeURIComponent(match[1]));
  } else {
    refreshState();
  }
})();
