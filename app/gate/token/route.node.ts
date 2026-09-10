// GET /gate/token — hand the backend token to a browser that ALREADY passed the gate. The token is the
// gate password; it lives only server-side (KOTOBA_WEB_PASSWORD) and is returned ONLY when the request
// carries a valid signed gate-session cookie. This is how a refresh / new tab (where sessionStorage is
// empty) re-acquires the token to call the FastAPI backend, without ever putting the password in the
// public bundle. When no gate password is configured (local dev) it returns an empty token → the backend
// gate is also open, so everything still works.
import { NextResponse } from "next/server";
import { cookies } from "next/headers";

import { GATE_COOKIE, gateEnabled, gatePassword, verifySession } from "@/lib/gate";

export const runtime = "nodejs";

export async function GET() {
  if (!gateEnabled()) return NextResponse.json({ token: "" }); // gate open (dev) → backend also open
  const cookie = (await cookies()).get(GATE_COOKIE)?.value;
  if (!(await verifySession(cookie))) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  // no-store explicitly: the body IS the password, and this response passes through whatever proxy or
  // tunnel fronts the app. Never let it be cached anywhere.
  return NextResponse.json({ token: gatePassword() }, { headers: { "Cache-Control": "no-store, private" } });
}
