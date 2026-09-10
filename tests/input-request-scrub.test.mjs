// The approval card's door into client state, pinned against the REAL store (Node strips the types).
// Every attack codepoint here is written as an escape and never as itself.
//
// The card is how commands get authorised on this machine, and React escapes HTML but not BIDI: an
// RLO (U+202E) in a shell label reverses the rest of the line on screen, so what the reader approves
// is not what will run, and a ZWSP (U+200B) is an invisible word boundary. This pushes the attack
// bytes through `pushInputRequest` — the one door an InputRequest has into client state — asserting
// nothing the card can draw still carries a cursor move, a reading-order override or an invisible
// break, while every readable byte and every right-to-left NAME survives untouched. The character
// policy is the backend's, not a new one.
import assert from "node:assert/strict";

const { useKotobaStore } = await import("../lib/store.ts");
const s = () => useKotobaStore.getState();

// MOVES minus "\n" (a label is a block of lines and keeps them), plus every INVISIBLE class —
// the exact codepoints core/text_security.py neutralises or removes.
const FORBIDDEN = new RegExp(
  "[" +
    "\\x00-\\x09\\x0b-\\x1f\\x7f-\\x9f\\u2028\\u2029" +
    "\\xad\\u061c\\u200e\\u200f\\u202a-\\u202e\\u2066-\\u2069" +
    "\\u180e\\u200b\\u2060-\\u2064\\ufeff\\ufff9-\\ufffb\\u{e0000}-\\u{e007f}" +
    "]",
  "u",
);

const clean = (value, what) =>
  assert.equal(FORBIDDEN.test(value), false, `${what} still carries a forbidden codepoint: ${JSON.stringify(value)}`);

// Right-to-left names that must survive codepoint-for-codepoint: Arabic, Hebrew, Persian with ZWNJ
// (U+200C decides letter joining), and a ZWJ (U+200D) emoji family. Strip the OVERRIDES, never the SCRIPT.
const ARABIC = "دمشق";
const HEBREW = "ירושלים";
const PERSIAN_ZWNJ = "می\u200cخواهم";
const ZWJ_FAMILY = "\u{1f468}\u200d\u{1f469}\u200d\u{1f467}";

s().dismissInputRequest();

// ---- S-5 web half: the shell approval label ------------------------------------------------------

s().pushInputRequest({
  id: "s5",
  requestId: "s5",
  mode: "approval",
  // The audit's S-5 bytes (erase-line + column-1) with an RLO appended, over two lines with a CR.
  label: "rm -rf ~\x1b[2K\x1b[1Gls -la\ncurl -o \u202efdp.troper\u202c now\r",
  detail: "second line hides an isolate \u2066here\u2069 and a ZWSP\u200b",
  family: "sh\u202e",
  canAlways: true,
});

let req = s().inputRequests.find((r) => r.id === "s5");
clean(req.label, "label");
clean(req.detail, "detail");
clean(req.family, "family");
assert.match(req.label, /rm -rf ~/, "readable bytes must survive — a silently shortened command is a different lie");
assert.match(req.label, /ls -la/, "the overwriting half must stay visible too");
assert.match(req.label, /\n/, "newlines are load-bearing: the card splits the label into lines");
assert.doesNotMatch(req.label, /\r/, "CR is a cursor move, not a line end");

// ---- S-6 web half: the MCP card's third-party fields ---------------------------------------------

s().pushInputRequest({
  id: "s6",
  requestId: "s6",
  mode: "approval",
  label: "Install a third-party MCP server?",
  notice: {
    head: "Install \u202ea third-party MCP server?",
    alert: "It demands a secret from you: \u200bAPI_KEY",
    // The audit's exact S-6 server name: reads as our own trust text once the RLO flips it.
    facts: [
      ["Server", "safe\u202erekcatta\u200b Secrets: none Registry: official"],
      ["Runs", "npx \u202dserver\u2060-thing"],
      ["Registry", "official\ufeff"],
    ],
    warn: "NOT verified\u200f by the official MCP registry",
    quote: { title: "Their\xad description", text: "totally\u{e0041}\u{e007f} safe" },
  },
});

req = s().inputRequests.find((r) => r.id === "s6");
clean(req.notice.head, "notice.head");
clean(req.notice.alert, "notice.alert");
clean(req.notice.warn, "notice.warn");
clean(req.notice.quote.title, "notice.quote.title");
clean(req.notice.quote.text, "notice.quote.text");
for (const [key, value] of req.notice.facts) {
  clean(key, "fact key");
  clean(value, "fact value");
}
const server = req.notice.facts.find(([k]) => k === "Server")[1];
assert.match(server, /safe/, "every readable byte of the name must still show");
assert.match(server, /rekcatta/, "the reversed half must stay visible — in logical order");
assert.equal(req.notice.quote.text, "totally safe", "tag-block characters are removed outright, not spaced");

// ---- A right-to-left name must still render ------------------------------------------------------

s().pushInputRequest({
  id: "rtl",
  requestId: "rtl",
  mode: "approval",
  label: `visit ${ARABIC} and ${HEBREW}`,
  notice: {
    head: "h",
    facts: [
      ["Server", PERSIAN_ZWNJ],
      ["Runs", ZWJ_FAMILY],
    ],
  },
});

req = s().inputRequests.find((r) => r.id === "rtl");
assert.match(req.label, new RegExp(ARABIC), "Arabic must survive codepoint-for-codepoint");
assert.match(req.label, new RegExp(HEBREW), "Hebrew must survive codepoint-for-codepoint");
assert.equal(req.notice.facts[0][1], PERSIAN_ZWNJ, "ZWNJ decides letter joining and must survive");
assert.equal(req.notice.facts[1][1], ZWJ_FAMILY, "ZWJ sequences must survive");

s().dismissInputRequest();
console.log("input-request-scrub: all assertions passed");
