/**
 * Text that somebody else wrote, made safe to DRAW — never safe to trust. This is the web half of the
 * backend's own text-security policy and must stay a MIRROR of it: same classes, same fates, same
 * exceptions. Change the policy THERE first, then copy it here. React escapes HTML, so what is left
 * to defend against is what React passes through intact: a reading-order override, an invisible
 * break, and the line separators a browser honours. The line is between the OVERRIDE and the SCRIPT,
 * which is why the RTL scripts themselves, ZWNJ/ZWJ, variation selectors and combining marks are
 * deliberately left alone — stripping any of those corrupts a real name. Every codepoint below is
 * written as an escape and never as itself: this module must be readable to be checkable.
 */

/** Cursor moves and line ends: C0, DEL, the C1 block, and the two separators a browser honours as
 *  line breaks even inside a "one line" span. */
const MOVES = /[\x00-\x1f\x7f-\x9f\u2028\u2029]/g;

/** No glyph, no script role. One range at a time, because a set this file widens by accident is
 *  the bug on the other side of the one it fixes. */
const INVISIBLE = new RegExp(
  "[" +
    "\\xad" + //                     soft hyphen — a break opportunity that shows nothing until it breaks
    "\\u061c\\u200e\\u200f" + //     ALM, LRM, RLM — direction marks over neutral runs
    "\\u202a-\\u202e" + //           LRE RLE PDF LRO RLO — the Trojan Source family
    "\\u2066-\\u2069" + //           LRI RLI FSI PDI — the isolates that do the same job
    "\\u180e\\u200b" + //            Mongolian vowel separator, zero-width space
    "\\u2060-\\u2064" + //           word joiner and the invisible math operators
    "\\ufeff" + //                   BOM / zero-width no-break space
    "\\ufff9-\\ufffb" + //           interlinear annotation — text hidden behind other text
    "\\u{e0000}-\\u{e007f}" + //     the tag block: readable ASCII, invisibly
    "]",
  "gu",
);

const scrubLine = (line: string): string => line.replace(MOVES, " ").replace(INVISIBLE, "");

/** One string, safe for the layout engine. `newlines` keeps `\n` — the approval card is built as
 *  a block of lines and splits on them — while still neutralising the CR beside it, which is a
 *  cursor move and not a line end. The default matches the backend's. */
export function scrub(value: unknown, { newlines = false }: { newlines?: boolean } = {}): string {
  const text = String(value ?? "");
  if (newlines) return text.split("\n").map(scrubLine).join("\n");
  return scrubLine(text);
}

/** A whole wire object, scrubbed wherever a string can hide, so a field added to the shape later is
 *  covered by having been carried and not by having been remembered. Object KEYS are left alone. */
export function scrubDeep<T>(value: T): T {
  if (typeof value === "string") return scrub(value, { newlines: true }) as unknown as T;
  if (Array.isArray(value)) return value.map(scrubDeep) as unknown as T;
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) out[k] = scrubDeep(v);
    return out as T;
  }
  return value;
}
