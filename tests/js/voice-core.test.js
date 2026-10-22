// Voice conversation core, driven by fake VAD and server events.
// Run: node --test tests/js/   (pytest runs it too when node is installed)
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const VC = require(path.join(__dirname, "..", "..", "kwil", "web", "voice-core.js"));

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

// Kyle's phone, 2026-10-20: barge-in fired "after 0 chunk(s)" on almost
// every turn: speech (trailing words, noise, our own cue) while it was still
// thinking cancelled the reply before any of it was heard.
test("talking while it thinks never cancels: held and sent after the reply", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd()); // turn 1
  c.onVad({ type: "speechStart" });
  c.onVad({ type: "speechConfirmed" });
  assert.equal(c.state, "thinking");
  assert.equal(log.stops, 0);
  assert.deepEqual(log.sent, []); // no interrupt
  const held = speechEnd();
  c.onVad(held); // "...and Friday"
  assert.equal(log.utterances.length, 1); // held, not sent over the reply
  assert.equal(c.state, "thinking");
  assert.equal(c.onServer({ type: "audio", turn: 1, index: 0 }), true); // reply plays
  assert.equal(c.state, "speaking");
  c.onServer({ type: "turn_done", turn: 1 });
  c.onPlaybackIdle();
  assert.equal(log.utterances.length, 2); // now it is the next turn
  assert.equal(log.utterances[1].turn, 2);
  assert.equal(log.utterances[1].audio, held.audio);
  assert.equal(c.state, "thinking");
  assert.deepEqual(log.sent, []);
});

test("barge-in only once the reply's audio started, by speech begun after that", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd()); // turn 1
  c.onVad({ type: "speechStart" }); // trailing words, before any audio
  assert.equal(c.onServer({ type: "audio", turn: 1, index: 0 }), true);
  c.onVad({ type: "speechConfirmed" });
  assert.equal(log.stops, 0); // began before the audio: not talking over it
  c.onVad(speechEnd());
  assert.equal(log.utterances.length, 1); // held
  assert.equal(c.state, "speaking"); // the reply played on meanwhile
  // now he really talks over it
  c.onVad({ type: "speechStart" });
  c.onVad({ type: "speechConfirmed" });
  assert.equal(log.stops, 1);
  assert.deepEqual(log.sent, [{ type: "interrupt", turn: 1, index: 1, fraction: 0.5 }]);
  c.onVad(speechEnd());
  assert.equal(log.utterances.length, 2);
  // the held words were superseded by the barge-in: never sent later
  c.onServer({ type: "turn_done", turn: 1 });
  c.onServer({ type: "audio", turn: 2, index: 0 });
  c.onServer({ type: "turn_done", turn: 2 });
  c.onPlaybackIdle();
  assert.equal(log.utterances.length, 2);
  assert.equal(c.state, "listening");
});

test("speech before the audio that runs past the end of the reply is the next turn", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd());
  c.onVad({ type: "speechStart" });
  c.onServer({ type: "audio", turn: 1, index: 0 });
  assert.equal(c.state, "thinking"); // he's talking; the audio plays
  c.onServer({ type: "turn_done", turn: 1 });
  c.onPlaybackIdle(); // finished while he was still talking
  c.onVad(speechEnd());
  assert.equal(log.stops, 0);
  assert.deepEqual(log.sent, []);
  assert.equal(log.utterances.length, 2); // not stuck in "thinking"
  assert.equal(c.state, "thinking");
  assert.equal(c.activeTurn, 2);
});

test("speech after a yes, before the rest of the reply, does not cancel it", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd()); // "delete the dentist"
  c.onServer({ type: "confirm", turn: 1, question: "Delete Dentist?" });
  c.onServer({ type: "audio", turn: 1, index: 0, question: true });
  c.onPlaybackIdle();
  c.onVad(speechEnd()); // "yes"
  c.onServer({ type: "confirm_answer", turn: 1, utterance: 2, text: "yes", yes: true });
  assert.equal(c.state, "thinking");
  c.onVad({ type: "speechStart" });
  c.onVad({ type: "speechConfirmed" });
  assert.deepEqual(log.sent, []);
  assert.equal(c.onServer({ type: "audio", turn: 1, index: 1 }), true);
});

