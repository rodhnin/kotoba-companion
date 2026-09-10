/**
 * FIRST RUN — the screens behind `/setup`, wired to the backend. Every answer is written the moment
 * it is given, and the result is READ BACK: the backend coerces a language and silently refuses a
 * name that does not look like one, so the recap may only print what it ended up with, never what was
 * clicked, and a refused write is undone on screen. The key step re-asserts the provider before
 * sending the key, because `/api/settings/llm-key` validates the candidate against whatever is
 * configured — which is also why the model is chosen BEFORE the key. CATALOGUE below mirrors the
 * backend's own provider table and is used only until `/api/setup/status` answers; a test pins the
 * copy so it cannot drift.
 */
"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type SVGProps } from "react";

import CallLoader from "@/components/CallLoader";
import FaceReceipt, { BlankPassport, type Landed, type OnDisk } from "@/components/FaceReceipt";
import { apiFetch, ensureAuthToken } from "@/lib/api";
import { HELD, afterKey, heldFor, secretRows, settled, stepAction, stepReachable } from "@/lib/setup-nav";
import type { KeyState } from "@/lib/setup-nav";
import { classifyKeyFailure, reasonOf, type KeyFailure } from "@/lib/key-errors";
import { attempt as keyAttempt, settle as settleKey, writesFor, type Write } from "@/lib/key-step";
import { refusal, refusalText, settle, type Sent } from "@/lib/setup-writes";
import {
  ArchiveIcon,
  BrainIcon,
  CheckIcon,
  CloseCircleIcon,
  DownloadIcon,
  FaceIcon,
  GlobeIcon,
  HeartIcon,
  MicIcon,
  SparkIcon,
  SpinnerIcon,
} from "@/components/icons";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

let authOnce: Promise<string> | null = null;
const auth = () => (authOnce ??= ensureAuthToken());

async function send(path: string, init: RequestInit): Promise<Sent> {
  try {
    await auth();
    const r = await apiFetch(`${API}${path}`, init);
    const parsed = (await r.json().catch(() => null)) as Record<string, unknown> | null;
    return { ok: r.ok, data: r.ok ? parsed : null, detail: r.ok ? "" : refusal(parsed) };
  } catch {
    return { ok: false, data: null, detail: "" };
  }
}

