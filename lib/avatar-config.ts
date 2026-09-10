/**
 * Everything once typed into the renderer for one particular model: framing, parts to hide, expression
 * map. WHICH model is no longer decided here — it is chosen at runtime and asked for at startup,
 * because Next inlines an env var at `next build` and a frozen answer can never be installed for
 * somebody else. Answers come in one order — a written profile, what the model
 * declares about itself, then a default that cannot be wrong for lack of information — so an
 * unprofiled model still loads, blinks and has a face for every emotion. The Emotion union is NOT
 * configurable: it is a contract shared with the backend. Every field exists for a trap and each is
 * SILENT when wrong: pixi resolves an unknown expression name to `false`, so the face never changes.
 */
import type { Emotion } from "@/lib/expressions";
import type { Framing } from "@/lib/head-probe";

/** Everything the renderer needs to know about one model. */
export type AvatarConfig = {
  /** Folder in the models directory, and the entry file inside it. */
  dir: string;
  entry: string;
  /** Canvas height over the model's native height. */
  scale?: number;
  /** Her centre as a fraction of canvas height; above 1 pushes the body off-screen so only the face
   *  shows, which is the whole look. Both are OPTIONAL because a hand-set pair is the one thing a
   *  model somebody else brings will not have: left blank, they are taken off the measured head. */
  anchorY?: number;
  /** Hidden every frame — a hat, a prop, anything between the viewer and her face. Numbers are part
   *  indices; strings are part ids from the model's own list. */
  hideParts?: (number | string)[];
  /** Emotion to the model's own expression name, or its index. */
  expressions?: Partial<Record<Emotion, string | number>>;
  /** The face she wears awake with nothing to react to: a turn ending, or a call starting. NOT on
   *  load — she loads asleep, and the drowsy face there is the phase talking, not the model. */
  defaultEmotion: Emotion;
  /** The parameter the lip sync writes. Models disagree, and writing the wrong one moves nothing at
   *  all, silently. */
  mouthParam?: string;
  /** The lids, when the model names its own. */
  eyeParams?: string[];
  /** True means the model DECLARES an `EyeBlink` group, so the engine would blink it itself — which
   *  is why the controller takes that blinker away and blinks her from `writeFace` instead. The
   *  engine's blinker runs AFTER expressions and wipes whatever eyelid one just set, and two
   *  blinkers fight over one lid. Reading this as "the engine blinks it, so we must not" leaves the
   *  default model never blinking. */
  autoBlink?: boolean;
};

/** Parameters that OPEN a mouth, as opposed to shaping one. `ParamA` is the Japanese "a" vowel and is
 *  what Live2D's own sample drives. */
const OPENS_THE_MOUTH = ["ParamMouthOpenY", "ParamA", "ParamMouthOpen", "ParamOpen"];

/** When a model has nothing for an emotion, `resolveExpression` walks this chain rather than freezing
 *  her face. Every chain ends at `neutral`, so three expressions still answer all fourteen emotions —
 *  demanding a complete map would turn every model missing one entry into one that does not work. */
export const FALLBACK: Record<Emotion, Emotion[]> = {
  neutral: [],
  happy: ["excited", "affectionate", "neutral"],
  excited: ["happy", "determined", "neutral"],
  affectionate: ["happy", "embarrassed", "neutral"],
  determined: ["excited", "angry", "neutral"],
  sad: ["crying", "confused", "neutral"],
  crying: ["sad", "scared", "neutral"],
  scared: ["surprised", "sad", "neutral"],
  surprised: ["scared", "confused", "neutral"],
  confused: ["thinking", "surprised", "neutral"],
  thinking: ["confused", "neutral"],
  embarrassed: ["affectionate", "confused", "neutral"],
  angry: ["determined", "neutral"],
  sleepy: ["neutral"],
};

/** Emotion words `guessFromNames` searches for inside a model's own expression names. Every emotion is
 *  searched on its own, so this order is not a tie-break between them — one name can answer several —
 *  it only fixes the order the keys are written in, and the FIRST of those is the last-resort face
 *  `resolveExpression` hands back. Japanese included: many models ship named in Japanese. The guess only
 *  ever fills what a profile did not already write. */
