import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const card = readFileSync(new URL("../components/panels/InputRequestPanel.tsx", import.meta.url), "utf8");

assert.match(card, /from "@\/lib\/approval-card"/, "the card must ask lib/approval-card, where a test can run it");
assert.match(card, /const grants = grantsOffered\(request\)/, "which grants are drawn is the module's answer");
assert.match(card, /post\(answerFor\(kind\)\)/, "what a grant sends is the module's answer");
assert.match(card, /const delivery = deliveryFor\(request, value\)/, "whether a value is posted or spoken is the module's answer");
assert.match(card, /addressed\(body, request\.requestId\)/, "request_id answers THIS card");
assert.match(card, /headlineOf\(label\)/, "the headline is the module's answer");
assert.doesNotMatch(card, /canAlways\s*[!=]==?/, "the flags are read in the module, never here");
assert.doesNotMatch(card, /approve\(true|approve\(false/, "a button names a GRANT KIND, never a wire triple");

for (const kind of ["yes", "no", "exact", "family"]) {
  assert.match(card, new RegExp(`\\b${kind}:`), `the ${kind} grant has a label`);
}
assert.match(card, /onClick=\{\(\) => approve\(kind\)\}/, "every grant button is drawn from the list, so none can be swapped by hand");
assert.doesNotMatch(card, /approve\("(yes|no|exact|family)"\)/, "no button hardcodes a kind beside a label");

assert.doesNotMatch(card, /Escape/, "no Esc-to-close on the approval card");
assert.doesNotMatch(card, /inset: 0[\s\S]{0,200}?onClick/, "the backdrop must not be a dismiss target");

assert.match(card, /if \(delivery\.kind === "post"\) \{\s*post\(delivery\.body\);\s*onResult\(\{ id, isApproval: false, value, posted: true \}\);/);

assert.match(card, /if \(sentRef\.current\) return;/);

assert.match(card, /<Card key=\{request\.id\}/, "a new card, or none, unmounts the one holding the typed value");
assert.doesNotMatch(card, /setValue\(""\)/, "the reset is structural now; a manual one would be the old shape creeping back");

console.log("approval-card-grants: ok");