test("barge-in off: speech while it thinks doesn't cancel either", () => {
  const { c, log } = timedHarness({ bargeIn: false });
  c.start();
  c.onVad(speechEnd());
  c.onVad({ type: "speechStart" });
  c.onVad({ type: "speechConfirmed" });
  c.onVad(speechEnd());
  assert.equal(log.stops, 0);
  assert.deepEqual(log.sent, []);
  assert.equal(log.utterances.length, 1);
  assert.equal(c.onServer({ type: "audio", turn: 1, index: 0 }), true);
});

test("pause and end drop a held utterance", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd());
  c.onVad(speechEnd()); // held
  c.pause(); // leaving the app still stops the turn
  assert.equal(log.sent[0].type, "interrupt");
  c.resume();
  c.onServer({ type: "turn_done", turn: 1 });
  assert.equal(log.utterances.length, 1);
});

test("endpointer: the app's own sound can't start an utterance", () => {
  const ep = new VC.Endpointer({ silenceMs: 300, minUtteranceMs: 100, confirmMs: 64, prePadMs: 64 });
  const events = [];
  const feed = (n, prob) => { for (let i = 0; i < n; i++) events.push(...ep.process(frame(0.1), prob)); };
  ep.holdOff(200); // a cue: 7 frames of 32 ms
  feed(7, 0.95); // the mic hears it as loud as speech
  assert.deepEqual(events, []);
  feed(1, 0.95); // after it: speech starts again
  assert.equal(events[0].type, "speechStart");
  // speech already under way is not cut by a cue
  feed(2, 0.95);
  ep.holdOff(200);
  feed(4, 0.95);
  feed(12, 0.0);
  assert.deepEqual(events.map((e) => e.type), ["speechStart", "speechConfirmed", "speechEnd"]);
});