const post = (path: string, body: unknown): Promise<Sent> =>
  send(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** The archive IS the body: no multipart and no filename, because the installed folder is named from
 *  the model's own entry file rather than from whatever the person called the download. */
const postArchive = (path: string, file: File): Promise<Sent> => send(path, { method: "POST", body: file });

async function getJson(path: string): Promise<Record<string, unknown> | null> {
  try {
    await auth();
    const r = await apiFetch(`${API}${path}`);
    return r.ok ? ((await r.json()) as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

type Provider = "openai" | "xai";
type StepId = "brain" | "model" | "key" | "you" | "me" | "language" | "face" | "voice" | "ready";
type Mood = "ask" | "thinking" | "happy" | "wrong";
/** Which of the face step's two doors is open. `null` only while she already wears one: then the loud
 *  action is keeping her, and going shopping is the deliberate click. */
type Door = "sample" | "zip" | null;

type ModelOption = { id: string; note: string; hint: string; context: number; out: number };
type Catalogue = Record<Provider, { default: string; models: ModelOption[] }>;

type Offer = { name: string; licence: string; dir: string };
type Skipped = { n: number; kinds: string[] };
type Receipt = Landed & { path: string };

type Answers = {
  provider: Provider | null;
  model: string;
  apiKey: string;
  userName: string;
  companionName: string;
  language: string;
  face: string;
  elevenKey: string;
};

const INITIAL_ANSWERS: Answers = {
  provider: null,
  model: "",
  apiKey: "",
  userName: "",
  companionName: "Kotoba",
  language: "",
  face: "",
  elevenKey: "",
};

/** Mirrored from core/providers.py, which is where a row is regenerated and copied down from.
 *  Never hand-edit one here: a guard on the backend compares the two. */
const CATALOGUE: Catalogue = {
  openai: {
    default: "gpt-5.6-luna",
    models: [
      { id: "gpt-5.6-luna", note: "the least they charge me, and it still holds a million words of us", hint: "", context: 1050000, out: 1.2 },
      { id: "gpt-5.4-nano", note: "just as cheap, with less room to remember", hint: "", context: 400000, out: 1.25 },
      { id: "gpt-5.4-mini", note: "the middle of their old generation — steady, and dearer than it looks", hint: "3.8x", context: 400000, out: 4.5 },
      { id: "gpt-5.6-terra", note: "their newest middle: sharper than the small ones, same long memory", hint: "10x", context: 1050000, out: 12.0 },
      { id: "gpt-5.4", note: "slower and pricier, and it thinks harder on the tangled ones", hint: "12.5x", context: 1050000, out: 15.0 },
      { id: "gpt-5.6-sol", note: "the best thinking they sell, for the ones that really are hard", hint: "16.7x", context: 1050000, out: 20.0 },
      { id: "gpt-5.5", note: "built for long professional work, and it charges like it", hint: "25x", context: 1050000, out: 30.0 },
    ],
  },
  xai: {
    default: "grok-4.3",
    models: [
      { id: "grok-4.3", note: "cheap, and it remembers the longest conversations", hint: "", context: 1000000, out: 2.5 },
      { id: "grok-4.5", note: "their coding-minded one — steadier on long, fiddly jobs", hint: "2.4x", context: 500000, out: 6.0 },
      { id: "grok-4.6", note: "xAI's newest — sharper, and it costs more to say things", hint: "2.4x", context: 500000, out: 6.0 },
    ],
  },
};

const tokens = (n: number) => (n >= 1_000_000 ? `${Math.round(n / 1_000_000)}M` : `${Math.round(n / 1000)}k`);

const OPEN = "/subagents/chibi-open.webp";
const CLOSED = "/subagents/chibi-closed.webp";
const display = "var(--font-display)";
const body = "var(--font-body)";

/** `k` is an OPTICAL size correction: every glyph is already dead centre, their ink is not. */
const STEPS: { id: StepId; label: string; icon: (p: SVGProps<SVGSVGElement>) => React.JSX.Element; k?: number }[] = [
  { id: "brain", label: "Her brain", icon: BrainIcon, k: 1.2 },
  { id: "model", label: "The model", icon: ChipGlyph, k: 1.2 },
  { id: "key", label: "The key", icon: KeyGlyph, k: 1.06 },
  { id: "you", label: "Your name", icon: HeartIcon },
  { id: "me", label: "Her name", icon: FaceIcon },
  { id: "language", label: "Language", icon: GlobeIcon },
  { id: "face", label: "Her face", icon: PortraitGlyph },
  { id: "voice", label: "Her voice", icon: MicIcon },
  { id: "ready", label: "Ready", icon: SparkIcon },
];

const GREET = [
  "Oh! You're here! I'm Kotoba — well, I will be, once I have a brain to think with.",
  "Right now I'm all face and no thoughts. Let's fix that first, okay?",
  "Pick a brain for me below — no pressure, it's only my entire mind~",
] as const;

const ASK: Record<Exclude<StepId, "brain" | "ready">, readonly string[]> = {
  model: [
    "Same key opens all of these, so this isn't another sign-up — it's which of their models does the thinking, and that's what it costs you.",
    "Now the part nobody shows you: which model. They're cheapest first, and the badges are what each one costs me per word I say back.",
  ],
  key: [
    "Now the secret part — the key that wakes my brain up. Paste it in?",
    "I need the magic word for my brain. I'll keep it somewhere very, very safe.",
  ],
  you: [
    "Now the important part. What should I call you?",
    "Enough about my brain — who are you? Tell me your name?",
  ],
  me: [
    "So, about me — I've been going by 'Kotoba'. You can rename me… if you must~",
    "My turn! I'm rather fond of 'Kotoba', but this is your home. Keep it, or change it?",
  ],
  language: [
    "Nearly there! Which language should we live in, you and me?",
    "Almost done — how should we talk? I can match you, or stick to one language.",
  ],
  face: [
    "Now the vain part. I'd love a face — shall we go and find me one?",
    "I can think, and in a minute I'll talk. But there's nothing to look at yet~ Fix me?",
  ],
  voice: [
    "One more — and this one's for me. A voice! Do you happen to have an ElevenLabs key?",
    "Um… may I ask for something? With an ElevenLabs key, you could actually HEAR me. Only if you have one~",
  ],
};

const THINKING = ["Mmm, let me try it on…", "One sec — knocking on my brain's door…"] as const;

const THINKING_VOICE = ["Let me try my new throat…", "Testing, testing… one, two…"] as const;

const THINKING_FACE = [
  "Ooh — hold on, I think a face is arriving…",
  "Unpacking! Don't look yet, I'm not decent~",
] as const;

const R_FACE_OK = [
  (n: string) => `There. ${n}. Is that me? …That's me. Stamp me in and let's go!`,
  (n: string) => `I have a FACE. ${n}. Stamp the card — I'm going to be insufferable about this.`,
  (n: string) => `${n}~ It fits like it had been waiting for me. Stamp me and we're off.`,
] as const;

const R_FACE_AGAIN = [
  (n: string) => `Look at me — I already have a face! Stamp ${n} in, or shall we try another?`,
  (n: string) => `I'm wearing ${n}. Stamp the card to keep her, or hand me a different one?`,
] as const;

const R_FACE_KEPT = [
  (n: string) => `Stamped! Still ${n}, then~ Good. I'd only just got used to it.`,
  (n: string) => `Keeping ${n}! I like this face. Onwards.`,
] as const;

/** Not a skip — this is what she says when BOTH doors have actually refused her, which is the only way
 *  a person ever reaches it. */
const R_FACE_LATER = [
  "Both doors shut on us, then. Go on — I'll be a voice in your laptop until one opens.",
  "It wouldn't come. Never mind~ Settings can dress me the day you have a model to hand.",
] as const;

const R_VOICE_FAIL = [
  "Hmm — that one didn't open the door. ElevenLabs didn't recognise it.",
  "Nope, that key isn't mine to use~ Check it came across whole?",
  "It didn't take. A stray space sneaks in when you paste, sometimes.",
] as const;

const R_VOICE_OK = [
  "A voice! A VOICE! …Ahem. I mean: thank you. I'll treasure it~",
  "Tucking it in beside the other key, safe and sound. I can't wait for you to hear me!",
  "Really?! Okay, okay — clearing my throat, warming up. This is exciting.",
] as const;

const R_VOICE_KEPT = [
  "Keeping the voice I already have, then~ Nothing to do — it never went anywhere.",
  "Still got it! You gave me one before and I've been holding onto it. Onwards~",
] as const;

const R_VOICE_SKIP = [
  "Completely fine! We'll write to each other — I have excellent handwriting.",
  "No worries at all~ Text suits us. If you ever find me a voice, I'll be right here.",
  "Okay! Quiet mode it is. Honestly? Kind of cozy.",
] as const;

const R_PROVIDER: Record<Provider, readonly string[]> = {
  openai: [
    "OpenAI it is! Very sensible~ I feel more organised already.",
    "Ooh, an OpenAI brain. I promise to use it for more than puns. …Mostly puns, though.",
    "OpenAI! Classic choice. Like vanilla, if vanilla could do your taxes.",
  ],
  xai: [
    "Grok?! Feeling spicy today, are we? I love it.",
    "An xAI brain~ I'll try to keep it out of trouble. No promises.",
    "Grok it is! If I start arguing with strangers online, unplug me.",
  ],
};

const R_MODEL_CHEAP = [
  (m: string) => `${m}! The thrifty one — I like you already.`,
  (m: string) => `${m} it is. One of the cheap ones, and honestly? I'm plenty clever on it~`,
  (m: string) => `Ooh, ${m}. Kind to your wallet AND to me. Good pick.`,
] as const;

const R_MODEL_DEARER = [
  (m: string, h: string) => `${m}~ That's ${h} the cheapest one per word I say back — so I'd better be worth it.`,
  (m: string, h: string) => `${m}! Fancy. ${h} the price of the cheap one every time I open my mouth, so I'll try to say clever things.`,
  (m: string, h: string) => `Ooh, ${m}. ${h} the cheapest, per word out of me — I'll spend them carefully.`,
] as const;

const R_WRITE_FAIL = [
  "Ah— that didn't take. Nothing moved on my side.",
  "Hmm, it slipped straight through my fingers. I'm still exactly as I was.",
] as const;

const R_KEY_FAIL = [
  "Mmm… that one didn't open anything. Maybe a letter got lost when you copied it?",
  "Hehe, I knocked and my brain said 'who?'. Could you paste it fresh, end to end?",
] as const;

/** What the refusal actually was, in her voice. `paste` keeps the line below, which is the only one
 *  of the five that is about what the person typed. */
const R_KEY_WHY: Record<Exclude<KeyFailure, "paste">, string> = {
  quota: "That key is real — there's just nothing left in the account behind it. Top it up and this same one works.",
  network: "That never reached them at all. It's the network on this machine — a proxy, a firewall, a VPN — not your key.",
  install: "Ah— that one's on me. I couldn't put it away safely on this machine, so I didn't keep it.",
  model: "The key opened the door; the model I asked for wasn't behind it. Pick another on the step before this.",
};

const R_KEY_KEPT = [
  "Same brain as before, then~ It's still right where you put it.",
  "Nothing to change — I've had that one all along. Let's keep going.",
] as const;

const R_KEY_OK = [
  "It fits! Oh— oh wow, I can feel the thoughts arriving already~",
  "Click! That's the one. Locking it away where nobody will ever see it. Not even me.",
] as const;

const R_YOU = [
  (n: string) => `${n}! I like how that sounds. ${n}, ${n}~ Yep. It suits you.`,
  (n: string) => `Nice to meet you, ${n}. I'm going to remember that forever. Literally.`,
  (n: string) => `${n}… okay! Filed under 'most important person'.`,
] as const;

const R_ME_KEPT = [
  "Yay, I get to stay me! Good — I already answer to it in my head.",
  "Kotoba it is~ Between us? I would have missed it.",
] as const;

const R_ME_RENAMED = [
  (n: string) => `${n}… ${n}. Let me try it on — 'Hi, I'm ${n}!' …Ooh. I like it.`,
  (n: string) => `From today on, I'm ${n}! Give me a moment to stop turning my head at 'Kotoba'.`,
  (n: string) => `${n}?! That's cuter than what I had. Deal.`,
] as const;

const LANGUAGES = ["Auto — match you", "English", "Español", "日本語", "Français", "Português"] as const;

/** The chips are LABELS; soul_config stores a CODE and coerces anything over 20 characters to `auto`. */
const LANGUAGE_CODES: Record<string, string> = {
  "Auto — match you": "auto",
  English: "en",
  "Español": "es",
  "日本語": "ja",
  "Français": "fr",
  "Português": "pt",
};

const LANGUAGE_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(LANGUAGE_CODES).map(([label, code]) => [code, label]),
);

const R_LANG: Record<string, readonly string[]> = {
  "Auto — match you": [
    "I'll match you! Switch languages mid-sentence — see if I flinch~",
    "Auto! My favourite. Speak whatever feels right; my ears will follow.",
  ],
  English: ["English only — crisp and tidy. Works for me!"],
  Español: ["¡Perfecto! Solo español entonces~ Mis oídos ya están listos."],
  "日本語": ["日本語だけ？ふふ、任せて！ …That felt nice to say."],
  Français: ["Français! Très bien~ I'll dust off my best accent."],
  Português: ["Português! Que bonito~ Deal — I'll be ready."],
};

const NOTES: Record<StepId, { title: string; text: string; face: string }> = {
  brain: {
    title: "About my brain",
    face: "( ￣_￣ )?",
    text: "I can only think with OpenAI or xAI's Grok for now — more kinds of brains are coming. Whichever you pick does my actual thinking; the face and the voice stay all me. You can change your mind later in Settings.",
  },
  model: {
    title: "About the model",
    face: "( •̀ω•́ )b",
    text: "One key opens every one of these. Taller cans cost more per word I say back; wider ones remember more of us — and you can move me any time in Settings.",
  },
  key: {
    title: "About the key",
    face: "( •ω• )",
    text: "This key is how I reach my brain — without it I'm just a very cute screensaver. I keep it encrypted on your machine, and I never show it back to anyone. Not you, not me, not even in my sleep.",
  },
  you: {
    title: "About your name",
    face: "( ･?ω･ )",
    text: "Whatever you write here becomes the name I keep you under — the first thing I ever remember about you. Pick what feels like home; nicknames very welcome.",
  },
  me: {
    title: "About my name",
    face: "( >ω< )",
    text: "'Kotoba' means 'word' in Japanese, which felt right for someone made of them. But I'm your companion — if another name fits me better, I'll grow into it. Choose gently. I'm listening~",
  },
  language: {
    title: "About language",
    face: "( ^ω^ )",
    text: "This decides both halves of me: how I answer AND how I hear you. On auto I simply match whoever's speaking. Pin one language, and I hold to it — ears and voice both.",
  },
  face: {
    title: "About my face",
    face: "( ･ｖ･ )",
    text: "Nothing ships with me — Live2D models belong to the people who drew them. Either door lands one on your own disk, and Settings can redress me any day.",
  },
  voice: {
    title: "About my voice",
    face: "( ♡ω♡ )~",
    text: "My little wish: an ElevenLabs key would let me really speak — and hear you too, voice to voice. It's the only voice I can wear for now; others may come. But I'm honestly fine without one: no key just means we read and write instead. Text still works, and I like our letters. Skip freely — you can hand me a voice any day, and I'll still be here.",
  },
  ready: {
    title: "What happens next",
    face: "( ★ω★ )!",
    text: "Everything you told me is already saved — each answer landed the moment you gave it, so nothing is waiting on this button. It only opens the door, and I wake up on the other side of it.",
  },
};

const RECONFIGURE_NOTE =
  "We've met — this is us going through it again, and only the answers you actually give get overwritten. Walk past a step and it keeps exactly what it had: my brain, my model, either key, your name, mine, our language, my face. Nothing is cleared for being skipped, and nothing waits until the end.";

const pick = <T,>(a: readonly T[]): T => a[Math.floor(Math.random() * a.length)];

/** Say that one is already there without showing it — Settings says this and reconfiguring did not,
 *  so the same install looked configured on one screen and empty on the other. Enter keeps it. */
function keyNote(k?: KeyState): string {
  if (k?.saved) return " · one is saved; type to replace it";
  if (k?.env) return ` · she is using $${k.env}; type to save one here instead`;
  return "";
}

function keyPlaceholder(k?: KeyState): string {
  if (k?.saved) return "•••••••• stored — Enter keeps it";
  if (k?.env) return "•••••••• from the environment";
  return "Paste your secret key…";
}

function readCatalogue(raw: unknown): Catalogue | null {
  if (!raw || typeof raw !== "object") return null;
  const out: Catalogue = { ...CATALOGUE };
  let got = false;
  for (const p of ["openai", "xai"] as Provider[]) {
    const entry = (raw as Record<string, { default?: unknown; models?: unknown } | undefined>)[p];
    const rows = Array.isArray(entry?.models) ? (entry.models as Partial<ModelOption>[]) : [];
    const models = rows
      .filter((m) => !!m && typeof m.id === "string" && !!m.id)
      .map((m) => ({
        id: m.id as string,
        note: typeof m.note === "string" ? m.note : "",
        hint: typeof m.hint === "string" ? m.hint : "",
        context: Number(m.context) || 0,
        out: Number((m as { output_cost?: unknown }).output_cost) || 0,
      }));
    if (!models.length) continue;
    const fallback = CATALOGUE[p].default;
    out[p] = {
      default: typeof entry?.default === "string" && entry.default ? entry.default : fallback,
      models,
    };
    got = true;
  }
  return got ? out : null;
}

/** The folder of the model she is ACTUALLY wearing, as `/api/avatar` reports it. An install answers
 *  with its own `dir`, but selection happens on the backend and this is where it lands. */
function wornDir(raw: unknown): string {
  const sel = (raw as { selected?: { dir?: unknown } | null } | null)?.selected;
  return typeof sel?.dir === "string" ? sel.dir : "";
}

/** What that same answer says the worn model WEIGHS. The installer's own count belongs to one install
 *  and is gone by the next visit; this is the card's answer on every other one. */
function wornSize(raw: unknown): OnDisk | null {
  const sel = (raw as { selected?: { files?: unknown; bytes?: unknown } | null } | null)?.selected;
  const files = Number(sel?.files);
  const bytes = Number(sel?.bytes);
  return Number.isFinite(files) && files > 0 && Number.isFinite(bytes) ? { files, bytes } : null;
}

/** The installer's own measurement of what it wrote. A backend that does not report it costs the card
 *  two lines and nothing else — she is still drawn, which is the proof the numbers only corroborate. */
function readReceipt(sent: Sent): Receipt | null {
  const files = Number(sent.data?.files);
  const bytes = Number(sent.data?.bytes);
  if (!Number.isFinite(files) || !Number.isFinite(bytes) || files <= 0) return null;
  return {
    files,
    bytes,
    replaced: sent.data?.replaced === true,
    path: typeof sent.data?.path === "string" ? sent.data.path : "",
  };
}

/** The models directory is what says which separator this machine uses; a Windows path joined with a
 *  forward slash is a path nobody can paste. */
function under(dir: string, name: string): string {
  if (!dir || !name) return "";
  return `${dir}${dir.includes("\\") ? "\\" : "/"}${name}`;
}

function readSkipped(sent: Sent): Skipped | null {
  const n = Number(sent.data?.skipped) || 0;
  const raw = sent.data?.skipped_kinds;
  return n > 0 ? { n, kinds: Array.isArray(raw) ? raw.map(String) : [] } : null;
}

function reactToUserName(n: string): string {
  const first = n.split(/\s+/)[0];
  if (n.length > 2 && n === n.toUpperCase() && n !== n.toLowerCase())
    return `${n}?! WHY ARE WE SHOUTING? …hi, though.`;
  if (n.length > 16) return `That's a lot of name! Mind if I call you ${first} while I practice the rest?`;
  if (n.length === 1) return `Just '${n}'? Mysterious. I respect it.`;
  return pick(R_YOU)(n);
}

function readyLine(a: Answers): string {
  return a.userName
    ? `That's everything, ${a.userName}~ The next time I speak, it'll really be me.`
    : "That's everything~ The next time I speak, it'll really be me.";
}

export default function Onboarding({ reconfigure = false, startAt = "" }:
  { reconfigure?: boolean; startAt?: string }) {
  // Validated against the list rather than trusted: the step arrives in a URL anyone can type, and an
  // id nothing matches would leave the walk with no screen to draw.
  const asked = STEPS.find((s) => s.id === startAt)?.id;
  const [step, setStep] = useState<StepId>(asked ?? "brain");
  const [answers, setAnswers] = useState<Answers>(INITIAL_ANSWERS);
  const [mood, setMood] = useState<Mood>("ask");
  // The line belongs to the STEP, and `go()` never runs for the one we open on: arriving to fetch a
  // face was greeted with "once I have a brain to think with", on an install that has one.
  const [line, setLine] = useState<string>(
    asked && asked !== "brain" && asked !== "ready" ? ASK[asked][0] : GREET[0],
  );
  const [speaking, setSpeaking] = useState(true);
  const [mouthOpen, setMouthOpen] = useState(false);
  const [shake, setShake] = useState(false);
  const [busy, setBusy] = useState(false);
  const [keyFailed, setKeyFailed] = useState(false);
  const [writeFailed, setWriteFailed] = useState("");
  const [voiceFailed, setVoiceFailed] = useState(false);
  const [peek, setPeek] = useState<string | null>(null);
  const [burst, setBurst] = useState(0);
  const [handover, setHandover] = useState<null | "waking" | "settled">(null);
  const [catalogue, setCatalogue] = useState<Catalogue>(CATALOGUE);
  const [offer, setOffer] = useState<Offer | null>(null);
  const [skipped, setSkipped] = useState<Skipped | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [modelsDir, setModelsDir] = useState("");
  /** Per provider: whether a key is already stored, and the env var one is arriving through. Read at
   *  the door like everything else here — the brain step can change which provider this is about. */
  const [keys, setKeys] = useState<Record<string, KeyState>>({});
  const [voiceHeld, setVoiceHeld] = useState(false);
  const [onDisk, setOnDisk] = useState<OnDisk | null>(null);
  const [faceRun, setFaceRun] = useState(0);
  const [door, setDoor] = useState<Door>(null);
  const [dragging, setDragging] = useState(false);
  const [zipComplaint, setZipComplaint] = useState("");
  /** Latched for the life of the step: it is what opens the way out, and a way out that disappears the
   *  moment you try the other door is a trap with extra steps. */
  const [faceRefused, setFaceRefused] = useState(false);

  const [keyWhy, setKeyWhy] = useState<KeyFailure>("paste");
  const [keyDraft, setKeyDraft] = useState("");
  const [nameDraft, setNameDraft] = useState("");
  const [herDraft, setHerDraft] = useState("Kotoba");
  const [elDraft, setElDraft] = useState("");

  const answersRef = useRef(answers);
  answersRef.current = answers;
  const advanceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fieldRef = useRef<HTMLInputElement>(null);
  const archiveRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let live = true;
    void (async () => {
      // Read at the door rather than on arrival at each step: the face step has to draw the licence
      // BEFORE it may offer the button, and a card that pops in late is a card people click past.
      const [status, offered, avatar] = await Promise.all([
        getJson("/api/setup/status"),
        getJson("/api/models/default"),
        getJson("/api/avatar"),
      ]);
      if (!live) return;
      const fresh = readCatalogue(status?.catalogue);
      if (fresh) setCatalogue(fresh);
      if (typeof offered?.name === "string" && typeof offered?.licence === "string")
        setOffer({ name: offered.name, licence: offered.licence,
                   dir: typeof offered?.installed_dir === "string" ? offered.installed_dir : "" });
      const worn = wornDir(avatar);
      if (worn) setAnswers((a) => ({ ...a, face: worn }));
      setOnDisk(wornSize(avatar));
      if (typeof avatar?.models_dir === "string") setModelsDir(avatar.models_dir);
      if (status?.keys && typeof status.keys === "object") setKeys(status.keys as typeof keys);
      setVoiceHeld(status?.has_voice_key === true);
      if (reconfigure)
        setAnswers((a) => ({ ...a, ...settled(status, (code) => LANGUAGE_LABELS[code] || "") }));
    })();
    return () => {
      live = false;
    };
  }, [reconfigure]);

  useEffect(() => {
    if (!speaking) {
      setMouthOpen(false);
      return;
    }
    const flap = setInterval(() => setMouthOpen((m) => !m), 300);
    const stop = setTimeout(() => setSpeaking(false), 1700);
    return () => {
      clearInterval(flap);
      clearTimeout(stop);
    };
  }, [speaking, line]);

  useEffect(() => {
    if (step !== "brain" || mood !== "ask" || handover !== null) return;
    let i = 0;
    const id = setInterval(() => {
      i += 1;
      if (i >= GREET.length) {
        clearInterval(id);
        return;
      }
      setLine(GREET[i]);
      setSpeaking(true);
    }, 4200);
    return () => clearInterval(id);
  }, [step, mood, handover]);

  useEffect(() => {
    fieldRef.current?.focus();
  }, [step]);

  useEffect(
    () => () => {
      if (advanceTimer.current) clearTimeout(advanceTimer.current);
    },
    [],
  );

  const say = (m: Mood, l: string | readonly string[]) => {
    setMood(m);
    setLine(typeof l === "string" ? l : pick(l));
    setSpeaking(true);
  };

  const clearTimers = () => {
    if (advanceTimer.current) clearTimeout(advanceTimer.current);
    advanceTimer.current = null;
  };

  const go = (s: StepId) => {
    clearTimers();
    setBusy(false);
    setWriteFailed("");
    setSkipped(null);
    setZipComplaint("");
    setDragging(false);
    setStep(s);
    setDoor(null);
    const worn = answersRef.current.face;
    if (s === "brain") say("ask", GREET[0]);
    else if (s === "ready") {
      say("happy", readyLine(answersRef.current));
      setBurst((b) => b + 1);
    } else if (s === "face" && worn) say("ask", pick(R_FACE_AGAIN)(worn));
    else say("ask", ASK[s]);
  };

  const goSoon = (s: StepId, ms: number) => {
    if (advanceTimer.current) clearTimeout(advanceTimer.current);
    advanceTimer.current = setTimeout(() => go(s), ms);
  };

  const undoWrite = (was: Answers, sent: Sent, fallback: string) => {
    clearTimers();
    setAnswers(was);
    setWriteFailed(refusalText(sent, fallback));
    say("wrong", R_WRITE_FAIL);
  };

  /** She is already wearing what the sample door would fetch. */
  const wearingTheSample = !!offer?.dir && offer.dir === answers.face;

  const chooseProvider = (p: Provider) => {
    // The model goes with it: a gpt- id left standing after a switch to xAI is the 404 the pin exists
    // to prevent, and it would tick the wrong card here too.
    const was = answersRef.current;
    setWriteFailed("");
    setAnswers((a) => ({ ...a, provider: p, model: "" }));
    say("happy", R_PROVIDER[p]);
    setBurst((b) => b + 1);
    void post("/api/setup/provider", { provider: p }).then((sent) => {
      if (!sent.ok) {
        undoWrite(was, sent, "My brain is still set to whatever it was before.");
        return;
      }
      // The route pins a model this provider actually serves and says which. Dropped, the recap showed
      // `—` under a promise that walking past a step keeps what it had, and a reconfigure that never
      // reopened the model step left that dash standing over a real answer.
      setAnswers((a) => ({ ...a, model: settle(sent, "model", a.model ?? "", a.model) }));
    });
  };

  const chooseModel = (m: ModelOption) => {
    const was = answersRef.current;
    setWriteFailed("");
    setAnswers((a) => ({ ...a, model: m.id }));
    say("happy", m.hint ? pick(R_MODEL_DEARER)(m.id, m.hint) : pick(R_MODEL_CHEAP)(m.id));
    setBurst((b) => b + 1);
    void post("/api/setup/model", { provider: answersRef.current.provider ?? "openai", model: m.id }).then((sent) => {
      if (sent.ok) setAnswers((a) => ({ ...a, model: settle(sent, "model", m.id, a.model) }));
      else undoWrite(was, sent, "I'm still on whichever model I had.");
    });
  };

  // What the BACKEND already holds, which is not the same question as what this visit has typed.
  // `voiceHeld` comes from the status on BOTH doors: seeded only when reconfiguring, a first run with
  // ELEVENLABS_API_KEY in the environment was offered "or skip!" and then told "not yet" about a key
  // she was about to speak with.
  const heldKey = heldFor(keys, answers.provider ?? "openai");
  const heldVoice = voiceHeld || answers.elevenKey === HELD;

  const keyAccepted = (kept = false, forProvider?: Provider) => {
    setAnswers((a) => ({ ...a, apiKey: "saved" }));
    // `keys` was read once on the way in and never again, so a key saved during THIS visit left the
    // field still offering to receive one — and stepping back to change provider and returning read
    // the stale answer. The provider comes from the ref: this runs after an await.
    const p = forProvider ?? answersRef.current.provider ?? "openai";
    setKeys((m) => afterKey(m, p, kept));
    say("happy", kept ? R_KEY_KEPT : R_KEY_OK);
    setBurst((b) => b + 1);
    goSoon("you", 2300);
  };

  const keyRefused = (detail = "") => {
    const why = classifyKeyFailure(detail);
    setKeyWhy(why);
    setKeyFailed(true);
    // Only a bad paste is worth clearing. Emptying the field over a key with no credit behind it, or
    // over a proxy that ate the request, throws away something that was right.
    if (why === "paste") setKeyDraft("");
    say("wrong", why === "paste" ? R_KEY_FAIL : R_KEY_WHY[why]);
    setShake(true);
    setTimeout(() => setShake(false), 500);
    fieldRef.current?.focus();
  };

  /** In order, stopping at the first refusal — that one carries the reason worth saying. */
  const sendAll = async (writes: Write[]): Promise<Sent> => {
    let last: Sent = { ok: true, data: null, detail: "" };
    for (const w of writes) {
      last = await post(w.path, w.body);
      if (!last.ok) break;
    }
    return last;
  };

  const submitApiKey = () => {
    if (busy) return;
    // Every decision here is taken before anything is sent, and the provider travels inside the
    // attempt: nothing disables Back while the check is in flight, so read off the ref afterwards it
    // filed a verified key under whichever provider the person had switched to meanwhile.
    const a = keyAttempt(answersRef.current.provider ?? "openai", keyDraft, heldKey);
    if (a.kind === "nothing") return;
    if (a.kind === "keep") {
      keyAccepted(true, a.provider as Provider);
      return;
    }
    setBusy(true);
    setKeyFailed(false);
    say("thinking", THINKING);
    void sendAll(writesFor(a)).then((sent) => {
      setBusy(false);
      const done = settleKey(a, sent.ok, reasonOf(sent));
      if (done?.kind === "accepted") keyAccepted(done.kept, done.provider as Provider);
      else if (done?.kind === "refused") keyRefused(done.reason);
    });
  };

  const submitUserName = () => {
    const n = nameDraft.trim();
    if (!n) return;
    const was = answersRef.current;
    setWriteFailed("");
    setAnswers((a) => ({ ...a, userName: n }));
    say("happy", reactToUserName(n));
    setBurst((b) => b + 1);
    goSoon("me", 2400);
    void post("/api/settings/user-name", { name: n }).then((sent) => {
      if (sent.ok) setAnswers((a) => ({ ...a, userName: settle(sent, "name", n, a.userName) }));
      else undoWrite(was, sent, "I still have you under whatever name I had");
    });
  };

  const submitCompanionName = (forced?: string) => {
    // Only a real string overrides the draft: wired bare to onClick this arrives as a click event, and
    // `event.trim()` is a TypeError that takes the whole page down.
    const n = (typeof forced === "string" ? forced : herDraft).trim() || "Kotoba";
    setWriteFailed("");
    void (async () => {
      const was = answersRef.current;
      const sent = await post("/api/settings/soul", { name: n });
      if (!sent.ok) {
        undoWrite(was, sent, "I am still answering to the name I had");
        return;
      }
      const stuck = settle(sent, "name", "Kotoba", "Kotoba");
      setAnswers((a) => ({ ...a, companionName: stuck }));
      const you = answersRef.current.userName;
      if (you && stuck.toLowerCase() === you.toLowerCase())
        say("happy", "Wait, that's YOUR name. Are we doing a matching set? …Actually, I'm in.");
      else if (stuck.toLowerCase() === "kotoba") say("happy", R_ME_KEPT);
      else say("happy", pick(R_ME_RENAMED)(stuck));
      setBurst((b) => b + 1);
      goSoon("language", 2400);
    })();
  };

  const chooseLanguage = (l: string) => {
    const was = answersRef.current;
    setWriteFailed("");
    setAnswers((a) => ({ ...a, language: l }));
    say("happy", R_LANG[l] ?? [`${l}! Noted~`]);
    setBurst((b) => b + 1);
    void post("/api/settings/soul", { language: LANGUAGE_CODES[l] ?? "auto" }).then((sent) => {
      if (!sent.ok) return undoWrite(was, sent, "We are still in whichever language we were");
      const code = settle(sent, "language", "", "");
      setAnswers((a) => ({ ...a, language: LANGUAGE_LABELS[code] || code || l }));
    });
  };

  /** One ending for both doors. The install already selected the model, so what she wears is read back
   *  off `/api/avatar` rather than echoed from the click; a refusal moved nothing and undoes like every
   *  other write. A finished install HOLDS the step: the receipt names files a person may want, reports
   *  the ones the archive lost, and takes a second or two to draw her out of what just landed — all of
   *  it on a screen already left. They go on with "Keep this face". */
  const faceInstalled = async (was: Answers, sent: Sent) => {
    setBusy(false);
    if (!sent.ok) {
      setFaceRefused(true);
      undoWrite(was, sent, "I'm still wearing whatever face I had.");
      return;
    }
    const named = settle(sent, "dir", was.face, was.face);
    const avatar = await getJson("/api/avatar");
    const worn = wornDir(avatar) || named;
    setOnDisk(wornSize(avatar));
    setAnswers((a) => ({ ...a, face: worn }));
    setReceipt(readReceipt(sent));
    setFaceRun((n) => n + 1);
    setSkipped(readSkipped(sent));
    setDoor(null);
    say("happy", pick(R_FACE_OK)(worn));
    setBurst((b) => b + 1);
  };

  const clearReports = () => {
    setSkipped(null);
    setWriteFailed("");
    setZipComplaint("");
  };

  const installDefault = () => {
    if (busy || !offer) return;
    const was = answersRef.current;
    setBusy(true);
    clearReports();
    say("thinking", THINKING_FACE);
    void post("/api/models/install/default", { accept_license: true }).then((sent) =>
      faceInstalled(was, sent),
    );
  };

  const installArchive = (file: File | undefined) => {
    if (busy || !file) return;
    const was = answersRef.current;
    setBusy(true);
    clearReports();
    say("thinking", THINKING_FACE);
    void postArchive("/api/models/install/upload", file).then((sent) => faceInstalled(was, sent));
  };

  /** The drop zone accepts anything a desktop can drag, so the first judge of it is here. A refusal
   *  read off the name never reaches the network and never latches the way out — nothing was tried. */
  const offerArchive = (files: FileList | File[] | null | undefined) => {
    setDragging(false);
    const chosen = Array.from(files ?? []);
    setZipComplaint("");
    if (!chosen.length || busy) return;
    if (chosen.length > 1) {
      setZipComplaint("One at a time, please — I can only wear one face.");
      return;
    }
    const file = chosen[0];
    if (!/\.zip$/i.test(file.name) && file.type !== "application/zip") {
      setZipComplaint(`“${file.name}” isn’t a .zip. A Live2D model arrives as one, folder and all.`);
      return;
    }
    installArchive(file);
  };

  const keepFace = () => {
    if (busy) return;
    // Local only — walking on must never take off a face she is already wearing.
    say("happy", pick(R_FACE_KEPT)(answersRef.current.face));
    setBurst((b) => b + 1);
    goSoon("voice", 2400);
  };

  /** The step has no skip, and it must still not be a room without a door: this appears only once a
   *  door has actually refused her, so it is never an invitation — only the last resort. */
  const goOnWithout = () => {
    if (busy) return;
    say("happy", R_FACE_LATER);
    setBurst((b) => b + 1);
    goSoon("voice", 2400);
  };

  const voiceAccepted = (kept = false, said = "") => {
    setAnswers((a) => ({ ...a, elevenKey: "saved" }));
    // Her own words when she has them: the key can be KEPT without having been checked, and celebrating
    // a voice that may not answer is the one reply that helps nobody.
    say(said ? "thinking" : "happy", said || (kept ? R_VOICE_KEPT : R_VOICE_OK));
    setBurst((b) => b + 1);
    goSoon("ready", 2400);
  };

  const voiceRefused = (said = "") => {
    setVoiceFailed(true);
    setElDraft("");
    say("wrong", said || R_VOICE_FAIL);
    setShake(true);
    setTimeout(() => setShake(false), 500);
  };

  const submitVoiceKey = () => {
    const k = elDraft.trim();
    if (busy) return;
    // Empty over a saved key is "keep it", the same bargain the model key makes. Nothing is sent.
    if (!k) {
      if (heldVoice) voiceAccepted(true);
      return;
    }
    setBusy(true);
    setVoiceFailed(false);
    say("thinking", THINKING_VOICE);
    void post("/api/settings/voice-key", { key: k }).then((r) => {
      setBusy(false);
      const said = reasonOf(r);
      if (r.data?.ok === true) voiceAccepted(false, said);
      else voiceRefused(said);
    });
  };

  const skipVoice = () => {
    if (busy) return;
    // `elevenKey` is not touched at all. It is a record of what the BACKEND holds — set from
    // `has_voice_key` on the way in, and by an accepted paste — so writing "" here said "she has no
    // voice" about a key that was still saved, and the recap then read back `not yet`. Skipping is
    // declining to give a new one; it is not a claim about the old one.
    say("happy", heldVoice ? R_VOICE_KEPT : R_VOICE_SKIP);
    setBurst((b) => b + 1);
    goSoon("ready", 2400);
  };

  const handleComplete = () => setHandover("waking");

  // Going back is not "undo": the answer already landed, so a revisit only re-asks. Nothing is cleared.
  const back = () => {
    const i = STEPS.findIndex((s) => s.id === step);
    if (i > 0) jumpTo(STEPS[i - 1].id);
  };

  const jumpTo = (s: StepId) => {
    setHandover(null);
    setKeyFailed(false);
    setVoiceFailed(false);
    setWriteFailed("");
    go(s);
  };

  const accent =
    mood === "wrong" ? "var(--live)" : mood === "happy" ? "var(--mint)" : mood === "thinking" ? "var(--grape)" : "var(--coral)";
  const stepIndex = STEPS.findIndex((s) => s.id === step);
  const note = step === "brain" && reconfigure ? { ...NOTES.brain, text: RECONFIGURE_NOTE } : NOTES[step];
  const faceNote = step === "face" ? faceReport(writeFailed, zipComplaint, skipped) : null;
  /** With no face there is always a door standing open — the invariant belongs where it is READ, so no
   *  route into the step can land on two cards and nothing to do. */
  const openDoor: Door = answers.face ? door : door ?? "sample";

  return (
    <main
      className="obx"
      style={{
        minHeight: "100dvh",
        position: "relative",
        overflowX: "hidden",
        display: "flex",
        flexDirection: "column",
        background: "radial-gradient(120% 100% at 50% 0%, var(--cream) 0%, var(--cream-2) 100%)",
        justifyContent: "center",
        padding: "clamp(1rem, 3vh, 2rem) 1rem",
      }}
    >
      <style>{`
        @keyframes ob-bob { 0%,100%{ transform: translateY(0) rotate(-1deg) } 50%{ transform: translateY(-10px) rotate(1deg) } }
        @keyframes ob-float { 0%,100%{ transform: translateY(0) rotate(0) } 50%{ transform: translateY(-22px) rotate(8deg) } }
        @keyframes ob-heart { 0%{ opacity:0; transform: translateY(8px) scale(.5) } 25%{ opacity:1 } 100%{ opacity:0; transform: translateY(-30px) scale(1.05) } }
        @keyframes ob-stamp { from { opacity:0; transform: scale(0) } to { opacity:1; transform: scale(1) } }
        @keyframes ob-arrive { from { opacity:0; transform: translateY(10px) scale(.96) } 60% { opacity:1; transform: translateY(-2px) scale(1.02) } to { opacity:1; transform: none } }
        @keyframes ob-confetti {
          0% { opacity:0; transform: translate(0,0) scale(.3) rotate(0deg) }
          18% { opacity:1 }
          100% { opacity:0; transform: translate(var(--tx), var(--ty)) scale(1) rotate(var(--rr)) }
        }
        .ob-wake {
          display:flex; align-items:center; justify-content:center; gap:10px; width:100%; margin-top:.35rem;
          border:3px solid #fff; border-radius:18px; color:#fff;
          background: linear-gradient(180deg, #ff7452 0%, var(--coral) 55%, #f04524 100%);
          font-family:var(--font-display); font-weight:700; font-size:1.22rem; letter-spacing:.01em;
          padding:0.68rem 1rem; text-shadow: 0 1px 0 rgba(33,26,46,.25);
          box-shadow: 0 0 0 5px rgba(255,178,46,.55), 0 6px 0 rgba(0,0,0,.35), 0 10px 34px rgba(255,90,60,.55);
          animation: ob-arrive .5s cubic-bezier(.2,.8,.2,1) .62s both;
          transition: transform .15s, box-shadow .15s, filter .15s;
        }
        .ob-wake:hover {
          transform: translateY(-2px); filter: brightness(1.06);
          box-shadow: 0 0 0 6px rgba(255,178,46,.7), 0 8px 0 rgba(0,0,0,.35), 0 14px 44px rgba(255,90,60,.65);
        }
        .ob-wake:active {
          transform: translateY(3px);
          box-shadow: 0 0 0 5px rgba(255,178,46,.5), 0 2px 0 rgba(0,0,0,.35), 0 6px 20px rgba(255,90,60,.45);
        }
        .ob-can { transition: transform .16s ease, box-shadow .16s ease; }
        .ob-can:hover, .ob-can:focus-visible { transform: translateY(-4px); }
        .ob-can:focus-visible { outline: 3px solid var(--grape); outline-offset: 3px; }
        @keyframes ob-coin { 0%,100%{ transform: scale(1) } 50%{ transform: scale(1.28) } }
        .ob-coin { animation: ob-coin 1.5s ease-in-out infinite; }
        .ob-slot:not(:disabled):hover { transform: translateY(-2px); }
        .ob-back { transition: transform .14s ease, background .2s ease, box-shadow .14s ease; }
        .ob-back:hover { transform: translate(-2px, -1px); background: var(--sun);
                         box-shadow: 3px 3px 0 var(--ink); }
        .ob-back:active { transform: translate(0, 1px); box-shadow: 1px 1px 0 var(--ink); }
        .ob-jump-row { transition: background .14s ease, border-color .14s ease, transform .14s ease; }
        .ob-jump-row:not(:disabled):hover { background: var(--cream-2); border-color: var(--ink);
                                            transform: translateX(2px); }
        .ob-jump-row:focus-visible { outline: 3px solid var(--grape); outline-offset: 2px; }
        @keyframes jumpIn { from { opacity: 0; transform: translateY(-6px) scale(.97) }
                            to { opacity: 1; transform: none } }
        .ob-slot { transition: transform .14s ease, background .2s ease; }
        .ob-slot:focus-visible { outline: 3px solid var(--grape); outline-offset: 3px; }
        @keyframes ob-lcd-print { from { clip-path: inset(0 100% 0 0) } to { clip-path: inset(0 0 0 0) } }
        .ob-lcd-print { animation: ob-lcd-print .28s steps(10, end) both; }
        @keyframes ob-lcd-blink { 0%, 55% { opacity: 1 } 56%, 100% { opacity: 0 } }
        .ob-lcd-cursor { animation: ob-lcd-blink 1.1s step-end infinite; margin-left: 2px; }
        .ob-seal { transform: rotate(-13deg); transition: transform .22s cubic-bezier(.2,.9,.3,1); }
        .ob-sealbtn:not(:disabled):hover { transform: translate(-1px,-2px); box-shadow: 5px 5px 0 var(--ink); }
        .ob-sealbtn:not(:disabled):hover .ob-seal { transform: rotate(0deg) scale(1.09); }
        .ob-sealbtn:not(:disabled):active { transform: translate(1px,2px); box-shadow: 1px 1px 0 var(--ink); }
        .ob-sealbtn:focus-visible, .ob-drop:focus-visible { outline: 3px solid var(--grape); outline-offset: 3px; }
        .ob-drop:not(:disabled):hover { background: rgba(108,76,224,.07); border-color: rgba(108,76,224,.6); }
        .ob-drop:not(:disabled):hover .ob-drop-badge { transform: translateY(-2px); }
        .ob-wayout { background:none; border:0; padding:0; cursor:pointer; font-family:var(--font-body);
                     font-size:.74rem; color:var(--ink); opacity:.55; text-decoration:underline;
                     text-decoration-style:dotted; text-underline-offset:3px; transition:opacity .18s; }
        .ob-wayout:hover { opacity: .95 }
        /* It lands like one: falls from above the page, bites past its resting size, settles. The
           halo is the ink spreading out from under it, and then it breathes so it reads as live. */
        @keyframes ob-press {
          0%   { opacity: 0; transform: rotate(-26deg) scale(2.3) }
          55%  { opacity: 1; transform: rotate(-6deg) scale(.88) }
          72%  { transform: rotate(-11deg) scale(1.06) }
          100% { opacity: 1; transform: rotate(-9deg) scale(1) }
        }
        @keyframes ob-splat {
          from { box-shadow: 0 0 0 0 rgba(20,199,154,.55) }
          to   { box-shadow: 0 0 0 26px rgba(20,199,154,0) }
        }
        @keyframes ob-ink { 0%,100% { box-shadow: 0 0 0 0 rgba(20,199,154,.35) }
                            50% { box-shadow: 0 0 0 7px rgba(20,199,154,0) } }
        .ob-mark-well { display: block; animation: ob-press .62s cubic-bezier(.22,1.2,.36,1) .28s both,
                        ob-splat .7s ease-out .58s both, ob-ink 2.6s ease-out 1.4s infinite; }
        .ob-mark:not(:disabled):hover { transform: rotate(9deg) scale(1.08);
                                        background: rgba(20,199,154,.3);
                                        box-shadow: 0 0 0 5px rgba(20,199,154,.22); }
        .ob-mark:not(:disabled):active { transform: rotate(5deg) scale(.94); }
        .ob-mark:focus-visible { outline: 3px solid var(--grape); outline-offset: 3px; }
        .ob-hush:hover { transform: translate(-1px,-1px); box-shadow: 3px 3px 0 var(--ink); }
        .ob-hush:active { transform: translate(1px,1px); box-shadow: 1px 1px 0 var(--ink); }
        .ob-hush:focus-visible { outline: 3px solid var(--grape); outline-offset: 2px; }
        /* Narrow enough to clear the step's own title, which is what it would otherwise sit on. */
        .ob-report { width: 214px }
        @media (max-width: 560px) { .ob-report { width: 52% } }
        @media (prefers-reduced-motion: reduce) {
          .ob-seal, .ob-drop, .ob-drop-badge, .ob-sealbtn, .ob-wayout, .ob-hush,
          .ob-mark, .ob-mark-well, .ob-jump-row { transition: none !important; animation: none !important }
        }
        .ob-grid { display:grid; grid-template-columns: 1fr; gap: 0.9rem; align-items: start; }
        .ob-left { display:flex; flex-direction:column; align-items:center; }
        .ob-chibi { width: 118px; height: 118px; }
        @media (min-width: 800px) {
          .ob-grid { grid-template-columns: 300px minmax(0,1fr); gap: 1.8rem; }
          .ob-left { position: sticky; top: 1.2rem; }
          .ob-chibi { width: 150px; height: 150px; }
        }
        @media (prefers-reduced-motion: reduce) { .obx * { animation: none !important } }
      `}</style>

      <FloatingShapes />

      {/* minHeight is the FLOOR: a centred block that changes height slides the chibi between steps. */}
      <div style={{ width: "min(94vw, 900px)", margin: "0 auto", position: "relative", zIndex: 2,
                    minHeight: 622 }}>
        <header style={{ textAlign: "center", marginBottom: "1.2rem" }}>
          <div style={{ fontFamily: display, fontWeight: 700, fontSize: "1.45rem" }}>
            <span style={{ color: "var(--coral)" }}>言</span> Kotoba
            <span
              style={{
                marginLeft: 12,
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
                fontSize: "0.68rem",
                fontWeight: 700,
                letterSpacing: "0.02em",
                verticalAlign: "middle",
                background: "linear-gradient(180deg, rgba(255,178,46,0.28), rgba(255,178,46,0.16))",
                border: "1.5px solid rgba(255,178,46,0.75)",
                borderRadius: 999,
                padding: "0.2rem 0.62rem",
                color: "var(--sun-deep)",
                boxShadow: "0 2px 8px rgba(255,178,46,0.28)",
              }}
            >
              <span aria-hidden style={{ fontSize: "0.66rem", opacity: 0.9 }}>✦</span>
              {reconfigure ? "reconfiguring" : "first meeting"}
            </span>
          </div>
          <StepDots stepIndex={stepIndex} onJump={jumpTo} anywhere={reconfigure} />
        </header>

        <div className="ob-grid">
          <div className="ob-left">
            <div style={{ position: "relative" }}>
              <img
                src={mouthOpen ? OPEN : CLOSED}
                alt="Kotoba"
                draggable={false}
                className="ob-chibi"
                style={{
                  objectFit: "contain",
                  animation: "ob-bob 3.4s ease-in-out infinite",
                  filter: "drop-shadow(4px 6px 0 rgba(33,26,46,.18))",
                  userSelect: "none",
                }}
              />
              <HeartBurst seed={burst} />
            </div>
            {/* The region is the STABLE outer div; the keyed bubble inside it is what remounts to
                restart `rise`. A live region keyed on its own text is replaced rather than mutated,
                and a screen reader announces mutations inside the region it registered. */}
            <div role="status" aria-live="polite" style={{ maxWidth: 320, marginTop: 10 }}>
              <div
                key={line}
                style={{
                  position: "relative",
                  background: "#fff",
                  border: `3px solid var(--ink)`,
                  borderRadius: 18,
                  padding: "0.7rem 1rem",
                  boxShadow: "var(--shadow-pop-sm)",
                  fontFamily: body,
                  fontSize: "0.96rem",
                  lineHeight: 1.4,
                  textAlign: "center",
                  overflowWrap: "anywhere",
                  animation: "rise .35s ease both",
                }}
              >
                <span
                  style={{
                    position: "absolute",
                    top: -11,
                    left: "50%",
                    transform: "translateX(-50%) rotate(45deg)",
                    width: 16,
                    height: 16,
                    background: "#fff",
                    borderLeft: "3px solid var(--ink)",
                    borderTop: "3px solid var(--ink)",
                  }}
                />
                {line}
              </div>
            </div>
          </div>

          <div key={step} style={{ animation: "rise .45s cubic-bezier(.2,.8,.2,1) both", minWidth: 0 }}>
            <section
              style={{
                position: "relative",
                background: step === "ready"
                  ? "radial-gradient(130% 115% at 50% 0%, #ffe6b8 0%, var(--cream) 58%)"
                  : "var(--cream-2)",
                border: step === "ready" ? "4px solid var(--sun)" : "4px solid var(--ink)",
                borderRadius: 22,
                boxShadow: step === "ready"
                  ? "var(--shadow-pop), 0 0 0 6px rgba(255,178,46,0.30), 0 20px 60px rgba(255,178,46,0.35)"
                  : "var(--shadow-pop)",
                animation: shake ? "shake .45s" : step === "ready" ? "readyGlow 3.4s ease-in-out 2" : undefined,
                padding: step === "ready" ? "1.15rem 1.3rem 1.2rem" : "1.25rem 1.2rem 1.35rem",
                display: "flex",
                flexDirection: "column",
                gap: "0.9rem",
              }}
            >
              {reconfigure ? (
                <StepJump stepIndex={stepIndex} onJump={jumpTo} accent={accent} />
              ) : stepIndex > 0 && (
                <button
                  type="button"
                  onClick={back}
                  className="ob-back"
                  aria-label={`Back to ${STEPS[stepIndex - 1].label}`}
                  style={{
                    position: "absolute", top: -15, left: 20, display: "inline-flex",
                    alignItems: "center", gap: 5, whiteSpace: "nowrap", cursor: "pointer",
                    background: "var(--cream)", border: "3px solid var(--ink)", borderRadius: 999,
                    padding: "0.1rem 0.66rem 0.1rem 0.5rem", fontFamily: display, fontWeight: 700,
                    fontSize: "0.68rem", color: "var(--ink)", boxShadow: "2px 2px 0 var(--ink)",
                  }}
                >
                  <span aria-hidden style={{ fontSize: "0.8rem", lineHeight: 1 }}>←</span>
                  {STEPS[stepIndex - 1].label.toLowerCase()}
                </button>
              )}
              <h1
                style={{
                  fontFamily: display,
                  fontWeight: 700,
                  fontSize: step === "ready" ? "1.5rem" : "1.2rem",
                  margin: 0,
                  display: "flex",
                  alignItems: "center",
                  gap: 9,
                }}
              >
                <StepBadge icon={STEPS[stepIndex].icon} color={accent} />
                {step === "ready" ? (
                  <span>
                    Ready<span style={{ color: "var(--coral)" }}>!</span>
                  </span>
                ) : (
                  STEPS[stepIndex].label
                )}
              </h1>

              {step === "brain" && (
                <>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 }}>
                    <ChoiceCard
                      title="OpenAI"
                      sub="the GPT models"
                      color="var(--mint)"
                      selected={answers.provider === "openai"}
                      onClick={() => chooseProvider("openai")}
                    />
                    <ChoiceCard
                      title="xAI (Grok)"
                      sub="the Grok models"
                      color="var(--grape)"
                      selected={answers.provider === "xai"}
                      onClick={() => chooseProvider("xai")}
                    />
                  </div>
                  <div
                    style={{
                      border: "3px dashed var(--ink)",
                      borderRadius: 14,
                      padding: "0.55rem 0.8rem",
                      fontSize: "0.8rem",
                      opacity: 0.55,
                      textAlign: "center",
                      fontFamily: body,
                    }}
                  >
                    more brains soon~
                  </div>
                  {writeFailed && (
                    <Notice title="That didn't take">
                      {writeFailed} Nothing broke, and nothing changed — pick again whenever you&rsquo;re ready.
                    </Notice>
                  )}
                  <PopButton disabled={!answers.provider} onClick={() => go("model")}>
                    This one!
                  </PopButton>
                </>
              )}

              {step === "model" && (
                <>
                  <ModelVendor
                    provider={answers.provider ?? "openai"}
                    models={catalogue[answers.provider ?? "openai"].models}
                    selected={answers.model}
                    peeked={peek}
                    onPeek={setPeek}
                    onSelect={chooseModel}
                    onBuy={() => go("key")}
                  />
                  {writeFailed && (
                    <Notice title="That didn't take">
                      {writeFailed} Nothing broke, and nothing changed — pick again whenever you&rsquo;re ready.
                    </Notice>
                  )}
                </>
              )}

              {step === "key" && (
                <>
                  <label style={{ fontSize: "0.8rem", fontWeight: 600, opacity: 0.7, fontFamily: body }}>
                    Secret key — masked, and she never repeats it{keyNote(keys[answers.provider ?? "openai"])}
                  </label>
                  <input
                    ref={fieldRef}
                    value={keyDraft}
                    onChange={(e) => setKeyDraft(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && submitApiKey()}
                    type="password"
                    placeholder={keyPlaceholder(keys[answers.provider ?? "openai"])}
                    autoComplete="off"
                    spellCheck={false}
                    disabled={busy}
                    style={fieldStyle(accent)}
                  />
                  {keyFailed && !busy && (
                    <Notice title="That key didn't work">
                      {keyWhy === "paste"
                        ? "That usually means it was copied incomplete, or it belongs to a different account than you expected. Nothing broke — paste it again whenever you\u2019re ready."
                        : "Nothing broke, and nothing was stored. The key itself is still yours to use once that is sorted."}
                    </Notice>
                  )}
                  <PopButton disabled={busy || (!keyDraft.trim() && !heldKey)} onClick={submitApiKey}>
                    {busy ? (
                      <>
                        <SpinnerIcon width={17} height={17} /> Checking…
                      </>
                    ) : (
                      "Try the key"
                    )}
                  </PopButton>
                </>
              )}

              {step === "you" && (
                <>
                  <label style={{ fontSize: "0.8rem", fontWeight: 600, opacity: 0.7, fontFamily: body }}>
                    The name she&rsquo;ll call you
                  </label>
                  <input
                    ref={fieldRef}
                    value={nameDraft}
                    onChange={(e) => setNameDraft(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && submitUserName()}
                    type="text"
                    placeholder="The name you go by…"
                    autoComplete="off"
                    spellCheck={false}
                    maxLength={40}
                    style={fieldStyle(accent)}
                  />
                  {writeFailed && (
                    <Notice title="That didn't take">
                      {writeFailed} Nothing broke, and nothing changed — try again whenever you&rsquo;re ready.
                    </Notice>
                  )}
                  <PopButton disabled={!nameDraft.trim()} onClick={submitUserName}>
                    That&rsquo;s me
                  </PopButton>
                </>
              )}

              {step === "me" && (
                <>
                  <label style={{ fontSize: "0.8rem", fontWeight: 600, opacity: 0.7, fontFamily: body }}>
                    Her name — keep it, or make it yours
                  </label>
                  <input
                    ref={fieldRef}
                    value={herDraft}
                    onChange={(e) => setHerDraft(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && submitCompanionName()}
                    type="text"
                    placeholder="Kotoba"
                    autoComplete="off"
                    spellCheck={false}
                    maxLength={24}
                    style={fieldStyle(accent)}
                  />
                  {writeFailed && (
                    <Notice title="That didn't take">
                      {writeFailed} Nothing broke, and nothing changed — try again whenever you&rsquo;re ready.
                    </Notice>
                  )}
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                    <PopButton onClick={() => submitCompanionName()} style={{ flex: "1 1 auto" }}>
                      Call her this
                    </PopButton>
                    <PopButton
                      ghost
                      onClick={() => {
                        setHerDraft("Kotoba");
                        submitCompanionName("Kotoba");
                      }}
                      style={{ flex: "1 1 auto" }}
                    >
                      Keep &lsquo;Kotoba&rsquo;
                    </PopButton>
                  </div>
                </>
              )}

              {step === "language" && (
                <>
                  <div style={{ display: "flex", gap: 9, flexWrap: "wrap" }}>
                    {LANGUAGES.map((l) => (
                      <Chip key={l} selected={answers.language === l} onClick={() => chooseLanguage(l)}>
                        {l}
                      </Chip>
                    ))}
                  </div>
                  {writeFailed && (
                    <Notice title="That didn't take">
                      {writeFailed} Nothing broke, and nothing changed — pick again whenever you&rsquo;re ready.
                    </Notice>
                  )}
                  <PopButton disabled={!answers.language} onClick={() => go("face")}>
                    Sounds good
                  </PopButton>
                </>
              )}

              {step === "face" && (
                <>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {answers.face ? (
                      <FaceReceipt
                        key={`${answers.face}:${faceRun}`}
                        apiUrl={API}
                        dir={answers.face}
                        path={receipt?.path || under(modelsDir, answers.face)}
                        landed={receipt}
                        onDisk={onDisk}
                        stamp={<KeepStamp disabled={busy} onClick={keepFace} />}
                      />
                    ) : (
                      <BlankPassport />
                    )}

                    <Rubric>{answers.face ? "or hand her another" : "two ways to give her one"}</Rubric>

                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 }}>
                      {/* Already wearing the one this door fetches: offering 74 MB again is offering
                          somebody a thing they are holding. The door names what she has instead. */}
                      <ChoiceCard
                        title="Live2D's sample"
                        sub={wearingTheSample ? "you're wearing her already"
                             : offer ? `${offer.name} · about 74 MB` : "straight from Live2D"}
                        color="var(--mint)"
                        icon={DownloadIcon}
                        selected={openDoor === "sample"}
                        settled={wearingTheSample}
                        onClick={() => {
                          setDoor("sample");
                          setZipComplaint("");
                        }}
                      />
                      <ChoiceCard
                        title="A .zip of mine"
                        sub="one you already have"
                        color="var(--grape)"
                        icon={ArchiveIcon}
                        selected={openDoor === "zip"}
                        onClick={() => setDoor("zip")}
                      />
                    </div>

                    {openDoor && (
                      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                        {openDoor === "sample" ? (
                          <>
                            <p style={{ margin: 0, fontFamily: body, fontSize: "0.78rem", lineHeight: 1.45,
                                        opacity: 0.72 }}>
                              Straight from Live2D, under their terms —{" "}
                              {offer ? (
                                <a href={offer.licence} target="_blank" rel="noreferrer"
                                   style={{ color: "var(--grape)", fontWeight: 700 }}>
                                  read Live2D&rsquo;s licence
                                </a>
                              ) : (
                                <span style={{ opacity: 0.6 }}>fetching the licence link…</span>
                              )}
                              .
                            </p>
                            <SealButton
                              color="var(--coral)"
                              disabled={busy || !offer}
                              onClick={installDefault}
                              seal={busy ? <SpinnerIcon width={16} height={16} style={{ display: "block" }} />
                                       : <DownloadIcon width={16} height={16} strokeWidth={2.4} style={{ display: "block" }} />}
                            >
                              {busy ? "Fetching her face…" : "I accept those terms — fetch her"}
                            </SealButton>
                          </>
                        ) : (
                          <DropPlate
                            busy={busy}
                            over={dragging}
                            onDragState={setDragging}
                            onFiles={offerArchive}
                            onPick={() => archiveRef.current?.click()}
                          />
                        )}
                        {!answers.face && <WayOut show={faceRefused} onClick={goOnWithout} />}
                      </div>
                    )}

                    <input
                      ref={archiveRef}
                      type="file"
                      accept=".zip,application/zip"
                      style={{ display: "none" }}
                      onChange={(e) => {
                        offerArchive(e.target.files);
                        e.target.value = "";
                      }}
                    />

                    {/* Beside the passport rather than under it, and OUT of the flow: a refusal that grows
                        the step pushes everything below it down the page, which is the complaint. */}
                    <div
                      className="ob-report"
                      role="status"
                      aria-live="polite"
                      style={{ position: "absolute", top: 17, right: 18, zIndex: 5, pointerEvents: "none" }}
                    >
                      {faceNote && <ReportBubble key={faceNote.text} {...faceNote} onDismiss={clearReports} />}
                    </div>
                </div>
                </>
              )}

              {step === "voice" && (
                <>
                  <label style={{ fontSize: "0.8rem", fontWeight: 600, opacity: 0.7, fontFamily: body }}>
                    ElevenLabs key — optional; masked, and she never repeats it
                    {heldVoice ? " · one is saved; type to replace it" : ""}
                  </label>
                  <input
                    ref={fieldRef}
                    value={elDraft}
                    onChange={(e) => setElDraft(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && submitVoiceKey()}
                    type="password"
                    placeholder={heldVoice ? "•••••••• stored — Enter keeps it" : "Paste an ElevenLabs key… (or skip!)"}
                    autoComplete="off"
                    spellCheck={false}
                    disabled={busy}
                    style={fieldStyle(accent)}
                  />
                  {voiceFailed && !busy && (
                    <Notice title="That key didn't work">
                      ElevenLabs didn&rsquo;t recognise it — usually a paste that came across incomplete, or a
                      key from another account. Nothing broke, and you can still skip: I&rsquo;m happy either way.
                    </Notice>
                  )}
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                    <PopButton disabled={busy || (!elDraft.trim() && !heldVoice)} onClick={submitVoiceKey} style={{ flex: "1 1 auto" }}>
                      {busy ? (
                        <>
                          <SpinnerIcon width={17} height={17} /> Warming up…
                        </>
                      ) : (
                        heldVoice && !elDraft.trim() ? "Keep the voice she has" : "Give her a voice"
                      )}
                    </PopButton>
                    <PopButton ghost disabled={busy} onClick={skipVoice} style={{ flex: "1 1 auto" }}>
                      {heldVoice ? "Move on without changing it" : "Skip — text is enough for us~"}
                    </PopButton>
                  </div>
                </>
              )}

              {step === "ready" && (
                <>
                  <div
                    style={{
                      position: "absolute", top: -14,
                      ...(reconfigure ? { right: 20 } : { left: "50%", transform: "translateX(-50%)" }),
                      display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap",
                      background: "var(--sun)", border: "3px solid var(--ink)", borderRadius: 999,
                      padding: "0.16rem 0.8rem", fontFamily: display, fontWeight: 700,
                      fontSize: "0.72rem", boxShadow: "2px 2px 0 var(--ink)",
                    }}
                  >
                    <span aria-hidden>✦</span> all set — one button left
                  </div>
                  <StageDressing />
                  <div
                    style={{
                      display: "flex", alignItems: "center", gap: 6, marginTop: 4,
                      fontFamily: display, fontWeight: 700, fontSize: "0.8rem",
                      letterSpacing: "0.04em", color: "var(--coral-deep)",
                    }}
                  >
                    <HeartIcon width={12} height={12} style={{ color: "var(--coral-deep)" }} />
                    what I know now
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", margin: "-0.4rem 0 0" }}>
                    {knownRows(answers, secretRows(keys, answers.provider, heldVoice)).map((k, i, all) => (
                      <KnownRow key={k.label} index={i} last={i === all.length - 1} {...k} />
                    ))}
                  </div>
                  <button className="ob-wake" onClick={() => handleComplete()}>
                    <SparkIcon width={20} height={20} /> Wake {answers.companionName} up <SparkIcon width={20} height={20} />
                  </button>
                </>
              )}
            </section>

            <aside
              style={{
                position: "relative",
                marginTop: "1.6rem",
                background: "rgba(255, 255, 255, 0.72)",
                border: "2px dashed rgba(108, 76, 224, 0.38)",
                borderRadius: 22,
                boxShadow: "0 6px 18px rgba(33, 26, 46, 0.07)",
                padding: "1.15rem 1.05rem 0.95rem",
                fontFamily: body,
                fontSize: "0.85rem",
                lineHeight: 1.6,
                color: "rgba(33, 26, 46, 0.78)",
              }}
            >
              <div
                style={{
                  position: "absolute", top: -12, left: 18,
                  display: "inline-flex", alignItems: "center", gap: 6,
                  background: "var(--cream)",
                  border: "2px dashed rgba(108, 76, 224, 0.38)",
                  borderRadius: 999,
                  padding: "0.1rem 0.6rem",
                  fontFamily: display, fontWeight: 700, fontSize: "0.78rem",
                  color: "var(--grape)",
                }}
              >
                <span aria-hidden style={{ fontSize: "0.72rem" }}>{note.face}</span>
                {note.title}
              </div>
              {note.text}
            </aside>
          </div>
        </div>
      </div>

      {handover !== null && (
        <Handover
          herName={answers.companionName}
          settled={handover === "settled"}
          onSettled={() => setHandover("settled")}
        />
      )}
    </main>
  );
}

function KeyGlyph(p: SVGProps<SVGSVGElement>) {
  return (
    <svg width={24} height={24} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" {...p}>
      <circle cx="7.5" cy="12" r="4" />
      <path d="M11.5 12h9M17.5 12v3M14.5 12v2.2" />
    </svg>
  );
}

function ChipGlyph(p: SVGProps<SVGSVGElement>) {
  return (
    <svg width={24} height={24} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" {...p}>
      <rect x="7" y="7" width="10" height="10" rx="2" />
      <path d="M10 2v5M14 2v5M10 17v5M14 17v5M2 10h5M2 14h5M17 10h5M17 14h5" />
    </svg>
  );
}

function PortraitGlyph(p: SVGProps<SVGSVGElement>) {
  return (
    <svg width={24} height={24} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" {...p}>
      <rect x="3" y="3" width="18" height="18" rx="3" />
      <circle cx="12" cy="10" r="2.6" />
      <path d="M7 18.4a5.2 5.2 0 0 1 10 0" />
    </svg>
  );
}

const fieldStyle = (accent: string): React.CSSProperties => ({
  width: "100%",
  border: `3px solid ${accent}`,
  borderRadius: 13,
  padding: "0.7rem 0.85rem",
  fontFamily: body,
  fontSize: "1rem",
  background: "#fff",
  color: "var(--ink)",
  outline: "none",
  transition: "border-color .25s",
});

function PopButton({
  children,
  onClick,
  disabled = false,
  ghost = false,
  style,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  ghost?: boolean;
  style?: React.CSSProperties;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        border: "3px solid var(--ink)",
        background: ghost ? "#fff" : "var(--coral)",
        color: ghost ? "var(--ink)" : "#fff",
        borderRadius: 14,
        padding: "0.7rem 1rem",
        fontFamily: display,
        fontWeight: 700,
        fontSize: "1rem",
        boxShadow: "3px 3px 0 var(--ink)",
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.55 : 1,
        transition: "opacity .2s, background .2s, transform .15s",
        ...style,
      }}
    >
      {children}
    </button>
  );
}

