// Turn-scoped audio acceptance + scheduling for the LOCAL voice mode (lib/local-voice.ts). Pure
// logic, no DOM/audio imports — unit-tested with node --test (erasable-syntax TS only).

// Scheduling lead: absorbs main-thread jitter between chunks without audible latency.
export const PLAYBACK_LEAD_SECONDS = 0.06;

export type AudioStartAction = "stale" | "resume" | "new-turn";

/** Decides which TTS frames belong to the live turn and where on the AudioContext timeline they
 *  start. Frames are accepted between audio_start and audio_end/flush OF THE SAME TURN — events of
 *  older (superseded) turns can neither re-arm nor disarm the live one. */
export class PlaybackScheduler {
  private accept = false;
  private turn = 0;
  private deadThrough = 0;
  private playhead = 0;
  private gen = 0;
  private lead: number;

  constructor(lead: number = PLAYBACK_LEAD_SECONDS) {
    this.lead = lead;
  }

  onAudioStart(turn: number): AudioStartAction {
    if (turn < this.turn || turn <= this.deadThrough) return "stale";
    const action: AudioStartAction = turn > this.turn ? "new-turn" : "resume";
    if (action === "new-turn") {
      this.turn = turn;
      this.playhead = 0;
    }
    this.accept = true;
    this.gen++;
    return action;
  }

  onAudioEnd(turn: number): void {
    if (turn >= this.turn) this.accept = false;
  }

  /** Interrupt/teardown: stop accepting, and never re-arm for the killed turn — its segments can
   *  still race in (opened server-side between the interrupt and the cancel landing). */
  flush(killedTurn?: number): void {
    this.accept = false;
    this.playhead = 0;
    this.deadThrough = Math.max(this.deadThrough, this.turn, killedTurn ?? 0);
  }

  get accepting(): boolean {
    return this.accept;
  }

  get currentTurn(): number {
    return this.turn;
  }

  get playheadTime(): number {
    return this.playhead;
  }

  /** Counts accepted audio_start frames. Each one begins a FRESH TTS byte stream, so byte-level
   *  state (the s16 split-byte carry) must not survive a generation change. */
  get streamGeneration(): number {
    return this.gen;
  }

  /** null → drop the frame. Otherwise the absolute ctx time to start it at — clamped to
   *  now + lead, so a chunk arriving after any idle gap can never be scheduled in the past. */
  schedule(durationSecs: number, ctxNow: number): number | null {
    if (!this.accept) return null;
    const at = Math.max(this.playhead, ctxNow + this.lead);
    this.playhead = at + durationSecs;
    return at;
  }
}

/** s16le byte alignment across arbitrarily-split chunks: a split byte is carried into the next
 *  chunk of the SAME stream generation; a new generation drops it, so a stream truncated at an odd
 *  byte can never byte-shift (pure static) the segment that follows it. */
export class PcmChunkAligner {
  private carry: Uint8Array | null = null;
  private gen = -1;

  align(bytes: Uint8Array, generation: number): Uint8Array {
    if (generation !== this.gen) {
      this.gen = generation;
      this.carry = null;
    }
    if (this.carry) {
      const joined = new Uint8Array(this.carry.length + bytes.length);
      joined.set(this.carry, 0);
      joined.set(bytes, this.carry.length);
      bytes = joined;
      this.carry = null;
    }
    if (bytes.length & 1) {
      this.carry = bytes.slice(bytes.length - 1);
      bytes = bytes.subarray(0, bytes.length - 1);
    }
    return bytes;
  }

  reset(): void {
    this.carry = null;
  }
}
