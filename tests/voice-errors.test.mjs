// Voice error frames must reach the user as words, not console noise — run against the REAL
// lib/voice-errors.ts (Node strips the types):
//   node tests/voice-errors.test.mjs
// Guards: every fatal frame yields a persistent notice a person can act on (a mistyped ElevenLabs
// key must say so, never the raw code), voice-breaking transient frames yield an auto-dismissable
// notice, chatty per-message codes stay console-only, and no notice ever leaks a raw code as text.
// The mic half (describeMicError / micUnsupportedNotice) covers browser-side getUserMedia failures:
// a DENIED mic must name the browser permission (never blame Kotoba or a key), every mic notice is
// fatal (the user acts outside the app, so it must persist), carries a mic_* code (the status line
// keys off the prefix), and says typing still works.
import assert from "node:assert/strict";

const { describeVoiceError, describeMicError, describeSkip, micUnsupportedNotice, socketNeverOpenedNotice } =
  await import("../lib/voice-errors.ts");

const badKey = describeVoiceError("stt_auth", true);
assert.ok(badKey, "a fatal stt_auth must produce a notice — this was the silent-forever call");
assert.equal(badKey.fatal, true);
assert.match(badKey.text, /API key/i, "the user must be told the key is the problem");
assert.match(badKey.text, /ELEVENLABS_API_KEY/, "and where to fix it");

for (const code of ["stt_connect", "stt_closed", "quota_exceeded", "auth_error"]) {
  const n = describeVoiceError(code, true);
  assert.ok(n && n.fatal, `fatal ${code} must surface`);
  assert.notEqual(n.text, code, "a notice is words, not the raw code");
  assert.ok(!n.text.includes(code), `the raw code ${code} must not appear in the text`);
}

// The noise cost latch. A rolling cap is a rate and rates do not end, so a forgotten call bought up
// to 12 turns a minute forever; the backend now takes the mic after two saturated windows. The notice
// has to do two things no other fatal does: name the ROOM rather than a key or a quota, and give a way
// back — a latch nobody can undo is one nobody will forgive.
const noisy = describeVoiceError("mic_noise", true);
assert.ok(noisy && noisy.fatal, "the latch must persist on screen — it lasts the whole call");
assert.equal(noisy.code, "mic_noise");
assert.ok(noisy.code.startsWith("mic_"), "the status line must stop saying 'just talk' — the mic IS off");
assert.match(noisy.text, /mute|hang up/i, "a latch with no way back is a dead end");
assert.match(noisy.text, /type/i, "and typing still works");
assert.doesNotMatch(noisy.text, /API key|ELEVENLABS_API_KEY/i, "nobody should go rotate a good key");
assert.ok(!noisy.text.includes("mic_noise"), "a notice is words, not the raw code");

const midCallKey = describeVoiceError("tts_auth", true);
assert.ok(midCallKey && midCallKey.fatal, "a key rejected mid-call must surface as fatal");
assert.match(midCallKey.text, /API key/i, "the user must be told the key is the problem");
assert.match(midCallKey.text, /ELEVENLABS_API_KEY/, "and where to fix it");
assert.doesNotMatch(midCallKey.text, /quota/i, "a rejected key must not be blamed on quota");

const midCallQuota = describeVoiceError("tts_quota", true);
assert.ok(midCallQuota && midCallQuota.fatal, "exhausted quota mid-call must surface as fatal");
assert.match(midCallQuota.text, /quota/i, "the user must be told it is the quota");
assert.doesNotMatch(
  midCallQuota.text,
  /API key|ELEVENLABS_API_KEY/i,
  "quota exhaustion must not send anyone off to rotate a perfectly good key",
);

const unknownFatal = describeVoiceError("some_future_code", true);
assert.ok(unknownFatal && unknownFatal.fatal, "an unmapped fatal frame still gets a generic notice");
assert.ok(!unknownFatal.text.includes("some_future_code"));

const ttsDown = describeVoiceError("tts_unavailable", false);
assert.ok(ttsDown && ttsDown.fatal === false, "tts_unavailable is a transient notice");
const ttsBlip = describeVoiceError("tts_stream", false);
assert.ok(ttsBlip && ttsBlip.fatal === false, "tts_stream is a transient notice");

