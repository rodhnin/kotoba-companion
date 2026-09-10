// Unit tests for lib/voice-gate.ts — run with: node --test lib/__tests__/voice-gate.test.mjs
// Plain .mjs so tsc ignores it; node's built-in type stripping loads the .ts module directly.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  GATE_TAIL_MS,
  GATED_FRAME_BUFFER,
  MicGate,
  MicUplink,
  SustainedSpeechDetector,
  VAD_FLOOR_INITIAL,
  VAD_MIN_RMS,
  VAD_SUSTAIN_WINDOWS,
  VAD_WINDOW_MS,
} from "../voice-gate.ts";

// ---- MicGate ------------------------------------------------------------------------------------

test("gate starts open and closes on audio_start", () => {
  const g = new MicGate();
  assert.equal(g.isOpen(0), true);
  g.onAudioStart();
  assert.equal(g.isOpen(0), false);
});

test("audio_end with no local playback opens after the tail", () => {
  const g = new MicGate();
  g.onAudioStart();
  g.onAudioEnd(1000);
  assert.equal(g.isOpen(1000), false);
  assert.equal(g.isOpen(1000 + GATE_TAIL_MS - 1), false);
  assert.equal(g.isOpen(1000 + GATE_TAIL_MS), true);
});

test("stays closed while buffered playback drains past audio_end, then tails from drain", () => {
  const g = new MicGate();
  g.onAudioStart();
  g.onPlayback(true, 0);
  g.onAudioEnd(500); // server finished sending — audio is still coming out of the speakers
  assert.equal(g.isOpen(500 + GATE_TAIL_MS), false);
  g.onPlayback(false, 2000); // playback queue truly drained
  assert.equal(g.isOpen(2000 + GATE_TAIL_MS - 1), false);
  assert.equal(g.isOpen(2000 + GATE_TAIL_MS), true);
});

test("forceOpen (interrupt/barge-in) opens immediately, tail included", () => {
  const g = new MicGate();
  g.onAudioStart();
  g.onPlayback(true, 0);
  g.onPlayback(false, 100); // stopPlayback during barge-in schedules a tail...
  g.forceOpen(); // ...which forceOpen must override
  assert.equal(g.isOpen(100), true);
});

test("next segment's audio_start closes the gate again during the tail", () => {
  const g = new MicGate();
  g.onAudioStart();
  g.onAudioEnd(1000);
  g.onAudioStart(); // tool-gap: a second TTS segment begins
  assert.equal(g.isOpen(1000 + GATE_TAIL_MS + 1), false);
});

// ---- SustainedSpeechDetector ----------------------------------------------------------------------

const RATE = 16000;
const WINDOW = Math.round((RATE * VAD_WINDOW_MS) / 1000);
const SPEECH = 800; // realistic quiet post-AGC speech — BELOW the old fixed bar of 1500
const QUIET = 60;

// Alternating ±amplitude → window RMS === amplitude exactly.
function frame(windows, amplitude) {
  const out = new Int16Array(WINDOW * windows);
  for (let i = 0; i < out.length; i++) out[i] = i % 2 ? amplitude : -amplitude;
  return out;
}

test("fires on sustained quiet speech spanning chunk boundaries (~200 ms)", () => {
  const d = new SustainedSpeechDetector(RATE);
  assert.equal(d.feed(frame(VAD_SUSTAIN_WINDOWS - 1, SPEECH)), false);
  assert.equal(d.feed(frame(1, SPEECH)), true); // window count carries across feeds
});

test("a transient burst (cough/keyboard, ~100 ms) does not fire", () => {
  const d = new SustainedSpeechDetector(RATE);
  assert.equal(d.feed(frame(2, 4000)), false); // loud but short
  assert.equal(d.feed(frame(VAD_SUSTAIN_WINDOWS, QUIET)), false); // silence resets the run
  assert.equal(d.feed(frame(VAD_SUSTAIN_WINDOWS - 1, 4000)), false); // consecutive count restarted
});

test("a single hot sample (click) does not fire", () => {
  const d = new SustainedSpeechDetector(RATE);
  const f = frame(VAD_SUSTAIN_WINDOWS * 2, QUIET);
  f[WINDOW] = 32767; // one max-amplitude sample lifts one window at most
  assert.equal(d.feed(f), false);
});

test("silence never fires and pulls the noise floor down", () => {
  const d = new SustainedSpeechDetector(RATE);
  assert.equal(d.feed(frame(VAD_SUSTAIN_WINDOWS * 8, QUIET)), false);
  assert.ok(d.debug().floor < VAD_FLOOR_INITIAL);
  assert.equal(d.debug().threshold, VAD_MIN_RMS); // absolute minimum holds in a quiet room
});

