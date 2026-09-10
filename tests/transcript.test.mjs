// The transcript, run against the REAL transcript rules (Node strips the types).
//
// In local voice mode a turn that opens with the tool heartbeat ("Mmm...") arrives as its own
// assistant_text frame. The frame opened the accumulator but its bubble was filtered away to nothing,
// so the NEXT frame arrived as a correction with no line of its own to correct — and the correction
// walked backwards until it found the PREVIOUS turn's answer and overwrote it. The user lost an answer
// they had already read, and the turn they were waiting on showed nothing. Agent mode never saw it:
// that SDK delivers one whole utterance per turn. The same hum landing mid-string was a second bug —
// anchored to the string start, the filler strip could not see it, so the bubble read
// "Déjame buscar eso.Mmm...Hmm...Listo…". These drive the real pipeline, at the level the user sees.
import assert from "node:assert/strict";

const { AgentTextStream, applyAgentCorrection, cleanText, stripAgentFiller } = await import(
  "../lib/transcript.ts"
);

// Mirrors components/CompanionExperience.tsx: appendLine + onAgentResponseCorrection.
function transcript(mode) {
  let lines = [];
  let seq = 0;
  const nextId = () => ++seq;
  return {
    get lines() {
      return lines;
    },
    get texts() {
      return lines.map((l) => `${l.from}:${l.text}`);
    },
    append(from, text) {
      let clean = cleanText(text);
      if (from === "ai") clean = stripAgentFiller(clean, mode);
      if (!clean) return;
      const last = lines[lines.length - 1];
      if (last && last.from === from && last.text === clean) return;
      lines = [...lines, { id: nextId(), from, text: clean }];
    },
    correct(text) {
      const corrected = stripAgentFiller(cleanText(text), mode);
      if (!corrected) return;
      lines = applyAgentCorrection(lines, corrected, () => ({ id: nextId(), from: "ai", text: corrected }));
    },
  };
}

// A local call: WS frames in, transcript out — exactly what lib/local-voice.ts wires together.
function localCall() {
  const stream = new AgentTextStream();
  const t = transcript("local");
  return {
    t,
    user: (text) => t.append("user", text),
    frame(text, turn) {
      const ev = stream.feed({ text, turn });
      if (!ev) return;
      if (ev.kind === "message") t.append("ai", ev.text);
      else t.correct(ev.text);
    },
  };
}

// ── The hum must not cost a bubble, and must never touch the turn before ──────────────────
{
  const c = localCall();
  c.user("¿Qué hora es en Tokio?");
  c.frame("Son las tres de la mañana allí.", 1);
  c.user("Búscame el horario del tren.");
  c.frame("Mmm...", 2); // the tool heartbeat, its own frame
  c.frame("Déjame buscar eso.", 2);

  assert.deepEqual(c.t.texts, [
    "user:¿Qué hora es en Tokio?",
    "ai:Son las tres de la mañana allí.",
    "user:Búscame el horario del tren.",
    "ai:Déjame buscar eso.",
  ]);
}

// A turn that is nothing but a hum leaves the transcript exactly as it was.
{
  const c = localCall();
  c.frame("Ya lo tengo.", 1);
  c.frame("Mmm...", 2);
  assert.deepEqual(c.t.texts, ["ai:Ya lo tengo."]);
}

// Every hum the backend can send (core/loop._NEUTRAL_FILLER) is one, including the H spellings.
for (const hum of ["Mmm...", "Hmm...", "Mm...", "Hmmm..."]) {
  const c = localCall();
  c.user("Busca eso.");
  c.frame(hum, 1);
  c.frame("Ya lo tengo.", 1);
  assert.deepEqual(c.t.texts, ["user:Busca eso.", "ai:Ya lo tengo."], `${hum} still costs a bubble`);
}

// ── A hum in the MIDDLE must not be spliced into her sentence ─────────────────────────────
{
  const c = localCall();
  c.frame("Déjame buscar eso.", 1);
  c.frame("Mmm...", 1);
  c.frame("Hmm...", 1);
  c.frame(" Listo, aquí está.", 1);
  assert.deepEqual(c.t.texts, ["ai:Déjame buscar eso. Listo, aquí está."]);
}

