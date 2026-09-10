// Who may open /setup, and from where. The components have JSX, so nothing here can import them; they
// are read as text.
//
// /setup used to open for anybody who typed it, on any install. It writes her name, her language and
// two keys, so on a machine that already works it is a screen you can only lose by. The route now
// redirects to /app unless first run is actually needed, and the ONE way back in is the button in
// Settings → Brain. Two halves are easy to break separately: the redirect firing for everyone
// including a virgin install, which would lock a stranger out of the only door they have; and a second
// link to /setup appearing somewhere else, which quietly makes "only from that button" false.
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative } from "node:path";
import { test } from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (...p) => readFileSync(join(root, ...p), "utf8");
const door = read("components", "SetupDoor.tsx");
const page = read("app", "setup", "page.tsx");
const onboarding = read("components", "Onboarding.tsx");
const panel = read("components", "panels", "SettingsPanel.tsx");

test("the route stays a server component and hands off to the door", () => {
  assert.ok(!/"use client"/.test(page), "/setup must stay a server component to export its metadata");
  assert.match(page, /<SetupDoor \/>/, "the page renders the door, not the flow directly");
  assert.match(door, /^"use client";$/m, "the door needs the browser: a token, a fetch and a router");
});

test("a configured install is sent to /app, and without a history entry", () => {
  assert.match(door, /router\.replace\("\/app"\)/, "the redirect is missing");
  assert.ok(!/router\.push\("\/app"\)/.test(door), "push would leave /setup on the Back stack");
});

test("a virgin install opens whatever the URL says", () => {
  // The invitation gates the SECOND door only. Requiring it on a fresh clone would lock a stranger out
  // of the only screen that can configure the machine at all.
  const decision = door.slice(door.indexOf("const needed"), door.indexOf("return open"));
  assert.match(decision, /if \(needed\) setOpen\("first-run"\)/, "needed must be answered first");
  assert.ok(
    decision.indexOf("if (needed)") < decision.indexOf("deliberate"),
    "the first-run answer has to be read BEFORE the invitation, or a fresh clone needs one too",
  );
});

test("the invitation is read from the URL, so a reload keeps the person where they are", () => {
  assert.match(door, /invited\(window\.location\.search\)/);
  assert.ok(
    !/sessionStorage|localStorage/.test(door),
    "a one-shot handshake is consumed on the first mount, so a refresh mid-reconfiguration would eject " +
      "the one visitor this door exists for",
  );
});

test("exactly two places navigate to /setup, and only one of them is a control", () => {
  const files = [];
  const walk = (dir) => {
    for (const name of readdirSync(join(root, dir))) {
      const rel = join(dir, name);
      if (statSync(join(root, rel)).isDirectory()) walk(rel);
      else if (/\.tsx?$/.test(name)) files.push(rel);
    }
  };
  for (const d of ["app", "components", "lib"]) walk(d);

  const GOES = /(?:href=|location\.href\s*=|router\.(?:push|replace)\()\s*[{"'`]*(?:\/setup|SETUP_INVITE_URL)/;
  const found = files
    .filter((f) => f !== join("lib", "first-run.ts"))
    .filter((f) => read(f).split("\n").some((l) => GOES.test(l) && !l.trim().startsWith("*")))
    .map((f) => relative(".", f))
    .sort();
  assert.deepEqual(
    found,
    ["app/app/page.tsx", "components/panels/SettingsPanel.tsx"].sort(),
    "one of these is /app sending a VIRGIN install through first run and the other is the reconfigure " +
      "button; a third would make 'only from that button' false",
  );
});

test("the button lives with provider, model and keys, and says what it risks", () => {
  const brain = panel.slice(panel.indexOf('<Section title="Brain"'), panel.indexOf('<Section title="Capabilities"'));
  assert.match(brain, /<ReconfigureRow \/>/, "the control belongs in the section that owns the brain");
  const row = panel.slice(panel.indexOf("function ReconfigureRow"), panel.indexOf("function ListRow"));
  assert.match(row, /SETUP_INVITE_URL/, "the button must carry the invitation or the route bounces it");
  assert.match(row, /window\.location\.href/, "a full navigation, so a live call does not follow it in");
  assert.match(row, /overwrites only itself/, "it never says what re-running actually costs");
  assert.match(row, /skip keeps exactly what it had/, "a skipped step changing nothing is the reassurance");
});

test("the screen itself says what a second run overwrites", () => {
  const note = onboarding.match(/const RECONFIGURE_NOTE\s*=\s*\n?\s*"([^"]+)"/);
  assert.ok(note, "the reconfigure arrival needs its own note, the way the live Ready note has one");
  assert.match(note[1], /overwritten/);
  assert.match(note[1], /keeps exactly what it had/);
  assert.ok(!/pretend/.test(note[1]), "that is the rig's note, and this door writes real keys");

  const chooses = onboarding.match(/const note =[^;]+;/);
  assert.match(chooses[0], /reconfigure/, "the note table has to branch on the door, not only on the rig");
  assert.match(onboarding, /reconfigure \? "reconfiguring" : "first meeting"/,
    "the badge is what says which door this is on every step, not just the first");
});
