/**
 * Where a model's face is, measured off the model itself. Framing came from two numbers hand-set per
 * model, and a repository that ships no model and invites any model is exactly where hand-set numbers
 * will not be. So the head is found by perturbing `ParamAngleZ` — a near-rigid tilt, unlike the turn
 * on X, which drags the neck and collar — and the eyes by closing the lids.
 *
 * Every box comes from the rig's OWN rest pose, never from the screen: between frames the parameter
 * array and the vertices describe different poses, so measuring off the vertices charged the head with
 * everything the last frame had moved — a head box the size of the whole figure.
 */

export type Framing = { scale: number; anchorY: number };

type Parameters = {
  ids: string[];
  values: Float32Array;
  minimumValues: Float32Array;
  maximumValues: Float32Array;
  /** The rig's rest pose. Every core that can open a `.moc3` exposes it; typed optional so a stand-in
   *  in a test is not forced to invent one. */
  defaultValues?: Float32Array;
};

type Drawables = {
  count: number;
  vertexPositions: Float32Array[];
  dynamicFlags: Uint8Array;
  opacities: Float32Array;
  parentPartIndices?: Int32Array;
};

type Parts = { ids: string[]; parentIndices?: Int32Array };

/** The Cubism model behind pixi's wrapper — the only object this file touches. */
type RawModel = {
  parameters: Parameters;
  drawables: Drawables;
  parts: Parts;
  canvasinfo: { CanvasHeight: number; PixelsPerUnit: number };
  update: () => void;
};

type Box = { y0: number; y1: number; cy: number };

const HEAD_PARAM = "ParamAngleZ";
/** The pose the app HOLDS, which is not the pose the rig declares: the controller writes the angles
 *  to nothing and the lids open on every frame. Measuring from the rig's own defaults read a head
 *  already tilted, or lids that could not close because they were shut to begin with. */
const NEUTRAL: [string, number][] = [["ParamAngleX", 0], ["ParamAngleY", 0], ["ParamAngleZ", 0]];
const LIDS_OPEN = 1;
/** A drawable that moves less than this fraction of the biggest response is only partly in the box.
 *  Closing one model's eyes drags geometry into a box 3.8x taller than the eye; the head needs no such
 *  cut, and holds its shape across 1% to 5% on every model measured. Cubism is deterministic and an
 *  untouched drawable is bit-identical, so neither number is a noise floor. */
const EYE_THRESHOLD = 0.2;
const HEAD_THRESHOLD = 0.05;
/** How much of the frame the head fills, the line the eyes sit on, and — with no eyes to sit anything
 *  on — how much clearance her crown gets instead. */
const HEAD_FILL = 0.96;
const EYE_LINE = 0.45;
const HEAD_TOP = 0.02;
const IS_VISIBLE = 1;
const MAX_PART_DEPTH = 24;

/** Read off the LOADED model, never off `.model3.json`: two of three models tested declare their
 *  `EyeBlink` group with an empty `Ids`, so the file names no lid the model plainly has. */
function eyeParams(ids: string[]): string[] {
  return ids.filter((id) => /eye/i.test(id) && /open/i.test(id));
}

function restPose(p: Parameters): Float32Array {
  const rest = Float32Array.from(p.defaultValues ?? p.values);
  const pin = (id: string, v: number) => {
    const i = p.ids.indexOf(id);
    // Clamped: a rig whose lids stop at 0.9 cannot be asked for 1, and asking would measure nothing.
    if (i >= 0) rest[i] = Math.min(p.maximumValues[i], Math.max(p.minimumValues[i], v));
  };
  for (const [id, v] of NEUTRAL) pin(id, v);
  for (const id of eyeParams(p.ids)) pin(id, LIDS_OPEN);
  return rest;
}
/** Drawables under a hidden part, its children included. The default model wears a hat the app then
 *  hides: counted in, its head box measures 37% too tall and twice too wide. */
function hiddenDrawables(raw: RawModel, hideParts: (number | string)[]): Set<number> {
  const { drawables: d, parts } = raw;
  const out = new Set<number>();
  for (const part of hideParts) {
    const target = typeof part === "number" ? part : parts.ids.indexOf(part);
    if (target < 0) continue;
    for (let j = 0; j < d.count; j++) {
      let owner = d.parentPartIndices ? d.parentPartIndices[j] : -1;
      for (let depth = 0; owner >= 0 && depth < MAX_PART_DEPTH; depth++) {
        if (owner === target) {
          out.add(j);
          break;
        }
        owner = parts.parentIndices ? parts.parentIndices[owner] : -1;
      }
    }
  }
  return out;
}

