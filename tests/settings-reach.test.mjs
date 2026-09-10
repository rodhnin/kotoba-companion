// The two Settings values the call shell reads for ITSELF, and when it re-reads them. The shell has
// JSX, so nothing here can import it; it is read as text.
//
// `voice_mode` and `elevenlabs_agent_id` are written by Settings but consumed by the call shell,
// which fetched /api/settings once from an effect with empty deps and never again: the control said
// "applies to the next call" and meant "applies to the next page load". Switching Local to the
// ElevenLabs agent still started a local call, and a first agent id still reached startSession as
// undefined, whereupon the notice blamed the id the person had just set correctly. The re-read is
// gated on the call being OFFLINE, because a live call has its transport latched and its agent id
// already handed over, and changing those props underneath it is the "createOffer" family.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const shell = readFileSync(join(root, "components", "CompanionExperience.tsx"), "utf8");
const panel = readFileSync(join(root, "components", "panels", "SettingsPanel.tsx"), "utf8");

test("the panel still writes the two values the shell reads for itself", () => {
  assert.match(panel, /setRuntime\("voice_mode", v\)/);
  assert.match(panel, /setRuntime\("elevenlabs_agent_id", v\)/);
  assert.match(panel, /Voice mode \(applies to the next call\)/,
    "the promise this test exists to keep — change the words and change the wiring with them");
});

test("the shell re-reads them, and not only once per mount", () => {
  const at = shell.indexOf("const refreshTransport");
  assert.notEqual(at, -1, "the settings read has to be something callable, not a mount effect");
  const fetcher = shell.slice(at, shell.indexOf("const providerProps"));
  assert.match(fetcher, /useCallback/, "and stable, or the effect that calls it would loop");
  assert.match(fetcher, /elevenlabs_agent_id/);
  assert.match(fetcher, /voice_mode/);
  assert.match(shell, /onTransportSettled=\{refreshTransport\}/,
    "a read nothing can call again is the defect: it runs once and Settings never reaches it");
});

test("it re-reads when the next call could start, and never mid-call", () => {
  const at = shell.indexOf("onTransportSettled();");
  assert.notEqual(at, -1, "nothing calls it, so nothing re-reads");
  const trigger = shell.slice(at - 400, at + 120);
  assert.match(trigger, /phase === "offline"/, "a live call has its transport latched — do not move it");
  assert.match(trigger, /!settingsOpen/, "wait until the panel is shut, or every keystroke re-reads");
});
