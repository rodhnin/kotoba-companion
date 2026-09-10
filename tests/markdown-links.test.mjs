/**
 * A bare `<ReactMarkdown>` emits plain anchors, and a plain anchor inside /app does not open a page:
 * it navigates the app away, dropping the call and the session with it. The rule had been written out
 * by hand in one renderer and forgotten in two others, so it now lives in `lib/markdown.tsx` and this
 * test refuses any renderer that does not take it from there.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : p.endsWith(".tsx") ? [p] : [];
  });
}

const files = [...walk("components"), ...walk("app")];

test("every markdown renderer takes its link rule from one place", () => {
  const renderers = files.filter((f) => /<ReactMarkdown\b/.test(readFileSync(f, "utf8")));
  assert.ok(renderers.length >= 3, "the renderers moved — this test is looking at nothing");

  for (const f of renderers) {
    const src = readFileSync(f, "utf8");
    assert.match(src, /components=\{mdComponents\(/,
      `${f} renders markdown without mdComponents — its links open in this tab, taking the app with them`);
    assert.match(src, /from "@\/lib\/markdown"/, `${f} does not import the rule it uses`);
  }
});

test("no anchor leaves the site in the tab the app is running in", () => {
  for (const f of files) {
    const src = readFileSync(f, "utf8");
    for (const tag of src.match(/<a\b[^>]*>/gs) ?? []) {
      const external = /https?:\/\//.test(tag) || /href=\{(href|viewUrl|request\.url)\}/.test(tag);
      if (!external || /\bdownload\b/.test(tag)) continue; // a download does not navigate
      assert.match(tag, /target="_blank"/, `${f}: an external link stays in this tab — ${tag.slice(0, 70)}`);
      // Without noopener the opened page keeps a handle on this one through window.opener.
      assert.match(tag, /rel="noopener noreferrer"/, `${f}: external link without rel=noopener — ${tag.slice(0, 70)}`);
    }
  }
});

test("nobody writes a style literal into the call — that silently defeats the memo", () => {
  // mdComponents keys its cache on the style OBJECT, so a literal in the JSX is a new key on every
  // render and the anchors go back to being unmounted and rebuilt. Measured: two calls returned
  // `a === a` false. Hoist the style to a module constant and pass the constant.
  const callers = files.filter((f) => /mdComponents\(/.test(readFileSync(f, "utf8")));
  assert.ok(callers.length >= 3, "the callers moved — this test is looking at nothing");

  for (const f of callers) {
    for (const call of readFileSync(f, "utf8").match(/mdComponents\([^)]*\)/gs) ?? []) {
      assert.doesNotMatch(call, /\{/,
        `${f}: mdComponents given a fresh object every render — hoist it — ${call.slice(0, 60)}`);
    }
  }
});

test("no window.open hands this page away either", () => {
  for (const f of files) {
    const src = readFileSync(f, "utf8");
    for (const call of src.match(/window\.open\([^;]*?\);/gs) ?? []) {
      // Same rule as the anchors, and it has to be asked for by name here: an anchor's `rel` does not
      // reach window.open, whose third argument is the only place noopener can be said.
      assert.match(call, /"noopener,noreferrer"/,
        `${f}: window.open without noopener — the opened tab keeps a handle on this one — ${call.slice(0, 90)}`);
    }
  }
});
