// The card's second copy of the output, run against the REAL lib/store.ts and the real TerminalPanel:
//   node tests/terminal-full-output.test.mjs
// Asked four times how many things were in one folder she said 23, 14+9, 92 and 25, and
// nothing on screen could tell the right answer from the confident wrong ones — a 23-line listing
// reached the card as ONE line of 18 characters, a 2887-character shell result as 13. The trim is the
// card's, and it stays; what these pin is that the rest now arrives beside it, that the preview did not
// widen by a character, and that a row with nothing more to show grows no control.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const { useKotobaStore } = await import("../lib/store.ts");
const s = () => useKotobaStore.getState();

const LISTING = [
  "[DIR] benchmarks", "[DIR] reports", "[DIR] screenshots", "[DIR] notes", "[DIR] drafts",
  "[DIR] tmp", "[DIR] visual-memory", "[DIR] exports", "[DIR] archive-2026",
  "[FILE] kotoba.log", "[FILE] todo.md", "[FILE] latency-numbers.csv", "[FILE] budget-2026.xlsx",
  "[FILE] readme.md", "[FILE] plan-fase-3.md", "[FILE] soul-notes.md", "[FILE] session-4213.json",
  "[FILE] avatar-sketch.png", "[FILE] garden_night.glb", "[FILE] invoice-july.pdf",
  "[FILE] prices.csv", "[FILE] meeting-notes.md", "[FILE] scratch-pad.txt",
].join("\n");

const after = (done) => {
  s().clearWork();
  s().pushStep({ phase: "start", id: "c1", step_kind: "mcp", action: "list_directory" });
  s().pushStep({ phase: "done", id: "c1", outcome: "ok", ok: true, ...done });
  return s().logs[0];
};

// ── the measurement ──────────────────────────────────────────────────────────
const row = after({ result: "  [DIR] benchmarks", full: LISTING });
assert.equal(row.result, "  [DIR] benchmarks", "the preview is the backend's, unwidened");
assert.equal(row.full, LISTING);
assert.equal(row.full.split("\n").length, 23, "every entry is on the card, or you cannot count them");
assert.equal(LISTING.length, 443);

// ── a re-sent row must not keep the parked one's output ──────────────────────
// core/deferred_exec completes a carded row minutes later; `full` has to be written, never merged, or
// the row shows the approval sentence's output under a command that has since actually run.
s().clearWork();
s().pushStep({ phase: "start", id: "c7", step_kind: "shell", action: "$ pip install requests" });
s().pushStep({ phase: "done", id: "c7", ok: true, outcome: "pending", pending: true, result: "asked", full: "I asked you on screen." });
s().pushStep({ phase: "done", id: "c7", ok: true, outcome: "ok", pending: false, result: "  I ran it.", full: "I ran `pip install requests` (exit code 0). Output: Collecting requests" });
assert.equal(s().logs.length, 1);
assert.match(s().logs[0].full, /exit code 0/);
assert.doesNotMatch(s().logs[0].full, /asked you on screen/);

// ── a backend that sends no `full` is unchanged ──────────────────────────────
assert.equal(after({ result: "  done" }).full, undefined, "the field is additive");

// ── the card itself ──────────────────────────────────────────────────────────
const panel = readFileSync(new URL("../components/panels/TerminalPanel.tsx", import.meta.url), "utf8");

// Tool output comes from outside the process — an MCP server, a command's stdout. It reaches the DOM as
// a React text child and must never reach it as markup.
assert.doesNotMatch(panel, /dangerouslySetInnerHTML/, "tool output must stay a text child");
assert.match(panel, /\{shown\}\s*<\/pre>/, "the console renders the string, it does not build markup");

// The control only exists when there is something behind it, and it is closed on arrival.
assert.match(panel, /useState\(false\)/, "closed by default — the compact card looks unchanged");
assert.match(panel, /hasMore = full !== "" && parseResult\(full\) !== body/);
assert.match(panel, /Show full output/);
assert.match(panel, /Hide full output/);

console.log("terminal-full-output: ok");
