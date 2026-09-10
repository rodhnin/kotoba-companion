// The brain and the model she thinks with are ONE decision. The panel has JSX, so nothing here can
// import it; it is read as text.
//
// Settings wrote `provider` and `model` as two unrelated `/api/settings/runtime` keys, so switching
// OpenAI to xAI while the model sat on gpt-5.6-luna left a pair that cannot work — reproduced against
// an isolated backend as `serves_model(...) == False`. The next turn asks api.x.ai for a model it does
// not serve, and `Test connection` then reports a perfectly good xai- key as a bad one. So
// `/api/setup/provider` pins a model the provider serves and `/api/setup/model` refuses one belonging
// to the other company, while still accepting an unlisted id the active provider could plausibly
// serve. The pin stays out of `set_runtime` on purpose: its other callers must not move.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(root, "components", "panels", "SettingsPanel.tsx"), "utf8");
const brain = src.slice(src.indexOf('<Section title="Brain"'), src.indexOf('<Section title="Capabilities"'));

test("the provider and the companion model go through the guarded pair", () => {
  assert.match(brain, /label="Provider"[\s\S]{0,160}setBrain\(\{ provider: v \}\)/);
  assert.match(brain, /label="Companion model \(voice\)"[\s\S]{0,200}setBrain\(\{ provider: [^,]+, model: v \}\)/);
  const setBrain = src.slice(src.indexOf("const setBrain"), src.indexOf("const setRuntime"));
  assert.match(setBrain, /"\/api\/setup\/provider"/);
  assert.match(setBrain, /"\/api\/setup\/model"/);
  assert.ok(!/setRuntime\("(provider|model)"/.test(src),
    "provider and model are the two keys that cannot be written independently");
});

test("the model rides with its brain, so the pair is one decision", () => {
  // Same reasoning as first run: /api/setup/model validates against the ACTIVE provider, and a request
  // that trusted a provider write to have landed would check the new provider's model against the old.
  assert.match(brain, /setBrain\(\{ provider: s\.runtime\.provider \|\| "openai", model: v \}\)/);
});

test("every other knob in the panel is untouched", () => {
  for (const key of ["base_url", "reasoning_effort", "expressive", "sandbox", "work_timeout",
                     "work_model", "code_model", "research_model", "utility_model"]) {
    assert.match(src, new RegExp(`setRuntime\\("${key}"`), `${key} left the raw runtime endpoint`);
  }
  // The per-role models are NOT this key. Their empty value MEANS inherit, and /api/setup/model refuses
  // an empty model outright — and would write `model`, the companion's, rather than the role's.
  assert.match(brain, /\{ v: "", t: "\(same as companion\)" \}/);
  assert.match(brain, /\{ v: "", t: "\(same as work\)" \}/);
});

test("typing an id the catalogue does not list still works", () => {
  const rows = brain.match(/allowCustom \/>/g) ?? [];
  assert.equal(rows.length, 5, "all five model menus still take a typed id");
  // /api/setup/model gives an unknown-shaped id the benefit of the doubt, so a typed one still lands;
  // only an id that MATCHES the other provider is refused. Verified against the backend, isolated.
  assert.match(src, /const opts = allowCustom && value && !options\.some/,
    "the typed id has to keep showing as the selected option");
});

test("a refusal is shown, not swallowed", () => {
  // Nothing is written optimistically, so a 400 leaves the select reading what is really configured,
  // and api() puts the backend's own sentence in the error banner.
  const setBrain = src.slice(src.indexOf("const setBrain"), src.indexOf("const setRuntime"));
  assert.ok(!/setS\(/.test(setBrain), "an optimistic write would leave a refused value on screen");
  assert.match(setBrain, /await api\(path, "POST", body\)/);
  assert.match(src, /const said = refusal\(await r\.json\(\)\.catch\(\(\) => null\)\);/,
    "the panel and first run read a refusal with the same parser");
  assert.match(src, /setError\(scrub\(said \|\| `Error \$\{r\.status\}`\)\)/);
  assert.match(src, /\{error && <ErrorBanner/, "and the banner is what draws it");
});

test("switching provider forgets the last connection test", () => {
  // A "✓ OpenAI · gpt-5.6-luna responded" left standing under a freshly picked xAI is a stale claim.
  const setBrain = src.slice(src.indexOf("const setBrain"), src.indexOf("const setRuntime"));
  assert.match(setBrain, /setLlmTest\(""\)/);
});

test("a refused runtime knob does not stay on screen either", () => {
  // The sibling of the test above, for the keys that DO write optimistically. setRuntime has to: a
  // select that snapped back for the length of a round trip would flicker on every change. But api()
  // re-reads /api/settings only when the write SUCCEEDED, so a 400 left the optimistic value sitting in
  // s.runtime with nothing to contradict it once the error banner cleared itself after five seconds —
  // and the controls are driven by s.runtime (NumField mirrors `value` into its draft, SelectField is
  // fully controlled), so the panel went on displaying a number the backend never took. Reachable from
  // every typed field in the panel: Base URL (400 "must start with http://"), the four work numbers
  // (400 "must be ≥ 30" / "must be ≥ 1") and any typed model id.
  const setRuntime = src.slice(src.indexOf("const setRuntime"), src.indexOf("const saveKey"));
  assert.match(setRuntime, /setS\(\(prev\)/, "the optimistic write is deliberate — it is the flicker fix");
  assert.match(setRuntime, /\.then\(\(ok\) =>/, "the result of the write has to be read at all");
  assert.match(setRuntime, /if \(!ok\) load\(\)/,
    "a refusal has to re-read the settings, or the screen keeps a value the backend refused");
});

test("a background reload does not clobber a personality field you are still typing", () => {
  // `load()` re-reads /api/settings and pushes personality straight into the three drafts, and `api()`
  // calls it after EVERY successful write — so typing a new Name and then flipping any toggle in the
  // panel (voice mode, a toolset, a plugin) silently reverted the Name before Save was ever pressed.
  // CommitField already guards its own draft while editing (`if (!editing) setDraft(value)`); the
  // Personality fields are driven by panel state and had nothing.
  //
  // The flag has to be dropped BEFORE the save, not after: `update_soul_config` silently refuses a
  // value that does not look like a name (the recurring "she's called 42"), so the read-back that
  // `api()` triggers on success is the only thing that stops the panel showing a name the backend
  // never took. On a refusal the guard goes back up and the typed text survives.
  const load = src.slice(src.indexOf("const load ="), src.indexOf("useEffect(() => {\n    if (open) load();"));
  assert.match(load, /personalityDirty\.current/, "the reload has to know somebody is typing");
  const save = src.slice(src.indexOf("const savePersonality"), src.indexOf("const saveKey"));
  assert.match(save, /personalityDirty\.current = false;[\s\S]{0,200}await api\(/,
    "clear it before the write, or the refused-name read-back is skipped");
  assert.match(save, /if \(!ok\) personalityDirty\.current = true;/,
    "and put it back on a refusal, or the next unrelated write eats the edit");
  assert.match(src, /onClick=\{savePersonality\}/, "the Save button has to go through it");
});