// A hum is only [mh] — a real word that starts with one is not filler.
{
  const c = localCall();
  c.frame("Mmm, déjame ver.", 1);
  assert.deepEqual(c.t.texts, ["ai:Mmm, déjame ver."]);
  assert.equal(new AgentTextStream().feed({ text: "Hola.", turn: 1 }).text, "Hola.");
}

// ── part 2: in local mode nothing may be filtered to nothing behind the accumulator's back ─
assert.notEqual(stripAgentFiller("Mmm...", "local"), "", "a local frame must never vanish silently");
assert.equal(stripAgentFiller("Mmm...", "agent"), "", "agent mode keeps the whole-utterance strip");

// Both filler regexes let `\s*` and `[.…?!\s]*` fight over the same whitespace,
// which is quadratic on a whitespace-leading string — measured 32 ms at 3.2k chars, 8.0 s at 51k,
// on her own output. cleanText's trim hides it from today's callers; `(?!\s)` removes it instead of
// relying on that. A budget an O(n^2) form cannot meet, so the shape cannot come back unnoticed.
for (const mode of ["local", "agent"]) {
  for (const pad of ["\t", " ", "\n", " "]) {
    const t0 = Date.now();
    stripAgentFiller(pad.repeat(60000) + "hola", mode);
    stripAgentFiller(pad.repeat(60000), mode);
    const ms = Date.now() - t0;
    assert.ok(ms < 250, `stripAgentFiller(${JSON.stringify(pad)}*60000, ${mode}) took ${ms}ms — the quadratic is back`);
  }
}
// …and the de-ambiguation must not have changed a single answer.
for (const [t, mode, want] of [
  ["Mmm...", "agent", ""], ["Mmm...", "local", "."], ["Mmm, déjame ver", "agent", "Mmm, déjame ver"],
  ["... ", "agent", ""], ["...", "local", "."], ["M", "agent", "M"], ["mmmm?", "agent", ""],
  ["Mmm... hola", "local", "hola"], ["Hola", "agent", "Hola"], ["   ", "agent", ""],
  ["", "agent", ""], [" Listo.", "local", "Listo."],
]) {
  assert.equal(stripAgentFiller(t, mode), want, `${mode} ${JSON.stringify(t)}`);
}

// ── part 3: a correction may rewrite the LAST line only, never one further back ────────────
{
  const lines = [
    { id: 1, from: "ai", text: "First answer." },
    { id: 2, from: "user", text: "Next question." },
  ];
  const out = applyAgentCorrection(lines, "Second answer.", () => ({ id: 3, from: "ai", text: "Second answer." }));
  assert.deepEqual(out.map((l) => `${l.from}:${l.text}`), [
    "ai:First answer.",
    "user:Next question.",
    "ai:Second answer.",
  ]);
  assert.equal(lines[0].text, "First answer.", "the answer already read must survive untouched");
}

// The ordinary case still corrects in place, and an identical correction is a no-op.
{
  const lines = [{ id: 1, from: "ai", text: "Hello" }];
  const out = applyAgentCorrection(lines, "Hello there", () => ({ id: 2, from: "ai", text: "Hello there" }));
  assert.deepEqual(out.map((l) => l.text), ["Hello there"]);
  assert.equal(applyAgentCorrection(out, "Hello there", () => ({ id: 3, from: "ai", text: "x" })), out);
}

// ── agent mode is untouched: one whole utterance per turn, same rules as before ────────────
{
  const t = transcript("agent");
  t.append("ai", "First answer.");
  t.append("user", "Next question.");
  t.append("ai", "Second answ");
  t.correct("Second answer.");
  assert.deepEqual(t.texts, ["ai:First answer.", "user:Next question.", "ai:Second answer."]);

  const only = transcript("agent");
  only.append("ai", "Mmm...");
  assert.deepEqual(only.texts, [], "a whole agent utterance that is only a hum shows nothing");
}

console.log("transcript.test.mjs: all assertions passed");
