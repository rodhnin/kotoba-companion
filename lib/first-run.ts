/**
 * First-run detection for the browser. `GET /api/setup/status` answers with `core.first_run.needed`,
 * the same predicate `kotoba setup` runs in a terminal, so a CLI install and a browser install can
 * never disagree about whether somebody has been set up. It fails OPEN in three directions — a
 * refusal, a thrown fetch and a backend that simply never answers all mean "not needed". That last
 * one is the reason for the deadline: /app waits on this before it mounts anything, and a fetch with
 * no timeout against a stopped backend would leave the app blank indefinitely. Being wrong this way
 * costs one visit to an app that reports its own trouble; being wrong the other way traps a configured
 * user in a setup screen they do not need. The fetch is injected so the caller picks origin and auth.
 */
const DEADLINE_MS = 2000;

/**
 * The invitation the Settings button carries. On a configured install /setup redirects to /app unless
 * the URL says the visit was deliberate, so first run can only be walked into on purpose.
 *
 * A query parameter rather than a one-shot sessionStorage handshake, and the difference is a reload: a
 * handshake is consumed on the first mount, so refreshing mid-reconfiguration would eject the person
 * back to /app — the one visitor this door exists for. The flag is guessable, which is honest: it stops
 * an ACCIDENT (a bookmark, browser history, an old link) rather than a person, and /setup already sits
 * behind the same password gate as /app while every answer it writes is writable from Settings anyway.
 */
export const SETUP_INVITE = "reconfigure";
export const SETUP_INVITE_URL = `/setup?${SETUP_INVITE}=1`;

/**
 * An invitation that names ONE step. A terminal install cannot fetch a Live2D model — `kotoba setup`
 * has no browser to draw the licence in — so the app is where that hole is closed, and walking the
 * other eight questions again to reach it is the reason nobody did.
 */
export const SETUP_STEP = "step";
export const SETUP_FACE_URL = `/setup?${SETUP_INVITE}=1&${SETUP_STEP}=face`;

/**
 * Whether this browser has already been sent to fetch a face. Offered ONCE, not once per tab: session
 * storage is per-tab, so declining in one and opening another was the same nag again, from a screen
 * whose way out is the screen you came from. The undo is a direct door in Settings — offering once is
 * only fair while getting back takes one click, and a face is optional anyway.
 */
const SENT_FOR_A_FACE = "kotoba.sent-for-a-face";

let sentThisLoad = false;

export function alreadySentForAFace(): boolean {
  if (sentThisLoad) return true;
  try {
    return localStorage.getItem(SENT_FOR_A_FACE) === "1";
  } catch {
    return true;   // no storage is no memory, and a loop is worse than no redirect
  }
}

export function rememberSentForAFace(): void {
  // Remembered in the page FIRST. Only reading was guarded, so a storage that reads and refuses to
  // write — a full origin, some private modes — never latched, and /app sent the same person to fetch
  // a face on every single visit: the loop the guard above exists to prevent, by the other door.
  sentThisLoad = true;
  try {
    localStorage.setItem(SENT_FOR_A_FACE, "1");
  } catch {
    /* private mode, blocked storage — the flag above already answered for this load */
  }
}

/** The step a deliberate visit asks to land on, or "" for the beginning. */
export function invitedStep(search: string): string {
  try {
    return new URLSearchParams(search).get(SETUP_STEP) || "";
  } catch {
    return "";
  }
}

/** Whether a location's query string carries the invitation. */
export function invited(search: string): boolean {
  try {
    return new URLSearchParams(search).get(SETUP_INVITE) === "1";
  } catch {
    return false;
  }
}

async function ask(get: (path: string) => Promise<Response>): Promise<boolean> {
  try {
    const r = await get("/api/setup/status");
    if (!r.ok) return false;
    const d = await r.json();
    return d?.needed === true;
  } catch {
    return false;
  }
}

/** Race a promise against a deadline, answering FALSE when the deadline wins. Both checks that stand
 *  between a visitor and /app use it: an answer that never comes must not hold the screen that would
 *  report the trouble. */
export async function beforeTheDeadline(
  work: Promise<boolean>,
  deadlineMs: number = DEADLINE_MS,
): Promise<boolean> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const tooLate = new Promise<boolean>((resolve) => {
    timer = setTimeout(() => resolve(false), deadlineMs);
  });
  try {
    return await Promise.race([work, tooLate]);
  } finally {
    clearTimeout(timer);
  }
}

export async function firstRunNeeded(
  get: (path: string) => Promise<Response>,
  deadlineMs: number = DEADLINE_MS,
): Promise<boolean> {
  return beforeTheDeadline(ask(get), deadlineMs);
}
