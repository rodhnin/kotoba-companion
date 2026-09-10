/**
 * Access gate. Everything under /app needs a valid signed session cookie, else it bounces to
 * /login?next=<path>; with no KOTOBA_WEB_PASSWORD the gate is open. (Next 16 renamed the
 * "middleware" convention to "proxy"; same Edge behaviour.)
 *
 * The `?next=` it writes is read back through safeNext, never a "starts with / but not //" test: the
 * URL parser folds `/\` into `//` and strips raw TAB/LF/CR, so that test admits values resolving to a
 * foreign host. Assigning .pathname only happened to contain them — the funnel makes it a rule.
 */
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { GATE_COOKIE, gateEnabled, verifySession } from "@/lib/gate";
import { safeNext } from "@/lib/safe-next";

export async function proxy(req: NextRequest) {
  if (!gateEnabled()) return NextResponse.next(); // no password set → open (local dev)

  const cookie = req.cookies.get(GATE_COOKIE)?.value;
  const authed = await verifySession(cookie);
  const path = req.nextUrl.pathname;
  const onLogin = path === "/login";

  // Already authenticated and trying to view /login → send them in (don't show the gate again).
  if (onLogin) {
    if (!authed) return NextResponse.next();
    const dest = safeNext(req.nextUrl.searchParams.get("next"), req.nextUrl.origin);
    return NextResponse.redirect(new URL(dest, req.nextUrl.origin));
  }

  // Protected routes (/app): valid session passes; otherwise bounce to the gate.
  if (authed) return NextResponse.next();
  const url = req.nextUrl.clone();
  const next = path + req.nextUrl.search;
  url.pathname = "/login";
  url.search = `?next=${encodeURIComponent(next)}`;
  return NextResponse.redirect(url);
}

// Protect the environment + guard /login (so an authed user can't see the gate again). /gate and static
// assets stay public. /setup is protected for the same reason /app is — it writes keys and her name, and
// on a configured instance the gate is what /api/* checks anyway, so a setup screen outside it could
// only 401. "/" is unmatched on purpose: it renders nothing, it only redirects to /app, and the gate
// runs there — matching it would cost a second hop to say the same thing.
export const config = {
  matcher: ["/app/:path*", "/setup/:path*", "/login"],
};
