// Voice conversation core: no DOM, no Web Audio, so node:test can drive it
// (tests/js/voice-core.test.js). voice.js wires it to the mic, the speaker
// and the WebSocket.
//
//   EnergyVAD     - adaptive-noise-floor fallback when Silero can't load
//   Endpointer    - turns per-frame speech probabilities into utterances:
//                   speechStart, speechConfirmed (barge-in), speechEnd, misfire
//   TurnController- the turn-taking state machine:
//                   listening -> thinking -> speaking -> (barge-in) -> listening
//                   with a follow-up window ("still listening") after a reply
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
      this.holdFrames = 0;
      this.reset();
    }
    // The app is playing one of its own sounds (an earcon, the follow-up
    // cue) for `ms`: what the mic hears then can't start an utterance.
    // Speech already under way carries on.
    holdOff(ms) {
      this.holdFrames = Math.max(this.holdFrames, Math.ceil(Math.max(0, ms) / this.frameMs));
    }
    setOptions(opts) {
      this.threshold = opts.threshold ?? this.threshold ?? 0.5;
      // While a reply plays, ask for more certainty so its echo doesn't count.
      this.strictThreshold = opts.strictThreshold ?? this.strictThreshold ??
        Math.min(0.9, this.threshold + 0.25);
      this.silenceMs = opts.silenceMs ?? this.silenceMs ?? 700;
      this.minUtteranceMs = opts.minUtteranceMs ?? this.minUtteranceMs ?? 300;
      // While a yes/no question waits for its answer (`answerPending`: asked
      // and no longer playing, TurnController.answerWindow), a quick "yes"
      // can be shorter than that: only shorter sounds than this are
      // misfires. Only for speech that began and ended in that window; a
      // blip while the question plays keeps the full minimum (else it
      // would cut the question off and decline the action).
      this.answerMinUtteranceMs = opts.answerMinUtteranceMs ?? this.answerMinUtteranceMs ?? 150;
      this.answerPending = this.answerPending ?? false;
      this.maxUtteranceMs = opts.maxUtteranceMs ?? this.maxUtteranceMs ?? 30000;
      this.confirmMs = opts.confirmMs ?? this.confirmMs ?? 250;
      this.prePadMs = opts.prePadMs ?? this.prePadMs ?? 320;
    }
    reset() {
      this.speaking = false;
      this.frames = [];
      this.speechFrames = 0;
      this.silentFrames = 0;
      this.silentMs = 0;
      this.confirmed = false;
      this.answerShort = false; // this utterance stayed in the answer window
    }
    // Feed one frame and its speech probability; returns events.
    process(frame, prob, strict = false) {
      const events = [];
      const positive = prob >= (strict ? this.strictThreshold : this.threshold);
      const negative = prob < this.threshold - 0.15;
      const held = this.holdFrames > 0;
      if (held) this.holdFrames--;
      if (!this.speaking) {
        this.prePad.push(frame);
        const keep = Math.ceil(this.prePadMs / this.frameMs);
        while (this.prePad.length > keep) this.prePad.shift();
        if (positive && !held) {
          this.speaking = true;
          this.answerShort = this.answerPending;
          this.frames = this.prePad.slice();
          this.prePad = [];
          this.speechFrames = 1;
          this.silentFrames = 0;
          this.silentMs = 0;
          events.push({ type: "speechStart" });
          this._maybeConfirm(events);
        }
        return events;
      }
      this.frames.push(frame);
      if (!this.answerPending) this.answerShort = false;
      if (positive) {
        this.speechFrames++;
        this.silentFrames = 0;
        this.silentMs = 0;
        this._maybeConfirm(events);
      } else {
        // Unsure frames (road noise, music: neither speech nor clearly
        // silence) count at half weight, so they can't hold a turn open
        // until the length limit.
        this.silentFrames++;
        this.silentMs += negative ? this.frameMs : this.frameMs / 2;
      }
      const lengthMs = this.frames.length * this.frameMs;
      if (this.silentMs >= this.silenceMs || lengthMs >= this.maxUtteranceMs) {
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
      const minMs = this.answerShort
        ? Math.min(this.minUtteranceMs, this.answerMinUtteranceMs) : this.minUtteranceMs;
      this.reset();
      if (speechMs < minMs) return { type: "misfire", speechMs };
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
  // mark(name, turn) for timings, ended(reason), followUp(open) when the
  // follow-up window opens/closes, setTimer(fn, ms) / clearTimer(id),
  // echoTail() when playback stops (the mic still hears the last of it),
  // now() -> ms (default Date.now).
  // `options`: bargeIn (false: talking over a playing reply doesn't stop
  // it; the mic is ignored while it plays), followUpMs (after a reply has
  // played, show/sound "still listening" this long; 0 = off).
  //
  // Barge-in (talking over a reply stops it and cancels the turn) only
  // counts once the reply's audio has started, and only for speech that
  // began after that. Speech while it is still thinking (Kyle's trailing
  // words, room noise) never cancels the turn: the utterance is held and
  // sent as the next turn once the reply is over (newest wins; a real
  // barge-in, a yes/no question, pause or end drops it). While a question
  // is open it isn't held at all: it is never an answer, then or later.
  // Speech that began in a pause between sentences counts the same way,
  // except that if the next sentence played under most of it, it was the
  // reply's echo and is dropped.
  class TurnController {
    constructor(effects = {}, options = {}) {
      this.fx = effects;
      this.bargeInOn = options.bargeIn !== false;
      this.followUpMs = Math.max(0, Number(options.followUpMs) || 0);
      this.followUp = false; // the follow-up window is open
      this.followUpTimer = null;
      this.replyPlayed = false; // audio of the active turn's reply was played
      this.audioStarted = false; // the active turn's audio has started playing
      this.speechOverAudio = false; // the current speech began after that
      this.held = null; // an utterance said while it was thinking (next turn)
      this.speechInGap = false; // the current speech began between sentences
      this.overlapMs = 0; // ...and this much of it had reply audio under it
      this.audibleSince = null;
      this.questionAudio = false; // the open question's audio was played
      this.answerSent = false; // an answer to it is on its way
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
      if (state !== "listening") this._closeFollowUp();
      this.state = state;
      if (this.fx.setState) this.fx.setState(state);
    }

    // After a reply has played: keep listening for a follow-up, and say so
    // (a soft cue, "Still listening"). Silence just closes it again.
    _replyFinished() {
      const played = this.replyPlayed;
      this.replyPlayed = false;
      this._set("listening");
      if (this.held) {
        const audio = this.held;
        this.held = null;
        // never an answer to a question that is still open
        if (!this.awaitingAnswer && !this.userSpeaking) { this._utterance(audio); return; }
      }
      if (!played || !this.followUpMs || this.awaitingAnswer || !this.live) return;
      this._closeFollowUp();
      this.followUp = true;
      this._do("followUp", true);
      const set = this.fx.setTimer || ((fn, ms) => setTimeout(fn, ms));
      this.followUpTimer = set(() => { this.followUpTimer = null; this._closeFollowUp(); },
        this.followUpMs);
    }
    _closeFollowUp() {
      if (this.followUpTimer !== null) {
        const clear = this.fx.clearTimer || ((id) => clearTimeout(id));
        clear(this.followUpTimer);
        this.followUpTimer = null;
      }
      if (!this.followUp) return;
      this.followUp = false;
      this._do("followUp", false);
    }
    _do(name, ...args) { if (this.fx[name]) return this.fx[name](...args); return undefined; }
    _now() { return this.fx.now ? this.fx.now() : Date.now(); }
    // reply audio started/stopped while he talks: how much of his speech it covered
    _audible(on) {
      if (on && this.userSpeaking && this.audibleSince === null) this.audibleSince = this._now();
      if (!on && this.audibleSince !== null) {
        this.overlapMs += this._now() - this.audibleSince;
        this.audibleSince = null;
      }
    }

    // Never sit in "thinking" with nothing coming: nothing plays, he isn't
    // talking, and the server is done or waiting for his yes/no.
    _settle() {
      if (this.state !== "thinking" || this.playing || this.userSpeaking) return;
      if (this.awaitingAnswer) {
        if (this.questionAudio && !this.answerSent) this._set("listening"); // heard: answer it
      } else if (this.serverDone) {
        this._replyFinished();
      }
    }

    start() { this._set("listening"); }
    get live() { return !["idle", "paused", "ended"].includes(this.state); }
    get busy() { return this.state === "thinking" || this.state === "speaking"; }
    get canBargeIn() {
      return this.bargeInOn && this.busy && this.audioStarted && this.speechOverAudio;
    }
    // Reply audio is coming out of the speaker (also while the state shows
    // "thinking" because he was talking when it started): the VAD should
    // ask for more certainty, so its echo doesn't count as speech.
    get replyAudible() { return this.state === "speaking" || this.playing; }
    // A yes/no question has been asked and is no longer playing: what is
    // said now is its answer (the endpointer then keeps a quick "yes").
    get answerWindow() { return this.awaitingAnswer && !this.replyAudible; }

    setMuted(muted) {
      this.muted = muted;
      if (muted) this._speechOver(); // he can't be talking now: catch the state up
    }

    // --- from the VAD ---
    onVad(ev) {
      if (!this.live || this.muted) return;
      // barge-in off: while a reply plays, what the mic hears is its echo
      if (!this.bargeInOn && this.state === "speaking") return;
      switch (ev.type) {
        case "speechStart":
          this._speechBegan();
          this._closeFollowUp(); // he's talking: the window did its job
          break;
        case "speechConfirmed":
          this._speechBegan();
          if (this.canBargeIn) this.bargeIn();
          break;
        case "misfire":
          this._speechOver();
          break;
        case "speechEnd":
          this._speechBegan();
          this._utterance(ev.audio);
          this._speechOver();
          break;
      }
    }

    // Speech began (or an event shows it did): does it talk over the reply?
    _speechBegan() {
      if (this.userSpeaking) return;
      this.userSpeaking = true;
      // talking over the reply only while its audio is playing, not in a
      // gap (or after it) while the state still says "thinking"
      this.speechOverAudio = this.busy && this.audioStarted && this.replyAudible;
      this.speechInGap = this.busy && this.audioStarted && !this.replyAudible;
      this.overlapMs = 0;
      this.audibleSince = null;
    }
    _speechOver() {
      this.userSpeaking = false;
      this.speechOverAudio = false;
      this.speechInGap = false;
      this.audibleSince = null;
      // audio that came while he was talking played on; catch the state up
      if (this.state === "thinking" && this.audioStarted && this.playing) this._set("speaking");
      this._settle();
    }

    bargeIn() {
      const pos = this._do("stopPlayback") || { index: 0, fraction: 0 };
      this._audible(false);
      this.playing = false;
      this.replyPlayed = false; // cut off: no "still listening" cue after it
      this.held = null; // newer speech than what was held
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
      if (this.busy) {
        if (!this.canBargeIn) {
          if (this.awaitingAnswer) {
            // A yes/no question is open but not heard yet (or was just
            // answered): this is neither the answer nor safe to replay later.
            this._do("mark", "dropped", this.activeTurn);
            return;
          }
          this._audible(false);
          const lengthMs = ((audio && audio.length) || 0) / SAMPLE_RATE * 1000;
          if (this.speechInGap && this.overlapMs > lengthMs / 2) {
            // began between sentences, the next one played under most of
            // it: the reply's own echo, not Kyle
            this._do("mark", "dropped_echo", this.activeTurn);
            return;
          }
          // still thinking (or barge-in is off): keep it for after the reply
          this.held = audio;
          this._do("mark", "held", this.activeTurn);
          return;
        }
        // the speech was too short to count as a barge-in yet; it does now
        this.bargeIn();
      }
      this.turn += 1;
      this._do("mark", "speech_end", this.turn);
      this._do("sendUtterance", this.turn, audio);
      if (this.awaitingAnswer) this.answerSent = true;
      else {
        this.activeTurn = this.turn;
        this.serverDone = false;
        this.replyPlayed = false;
        this.audioStarted = false;
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
          this.questionAudio = false;
          this.answerSent = false;
          // words held from before the question must never become an
          // answer, nor a "yes" to whatever comes after it
          this.held = null;
          this._do("caption", "assistant", ev.question, false);
          break;
        case "confirm_answer":
          this.awaitingAnswer = false;
          this.questionCut = false;
          this.answerSent = false;
          this.audioStarted = false; // what's said next is a new stretch of reply
          this._do("caption", "user", ev.text || "", false);
          if (this.live && !this.userSpeaking) this._set("thinking");
          break;
        case "audio":
          if (this.awaitingAnswer && this.questionCut) return false;
          if (!this.live || this.state === "paused") return false;
          if (t !== this.activeTurn) return false;
          this.playing = true;
          this.audioStarted = true;
          this._audible(true);
          if (ev.question) this.questionAudio = true;
          else this.replyPlayed = true;
          if (!this.userSpeaking) this._set("speaking");
          // the spoken text, for subtitles when no text delta showed it
          if (ev.text) this._do("caption", "audio", ev.text, true);
          return true; // play it
        case "error":
          this._do("caption", "status", ev.message, false);
          break;
        case "turn_done":
          if (t === this.activeTurn) {
            this.serverDone = true;
            // the server closes an unanswered question with its turn
            this.awaitingAnswer = false;
            this.answerSent = false;
            if (!this.playing && this.live) this._replyFinished();
          }
          break;
      }
      return undefined;
    }

    // --- from playback ---
    onPlaybackIdle() {
      this._audible(false);
      this.playing = false;
      if (this.live) this._do("echoTail");
      if (this.state !== "speaking") { this._settle(); return; } // ended under his speech
      if (this.awaitingAnswer) this._set("listening"); // the yes/no question
      else if (this.serverDone) this._replyFinished();
      else this._set("thinking");
    }

    pause() {
      // leaving the app stops the reply even before its audio (not a barge-in)
      if (this.busy) this.bargeIn();
      this.held = null;
      this.userSpeaking = false;
      this._set("paused");
    }
    resume() { if (this.state === "paused") this._set("listening"); }
    end() {
      if (this.state === "ended") return;
      this.held = null;
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