test("voice.js holds the endpointer off while an earcon or the cue plays", () => {
  const fs = require("node:fs");
  const src = fs.readFileSync(path.join(__dirname, "..", "..", "kwil", "web", "voice.js"), "utf8");
  const play = src.slice(src.indexOf("    play(name) {"), src.indexOf("    onState(prev, state)"));
  assert.match(play, /endpointer\.holdOff\(/);
  assert.match(src, /if \(open\) earcons\.play\("thinking"\)/); // the follow-up cue goes through it
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

// --- follow-up window and the barge-in option ---

function timedHarness(options) {
  const log = { states: [], sent: [], utterances: [], stops: 0, followUps: [] };
  const timers = new Map();
  let nextId = 1;
  const fx = {
    setState: (s) => log.states.push(s),
    send: (m) => log.sent.push(m),
    sendUtterance: (turn, audio) => log.utterances.push({ turn, audio }),
    stopPlayback: () => { log.stops++; return { index: 0, fraction: 0.3 }; },
    followUp: (open) => log.followUps.push(open),
    setTimer: (fn, ms) => { const id = nextId++; timers.set(id, { fn, ms }); return id; },
    clearTimer: (id) => timers.delete(id),
  };
  const c = new VC.TurnController(fx, options);
  const fire = () => { for (const [id, t] of [...timers]) { timers.delete(id); t.fn(); } };
  return { c, log, timers, fire };
}

function playReply(c, turn) {
  c.onVad(speechEnd());
  c.onServer({ type: "audio", turn, index: 0 });
  c.onServer({ type: "turn_done", turn });
  c.onPlaybackIdle();
}

test("follow-up: after a reply plays, the window opens; silence closes it quietly", () => {
  const { c, log, timers, fire } = timedHarness({ followUpMs: 5000 });
  c.start();
  playReply(c, 1);
  assert.equal(c.state, "listening");
  assert.equal(c.followUp, true);
  assert.deepEqual(log.followUps, [true]);
  assert.deepEqual([...timers.values()].map((t) => t.ms), [5000]);
  fire(); // 5 s of silence
  assert.equal(c.followUp, false);
  assert.deepEqual(log.followUps, [true, false]);
  assert.equal(c.state, "listening"); // still a conversation; no sound, no send
  assert.equal(log.sent.length, 0);
});

test("follow-up: speaking in the window is the next turn, no wake word", () => {
  const { c, log, timers } = timedHarness({ followUpMs: 5000 });
  c.start();
  playReply(c, 1);
  c.onVad({ type: "speechStart" });
  assert.deepEqual(log.followUps, [true, false]); // closed as he started
  assert.equal(timers.size, 0);
  c.onVad(speechEnd());
  assert.equal(log.utterances.length, 2);
  assert.equal(c.state, "thinking");
});

test("follow-up: not after a barge-in, a yes/no question, or a reply with no audio", () => {
  const { c, log } = timedHarness({ followUpMs: 5000 });
  c.start();
  c.onVad(speechEnd());
  c.onServer({ type: "audio", turn: 1, index: 0 });
  c.onVad({ type: "speechConfirmed" }); // barge-in
  assert.equal(c.state, "listening");
  assert.deepEqual(log.followUps, []);
  // a delete question: waiting for the answer is not a follow-up window
  c.onVad(speechEnd()); // turn 2
  c.onServer({ type: "confirm", turn: 2, question: "Delete it?" });
  c.onServer({ type: "audio", turn: 2, index: 0, question: true });
  c.onPlaybackIdle();
  assert.equal(c.state, "listening");
  assert.deepEqual(log.followUps, []);
  c.onVad(speechEnd()); // "yes"
  c.onServer({ type: "confirm_answer", turn: 2, text: "yes" });
  assert.equal(c.awaitingAnswer, false);
  c.onServer({ type: "audio", turn: 2, index: 1 }); // "Done."
  c.onServer({ type: "turn_done", turn: 2 });
  c.onPlaybackIdle();
  assert.deepEqual(log.followUps, [true]); // only after the final reply
  // a turn that sent no audio (nothing heard) opens no window
  const quiet = timedHarness({ followUpMs: 5000 });
  quiet.c.start();
  quiet.c.onVad(speechEnd());
  quiet.c.onServer({ type: "turn_done", turn: 1 });
  assert.equal(quiet.c.state, "listening");
  assert.deepEqual(quiet.log.followUps, []);
});

test("follow-up: off with followUpMs 0; pause and end close an open window", () => {
  const off = timedHarness({ followUpMs: 0 });
  off.c.start();
  playReply(off.c, 1);
  assert.deepEqual(off.log.followUps, []);
  const { c, log, timers } = timedHarness({ followUpMs: 5000 });
  c.start();
  playReply(c, 1);
  c.pause();
  assert.deepEqual(log.followUps, [true, false]);
  assert.equal(timers.size, 0);
  c.resume();
  playReply(c, 2);
  c.end();
  assert.deepEqual(log.followUps, [true, false, true, false]);
});

test("follow-up: a reply that ends before turn_done still opens the window", () => {
  const { c, log } = timedHarness({ followUpMs: 3000 });
  c.start();
  c.onVad(speechEnd());
  c.onServer({ type: "audio", turn: 1, index: 0 });
  c.onPlaybackIdle(); // played; the server isn't done yet
  assert.equal(c.state, "thinking");
  assert.deepEqual(log.followUps, []);
  c.onServer({ type: "turn_done", turn: 1 });
  assert.equal(c.state, "listening");
  assert.deepEqual(log.followUps, [true]);
});

test("barge-in on (default): talking over the reply stops it and cancels the turn", () => {
  const { c, log } = timedHarness({});
  c.start();
  c.onVad(speechEnd());
  c.onServer({ type: "audio", turn: 1, index: 0 });
  c.onVad({ type: "speechStart" });
  c.onVad({ type: "speechConfirmed" });
  assert.equal(log.stops, 1);
  assert.deepEqual(log.sent, [{ type: "interrupt", turn: 1, index: 0, fraction: 0.3 }]);
  // the rest of the cancelled turn's audio is never played
  assert.equal(c.onServer({ type: "audio", turn: 1, index: 1 }), false);
  c.onVad(speechEnd());
  assert.equal(log.utterances.at(-1).turn, 2);
});

test("barge-in off: the mic is ignored while a reply plays, heard again after", () => {
  const { c, log } = timedHarness({ bargeIn: false, followUpMs: 5000 });
  c.start();
  c.onVad(speechEnd());
  c.onServer({ type: "audio", turn: 1, index: 0 });
  assert.equal(c.state, "speaking");
  c.onVad({ type: "speechStart" });
  c.onVad({ type: "speechConfirmed" });
  c.onVad(speechEnd()); // its own echo, as a whole "utterance"
  assert.equal(log.stops, 0);
  assert.equal(log.sent.length, 0);
  assert.equal(log.utterances.length, 1);
  assert.equal(c.state, "speaking");
  c.onServer({ type: "turn_done", turn: 1 });
  c.onPlaybackIdle();
  assert.equal(c.state, "listening");
  assert.deepEqual(log.followUps, [true]);
  c.onVad(speechEnd()); // the follow-up is heard
  assert.equal(log.utterances.length, 2);
});

// --- round 2 (judge findings) ---

test("muting while he talked over a finished reply doesn't leave it 'thinking'", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd()); // turn 1
  c.onVad({ type: "speechStart" }); // still talking when the audio comes
  c.onServer({ type: "audio", turn: 1, index: 0 });
  c.onServer({ type: "turn_done", turn: 1 });
  c.onPlaybackIdle(); // played to the end while he talked
  assert.equal(c.state, "thinking");
  c.setMuted(true);
  c.setMuted(false);
  assert.equal(c.state, "listening");
  c.onVad({ type: "speechStart" });
  c.onVad(speechEnd()); // the next question
  assert.deepEqual(log.sent, []); // no interrupt: the reply was heard in full
  assert.equal(log.utterances.length, 2);
  assert.equal(c.activeTurn, 2);
});

test("speech in a gap between chunks is held, not a barge-in", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd());
  c.onServer({ type: "audio", turn: 1, index: 0 });
  c.onPlaybackIdle(); // chunk 0 played, the next one isn't there yet
  assert.equal(c.state, "thinking");
  assert.equal(c.replyAudible, false);
  c.onVad({ type: "speechStart" });
  c.onVad({ type: "speechConfirmed" });
  c.onVad(speechEnd());
  assert.deepEqual(log.sent, []);
  assert.equal(log.stops, 0);
  assert.equal(c.onServer({ type: "audio", turn: 1, index: 1 }), true);
  c.onServer({ type: "turn_done", turn: 1 });
  c.onPlaybackIdle();
  assert.equal(log.utterances.length, 2); // asked after the reply
});

