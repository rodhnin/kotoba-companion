// Synthesized UI sounds (WebAudio, no assets). Created lazily on first use to satisfy autoplay policies.
"use client";

let ctx: AudioContext | null = null;
let unlockArmed = false;

/** One-time gesture listener: the AudioContext must be created/resumed by a REAL user gesture. */
function armUnlock() {
  if (unlockArmed || typeof window === "undefined") return;
  unlockArmed = true;
  const unlock = () => {
    const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!ctx) ctx = new AC();
    if (ctx.state === "suspended") void ctx.resume();
    window.removeEventListener("pointerdown", unlock);
    window.removeEventListener("keydown", unlock);
  };
  window.addEventListener("pointerdown", unlock);
  window.addEventListener("keydown", unlock);
}
if (typeof window !== "undefined") armUnlock();

function ac(): AudioContext | null {
  if (typeof window === "undefined") return null;
  if (!ctx) {
    const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    ctx = new AC();
  }
  if (ctx.state === "suspended") void ctx.resume();
  // Only emit sound once the context is actually running — avoids the autoplay-policy warning spam.
  return ctx.state === "running" ? ctx : null;
}

function note(freq: number, start: number, dur: number, gain = 0.18, type: OscillatorType = "sine") {
  const c = ac();
  if (!c) return;
  const osc = c.createOscillator();
  const g = c.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  osc.connect(g);
  g.connect(c.destination);
  const t = c.currentTime + start;
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(gain, t + 0.02);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  osc.start(t);
  osc.stop(t + dur + 0.05);
}

export function playClick() {
  note(660, 0, 0.08, 0.08, "triangle");
}

/** Played when a call connects. */
export function playWakeChime() {
  note(523.25, 0, 0.18, 0.16); // C5
  note(659.25, 0.1, 0.18, 0.16); // E5
  note(783.99, 0.2, 0.32, 0.18); // G5
}

export function playBlip(fromUser = false) {
  note(fromUser ? 480 : 720, 0, 0.12, 0.07, "sine");
}

export function playSwoosh(open = true) {
  if (open) {
    note(520, 0, 0.12, 0.06);
    note(700, 0.06, 0.14, 0.06);
  } else {
    note(700, 0, 0.12, 0.06);
    note(520, 0.06, 0.14, 0.06);
  }
}

export function startRingtone(): () => void {
  const c = ac();
  if (!c) return () => {};
  let stopped = false;
  const ringOnce = (at: number) => {
    note(880, at, 0.4, 0.14, "sine");
    note(1108.73, at, 0.4, 0.1, "sine");
    note(880, at + 0.5, 0.4, 0.14, "sine");
    note(1108.73, at + 0.5, 0.4, 0.1, "sine");
  };
  let timer: ReturnType<typeof setTimeout> | undefined;
  const schedule = () => {
    if (stopped) return;
    ringOnce(0.02);
    timer = setTimeout(schedule, 1600);
  };
  schedule();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
  };
}
