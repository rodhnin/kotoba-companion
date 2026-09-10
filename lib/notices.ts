/**
 * The one notice surface on the call shell — the pill on the webcam card — and the precedence that
 * decides which silent failure gets to use it, kept here so it can be pinned by a test instead of by
 * a nested ternary nobody reads. The events channel outranks everything: approval cards ride it, so
 * while it is down nothing else on screen can be trusted. Voice comes next, because a call that
 * cannot hear or speak is the failure a person notices first. The avatar failing to load is last —
 * calls, chat and work all still run.
 */

import type { VoiceErrorNotice } from "@/lib/voice-errors";

export type ShellNotice = { fatal: boolean; text: string; onDismiss?: () => void };

export const BRIDGE_DOWN_TEXT =
  "Can't reach the backend right now — retrying. Approvals and live progress are paused until it's back.";
export const MODEL_FAILED_TEXT =
  "Her avatar didn't load — the model is installed but something in it failed to fetch or parse (the browser's Network tab names the file). Calls, chat and work all still run.";

/** Not a failure: nothing is installed, which is every fresh install. It names the folder the backend
 *  actually reads rather than the env var that used to be the control, so the instruction is one a
 *  person can follow without a rebuild. */
export const DEFAULT_MODELS_DIR = "~/.kotoba/models";

/** The SECOND arrival: the first one is sent to the face step outright, and coming back here means a
 *  face was declined. So it names the way back — with the label that button actually carries, and
 *  with the walk that follows it, because the button lands on the brain step six dots earlier. */
export function modelMissingText(modelsDir: string): string {
  return `No Live2D model is installed yet, so she has no face — Settings → Brain → Run setup again, then walk to Her face, or unpack a Cubism 4 model into its own folder under ${modelsDir || DEFAULT_MODELS_DIR} and reload. Calls, chat and work all still run.`;
}

/** Agent mode's own vocabulary. The ElevenLabs SDK reports a failed start, a rejected key, a
 *  spent quota and every server error as one free-text string, so these read it by shape and
 *  answer in the same voice as lib/voice-errors.ts — what broke, where to fix it, and that typing
 *  still works. Most specific cause first: a token fetch that failed on authentication names both
 *  the token and the agent, and "check the agent id" would be the wrong thing to send someone off
 *  to check. */
const AGENT_TEXT: [RegExp, string][] = [
  [
    /auth|signed url|unauthor|forbidden|api[ _]?key|401|403/i,
    "ElevenLabs rejected the credentials for this agent. Check your ELEVENLABS_API_KEY, and whether the agent needs a signed URL, then call again. You can still type.",
  ],
  [
    /quota|credit|insufficient|payment|429|402/i,
    "The ElevenLabs quota is used up, so the call can't start. Add credits or wait for the quota to reset — you can still type.",
  ],
  [
    /conversation token|agent[ _]?id|not found|no agent|404/i,
    "ElevenLabs wouldn't start a call with that agent. Check the agent id in Settings (or NEXT_PUBLIC_ELEVENLABS_AGENT_ID), then call again. You can still type.",
  ],
  [
    /duration|timed out|timeout/i,
    "The call reached its ElevenLabs time limit and ended. Call again to pick up where you left off — anything running kept going.",
  ],
];

const AGENT_DEFAULT =
  "The ElevenLabs call failed, so voice is off for now. Check your ElevenLabs account and the agent's settings, then call again. You can still type.";

export function describeAgentError(message: unknown): string {
  const raw = typeof message === "string" ? message : "";
  for (const [pattern, text] of AGENT_TEXT) if (pattern.test(raw)) return text;
  return AGENT_DEFAULT;
}

export type ShellNoticeInput = {
  bridgeDown: boolean;
  voiceNotice: VoiceErrorNotice | null;
  dismissVoice: () => void;
  isLocalMode: boolean;
  agentStatus: string;
  agentMessage?: string;
  modelFailed: boolean;
  modelMissing: boolean;
  modelsDir: string;
  dismissModel: () => void;
};

export function pickShellNotice(s: ShellNoticeInput): ShellNotice | null {
  if (s.bridgeDown) return { fatal: false, text: BRIDGE_DOWN_TEXT };
  if (s.voiceNotice)
    return { fatal: s.voiceNotice.fatal, text: s.voiceNotice.text, onDismiss: s.dismissVoice };
  // Gated on the LATCHED mode, like every other transport read: a mid-call Settings flip must not
  // start quoting a transport this call never used. It clears itself when the next call changes status.
  if (!s.isLocalMode && s.agentStatus === "error")
    return { fatal: true, text: describeAgentError(s.agentMessage) };
  // Absence before failure: a model that is not there cannot also have failed to load, and "check the
  // Network tab" for a fetch nobody made is the kind of advice that sends a first run in circles.
  if (s.modelMissing) return { fatal: true, text: modelMissingText(s.modelsDir), onDismiss: s.dismissModel };
  if (s.modelFailed) return { fatal: true, text: MODEL_FAILED_TEXT, onDismiss: s.dismissModel };
  return null;
}
