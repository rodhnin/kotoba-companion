/**
 * Which first-run steps the strip lets you click.
 *
 * Two different screens share one flow. A FIRST RUN walks forward through questions that have no
 * answer yet, so only the ones already behind you can be reopened. RECONFIGURING is the opposite: every
 * answer exists, you came in to change exactly one, and walking the whole wizard again to reach it is
 * the reason people stop using the button at all.
 */
export function stepReachable(index: number, current: number, reconfiguring: boolean): boolean {
  if (index === current) return false;
  return reconfiguring || index < current;
}

/** "Back" and "Go to" are not the same promise: one returns to an answer, the other opens a new one. */
export function stepAction(index: number, current: number): "back" | "forward" {
  return index < current ? "back" : "forward";
}

/** Stands in for a key that exists. The backend describes both keys as present and never sends one, so
 *  the recap reads these for truthiness alone and no screen ever renders the word. */
export const HELD = "saved";

export type KeyState = { saved?: boolean; env?: string };

/**
 * Whether the BACKEND holds a model key for this provider — which is a different question from what
 * this visit has typed.
 *
 * Per provider, and it lives here rather than in the screen so a test can actually run it. Read off a
 * single `answers.apiKey` flag it was one boolean for two providers: switching from the one with a key
 * to the one without offered to KEEP a key that provider never had, and walked the person past the one
 * step that would have fixed their install. An environment key counts: it is already answering.
 */
export function heldFor(keys: Record<string, KeyState> | null | undefined, provider: string): boolean {
  const k = keys && typeof keys === "object" ? (keys as Record<string, KeyState>)[provider] : undefined;
  if (!k || typeof k !== "object") return false;
  return k.saved === true || (typeof k.env === "string" && k.env !== "");
}

/**
 * What the recap may say about the two secrets. Both rows read the SAME source the steps read, because
 * read off the answers they contradicted it: skipping the voice step printed "not yet" over a key the
 * backend held, and switching provider printed "locked away safe" over one it did not.
 */
export function secretRows(keys: Record<string, KeyState> | null | undefined,
                           provider: string | null, voiceHeld: boolean): { key: boolean; voice: boolean } {
  return { key: heldFor(keys, provider || "openai"), voice: voiceHeld };
}

/**
 * The key map after a key step closes. `kept` is the empty field left over a key the backend already
 * holds: nothing was sent, so nothing about WHERE that key lives changed. Writing `saved` there claimed
 * the app was holding a key the environment supplies — the one word the recap uses to tell them apart.
 */
export function afterKey(keys: Record<string, KeyState> | null | undefined,
                         provider: string, kept: boolean): Record<string, KeyState> {
  const m = keys && typeof keys === "object" ? { ...(keys as Record<string, KeyState>) } : {};
  const was = m[provider];
  const env = was && typeof was.env === "string" ? was.env : "";
  m[provider] = { saved: kept ? was?.saved === true : true, env };
  return m;
}

export type Settled = {
  provider?: "openai" | "xai";
  model?: string;
  apiKey?: string;
  elevenKey?: string;
  userName?: string;
  companionName?: string;
  language?: string;
};

/**
 * The answers this install already holds, read off `/api/setup/status`.
 *
 * Only for a reconfigure. On a first run every one of these fields already carries a default nobody
 * chose, and seeding them would let the recap claim a brain, a model and a language before a single
 * question was answered. Reconfiguring is the opposite case: somebody changing one answer was shown
 * seven dashes for the ones they were keeping, which reads as an install about to be wiped.
 */
export function settled(raw: unknown, labelFor: (code: string) => string): Settled {
  const s = (raw ?? {}) as Record<string, unknown>;
  const text = (k: string) => (typeof s[k] === "string" ? (s[k] as string).trim() : "");
  const out: Settled = {};
  const provider = text("provider");
  if (provider === "openai" || provider === "xai") out.provider = provider;
  if (text("model")) out.model = text("model");
  if (s.needed === false) out.apiKey = HELD;
  if (s.has_voice_key === true) out.elevenKey = HELD;
  if (text("user_name")) out.userName = text("user_name");
  if (text("companion_name")) out.companionName = text("companion_name");
  const code = text("language");
  if (code) out.language = labelFor(code) || code;
  return out;
}
