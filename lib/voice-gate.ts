// Mic gating, local VAD and the barge-in decision for the LOCAL voice mode; they live together
// because the barge-in decision needs all three. THE GATE is closed from `audio_start` until local
// playback truly DRAINS (the server's `audio_end` outruns realtime), plus a tail, so echo already in
// the mic pipeline never reaches STT. Open, EVERY frame goes up the socket — the server's VAD needs
// the silence to detect end-of-turn; shut, frames feed only the local VAD, whose noise floor stays
// calibrated. A HELD bracket is one the server opened with NO voice behind it, to keep the mic shut
// while a blocking card waits on a human; its pre-roll ring is DROPPED rather than replayed, because
// those frames are exactly the words the hold exists to keep off the wire. THE VAD is tuned toward
// firing — a fixed RMS bar sat above much real post-AGC speech and barge-in never fired at all — so
// the threshold is a ratio over a rolling floor, never below MIN_RMS, quick to fall and slow to rise.

import type { PlaybackScheduler } from "@/lib/voice-playback";

export const GATE_TAIL_MS = 350;
export const GATED_FRAME_BUFFER = 3; // ~750 ms ring: replayed on barge-in so the utterance start isn't lost
export const VAD_WINDOW_MS = 50;
export const VAD_SUSTAIN_WINDOWS = 4; // 200 ms — a real "stop!" must land fast
export const VAD_MIN_RMS = 350;
export const VAD_FLOOR_RATIO = 3;
export const VAD_FLOOR_INITIAL = 120;
export const VAD_FLOOR_MIN = 50;

export class MicGate {
  private tailMs: number;
  private streaming = false;
  private playing = false;
  private held = false;
  private tailUntil = 0;

  constructor(tailMs: number = GATE_TAIL_MS) {
    this.tailMs = tailMs;
  }

  onAudioStart(hold: boolean = false): void {
    this.streaming = true;
    this.held = hold;
  }

  onAudioEnd(now: number): void {
    this.streaming = false;
    this.held = false;
    if (!this.playing) this.tailUntil = Math.max(this.tailUntil, now + this.tailMs);
  }

  onPlayback(playing: boolean, now: number): void {
    if (this.playing && !playing) this.tailUntil = Math.max(this.tailUntil, now + this.tailMs);
    this.playing = playing;
  }

  forceOpen(): void {
    this.streaming = false;
    this.playing = false;
    this.held = false;
    this.tailUntil = 0;
  }

  isOpen(now: number): boolean {
    return !this.streaming && !this.playing && now >= this.tailUntil;
  }

  get isHeld(): boolean {
    return this.held;
  }
}

export type VadDebug = {
  rms: number;
  floor: number;
  threshold: number;
  active: number;
  fires: number;
};

/** Fires only on SUSTAINED sound (N windows over an adaptive threshold), so a cough or a keyboard
 *  clack never trips it. */
export class SustainedSpeechDetector {
  private windowSamples: number;
  private minRms: number;
  private floorRatio: number;
  private sustainWindows: number;
  private floor = VAD_FLOOR_INITIAL;
  private sumSquares = 0;
  private count = 0;
  private activeWindows = 0;
  private lastRms = 0;
  private fires = 0;

  constructor(
    sampleRate: number,
    minRms: number = VAD_MIN_RMS,
    sustainWindows: number = VAD_SUSTAIN_WINDOWS,
    windowMs: number = VAD_WINDOW_MS,
    floorRatio: number = VAD_FLOOR_RATIO,
  ) {
    this.windowSamples = Math.max(1, Math.round((sampleRate * windowMs) / 1000));
    this.minRms = minRms;
    this.sustainWindows = sustainWindows;
    this.floorRatio = floorRatio;
  }

  get threshold(): number {
    return Math.max(this.minRms, this.floor * this.floorRatio);
  }

  feed(samples: Int16Array): boolean {
    let fired = false;
    for (let i = 0; i < samples.length; i++) {
      const s = samples[i];
      this.sumSquares += s * s;
      if (++this.count < this.windowSamples) continue;
      const rms = Math.sqrt(this.sumSquares / this.count);
      this.sumSquares = 0;
      this.count = 0;
      this.lastRms = rms;
      if (rms >= this.threshold) {
        this.activeWindows++;
      } else {
        this.activeWindows = 0;
        this.floor += (rms - this.floor) * (rms < this.floor ? 0.3 : 0.05);
        if (this.floor < VAD_FLOOR_MIN) this.floor = VAD_FLOOR_MIN;
      }
      if (this.activeWindows >= this.sustainWindows) {
        fired = true;
        this.fires++;
        this.activeWindows = 0;
      }
    }
    return fired;
  }