test("adaptive floor: steady room noise raises the bar; speech over it still fires", () => {
  const d = new SustainedSpeechDetector(RATE);
  assert.equal(d.feed(frame(100, 300)), false); // sub-minimum noise never fires...
  const { floor, threshold } = d.debug();
  assert.ok(floor > 250 && floor <= 300, `floor learned the noise, got ${floor}`);
  assert.ok(threshold > 2 * VAD_MIN_RMS, `bar rose over the noise, got ${threshold}`);
  assert.equal(d.feed(frame(VAD_SUSTAIN_WINDOWS, 3200)), true); // voice over a noisy room fires
});

test("adaptive floor recovers quickly when the room goes quiet", () => {
  const d = new SustainedSpeechDetector(RATE);
  d.feed(frame(100, 300)); // noisy phase: threshold ≈ 900
  assert.equal(d.feed(frame(VAD_SUSTAIN_WINDOWS, SPEECH)), false); // 800 is under the raised bar
  d.feed(frame(30, QUIET)); // quiet again — floor falls fast
  assert.equal(d.debug().threshold, VAD_MIN_RMS);
  assert.equal(d.feed(frame(VAD_SUSTAIN_WINDOWS, SPEECH)), true);
});

test("fires once, then needs a fresh sustained run", () => {
  const d = new SustainedSpeechDetector(RATE);
  assert.equal(d.feed(frame(VAD_SUSTAIN_WINDOWS, SPEECH)), true);
  assert.equal(d.feed(frame(VAD_SUSTAIN_WINDOWS - 1, SPEECH)), false);
  assert.equal(d.feed(frame(1, SPEECH)), true);
});

test("reset() clears an in-progress run but keeps the learned floor", () => {
  const d = new SustainedSpeechDetector(RATE);
  d.feed(frame(100, 300));
  const floorBefore = d.debug().floor;
  d.feed(frame(VAD_SUSTAIN_WINDOWS - 1, 3200));
  d.reset();
  assert.equal(d.feed(frame(VAD_SUSTAIN_WINDOWS - 1, 3200)), false);
  assert.equal(d.debug().floor, floorBefore);
  assert.equal(d.feed(frame(1, 3200)), true);
});

test("debug() reports live numbers for mic tuning", () => {
  const d = new SustainedSpeechDetector(RATE);
  d.feed(frame(2, SPEECH));
  const dbg = d.debug();
  assert.equal(dbg.rms, SPEECH);
  assert.equal(dbg.active, 2);
  assert.equal(dbg.fires, 0);
  d.feed(frame(VAD_SUSTAIN_WINDOWS, SPEECH));
  assert.equal(d.debug().fires, 1);
});

// ---- MicUplink: the barge-in path itself ----------------------------------------------------------
// Not a port — lib/local-voice.ts executes exactly what feed() returns, so these ARE the frames that
// would reach ElevenLabs. The last round tested the gate alone and missed the defect below.

const FRAME_WINDOWS = 5; // 250 ms — MIC_FRAME_SECONDS in lib/local-voice.ts

function armed() {
  const u = new MicUplink();
  u.arm(RATE); // the server's `ready` frame
  return u;
}

function pcm(amplitude) {
  return frame(FRAME_WINDOWS, amplitude).buffer;
}

test("an open gate sends every frame and buffers nothing", () => {
  const u = armed();
  const f = pcm(QUIET);
  assert.deepEqual(u.feed(f, 0), { send: [f], interrupt: false });
});

test("nothing can barge in before the server's ready frame arms the detector", () => {
  const u = new MicUplink();
  u.onAudioStart();
  assert.deepEqual(u.feed(pcm(SPEECH), 0), { send: [], interrupt: false });
});

test("a real barge-in replays the pre-roll ring and opens the mic", () => {
  const u = armed();
  u.onAudioStart(); // she is genuinely speaking
  const pre = [pcm(QUIET), pcm(QUIET), pcm(QUIET), pcm(QUIET)];
  for (const f of pre) assert.deepEqual(u.feed(f, 0).send, []);
  const speech = pcm(SPEECH);
  const act = u.feed(speech, 0);
  assert.equal(act.interrupt, true);
  assert.equal(act.send.length, GATED_FRAME_BUFFER);
  assert.deepEqual(act.send, [pre[2], pre[3], speech]); // the ring, oldest first, triggering frame last
  const after = pcm(QUIET);
  assert.deepEqual(u.feed(after, 0), { send: [after], interrupt: false }); // gate force-opened
});

