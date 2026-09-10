/**
 * Where the gate is allowed to send you afterwards. `/login?next=…` is attacker-controlled, and the
 * obvious guard — "starts with a slash, but not two" — does not hold the value on this origin: the
 * URL parser folds `/\` into `//` for http(s) and strips raw TAB, LF and CR before it parses anything,
 * so `/\evil.com` and `?next=%2F%09%2Fevil.com` both arrive as a leading slash and resolve to a
 * foreign host. Only the parser can see that, so resolve the candidate against the origin it must not
 * leave, keep it only if it stayed, and return the re-serialised path. The fallback is always `/app`:
 * this runs on the success path of a login, where refusing to navigate at all would strand someone
 * who just proved they belong here.
 */

const HOME = "/app";

export function safeNext(next: string | undefined | null, origin = "http://gate.invalid"): string {
  if (!next || !next.startsWith("/")) return HOME;
  try {
    const url = new URL(next, origin);
    if (url.origin !== origin) return HOME;
    return url.pathname + url.search;
  } catch {
    return HOME;
  }
}
