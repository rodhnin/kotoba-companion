// Dev access gate — an HMAC-signed session cookie, built on Web Crypto (NOT node:crypto) so it verifies
// in BOTH the Edge middleware and the Node route handler. The password lives only server-side; the
// browser POSTs an attempt and gets an httpOnly signed cookie, re-verified on every protected request.
// This is dev/staging protection, not a user auth system.
//
// Two rules, each stated again at its own line below: the two env names resolve in the BACKEND's order
// (diverging lets a login succeed with one value while every /api/* call 401s with the other), and the
// cookie's `secure` flag follows the CONNECTION and never the build mode — `npm start` on
// http://<lan-ip>:3000 is NODE_ENV=production, so keying off that marks the cookie secure, the browser
// drops it on an insecure origin without a word, and the login loops with no error to read.

const COOKIE_NAME = "kotoba_gate";
const TTL_SECONDS = 12 * 60 * 60;

const enc = new TextEncoder();

function b64url(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function hmac(secret: string, data: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(data));
  return b64url(new Uint8Array(sig));
}

/** Constant-time string compare (both inputs are fixed-length HMAC outputs → no length leak). */
function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function gateSecret(): string {
  return process.env.KOTOBA_GATE_SECRET || gatePassword();
}

export function gatePassword(): string {
  // The backend's _web_password() order, ON PURPOSE: diverging lets a login succeed with one value
  // while every /api/* call 401s with the other.
  return process.env.KOTOBA_WEB_PASSWORD || process.env.KOTOBA_GATE_PASSWORD || "";
}

/** True when a gate password is configured; if not, the gate is OPEN (no lockout in local dev). */
export function gateEnabled(): boolean {
  return gatePassword().length > 0;
}

export const GATE_COOKIE = COOKIE_NAME;

/** Compare an attempt to the real password in constant time (HMAC both, then compare the digests). */
export async function passwordMatches(attempt: string): Promise<boolean> {
  const real = gatePassword();
  if (!real) return true; // no password set → gate open
  const secret = gateSecret();
  const [a, b] = await Promise.all([hmac(secret, attempt || ""), hmac(secret, real)]);
  return constantTimeEqual(a, b);
}

export async function signSession(): Promise<string> {
  const payload = b64url(enc.encode(JSON.stringify({ iat: Math.floor(Date.now() / 1000) })));
  const sig = await hmac(gateSecret(), payload);
  return `${payload}.${sig}`;
}

/** Was the request the BROWSER made carried over TLS? Deliberately lopsided: any hop claiming https
 *  wins, and only a URL that positively says `http:` turns the flag off. */
export function requestIsSecure(req: Request): boolean {
  for (const name of ["x-forwarded-proto", "x-forwarded-scheme"]) {
    const raw = req.headers.get(name);
    if (raw && raw.split(",").some((v) => v.trim().toLowerCase() === "https")) return true;
  }
  try {
    return new URL(req.url).protocol !== "http:";
  } catch {
    return true; // unreadable → assume TLS: a needlessly-secure cookie loops, an open one leaks
  }
}

/** Session-cookie attributes. Only `secure` varies; the rest is fixed policy. */
export function gateCookieOptions(req: Request) {
  return {
    httpOnly: true,
    secure: requestIsSecure(req),
    sameSite: "lax" as const,
    path: "/",
    maxAge: TTL_SECONDS,
  };
}

export async function verifySession(value: string | undefined): Promise<boolean> {
  if (!value) return false;
  const dot = value.lastIndexOf(".");
  if (dot <= 0) return false;
  const payload = value.slice(0, dot);
  const sig = value.slice(dot + 1);
  const expected = await hmac(gateSecret(), payload);
  if (!constantTimeEqual(sig, expected)) return false;
  try {
    const json = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
    const iat = Number(json.iat) || 0;
    return iat + TTL_SECONDS >= Math.floor(Date.now() / 1000);
  } catch {
    return false;
  }
}