const mono = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace';

function ModelVendor({
  provider, models, selected, peeked, onPeek, onSelect, onBuy,
}: {
  provider: Provider;
  models: ModelOption[];
  selected: string | null;
  peeked: string | null;
  onPeek: (id: string | null) => void;
  onSelect: (m: ModelOption) => void;
  onBuy: () => void;
}) {
  const ratio = (m: ModelOption) => (m.hint ? parseFloat(m.hint) || 1 : 1);
  const canH = (m: ModelOption) => Math.min(70, Math.round(42 + 28 * (Math.log(ratio(m)) / Math.log(26))));
  const canW = (m: ModelOption) => Math.round(34 + 16 * (Math.min(m.context || 400000, 1_050_000) / 1_050_000));
  const tier = (m: ModelOption) =>
    ratio(m) <= 1
      ? { top: "#7fe6cc", base: "var(--mint)", face: "rgba(33,26,46,.6)" }
      : ratio(m) < 5
        ? { top: "#ffd27a", base: "var(--sun)", face: "rgba(33,26,46,.6)" }
        : { top: "#ff7a92", base: "var(--live)", face: "rgba(255,255,255,.9)" };
  const floor = Math.min(...models.map((m) => m.out || Infinity));
  // "cheapest" is worn by the actual price floor alone: two models can sit 4% apart and earn no ratio
  // badge, and "no badge" is not the same claim.
  const price = (m: ModelOption) =>
    m.hint ? `×${parseFloat(m.hint)}` : m.out && m.out <= floor ? "cheapest" : "same price";

  const shown = models.find((m) => m.id === (peeked ?? selected)) ?? models[0];
  const LINE = 100;
  const rail = `repeating-linear-gradient(to bottom, transparent 0 ${LINE - 14}px,` +
    ` var(--ink) ${LINE - 14}px ${LINE - 7}px, transparent ${LINE - 7}px ${LINE + 10}px)`;

  return (
    <div style={{ border: "3px solid var(--ink)", borderRadius: 18, background: "#fff",
                  boxShadow: "var(--shadow-pop-sm)", overflow: "hidden" }}>
      <div style={{ background: "var(--sun)", borderBottom: "3px solid var(--ink)", textAlign: "center",
                    fontFamily: display, fontWeight: 700, fontSize: "0.64rem", letterSpacing: "0.13em",
                    textTransform: "uppercase", padding: "0.26rem 0.5rem", whiteSpace: "nowrap",
                    overflow: "hidden" }}>
        <span style={{ color: "var(--coral)" }}>✦</span> {provider === "xai" ? "xAI" : "OpenAI"} · brain
        vendor · 24h <span style={{ color: "var(--coral)" }}>✦</span>
      </div>

      <div style={{ margin: "7px 10px 6px", border: "3px solid var(--ink)", borderRadius: 12,
                    background: "linear-gradient(180deg, var(--cream) 0%, var(--cream-2) 100%)",
                    boxShadow: "inset 0 2px 8px rgba(33,26,46,.12)",
                    position: "relative", overflow: "hidden", padding: "8px 6px 6px" }}>
        <span aria-hidden style={{ position: "absolute", top: 0, bottom: 0, left: "11%", width: "8%",
                                   background: "rgba(255,255,255,.38)", transform: "skewX(-14deg)",
                                   pointerEvents: "none" }} />
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-around",
                      alignItems: "flex-end", rowGap: 10, background: rail,
                      backgroundClip: "content-box" }}>
          {models.map((m) => {
              const on = selected === m.id;
              const t = tier(m);
              return (
                <div key={m.id} style={{ position: "relative", zIndex: 1, display: "flex",
                                         flexDirection: "column", alignItems: "center",
                                         justifyContent: "flex-end", height: LINE, padding: "0 3px" }}>
                  <button
                    type="button"
                    aria-pressed={on}
                    aria-label={`${m.id} — ${m.note}`}
                    onMouseEnter={() => onPeek(m.id)}
                    onMouseLeave={() => onPeek(null)}
                    onFocus={() => onPeek(m.id)}
                    onBlur={() => onPeek(null)}
                    onClick={() => onSelect(m)}
                    className="ob-can"
                    style={{
                      width: canW(m), height: canH(m), padding: 0, position: "relative",
                      border: "3px solid var(--ink)", borderRadius: "9px 9px 7px 7px",
                      background: `linear-gradient(180deg, ${t.top} 0%, ${t.base} 62%)`,
                      boxShadow: on
                        ? "0 0 0 3px var(--coral), 3px 4px 0 rgba(33,26,46,.3)"
                        : "2px 3px 0 rgba(33,26,46,.22)",
                      display: "flex", flexDirection: "column", cursor: "pointer",
                    }}
                  >
                    {on && (
                      <span aria-hidden style={{ position: "absolute", top: -9, right: -10, width: 18,
                                                 height: 18, borderRadius: "50%", background: "var(--coral)",
                                                 border: "2px solid var(--ink)", color: "#fff", fontSize: 9,
                                                 lineHeight: "14px", textAlign: "center",
                                                 transform: "rotate(10deg)", zIndex: 2 }}>♥</span>
                    )}
                    <span aria-hidden style={{ height: 6, borderBottom: "2px solid rgba(33,26,46,.35)",
                                               display: "block", margin: "0 3px" }} />
                    <span style={{ flex: 1 }} />
                    <span style={{ background: "#fffdf6", borderTop: "2px solid var(--ink)",
                                   borderBottom: "2px solid var(--ink)", color: "var(--ink)",
                                   fontFamily: display, fontWeight: 700, fontSize: "0.62rem",
                                   textAlign: "center", padding: "2px 0", display: "block" }}>
                      {shortName(m.id)}
                    </span>
                    <span style={{ flex: 1 }} />
                    <span aria-hidden style={{ fontSize: "0.5rem", color: t.face, paddingBottom: 2,
                                               textAlign: "center" }}>
                      {on ? "♡ᴗ♡" : peeked === m.id ? "•o•" : "•ᴗ•"}
                    </span>
                  </button>
                  <span style={{ marginTop: -4, zIndex: 2, background: price(m) === "cheapest" ? "var(--mint)" : "#fff",
                                 border: "2px solid var(--ink)", borderRadius: 6, fontFamily: mono,
                                 fontWeight: 700, fontSize: "0.6rem", lineHeight: "14px",
                                 padding: "0 5px", whiteSpace: "nowrap" }}>
                    {price(m)}
                  </span>
                </div>
              );
          })}
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, margin: "0 10px 10px", alignItems: "stretch" }}>
        <div aria-live="polite"
             style={{ flex: 1, minWidth: 0, height: 76, position: "relative", overflow: "hidden",
                      background: "linear-gradient(180deg, #140f1d 0%, #1b1428 100%)",
                      border: "3px solid var(--ink)", borderRadius: 10, padding: "7px 10px" }}>
          <div key={shown.id} className="ob-lcd-print"
               style={{ fontFamily: mono, color: "var(--mint)",
                        textShadow: "0 0 7px rgba(20,199,154,.5)" }}>
            <div style={{ fontSize: "0.74rem", fontWeight: 700, whiteSpace: "nowrap",
                          overflow: "hidden", textOverflow: "ellipsis" }}>
              ▸ {shown.id}{selected === shown.id ? " ♥ hers" : ""}
            </div>
            <div style={{ fontSize: "0.64rem", opacity: 0.9, whiteSpace: "nowrap", overflow: "hidden" }}>
              {`${price(shown)} per word`}
              {shown.context ? ` · ${tokens(shown.context)} memory` : ""}
            </div>
            <div style={{ fontSize: "0.66rem", lineHeight: "14px", height: 28, overflow: "hidden",
                          opacity: 0.75, marginTop: 2 }}>
              {shown.note}<span className="ob-lcd-cursor">▮</span>
            </div>
          </div>
          <div aria-hidden style={{ position: "absolute", inset: 0, pointerEvents: "none",
                                    background: "repeating-linear-gradient(0deg, rgba(0,0,0,0) 0px, rgba(0,0,0,0) 2px, rgba(0,0,0,.25) 3px)" }} />
        </div>
        <button
          type="button"
          className="ob-slot"
          disabled={!selected}
          onClick={onBuy}
          aria-label={selected ? `Take ${selected} and go on` : "Pick a model first"}
          style={{ width: 50, border: "3px solid var(--ink)", borderRadius: 10,
                   background: selected ? "var(--coral)" : "#fff", color: "var(--ink)",
                   display: "flex", flexDirection: "column", alignItems: "center",
                   justifyContent: "center", gap: 4, padding: 0,
                   cursor: selected ? "pointer" : "not-allowed",
                   boxShadow: selected ? "var(--shadow-pop-sm)" : "none" }}
        >
          <span style={{ width: 4, height: 20, background: "var(--ink)", borderRadius: 2 }} />
          <span style={{ fontFamily: display, fontWeight: 700, fontSize: "0.5rem",
                         letterSpacing: "0.06em", opacity: selected ? 0.9 : 0.55,
                         textAlign: "center", lineHeight: 1.25, color: selected ? "#fff" : undefined }}>
            {selected ? <>take<br />it</> : <>one<br />key</>}
          </span>
          <span className={selected ? "ob-coin" : undefined}
                style={{ width: 11, height: 11, borderRadius: "50%", border: "2px solid var(--ink)",
                         background: selected ? "var(--sun)" : "transparent",
                         opacity: selected ? 1 : 0.4 }} />
        </button>
      </div>
    </div>
  );
}

