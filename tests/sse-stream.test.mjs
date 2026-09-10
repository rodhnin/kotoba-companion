// The stream-health decision and the SSE grammar behind the half-open-proxy fix — run against the
// REAL lib/sse-stream.ts (Node strips the types):
//   node tests/sse-stream.test.mjs
// Guards: the staleness rule tolerates two missed server pings plus jitter (a healthy-but-quiet
// stream must never look dead — a false reconnect loop is worse than the bug it replaces), and the
// parser survives everything the wire can do: frames split at any byte, multi-line data, comment
// keepalives (": ping") producing no frame, CRLF/CR line ends, and event-name reset per dispatch.
import assert from "node:assert/strict";

const { SseParser, sseIsStale, SSE_PING_MS, SSE_STALE_MS } = await import("../lib/sse-stream.ts");

// staleness contract: the backend pings every 15s of quiet (api events endpoint) — the threshold
// must survive two missed pings + 5s of jitter, and a stream is only stale strictly BEYOND it.
assert.equal(SSE_PING_MS, 15000, "must match the backend's keepalive cadence");
assert.ok(SSE_STALE_MS >= 2 * SSE_PING_MS + 5000, "two missed pings + jitter must NOT count as dead");
assert.equal(sseIsStale(0, SSE_STALE_MS), false, "exactly at the threshold is still alive");
assert.equal(sseIsStale(0, SSE_STALE_MS + 1), true, "past the threshold is dead");
assert.equal(sseIsStale(1000, 1000 + SSE_PING_MS), false, "one quiet ping interval is healthy");

// one whole frame
let p = new SseParser();
assert.deepEqual(p.feed('event: emotion\ndata: {"emotion":"happy"}\n\n'), [
  { event: "emotion", data: '{"emotion":"happy"}' },
]);

// the same frame split at every byte boundary — chunking must never change the result
const wire = 'event: task\ndata: {"kind":"working","on":true}\n\nevent: emotion\ndata: {"a":1}\n\n';
for (let cut = 1; cut < wire.length; cut++) {
  const q = new SseParser();
  const got = [...q.feed(wire.slice(0, cut)), ...q.feed(wire.slice(cut))];
  assert.deepEqual(
    got,
    [
      { event: "task", data: '{"kind":"working","on":true}' },
      { event: "emotion", data: '{"a":1}' },
    ],
    `split at byte ${cut} must not corrupt frames`,
  );
}

// comment keepalives produce no frame — they are activity, not data
p = new SseParser();
assert.deepEqual(p.feed(": connected\n\n: ping\n\n: ping\n\n"), []);

// a comment BETWEEN fields of a frame must not break the frame
p = new SseParser();
assert.deepEqual(p.feed("event: emotion\n: ping\ndata: x\n\n"), [{ event: "emotion", data: "x" }]);

// blank line with no data = no dispatch (that is what a pure keepalive flush looks like)
p = new SseParser();
assert.deepEqual(p.feed("\n\n\n"), []);

// multi-line data joins with \n; missing event name defaults to "message"
p = new SseParser();
assert.deepEqual(p.feed("data: one\ndata: two\n\n"), [{ event: "message", data: "one\ntwo" }]);

// event name resets after each dispatch — a later bare data frame must not inherit "emotion"
p = new SseParser();
assert.deepEqual(p.feed("event: emotion\ndata: a\n\ndata: b\n\n"), [
  { event: "emotion", data: "a" },
  { event: "message", data: "b" },
]);

// CRLF and lone-CR line ends, including a \r\n split exactly between chunks
p = new SseParser();
assert.deepEqual([...p.feed("event: task\r\ndata: y\r"), ...p.feed("\n\r\n")], [{ event: "task", data: "y" }]);

// field value space-stripping only removes ONE leading space
p = new SseParser();
assert.deepEqual(p.feed("data:  padded\n\n"), [{ event: "message", data: " padded" }]);

console.log("sse-stream.test.mjs: all assertions passed");
