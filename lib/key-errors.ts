/**
 * Why a key was refused, from the words the backend already sent back.
 *
 * The screen threw them away and blamed the paste for everything: no credit behind a perfectly good
 * key, a proxy that ate the request, a keystore that could not write — all three were answered with
 * "maybe a letter got lost when you copied it". The terminal has said the difference for a long time.
 */
export type KeyFailure = "paste" | "quota" | "network" | "install" | "model";

/** The reason a write carries, whether it arrived as an HTTP refusal or inside a 200 that said no. */
export function reasonOf(sent: { data?: Record<string, unknown> | null; detail?: string } | null): string {
  const body = sent?.data;
  const inBody = body && typeof body.detail === "string" ? body.detail : "";
  return (inBody || sent?.detail || "").trim();
}

export function classifyKeyFailure(detail: string): KeyFailure {
  const d = (detail || "").toLowerCase();
  if (/insufficient_quota|exceeded your current quota|billing|payment|credit/.test(d)) return "quota";
  if (/master key|keystore|cannot store|encrypt/.test(d)) return "install";
  if (/connection error|apiconnectionerror|timed out|timeout|getaddrinfo|proxy|ssl|network|unreachable/.test(d))
    return "network";
  if (/model_not_found|notfounderror|does not exist|unknown model|no such model/.test(d)) return "model";
  return "paste";
}