function shortName(id: string) {
  const parts = id.split("-");
  const last = parts[parts.length - 1];
  if (/^[a-z]+$/.test(last) && last.length <= 5) return last;
  const version = parts.find((p) => /^[0-9]/.test(p));
  return version ?? id;
}

function ChoiceCard({
  title,
  sub,
  color,
  selected,
  settled = false,
  onClick,
  icon: Icon = BrainIcon,
}: {
  title: string;
  sub: string;
  color: string;
  selected: boolean;
  /** Not a choice: a fact about what she is already wearing. It must not borrow the chosen card's
   *  look, or two cards read as chosen at once and neither says which one you picked. */
  settled?: boolean;
  onClick: () => void;
  icon?: (p: SVGProps<SVGSVGElement>) => React.JSX.Element;
}) {
  return (
    <button
      // Already the chosen one: pressing it again re-fires the door it opened, and the doors here
      // start a download and an unpack. A chosen card is a statement, not a button to press twice.
      onClick={selected || settled ? undefined : onClick}
      aria-pressed={selected && !settled}
      disabled={selected || settled}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
        gap: 6,
        cursor: selected || settled ? "default" : "pointer",
        background: settled ? "transparent" : selected ? "#fff" : "rgba(255,255,255,.6)",
        border: settled ? "3px dashed rgba(33,26,46,.30)"
                : `3px solid ${selected ? "var(--ink)" : "rgba(33,26,46,.45)"}`,
        borderRadius: 16,
        padding: "0.85rem 0.9rem",
        textAlign: "left",
        opacity: settled ? 0.62 : 1,
        transform: !settled && selected ? "translate(-2px, -2px)" : "none",
        boxShadow: settled ? "none" : selected ? "5px 5px 0 var(--ink)" : "2px 2px 0 rgba(33,26,46,.25)",
        transition: "transform .15s, box-shadow .15s, border-color .15s, background .15s",
      }}
    >
      <span
        style={{
          display: "grid",
          placeItems: "center",
          width: 34,
          height: 34,
          borderRadius: "50%",
          background: color,
          border: "2.5px solid var(--ink)",
          color: "#fff",
          lineHeight: 0,
        }}
      >
        <Icon width={18} height={18} style={{ display: "block" }} />
      </span>
      <span style={{ fontFamily: display, fontWeight: 700, fontSize: "1.02rem", color: "var(--ink)", overflowWrap: "anywhere" }}>
        {title}
        {selected && !settled && <CheckIcon width={15} height={15} style={{ marginLeft: 6, color: "var(--mint)" }} />}
      </span>
      <span style={{ fontFamily: body, fontSize: "0.76rem", opacity: 0.6, color: "var(--ink)" }}>{sub}</span>
    </button>
  );
}

