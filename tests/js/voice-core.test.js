// Voice conversation core, driven by fake VAD and server events.
// Run: node --test tests/js/   (pytest runs it too when node is installed)
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const VC = require(path.join(__dirname, "..", "..", "assistant", "web", "voice-core.js"));

function frame(level) {
  return new Float32Array(VC.FRAME_SAMPLES).fill(level);
}

function harness() {
  const log = { states: [], sent: [], utterances: [], stops: 0, captions: [], marks: [] };
  const fx = {
    setState: (s) => log.states.push(s),
    send: (m) => log.sent.push(m),
    sendUtterance: (turn, audio) => log.utterances.push({ turn, audio }),
    stopPlayback: () => { log.stops++; return { index: 1, fraction: 0.5 }; },
    caption: (who, text, append) => log.captions.push({ who, text, append }),
    mark: (name, turn) => log.marks.push([name, turn]),
  };
  const c = new VC.TurnController(fx);
  return { c, log };
}

const speechEnd = () => ({ type: "speechEnd", audio: new Float32Array(16000) });

test("full turn: listening -> thinking -> speaking -> listening", () => {
  const { c, log } = harness();
  c.start();
  assert.equal(c.state, "listening");
  c.onVad({ type: "speechStart" });
  c.onVad({ type: "speechConfirmed" });
  assert.equal(c.state, "listening");  // talking normally is not a barge-in
  c.onVad(speechEnd());
  assert.equal(c.state, "thinking");
  assert.equal(log.utterances.length, 1);
  assert.equal(log.utterances[0].turn, 1);
  c.onServer({ type: "transcript", turn: 1, text: "what's on tomorrow" });
  c.onServer({ type: "text", turn: 1, delta: "You have" });
  assert.equal(c.onServer({ type: "audio", turn: 1, index: 0 }), true);
  assert.equal(c.state, "speaking");
  c.onPlaybackIdle();  // played chunk 0, more coming
  assert.equal(c.state, "thinking");
  c.onServer({ type: "audio", turn: 1, index: 1 });
  c.onServer({ type: "turn_done", turn: 1 });
  assert.equal(c.state, "speaking");  // still playing
  c.onPlaybackIdle();
  assert.equal(c.state, "listening");
  assert.deepEqual(log.states, ["listening", "thinking", "speaking", "thinking",
    "speaking", "listening"]);
  assert.deepEqual(log.captions[0], { who: "user", text: "what's on tomorrow", append: false });
});

test("barge-in stops playback, reports position, ignores the rest of the turn", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd());
  c.onServer({ type: "audio", turn: 1, index: 0 });
  assert.equal(c.state, "speaking");
  c.onVad({ type: "speechStart" });
  assert.equal(c.state, "speaking");  // not yet: needs ~250 ms of speech
  c.onVad({ type: "speechConfirmed" });
  assert.equal(c.state, "listening");
  assert.equal(log.stops, 1);
  assert.deepEqual(log.sent, [{ type: "interrupt", turn: 1, index: 1, fraction: 0.5 }]);
  // late chunks of the interrupted reply are dropped
  assert.equal(c.onServer({ type: "audio", turn: 1, index: 1 }), false);
  c.onServer({ type: "turn_done", turn: 1 });
  assert.equal(c.state, "listening");
  // the interrupting speech becomes the next turn
  c.onVad(speechEnd());
  assert.equal(log.utterances[1].turn, 2);
  assert.equal(c.state, "thinking");
  assert.equal(c.onServer({ type: "audio", turn: 2, index: 0 }), true);
  assert.equal(c.state, "speaking");
});

test("talking while it thinks cancels that turn", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd());
  c.onVad({ type: "speechStart" });
  c.onVad({ type: "speechConfirmed" });
  assert.deepEqual(log.sent[0], { type: "interrupt", turn: 1, index: 1, fraction: 0.5 });
  assert.equal(c.state, "listening");
  assert.equal(c.onServer({ type: "audio", turn: 1, index: 0 }), false);
});

