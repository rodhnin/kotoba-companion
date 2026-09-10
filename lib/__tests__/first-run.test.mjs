// Unit tests for lib/first-run.ts — run with: node --test lib/__tests__/first-run.test.mjs
// Plain .mjs so tsc ignores it; node's built-in type stripping loads the .ts module directly.
//
// This decides whether a visitor is sent to /setup instead of into the app, so the whole point is
// which way it is wrong when it cannot tell. Every unclear answer — a refusal, a thrown fetch, a
// backend that never replies — has to read as "already set up": one visit to an app that reports its
// own trouble beats trapping a configured user in a setup screen they do not need.
import assert from "node:assert/strict";
import { test } from "node:test";

const { firstRunNeeded } = await import("../first-run.ts");

const reply = (body, ok = true) => async () => ({ ok, json: async () => body });

test("a virgin install is sent to setup", async () => {
  assert.equal(await firstRunNeeded(reply({ needed: true })), true);
});

test("an install that has a key is left alone", async () => {
  assert.equal(await firstRunNeeded(reply({ needed: false })), false);
});

test("only a literal true counts — a missing or fuzzy field is not a first run", async () => {
  for (const body of [{}, { needed: "yes" }, { needed: 1 }, null]) {
    assert.equal(await firstRunNeeded(reply(body)), false, JSON.stringify(body));
  }
});

test("a 401 or a 404 does not send anyone through setup", async () => {
  assert.equal(await firstRunNeeded(reply({ needed: true }, false)), false);
});

test("a thrown fetch fails open", async () => {
  assert.equal(
    await firstRunNeeded(async () => {
      throw new TypeError("Failed to fetch");
    }),
    false,
  );
});

test("unparseable JSON fails open", async () => {
  assert.equal(
    await firstRunNeeded(async () => ({
      ok: true,
      json: async () => {
        throw new SyntaxError("Unexpected token <");
      },
    })),
    false,
  );
});

test("a backend that never answers gives up on its own", async () => {
  // Without the deadline /app waits on this before it mounts anything, and a stopped backend leaves
  // the page blank for as long as the browser is willing to wait.
  const started = Date.now();
  const answer = await firstRunNeeded(() => new Promise(() => {}), 30);
  assert.equal(answer, false);
  assert.ok(Date.now() - started < 1000, "it waited past its own deadline");
});

test("a slow but real answer still wins its race", async () => {
  const slow = async () => {
    await new Promise((r) => setTimeout(r, 10));
    return { ok: true, json: async () => ({ needed: true }) };
  };
  assert.equal(await firstRunNeeded(slow, 500), true);
});

// ---- the invitation the reconfigure button carries -------------------------------------------------
// /setup redirects a configured install to /app, so the button in Settings has to say the visit was
// meant. A query parameter and not a one-shot handshake: it survives a reload, which is the difference
// between refreshing mid-reconfiguration and being thrown back to /app.
const { invited, SETUP_INVITE_URL } = await import("../first-run.ts");

test("the button's own URL is an invitation", () => {
  assert.equal(invited(new URL(SETUP_INVITE_URL, "http://x").search), true);
});

test("a bare visit is not one — that is the whole point", () => {
  for (const search of ["", "?", "?next=/app", "?reconfigure", "?reconfigure=0", "?reconfigure=true"]) {
    assert.equal(invited(search), false, search);
  }
});

test("it reads the same whichever way the query is put together", () => {
  assert.equal(invited("?a=1&reconfigure=1"), true);
  assert.equal(invited("reconfigure=1"), true);
});
