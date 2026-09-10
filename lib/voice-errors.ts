/**
 * Voice-WS error frames turned into words a person can act on, in the LOCAL voice mode. Fatal
 * notices stay up until the next call, transient ones auto-dismiss, and a code with no entry stays
 * console-only — surfacing every one buries the ones that matter. `mic_noise` is the cost latch:
 * speech kept arriving past the burst cap for two windows running, so the backend took the mic for
 * the rest of the call; the `mic_` prefix is deliberate, because the mic really IS off and the status
 * line must stop inviting the user to talk. `describeMicError` and `micUnsupportedNotice` cover what
 * never reaches the WS — getUserMedia rejections (permission, missing device, busy device, insecure
 * context), all fatal: a denied mic left the call saying LIVE while she could never hear a word.
 */

/** What a FATAL notice took for the rest of the call. Classified HERE — the words already say
 *  "she can't hear you" vs "her voice is gone", so this is the one place that knows — and
 *  consumed by lib/call-status.ts, which reroutes the badge and the status line off it. Transient
 *  notices carry none. */
export type VoiceLost = "hearing" | "speech";

export type VoiceErrorNotice = {
  code: string;
  fatal: boolean;
  lost?: VoiceLost;
  text: string;
};

const FATAL_TEXT: Record<string, string> = {
  stt_auth:
    "ElevenLabs rejected the API key, so she can't hear you. Check your ELEVENLABS_API_KEY, then start the call again.",
  auth_error:
    "ElevenLabs rejected the API key, so she can't hear you. Check your ELEVENLABS_API_KEY, then start the call again.",
  stt_connect:
    "Couldn't reach ElevenLabs speech recognition, so she can't hear you. Check your connection and API key, then start the call again.",
  stt_closed:
    "Speech recognition keeps dropping, so the mic is off for this call. You can still type — hang up and call again to retry voice.",
  mic_noise:
    "Speech kept arriving faster than anyone talks, so the mic is off — that is usually a TV, a fan or a call nobody hung up, and every one of those turns costs money. Mute and unmute to bring it back, or hang up and call again. You can still type.",
  quota_exceeded:
    "The ElevenLabs quota is used up, so she can't hear you right now. You can still type.",
  tts_auth:
    "ElevenLabs rejected the API key, so her voice is gone for this call. Check your ELEVENLABS_API_KEY, then start the call again.",
  tts_quota:
    "The ElevenLabs quota is used up, so her voice is gone for now — captions still work. Add credits or wait for the quota to reset.",
};

const TRANSIENT_TEXT: Record<string, string> = {
  tts_unavailable: "Her voice couldn't start — captions only for now.",
  tts_stream: "Her voice cut out for a moment — a sentence may have gone captions-only.",
};

const FATAL_DEFAULT = "Voice hit a problem it can't recover from this call. You can still type.";

export function describeVoiceError(code: unknown, fatal: unknown): VoiceErrorNotice | null {
  const key = typeof code === "string" ? code : "";
  if (fatal)
    return {
      code: key,
      fatal: true,
      lost: key.startsWith("tts_") ? "speech" : "hearing",
      text: FATAL_TEXT[key] ?? FATAL_DEFAULT,
    };
  const text = TRANSIENT_TEXT[key];
  return text ? { code: key, fatal: false, text } : null;
}

const MIC_TEXT: Record<string, string> = {
  mic_denied:
    "The browser blocked the microphone, so she can't hear you. Allow mic access for this site (the icon by the address bar), then call again. You can still type.",
  mic_missing:
    "No microphone was found, so she can't hear you. Plug one in or pick another input device in the browser, then call again. You can still type.",
  mic_busy:
    "The microphone couldn't start — another app may be using it. Free it up, then call again. You can still type.",
  mic_failed: "The microphone couldn't start, so she can't hear you. You can still type.",
};

/** The socket closed before it ever said `ready`, so nothing was wrong with the call — it never
 *  started one. The handshake is exempt from CORS and the backend keeps its own allowlist, so a page
 *  served on an unexpected port is refused with a 403 the browser will not describe. Without this the
 *  button simply did nothing, which reads as the product being broken. */
export function socketNeverOpenedNotice(): VoiceErrorNotice {
  return {
    code: "ws_refused",
    fatal: true,
    lost: "hearing",
    text:
      "The voice connection was refused before it opened, and typing rides the same connection, so " +
      "she cannot be reached from this page at all. The backend only accepts the page it knows " +
      "about: if you are serving this on a different port, add that address to CORS_ORIGINS and " +
      "restart.",
  };
}

export function describeMicError(err: unknown): VoiceErrorNotice {
  const name = err instanceof Error || err instanceof DOMException ? err.name : "";
  const code =
    name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError"
      ? "mic_denied"
      : name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError"
        ? "mic_missing"
        : name === "NotReadableError" || name === "TrackStartError"
          ? "mic_busy"
          : "mic_failed";
  return { code, fatal: true, lost: "hearing", text: MIC_TEXT[code] };
}

/** A turn the backend refused. This client declares mute and the backend enforces it, so the two
 *  can disagree — and the drop was invisible: no case in the client switch, the UI still saying
 *  "just talk". The desync notice is the load-bearing one; the client re-declares on it, so it
 *  lasts a turn, not a page. */
const SKIP_TEXT: Record<string, string> = {
  muted: "Your mic is muted, so she didn't hear that. Unmute to talk — typing always works.",
  muted_desync:
    "She had the mic down as muted while your side had it live, so that went unheard. Reconnecting them now — say it again.",
  too_many_turns:
    "Speech is arriving faster than anyone talks, so she's ignoring the mic for a moment. Check for background noise or a second speaker nearby.",
};

export function describeSkip(reason: unknown, clientMuted: boolean): VoiceErrorNotice | null {
  const raw = typeof reason === "string" ? reason : "";
  const key = raw === "muted" && !clientMuted ? "muted_desync" : raw;
  const text = SKIP_TEXT[key];
  return text ? { code: `skipped_${key}`, fatal: false, text } : null;
}

export function micUnsupportedNotice(): VoiceErrorNotice {
  return {
    code: "mic_insecure",
    fatal: true,
    lost: "hearing",
    text: "This browser only allows the microphone on HTTPS or localhost, so she can't hear you here. Open Kotoba over HTTPS (or on localhost) for voice — you can still type.",
  };
}