test("short speech end during playback still counts as barge-in", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd());
  c.onServer({ type: "audio", turn: 1, index: 0 });
  c.onVad(speechEnd());
  assert.equal(log.sent[0].type, "interrupt");
  assert.equal(log.utterances.length, 2);
  assert.equal(c.state, "thinking");
});

test("a yes/no answer keeps the original turn and its later audio", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd());  // "delete my dentist appointment"
  c.onServer({ type: "confirm", turn: 1, question: "Delete Dentist?" });
  c.onServer({ type: "audio", turn: 1, index: 0, reply: false });
  c.onPlaybackIdle();
  assert.equal(c.state, "listening");  // waiting for the answer
  c.onVad(speechEnd());  // "yes"
  assert.equal(log.utterances[1].turn, 2);
  c.onServer({ type: "confirm_answer", turn: 1, utterance: 2, text: "yes", yes: true });
  assert.equal(c.state, "thinking");
  assert.equal(c.onServer({ type: "audio", turn: 1, index: 1 }), true);
  assert.equal(c.state, "speaking");
  c.onServer({ type: "turn_done", turn: 1 });
  c.onPlaybackIdle();
  assert.equal(c.state, "listening");
});

test("cutting the question short does not cancel the turn", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd());
  c.onServer({ type: "confirm", turn: 1, question: "Delete Dentist?" });
  c.onServer({ type: "audio", turn: 1, index: 0, reply: false });
  c.onVad({ type: "speechConfirmed" });  // "yes" over the question
  assert.equal(log.sent[0].type, "interrupt");
  assert.equal(c.onServer({ type: "audio", turn: 1, index: 0 }), false);  // rest of question
  c.onVad(speechEnd());
  c.onServer({ type: "confirm_answer", turn: 1, text: "yes", yes: true });
  assert.equal(c.onServer({ type: "audio", turn: 1, index: 1 }), true);
});

test("mute ignores the VAD; pause stops and resume listens; end sends end", () => {
  const { c, log } = harness();
  c.start();
  c.setMuted(true);
  c.onVad(speechEnd());
  assert.equal(log.utterances.length, 0);
  c.setMuted(false);
  c.onVad(speechEnd());
  c.onServer({ type: "audio", turn: 1, index: 0 });
  c.pause();
  assert.equal(c.state, "paused");
  assert.equal(log.sent[0].type, "interrupt");
  c.onVad(speechEnd());
  assert.equal(log.utterances.length, 1);  // paused: nothing sent
  c.resume();
  assert.equal(c.state, "listening");
  c.end();
  assert.equal(c.state, "ended");
  assert.deepEqual(log.sent.at(-1), { type: "end" });
});

test("server 'ended' (idle) ends the controller", () => {
  let reason = null;
  const c = new VC.TurnController({ ended: (r) => { reason = r; } });
  c.start();
  c.onServer({ type: "ended", reason: "idle" });
  assert.equal(c.state, "ended");
  assert.equal(reason, "idle");
});

test("endpointer: silence ends a turn, short blips misfire, max length cuts", () => {
  const ep = new VC.Endpointer({ silenceMs: 700, minUtteranceMs: 300, confirmMs: 250,
    maxUtteranceMs: 3000, prePadMs: 96 });
  const events = [];
  const feed = (n, prob) => { for (let i = 0; i < n; i++) events.push(...ep.process(frame(0.1), prob)); };
  feed(10, 0.0);
  feed(1, 0.9);
  assert.deepEqual(events.map((e) => e.type), ["speechStart"]);
  feed(9, 0.9);  // 320 ms of speech
  assert.deepEqual(events.map((e) => e.type), ["speechStart", "speechConfirmed"]);
  feed(21, 0.1);  // 672 ms of silence: not yet
  assert.equal(events.length, 2);
  feed(1, 0.1);  // 704 ms
  const end = events.at(-1);
  assert.equal(end.type, "speechEnd");
  // 2 frames of pre-pad + 10 of speech + ~100 ms of the trailing silence
  assert.equal(end.audio.length, (2 + 10 + 4) * VC.FRAME_SAMPLES);
  assert.equal(end.cutOff, false);

  events.length = 0;
  feed(3, 0.9);  // 96 ms: too short
  feed(25, 0.0);
  assert.deepEqual(events.map((e) => e.type), ["speechStart", "misfire"]);

  events.length = 0;
  feed(94, 0.9);  // 3 s of talk without a pause: cut at the max
  const cut = events.find((e) => e.type === "speechEnd");
  assert.equal(cut.cutOff, true);
  assert.ok(cut.audio.length <= 3008 * 16);
});

