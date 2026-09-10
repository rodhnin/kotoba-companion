// Lip-sync amplitude. The source is STRUCTURAL: anything exposing the ElevenLabs SDK contract
// `getOutputByteFrequencyData(): Uint8Array` (empty when inactive) — the EL agent or useLocalVoice().
// Bin counts differ per mode (EL WebRTC is hardcoded pcm_48000; local runs 24 kHz): averaging the
// whole spectrum is robust to that — never hardcode bin indices.
"use client";

/** What Live2DCanvas needs from a voice source: speaking flag + the EL-shaped spectrum getter. */
export interface VoiceAudioSource {
  isSpeaking: boolean;
  getOutputByteFrequencyData: () => Uint8Array;
}

/** Map the agent's output frequency spectrum to a 0.0–1.0 mouth-open value. */
export function getMouthAmplitude(source: VoiceAudioSource): number {
  const data = source.getOutputByteFrequencyData();
  if (!data || data.length === 0) return 0;

  let sum = 0;
  for (let i = 0; i < data.length; i++) sum += data[i];
  return Math.min(1, sum / data.length / 255);
}