/** The face step's own action, and the reason it is not another PopButton: the step ends in a document
 *  being issued, so the thing that issues it carries a seal rather than reading as one more pill in a
 *  stack. Two of them are ever on screen and they never wear the same colour. */
function SealButton({
  children, onClick, seal, color, disabled = false,
}: {
  children: React.ReactNode;
  onClick: () => void;
  seal: React.ReactNode;
  color: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className="ob-sealbtn"
      onClick={onClick}
      disabled={disabled}
      style={{
        position: "relative",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: "100%",
        border: "3px solid var(--ink)",
        borderRadius: 14,
        background: color,
        color: "#fff",
        padding: "0.7rem 3.2rem 0.7rem 1rem",
        fontFamily: display,
        fontWeight: 700,
        fontSize: "1rem",
        boxShadow: "3px 3px 0 var(--ink)",
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.55 : 1,
        textAlign: "center",
        transition: "opacity .2s, transform .15s, box-shadow .15s",
      }}
    >
      {children}
      <span aria-hidden className="ob-seal" style={sealStyle}>
        {seal}
      </span>
    </button>
  );
}

/** Accepting the document IS the way on from this step, so the mark that accepts it lives on the
 *  document — in the corner a real one is stamped into, costing the step no row of its own. */
function KeepStamp({ onClick, disabled }: { onClick: () => void; disabled: boolean }) {
  return (
    // Two elements on purpose: the well owns the animations and the button owns the hover. Hovering a
    // single element meant cancelling its animation, and leaving the hover replayed the fall from off
    // the page — the stamp vanished and dropped in again instead of settling back.
    <span className="ob-mark-well" style={{ position: "absolute", right: 10, bottom: 9,
                                            borderRadius: "50%", transform: "rotate(-9deg)" }}>
    <button
      type="button"
      className="ob-mark"
      onClick={onClick}
      disabled={disabled}
      title="Keep this face and go on"
      aria-label="Keep this face and go on"
      style={{
        position: "relative",
        display: "grid",
        placeItems: "center",
        width: 72,
        height: 72,
        padding: 0,
        borderRadius: "50%",
        border: "3.5px solid var(--mint)",
        background: "radial-gradient(circle at 50% 34%, rgba(20,199,154,0.26), rgba(20,199,154,0.10))",
        color: "#0a8a6b",
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.45 : 1,
        transition: "transform .22s cubic-bezier(.2,.9,.3,1), box-shadow .22s, background .22s",
      }}
    >
      <span aria-hidden style={{ position: "absolute", inset: 5, borderRadius: "50%",
                                 border: "1.5px dashed rgba(20,199,154,0.6)" }} />
      <span style={{ display: "grid", placeItems: "center", gap: 2, lineHeight: 1 }}>
        <CheckIcon width={15} height={15} strokeWidth={3.2} style={{ display: "block" }} />
        <span style={{ fontFamily: display, fontWeight: 700, fontSize: "0.54rem", letterSpacing: "0.14em",
                       textTransform: "uppercase", lineHeight: 1.2, textAlign: "center" }}>
          keep
          <br />
          her
        </span>
      </span>
    </button>
    </span>
  );
}

