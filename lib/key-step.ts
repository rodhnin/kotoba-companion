/**
 * The key step's decisions, taken out of the screen that draws them.
 *
 * Three of them only ever went wrong because they lived inside a component: which provider a finished
 * check belongs to, whether an empty field means "keep it" or "stop", and what a refusal is allowed to
 * send. Read off a live ref after the await, the first filed a verified key under whichever provider
 * the person had switched to while it was in flight. Here the provider travels INSIDE the attempt, so
 * nothing that happens on screen afterwards can change the answer.
 */
export type Attempt =
  | { kind: "check"; provider: string; key: string }
  | { kind: "keep"; provider: string }
  | { kind: "nothing" };

/** What pressing the button means, decided before anything is sent. */
export function attempt(provider: string, typed: string, held: boolean): Attempt {
  const key = (typed || "").trim();
  if (key) return { kind: "check", provider: provider || "openai", key };
  return held ? { kind: "keep", provider: provider || "openai" } : { kind: "nothing" };
}

export type Write = { path: string; body: Record<string, unknown> };

/**
 * Everything this attempt may send, in order, and nothing else. `keep` and `nothing` send NOTHING:
 * there is nothing to verify and nothing to overwrite. A write of an empty key is not a way of
 * reporting a refusal — it deletes the key the person still owns.
 */
export function writesFor(a: Attempt): Write[] {
  if (a.kind !== "check") return [];
  return [
    { path: "/api/setup/provider", body: { provider: a.provider } },
    { path: "/api/settings/llm-key", body: { provider: a.provider, key: a.key } },
  ];
}

export type Settled =
  | { kind: "accepted"; provider: string; kept: boolean }
  | { kind: "refused"; provider: string; reason: string }
  | null;

/** The verdict, filed under the provider the ATTEMPT carried rather than the one on screen now. */
export function settle(a: Attempt, ok: boolean, reason = ""): Settled {
  if (a.kind === "nothing") return null;
  if (a.kind === "keep") return { kind: "accepted", provider: a.provider, kept: true };
  return ok
    ? { kind: "accepted", provider: a.provider, kept: false }
    : { kind: "refused", provider: a.provider, reason };
}
