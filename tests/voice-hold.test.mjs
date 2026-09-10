// Does a hold the server sent actually shut this microphone?
//   node tests/voice-hold.test.mjs
//
// Run against the REAL MicUplink / PlaybackScheduler / applyAudioFrame (Node strips the types), over
// the frame trace the BACKEND produced and recorded as a snapshot, loaded below. A hand-written
// port of that trace would have passed while the real
// one failed, which is exactly what happened last round: the trace's hold names turn 1, the client
// buried turn 1 the moment it barged in, and the scheduler answers "stale" — so routing the hold
// through it dropped the gate close and the mic was live for the rest of the card.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const { MicUplink, applyAudioFrame, GATE_TAIL_MS } = await import("../lib/voice-gate.ts");
const { PlaybackScheduler } = await import("../lib/voice-playback.ts");

const traceUrl = new URL("../api/tests/snapshots/voice_hold_across_turns.json", import.meta.url);
const trace = JSON.parse(readFileSync(fileURLToPath(traceUrl), "utf-8"));

const RATE = 16000;
const loud = () => new Int16Array(3200).fill(5000).buffer; // 4 × 50 ms windows over the VAD threshold
const quiet = () => new Int16Array(3200).fill(20).buffer;  // room tone: heard by nobody

const at = (kind, hold) =>
  trace.findIndex((f) => f.type === kind && (hold === undefined || Boolean(f.hold) === hold));

// The trace has to be the measured shape, or the replay below proves nothing.
assert.ok(at("audio_start", false) >= 0, "no spoken bracket in the trace");
assert.ok(at("interrupted") > at("audio_start", false), "nothing interrupted her");
assert.ok(at("audio_start", true) > at("interrupted"), "no hold followed the interrupt");

const mic = new MicUplink();
const sched = new PlaybackScheduler();
mic.arm(RATE);
let now = 1000;
const apply = (frame) => applyAudioFrame(mic, sched, frame, now, () => {});

// 1. She speaks. The gate shuts, so the room does not go up the socket.
apply(trace[at("audio_start", false)]);
assert.equal(mic.feed(quiet(), now).send.length, 0, "the gate did not shut on her audio_start");

// 2. Utterance one, over her voice. The client barges in: ring replayed, gate forced open, turn
//    buried — this is lib/local-voice.ts bargeIn(), which is why sched.flush() follows.
now += 100;
const first = mic.feed(loud(), now);
assert.equal(first.interrupt, true, "the barge-in never fired");
assert.ok(first.send.length > 0, "over her voice the pre-roll ring must be replayed");
sched.flush();

// 3. The server's own frames land: `interrupted`, then the hold for the card that survived.
now += 10;
apply(trace[at("interrupted")]);
const holdFrame = trace[at("audio_start", true)];
assert.equal(
  sched.onAudioStart(Number(holdFrame.turn) || 0),
  "stale",
  "the turn the hold names must already be buried — otherwise this test proves nothing",
);
apply(holdFrame);

// 4. Utterance TWO. This is the one the old code sold to ElevenLabs: the hold is up, so his words cut
//    the turn and nothing whatsoever goes on the wire.
now += 100;
const second = mic.feed(loud(), now);
assert.equal(second.send.length, 0, "the second utterance reached the socket with a card still open");
assert.equal(second.interrupt, true, "behind a hold his voice must still cut the turn");
assert.equal(mic.held, true, "the gate should still be marked held");

// 5. The card is gone, the server closes the bracket, and the microphone really does come back — a mic
//    left shut with no card open is the failure this whole bracket exists to avoid.
now += 10;
apply(trace[at("audio_end")]);
now += GATE_TAIL_MS + 1;
const third = mic.feed(loud(), now);
assert.equal(third.send.length, 1, "the mic never re-opened after the bracket closed");
assert.equal(third.interrupt, false, "an open gate must not barge in");
assert.equal(mic.held, false);

console.log("voice-hold: ok");