const WORDS: [Emotion, string[]][] = [
  ["affectionate", ["affection", "love", "heart", "koi", "好き"]],
  ["embarrassed", ["embarrass", "blush", "shy", "照れ"]],
  ["determined", ["determin", "confident", "smug", "ドヤ"]],
  ["surprised", ["surprise", "shock", "驚"]],
  ["confused", ["confus", "puzzl", "question"]],
  ["thinking", ["think", "ponder", "hmm", "考"]],
  ["excited", ["excite", "star", "sparkl", "wow"]],
  ["crying", ["cry", "tears", "sob", "泣"]],
  ["sleepy", ["sleep", "tired", "yawn", "眠"]],
  ["scared", ["scare", "fear", "afraid", "怖"]],
  ["angry", ["angry", "anger", "mad", "怒"]],
  ["happy", ["happy", "smile", "joy", "笑"]],
  ["neutral", ["neutral", "normal", "default", "idle"]],
  ["sad", ["sad", "down", "悲"]],
];

export function guessFromNames(names: string[]): Partial<Record<Emotion, string>> {
  const out: Partial<Record<Emotion, string>> = {};
  for (const [emotion, words] of WORDS) {
    const hit = names.find((n) => words.some((w) => n.toLowerCase().includes(w)));
    if (hit) out[emotion] = hit;
  }
  return out;
}

export function resolveExpression(
  emotion: Emotion,
  map: Partial<Record<Emotion, string | number>>,
): string | number | undefined {
  const own = map[emotion];
  if (own !== undefined) return own;
  for (const next of FALLBACK[emotion] || []) {
    const alt = map[next];
    if (alt !== undefined) return alt;
  }
  // Every chain ends at neutral, so an UNMAPPED neutral strands the whole map — and a model whose
  // faces are named `happy`/`sad`/`angry` leaves it unmapped. Returning nothing is not a blank face:
  // the renderer skips the call and she keeps wearing whatever she had, for the rest of the session.
  // Any face beats a frozen one, so the first the model has stands in.
  return Object.values(map)[0];
}

/** `free1`, the model this app was built against — NOT the default any more and it cannot be: no
 *  redistribution licence, so it can never be shipped or fetched for anyone. The profile stays because
 *  anyone who owns a copy can still install it: this profile is matched by folder name. It declares BOTH
 *  its LipSync and EyeBlink groups empty, which is why the mouth and the blink are ours to write. */
export const AVATAR_FREE1: AvatarConfig = {
  dir: "free1",
  entry: "free1.model3.json",
  scale: 5.5,
  anchorY: 1.88,
  hideParts: [1, 2], // the bird and the hood: all 46 parts are named `PartN`, so index is the only handle
  defaultEmotion: "neutral",
  mouthParam: "ParamMouthOpenY",
  autoBlink: false,
  expressions: {
    neutral: "11.exp3.json",
    happy: "7.exp3.json",
    excited: "7.exp3.json",
    sad: "1.exp3.json",
    crying: "1.exp3.json",
    angry: "8.exp3.json",
    surprised: "3.exp3.json",
    embarrassed: "3.exp3.json",
    thinking: "6.exp3.json",
    sleepy: "0.exp3.json",
    affectionate: "2.exp3.json",
    confused: "5.exp3.json",
    scared: "3.exp3.json",
    determined: "7.exp3.json",
  },
};

/**
 * Niziiro Mao (`mao_pro`) — Live2D's own free sample and THE DEFAULT, what a person with no model of
 * their own gets. The Free Material License Agreement forbids REDISTRIBUTION, so the download has to
 * happen on the installer's own machine, from Live2D, and never from this repo; the licence file
 * belongs beside it, and only `runtime/` is needed. Unlike free1 it POPULATES its own `LipSync`
 * (`ParamA`) and `EyeBlink`, so the two flags below are not decoration: driving it free1's way writes
 * a parameter it does not have and the mouth never opens, and blinking it ourselves puts two blinkers
 * on one lid. Its `exp_01`…`exp_08` names say nothing, so the map is explicit and PROVISIONAL until
 * seen on screen.
 */