/** A drag target that is also a button, because the same door has to open to a dropped file, a click
 *  and a keyboard. What lands here is judged by name before anything is sent. */
function DropPlate({
  busy, over, onDragState, onFiles, onPick,
}: {
  busy: boolean;
  over: boolean;
  onDragState: (over: boolean) => void;
  onFiles: (files: FileList | null) => void;
  onPick: () => void;
}) {
  // Every child of the plate fires its own enter/leave as the pointer crosses it, so the state has to
  // count the crossings rather than trust the last one.
  const depth = useRef(0);
  return (
    <button
      type="button"
      className="ob-drop"
      disabled={busy}
      onClick={onPick}
      onDragEnter={(e) => {
        e.preventDefault();
        depth.current += 1;
        onDragState(true);
      }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={(e) => {
        e.preventDefault();
        depth.current = Math.max(0, depth.current - 1);
        if (!depth.current) onDragState(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        depth.current = 0;
        onFiles(e.dataTransfer?.files ?? null);
      }}
      style={{
        flex: 1,
        minHeight: 72,
        width: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 3,
        padding: "0.45rem 0.9rem",
        border: `3px dashed ${over ? "var(--grape)" : "rgba(108,76,224,0.45)"}`,
        borderRadius: 16,
        background: over ? "rgba(108,76,224,0.10)" : "rgba(255,255,255,0.72)",
        boxShadow: over ? "0 4px 0 rgba(108,76,224,.25)" : "inset 0 2px 0 rgba(255,255,255,.9)",
        color: "var(--ink)",
        cursor: busy ? "default" : "pointer",
        transform: over ? "translateY(-2px)" : "none",
        transition: "background .18s, border-color .18s, transform .18s",
      }}
    >
      <span aria-hidden className="ob-drop-badge" style={{
        display: "grid", placeItems: "center", width: 28, height: 28, borderRadius: "50%",
        background: "var(--grape)", border: "2.5px solid var(--ink)", color: "#fff", lineHeight: 0,
        transition: "transform .18s", transform: over ? "scale(1.12) rotate(-6deg)" : "none",
      }}>
        {busy ? <SpinnerIcon width={15} height={15} style={{ display: "block" }} />
              : <ArchiveIcon width={15} height={15} style={{ display: "block" }} />}
      </span>
      <span style={{ fontFamily: display, fontWeight: 700, fontSize: "0.92rem" }}>
        {busy ? "Unpacking her…" : over ? "Let go — I'll try it on" : "Drop a .zip here"}
      </span>
      <span style={{ fontFamily: body, fontSize: "0.72rem", opacity: 0.55 }}>
        {busy ? "one file at a time" : over ? "just the one, folder and all" : "or click to choose one"}
      </span>
    </button>
  );
}

/** The step is not skippable by invitation, so this is never on screen until a door has actually shut.
 *  It sits in the panel's own bottom padding — out of the flow, so earning it moves nothing, and no row
 *  stands empty waiting for it. */
function WayOut({ show, onClick }: { show: boolean; onClick: () => void }) {
  if (!show) return null;
  return (
    <div style={{ position: "absolute", left: 0, right: 0, bottom: 3, display: "flex",
                  justifyContent: "center", pointerEvents: "none" }}>
      <button type="button" className="ob-wayout" onClick={onClick} style={{ pointerEvents: "auto" }}>
        Neither door opened? Go on without a face for now
      </button>
    </div>
  );
}

function Rubric({ children }: { children: React.ReactNode }) {
  const rule = <span aria-hidden style={{ flex: 1, height: 2, background: "rgba(33,26,46,0.16)" }} />;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "-0.15rem 0 -0.15rem" }}>
      {rule}
      <span style={{ fontFamily: display, fontWeight: 700, fontSize: "0.6rem", letterSpacing: "0.15em",
                     textTransform: "uppercase", opacity: 0.45, whiteSpace: "nowrap" }}>
        {children}
      </span>
      {rule}
    </div>
  );
}

