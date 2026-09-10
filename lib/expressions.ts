// Shared emotion contract. The Emotion union MUST equal the backend's VALID_EMOTIONS
// (api/src/kotoba/core/emotions.py) and the soul/default.md emotion list.
// isEmotion must test OWN keys only: `in` sees inherited prototype keys, so a frame carrying
// "constructor" passed the guard and reached the model as a function (random expression applied).

export type Emotion =
  | "neutral"
  | "happy"
  | "excited"
  | "sad"
  | "crying"
  | "angry"
  | "surprised"
  | "embarrassed"
  | "thinking"
  | "sleepy"
  | "affectionate"
  | "confused"
  | "scared"
  | "determined";

/** The fourteen names, and nothing about any model. Which expression each one plays lives in
 *  `lib/avatar-config.ts`, because that answer changes with the model and this list does not. */
export const EMOTIONS: readonly Emotion[] = [
  "neutral", "happy", "excited", "sad", "crying", "angry", "surprised",
  "embarrassed", "thinking", "sleepy", "affectionate", "confused", "scared", "determined",
] as const;

export function isEmotion(value: unknown): value is Emotion {
  return typeof value === "string" && (EMOTIONS as readonly string[]).includes(value);
}