  debug(): VadDebug {
    return {
      rms: Math.round(this.lastRms),
      floor: Math.round(this.floor),
      threshold: Math.round(this.threshold),
      active: this.activeWindows,
      fires: this.fires,
    };
  }

  /** Clears an in-progress run; the learned noise floor is kept. */
  reset(): void {
    this.sumSquares = 0;
    this.count = 0;
    this.activeWindows = 0;
  }
}

/** What to do with the socket: `send` goes on the wire in order, and when `interrupt` is set the
 *  control frame that cuts the turn must precede it. */
export type MicUplinkAction = {
  send: ArrayBuffer[];
  interrupt: boolean;
};

export class MicUplink {
  private gate: MicGate;
  private vad: SustainedSpeechDetector | null = null;
  private ring: ArrayBuffer[] = [];
  private preroll: number;

  constructor(tailMs: number = GATE_TAIL_MS, preroll: number = GATED_FRAME_BUFFER) {
    this.gate = new MicGate(tailMs);
    this.preroll = preroll;
  }

  /** The server's `ready` frame is authoritative for the mic rate: until it lands nothing can barge in. */
  arm(sampleRate: number): void {
    this.vad = new SustainedSpeechDetector(sampleRate);
  }

  onAudioStart(hold: boolean = false): void {
    if (this.gate.isHeld && !hold) this.ring.length = 0;
    this.gate.onAudioStart(hold);
  }

  onAudioEnd(now: number): void {
    if (this.gate.isHeld) this.ring.length = 0;
    this.gate.onAudioEnd(now);
  }

  onPlayback(playing: boolean, now: number): void {
    this.gate.onPlayback(playing, now);
  }

  forceOpen(): void {
    if (this.gate.isHeld) this.ring.length = 0;
    this.gate.forceOpen();
  }

  clear(): void {
    this.ring.length = 0;
  }

  get held(): boolean {
    return this.gate.isHeld;
  }

  /** A HELD bracket is not her voice: it carries no frames, and the server closes it itself
   *  (audio_end / interrupted), which is what re-opens the gate. */
  feed(frame: ArrayBuffer, now: number): MicUplinkAction {
    const heard = this.vad ? this.vad.feed(new Int16Array(frame)) : false;
    if (this.gate.isOpen(now)) {
      this.ring.length = 0;
      return { send: [frame], interrupt: false };
    }
    this.ring.push(frame);
    if (this.ring.length > this.preroll) this.ring.shift();
    if (!heard) return { send: [], interrupt: false };
    if (this.gate.isHeld) {
      this.ring.length = 0;
      this.vad?.reset();
      return { send: [], interrupt: true };
    }
    const send = this.ring.slice();
    this.ring.length = 0;
    this.gate.forceOpen();
    this.vad?.reset();
    return { send, interrupt: true };
  }
}

export type ServerAudioFrame = { type: string; turn?: unknown; hold?: unknown };

/** Route one server audio-bracket frame to the mic gate and the playback scheduler. A held bracket goes
 *  straight to the gate and never through the scheduler — the scheduler would discard it as stale. */
export function applyAudioFrame(
  mic: MicUplink,
  sched: PlaybackScheduler,
  frame: ServerAudioFrame,
  now: number,
  dropPlayback: () => void,
): void {
  const turn = Number(frame.turn) || 0;
  if (frame.type === "audio_start") {
    if (frame.hold === true) {
      mic.onAudioStart(true);
      return;
    }
    const action = sched.onAudioStart(turn);
    if (action === "stale") return;
    if (action === "new-turn") dropPlayback(); // delivery outruns realtime — drop a superseded turn's leftovers
    mic.onAudioStart(false);
    return;
  }
  if (frame.type === "audio_end") {
    sched.onAudioEnd(turn);
    mic.onAudioEnd(now);
    return;
  }
  sched.flush(turn);
  dropPlayback();
  mic.forceOpen();
}
