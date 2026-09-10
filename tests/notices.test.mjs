// The call shell's one notice surface, run against the REAL lib/notices.ts (Node strips the types):
//   node tests/notices.test.mjs
// Agent mode had no error surface at all. The ElevenLabs SDK reports a
// mistyped agent id, an exhausted quota and every server error through `conversation.message` with
// `status === "error"`, and nobody read it — so a stranger's very first call dropped straight back to
// OFFLINE without a word, while local mode had the whole vocabulary of lib/voice-errors.ts. Agent
// mode ships in the open-source release, so that silence was its first-run experience.
// The rest pin the precedence the shell has always claimed but never proved: the events channel
// outranks everything (approval cards ride it), voice comes next, the avatar last.
import assert from "node:assert/strict";

const { describeAgentError, pickShellNotice, BRIDGE_DOWN_TEXT, MODEL_FAILED_TEXT } = await import(
  "../lib/notices.ts"
);

const { SETUP_FACE_URL, invited, invitedStep, alreadySentForAFace, rememberSentForAFace } =
  await import("../lib/first-run.ts");

const base = {
  bridgeDown: false,
  voiceNotice: null,
  dismissVoice: () => {},
  agentStatus: "disconnected",
  agentMessage: undefined,
  isLocalMode: false,
  modelFailed: false,
  dismissModel: () => {},
};
const pick = (over) => pickShellNotice({ ...base, ...over });

// ── An agent-mode failure must reach the user as words ────────────────────────────────────
{
  const badId = pick({
    agentStatus: "error",
    agentMessage: "Failed to fetch conversation token for agent agent_0000: 404 Not Found",
  });
  assert.ok(badId, "an agent-mode error must produce a notice — this was the wordless OFFLINE drop");
  assert.equal(badId.fatal, true, "the call is over; the notice has to persist");
  assert.ok(badId.text.length > 20, "a notice is words a person can act on");
  assert.ok(!/404|Failed to fetch|agent_0000/.test(badId.text), "never show her raw technical error");
  assert.match(badId.text, /agent id/i, "and name what to check");
}

{
  const quota = pick({ agentStatus: "error", agentMessage: "Server error: quota exceeded for this workspace" });
  assert.ok(quota && quota.fatal);
  assert.match(quota.text, /quota/i);
  assert.ok(!quota.text.includes("Server error"));
}

// The same token fetch fails for two different reasons — an auth failure must not send them off to
// check the agent id, which is the one thing that was right.
assert.match(
  describeAgentError(
    "Failed to fetch conversation token for agent agent_1: Your agent has authentication enabled, but no signed URL or conversation token was provided.",
  ),
  /ELEVENLABS_API_KEY|signed URL/,
);

// An error with no message at all still says something.
{
  const bare = pick({ agentStatus: "error", agentMessage: undefined });
  assert.ok(bare && bare.fatal && bare.text.length > 20);
}

// ── the gate is the LATCHED mode: local mode has its own vocabulary, agent's must stay out ─
assert.equal(
  pick({ isLocalMode: true, agentStatus: "error", agentMessage: "Server error: whatever" }),
  null,
  "in a latched local call the agent SDK's status is not ours to report",
);

// A healthy agent call says nothing.
assert.equal(pick({ agentStatus: "connected" }), null);
assert.equal(pick({}), null);

// ── precedence: the events channel outranks the rest, voice outranks the avatar ───────────
{
  const voice = { code: "tts_auth", fatal: true, text: "Her voice is gone for this call." };
  assert.equal(
    pick({ bridgeDown: true, voiceNotice: voice, agentStatus: "error", modelFailed: true }).text,
    BRIDGE_DOWN_TEXT,
  );
  assert.equal(pick({ voiceNotice: voice, agentStatus: "error", modelFailed: true }).text, voice.text);
  assert.equal(pick({ agentStatus: "error", modelFailed: true }).text, describeAgentError(undefined));
  assert.equal(pick({ modelFailed: true }).text, MODEL_FAILED_TEXT);
}

