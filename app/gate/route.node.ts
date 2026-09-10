/**
 * POST /gate — verify the dev-access password and set the signed session cookie. DELETE clears it.
 * Lives at /gate and NOT /api/gate: `/api/*` is the backend's namespace. This is pure
 * frontend — the password never leaves the server; only the attempt is posted here. The cookie's
 * attributes come from `gateCookieOptions(req)`, which reads the REQUEST's scheme rather than the
 * build mode: that distinction is the difference between a working self-hosted install on plain HTTP
 * and a login that loops forever.
 */
import { NextResponse } from "next/server";

import { GATE_COOKIE, gateCookieOptions, gateEnabled, passwordMatches, signSession } from "@/lib/gate";

export const runtime = "nodejs";

export async function POST(req: Request) {
  let attempt = "";
  try {
    const body = await req.json();
    attempt = typeof body?.password === "string" ? body.password : "";
  } catch {
    return NextResponse.json({ ok: false, error: "bad request" }, { status: 400 });
  }

  // No password configured → gate is open (local dev). Sign NOTHING: the signing key derives from the
  // password, and Web Crypto rejects a zero-length HMAC key, so signSession() here threw a 500. No cookie
  // is needed either — proxy.ts and /gate/token both return early while the gate is open.
  if (!gateEnabled()) return NextResponse.json({ ok: true, open: true });

  if (!(await passwordMatches(attempt))) {
    await new Promise((r) => setTimeout(r, 600)); // constant friction against guessing
    return NextResponse.json({ ok: false, error: "wrong" }, { status: 401 });
  }

  const res = NextResponse.json({ ok: true });
  res.cookies.set(GATE_COOKIE, await signSession(), gateCookieOptions(req));
  return res;
}

export async function DELETE(req: Request) {
  const res = NextResponse.json({ ok: true });
  res.cookies.set(GATE_COOKIE, "", { ...gateCookieOptions(req), maxAge: 0 });
  return res;
}