export const MAO_PRO: AvatarConfig = {
  dir: "mao_pro",
  entry: "runtime/mao_pro.model3.json",
  scale: 5.5,
  anchorY: 1.88,
  hideParts: ["PartHat", "PartWandA", "PartWandB"],
  defaultEmotion: "neutral",
  mouthParam: "ParamA",
  eyeParams: ["ParamEyeLOpen", "ParamEyeROpen"],
  autoBlink: true,
  // Each reading is what that file actually sets, not what its name suggests: `exp_01` eyes open, no
  // cheek, no smile · `exp_02` smiling eyes, closed · `exp_03` eyes shut, flat · `exp_04` eyes wide
  // 1.2 plus smile · `exp_05` brows angled down · `exp_06` cheek 1, blush · `exp_07` eyes wide,
  // eyeball form · `exp_08` eye form narrowed.
  expressions: {
    neutral: "exp_01",
    happy: "exp_02",
    excited: "exp_04",
    affectionate: "exp_06",
    embarrassed: "exp_06",
    angry: "exp_05",
    determined: "exp_08",
    confused: "exp_08",
    thinking: "exp_08",
    surprised: "exp_07",
    scared: "exp_07",
    sad: "exp_03",
    crying: "exp_03",
    sleepy: "exp_03",
  },
};

/** What a loaded model says about itself, as read by `readModelFacts`. Every field is optional because
 *  every field can be absent from a real `.model3.json` — that is the whole reason this exists — and
 *  reading never throws: a model that answers nothing is one we fall back for, not a crash on
 *  somebody's first run. The Cubism API is smaller than its docs suggest: there is no `getPartIds()`
 *  and no `getLipSyncParameters()`, so the part ids are taken from `_partIds` (or `_model.parts.ids`
 *  in the moc3) and the groups from `settings.json.Groups` — both read off a model loaded in a browser
 *  rather than assumed. */
export type ModelFacts = {
  expressionNames?: string[];
  lipSyncParams?: string[];
  eyeBlinkParams?: string[];
  partIds?: string[];
  /** Part id → the name its author typed, from the model's own DisplayInfo file. Most models number
   *  their parts `PartN`, which says nothing; the author's name is the only place the hat is called a
   *  hat. Absent whenever the model declares no DisplayInfo or it could not be read. */
  partNames?: Record<string, string>;
};

export function readModelFacts(model: unknown): ModelFacts {
  const out: ModelFacts = {};
  try {
    const internal = (model as { internalModel?: Record<string, unknown> })?.internalModel;
    const settings = internal?.settings as Record<string, unknown> | undefined;
    const exprs = settings?.expressions as { Name?: string; name?: string }[] | undefined;
    if (Array.isArray(exprs)) {
      out.expressionNames = exprs.map((e) => e?.Name ?? e?.name ?? "").filter(Boolean);
    }
    const json = settings?.json as Record<string, unknown> | undefined;
    const groups = (json?.Groups ?? settings?.groups) as { Name?: string; Ids?: string[] }[] | undefined;
    for (const g of groups || []) {
      if (g?.Name === "LipSync") out.lipSyncParams = g.Ids || [];
      if (g?.Name === "EyeBlink") out.eyeBlinkParams = g.Ids || [];
    }
    const core = internal?.coreModel as
      | { _partIds?: string[]; _model?: { parts?: { ids?: string[] } }; getPartIds?: () => string[] }
      | undefined;
    const ids = core?._partIds ?? core?._model?.parts?.ids
      ?? (typeof core?.getPartIds === "function" ? core.getPartIds() : undefined);
    if (Array.isArray(ids)) out.partIds = ids;
  } catch {
  }
  return out;
}

/** The author's own names for the parts, from the DisplayInfo file the model3.json points at. Every
 *  step is allowed to fail: a model that declares none, a file that 404s and a body that is not the
 *  shape expected all return {} and leave the id-only matching exactly as it was. */
