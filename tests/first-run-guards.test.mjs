// The three things about first run that nothing else checks — how /setup names itself, what it puts on
// the network, and whether what she says is actually announced:
//   node --test tests/first-run-guards.test.mjs
// The components have JSX, so nothing here can import them; they are read as text, which is how
// every other guard over these files reads them too.
//
// These guards were rescued from a test that also drove the removed /demo/onboarding rig and could not
// travel with the flow. Everything below is about the product; nothing below is about a preview.
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (...p) => readFileSync(join(root, ...p), "utf8");
const src = read("components", "Onboarding.tsx");
const page = read("app", "setup", "page.tsx");

const sources = [];
const walk = (dir) => {
  for (const name of readdirSync(join(root, dir))) {
    const rel = join(dir, name);
    if (statSync(join(root, rel)).isDirectory()) walk(rel);
    else if (/\.tsx?$/.test(name)) sources.push(rel);
  }
};
for (const d of ["app", "components", "lib"]) walk(d);

test("nothing names the browser tab from an effect", () => {
  // Measured live, back when the flow did this: Next writes its own <title> from route metadata AFTER
  // hydration, so a `document.title` set in a useEffect is overwritten and the tab kept the layout's
  // name. The screen looked right; the tab lied. Route metadata is the only place a title can win from.
  const offenders = sources.filter((f) =>
    read(f)
      .split("\n")
      .some((l) => /document\.title\s*=/.test(l) && !/^\s*(\*|\/\/)/.test(l)),
  );
  assert.deepEqual(
    offenders,
    [],
    "a title assigned from an effect loses to Next's own <title> — export route metadata instead",
  );
});

test("/setup names itself through route metadata, and stays a server component", () => {
  const meta = page.match(/export const metadata[^=]*=\s*\{[^}]*title:\s*"([^"]+)"/);
  assert.ok(meta, "/setup names itself through route metadata");
  assert.equal(meta[1], "Kotoba — first meeting", "/setup carries its own title");
  assert.ok(!/"use client"/.test(page), "/setup must stay a server component to export metadata");
});

test("the first-run flow's network calls, counted by PRIMITIVE and not by helper name", () => {
  // The backend's guard scans lines containing `post("`, so a call written with any other
  // primitive — apiFetch, fetch, EventSource, a socket — passes it unseen. This is the list that keeps
  // the next one honest: first run writes keys and a name, so traffic it grows has to be deliberate.
  const PRIMITIVES = /\b(fetch|apiFetch|EventSource|WebSocket|XMLHttpRequest|sendBeacon)\s*\(/;
  const sites = src.split("\n").filter((l) => PRIMITIVES.test(l) && !l.trim().startsWith("*"));
  assert.deepEqual(
    sites.map((l) => l.trim()),
    ["const r = await apiFetch(`${API}${path}`, init);", "const r = await apiFetch(`${API}${path}`);"],
    "a new network call in the first-run flow has to be added here on purpose",
  );
});

test("and by ADDRESS, because two generic primitives can carry any number of them", () => {
  // Both sites above now take their path as an argument — the face step needed a GET beside the POST
  // helper and an upload whose body is the archive itself, so one shape could not serve all three. That
  // makes the call-site list blind to a new endpoint, which is the very thing it was counting. The
  // addresses are what is actually deliberate; this is the list that grew a step.
  // The key step's two writes live in lib/key-step.ts now — the decision of WHAT may be sent was the
  // thing worth taking out of a component — so the list has to look there as well or it stops counting.
  const wired = src + read("lib", "key-step.ts");
  const addressed = [...new Set([...wired.matchAll(/"(\/api\/[^"]+)"/g)].map((m) => m[1]))].sort();
  assert.deepEqual(addressed, [
    "/api/avatar",
    "/api/models/default",
    "/api/models/install/default",
    "/api/models/install/upload",
    "/api/settings/llm-key",
    "/api/settings/soul",
    "/api/settings/user-name",
    "/api/settings/voice-key",
    "/api/setup/model",
    "/api/setup/provider",
    "/api/setup/status",
  ]);
});

test("what she says is announced, not just drawn", () => {
  // The speech bubble is the flow's whole spoken channel — the greeting, every question and every
  // reaction go through it. It carried `key={line}` and `role="status"` on the SAME element, so each new
  // line REPLACED the live region instead of mutating it. Measured across one greeting cycle: the node
  // with role="status" was removed and a fresh one inserted (sameNode false, 1 live-region node removed,
  // 0 mutations inside one) — a screen reader registers a region and then watches it change, and nothing
  // ever changed inside the region it registered.
  // The key stays, because it is what restarts the `rise` animation per line; it just belongs on the
  // bubble INSIDE the region rather than on the region itself.
  const bubble = src.slice(src.indexOf('role="status"') - 400, src.indexOf('role="status"'));
  assert.ok(
    !/key=\{line\}/.test(bubble),
    "a live region keyed on its own content is remounted per line, and a remount is not an announcement",
  );
  const region = src.slice(src.indexOf('role="status"'), src.indexOf('role="status"') + 900);
  assert.match(
    region,
    /key=\{line\}/,
    "the keyed bubble has to live inside the region, so the region stays put and its subtree changes",
  );
  assert.match(region, /animation: "rise \.35s ease both"/, "and the key still has an animation to restart");
});
