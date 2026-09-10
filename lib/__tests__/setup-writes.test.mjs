// Unit tests for lib/setup-writes.ts — run with: node --test lib/__tests__/setup-writes.test.mjs
// Plain .mjs so tsc ignores it; node's built-in type stripping loads the .ts module directly.
//
// The defect this module exists for: /setup posted the provider and the model with `void post(...)` and
// printed the value it had SENT. A refused or unreachable write left the Ready card saying
// `Model: gpt-5.4` while the backend had kept the old one — and the key check that comes next validates
// against whatever IS configured, so nothing on the screen ever contradicted it.
import assert from "node:assert/strict";
import { test } from "node:test";

const { refusal, refusalText, settle } = await import("../setup-writes.ts");

const ok = (data) => ({ ok: true, data, detail: "" });
const no = (detail = "") => ({ ok: false, data: null, detail });

test("a refusal leaves the value that was there before the click", () => {
  assert.equal(settle(no(), "model", "gpt-5.4", "grok-4.3"), "grok-4.3");
  assert.equal(settle(no("nope"), "provider", "xai", "openai"), "openai");
});

test("a success is answered in the backend's own words, not the caller's", () => {
  // /api/setup/provider pins a model along with the brain, and /api/setup/model returns what
  // llm.model_name now reads — so what comes back is not always what was clicked.
  assert.equal(settle(ok({ model: "grok-4.3" }), "model", "gpt-5.6-luna", ""), "grok-4.3");
  assert.equal(settle(ok({ provider: "xai" }), "provider", "xai", "openai"), "xai");
});

test("a success that says nothing about the field falls back to what was clicked", () => {
  for (const body of [{}, null, { model: "" }, { model: 7 }]) {
    assert.equal(settle(ok(body), "model", "grok-4.6", "grok-4.3"), "grok-4.6", JSON.stringify(body));
  }
});

test("the reason comes out of a FastAPI refusal", () => {
  assert.equal(
    refusal({ detail: "OpenAI does not serve grok-4.6 — xAI (Grok) does" }),
    "OpenAI does not serve grok-4.6 — xAI (Grok) does",
  );
  assert.equal(refusal({ detail: "  model required  " }), "model required");
});

test("a body with no reason in it has none to give", () => {
  for (const body of [null, undefined, {}, "<html>502</html>", { detail: 42 }, { detail: [] }]) {
    assert.equal(refusal(body), "", JSON.stringify(body ?? null));
  }
});

test("a validation error is a list, and the first message is the readable half", () => {
  const body = { detail: [{ loc: ["body", "model"], msg: "Input should be a valid string" }] };
  assert.equal(refusal(body), "Input should be a valid string");
});

test("the screen never shows a failure with nothing said about it", () => {
  assert.equal(refusalText(no(), "I'm still on whichever model I had"), "I'm still on whichever model I had.");
  assert.equal(refusalText(no("model required"), "unused"), "model required.");
});

test("a reason that is already a sentence is not punctuated twice", () => {
  for (const said of ["that is the placeholder rather than a key.", "Really?", "Wait…", "No!"]) {
    assert.equal(refusalText(no(said), "unused"), said);
  }
});
