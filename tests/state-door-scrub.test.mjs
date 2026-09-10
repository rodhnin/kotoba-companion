// Every OTHER door into client state, pinned against the REAL store (Node strips the types). Every
// attack codepoint below is written as an escape, never as itself.
//
// The approval-card gate alone was not enough: the same turn that showed a clean card drew
// `$ touch fdp.troper && ls -la` REVERSED in the Terminal panel, so the row that exists so the
// operator can verify what ran displayed `report.pdf` for a command that touched something else.
// `pushInputRequest` was the only scrubbed door. This pushes the live repro bytes through every
// remaining one — steps, tasks, subagents, the report title — asserting nothing a component draws
// still carries a cursor move, an override or an invisible break, while readable bytes and
// right-to-left NAMES survive. File PATHS are pinned RAW: a path is protocol, gated at the draw site.
import assert from "node:assert/strict";

const { useKotobaStore } = await import("../lib/store.ts");
const s = () => useKotobaStore.getState();

// MOVES minus "\n" (blocks of lines keep them), plus every INVISIBLE class — the exact codepoints
// core/text_security.py neutralises or removes.
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

const ARABIC = "دمشق";
const HEBREW = "ירושלים";
const PERSIAN_ZWNJ = "می\u200cخواهم";

// ---- the Terminal row: the live repro, byte for byte ----------------------------------------------

s().clearWork();

// phase:start — the command line the panel draws while it runs (the SPAN read in the live DOM).
s().pushStep({ phase: "start", id: "call_rlo", step_kind: "shell", action: "touch \u202efdp.troper\u202c && ls -la" });
let row = s().logs.find((l) => l.id === "call_rlo");
clean(row.action, "running row action");
assert.match(row.action, /touch fdp\.troper && ls -la/, "every readable byte, in logical order");

// phase:done UPDATE of the same row — the second write path must be gated too, not just the first.
s().pushStep({
  phase: "done",
  id: "call_rlo",
  ok: true,
  result: "  exit=0\n  total 8\u202e\n  -rw-r--r-- 1 u u 0 fdp.troper\r",
  full: "exit=0\nstdout:\ntotal 8\n-rw-r--r-- 1 u u 0 \u202efdp.troper\u202c\ndone\u200b\x1b[2K",
});
row = s().logs.find((l) => l.id === "call_rlo");
clean(row.result, "row result");
clean(row.full, "row full output");
assert.match(row.full, /\n/, "newlines are load-bearing: the console block splits on them");
assert.doesNotMatch(row.result, /\r/, "CR is a cursor move, not a line end");
assert.match(row.full, /fdp\.troper/, "the reversed half must stay visible — in logical order");

// A standalone done frame (no start) and a legacy plain line take the other branch.
s().pushStep({ phase: "done", id: "call_solo", step_kind: "web", action: "open \u2066evil\u2069.example", result: "ok\ufeff", full: "ok" });
row = s().logs.find((l) => l.id === "call_solo");
clean(row.action, "solo action");
clean(row.result, "solo result");
s().pushStep({ text: "legacy \u202eline\x07" });
clean(s().logs[s().logs.length - 1].text, "legacy text line");

// An RTL command target still renders.
s().pushStep({ phase: "start", id: "call_rtl", step_kind: "shell", action: `cat ${ARABIC}/${HEBREW}.txt` });
row = s().logs.find((l) => l.id === "call_rtl");
assert.match(row.action, new RegExp(ARABIC), "Arabic must survive codepoint-for-codepoint");
assert.match(row.action, new RegExp(HEBREW), "Hebrew must survive codepoint-for-codepoint");

// ---- the Plan (task list) --------------------------------------------------------------------------

s().setTasks({
  list_id: "plan1",
  title: "deploy \u202egnihtemos\u202c",
  tasks: [
    { id: "t1", text: "run \u202efdp.troper\u202c first\x1b[1G", status: "pending", order: 1 },
    { id: "t2", text: PERSIAN_ZWNJ, status: "active", order: 2 },
  ],
  status: "open",
  rev: 1,
});
clean(s().tasks.title, "plan title");
clean(s().tasks.tasks[0].text, "task text");
assert.match(s().tasks.tasks[0].text, /fdp\.troper/, "readable bytes survive in logical order");
assert.equal(s().tasks.tasks[1].text, PERSIAN_ZWNJ, "ZWNJ decides letter joining and must survive");
assert.equal(s().tasks.rev, 1, "protocol numbers pass through untouched");

// ---- the helper chibis (goal, steps, summary) ------------------------------------------------------

s().spawnSubagent("sub1", "research \u202esretniop\u202c\u200b now");
let agent = s().subagents.find((a) => a.id === "sub1");
clean(agent.goal, "subagent goal");
assert.match(agent.goal, /sretniop/, "readable bytes survive in logical order");

s().addSubagentStep("sub1", "reading \u202eeht\u202c docs\r\nline two");
agent = s().subagents.find((a) => a.id === "sub1");
clean(agent.steps[0], "subagent step");
assert.match(agent.steps[0], /\n/, "a multi-line step keeps its line ends");

s().finishSubagent("sub1", `# Done\u202e\nFound ${ARABIC}\u200b in the docs.`);
agent = s().subagents.find((a) => a.id === "sub1");
clean(agent.summary, "subagent summary");
assert.match(agent.summary, new RegExp(ARABIC), "Arabic must survive in the summary");

// The id is PROTOCOL: later frames arrive with the same raw id and must keep matching.
s().spawnSubagent("sub\u200b2", "goal");
s().addSubagentStep("sub\u200b2", "step");
assert.equal(s().subagents.find((a) => a.id === "sub\u200b2").steps.length, 1, "raw ids must keep matching across frames");

// ---- the report title (panel header chrome) --------------------------------------------------------

s().setReport("Q3 \u202etroper\u202c\x1b[2K");
clean(s().reportTitle, "report title");
assert.match(s().reportTitle, /troper/, "readable bytes survive");
s().setReport(null);
assert.equal(s().reportTitle, null, "null means NO report and must stay null, not become a string");

// ---- file paths are protocol: pinned RAW here, gated at the FilesPanel draw site -------------------

const trapped = "reports/\u202efdp.troper\u202c";
s().pushFile(trapped, "created");
assert.equal(
  s().files.find((f) => f.path === trapped)?.path,
  trapped,
  "the path must stay byte-identical — open/delete/raw-URL round-trip it to disk",
);

// ---- dismissal parity: stored ids are scrubbed, so a dismissal by the raw id must still land ------

s().pushInputRequest({ id: "req\u200b9", requestId: "req\u200b9", mode: "approval", label: "x" });
s().dismissInputRequest("req\u200b9");
assert.equal(
  s().inputRequests.some((r) => r.requestId === "req9" || r.id === "req9"),
  false,
  "a card whose id was scrubbed on push must still be dismissible by the wire's raw id",
);

s().clearWork();
console.log("state-door-scrub: all assertions passed");
