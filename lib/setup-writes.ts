/**
 * What a screen is allowed to CLAIM after it has written a setting. Both callers had the same hole:
 * they posted an answer, threw the response away and printed the value they had sent. On the Ready
 * recap that meant a card reading `Model: gpt-5.4` while the backend had kept the old one — and the
 * key check that follows validates against whatever IS configured, so nothing ever contradicted it.
 * Two rules, and they are the whole module. A refusal moved nothing, so the value from BEFORE the
 * click still stands. A success is answered in the backend's own words, not the caller's:
 * `/api/setup/provider` pins a model along with the brain and `/api/setup/model` returns what
 * `llm.model_name` now reads, so what comes back is not always what was clicked.
 */

export type Sent = {
  ok: boolean;
  /** The parsed body of a SUCCESSFUL write. A refusal carries its reason in `detail` instead. */
  data: Record<string, unknown> | null;
  detail: string;
};

/** The reason a write was refused, as the backend gave it. FastAPI answers `{detail}` — a string for
 *  our own `HTTPException`s, a list of objects when a body fails validation. Anything else (an HTML
 *  error page, a socket that died mid-answer) has no reason to offer and comes back empty. */
export function refusal(body: unknown): string {
  const d = (body as { detail?: unknown } | null | undefined)?.detail;
  if (typeof d === "string") return d.trim();
  if (Array.isArray(d)) {
    const first = d.find((e) => typeof (e as { msg?: unknown })?.msg === "string");
    if (first) return String((first as { msg: string }).msg).trim();
  }
  return "";
}

/** The value the screen may print for FIELD after a write. */
export function settle(sent: Sent, field: string, clicked: string, before: string): string {
  if (!sent.ok) return before;
  const v = sent.data?.[field];
  return typeof v === "string" && v ? v : clicked;
}

/** What to show when a write is refused. The backend's own reason when it gave one — "OpenAI does not
 *  serve grok-4.6 — xAI (Grok) does" is the whole answer — and the general line when it did not, because
 *  a failure with no face at all is what this module exists to stop. Finished as a sentence, since the
 *  reason is written to sit in front of copy the screen supplies. */
export function refusalText(sent: Sent, fallback: string): string {
  const text = (sent.detail || fallback).trim();
  return !text || /[.!?…]$/.test(text) ? text : `${text}.`;
}