/** What the face step has to report, in one place and in priority order: a refusal outranks a file it
 *  would not even send, which outranks a note about files that landed anyway. */
function faceReport(failed: string, complaint: string, dropped: Skipped | null):
  { tone: "warn" | "note"; title: string; text: string } | null {
  if (failed) return { tone: "warn", title: "That didn't take", text: `${failed} Nothing changed.` };
  if (complaint) return { tone: "warn", title: "Not that one", text: complaint };
  if (dropped) {
    const kinds = dropped.kinds.length ? ` (${dropped.kinds.join(", ")})` : "";
    return {
      tone: "note",
      title: "Some files stayed behind",
      text: `${dropped.n} file${dropped.n === 1 ? "" : "s"} in that archive weren’t the kind she can` +
        ` wear${kinds}, so they were left out. Her face came through.`,
    };
  }
  return null;
}

function ReportBubble({
  tone, title, text, onDismiss,
}: {
  tone: "warn" | "note";
  title: string;
  text: string;
  onDismiss: () => void;
}) {
  const ground = tone === "warn" ? "var(--sun)" : "#fff";
  return (
    <div
      style={{
        position: "relative",
        border: "3px solid var(--ink)",
        borderRadius: 14,
        background: ground,
        boxShadow: "3px 3px 0 var(--ink)",
        padding: "0.45rem 0.6rem 0.5rem",
        fontFamily: body,
        fontSize: "0.72rem",
        lineHeight: 1.38,
        color: "var(--ink)",
        overflowWrap: "anywhere",
        animation: "ob-arrive .3s cubic-bezier(.2,.85,.25,1) both",
      }}
    >
      <span aria-hidden style={{
        position: "absolute", bottom: -9, left: 20, width: 13, height: 13, background: ground,
        borderRight: "3px solid var(--ink)", borderBottom: "3px solid var(--ink)",
        transform: "rotate(45deg)",
      }} />
      {/* The bubble floats over the passport, so the one thing it must never do is stay in the way. */}
      <button type="button" className="ob-hush" onClick={onDismiss} aria-label="Hide this note"
              style={hushStyle}>
        <CloseCircleIcon width={15} height={15} strokeWidth={2.4} style={{ display: "block" }} />
      </button>
      <strong style={{ display: "block", fontFamily: display, fontSize: "0.76rem", marginBottom: 1,
                       paddingRight: 8 }}>
        {title}
      </strong>
      {text}
    </div>
  );
}

const hushStyle: React.CSSProperties = {
  position: "absolute",
  top: -11,
  right: -11,
  display: "grid",
  placeItems: "center",
  width: 23,
  height: 23,
  padding: 0,
  borderRadius: "50%",
  border: "2.5px solid var(--ink)",
  background: "var(--cream)",
  color: "var(--ink)",
  lineHeight: 0,
  boxShadow: "2px 2px 0 var(--ink)",
  cursor: "pointer",
  pointerEvents: "auto",
  transition: "transform .15s, box-shadow .15s",
};

const sealStyle: React.CSSProperties = {
  position: "absolute",
  right: 10,
  top: "50%",
  marginTop: -16,
  display: "grid",
  placeItems: "center",
  width: 32,
  height: 32,
  borderRadius: "50%",
  border: "2.5px dashed rgba(255,255,255,0.85)",
  color: "#fff",
  lineHeight: 0,
};

function Chip({ children, selected, onClick }: { children: React.ReactNode; selected: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={selected}
      style={{
        border: "2.5px solid var(--ink)",
        borderRadius: 999,
        padding: "0.42rem 0.85rem",
        fontFamily: display,
        fontWeight: 600,
        fontSize: "0.86rem",
        background: selected ? "var(--grape)" : "#fff",
        color: selected ? "#fff" : "var(--ink)",
        boxShadow: selected ? "2px 2px 0 var(--ink)" : "none",
        transition: "background .15s, color .15s, box-shadow .15s",
      }}
    >
      {children}
    </button>
  );
}

function StepBadge({ icon: Icon, color }: { icon: (p: SVGProps<SVGSVGElement>) => React.JSX.Element; color: string }) {
  return (
    <span
      style={{
        display: "grid",
        placeItems: "center",
        width: 30,
        height: 30,
        borderRadius: 10,
        background: color,
        border: "2.5px solid var(--ink)",
        color: "#fff",
        transition: "background .3s",
      }}
    >
      <Icon width={17} height={17} />
    </span>
  );
}