export async function readPartNames(modelUrl: string, settingsJson: unknown): Promise<Record<string, string>> {
  try {
    const refs = (settingsJson as { FileReferences?: { DisplayInfo?: string } })?.FileReferences;
    const rel = refs?.DisplayInfo;
    if (!rel) return {};
    const url = new URL(rel, new URL(modelUrl, window.location.href)).toString();
    const res = await fetch(url);
    if (!res.ok) return {};
    const parts = ((await res.json()) as { Parts?: { Id?: string; Name?: string }[] })?.Parts;
    const out: Record<string, string> = {};
    for (const p of parts || []) {
      if (p?.Id && p?.Name) out[p.Id] = p.Name;
    }
    return out;
  } catch {
    return {};
  }
}

/** Words for a part worn over the face or held in front of it, used ONLY when no profile said
 *  otherwise — someone who brings their own model owns that choice, and a companion silently missing
 *  her hat is worse than one wearing it. `coversTheFace` needs the word to END where it ends:
 *  `PartHood` is over her face and `PartHoodie` is a jumper (Live2D's own sample ships both), so a
 *  lowercase letter after the word disqualifies the match while a numbered suffix does not. */
const IN_THE_WAY = ["hat", "cap", "hood", "mask", "wand", "stick", "prop", "item"];

/** The same idea in the languages most free models are authored in. Matched anywhere in the author's
 *  name rather than at its start, because these are written as compounds (`帽子沿` is the brim OF the
 *  hat). Deliberately short: over-hiding takes something off her the owner wanted. */
const IN_THE_WAY_CJK = ["帽", "冠", "头饰", "頭飾", "髪飾", "面具", "マスク", "旗", "杖"];

function coversTheFace(id: string, name?: string): boolean {
  const rest = id.replace(/^part[_-]?/i, "");
  if (rest !== id) {
    const word = IN_THE_WAY.find((w) => rest.toLowerCase().startsWith(w));
    if (word && !/[a-z]/.test(rest.charAt(word.length))) return true;
  }
  if (!name) return false;
  if (IN_THE_WAY_CJK.some((w) => name.includes(w))) return true;
  // The author's name is prose, so the word can sit anywhere in it — but only as a whole word, or
  // "Hatch" and "Capsule" come off with the hat.
  return IN_THE_WAY.some((w) => new RegExp(`\\b${w}\\b`, "i").test(name));
}

/** Build a working config for a model nobody wrote a profile for: names first, because most models
 *  name their faces after the feeling; when fewer than three names matched a feeling the guess is
 *  dropped and what exists is spread over the emotions in a fixed order, so the same emotion always
 *  gets the same face — a wrong-but-stable face reads as a personality, a random one reads as a bug.
 *  What the model declares wins for the mouth and the lids, and a model that declares neither leaves
 *  both to us. */
export function adaptTo(facts: ModelFacts, dir: string, entry: string): AvatarConfig {
  const names = facts.expressionNames || [];
  const guessed = guessFromNames(names);
  const named = Object.keys(guessed).length;

  let expressions: Partial<Record<Emotion, string | number>> = guessed;
  if (named < 3 && names.length) {
    const ORDER: Emotion[] = ["neutral", "happy", "sad", "excited", "angry", "embarrassed",
                              "surprised", "determined", "thinking", "affectionate", "crying",
                              "scared", "confused", "sleepy"];
    expressions = {};
    ORDER.forEach((emotion, i) => {
      if (i < names.length) expressions[emotion] = names[i];
    });
  }

  const lip = facts.lipSyncParams || [];
  const eyes = facts.eyeBlinkParams || [];
  return {
    dir,
    entry,
    hideParts: (facts.partIds || []).filter((id) => coversTheFace(id, facts.partNames?.[id])),
    defaultEmotion: "neutral",
    // `Groups[].Ids` come out in whatever order the rigger typed. A rig that lists
    // ["ParamMouthForm","ParamMouthOpenY"] would otherwise be driven by its WIDTH, and a five-vowel
    // rig by whichever vowel came first, so a parameter that actually opens the mouth wins.
    mouthParam: lip.find((id) => OPENS_THE_MOUTH.includes(id)) || lip[0] || "ParamMouthOpenY",
    eyeParams: eyes.length ? eyes : undefined,
    autoBlink: eyes.length > 0,
    expressions,
  };
}

