import assert from "node:assert/strict";
import { test } from "node:test";

import { addressed, answerFor, deliveryFor, grantsOffered, headlineOf, LABEL_BUDGET } from "../approval-card.ts";

test("a card with no flags offers yes and no, and nothing wider", () => {
  assert.deepEqual(grantsOffered({}), ["yes", "no"]);
  assert.deepEqual(grantsOffered({ family: "shell" }), ["yes", "no"]);
  assert.deepEqual(grantsOffered({ canAlways: undefined, canAlwaysExact: undefined, family: "shell" }), ["yes", "no"]);
});

test("the wide grant needs the flag to be exactly true AND a family to name", () => {
  assert.deepEqual(grantsOffered({ canAlways: true, family: "shell" }), ["yes", "no", "family"]);
  assert.deepEqual(grantsOffered({ canAlways: true }), ["yes", "no"]);
  assert.deepEqual(grantsOffered({ canAlways: true, family: "" }), ["yes", "no"]);
  assert.deepEqual(grantsOffered({ canAlways: "true", family: "shell" }), ["yes", "no"]);
  assert.deepEqual(grantsOffered({ canAlways: 1, family: "shell" }), ["yes", "no"]);
});

test("the narrow grant fails closed the same way", () => {
  assert.deepEqual(grantsOffered({ canAlwaysExact: true }), ["yes", "no", "exact"]);
  assert.deepEqual(grantsOffered({ canAlwaysExact: "true" }), ["yes", "no"]);
  assert.deepEqual(grantsOffered({ canAlwaysExact: true, canAlways: true, family: "git" }), ["yes", "no", "exact", "family"]);
});

test("every grant carries exactly its own width on the wire", () => {
  assert.deepEqual(answerFor("yes"), { approved: true, always: false, always_exact: false });
  assert.deepEqual(answerFor("no"), { approved: false, always: false, always_exact: false });
  assert.deepEqual(answerFor("exact"), { approved: true, always: false, always_exact: true });
  assert.deepEqual(answerFor("family"), { approved: true, always: true, always_exact: false });
});

test("no is the only refusal and never persists anything", () => {
  for (const kind of ["yes", "exact", "family"]) assert.equal(answerFor(kind).approved, true);
  const no = answerFor("no");
  assert.equal(no.approved, false);
  assert.equal(no.always, false);
  assert.equal(no.always_exact, false);
});

test("only one grant may set each persistence flag", () => {
  const kinds = ["yes", "no", "exact", "family"];
  assert.deepEqual(kinds.filter((k) => answerFor(k).always), ["family"]);
  assert.deepEqual(kinds.filter((k) => answerFor(k).always_exact), ["exact"]);
});

test("the headline is the first NON-BLANK line, and the toggle is offered whenever it is not the whole label", () => {
  assert.deepEqual(headlineOf("\n\nrm -rf build\nmore"), { headline: "rm -rf build", hasMore: true });
  assert.deepEqual(headlineOf("ls -la"), { headline: "ls -la", hasMore: false });
  assert.deepEqual(headlineOf("   "), { headline: "", hasMore: false });
  const long = "x".repeat(LABEL_BUDGET + 5);
  const clipped = headlineOf(long);
  assert.equal(clipped.headline, "x".repeat(LABEL_BUDGET) + "…");
  assert.equal(clipped.hasMore, true);
});

test("a key or a secret is delivered by POST, never spoken, whatever the backend said about wait", () => {
  assert.deepEqual(deliveryFor({ inputKind: "key", name: "OPENAI" }, "sk-1"), { kind: "post", body: { kind: "key", value: "sk-1", name: "OPENAI" } });
  assert.deepEqual(deliveryFor({ inputKind: "key" }, "sk-1"), { kind: "post", body: { kind: "key", value: "sk-1" } });
  assert.deepEqual(deliveryFor({ inputKind: "secret", wait: false }, "hunter2"), { kind: "post", body: { kind: "secret", value: "hunter2" } });
  assert.deepEqual(deliveryFor({ inputKind: "secret", name: "x" }, "hunter2"), { kind: "post", body: { kind: "secret", value: "hunter2" } });
});

test("a blocking text answer is posted; a plain one is spoken", () => {
  assert.deepEqual(deliveryFor({ inputKind: "text", wait: true }, "yes"), { kind: "post", body: { kind: "text", value: "yes" } });
  assert.deepEqual(deliveryFor({ wait: true }, "yes"), { kind: "post", body: { kind: "text", value: "yes" } });
  assert.deepEqual(deliveryFor({ inputKind: "text" }, "hello"), { kind: "speak" });
  assert.deepEqual(deliveryFor({}, "hello"), { kind: "speak" });
  assert.deepEqual(deliveryFor({ inputKind: "link" }, "https://x"), { kind: "speak" });
});

test("a body answers THIS card when it has an id, and is left alone when it has none", () => {
  assert.deepEqual(addressed({ approved: true }, "r1"), { approved: true, request_id: "r1" });
  assert.deepEqual(addressed({ approved: true }, undefined), { approved: true });
  assert.deepEqual(addressed({ approved: true }, ""), { approved: true });
});
