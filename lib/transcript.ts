/**
 * What her words do to the transcript, kept out of the component so the rules can be driven without a
 * DOM. The two transports deliver a turn differently and every rule here has to survive both: the
 * ElevenLabs SDK hands over a whole utterance as ONE event, while our own WebSocket hands over
 * whatever the backend's sentence filter emitted, so one turn arrives as SEVERAL frames — one of
 * which, in local mode, is the tool heartbeat's bare hum. `AgentTextStream` is the local half, and it
 * drops that hum BEFORE the accumulator, which is what keeps text and bubbles in step and her first
 * real words arriving as a first.
 */

// Relative + extension so plain node can load this module directly.
import { scrub } from "./text-security.ts";

export type TranscriptMode = "agent" | "local";

export type AgentTextEvent = { kind: "message" | "correction"; text: string } | null;

const HUM_EDGE = /^[\s.!?¿¡…"']+|[\s.!?¿¡…"']+$/g;
const WORDLESS_SOUND = /^[mh]+$/i;

/** Mirrors core/stream.py `_WORDLESS_SOUND`. A second definition here would drift from the one that
 *  actually emits them. */
export function isWordlessHum(text: string): boolean {
  const core = text.replace(HUM_EDGE, "");
  return core !== "" && WORDLESS_SOUND.test(core);
}

/** The one normalisation funnel every bubble passes through, so the bidi/invisible gate lives here:
 *  her replies quote web pages and MCP servers, and a transcript line deceives like a Terminal row. */
export const cleanText = (t: string) =>
  scrub(t, { newlines: true }).replace(/<\|[^>]*\|>/g, "").replace(/[ \t]{2,}/g, " ").trim();

/** Strip EL's "Mmm…" filler and our "... " buffer words from the DISPLAYED text only, shape-anchored
 *  so "Mmm, déjame…" survives. Emptying a WHOLE line is an agent-mode move only; a local frame is a
 *  fragment whose siblings are still coming.
 *
 *  The `(?!\s)` is load-bearing, not decoration: without it the leading whitespace run is retried at
 *  every split and both regexes go quadratic — measured 32 ms at 3.2k characters, 8.0 s at 51k. */
export const stripAgentFiller = (t: string, mode: TranscriptMode) => {
  if (mode !== "local" && /^\s*(?!\s)[mM]{0,4}[.…?!\s]*$/.test(t) && /[.…?]/.test(t)) return "";
  return t.replace(/^\s*(?!\s)[mM]{0,4}[.…?!\s]*(?=\S)/, (m) => (/[.…?]/.test(m) ? "" : m)).trimStart();
};

/** Rewrite the open agent line, or append when there is none. Only the LAST line is open: walking
 *  further back reaches a turn the user has already read, and rewriting that is losing an answer. */
export function applyAgentCorrection<T extends { from: "user" | "ai"; text: string }>(
  lines: T[],
  corrected: string,
  newLine: () => T,
): T[] {
  const last = lines[lines.length - 1];
  if (!last || last.from !== "ai") return [...lines, newLine()];
  if (last.text === corrected) return lines;
  const copy = lines.slice();
  copy[copy.length - 1] = { ...last, text: corrected };
  return copy;
}

export class AgentTextStream {
  private turn = 0;
  private text = "";

  feed(frame: { text: unknown; turn: unknown }): AgentTextEvent {
    if (typeof frame.text !== "string" || !frame.text) return null;
    // The tool heartbeat is a frame of its own. Dropping it here — before the accumulator, not after —
    // is what keeps text and bubbles in step, and keeps her first real words arriving as a first.
    if (isWordlessHum(frame.text)) return null;
    // No turn id → join the open bubble. `Number(...) || 0` read "absent" as turn 0, a real turn number.
    const n = Number(frame.turn);
    const turn = Number.isFinite(n) ? n : this.turn;
    if (turn !== this.turn) {
      this.turn = turn;
      this.text = "";
    }
    const first = this.text === "";
    this.text += frame.text;
    return { kind: first ? "message" : "correction", text: this.text };
  }
}