// Both dismissable notices keep their own dismisser; the bridge one is transient and has none.
{
  let dismissed = "";
  const voice = { code: "tts_stream", fatal: false, text: "Her voice cut out for a moment." };
  assert.equal(pick({ voiceNotice: voice, dismissVoice: () => (dismissed = "voice") }).fatal, false);
  pick({ voiceNotice: voice, dismissVoice: () => (dismissed = "voice") }).onDismiss();
  assert.equal(dismissed, "voice");
  pick({ modelFailed: true, dismissModel: () => (dismissed = "model") }).onDismiss();
  assert.equal(dismissed, "model");
  assert.equal(pick({ bridgeDown: true }).onDismiss, undefined);
}

// ── describeAgentError never leaks a code, and always ends up with something to say ───────
for (const raw of [
  "Failed to fetch conversation token for agent agent_1: Your agent has authentication enabled, but no signed URL or conversation token was provided.",
  "Server error: agent not found",
  "The connection was closed due to a socket error.",
  "Session failed to start",
  "",
  undefined,
  null,
  { message: "not a string" },
]) {
  const text = describeAgentError(raw);
  assert.equal(typeof text, "string");
  assert.ok(text.length > 20, `an unhelpful error still needs words: ${String(raw)}`);
  assert.ok(!text.includes("Server error"), "never echo the SDK's own wording");
}

// ── The model notice is the SECOND arrival ─────────────────────────────────────────────────
// /app sends a first visit straight to the face step, so seeing this at all means a face was
// declined. It names the way back, and it must name the button by the label the button carries.
{
  const missing = pick({ modelMissing: true, modelsDir: "C:\\Users\\ada\\.kotoba\\models" });
  assert.ok(missing, "a missing model has to say so");
  assert.ok(!/Reconfigure/.test(missing.text), "that button is labelled `Run setup again`");
  assert.match(missing.text, /Run setup again/, "and the label is the one to name");
  assert.ok(missing.text.includes("C:\\Users\\ada\\.kotoba\\models"),
    "the manual route still names the folder the backend really reads");
  assert.ok(missing.onDismiss, "and it stays dismissable — she runs without a face");
}

// ── The step a URL may ask for ─────────────────────────────────────────────────────────────
assert.equal(invitedStep("?reconfigure=1&step=face"), "face");
assert.equal(invitedStep("?reconfigure=1"), "", "no step named is the beginning");
assert.equal(invitedStep("not a query"), "", "and nothing unparseable throws");
assert.ok(invited(SETUP_FACE_URL.slice(SETUP_FACE_URL.indexOf("?"))),
  "the face link must carry the invitation too, or the door sends it straight back to /app");
assert.match(SETUP_FACE_URL, /step=face/);

// ── Once per BROWSER, or the two screens bounce a declined face between them ───────────────
{
  const store = new Map();
  globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
  };
  assert.equal(alreadySentForAFace(), false, "a fresh browser has not sent anybody anywhere");
  rememberSentForAFace();
  assert.equal(alreadySentForAFace(), true, "and the second arrival is left alone");

  // Per-tab storage is not the memory this needs: declining in one tab and opening another asked
  // again, from a screen whose only way out is the screen it came from.
  globalThis.sessionStorage = { getItem: () => null, setItem: () => {} };
  assert.equal(alreadySentForAFace(), true, "a second tab is not a second offer");

  globalThis.localStorage = {
    getItem: () => { throw new Error("blocked"); },
    setItem: () => { throw new Error("blocked"); },
  };
  assert.equal(alreadySentForAFace(), true,
    "no storage is no memory, and a loop between two screens is worse than no redirect");
  rememberSentForAFace();   // must not throw
  delete globalThis.sessionStorage;
  delete globalThis.localStorage;
}

console.log("notices.test.mjs: all assertions passed");
