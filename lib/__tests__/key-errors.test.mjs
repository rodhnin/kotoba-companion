// Five different problems used to get one answer: "maybe a letter got lost when you copied it".
// These import and CALL the classifier, so a break in it has somewhere to show.
import assert from "node:assert/strict";
import { test } from "node:test";

const { classifyKeyFailure, reasonOf } = await import("../key-errors.ts");

test("no credit behind a real key is not a bad paste", () => {
  for (const d of ["RateLimitError: Error code: 429 - insufficient_quota",
                   "You exceeded your current quota, please check your plan and billing details"]) {
    assert.equal(classifyKeyFailure(d), "quota", d);
  }
});

test("something in front of the provider is this machine, not the key", () => {
  for (const d of ["APIConnectionError: Connection error.",
                   "request timed out", "getaddrinfo ENOTFOUND api.openai.com",
                   "proxy refused the connection", "SSL: CERTIFICATE_VERIFY_FAILED"]) {
    assert.equal(classifyKeyFailure(d), "network", d);
  }
});

test("a keystore that cannot write is our install", () => {
  assert.equal(classifyKeyFailure("cannot store the key encrypted: no usable master key"), "install");
});

test("the wrong model is not the wrong key", () => {
  assert.equal(classifyKeyFailure("NotFoundError: Error code: 404 - model_not_found"), "model");
});

test("anything unrecognised stays the answer that asks for the key again", () => {
  for (const d of ["", "  ", "Incorrect API key provided", "nonsense", null, undefined]) {
    assert.equal(classifyKeyFailure(d), "paste", String(d));
  }
});

test("the reason is read from a refusal or from a 200 that said no", () => {
  assert.equal(reasonOf({ ok: false, data: null, detail: "boom" }), "boom");
  assert.equal(reasonOf({ ok: true, data: { ok: false, detail: "kept it anyway" }, detail: "" }),
               "kept it anyway");
  assert.equal(reasonOf({ ok: false, data: { ok: false }, detail: " trimmed " }), "trimmed");
  assert.equal(reasonOf(null), "");
});
