// The bubble's pointer must be able to paint:
//   node --test tests/subagent-bubble-tails.test.mjs
//
// ProcessBubble's root carried `overflow: "hidden"` (needed to clip the white header into the
// rounded corners) with the two tail <span>s at top:100% as its children — and an absolutely
// positioned root is its absolute children's containing block, so everything below the box was
// statically clipped and the bubble pointed at nobody. ThinkingBubble, whose root never clips, kept
// painting its tail throughout; only ProcessBubble lost one. The clip and the tails therefore may
// never live on the same element — the wrapper owns placement and the tails, the inner box owns
// radius and clip.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(root, "components", "SubagentChibis.tsx"), "utf8");

/** The component's source from its `function NAME` line to the next column-0 closing brace. */
function component(name) {
  const at = src.indexOf(`function ${name}`);
  assert.ok(at >= 0, `${name} is gone`);
  return src.slice(at, src.indexOf("\n}", at));
}

/** The root JSX element's open tag: everything between `return (` and the first `>`. */
function rootTag(slice) {
  const at = slice.indexOf("return (");
  assert.ok(at >= 0);
  return slice.slice(at, slice.indexOf(">", at) + 1);
}

test("the tails hang off an element that never clips", () => {
  const bubble = component("ProcessBubble");
  assert.ok(!rootTag(bubble).includes('overflow: "hidden"'),
    "the root clips, so its tails at top:100% can never paint");
  assert.equal((bubble.match(/top: "100%"/g) ?? []).length, 2,
    "the ink tail and the cream tail");
});

test("the header clip that motivated the overflow is still somewhere inside", () => {
  const bubble = component("ProcessBubble");
  const tag = rootTag(bubble);
  const rest = bubble.slice(bubble.indexOf(tag) + tag.length);
  assert.ok(rest.includes('overflow: "hidden"'),
    "the white header still needs clipping into the rounded corners");
});

test("ThinkingBubble stays the control: no clip on its root, tails present", () => {
  const think = component("ThinkingBubble");
  assert.ok(!rootTag(think).includes('overflow: "hidden"'));
  assert.equal((think.match(/top: "100%"/g) ?? []).length, 2);
});