assert.equal(describeVoiceError("bad_message", false), null, "protocol nits stay console-only");
assert.equal(describeVoiceError("commit_throttled", false), null, "chatty STT rejections stay console-only");
assert.equal(describeVoiceError(undefined, false), null);

const denied = describeMicError(new DOMException("Permission denied", "NotAllowedError"));
assert.equal(denied.code, "mic_denied", "a refused permission is mic_denied — this was the silent LIVE call");
assert.equal(denied.fatal, true, "mic notices persist until the user acts");
assert.match(denied.text, /browser/i, "the user must be told it is the BROWSER, not Kotoba");
assert.match(denied.text, /allow|permission/i, "and what to do about it");
assert.match(denied.text, /type/i, "and that typing still works");
assert.doesNotMatch(denied.text, /API key|quota/i, "a mic problem must not send anyone key-hunting");
assert.equal(describeMicError(new DOMException("blocked", "SecurityError")).code, "mic_denied");

const missing = describeMicError(new DOMException("no device", "NotFoundError"));
assert.equal(missing.code, "mic_missing");
assert.ok(missing.fatal);
assert.match(missing.text, /microphone/i);

const busy = describeMicError(new DOMException("hw", "NotReadableError"));
assert.equal(busy.code, "mic_busy");
assert.match(busy.text, /another app|in use/i, "a busy device points at the other app");

const unknownMic = describeMicError(new Error("weird worklet failure"));
assert.equal(unknownMic.code, "mic_failed", "anything else still surfaces instead of console-only");
assert.ok(unknownMic.fatal);
assert.doesNotMatch(unknownMic.text, /weird worklet/, "raw error text never leaks into the notice");

const insecure = micUnsupportedNotice();
assert.equal(insecure.code, "mic_insecure");
assert.ok(insecure.fatal);
assert.match(insecure.text, /HTTPS|localhost/, "plain-HTTP hosts must learn why there is no mic prompt at all");

for (const n of [denied, missing, busy, unknownMic, insecure]) {
  assert.ok(n.code.startsWith("mic_"), "the shell keys the status line off the mic_ prefix");
}

// A dropped turn must never be invisible. The `skipped` frame had no case in the client switch at
// all, so a server that thought the mic was muted while this side had it live left the UI saying
// "just talk" while every word was discarded — deaf for the rest of the page, with no clue why.
const onMute = describeSkip("muted", true);
assert.ok(onMute && onMute.fatal === false, "a turn dropped for mute is a transient notice");
assert.match(onMute.text, /mute/i, "the user must learn WHY she said nothing");

const desync = describeSkip("muted", false);
assert.ok(desync, "server-muted while the client is live is the disagreement — it must surface");
assert.notEqual(desync.text, onMute.text, "the desync reads differently: the user did not mute anything");
assert.equal(desync.code, "skipped_muted_desync");

const throttled = describeSkip("too_many_turns", false);
assert.ok(throttled && throttled.fatal === false);
assert.match(throttled.text, /noise|background/i, "point at the room, not at her");

assert.equal(describeSkip("some_future_reason", false), null, "unknown reasons stay console-only");
assert.equal(describeSkip(undefined, false), null);
for (const n of [onMute, desync, throttled]) {
  assert.ok(!n.text.includes(n.code), "a notice is words, not the raw code");
  assert.ok(!n.code.startsWith("mic_"), "a skip is not a broken microphone — the status line keys off mic_");
}

console.log("voice-errors.test.mjs: all assertions passed");

// A socket refused before it opened used to be reported as an ordinary disconnect, so the call
// button went back to idle and named nothing at all. The commonest cause is serving the page on a
// port the backend's own allowlist does not know, which the browser will not describe either.
const refused = socketNeverOpenedNotice();
assert.equal(refused.fatal, true, "the call cannot be retried into working — it must persist");
assert.match(refused.code, /^ws_/, "the code says which half broke");
assert.doesNotMatch(refused.text, /403|ws_refused/, "no raw status code or internal name to read");
assert.match(refused.text, /CORS_ORIGINS/, "it names the one setting that fixes it");
// It used to end "Typing to her still works." — text rides the same socket, so with the socket
// refused the composer is disabled and that sentence sent people to a box they could not use.
assert.match(refused.text, /cannot be reached/, "it must not promise a way in that is also shut");
assert.ok(!refused.code.startsWith("mic_"), "not a microphone problem — the status line keys off that");
