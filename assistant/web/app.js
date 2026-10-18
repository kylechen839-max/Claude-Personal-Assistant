// Hey Claude phone page: record with the phone's mic, send to the PC, show
// and play the reply. The server streams NDJSON events for each turn.
"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  login: $("login"), app: $("app"), loginForm: $("login-form"), token: $("token"),
  loginError: $("login-error"), log: $("log"), status: $("status"),
  confirm: $("confirm"), question: $("confirm-question"), yes: $("yes"), no: $("no"),
  textForm: $("text-form"), text: $("text"), mic: $("mic"), micHint: $("mic-hint"),
  phoneVoice: $("phone-voice"), mute: $("mute"), logout: $("logout"),
};

let busy = false;
let confirming = false;
let live = null;  // the reply bubble that streamed text is going into
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
}

function addMessage(kind, text) {
  const div = document.createElement("div");
  div.className = `msg ${kind}`;
  div.textContent = text;
  els.log.appendChild(div);
  els.log.scrollTop = els.log.scrollHeight;
}

function setStatus(text) { els.status.textContent = text || ""; }

function setBusy(value) {
  busy = value;
  els.text.disabled = value && !confirming;
  els.mic.disabled = value && !confirming;
}

function setConfirm(question) {
  confirming = Boolean(question);
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

function speak(text, url) {
  if (els.mute.checked) return;
  if (els.phoneVoice.checked || !url) { speakInBrowser(text); return; }
  player.src = url;
  player.play().catch(() => speakInBrowser(text));
}

// --- talking to the server ---
async function request(url, options) {
  const res = await fetch(url, { credentials: "same-origin", ...options });
  if (res.status === 401) { show("login"); throw new Error("Please sign in."); }
  return res;
}

async function readEvents(res) {
  if (!res.ok) {
    let message = `Error ${res.status}`;
    try { message = (await res.json()).error || message; } catch (e) { /* not JSON */ }
    throw new Error(message);
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
      if (line) handleEvent(JSON.parse(line));
    }
  }
}

function handleEvent(ev) {
  if (ev.type !== "delta" && ev.type !== "reply") live = null;
  switch (ev.type) {
    case "delta":
      if (!live) { addMessage("claude", ""); live = els.log.lastElementChild; }
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
      setConfirm(ev.question);
      setStatus("");
      speak(`${ev.question} Please say yes or no.`, ev.audio);
      break;
    case "reply":
      setConfirm(null);
      if (live) { live.textContent = ev.text; live = null; } else addMessage("claude", ev.text);
      setStatus("");
      speak(ev.text, ev.audio);
      if (ev.ended) addMessage("note", "Conversation ended.");
      break;
    case "error":
      addMessage("note", ev.message);
      break;
    case "done":
      setConfirm(null);
      setStatus("");
      break;
  }
}

async function send(body) {
  // body: {text} | FormData with "audio"
  const answering = confirming;
  const url = answering ? "/api/confirm" : "/api/turn";
  let options;
  if (body instanceof FormData) {
    options = { method: "POST", body };
  } else {
    const payload = answering ? { answer: body.text } : { text: body.text };
    options = { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload) };
    addMessage("me", body.text);
  }
  if (answering) els.confirm.hidden = true;
  setBusy(true);
  setStatus(body instanceof FormData ? "Listening back…" : "Thinking…");
  try {
    await readEvents(await request(url, options));
  } catch (e) {
    addMessage("note", e.message || String(e));
    setStatus("");
    if (answering) els.confirm.hidden = false;
  } finally {
    setBusy(false);
  }
}

async function refreshState() {
  try {
    const res = await request("/api/state");
    const state = await res.json();
    show("app");
    setConfirm(state.confirm);
    if (state.busy && !state.confirm) {
      setBusy(true);
      setStatus("Thinking…");
      try { await readEvents(await request("/api/events")); } finally { setBusy(false); }
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

els.logout.addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
  els.log.textContent = "";
  show("login");
});

// --- text ---
els.textForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = els.text.value.trim();
  if (!text || (busy && !confirming)) return;
  unlockAudio();
  els.text.value = "";
  send({ text });
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
    els.mic.classList.remove("recording");
    els.micHint.textContent = "Hold to talk, or tap to start and stop";
    releaseStream();
    if (blob.size < 1000) { setStatus("Too short. Try again."); return; }
    const form = new FormData();
    form.append("audio", blob, `speech.${extensionFor(type)}`);
    send(form);
  });
  recorder.start();
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
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && !els.app.hidden && !busy) refreshState();
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

(function start() {
  // A sign-in link may carry the token after "#token=" (never sent to the server).
  const match = location.hash.match(/token=([^&]+)/);
  if (match) {
    history.replaceState(null, "", location.pathname);
    login(decodeURIComponent(match[1]));
  } else {
    refreshState();
  }
})();