/**
 * The box of everything these parameters move, in model units, with the pose put back on the way out.
 * `pick` is a direction and not a distance from the default: huohuo's `ParamEyeLOpen` runs 0..2 from a
 * default of 1, and driving it to the max moves ZERO vertices because the rig authored no keys past
 * ~1.1 — a silent empty box for anything that reaches for the far end. `perVertex` keeps a partly
 * moved drawable's still points out of the box, which is what makes the eye box an eye.
 */
function movedBox(
  raw: RawModel,
  rest: Float32Array,
  ids: string[],
  pick: "min" | "max",
  threshold: number,
  skip: Set<number>,
  perVertex: boolean,
): Box | null {
  const p = raw.parameters;
  const d = raw.drawables;
  const indices = ids.map((id) => p.ids.indexOf(id)).filter((i) => i >= 0);
  if (!indices.length) return null;

  const saved = Float32Array.from(p.values);
  try {
    // Both halves of the difference are taken from the same pose, so the only thing separating them is
    // `ids`. Reading `vertexPositions` as they stand instead measures every parameter a frame moved.
    p.values.set(rest);
    raw.update();
    const before: Float32Array[] = [];
    for (let j = 0; j < d.count; j++) before.push(Float32Array.from(d.vertexPositions[j]));
    for (const i of indices) p.values[i] = pick === "min" ? p.minimumValues[i] : p.maximumValues[i];
    raw.update();

    const response = new Float64Array(d.count);
    let peak = 0;
    for (let j = 0; j < d.count; j++) {
      const a = before[j];
      const b = d.vertexPositions[j];
      let most = 0;
      for (let k = 0; k < a.length; k += 2) {
        const m = Math.hypot(b[k] - a[k], b[k + 1] - a[k + 1]);
        if (m > most) most = m;
      }
      response[j] = most;
      if (most > peak) peak = most;
    }

    const cut = peak * threshold;
    let y0 = Infinity;
    let y1 = -Infinity;
    let moved = 0;
    for (let j = 0; j < d.count; j++) {
      if (response[j] <= cut) continue;
      if (skip.has(j) || !(d.dynamicFlags[j] & IS_VISIBLE) || d.opacities[j] <= 0.01) continue;
      moved++;
      const a = before[j];
      const b = d.vertexPositions[j];
      for (let k = 0; k < a.length; k += 2) {
        if (perVertex && Math.hypot(b[k] - a[k], b[k + 1] - a[k + 1]) <= cut) continue;
        if (a[k + 1] < y0) y0 = a[k + 1];
        if (a[k + 1] > y1) y1 = a[k + 1];
      }
    }

    return moved && y1 > y0 ? { y0, y1, cy: (y0 + y1) / 2 } : null;
  } finally {
    // Whatever happened in there, the model does not keep the pose it took to measure it: an
    // exception used to leave the head tilted to its maximum.
    p.values.set(saved);
    raw.update();
  }
}

/** The preferred direction first: `pick` encodes which way a parameter has to travel to show what is
 *  being measured. Only a rig that answers it with no movement at all is asked the other way. */
function eitherWay(
  raw: RawModel,
  rest: Float32Array,
  ids: string[],
  first: "min" | "max",
  threshold: number,
  skip: Set<number>,
  perVertex: boolean,
): Box | null {
  const other = first === "max" ? "min" : "max";
  return (
    movedBox(raw, rest, ids, first, threshold, skip, perVertex) ??
    movedBox(raw, rest, ids, other, threshold, skip, perVertex)
  );
}


/**
 * Framing for a model nobody measured by hand, or `null` when the model cannot be measured at all.
 * It degrades in layers rather than all at once: with both boxes the eyes sit on a fixed line, with
 * only a head box her crown does — still a face, never a chest — and with neither the caller's fixed
 * numbers stand.
 */
export function probeFraming(coreModel: unknown, hideParts: (number | string)[] = []): Framing | null {
  try {
    const wrapper = coreModel as { getModel?: () => RawModel; _model?: RawModel } | null;
    const raw = wrapper?.getModel?.() ?? wrapper?._model;
    if (!raw?.parameters?.ids || !raw.drawables?.count || !raw.canvasinfo) return null;

    const height = raw.canvasinfo.CanvasHeight / raw.canvasinfo.PixelsPerUnit;
    if (!(height > 0)) return null;

    const hidden = hiddenDrawables(raw, hideParts);
    const rest = restPose(raw.parameters);
    const head = eitherWay(raw, rest, [HEAD_PARAM], "max", HEAD_THRESHOLD, hidden, false);
    if (!head) return null;

    const scale = (HEAD_FILL * height) / (head.y1 - head.y0);
    const eye = eitherWay(raw, rest, eyeParams(raw.parameters.ids), "min", EYE_THRESHOLD, hidden, true);
    const anchorY = eye
      ? EYE_LINE + (eye.cy * scale) / height
      : HEAD_TOP + (head.y1 * scale) / height;
    return { scale, anchorY };
  } catch {
    return null;
  }
}
