// `icons.tsx` opens by declaring the rule — hand-picked line icons, no emojis — and the interface
// drifted away from it three times anyway: a mic on the last line of the ElevenLabs guide and a page
// glyph on both attachment lines. An emoji is the one glyph the design cannot style, size or recolour,
// and it renders as somebody else's artwork on every platform. The design's own marks (✓ ✕ ⚠ ✦ ♥ ★)
// are typography and stay.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative } from "node:path";
import { test } from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const TREE = ["components", "app", "lib"];

// The pictographic planes and the selector that turns a plain mark into a picture, plus the handful
// of BMP characters every platform draws in full colour. Deliberately NOT the whole ✓✕⚠✦♥♡★ block:
// those are the interface's own typography and the kaomoji are built out of them.
const EMOJI = /[\u{1F000}-\u{1FAFF}\u{1F1E6}-\u{1F1FF}\u{FE0F}]|[☀☁☂☎☔☕♻⌚⌛⏰⏳⭐✅✉✨❌❗❤]/u;

function* sources(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* sources(path);
    else if (/\.tsx?$/.test(path)) yield path;
  }
}

test("no emoji reaches the interface", () => {
  const found = [];
  for (const base of TREE) {
    for (const path of sources(join(root, base))) {
      readFileSync(path, "utf8").split("\n").forEach((line, i) => {
        for (const ch of line) {
          if (EMOJI.test(ch)) {
            found.push(`${relative(root, path)}:${i + 1} ${JSON.stringify(ch)} — ${line.trim().slice(0, 70)}`);
          }
        }
      });
    }
  }
  assert.deepEqual(found, [], `use an icon from components/icons.tsx instead:\n${found.join("\n")}`);
});

test("the icons the emoji were replaced by are still exported", () => {
  const icons = readFileSync(join(root, "components", "icons.tsx"), "utf8");
  assert.match(icons, /export function FileTextIcon/);
  assert.match(icons, /export function MicIcon/);
  const transcript = readFileSync(join(root, "components", "TranscriptPanel.tsx"), "utf8");
  assert.match(transcript, /<FileTextIcon/, "an attachment with nothing to show is named by its icon");
});

console.log("no-emoji-in-the-interface.test.mjs: all assertions passed");
