// The "Connect a server…" dropdown says which rows need something and puts the ones that don't first.
// The panel has JSX, so nothing here can import it; it is read as text.
//
// Google Calendar was the FIRST option, and it is the one server in the list that signs itself in
// through Google Cloud rather than through Kotoba: `POST /api/mcp/connect` refuses it with a 400 for
// anyone who has not done that setup, so the first thing a stranger clicked was the only thing that
// could not work. The panel's suffix and the backend's refusal are two descriptions of one fact —
// WHERE the setup happens — so the assertion reads the backend's own table rather than restating it.
// The ordering is derived the same way: a server that declares no `needs` works out of the box and
// must come before every one that declares something, so adding a needy server to the top trips this.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const panel = readFileSync(join(root, "components", "panels", "SettingsPanel.tsx"), "utf8");
const known = readFileSync(join(root, "api", "src", "kotoba", "core", "mcp", "known.py"), "utf8");

/** The dropdown's options, in the order they are painted. */
function options() {
  const at = panel.indexOf("function ConnectKnown");
  assert.notEqual(at, -1, "the dropdown moved — this pin reads it by name");
  const body = panel.slice(at, panel.indexOf("\n}", at));
  return [...body.matchAll(/<option value="([^"]*)">([^<]+)<\/option>/g)]
    .map(([, value, label]) => ({ value, label }))
    .filter((o) => o.value);
}

/** Each KNOWN_SERVERS entry's source text, by canonical name. */
function servers() {
  const body = known.slice(known.indexOf("KNOWN_SERVERS"), known.indexOf("_ALIASES"));
  const out = {};
  for (const [, name, spec] of body.matchAll(/\n {4}"([a-z-]+)": \{([\s\S]*?)\n {4}\},/g)) out[name] = spec;
  assert.ok(Object.keys(out).length >= 8, "the allowlist did not parse — the pin would pass on nothing");
  return out;
}

const canonical = (value) => value.replace(/\s+/g, "-");

test("everything that works out of the box comes before everything that needs something", () => {
  const specs = servers();
  const rows = options().map((o, i) => {
    const spec = specs[canonical(o.value)];
    assert.ok(spec, `the dropdown offers ${o.value}, which is not in KNOWN_SERVERS`);
    return { ...o, i, needs: /"needs"/.test(spec) };
  });
  const lastFree = Math.max(...rows.filter((r) => !r.needs).map((r) => r.i));
  const firstNeedy = Math.min(...rows.filter((r) => r.needs).map((r) => r.i));
  assert.ok(
    lastFree < firstNeedy,
    `a server that needs setup is offered above one that just works: ${JSON.stringify(rows)}`,
  );
});

test("google calendar is no longer the first thing a stranger clicks", () => {
  const rows = options();
  assert.notEqual(rows[0].value, "google calendar");
  assert.equal(rows[rows.length - 1].value, "google calendar",
    "it is the only row with no remedy inside this panel — it belongs last");
});

test("every row that needs something says so", () => {
  // One direction only: "Browser (Playwright)" carries a parenthetical that names which browser, not a
  // thing still missing. What must not happen is the reverse — a row that cannot connect yet and is
  // silent about it, which is the whole of this defect.
  const specs = servers();
  for (const row of options()) {
    if (!/"needs"/.test(specs[canonical(row.value)])) continue;
    assert.match(row.label, /\([^)]+\)\s*$/,
      `${row.value} cannot connect until the user does something, and its row does not say so`);
  }
});

test("its row says setup happens outside Kotoba, in the same words the 400 uses", () => {
  const row = options().find((o) => o.value === "google calendar");
  const suffix = row.label.match(/\(([^)]+)\)\s*$/);
  assert.ok(suffix, `the row must carry a suffix the way the sign-in rows do: ${row.label}`);
  assert.match(suffix[1], /Google Cloud/,
    "the suffix has to name where the setup happens, not merely that there is some");

  const spec = servers()["google-calendar"];
  assert.match(spec, /"setup":/, "the sentence the endpoint refuses with is gone");
  assert.ok(spec.includes("Google Cloud"),
    "the panel and known.setup_note now describe different places — that is the drift, back again");
});

test("the browser sign-in rows keep saying so, and are the rows that really need one", () => {
  const specs = servers();
  for (const row of options().filter((o) => /\(sign-in\)/.test(o.label))) {
    assert.match(specs[canonical(row.value)], /"oauth:/,
      `${row.value} is labelled (sign-in) and declares no oauth need`);
  }
  for (const [name, spec] of Object.entries(specs)) {
    if (!/"oauth:/.test(spec)) continue;
    const row = options().find((o) => canonical(o.value) === name);
    if (row) assert.match(row.label, /\(sign-in\)/, `${name} needs a sign-in and does not say so`);
  }
});