test("a held bracket keeps a deliberate utterance off the socket and still cuts the turn", () => {
  // Live: he waited out the card in silence, then said "Hola" — and it was transcribed into a turn.
  const u = armed();
  u.onAudioStart(true); // a blocking card holds the mic with no voice behind it
  for (let i = 0; i < 4; i++) assert.deepEqual(u.feed(pcm(QUIET), 0).send, []);
  const sent = [];
  let cuts = 0;
  for (let i = 0; i < 6; i++) {
    const act = u.feed(pcm(SPEECH), 0); // ~1.5 s of sustained speech
    if (act.interrupt) cuts++;
    sent.push(...act.send);
  }
  assert.equal(sent.length, 0, "the utterance reached ElevenLabs through the hold");
  assert.ok(cuts >= 1, "talking over an open card must still end the turn");
});

test("the held mic comes back the moment the server closes the bracket", () => {
  const u = armed();
  u.onAudioStart(true);
  assert.equal(u.feed(pcm(SPEECH), 0).interrupt, true);
  assert.deepEqual(u.feed(pcm(SPEECH), 0).send, [], "the client must not open its own gate");
  u.forceOpen(); // the server's `interrupted` / audio_end answers the cut
  const f = pcm(QUIET);
  assert.deepEqual(u.feed(f, 0), { send: [f], interrupt: false });
});

// A 250ms frame whose five 50ms VAD windows have the given amplitudes — a word too short to fire.
function shortWord() {
  const out = new Int16Array(WINDOW * FRAME_WINDOWS);
  const pattern = [QUIET, QUIET, SPEECH, SPEECH, SPEECH]; // 3 active windows < VAD_SUSTAIN_WINDOWS
  for (let w = 0; w < pattern.length; w++)
    for (let i = 0; i < WINDOW; i++) out[w * WINDOW + i] = (i % 2 ? 1 : -1) * pattern[w];
  return out.buffer;
}

test("a sub-VAD word at a held card never rides the ring into the next bracket's barge-in", () => {
  // Live shape: he mutters at the card (too short to cut), her reply takes the bracket over,
  // he barges in on her voice within the ring's depth — the mutter must not be replayed.
  const u = armed();
  u.onAudioStart(true);
  const cardWords = shortWord();
  assert.deepEqual(u.feed(cardWords, 0), { send: [], interrupt: false });
  u.onAudioStart(); // her reply takes the held bracket over (server _open_segment)
  const act = u.feed(pcm(SPEECH), 250);
  assert.equal(act.interrupt, true);
  assert.ok(!act.send.includes(cardWords), "card-time words were replayed to ElevenLabs");
});

test("a sub-VAD word at a held card never rides the ring through the hold-release tail", () => {
  const u = armed();
  u.onAudioStart(true);
  const cardWords = shortWord();
  assert.deepEqual(u.feed(cardWords, 0), { send: [], interrupt: false });
  u.onAudioEnd(1000); // card answered — the server releases the hold; the echo tail still gates
  const act = u.feed(pcm(SPEECH), 1100);
  assert.equal(act.interrupt, true);
  assert.ok(!act.send.includes(cardWords), "card-time words were replayed to ElevenLabs");
});

test("a sub-VAD word at a held card is dropped when an interrupted force-opens the gate", () => {
  const u = armed();
  u.onAudioStart(true);
  const cardWords = shortWord();
  assert.deepEqual(u.feed(cardWords, 0), { send: [], interrupt: false });
  u.forceOpen(); // the server's `interrupted` answers a held cut
  const f = pcm(QUIET);
  assert.deepEqual(u.feed(f, 0), { send: [f], interrupt: false });
});

test("words dropped at a held cut can never be replayed into a later turn", () => {
  const u = armed();
  u.onAudioStart(true);
  const held = pcm(SPEECH);
  assert.equal(u.feed(held, 0).interrupt, true);
  u.onAudioStart(); // the next turn really speaks, and he cuts THAT off
  const act = u.feed(pcm(SPEECH), 0);
  assert.equal(act.interrupt, true);
  assert.ok(!act.send.includes(held), "pre-cut audio was replayed into another turn");
});

test("the hold belongs to the bracket, not to the call", () => {
  const u = armed();
  assert.equal(u.held, false);
  u.onAudioStart(true);
  assert.equal(u.held, true);
  u.onAudioEnd(0);
  assert.equal(u.held, false);
  u.onAudioStart(true);
  u.forceOpen();
  assert.equal(u.held, false);
  u.onAudioStart(true);
  u.onAudioStart(); // a real segment takes the bracket over mid-card
  assert.equal(u.held, false);
});

test("an audio_start with no hold flag is an ordinary segment (old server)", () => {
  const u = armed();
  u.onAudioStart(); // a server that predates the flag never marks its bracket
  assert.equal(u.held, false);
  const speech = pcm(SPEECH);
  const act = u.feed(speech, 0);
  assert.deepEqual(act, { send: [speech], interrupt: true });
});
