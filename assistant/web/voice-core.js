// Voice conversation core: no DOM, no Web Audio, so node:test can drive it
// (tests/js/voice-core.test.js). voice.js wires it to the mic, the speaker
// and the WebSocket.
//
//   EnergyVAD     - adaptive-noise-floor fallback when Silero can't load
//   Endpointer    - turns per-frame speech probabilities into utterances:
//                   speechStart, speechConfirmed (barge-in), speechEnd, misfire
//   TurnController- the turn-taking state machine:
//                   listening -> thinking -> speaking -> (barge-in) -> listening
//   packFrame / unpackFrame / floatToPcm16 - the socket's binary framing
"use strict";

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiceCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const SAMPLE_RATE = 16000;
  const FRAME_SAMPLES = 512; // 32 ms at 16 kHz, Silero v5's frame

  function rms(frame) {
    let sum = 0;
    for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
    return Math.sqrt(sum / (frame.length || 1));
  }

  // A speech "probability" from loudness over an adaptive noise floor, so it
  // can feed the same Endpointer as Silero. The floor follows quiet frames
  // quickly down and slowly up, so steady background noise is ignored.
  class EnergyVAD {
    constructor({ minLevel = 0.01, ratio = 3 } = {}) {
      this.minLevel = minLevel;
      this.ratio = ratio;
      this.floor = null;
    }
    process(frame) {
      const level = rms(frame);
      if (this.floor === null) this.floor = level;
      const threshold = Math.max(this.minLevel, this.floor * this.ratio);
      let prob;
      if (level >= threshold) prob = 0.9;
      else if (level >= threshold * 0.6) prob = 0.4;
      else prob = 0.05;
      if (prob < 0.5) {
        // learn the floor only from non-speech
        this.floor = level < this.floor ? 0.7 * this.floor + 0.3 * level
          : 0.98 * this.floor + 0.02 * level;
      }
      return prob;
    }
  }

  class Endpointer {
    constructor(opts = {}) {
      this.frameMs = opts.frameMs || (FRAME_SAMPLES / SAMPLE_RATE) * 1000;
      this.setOptions(opts);
      this.prePad = [];
      this.reset();
    }
    setOptions(opts) {
      this.threshold = opts.threshold ?? this.threshold ?? 0.5;
      // While a reply plays, ask for more certainty so its echo doesn't count.
      this.strictThreshold = opts.strictThreshold ?? this.strictThreshold ??
        Math.min(0.9, this.threshold + 0.25);
      this.silenceMs = opts.silenceMs ?? this.silenceMs ?? 700;
      this.minUtteranceMs = opts.minUtteranceMs ?? this.minUtteranceMs ?? 300;
      this.maxUtteranceMs = opts.maxUtteranceMs ?? this.maxUtteranceMs ?? 30000;
      this.confirmMs = opts.confirmMs ?? this.confirmMs ?? 250;
      this.prePadMs = opts.prePadMs ?? this.prePadMs ?? 320;
    }
    reset() {
      this.speaking = false;
      this.frames = [];
      this.speechFrames = 0;
      this.silentFrames = 0;
      this.confirmed = false;
    }
    // Feed one frame and its speech probability; returns events.
    process(frame, prob, strict = false) {
      const events = [];
      const positive = prob >= (strict ? this.strictThreshold : this.threshold);
      const negative = prob < this.threshold - 0.15;
      if (!this.speaking) {
        this.prePad.push(frame);
        const keep = Math.ceil(this.prePadMs / this.frameMs);
        while (this.prePad.length > keep) this.prePad.shift();
        if (positive) {
          this.speaking = true;
          this.frames = this.prePad.slice();
          this.prePad = [];
          this.speechFrames = 1;
          this.silentFrames = 0;
          events.push({ type: "speechStart" });
          this._maybeConfirm(events);
        }
        return events;
      }
      this.frames.push(frame);
      if (positive) {
        this.speechFrames++;
        this.silentFrames = 0;
        this._maybeConfirm(events);
      } else if (negative) {
        this.silentFrames++;
      }
      const lengthMs = this.frames.length * this.frameMs;
      if (this.silentFrames * this.frameMs >= this.silenceMs || lengthMs >= this.maxUtteranceMs) {
        events.push(this._finish(lengthMs >= this.maxUtteranceMs));
      }
      return events;
    }
    _maybeConfirm(events) {
      if (!this.confirmed && this.speechFrames * this.frameMs >= this.confirmMs) {
        this.confirmed = true;
        events.push({ type: "speechConfirmed" });
      }
    }
    _finish(cutOff) {
      const speechMs = this.speechFrames * this.frameMs;
      // keep ~100 ms of the trailing silence, drop the rest
      const trailing = Math.max(0, this.silentFrames - Math.ceil(100 / this.frameMs));
      const frames = this.frames.slice(0, this.frames.length - trailing);
      this.reset();
      if (speechMs < this.minUtteranceMs) return { type: "misfire", speechMs };
      return { type: "speechEnd", audio: concat(frames), speechMs, cutOff };
    }
  }

  function concat(frames) {
    let n = 0;
    for (const f of frames) n += f.length;
    const out = new Float32Array(n);
    let pos = 0;
    for (const f of frames) { out.set(f, pos); pos += f.length; }
    return out;
  }

  // States: idle, listening, thinking, speaking, paused, ended.
  // `effects` (all optional): setState(state), send(msg), sendUtterance(turn,
  // audio), stopPlayback() -> {index, fraction}, caption(who, text, append),
  // mark(name, turn) for timings, ended(reason).
  class TurnController {
    constructor(effects = {}) {
      this.fx = effects;
      this.state = "idle";
      this.turn = 0; // last utterance id sent
      this.activeTurn = null; // turn whose reply is coming or playing
      this.cancelled = new Set();
      this.awaitingAnswer = false; // a spoken yes/no question is pending
      this.questionCut = false;
      this.playing = false;
      this.serverDone = true;
      this.muted = false;
      this.userSpeaking = false;
    }
    _set(state) {
      if (this.state === state) return;
      this.state = state;
      if (this.fx.setState) this.fx.setState(state);
    }
    _do(name, ...args) { if (this.fx[name]) return this.fx[name](...args); return undefined; }

    start() { this._set("listening"); }
    get live() { return !["idle", "paused", "ended"].includes(this.state); }

    setMuted(muted) { this.muted = muted; if (muted) this.userSpeaking = false; }

    // --- from the VAD ---
    onVad(ev) {
      if (!this.live || this.muted) return;
      switch (ev.type) {
        case "speechStart":
          this.userSpeaking = true;
          break;
        case "speechConfirmed":
          if (this.state === "speaking" || this.state === "thinking") this.bargeIn();
          break;
        case "misfire":
          this.userSpeaking = false;
          break;
        case "speechEnd":
          this.userSpeaking = false;
          this._utterance(ev.audio);
          break;
      }
    }

    bargeIn() {
      const pos = this._do("stopPlayback") || { index: 0, fraction: 0 };
      this.playing = false;
      if (this.activeTurn !== null) {
        this._do("send", { type: "interrupt", turn: this.activeTurn,
          index: pos.index || 0, fraction: pos.fraction || 0 });
        if (this.awaitingAnswer) this.questionCut = true;
        else this.cancelled.add(this.activeTurn);
      }
      this._do("mark", "barge_in", this.activeTurn);
      this._set("listening");
    }

    _utterance(audio) {
      if (this.state === "speaking" || this.state === "thinking") {
        // the speech was too short to count as a barge-in yet; it does now
        this.bargeIn();
      }
      this.turn += 1;
      this._do("mark", "speech_end", this.turn);
      this._do("sendUtterance", this.turn, audio);
      if (!this.awaitingAnswer) {
        this.activeTurn = this.turn;
        this.serverDone = false;
      }
      this._set("thinking");
    }

    // --- from the server (header of binary audio frames included) ---
    onServer(ev) {
      const t = ev.turn;
      if (ev.type === "ended") {
        this._set("ended");
        this._do("ended", ev.reason);
        return;
      }
      if (t !== undefined && this.cancelled.has(t)) {
        if (ev.type === "turn_done") this.cancelled.delete(t);
        return false;
      }
      switch (ev.type) {
        case "transcript":
          this._do("mark", "transcript", t);
          this._do("caption", "user", ev.text || "", false);
          break;
        case "text":
          this._do("mark", "first_text", t);
          this._do("caption", "assistant", ev.delta, true);
          break;
        case "tool":
          this._do("caption", "status", ev.name, false);
          break;
        case "confirm":
          this.awaitingAnswer = true;
          this.questionCut = false;
          this._do("caption", "assistant", ev.question, false);
          break;
        case "confirm_answer":
          this.awaitingAnswer = false;
          this.questionCut = false;
          this._do("caption", "user", ev.text || "", false);
          if (this.live && !this.userSpeaking) this._set("thinking");
          break;
        case "audio":
          if (this.awaitingAnswer && this.questionCut) return false;
          if (!this.live || this.state === "paused") return false;
          if (t !== this.activeTurn) return false;
          this.playing = true;
          if (!this.userSpeaking) this._set("speaking");
          return true; // play it
        case "error":
          this._do("caption", "status", ev.message, false);
          break;
        case "turn_done":
          if (t === this.activeTurn) {
            this.serverDone = true;
            if (!this.playing && this.live) this._set("listening");
          }
          break;
      }
      return undefined;
    }

    // --- from playback ---
    onPlaybackIdle() {
      this.playing = false;
      if (this.state !== "speaking") return;
      this._set(this.serverDone || this.awaitingAnswer ? "listening" : "thinking");
    }

    pause() {
      if (this.state === "speaking" || this.state === "thinking") this.bargeIn();
      this.userSpeaking = false;
      this._set("paused");
    }
    resume() { if (this.state === "paused") this._set("listening"); }
    end() {
      if (this.state === "ended") return;
      this._do("stopPlayback");
      this._do("send", { type: "end" });
      this._set("ended");
    }
  }

  // --- binary framing: 4-byte big-endian header length, JSON, payload ---
  function packFrame(header, payload) {
    const head = new TextEncoder().encode(JSON.stringify(header));
    const body = payload ? new Uint8Array(payload.buffer || payload, payload.byteOffset || 0,
      payload.byteLength) : new Uint8Array(0);
    const out = new Uint8Array(4 + head.length + body.length);
    new DataView(out.buffer).setUint32(0, head.length, false);
    out.set(head, 4);
    out.set(body, 4 + head.length);
    return out.buffer;
  }

  function unpackFrame(buffer) {
    const view = new DataView(buffer);
    const n = view.getUint32(0, false);
    const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, n)));
    return { header, payload: buffer.slice(4 + n) };
  }

  function floatToPcm16(samples) {
    const out = new DataView(new ArrayBuffer(samples.length * 2));
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      out.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Uint8Array(out.buffer);
  }

  return { SAMPLE_RATE, FRAME_SAMPLES, rms, EnergyVAD, Endpointer, TurnController,
    packFrame, unpackFrame, floatToPcm16, concat };
});
