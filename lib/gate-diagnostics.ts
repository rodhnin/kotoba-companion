/**
 * Turning a silent login loop into a sentence. /gate answers 200 and sets the session cookie, and the
 * browser is free to drop it anyway: a `secure` cookie on a plain-http origin, cookies blocked for the
 * site, a proxy that eats Set-Cookie. The next request then arrives with no cookie, is bounced back to
 * /login, and the user faces the same form with no error. So the login screen records the instant the
 * password was accepted and checks that mark on mount: landing back here seconds after a success means
 * the cookie did not survive the round trip. The mark is read once and cleared, so an ordinary later
 * visit says nothing. Signing out is the one ordinary visit that arrives INSIDE the window, so
 * `signOut` drops the mark too — without it a sign-out read as a browser that eats cookies.
 */

export const GATE_OK_MARK = "kotoba_gate_ok_at";

const LOOP_WINDOW_MS = 60_000;

/** True when `mark` is a timestamp from the recent past — not the future, not an old session's. */
export function isLoopSignal(mark: string | null, now: number, windowMs = LOOP_WINDOW_MS): boolean {
  if (!mark) return false;
  const at = Number(mark);
  if (!Number.isFinite(at) || at <= 0) return false;
  const age = now - at;
  return age >= 0 && age < windowMs;
}

export function markGateAccepted(now: number = Date.now()): void {
  try {
    sessionStorage.setItem(GATE_OK_MARK, String(now));
  } catch {
    /* sessionStorage unavailable (SSR / privacy mode) — the diagnosis is a courtesy, not a dependency */
  }
}

/** Drop the mark without reading it — for an exit that is not a failed round trip. */
export function clearGateMark(): void {
  try {
    sessionStorage.removeItem(GATE_OK_MARK);
  } catch {
    /* no storage — nothing was marked either */
  }
}

/** Read the mark, clear it, and say whether we just came back from a login that should have worked. */
export function takeGateLoopSignal(now: number = Date.now()): boolean {
  try {
    const mark = sessionStorage.getItem(GATE_OK_MARK);
    if (mark !== null) sessionStorage.removeItem(GATE_OK_MARK);
    return isLoopSignal(mark, now);
  } catch {
    return false;
  }
}
