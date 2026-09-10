/**
 * The ticker that holds an ElevenLabs agent call open while background work runs: `sendUserActivity()`
 * resets EL's inactivity clock without making the agent speak. `start_work` decouples — the turn ends
 * and the work runs out of band — so the call goes silent and that timeout would kill the call the
 * `work_done` announcement was going to land on. EL has TWO timeouts and this answers only one: it
 * does NOT reset the hard cap on the whole call's duration, so the keepalive stops itself at a
 * wall-clock cap below that one, with headroom left to announce. The guard sits at `start` and not
 * inside the tick, so `voice_mode=local` never creates a timer at all; and the mode it reads is the
 * LATCHED one, never the live Settings prop, so a mid-call flip cannot move the running call.
 */

export type KeepaliveMode = "agent" | "local";

export const WORK_KEEPALIVE_TICK_MS = 8000;
// EL's max_duration_seconds is a HARD cap that sendUserActivity does NOT reset — see the module note.
export const WORK_KEEPALIVE_MAX_SECONDS = 1700;
export const WORK_KEEPALIVE_MAX_TICKS = Math.floor(
  (WORK_KEEPALIVE_MAX_SECONDS * 1000) / WORK_KEEPALIVE_TICK_MS,
);

/** Is there an ElevenLabs call under this transport for a keepalive to hold open? */
export function keepaliveHoldsACall(mode: KeepaliveMode): boolean {
  return mode !== "local";
}

type Timers = {
  set: (fn: () => void, ms: number) => ReturnType<typeof setInterval>;
  clear: (handle: ReturnType<typeof setInterval>) => void;
};

const REAL_TIMERS: Timers = {
  set: (fn, ms) => setInterval(fn, ms),
  clear: (handle) => clearInterval(handle),
};

export class WorkKeepalive {
  private handle: ReturnType<typeof setInterval> | null = null;
  private ticks = 0;
  private readonly ping: () => void;
  private readonly timers: Timers;

  // Fields assigned in the body, not parameter properties: node's strip-only loader rejects those,
  // and the tests here run the real .ts module through it.
  constructor(ping: () => void, timers: Timers = REAL_TIMERS) {
    this.ping = ping;
    this.timers = timers;
  }

  /** Running only means a timer exists — on the local transport `start` never creates one. */
  get running(): boolean {
    return this.handle !== null;
  }

  start(mode: KeepaliveMode): void {
    if (!keepaliveHoldsACall(mode)) return;
    if (this.handle !== null) return;
    this.ticks = 0;
    this.handle = this.timers.set(() => {
      if (++this.ticks > WORK_KEEPALIVE_MAX_TICKS) {
        this.stop();
        return;
      }
      try {
        this.ping();
      } catch {
        // A dead SDK connection is not this ticker's problem; the status effect tears the call down.
      }
    }, WORK_KEEPALIVE_TICK_MS);
  }

  stop(): void {
    if (this.handle !== null) {
      this.timers.clear(this.handle);
      this.handle = null;
    }
  }
}
