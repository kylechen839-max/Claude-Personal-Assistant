// Hands-free voice conversation view. Tap "Voice" once: it listens, notices
// when you stop talking (Silero VAD in WebAssembly, or an energy VAD if that
// can't load), streams the reply back sentence by sentence and plays it, and
// stops talking as soon as you talk over it. The turn-taking logic lives in
// voice-core.js; this file is the mic, the speaker, the socket and the UI.
"use strict";

(() => {
  const VC = window.VoiceCore;
  const $ = (id) => document.getElementById(id);
  const ui = {
    open: $("voice-open"), view: $("voice"), orb: $("voice-orb"), state: $("voice-state"),
    user: $("voice-user"), reply: $("voice-reply"), captions: $("voice-captions"),
    end: $("voice-end"), mute: $("voice-mute"), ccToggle: $("voice-cc"),
    resume: $("voice-resume"), debug: $("voice-debug"), debugToggle: $("voice-timings"),
  };
  if (!ui.open || !VC) return;

  const LABELS = { connecting: "Connecting…", listening: "Listening", thinking: "Thinking…",
    speaking: "Speaking", paused: "Paused", ended: "Ended" };
  const reducedMotion = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)");

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
  let timings = {}; // turn -> {name: ms}
  let debugOn = false;

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
          if (header.question && ws && ws.readyState === 1) {
            ws.send(JSON.stringify({ type: "played", turn: header.turn, index: header.index }));
          }
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
      ws.send(JSON.stringify(out));
    }
  }

  // --- UI ---
  function setState(state) {
    ui.view.dataset.state = state;
    ui.state.textContent = LABELS[state] || state;
    if (endpointer) endpointer.strict = state === "speaking";
    if (state === "thinking" || state === "listening") ui.resume.hidden = true;
  }

  function caption(who, text, append) {
    if (who === "user") {
      ui.user.textContent = text;
      ui.reply.textContent = "";
    } else if (who === "assistant") {
      ui.reply.textContent = append ? ui.reply.textContent + text : text;
    } else if (who === "status") {
      ui.reply.textContent = text.includes("_") ? `${text.replace(/_/g, " ")}…` : text;
    }
  }

  function animate() {
    if (!running) return;
    const state = ui.view.dataset.state;
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
    if (backlog > 8) return; // the device can't keep up: drop rather than lag
    backlog++;
    frameQueue = frameQueue.then(async () => {
      let prob = 0;
      try { prob = await vad.process(frame); } catch (e) { prob = 0; }
      backlog--;
      for (const ev of endpointer.process(frame, prob, endpointer.strict)) controller.onVad(ev);
    });
  }

  // --- socket ---
  function connect() {
    return new Promise((resolve, reject) => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/api/voice`);
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

  function sendUtterance(turn, audio) {
    if (!ws || ws.readyState !== 1) return;
    const pcm = VC.floatToPcm16(audio);
    ws.send(VC.packFrame({ type: "utterance", turn, sample_rate: VC.SAMPLE_RATE, vad: vadName }, pcm));
    mark("sent", turn);
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
    ui.reply.textContent = why || "Paused. Tap to continue.";
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

  document.addEventListener("visibilitychange", () => {
    if (!running) return;
    if (document.visibilityState === "hidden") pause("Paused while the app was in the background.");
    else keepAwake();
  });

  // --- start / stop ---
  async function start() {
    if (running) return;
    running = true;
    timings = {};
    ui.view.hidden = false;
    document.body.classList.add("voice-open");
    setState("connecting");
    ui.user.textContent = "";
    ui.reply.textContent = "";
    // iOS: audio and mic must start inside this tap.
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!ctx) ctx = new Ctx();
    if (navigator.audioSession) { try { navigator.audioSession.type = "play-and-record"; } catch (e) { /* old */ } }
    ctx.resume();
    const unlock = ctx.createBufferSource();
    unlock.buffer = ctx.createBuffer(1, 1, 22050);
    unlock.connect(ctx.destination);
    unlock.start();
    let micPromise;
    try {
      micPromise = navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true,
        noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
    } catch (e) {
      micPromise = Promise.reject(e);
    }
    keepAwake();
    try {
      mic = await micPromise;
      await connect();
      if (settings.debug_panel && !debugOn && ui.debugToggle) ui.debugToggle.click();
      controller = new VC.TurnController({
        setState, caption, sendUtterance,
        send: (m) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(m)); },
        stopPlayback: () => playback.stop(),
        mark,
        ended: (reason) => finish(reason === "idle" ? "Ended after a quiet stretch." : ""),
      });
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
      capture = new AudioWorkletNode(ctx, "voice-capture");
      capture.port.onmessage = (e) => onFrame(new Float32Array(e.data));
      micNode.connect(capture);
      ui.state.textContent = "Loading…";
      vad = new VC.EnergyVAD();
      vadName = "energy";
      if (settings.vad !== "energy") {
        try { vad = await loadSilero(); vadName = "silero"; } catch (e) {
          console.warn("Silero VAD unavailable, using the energy VAD:", e);
        }
      }
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
      finish(`Voice mode couldn't start: ${e.message || e.name}`);
    }
  }

  function finish(message) {
    if (!running) return;
    running = false;
    if (controller && controller.state !== "ended") controller.end();
    playback.stop();
    if (ws) { try { ws.close(1000); } catch (e) { /* closed */ } ws = null; }
    if (capture) { capture.port.onmessage = null; capture.disconnect(); capture = null; }
    if (micNode) { micNode.disconnect(); micNode = null; }
    if (mic) { mic.getTracks().forEach((t) => t.stop()); mic = null; }
    if (ctx) ctx.onstatechange = null;
    if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
    controller = null;
    document.body.classList.remove("voice-open");
    ui.view.hidden = true;
    ui.resume.hidden = true;
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