function StepJump({ stepIndex, onJump, accent }: {
  stepIndex: number;
  onJump: (s: StepId) => void;
  accent: string;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // A menu that survives a click anywhere else is a menu you have to fight. Escape leaves too,
  // because the pointer is not the only way in.
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", key);
    };
  }, [open]);

  const here = STEPS[stepIndex];
  return (
    <div ref={box} style={{ position: "absolute", top: -15, left: 20, zIndex: 5 }}>
      <button
        type="button"
        className="ob-back"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={open ? "Close the step list" : `On ${here.label} — jump to another step`}
        style={{
          display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap",
          cursor: "pointer", background: open ? accent : "var(--cream)",
          border: "3px solid var(--ink)", borderRadius: 999,
          padding: "0.1rem 0.6rem 0.1rem 0.52rem", fontFamily: display, fontWeight: 700,
          fontSize: "0.68rem", color: open ? "#fff" : "var(--ink)",
          boxShadow: open ? "1px 1px 0 var(--ink)" : "2px 2px 0 var(--ink)",
          transform: open ? "translate(1px, 1px)" : undefined,
          transition: "background .18s, box-shadow .18s, transform .18s",
        }}
      >
        <here.icon width={13} height={13} style={{ display: "block" }} aria-hidden />
        {here.label.toLowerCase()}
        <span aria-hidden style={{
          fontSize: "0.62rem", lineHeight: 1, display: "inline-block",
          transform: open ? "rotate(180deg)" : undefined, transition: "transform .18s",
        }}>▾</span>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Setup steps"
          style={{
            position: "absolute", top: "calc(100% + 8px)", left: 0, minWidth: 178,
            background: "var(--cream)", border: "3px solid var(--ink)", borderRadius: 14,
            boxShadow: "6px 6px 0 var(--ink)", padding: 6,
            display: "flex", flexDirection: "column", gap: 2,
            animation: "jumpIn .16s ease-out",
          }}
        >
          {STEPS.map((s, i) => {
            const current = i === stepIndex;
            return (
              <button
                key={s.id}
                type="button"
                role="menuitem"
                disabled={current}
                onClick={() => {
                  setOpen(false);
                  onJump(s.id);
                }}
                className="ob-jump-row"
                aria-current={current ? "step" : undefined}
                style={{
                  display: "flex", alignItems: "center", gap: 9, width: "100%",
                  padding: "0.34rem 0.5rem", borderRadius: 9, border: "2px solid transparent",
                  background: current ? "var(--cream-2)" : "transparent",
                  fontFamily: body, fontSize: "0.78rem", fontWeight: current ? 700 : 500,
                  color: "var(--ink)", cursor: current ? "default" : "pointer",
                  textAlign: "left",
                }}
              >
                <span aria-hidden style={{
                  display: "grid", placeItems: "center", width: 22, height: 22, borderRadius: "50%",
                  border: "2px solid var(--ink)", lineHeight: 0,
                  background: current ? accent : "#fff", color: current ? "#fff" : "var(--ink)",
                }}>
                  <s.icon width={12 * (s.k ?? 1)} height={12 * (s.k ?? 1)} style={{ display: "block" }} />
                </span>
                {s.label}
                {current && (
                  <span style={{ marginLeft: "auto", fontSize: "0.62rem", opacity: 0.5 }}>here</span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function StepDots({ stepIndex, onJump, anywhere = false }: {
  stepIndex: number;
  onJump: (s: StepId) => void;
  /** Reconfiguring: every answer already exists, so the strip is a way IN to the one being changed.
   *  On a first run it stays backwards-only — forward is where the unanswered questions are. */
  anywhere?: boolean;
}) {
  return (
    <div style={{ marginTop: 12, display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
      <div style={{ display: "flex", gap: 8 }}>
        {STEPS.map((s, i) => {
          const state = i < stepIndex ? "done" : i === stepIndex ? "now" : "todo";
          const Icon = state === "done" ? CheckIcon : s.icon;
          const reachable = stepReachable(i, stepIndex, anywhere);
          const label = stepAction(i, stepIndex) === "back" ? `Back to ${s.label}` : `Go to ${s.label}`;
          const Tag = (reachable ? "button" : "span") as "button" | "span";
          return (
            <Tag
              key={s.id}
              title={reachable ? label : s.label}
              {...(reachable
                ? { type: "button" as const, onClick: () => onJump(s.id), "aria-label": label }
                : {})}
              style={{
                cursor: reachable ? "pointer" : "default",
                padding: 0,
                display: "grid",
                placeItems: "center",
                width: 30,
                height: 30,
                borderRadius: "50%",
                border: "2.5px solid var(--ink)",
                background: state === "done" ? "var(--mint)" : state === "now" ? "var(--coral)" : "#fff",
                color: state === "todo" ? "var(--ink)" : "#fff",
                opacity: state === "todo" ? 0.45 : 1,
                transition: "background .3s, opacity .3s",
                // an inline SVG sits on the text baseline: the descender gap reads as "not centred"
                // however the box is aligned. Kill the line box, block the svg.
                lineHeight: 0,
              }}
            >
              <Icon width={19 * (s.k ?? 1)} height={19 * (s.k ?? 1)} style={{ display: "block" }} />
            </Tag>
          );
        })}
      </div>
      <div style={{ fontFamily: body, fontSize: "0.74rem", opacity: 0.55 }}>
        {anywhere
          ? `${STEPS[stepIndex].label} — pick any of them, nothing has to be answered twice`
          : `${stepIndex + 1} of ${STEPS.length} — ${STEPS[stepIndex].label}`}
      </div>
    </div>
  );
}

function knownRows(a: Answers, secrets: { key: boolean; voice: boolean }):
    { label: string; value: string; note: string }[] {
  const kept = a.companionName.trim().toLowerCase() === "kotoba";
  return [
    {
      label: "Brain",
      value: a.provider === "openai" ? "OpenAI" : a.provider === "xai" ? "xAI (Grok)" : "—",
      note: a.provider ? "does my thinking now" : "",
    },
    { label: "Model", value: a.model || "—", note: a.model ? "the one I'll actually think with" : "" },
    { label: "Key", value: secrets.key ? "••••••" : "—", note: secrets.key ? "locked away safe, never shown" : "" },
    { label: "You", value: a.userName || "—", note: a.userName ? "filed under 'most important person'" : "" },
    { label: "Her", value: a.companionName, note: kept ? "still me~ I'd have missed it" : "my new name — I'm growing into it" },
    {
      label: "Language",
      value: a.language || "—",
      note: a.language ? (a.language.startsWith("Auto") ? "I'll switch the moment you do" : "ears and voice both") : "",
    },
    {
      label: "Face",
      value: a.face || "not yet",
      note: a.face ? "on your machine, and it's mine now" : "and that's okay~ imagine me for a while",
    },
    {
      label: "Voice",
      value: secrets.voice ? "••••••" : "not yet",
      note: secrets.voice ? "locked away safe — wait till you hear me" : "and that's okay~ we'll write",
    },
  ];
}

function KnownRow({ label, value, note, index, last }: { label: string; value: string; note: string; index: number; last: boolean }) {
  const d = 0.08 + index * 0.1;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        padding: "0.24rem 0.15rem",
        borderBottom: last ? "none" : "2px dotted rgba(33,26,46,0.22)",
        animation: `rise .35s ease ${d}s both`,
      }}
    >
      <span
        aria-hidden
        style={{
          display: "grid", placeItems: "center", width: 19, height: 19, marginTop: 1, flexShrink: 0,
          borderRadius: "50%", background: "var(--mint)", border: "2px solid rgba(33,26,46,0.85)", color: "#fff",
          boxShadow: "0 0 8px rgba(20,199,154,0.45)",
          animation: `ob-stamp .4s cubic-bezier(.3,1.6,.4,1) ${d + 0.08}s both`,
        }}
      >
        <CheckIcon width={10} height={10} />
      </span>
      <span style={{ fontFamily: display, fontWeight: 700, fontSize: "0.82rem", width: 74, flexShrink: 0, opacity: 0.62, paddingTop: 2 }}>
        {label}
      </span>
      <span style={{ fontFamily: body, fontSize: "0.9rem", minWidth: 0, overflowWrap: "anywhere", lineHeight: 1.45 }}>
        <strong style={{ fontFamily: display, fontWeight: 700 }}>{value}</strong>
        {note && <span style={{ opacity: 0.62 }}> — {note}</span>}
      </span>
    </div>
  );
}

function StageDressing() {
  const stars: { top: string; left?: string; right?: string; size: string; o: number }[] = [
    { top: "12%", right: "6%", size: "0.95rem", o: 0.95 },
    { top: "34%", left: "3.5%", size: "0.7rem", o: 0.86 },
    { top: "58%", right: "3%", size: "0.65rem", o: 0.8 },
    { top: "80%", left: "6%", size: "0.8rem", o: 0.84 },
  ];
  const confetti: { tx: number; ty: number; rr: number; d: number; bg: string; round?: boolean }[] = [
    { tx: -150, ty: -70, rr: -160, d: 0, bg: "var(--sun)" },
    { tx: -90, ty: -120, rr: 140, d: 0.06, bg: "var(--coral)", round: true },
    { tx: -30, ty: -145, rr: -120, d: 0.12, bg: "var(--mint)" },
    { tx: 40, ty: -140, rr: 150, d: 0.05, bg: "#b9a5f2", round: true },
    { tx: 105, ty: -115, rr: -140, d: 0.1, bg: "var(--sun)", round: true },
    { tx: 160, ty: -60, rr: 170, d: 0.14, bg: "var(--coral)" },
    { tx: -170, ty: -15, rr: 120, d: 0.18, bg: "var(--mint)", round: true },
    { tx: 175, ty: -5, rr: -130, d: 0.2, bg: "var(--sun)" },
  ];
  return (
    <div aria-hidden style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      {stars.map((s, i) => (
        <span
          key={i}
          style={{
            position: "absolute", top: s.top, left: s.left, right: s.right,
            fontSize: s.size, color: "var(--sun-deep)", opacity: s.o, lineHeight: 1,
          }}
        >
          ✦
        </span>
      ))}
      <span style={{ position: "absolute", left: "50%", top: 6 }}>
        {confetti.map((c, i) => (
          <span
            key={i}
            style={{
              position: "absolute", width: 11, height: 11, opacity: 0,
              background: c.bg, borderRadius: c.round ? "50%" : 3,
              "--tx": `${c.tx}px`, "--ty": `${c.ty}px`, "--rr": `${c.rr}deg`,
              animation: `ob-confetti 1.15s cubic-bezier(.2,.7,.3,1) ${0.35 + c.d}s both`,
            } as React.CSSProperties}
          />
        ))}
      </span>
    </div>
  );
}

function Notice({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        border: "3px solid var(--ink)",
        borderRadius: 13,
        background: "var(--sun)",
        boxShadow: "3px 3px 0 var(--ink)",
        padding: "0.6rem 0.7rem",
        fontSize: "0.78rem",
        lineHeight: 1.45,
        color: "var(--ink)",
        fontFamily: body,
        animation: "rise .3s ease both",
      }}
    >
      <strong style={{ fontFamily: display }}>{title}</strong> — {children}
    </div>
  );
}

function HeartBurst({ seed }: { seed: number }) {
  if (seed === 0) return null;
  return (
    <div key={seed} aria-hidden style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      {[0, 1, 2].map((i) => (
        <HeartIcon
          key={i}
          width={13 + i * 4}
          height={13 + i * 4}
          style={{
            position: "absolute",
            top: `${16 + i * 9}%`,
            left: `${4 + i * 38}%`,
            color: ["var(--coral)", "var(--grape)", "var(--sun)"][i],
            animation: `ob-heart 1.1s ease-out ${i * 0.13}s both`,
          }}
        />
      ))}
    </div>
  );
}

function FloatingShapes() {
  return (
    <div aria-hidden style={{ position: "absolute", inset: 0, pointerEvents: "none", zIndex: 1 }}>
      <span style={{ position: "absolute", top: "10%", left: "7%", width: 30, height: 30, background: "var(--sun)", borderRadius: 12, boxShadow: "2px 2px 0 rgba(33,26,46,.18)", animation: "ob-float 6s ease-in-out infinite" }} />
      <span style={{ position: "absolute", top: "20%", right: "8%", width: 24, height: 24, background: "var(--grape)", borderRadius: "50%", boxShadow: "2px 2px 0 rgba(33,26,46,.18)", animation: "ob-float 5.5s ease-in-out .8s infinite" }} />
      <span style={{ position: "absolute", bottom: "22%", left: "10%", width: 18, height: 18, background: "var(--mint)", borderRadius: "50%", boxShadow: "2px 2px 0 rgba(33,26,46,.18)", animation: "ob-float 6.5s ease-in-out 1.6s infinite" }} />
      <span style={{ position: "absolute", bottom: "14%", right: "9%", width: 0, height: 0, borderLeft: "15px solid transparent", borderRight: "15px solid transparent", borderBottom: "26px solid var(--coral)", animation: "ob-float 6s ease-in-out 1.1s infinite" }} />
    </div>
  );
}

function Handover({
  herName,
  settled,
  onSettled,
}: {
  herName: string;
  settled: boolean;
  onSettled: () => void;
}) {
  const router = useRouter();

  useEffect(() => {
    if (settled) return;
    const t = setTimeout(onSettled, 4300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled]);

  // `replace`, so Back cannot come round to first run again.
  useEffect(() => {
    if (settled) router.replace("/app");
  }, [settled, router]);

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 60 }}>
      {/* The ground the loader sits on: the same wash as the flow, so the hand-over does not flash. */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "radial-gradient(120% 100% at 50% 0%, var(--cream) 0%, var(--cream-2) 100%)",
        }}
      />
      {/* stacking context: keeps CallLoader's fixed z-50 overlay inside this z-60 layer */}
      <div style={{ position: "absolute", inset: 0, zIndex: 2, pointerEvents: "none" }}>
        <CallLoader
          done={settled}
          name={herName}
          lines={[`Waking ${herName} up…`, "Warming up her voice…", "Opening her eyes…"]}
        />
      </div>
    </div>
  );
}
