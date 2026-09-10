/**
 * What the call shell CLAIMS about the call — the badge and the status line under the controls —
 * decided in one place so both surfaces read from the same voice fact. They used to contradict the
 * notice pill: a fatal `stt_auth` left "LIVE" and "You're connected — just talk" up permanently while
 * the one true sentence carried a dismiss control. `voiceLost`, not bare `fatal`, is the trigger:
 * losing her HEARING makes "just talk" the lie, while losing her SPEECH leaves talking honest because
 * captions answer, and killing the invitation there would be the over-correction. The badge says
 * VOICE DOWN rather than dropping to OFFLINE, because the WebSocket is up and a typed turn still
 * works — "disconnected" would be its own lie. The session is alive; the voice is not.
 */

import type { VoiceLost } from "@/lib/voice-errors";

export type CallPhase = "offline" | "ringing" | "live";

export type CallStatusInput = {
  phase: CallPhase;
  working: boolean;
  isMuted: boolean;
  micBlocked: boolean;
  voiceLost: VoiceLost | null;
};

export type CallStatusLine = { text: string; tone: "working" | "alert" | "calm" };

export function badgeLabel(phase: CallPhase, voiceDown: boolean): string {
  if (phase === "live") return voiceDown ? "VOICE DOWN" : "LIVE";
  return phase === "ringing" ? "RINGING" : "OFFLINE";
}

/** Precedence: work talks over everything; the browser-side mic block keeps its more specific
 *  words; a hearing loss outranks mute (unmuting would fix nothing); mute outranks a speech loss
 *  (it is the half the user can act on). */
export function callStatusLine(s: CallStatusInput): CallStatusLine {
  if (s.working) return { text: "Focused — working…", tone: "working" };
  if (s.phase === "offline") return { text: "Tap the mic to call Kotoba", tone: "calm" };
  if (s.phase === "ringing") return { text: "Calling…", tone: "calm" };
  if (s.micBlocked) return { text: "Mic blocked — she can't hear you, but you can type", tone: "alert" };
  if (s.voiceLost === "hearing")
    return { text: "Voice is down — she can't hear you, but you can type", tone: "alert" };
  if (s.isMuted) return { text: "You're muted — she can't hear you", tone: "alert" };
  if (s.voiceLost === "speech")
    return { text: "Her voice is down — talk or type, captions still work", tone: "alert" };
  return { text: "You're connected — just talk", tone: "calm" };
}