test("words held from before a yes/no question are dropped, never sent later", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd()); // "delete the dentist"
  c.onVad(speechEnd()); // "...yes" while it thinks: held
  assert.notEqual(c.held, null);
  c.onServer({ type: "confirm", turn: 1, question: "Delete Dentist?" });
  assert.equal(c.held, null);
  c.onServer({ type: "audio", turn: 1, index: 0, question: true });
  c.onPlaybackIdle();
  c.onVad(speechEnd()); // the real answer
  c.onServer({ type: "confirm_answer", turn: 1, utterance: 2, text: "yes", yes: true });
  c.onServer({ type: "audio", turn: 1, index: 1 }); // "Done. Anything else?"
  c.onServer({ type: "turn_done", turn: 1 });
  c.onPlaybackIdle();
  assert.equal(log.utterances.length, 2); // no third turn
  assert.equal(c.state, "listening");
});

test("speech while a question is open but not heard yet is dropped", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd());
  c.onServer({ type: "confirm", turn: 1, question: "Delete Dentist?" });
  c.onVad(speechEnd()); // "yes" before the question was played
  assert.equal(c.held, null);
  c.onServer({ type: "audio", turn: 1, index: 0, question: true });
  c.onPlaybackIdle();
  c.onVad(speechEnd()); // the answer
  c.onServer({ type: "confirm_answer", turn: 1, utterance: 2, text: "no", yes: false });
  c.onServer({ type: "turn_done", turn: 1 });
  assert.equal(log.utterances.length, 2);
  assert.deepEqual(log.sent, []);
});

test("speech right after the answer doesn't cut the rest of the reply", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd());
  c.onServer({ type: "confirm", turn: 1, question: "Delete Dentist?" });
  c.onServer({ type: "audio", turn: 1, index: 0, question: true });
  c.onPlaybackIdle();
  c.onVad(speechEnd()); // "yes"
  c.onVad({ type: "speechStart" }); // "...go ahead"
  c.onVad({ type: "speechConfirmed" });
  c.onVad(speechEnd());
  assert.deepEqual(log.sent, []);
  assert.equal(c.questionCut, false);
  c.onServer({ type: "confirm_answer", turn: 1, utterance: 2, text: "yes", yes: true });
  assert.equal(c.onServer({ type: "audio", turn: 1, index: 1 }), true); // "Done."
});

