// Backend auth: the gate password doubles as the FastAPI token (required on /api/* once
// KOTOBA_WEB_PASSWORD is set). apiFetch() sends a Bearer header; tokenUrl() appends ?token= where headers
// can't be set (EventSource, <img>, downloads, window.open). The token never ships in the bundle: it's the
// typed password (sessionStorage), re-fetched on refresh from /gate/token behind the gate cookie.
"use client";

// Relative + extension so plain node can load this module the way lib/__tests__ does.
import { clearGateMark } from "./gate-diagnostics.ts";

const KEY = "kotoba_token";
let _token = "";

export function setAuthToken(t: string): void {
  _token = t || "";
  try {
    // An empty value REMOVES the entry — else authToken() reads the old token back and sign-out signs out nothing.
    if (_token) sessionStorage.setItem(KEY, _token);
    else sessionStorage.removeItem(KEY);
  } catch {
    /* sessionStorage unavailable (SSR / privacy mode) — in-memory only */
  }
}

export function authToken(): string {
  if (_token) return _token;
  try {
    _token = sessionStorage.getItem(KEY) || "";
  } catch {
  }
  return _token;
}

export function apiFetch(url: string, opts: RequestInit = {}): Promise<Response> {
  const t = authToken();
  const headers = new Headers(opts.headers || {});
  if (t && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${t}`);
  return fetch(url, { ...opts, headers });
}

// The ws:// base of the backend, because WebSockets don't survive Next rewrites and must dial it
// directly. next.config.ts derives it in same-origin proxy mode and leaves it EMPTY in the packaged
// build, where the socket dials the page's own origin.
const VOICE_WS_BASE = process.env.NEXT_PUBLIC_VOICE_WS_URL || "";

export function tokenUrl(url: string): string {
  if (VOICE_WS_BASE && url.startsWith("/api/voice/")) url = VOICE_WS_BASE + url;
  const t = authToken();
  if (!t) return url;
  return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(t);
}

/** True when a gate password is configured; empty means the gate is open, so there is nothing to end. */
export function gateIsOn(): boolean {
  return authToken() !== "";
}

/** Drop the local token AND the login-loop mark beside it — a sign-out lands on /login inside the very
 *  window that mark exists to flag — then clear the server's signed cookie. */
export async function signOut(): Promise<void> {
  setAuthToken("");
  clearGateMark();
  try {
    await fetch("/gate", { method: "DELETE" });
  } catch {
    /* offline → the local token is already gone and the cookie expires on its own */
  }
}

export async function ensureAuthToken(): Promise<string> {
  if (authToken()) return authToken();
  try {
    const r = await fetch("/gate/token");
    if (r.ok) {
      const d = await r.json();
      if (d?.token) setAuthToken(String(d.token));
    }
  } catch {
    /* offline / gate route missing → leave empty (gate likely open) */
  }
  return authToken();
}