const PROFILES: Record<string, AvatarConfig> = { free1: AVATAR_FREE1, mao_pro: MAO_PRO };

/** Where the probe cannot measure: one model's two numbers, and the only pair ever measured against
 *  this card. `_SCALE`/`_ANCHOR_Y` retune them for a model shaped differently. */
export const FIXED_FRAMING = { scale: 5.5, anchorY: 1.88 };

export function profileFor(dir: string, entry: string): AvatarConfig {
  const base = PROFILES[dir] ?? {
    dir,
    entry: `${dir}.model3.json`,
    defaultEmotion: "neutral" as Emotion,
    // Left UNSET on purpose: `completeConfig` fills what nobody answered, and an empty array is an
    // answer — "this model wears nothing in the way".
  };
  return {
    ...base,
    dir,
    // An entry found ON DISK beats the profile's: the same model gets unpacked flat by one person and
    // under `runtime/` by the next, so a written path is a guess about somebody else's folder.
    entry: entry || process.env.NEXT_PUBLIC_LIVE2D_ENTRY || base.entry,
    scale: Number(process.env.NEXT_PUBLIC_LIVE2D_SCALE) || base.scale,
    anchorY: Number(process.env.NEXT_PUBLIC_LIVE2D_ANCHOR_Y) || base.anchorY,
  };
}

export type InstalledModel = { dir: string; entry: string };
/** What `GET /api/avatar` answers: what is on disk, and which one she wears. */
export type AvatarAnswer = {
  installed?: InstalledModel[];
  selected?: InstalledModel | null;
  models_dir?: string;
};
/** A model to draw and where its bytes come from. `api` is served by the backend out of the models
 *  directory and needs the API credential on the first fetch; `public` is the pre-runtime path Next
 *  serves itself. */
export type AvatarChoice = { config: AvatarConfig; url: string; served: "api" | "public" };

const encodePath = (p: string) => p.split("/").map(encodeURIComponent).join("/");

/** The build-time pair, when somebody set it: their model sits in `public/models/` and keeps working.
 *  Only ever a FALLBACK — a value `next build` froze into the bundle must not outvote a model actually
 *  installed, which is the whole reason the build-time answer had to go. */
export function legacyChoice(): AvatarChoice | null {
  const dir = process.env.NEXT_PUBLIC_LIVE2D_MODEL;
  if (!dir) return null;
  const config = profileFor(dir, "");
  return {
    config,
    url: `/models/${encodePath(config.dir)}/${encodePath(config.entry)}`,
    served: "public",
  };
}

export function pickAvatar(answer: AvatarAnswer | null, apiUrl: string): AvatarChoice | null {
  const sel = answer?.selected;
  if (!sel?.dir || !sel?.entry) return legacyChoice();
  return {
    config: profileFor(sel.dir, sel.entry),
    url: `${apiUrl}/api/models/raw/${encodePath(sel.dir)}/${encodePath(sel.entry)}`,
    served: "api",
  };
}

/** A config whose framing is settled, which is the only kind the renderer can use. */
export type FramedConfig = AvatarConfig & { scale: number; anchorY: number };

/** The config to actually drive a loaded model with: what was written for it, completed by what the
 *  model says about itself and by what the probe measured on it. A written answer always wins — only
 *  the blanks are filled — and the fixed pair is the last resort, for a model that cannot be
 *  measured. */
export function completeConfig(
  base: AvatarConfig,
  facts: ModelFacts,
  framing?: Framing | null,
): FramedConfig {
  const auto = adaptTo(facts, base.dir, base.entry);
  return {
    ...base,
    expressions: base.expressions ?? auto.expressions,
    hideParts: base.hideParts ?? auto.hideParts,
    mouthParam: base.mouthParam ?? auto.mouthParam,
    eyeParams: base.eyeParams ?? auto.eyeParams,
    autoBlink: base.autoBlink ?? auto.autoBlink,
    scale: base.scale ?? framing?.scale ?? FIXED_FRAMING.scale,
    anchorY: base.anchorY ?? framing?.anchorY ?? FIXED_FRAMING.anchorY,
  };
}