test("endpointer: strict mode (reply playing) needs a higher probability", () => {
  const ep = new VC.Endpointer({ threshold: 0.5 });
  assert.deepEqual(ep.process(frame(0), 0.6, true), []);
  assert.equal(ep.process(frame(0), 0.8, true)[0].type, "speechStart");
});

test("energy VAD adapts to steady noise and still hears speech", () => {
  const vad = new VC.EnergyVAD({ minLevel: 0.005 });
  let p;
  for (let i = 0; i < 50; i++) p = vad.process(frame(0.02));  // fan noise
  assert.ok(p < 0.5, `noise counted as speech (${p})`);
  assert.ok(vad.process(frame(0.2)) > 0.5);
  // with the energy VAD feeding the endpointer, a spoken phrase becomes an utterance
  const ep = new VC.Endpointer({ silenceMs: 320, minUtteranceMs: 200 });
  const types = [];
  const levels = [...Array(10).fill(0.02), ...Array(15).fill(0.25), ...Array(15).fill(0.02)];
  for (const l of levels) types.push(...ep.process(frame(l), vad.process(frame(l))).map((e) => e.type));
  assert.deepEqual(types, ["speechStart", "speechConfirmed", "speechEnd"]);
});

test("binary framing round-trips and PCM16 clips", () => {
  const pcm = VC.floatToPcm16(new Float32Array([0, 1, -1, 2, -0.5]));
  const view = new DataView(pcm.buffer);
  assert.deepEqual([0, 1, 2, 3, 4].map((i) => view.getInt16(i * 2, true)),
    [0, 32767, -32768, 32767, -16384]);
  const buf = VC.packFrame({ type: "utterance", turn: 3, sample_rate: 16000 }, pcm);
  const { header, payload } = VC.unpackFrame(buf);
  assert.deepEqual(header, { type: "utterance", turn: 3, sample_rate: 16000 });
  assert.deepEqual(new Uint8Array(payload), pcm);
});

test("endpointer: unsure frames (noise) still end the turn, at half speed", () => {
  const ep = new VC.Endpointer({ threshold: 0.5, silenceMs: 640, minUtteranceMs: 100, maxUtteranceMs: 30000 });
  const f = frame(0.1);
  ep.process(f, 0.9);
  for (let i = 0; i < 10; i++) ep.process(f, 0.9);  // ~350 ms of speech
  let events = [];
  let n = 0;
  while (!events.length && n < 200) { events = ep.process(f, 0.45); n++; }  // between the thresholds
  assert.equal(events[0].type, "speechEnd");
  assert.equal(n, 40);  // 640 ms at half weight = 40 frames of 32 ms, not the 30 s cap
  // clear silence still ends after silenceMs
  ep.process(f, 0.9);
  for (let i = 0; i < 10; i++) ep.process(f, 0.9);
  n = 0; events = [];
  while (!events.length) { events = ep.process(f, 0.1); n++; }
  assert.equal(n, 20);
});

test("audio chunk text reaches the subtitles", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd());
  c.onServer({ type: "audio", turn: 1, index: 0, text: "Pretty light today." });
  assert.deepEqual(log.captions.at(-1), { who: "audio", text: "Pretty light today.", append: true });
});