test("stricter VAD whenever reply audio plays, even while the state says thinking", () => {
  const { c } = harness();
  c.start();
  c.onVad(speechEnd());
  assert.equal(c.replyAudible, false);
  c.onVad({ type: "speechStart" }); // talking as the audio starts
  c.onServer({ type: "audio", turn: 1, index: 0 });
  assert.equal(c.state, "thinking");
  assert.equal(c.replyAudible, true);
  c.onPlaybackIdle();
  assert.equal(c.replyAudible, false);
  const fs = require("node:fs");
  const src = fs.readFileSync(path.join(__dirname, "..", "..", "kwil", "web", "voice.js"), "utf8");
  assert.match(src, /endpointer\.process\(frame, prob, controller\.replyAudible\)/);
  assert.doesNotMatch(src, /endpointer\.strict/);
});

// --- round 3 (judge findings) ---

for (const end of ["speechEnd", "misfire"]) {
  test(`a question that played under his speech (${end}) still takes the answer after it`, () => {
    const { c, log } = harness();
    c.start();
    c.onVad(speechEnd()); // "delete the dentist"
    c.onServer({ type: "confirm", turn: 1, question: "Delete Dentist?" });
    c.onVad({ type: "speechStart" }); // he starts just before the question audio
    c.onServer({ type: "audio", turn: 1, index: 0, question: true });
    assert.equal(c.state, "thinking"); // it plays under him
    c.onPlaybackIdle(); // the question ended
    c.onVad(end === "speechEnd" ? speechEnd() : { type: "misfire" });
    assert.equal(log.utterances.length, 1); // the early words are no answer
    assert.equal(c.state, "listening"); // not stuck "Thinking…"
    c.onVad({ type: "speechStart" });
    c.onVad({ type: "speechConfirmed" });
    c.onVad(speechEnd()); // "yes", after hearing it
    assert.equal(log.utterances.length, 2); // sent, as the answer
    assert.equal(c.activeTurn, 1);
    assert.deepEqual(log.sent, []);
    assert.equal(c.state, "thinking"); // waiting for confirm_answer, not "listening"
    c.onServer({ type: "confirm_answer", turn: 1, utterance: 2, text: "yes", yes: true });
    assert.equal(c.onServer({ type: "audio", turn: 1, index: 1 }), true);
  });
}

test("a question left unanswered closes with its turn: the next words are a new turn", () => {
  const { c, log } = harness();
  c.start();
  c.onVad(speechEnd());
  c.onServer({ type: "confirm", turn: 1, question: "Delete Dentist?" });
  c.onServer({ type: "audio", turn: 1, index: 0, question: true });
  c.onPlaybackIdle();
  c.onServer({ type: "turn_done", turn: 1 }); // the server gave up waiting
  assert.equal(c.awaitingAnswer, false);
  assert.equal(c.state, "listening");
  c.onVad(speechEnd());
  assert.equal(c.activeTurn, 2);
  assert.equal(log.utterances.length, 2);
});

function clockHarness() {
  const h = harness();
  let t = 0;
  let tails = 0;
  h.c.fx.now = () => t;
  h.c.fx.echoTail = () => { tails++; };
  return { ...h, at: (ms) => { t = ms; }, tails: () => tails };
}

test("the reply's echo in a pause between sentences is not queued as a turn", () => {
  const { c, log, at, tails } = clockHarness();
  c.start();
  c.onVad(speechEnd());
  c.onServer({ type: "audio", turn: 1, index: 0 });
  c.onPlaybackIdle(); // pause between sentences
  assert.equal(tails(), 1); // the mic is held off for the echo's tail
  at(0);
  c.onVad({ type: "speechStart" }); // echo of the sentence's end
  at(100);
  c.onServer({ type: "audio", turn: 1, index: 1 }); // the next one, under it
  c.onServer({ type: "turn_done", turn: 1 });
  at(1000);
  c.onVad(speechEnd()); // 1 s "utterance", 900 ms of it over reply audio
  c.onPlaybackIdle();
  assert.equal(log.utterances.length, 1); // dropped, never asked
  assert.equal(c.state, "listening");
  assert.deepEqual(log.sent, []);
});

test("real speech in a pause between sentences is still asked after the reply", () => {
  const { c, log, at } = clockHarness();
  c.start();
  c.onVad(speechEnd());
  c.onServer({ type: "audio", turn: 1, index: 0 });
  c.onPlaybackIdle();
  at(0);
  c.onVad({ type: "speechStart" });
  at(900);
  c.onServer({ type: "audio", turn: 1, index: 1 }); // overlaps only the last 100 ms
  c.onServer({ type: "turn_done", turn: 1 });
  at(1000);
  c.onVad(speechEnd());
  c.onPlaybackIdle();
  assert.equal(log.utterances.length, 2);
  assert.deepEqual(log.sent, []);
});

