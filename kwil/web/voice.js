// Hands-free voice conversation view. Tap "Voice" once: it listens, notices
// when you stop talking (Silero VAD in WebAssembly, or an energy VAD if that
// can't load), streams the reply back sentence by sentence and plays it, and
// stops talking as soon as you talk over it. The turn-taking logic lives in
// voice-core.js; this file is the mic, the speaker, the socket and the UI.
// The Camera button adds the back camera: each turn carries one frame of it
// (or the one "Send this frame" froze), shrunk and JPEG-encoded here, in the
// same socket message as the words.
"use strict";

(() => {
  const VC = window.VoiceCore;
  const $ = (id) => document.getElementById(id);
  const ui = {
    open: $("voice-open"), view: $("voice"), orb: $("voice-orb"), state: $("voice-state"),
    user: $("voice-user"), reply: $("voice-reply"), captions: $("voice-captions"),
    end: $("voice-end"), mute: $("voice-mute"), ccToggle: $("voice-cc"),
    resume: $("voice-resume"), debug: $("voice-debug"), debugToggle: $("voice-timings"),
    meter: $("voice-meter"),
    camera: $("voice-camera"), frame: $("voice-frame"), preview: $("voice-camera-preview"),
    cameraOn: $("voice-camera-on"),
  };
  if (!ui.open || !VC) return;

  const LABELS = { connecting: "Connecting…", listening: "Listening", thinking: "Thinking…",
    speaking: "Speaking", paused: "Paused", ended: "Ended" };
  const FOLLOW_UP_LABEL = "Still listening…"; // the follow-up window after a reply
  const CUE_TAIL_MS = 250; // after an earcon, the mic still hears it (latency, room)
  const reducedMotion = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)");
  // What the camera says on the page (i18n.js has each in Chinese)
  const CAMERA_TEXT = {
    denied: "Kwil can't use the camera. Allow it, then close and reopen Kwil.",
    failed: "The camera couldn't start.",
    stopped: "The camera stopped.",
    tooBig: "That frame was too large to send; the turn went without it.",
    pinned: "This frame goes with your next question.",
  };

  let ctx = null; // one AudioContext for capture and playback (unlocked by the tap)
  let mic = null; // MediaStream
  let micNode = null;
  let capture = null; // AudioWorkletNode
  let inAnalyser = null;
  let outAnalyser = null;
  let outGain = null;
  let ws = null;
  let controller = null;
  let endpointer = null;
  let vadName = "energy";
  let vad = null; // { process(frame) -> Promise<number> | number }
  let settings = {};
  let wakeLock = null;
  let running = false;
  let frameQueue = Promise.resolve();
  let backlog = 0;
  let quickVad = null; // energy VAD for frames the model can't keep up with
  let replyIsStatus = false; // the reply line shows a status, not reply text
  let audioCaption = false; // this reply's subtitles come from the audio chunks
  let micHeardAt = 0; // last time the mic gave anything but digital silence
  let timings = {}; // turn -> {name: ms}
  let debugOn = false;
  let generation = 0; // bumped by finish(), so a start() still loading gives up
  // Everything sent on the socket, in the order it happened: an utterance
  // waiting for its camera frame holds back the "interrupt" that follows it
  // (VoiceCore.OrderedSender). Made per socket by start().
  let sender = null;
  function sendJson(m) { if (sender) sender.push(JSON.stringify(m)); }

  // --- camera: a video-only stream (the mic is never asked for again) ---
  const camera = {
    stream: null,
    policy: new VC.CameraPolicy(),
    canvas: null,
    asking: false,
    // Inside the Camera tap (iOS wants getUserMedia there).
    async start() {
      if (this.stream || this.asking || !running) return;
      this.asking = true;
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } }, audio: false });
      } catch (e) {
        this.asking = false;
        const denied = e && (e.name === "NotAllowedError" || e.name === "SecurityError");
        caption("status", denied ? CAMERA_TEXT.denied : CAMERA_TEXT.failed);
        return;
      }
      this.asking = false;
      if (!running || document.visibilityState === "hidden") {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      this.stream = stream;
      for (const track of stream.getVideoTracks()) {
        track.onended = () => { if (this.stream === stream) { this.stop(); caption("status", CAMERA_TEXT.stopped); } };
      }
      ui.preview.srcObject = stream; // never a blob: URL (the CSP has none)
      ui.preview.hidden = false;
      ui.preview.play().catch(() => { /* autoplay+muted plays anyway */ });
      this.policy.setOn(true);
      this.show(true);
    },
    stop() {
      this.policy.setOn(false);
      if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
      this.stream = null;
      if (ui.preview) { ui.preview.pause(); ui.preview.srcObject = null; ui.preview.hidden = true; }
      this.show(false);
    },
    show(on) {
      if (!ui.camera) return;
      ui.camera.setAttribute("aria-pressed", String(on));
      ui.frame.hidden = !on;
      ui.cameraOn.hidden = !on;
      ui.view.classList.toggle("camera-on", on);
      this.showPinned(false);
    },
    showPinned(pinned) {
      ui.frame.setAttribute("aria-pressed", String(pinned));
      ui.view.classList.toggle("frame-pinned", pinned);
      if (!pinned && ui.preview && this.stream) ui.preview.play().catch(() => {});
    },
    // The live picture now, as JPEG bytes under the server's cap.
    async grab() {
      const v = ui.preview;
      if (!this.stream || !v || !v.videoWidth) return null;
      const size = VC.frameSize(v.videoWidth, v.videoHeight, settings.camera_max_side ?? 1024);
      if (!size.width) return null;
      const c = this.canvas || (this.canvas = document.createElement("canvas"));
      c.width = size.width;
      c.height = size.height;
      c.getContext("2d").drawImage(v, 0, 0, size.width, size.height);
      return VC.encodeFrame(c, { maxBytes: settings.camera_max_bytes ?? 400 * 1024 });
    },
    // "Send this frame": freeze the picture; it goes with the next turn.
    async pin() {
      if (!this.stream) return;
      if (this.policy.pinned) { this.policy.unpin(); this.showPinned(false); return; }
      let jpeg = null;
      try { jpeg = await this.grab(); } catch (e) { caption("status", CAMERA_TEXT.tooBig); return; }
      if (!jpeg || !this.stream) return;
      this.policy.pin(jpeg);
      ui.preview.pause(); // the frozen frame stays on screen
      this.showPinned(true);
      caption("status", CAMERA_TEXT.pinned);
    },
    // The frame for this utterance: null at once (camera off, an answer),
    // else a promise of the JPEG bytes (null if it couldn't be made).
    forTurn(awaitingAnswer) {
      const pick = this.policy.take(awaitingAnswer);
      if (!pick) return null;
      if (pick.kind === "pinned") { this.showPinned(false); return Promise.resolve(pick.frame); }
      return this.grab().catch(() => { caption("status", CAMERA_TEXT.tooBig); return null; });
    },
  };

  // --- playback: decoded chunks scheduled back to back ---
  const playback = {
    sources: [], // {node, start, duration, index, turn}
    nextTime: 0,
    chain: Promise.resolve(),
    enqueue(header, payload) {
      this.chain = this.chain.then(async () => {
        let buffer;
        try { buffer = await ctx.decodeAudioData(payload); } catch (e) { return; }
        if (controller.onServer(header) !== true) return; // stale or cancelled
        const node = ctx.createBufferSource();
        node.buffer = buffer;
        node.connect(outGain);
        const start = Math.max(ctx.currentTime + 0.03, this.nextTime);
        node.start(start);
        this.nextTime = start + buffer.duration;
        const entry = { node, start, duration: buffer.duration, index: header.index, turn: header.turn };
        this.sources.push(entry);
        if (!timings[header.turn]?.first_audio_play) {
          mark("first_audio_play", header.turn, (start - ctx.currentTime) * 1000);
        }
        node.onended = () => {
          this.sources = this.sources.filter((s) => s !== entry);
          // A yes/no question only counts as asked once it was heard to the end.
          if (header.question) sendJson({ type: "played", turn: header.turn, index: header.index });
          if (!this.sources.length) controller.onPlaybackIdle();
        };
      });
    },
    // Stop now; returns how much was heard: whole chunks + fraction of the current one.
    stop() {
      const now = ctx ? ctx.currentTime : 0;
      let index = 0;
      let fraction = 0;
      let first = true;
      for (const s of this.sources) {
        if (first) { index = s.index; first = false; }
        if (now >= s.start + s.duration) index = s.index + 1;
        else if (now > s.start) { index = s.index; fraction = (now - s.start) / s.duration; }
        s.node.onended = null;
        try { s.node.stop(); } catch (e) { /* not started */ }
      }
      this.sources = [];
      this.nextTime = 0;
      return { index, fraction: Math.min(1, Math.max(0, fraction)) };
    },
  };

  // --- earcons: soft original cues made by kwil/earcons.py, served locally ---
  const earcons = {
    buffers: {},
    load() {
      for (const name of ["listening", "thinking", "ended"]) {
        if (this.buffers[name]) continue;
        fetch(`/earcons/${name}.wav`, { credentials: "same-origin" })
          .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(r.status))))
          .then((data) => ctx.decodeAudioData(data))
          .then((buffer) => { this.buffers[name] = buffer; })
          .catch(() => { /* no cue then; never blocks the conversation */ });
      }
    },
    play(name) {
      const buffer = this.buffers[name];
      if (!ctx || !buffer) return;
      const node = ctx.createBufferSource();
      node.buffer = buffer;
      node.connect(ctx.destination); // not outGain: the orb shows only the voice
      node.start();
      // its echo must not count as speech (a turn, or later a barge-in);
      // the tail covers output latency and the room
      if (endpointer) endpointer.holdOff(buffer.duration * 1000 + CUE_TAIL_MS);
    },
    // listening when the mic first opens (or after a pause), thinking once
    // your turn is sent; the ended cue is played by finish().
    onState(prev, state) {
      if (state === "listening" && (prev === "connecting" || prev === "paused")) this.play("listening");
      else if (state === "thinking" && prev === "listening") this.play("thinking");
    },
  };

  // --- timings (debug panel) ---
  function mark(name, turn, offsetMs = 0) {
    if (turn === null || turn === undefined) return;
    const t = (timings[turn] = timings[turn] || {});
    if (t[name] === undefined) t[name] = performance.now() + offsetMs;
    if (name === "first_audio_play") showTimings(turn);
  }

  function showTimings(turn, server) {
    const t = timings[turn];
    if (!t) return;
    if (server) t.server = server;
    const base = t.speech_end;
    const rows = [["transcript", "transcript"], ["first_text", "first words"],
      ["first_audio_play", "first audio"]];
    const parts = base === undefined ? [] : rows.filter(([k]) => t[k] !== undefined)
      .map(([k, label]) => `${label} ${((t[k] - base) / 1000).toFixed(2)} s`);
    const silence = (settings.silence_ms || 0) / 1000;
    let line = `Turn ${turn} (${vadName} VAD), after end of turn: ${parts.join(" · ")}`;
    if (t.first_audio_play !== undefined && base !== undefined) {
      line += ` — ${((t.first_audio_play - base) / 1000 + silence).toFixed(2)} s after you stopped talking`;
    }
    if (t.server) {
      line += `\nserver: ${Object.entries(t.server).map(([k, v]) => `${k} ${v.toFixed(2)}`).join(", ")}`;
    }
    if (ui.debug) ui.debug.textContent = line;
    if (t.server && t.first_audio_play !== undefined && !t.reported && ws && ws.readyState === 1) {
      t.reported = true;
      const out = { type: "client_timings", turn, vad: vadName };
      for (const [k] of rows) if (t[k] !== undefined) out[k] = Math.round(t[k] - base);
      sendJson(out);
    }
  }

  // --- UI ---
  function setState(state) {
    const prev = ui.view.dataset.state;
    ui.view.dataset.state = state;
    if (prev !== state) earcons.onState(prev, state);
    ui.state.textContent = LABELS[state] || state;
    if (state === "thinking" || state === "listening") ui.resume.hidden = true;
  }

  // After a reply: a soft cue (the same low note as the PC's follow-up
  // blip) and "Still listening…" with the mic meter lit, for
  // settings.follow_up_ms; silence just puts the plain label back.
  function followUp(open) {
    ui.view.classList.toggle("follow-up", open);
    const state = ui.view.dataset.state;
    if (state === "listening") ui.state.textContent = open ? FOLLOW_UP_LABEL : LABELS.listening;
    if (open) earcons.play("thinking");
  }

  function caption(who, text, append) {
    if (who === "user") {
      ui.user.textContent = text;
      ui.reply.textContent = "";
      replyIsStatus = false;
      audioCaption = false;
    } else if (who === "assistant") {
      // reply text replaces a status line ("list events…") instead of
      // being appended to it
      ui.reply.textContent = append && !replyIsStatus ? ui.reply.textContent + text : text;
      replyIsStatus = false;
      audioCaption = false;
    } else if (who === "audio") {
      // The sentence being spoken. Shown only when no reply text did (a
      // missed or late text event), so subtitles never stay empty.
      if (replyIsStatus || !ui.reply.textContent.trim()) {
        ui.reply.textContent = text;
        replyIsStatus = false;
        audioCaption = true;
      } else if (audioCaption) {
        ui.reply.textContent += ` ${text}`;
      }
    } else if (who === "status") {
      ui.reply.textContent = text.includes("_") ? `${text.replace(/_/g, " ")}…` : text;
      replyIsStatus = true;
    }
  }

  function micLevel() {
    if (!inAnalyser) return 0;
    const data = new Uint8Array(inAnalyser.fftSize);
    inAnalyser.getByteTimeDomainData(data);
    let sum = 0;
    let peak = 0;
    for (let i = 0; i < data.length; i++) {
      const v = (data[i] - 128) / 128;
      sum += v * v;
      peak = Math.max(peak, Math.abs(v));
    }
    if (peak > 0) micHeardAt = performance.now();
    return Math.min(1, Math.sqrt(sum / data.length) * 4);
  }

  function animate() {
    if (!running) return;
    const state = ui.view.dataset.state;
    // The mic meter: how much sound is coming in, and whether it counts as
    // speech (VAD), in every state.
    const muted = ui.mute.getAttribute("aria-pressed") === "true";
    const heard = muted ? 0 : micLevel();
    if (ui.meter) {
      ui.meter.style.setProperty("--mic", heard.toFixed(3));
      ui.meter.classList.toggle("hearing", !!(endpointer && endpointer.speaking));
    }
    // A mic that gives exact digital silence isn't reaching us (seen with
    // some car and Bluetooth audio routes): say so instead of waiting.
    if (!muted && state === "listening" && micHeardAt && performance.now() - micHeardAt > 5000
        && !replyIsStatus && !ui.reply.textContent) {
      caption("status", "No sound is coming from this microphone. If you're on CarPlay or " +
        "Bluetooth, try push-to-talk.");
    }
    let level = 0;
    const analyser = state === "speaking" ? outAnalyser : state === "listening" ? inAnalyser : null;
    if (analyser && !(reducedMotion && reducedMotion.matches)) {
      const data = new Uint8Array(analyser.fftSize);
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) { const v = (data[i] - 128) / 128; sum += v * v; }
      level = Math.min(1, Math.sqrt(sum / data.length) * 4);
    }
    ui.orb.style.setProperty("--level", level.toFixed(3));
    requestAnimationFrame(animate);
  }

  // --- VAD ---
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error(`could not load ${src}`));
      document.head.appendChild(s);
    });
  }

  // Silero v5 on onnxruntime-web (same scheme as @ricky0123/vad-web's Silero:
  // 512-sample frames with a 64-sample context, one recurrent state tensor).
  async function loadSilero() {
    if (!window.ort) await loadScript("/static/vad/ort.wasm.min.js");
    const ort = window.ort;
    ort.env.wasm.wasmPaths = "/static/vad/";
    ort.env.wasm.numThreads = 1;
    ort.env.logLevel = "error";
    const model = await (await fetch("/static/vad/silero_vad_v5.onnx", { credentials: "same-origin" })).arrayBuffer();
    const session = await ort.InferenceSession.create(model, { executionProviders: ["wasm"] });
    const sr = new ort.Tensor("int64", BigInt64Array.from([16000n]));
    let state = new ort.Tensor("float32", new Float32Array(256), [2, 1, 128]);
    let context = new Float32Array(64);
    return {
      async process(frame) {
        const input = new Float32Array(64 + frame.length);
        input.set(context, 0);
        input.set(frame, 64);
        context = frame.slice(-64);
        const out = await session.run({ input: new ort.Tensor("float32", input, [1, input.length]), state, sr });
        state = out.stateN;
        return out.output.data[0];
      },
    };
  }

  function onFrame(frame) {
    if (!controller || !controller.live) return;
    if (settings.barge_in === false && controller.state === "speaking") {
      // barge-in off: the mic only hears the reply now; start afresh after it
      endpointer.reset();
      endpointer.prePad = [];
      return;
    }
    // The device can't keep up with the model: judge this frame by loudness
    // instead of dropping it, so pauses are still timed in real time.
    const quick = backlog > 8;
    backlog++;
    frameQueue = frameQueue.then(async () => {
      let prob = 0;
      try { prob = quick ? quickVad.process(frame) : await vad.process(frame); } catch (e) { prob = 0; }
      backlog--;
      if (!controller) return; // ended meanwhile
      // a short "yes" to an open question isn't dropped as a misfire
      // (only once the question has finished playing: a blip over it
      // mustn't cut it off)
      endpointer.answerPending = controller.answerWindow;
      // stricter while reply audio plays, whatever the state shows
      for (const ev of endpointer.process(frame, prob, controller.replyAudible)) controller.onVad(ev);
    });
  }

  // --- socket ---
  function connect() {
    return new Promise((resolve, reject) => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      // Pi -> PC -> cloud failover (FAILOVER.md): the active backend, with
      // the access token as the first message (never in the URL)
      const fo = window.kwilFailover;
      const foUrl = fo && fo.wsUrl("/api/voice?auth=first");
      ws = new WebSocket(foUrl || `${proto}://${location.host}/api/voice`);
      if (foUrl) {
        const sock = ws;
        sock.onopen = async () => {
          sock.send(JSON.stringify({ type: "auth", token: await fo.token() }));
        };
      }
      ws.binaryType = "arraybuffer";
      let ready = false;
      ws.onmessage = (msg) => {
        if (typeof msg.data !== "string") {
          const { header, payload } = VC.unpackFrame(msg.data);
          mark("first_audio_recv", header.turn);
          playback.enqueue(header, payload);
          return;
        }
        const ev = JSON.parse(msg.data);
        if (ev.type === "auth_ok") return;
        if (ev.type === "ready") { ready = true; settings = ev.settings || {}; resolve(); return; }
        if (ev.type === "pong") return;
        if (ev.type === "turn_done" && ev.timings) {
          const server = {};
          for (const [k, v] of Object.entries(ev.timings)) server[k] = v;
          showTimings(ev.turn, server);
        }
        if (ev.type === "audio") return;
        controller.onServer(ev);
      };
      ws.onerror = () => { if (!ready) reject(new Error("could not connect (are you signed in?)")); };
      ws.onclose = () => {
        if (!ready) reject(new Error("the server refused the voice connection"));
        else if (running) finish("Connection closed.");
      };
    });
  }

  // The words and (camera on) one frame, as one message, in order with
  // everything else sent. Without a frame it goes out at once. With one,
  // it waits for the frame, and is dropped if the turn was cancelled or
  // the conversation paused or ended meanwhile (the page's "interrupt" for
  // it is queued behind it, so the server never runs a dropped turn).
  function sendUtterance(turn, audio) {
    if (!sender || !ws || ws.readyState !== 1) return;
    const pcm = VC.floatToPcm16(audio);
    const ctl = controller;
    const answering = !!(ctl && ctl.awaitingAnswer); // never with a frame
    const frame = camera.forTurn(answering);
    const build = (jpeg, waited) => {
      if (waited && (controller !== ctl || !ctl || !ctl.stillWanted(turn))) return null;
      mark("sent", turn);
      return VC.packUtterance({ type: "utterance", turn, sample_rate: VC.SAMPLE_RATE,
        vad: vadName }, pcm, jpeg);
    };
    sender.push(frame ? () => frame.then((j) => build(j, true), () => build(null, true))
      : () => build(null, false));
  }

  // --- wake lock / interruptions ---
  async function keepAwake() {
    try {
      if ("wakeLock" in navigator) wakeLock = await navigator.wakeLock.request("screen");
    } catch (e) { wakeLock = null; } // low battery, not allowed: fine
  }

  function pause(why) {
    if (!running || !controller || controller.state === "paused") return;
    controller.pause();
    ui.resume.hidden = false;
    caption("status", why || "Paused. Tap to continue.");
  }

  async function resume() {
    if (!running) return;
    try { await ctx.resume(); } catch (e) { /* stays suspended */ }
    if (mic && mic.getAudioTracks().every((t) => t.readyState === "ended")) {
      finish("The microphone stopped.");
      return;
    }
    keepAwake();
    ui.resume.hidden = true;
    caption("assistant", "", false);
    controller.resume();
  }

  // In the background the camera is turned off (and stays off until tapped
  // again): a hidden page never keeps it live.
  document.addEventListener("visibilitychange", () => {
    if (!running) return;
    if (document.visibilityState === "hidden") {
      camera.stop();
      pause("Paused while the app was in the background.");
    } else keepAwake();
  });

  // The chat pane shows a stored conversation (app.js, history.js): voice
  // continues it too. After getUserMedia (iOS wants the mic asked inside
  // the tap); the server's conversation is checked first, so a call in the
  // conversation already open costs nothing.
  async function continueBound() {
    const app = window.KwilApp;
    const cid = app && app.pane().cid;
    if (!cid) return;
    const state = await (await app.request("/api/state")).json();
    if (state.conversation === cid) return;
    const res = await app.request(`/api/conversations/${encodeURIComponent(cid)}/continue`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    if (res.ok) return;
    if (res.status === 404) { app.forgetConversation(cid); return; }  // deleted: a new one
    let message = `Error ${res.status}`;
    try { message = (await res.json()).error || message; } catch (e) { /* not JSON */ }
    const err = new Error(res.status === 409 ? "Still working on the last request." : message);
    err.plain = res.status === 409;  // shown as it is (i18n.js translates it)
    throw err;
  }

  // --- start / stop ---
  // voice mode covers the whole window (KwilUI.voice in ui.js; Escape ends it)
  function overlay(step) {
    const v = window.KwilUI && window.KwilUI.voice;
    if (v && typeof v[step] === "function") v[step]();
  }
  async function start() {
    if (running) return;
    running = true;
    if (window.kwilFailover) window.kwilFailover.beginTurn(); // no backend switch mid-session
    const gen = ++generation;
    // End tapped while we were waiting: drop whatever this start() opened.
    const cancelled = () => {
      if (gen === generation) return false;
      if (mic && !running) { mic.getTracks().forEach((t) => t.stop()); mic = null; }
      if (ws && !running) { try { ws.close(1000); } catch (e) { /* closed */ } ws = null; }
      return true;
    };
    timings = {};
    ui.view.hidden = false;
    document.body.classList.add("voice-open");
    overlay("opened");  // ui.js: the page behind is inert, focus on the orb
    setState("connecting");
    ui.user.textContent = "";
    ui.reply.textContent = "";
    // iOS: audio and mic must start inside this tap.
    const Ctx = window.AudioContext || window.webkitAudioContext;
    // the session type first, so the context opens on the play-and-record route
    if (navigator.audioSession) { try { navigator.audioSession.type = "play-and-record"; } catch (e) { /* old */ } }
    if (!ctx) ctx = new Ctx();
    ctx.resume();
    const unlock = ctx.createBufferSource();
    unlock.buffer = ctx.createBuffer(1, 1, 22050);
    unlock.connect(ctx.destination);
    unlock.start();
    earcons.load();
    let micPromise;
    try {
      micPromise = navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true,
        noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
    } catch (e) {
      micPromise = Promise.reject(e);
    }
    keepAwake();
    try {
      const stream = await micPromise;
      if (gen !== generation) { stream.getTracks().forEach((t) => t.stop()); return; }
      mic = stream;
      micHeardAt = performance.now();
      // A car or Bluetooth mic can run at another sample rate than the
      // context opened at, and Safari then hands the context silence: reopen
      // it at the mic's rate (allowed without a tap while the mic is live).
      const rate = (stream.getAudioTracks()[0]?.getSettings?.() || {}).sampleRate;
      if (rate && ctx.sampleRate !== rate) {
        try {
          const fresh = new Ctx({ sampleRate: rate });
          // resume() can stay pending when the browser wants a tap: give up
          // after a second and keep the context we have
          await Promise.race([fresh.resume(), new Promise((r) => setTimeout(r, 1000))]);
          if (fresh.state === "running") {
            ctx.close().catch(() => {});
            ctx = fresh;
            earcons.load();
          } else {
            fresh.close().catch(() => {});
          }
        } catch (e) {
          console.warn("Couldn't match the mic's sample rate:", e);
        }
      }
      await continueBound();
      if (cancelled()) return;
      await connect();
      if (cancelled()) return;
      const sock = ws;
      sender = new VC.OrderedSender((data) => { if (ws === sock && sock.readyState === 1) sock.send(data); });
      if (settings.debug_panel && !debugOn && ui.debugToggle) ui.debugToggle.click();
      // a cap of 0 means no camera (the server says camera: false then too)
      if (ui.camera) {
        ui.camera.hidden = settings.camera !== true || !navigator.mediaDevices
          || !(settings.camera_max_side > 0) || !(settings.camera_max_bytes > 0);
      }
      controller = new VC.TurnController({
        setState, caption, sendUtterance,
        send: sendJson,
        stopPlayback: () => playback.stop(),
        mark, followUp,
        // the end of a sentence still echoes: it mustn't start an utterance
        echoTail: () => { if (endpointer) endpointer.holdOff(CUE_TAIL_MS); },
        now: () => performance.now(),
        ended: (reason) => finish(reason === "idle" ? "Ended after a quiet stretch." : ""),
      }, { bargeIn: settings.barge_in !== false, followUpMs: settings.follow_up_ms || 0 });
      endpointer = new VC.Endpointer({ threshold: settings.vad_threshold, silenceMs: settings.silence_ms,
        minUtteranceMs: settings.min_utterance_ms, maxUtteranceMs: settings.max_utterance_ms,
        confirmMs: settings.barge_in_ms });
      outGain = ctx.createGain();
      outAnalyser = ctx.createAnalyser();
      outAnalyser.fftSize = 512;
      outGain.connect(outAnalyser);
      outGain.connect(ctx.destination);
      micNode = ctx.createMediaStreamSource(mic);
      inAnalyser = ctx.createAnalyser();
      inAnalyser.fftSize = 512;
      micNode.connect(inAnalyser);
      await ctx.audioWorklet.addModule("/voice-worklet.js");
      if (cancelled()) return;
      capture = new AudioWorkletNode(ctx, "voice-capture");
      capture.port.onmessage = (e) => onFrame(new Float32Array(e.data));
      micNode.connect(capture);
      ui.state.textContent = "Loading…";
      vad = new VC.EnergyVAD();
      quickVad = new VC.EnergyVAD();
      vadName = "energy";
      if (settings.vad !== "energy") {
        try { vad = await loadSilero(); vadName = "silero"; } catch (e) {
          console.warn("Silero VAD unavailable, using the energy VAD:", e);
        }
      }
      if (cancelled()) return;
      for (const track of mic.getAudioTracks()) {
        track.onended = () => finish("The microphone stopped.");
        track.onmute = () => pause("Paused: the microphone was interrupted.");
        track.onunmute = () => { if (controller && controller.state === "paused" && document.visibilityState === "visible") resume(); };
      }
      ctx.onstatechange = () => {
        // "interrupted" is iOS (phone call, Siri); "suspended" elsewhere
        if (running && ctx.state !== "running") pause("Paused: audio was interrupted. Tap to continue.");
      };
      controller.start();
      if (ui.mute.getAttribute("aria-pressed") === "true") toggleMute(true);
      requestAnimationFrame(animate);
    } catch (e) {
      if (!cancelled()) finish(e.plain ? e.message : `Voice mode couldn't start: ${e.message || e.name}`);
    }
  }

  function finish(message) {
    if (!running) return;
    running = false;
    if (window.kwilFailover) window.kwilFailover.endTurn();
    generation++;
    if (controller && controller.state !== "ended") controller.end();
    playback.stop();
    earcons.play("ended");
    if (ws) { try { ws.close(1000); } catch (e) { /* closed */ } ws = null; }
    sender = null;
    if (capture) { capture.port.onmessage = null; capture.disconnect(); capture = null; }
    if (micNode) { micNode.disconnect(); micNode = null; }
    if (mic) { mic.getTracks().forEach((t) => t.stop()); mic = null; }
    camera.stop();
    if (ui.camera) ui.camera.hidden = true;
    if (ctx) ctx.onstatechange = null;
    if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
    controller = null;
    ui.view.classList.remove("follow-up");
    document.body.classList.remove("voice-open");
    ui.view.hidden = true;
    ui.resume.hidden = true;
    overlay("closed");  // ui.js: the page back, focus on the Voice button
    if (message) {
      const status = $("status");
      if (status) status.textContent = message;
    }
  }

  function toggleMute(force) {
    const muted = force !== undefined ? force : ui.mute.getAttribute("aria-pressed") !== "true";
    ui.mute.setAttribute("aria-pressed", String(muted));
    ui.mute.textContent = muted ? "Unmute" : "Mute";
    if (mic) mic.getAudioTracks().forEach((t) => { t.enabled = !muted; });
    if (controller) controller.setMuted(muted);
    ui.view.classList.toggle("muted", muted);
  }

  function setCaptions(on) {
    ui.ccToggle.setAttribute("aria-pressed", String(on));
    ui.captions.hidden = !on;
    try { localStorage.setItem("voiceCaptions", on ? "1" : "0"); } catch (e) { /* private mode */ }
  }

  ui.open.addEventListener("click", start);
  ui.end.addEventListener("click", () => finish(""));
  ui.mute.addEventListener("click", () => toggleMute());
  ui.ccToggle.addEventListener("click", () => setCaptions(ui.ccToggle.getAttribute("aria-pressed") !== "true"));
  ui.resume.addEventListener("click", resume);
  // No hash navigation here while the camera is on: in an iPhone home-screen
  // app a navigation can drop the camera grant and ask again.
  if (ui.camera) {
    ui.camera.addEventListener("click", () => {
      if (camera.stream) camera.stop();
      else camera.start();
    });
    ui.frame.addEventListener("click", () => camera.pin());
  }
  ui.orb.addEventListener("click", () => { if (controller && controller.state === "paused") resume(); });
  if (ui.debugToggle) {
    ui.debugToggle.addEventListener("click", () => {
      debugOn = !debugOn;
      ui.debug.hidden = !debugOn;
      ui.debugToggle.setAttribute("aria-pressed", String(debugOn));
    });
  }
  let cc = true;
  try { cc = localStorage.getItem("voiceCaptions") !== "0"; } catch (e) { /* default on */ }
  setCaptions(cc);
})();
