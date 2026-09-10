// Two surfaces, one fact: while a fatal voice notice is up, the badge and the status line must stop
// saying "LIVE" / "just talk" — run against the REAL lib (Node strips the types):
//   node tests/voice-status.test.mjs
// Live QA (screenshot-confirmed): a bad ELEVENLABS_API_KEY put the one true sentence on a dismissible
// pill while "LIVE" and "You're connected — just talk" stayed up permanently — an invitation to do the
// one thing that could not work. The status surfaces read only (working, phase, micBlocked, isMuted),
// so no server-side fatal frame could ever reach them. lib/call-status.ts is the seam: both surfaces
// decide from the same voice fact. voice-errors.ts classifies WHICH half died, because bare `fatal`
// over-corrects: losing her HEARING kills the talk invitation, losing her SPEECH does not (talking
// still works — captions answer). Transient blips must change neither surface.
import assert from "node:assert/strict";

const { describeVoiceError, describeMicError, micUnsupportedNotice } = await import(
  "../lib/voice-errors.ts"
);

// ── the reproduced contradiction: the fact reached the frontend, no status surface could hear it ──
const badKey = describeVoiceError("stt_auth", true);
assert.ok(badKey && badKey.fatal, "the pill half of the fix was always right");
assert.ok(
  !badKey.code.startsWith("mic_"),
  "no mic_ prefix — so micBlocked, the only channel into the status line, stayed false",
);

const { badgeLabel, callStatusLine } = await import("../lib/call-status.ts");

const line = (over) =>
  callStatusLine({ phase: "live", working: false, isMuted: false, micBlocked: false, voiceLost: null, ...over });

// ── voice-errors is the one place that knows what a code means: it must also say what died ──
assert.equal(badKey.lost, "hearing", "stt_auth kills her hearing — the notice must carry that");
for (const code of ["auth_error", "stt_connect", "stt_closed", "quota_exceeded", "mic_noise", "some_future_code"]) {
  assert.equal(describeVoiceError(code, true).lost, "hearing", `fatal ${code} means she can't hear`);
}
for (const code of ["tts_auth", "tts_quota"]) {
  assert.equal(describeVoiceError(code, true).lost, "speech", `fatal ${code} kills only her voice out`);
}
assert.equal(describeMicError(new Error("nope")).lost, "hearing");
assert.equal(micUnsupportedNotice().lost, "hearing");
for (const code of ["tts_unavailable", "tts_stream"]) {
  assert.equal(describeVoiceError(code, false).lost, undefined, `transient ${code} must not claim a loss`);
}

// ── hearing lost: the invitation dies on BOTH surfaces, and typing is offered instead ──────
{
  const s = line({ voiceLost: badKey.lost });
  assert.doesNotMatch(s.text, /just talk/i, "the one thing that cannot work must not be invited");
  assert.match(s.text, /can't hear you/i, "the status has to say what is actually true");
  assert.match(s.text, /type/i, "and that typing still works");
  assert.equal(s.tone, "alert");
  assert.equal(badgeLabel("live", true), "VOICE DOWN", "the session is up, the voice is not — say exactly that");
}

// ── speech lost: talking still works, so killing the invitation here would be the new lie ──
{
  const s = line({ voiceLost: "speech" });
  assert.doesNotMatch(s.text, /can't hear you/i, "she hears fine — do not claim otherwise");
  assert.match(s.text, /talk/i, "talking still reaches her and must stay offered");
  assert.match(s.text, /caption/i, "and where her answers now land");
  assert.equal(s.tone, "alert");
  assert.equal(badgeLabel("live", true), "VOICE DOWN", "her voice being gone for the call is not LIVE either");
}

// ── over-correction guard: a transient blip changes neither surface ────────────────────────
{
  const blip = describeVoiceError("tts_stream", false);
  assert.ok(blip && !blip.fatal && blip.lost === undefined);
  const s = line({});
  assert.equal(s.text, "You're connected — just talk", "a hiccup on the pill must not rewrite the status");
  assert.equal(s.tone, "calm");
  assert.equal(badgeLabel("live", false), "LIVE");
}

// ── the badge's other states survive: a fatal pill outliving the call must not relabel OFFLINE ──
assert.equal(badgeLabel("offline", true), "OFFLINE");
assert.equal(badgeLabel("ringing", false), "RINGING");
assert.equal(badgeLabel("offline", false), "OFFLINE");

// ── precedence: work talks over everything; the more specific loss wins; mute stays honest ─
assert.equal(line({ working: true, voiceLost: "hearing" }).text, "Focused — working…");
assert.equal(line({ phase: "offline", voiceLost: "hearing" }).text, "Tap the mic to call Kotoba");
assert.equal(line({ phase: "ringing" }).text, "Calling…");
assert.match(line({ micBlocked: true, voiceLost: "hearing" }).text, /^Mic blocked/, "the browser-side words are the more specific truth");
assert.match(line({ isMuted: true, voiceLost: "hearing" }).text, /can't hear you/i);
assert.doesNotMatch(line({ isMuted: true, voiceLost: "hearing" }).text, /muted/i, "unmuting would fix nothing — don't offer it");
assert.match(line({ isMuted: true, voiceLost: "speech" }).text, /muted/i, "muted is the actionable half when only her voice is gone");
assert.match(line({ isMuted: true }).text, /muted/i);

console.log("voice-status.test.mjs: all assertions passed");