test("voice.js holds the mic off for the echo tail when playback stops", () => {
  const fs = require("node:fs");
  const src = fs.readFileSync(path.join(__dirname, "..", "..", "kwil", "web", "voice.js"), "utf8");
  assert.match(src, /echoTail: \(\) => \{ if \(endpointer\) endpointer\.holdOff\(CUE_TAIL_MS\); \}/);
});

test("endpointer: a short yes to an open question isn't a misfire", () => {
  // the server's default min_utterance_ms (300); ~190 ms of speech
  const ep = new VC.Endpointer({ silenceMs: 320, minUtteranceMs: 300 });
  const say = (speechFrames) => {
    const types = [];
    const probs = [...Array(speechFrames).fill(0.9), ...Array(12).fill(0.05)];
    for (const p of probs) types.push(...ep.process(frame(0.1), p).map((e) => e.type));
    return types.at(-1);
  };
  assert.ok(6 * ep.frameMs < 300 && 6 * ep.frameMs >= 150);
  assert.equal(say(6), "misfire");  // no question open: a click, a cough
  ep.answerPending = true;          // "Should I delete it?" is open
  assert.equal(ep.answerMinUtteranceMs, 150);
  assert.equal(say(6), "speechEnd");
  assert.equal(say(3), "misfire");  // under 150 ms: still a misfire
  ep.answerPending = false;
  assert.equal(say(6), "misfire");
  // the window closed mid-utterance (the question is asked again): full minimum
  const types2 = [];
  [...Array(6).fill(0.9), ...Array(12).fill(0.05)].forEach((p, i) => {
    ep.answerPending = i < 3;
    types2.push(...ep.process(frame(0.1), p).map((e) => e.type));
  });
  assert.equal(types2.at(-1), "misfire");
  // a lower min_utterance_ms setting is never raised by it
  const low = new VC.Endpointer({ minUtteranceMs: 64, silenceMs: 320 });
  low.answerPending = true;
  const types = [];
  for (const p of [0.9, 0.9, 0.9, ...Array(12).fill(0.05)]) {
    types.push(...low.process(frame(0.1), p).map((e) => e.type));
  }
  assert.equal(types.at(-1), "speechEnd");
  // voice.js tells the endpointer whether a question is open, frame by frame
  const fs = require("node:fs");
  const src = fs.readFileSync(path.join(__dirname, "..", "..", "kwil", "web", "voice.js"), "utf8");
  assert.match(src, /endpointer\.answerPending = controller\.answerWindow;/);
});

test("a short blip while the question plays doesn't cut it off; a quick yes after it counts", () => {
  // wired as voice.js does: the endpointer's answer window is the controller's
  const { c, log } = harness();
  const ep = new VC.Endpointer({ silenceMs: 320, minUtteranceMs: 300 });
  const say = (speechFrames, quietFrames = 12) => {
    const probs = [...Array(speechFrames).fill(0.9), ...Array(quietFrames).fill(0.05)];
    for (const p of probs) {
      ep.answerPending = c.answerWindow;
      for (const ev of ep.process(frame(0.1), p, c.replyAudible)) c.onVad(ev);
    }
  };
  c.start();
  c.onVad({ type: "speechStart" });
  c.onVad(speechEnd());                                   // turn 1: "delete my exam"
  c.onServer({ type: "confirm", turn: 1, question: "Delete the exam?" });
  assert.equal(c.onServer({ type: "audio", turn: 1, question: true }), true);
  assert.equal(c.answerWindow, false);                    // the question is playing
  say(6);                                                 // ~190 ms blip over it
  assert.equal(log.sent.filter((m) => m.type === "interrupt").length, 0, JSON.stringify(log.sent));
  assert.equal(c.questionCut, false);
  assert.equal(log.utterances.length, 1);
  // speech that began over the question and ended after it: still the full minimum
  say(4, 0);
  c.onPlaybackIdle();
  assert.equal(c.answerWindow, true);
  say(2);
  assert.equal(log.utterances.length, 1);
  assert.equal(log.sent.filter((m) => m.type === "interrupt").length, 0);
  // the question has played: a quick ~190 ms "yes" is sent as the answer
  say(6);
  assert.equal(log.utterances.length, 2, JSON.stringify(log.sent));
  assert.equal(log.sent.filter((m) => m.type === "interrupt").length, 0);
});
