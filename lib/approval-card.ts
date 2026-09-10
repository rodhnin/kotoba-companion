export const LABEL_BUDGET = 120;

export type GrantKind = "yes" | "no" | "exact" | "family";

export type Answer = { approved: boolean; always: boolean; always_exact: boolean };

export type GrantFlags = { canAlways?: unknown; canAlwaysExact?: unknown; family?: unknown };

export type DeliveryFlags = { inputKind?: unknown; wait?: unknown; name?: unknown };

export type Delivery =
  | { kind: "post"; body: Record<string, unknown> }
  | { kind: "speak" };

export function answerFor(kind: GrantKind): Answer {
  switch (kind) {
    case "yes":
      return { approved: true, always: false, always_exact: false };
    case "no":
      return { approved: false, always: false, always_exact: false };
    case "exact":
      return { approved: true, always: false, always_exact: true };
    case "family":
      return { approved: true, always: true, always_exact: false };
  }
}

export function grantsOffered(r: GrantFlags): GrantKind[] {
  const out: GrantKind[] = ["yes", "no"];
  if (r.canAlwaysExact === true) out.push("exact");
  if (r.canAlways === true && typeof r.family === "string" && r.family !== "") out.push("family");
  return out;
}

export function headlineOf(label: string, budget: number = LABEL_BUDGET): { headline: string; hasMore: boolean } {
  const first = label.split("\n").find((l) => l.trim()) ?? "";
  const headline = first.length > budget ? `${first.slice(0, budget)}…` : first;
  return { headline, hasMore: label.trim() !== headline.trim() };
}

export function deliveryFor(r: DeliveryFlags, value: string): Delivery {
  const isKey = r.inputKind === "key";
  const isSecret = r.inputKind === "secret";
  if (!isKey && !isSecret && !r.wait) return { kind: "speak" };
  const kind = typeof r.inputKind === "string" && r.inputKind ? r.inputKind : "text";
  const body: Record<string, unknown> = { kind, value };
  if (isKey && typeof r.name === "string" && r.name) body.name = r.name;
  return { kind: "post", body };
}

export function addressed(body: Record<string, unknown>, requestId: unknown): Record<string, unknown> {
  return typeof requestId === "string" && requestId ? { ...body, request_id: requestId } : body;
}
