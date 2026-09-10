/**
 * Live2D control. Nothing here knows WHICH model it drives — parameter names, faces and parts arrive
 * in an AvatarConfig — and every write is wrapped, so a parameter or part this model does not have is
 * a silent no-op rather than a crash. Three invariants, each measured on a loaded model: nothing may
 * write a parameter BETWEEN frames, because Cubism restores every parameter at the top of its own
 * update, so an outside write survives one partial frame and is then wiped (0.9 → 0.562 → 0); the
 * head goes in on `afterMotionUpdate`, BEFORE physics, or the hair never answers it, and the face
 * goes in last, after the expression that just applied; and the engine is a second animator — an idle
 * motion, plus its own blinker on a model that declares one — both taken away, but CubismBreath KEPT.
 */

import { type AvatarConfig, resolveExpression } from "@/lib/avatar-config";
import { type Emotion } from "@/lib/expressions";

interface CoreModel {
  setParameterValueById: (id: string, value: number) => void;
  setPartOpacityByIndex?: (index: number, opacity: number) => void;
  setPartOpacityById?: (id: string, opacity: number) => void;
}
/** Only the surface we drive. `expression()` takes a NAME or an INDEX because models in the wild use
 *  both; `eyeBlink` is the model's own blinker, present only when it declares an EyeBlink group;
 *  `motionManager.groups.idle` names the group the engine auto-plays; and the internal model is an
 *  EventEmitter whose `afterMotionUpdate` is the seam between the motions and physics. */
interface Live2DModelLike {
  expression: (nameOrIndex: string | number) => void;
  autoInteract?: boolean;
  x: number;
  y: number;
  anchor: { set: (x: number, y: number) => void };
  scale: { set: (v: number) => void };
  internalModel: {
    originalHeight?: number;
    coreModel: CoreModel;
    eyeBlink?: unknown;
    /** The model's own pointer aim. Kept centred so the head is ours alone. */
    focusController?: { x: number; y: number; targetX: number; targetY: number };
    motionManager?: { groups?: { idle?: string }; stopAllMotions?: () => void };
    on?: (event: string, listener: () => void) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    update: (...args: any[]) => void;
    __kotobaHooked?: boolean;
  };
}

const DEFAULT_MOUTH = "ParamMouthOpenY";
const DEFAULT_EYES = ["ParamEyeLOpen", "ParamEyeROpen"];
/** A motion group no model can define. `""` is NOT safe: Live2D's own sample files its six ungrouped
 *  motions under exactly that name, so pointing idle at it plays all six instead of none. */
const NO_MOTION_GROUP = "__kotoba_no_idle__";

type Behavior = "asleep" | "startled" | "idle" | "speaking" | "focus";

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

type MotionProfile = { energy: number; pitch: number; tilt: number; gaze: number; speed: number };
const MOTION: Record<string, MotionProfile> = {
  excited: { energy: 1.5, pitch: 4, tilt: 0, gaze: 0, speed: 1.5 },
  happy: { energy: 1.3, pitch: 3, tilt: 2, gaze: 0, speed: 1.3 },
  affectionate: { energy: 0.9, pitch: 1, tilt: 7, gaze: 0, speed: 0.9 },
  surprised: { energy: 1.6, pitch: 6, tilt: 0, gaze: 0, speed: 1.9 },
  scared: { energy: 1.5, pitch: 3, tilt: -4, gaze: 0.3, speed: 2.0 },
  thinking: { energy: 0.7, pitch: -1, tilt: 9, gaze: 0.4, speed: 0.7 },
  confused: { energy: 0.8, pitch: 0, tilt: 8, gaze: 0.35, speed: 0.8 },
  sad: { energy: 0.5, pitch: -8, tilt: 3, gaze: -0.1, speed: 0.5 },
  crying: { energy: 0.45, pitch: -11, tilt: 2, gaze: -0.1, speed: 0.5 },
  sleepy: { energy: 0.35, pitch: -7, tilt: 4, gaze: 0, speed: 0.4 },
  angry: { energy: 1.1, pitch: -2, tilt: -3, gaze: 0, speed: 1.2 },
  determined: { energy: 1.1, pitch: 2, tilt: 0, gaze: 0, speed: 1.1 },
  embarrassed: { energy: 0.8, pitch: -3, tilt: 6, gaze: 0.3, speed: 0.9 },
  neutral: { energy: 1.0, pitch: 0, tilt: 0, gaze: 0, speed: 1.0 },
};
const profileFor = (e: string): MotionProfile => MOTION[e] || MOTION.neutral;

const FOCUS_POOL: Emotion[] = ["thinking", "determined", "excited", "neutral", "happy"];

export class Live2DController {
  private model: Live2DModelLike;
  private cfg: AvatarConfig;
  private mouth: string;
  private eyes: string[];
  private current: Emotion = "neutral";
  private behavior: Behavior = "asleep";

  private pose = { ax: 0, ay: 0, az: 0, bx: 0, eyeX: 0, eyeY: 0, eyeOpen: 1 };
  private target = { ax: 0, ay: 0, az: 0, bx: 0, eyeX: 0, eyeY: 0, eyeOpen: 1 };
  private nextRetarget = 0;
  private t0 = performance.now();
  private nextBlink = 0;
  private blinkStart = -1;
  private nextSaccade = 0;
  private sacX = 0;
  private sacY = 0;
  private lastEmotionAt = -1e9;
  private nextFocusExpr = 0;
  private mouthAmp = 0;
  private blinkValue = 1;
  private headWritten = false;

  constructor(model: Live2DModelLike, config: AvatarConfig) {
    this.model = model;
    this.cfg = config;
    this.mouth = config.mouthParam || DEFAULT_MOUTH;
    this.eyes = config.eyeParams?.length ? config.eyeParams : DEFAULT_EYES;
    try {
      this.model.autoInteract = false;
    } catch {
    }
    this.takeOverAnimation();
    this.installHook();
    this.setAsleep();
  }

  /** Kotoba animates the head, the lids and the mouth herself, so nothing else may: the auto-played
   *  idle motion always goes, and the model's own blinker goes with it when the profile says it has one
   *  (`autoBlink`) — invariant 3.
   *
   *  The focus controller is the third: `autoInteract = false` stops the model listening for the
   *  pointer, and it still drifts to wherever the pointer last was, so her head is aimed by the mouse
   *  and by us at once. Pinned to centre every frame, she looks where SHE is looking. */
  private takeOverAnimation(): void {
    const im = this.model.internalModel;
    try {
      const mm = im.motionManager;
      if (mm?.groups) mm.groups.idle = NO_MOTION_GROUP;
      mm?.stopAllMotions?.();
    } catch {
    }
    if (this.cfg.autoBlink) {
      try {
        im.eyeBlink = undefined;
      } catch {
      }
    }
  }

  private centreFocus(): void {
    try {
      const f = this.model.internalModel?.focusController;
      if (f) {
        f.targetX = 0;
        f.targetY = 0;
        f.x = 0;
        f.y = 0;
      }
    } catch {
    }
  }

  /** Two write points per frame, because the engine's own steps sit between them: the head early, on
   *  `afterMotionUpdate`, so physics can read it; the face late, after everything (invariant 2). */
  private installHook(): void {
    const im = this.model.internalModel;
    const core = im.coreModel;
    if (im.__kotobaHooked) return;

    im.on?.("afterMotionUpdate", () => {
      this.advance();
      this.writeHead(core);
      this.headWritten = true;
    });

    const original = im.update.bind(im);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    im.update = (...args: any[]) => {
      this.centreFocus();
      original(...args);
      // A part is addressed by id when the model gives it a meaningful one, by index when it does not.
      for (const part of this.cfg.hideParts ?? []) {
        try {
          if (typeof part === "number") core.setPartOpacityByIndex?.(part, 0);
          else core.setPartOpacityById?.(part, 0);
        } catch {
        }
      }
      if (!this.headWritten) {
        // An engine that does not emit the event still gets a pose, just one physics cannot see.
        this.advance();
        this.writeHead(core);
      }
      this.headWritten = false;
      this.writeFace(core);
    };
    im.__kotobaHooked = true;
  }

  private set(core: CoreModel, id: string, v: number): void {
    try {
      core.setParameterValueById(id, v);
    } catch {
    }
  }

  private writeHead(core: CoreModel): void {
    this.set(core, "ParamAngleX", this.pose.ax);
    this.set(core, "ParamAngleY", this.pose.ay);
    this.set(core, "ParamAngleZ", this.pose.az);
    this.set(core, "ParamBodyAngleX", this.pose.bx);
    this.set(core, "ParamEyeBallX", Math.max(-1, Math.min(1, this.pose.eyeX + this.sacX)));
    this.set(core, "ParamEyeBallY", Math.max(-1, Math.min(1, this.pose.eyeY + this.sacY)));
    // ParamBreath is NOT written here, and it is not missing. CubismBreath drives it between this
    // point and the render: 0.0000 → 0.2500 on a 3.23s cycle, measured inside physics.evaluate on
    // both models. From OUTSIDE the frame the same parameter reads a flat 0.00 — the reading that
    // once made it look dead. Writing ours on top would only fight it.
  }

  /** Lids and mouth, after the expression has had its say. The lids follow the pose while she is
   *  asleep and while it is still ramping open (a wake-up and a startle both ramp), and otherwise
   *  carry the blink — which is 1 between blinks, so the value is written EVERY frame.
   *
   *  Leaving them alone between blinks looked like deference to the expression and was a way to stay
   *  shut forever: taking over the animation removes the model's own blinker and idle motion, the two
   *  things that used to reopen them, and an expression that blends Multiply cannot open anything on
   *  its own. Writing the base each frame composes with those expressions instead of fighting them. */
  private writeFace(core: CoreModel): void {
    if (this.behavior === "asleep" || this.pose.eyeOpen < 0.85) {
      for (const id of this.eyes) this.set(core, id, this.pose.eyeOpen);
    } else {
      for (const id of this.eyes) this.set(core, id, this.blinkValue);
    }
    this.set(core, this.mouth, this.behavior === "startled" ? 0.7 : this.mouthAmp);
  }

  private advance(): void {
    const now = performance.now();
    const time = (now - this.t0) / 1000;
    const prof = profileFor(this.current);

    if (this.behavior === "idle" && now > this.nextRetarget) {
      this.target.ax = (Math.random() * 2 - 1) * 14 * prof.energy;
      this.target.ay = (Math.random() * 2 - 1) * 8 * prof.energy + prof.pitch;
      this.target.az = (Math.random() * 2 - 1) * 6 + prof.tilt;
      this.target.eyeX = this.target.ax / 30 + prof.gaze;
      this.target.eyeY = this.target.ay / 30;
      this.target.bx = this.target.ax * 0.3;
      this.nextRetarget = now + (1800 + Math.random() * 2600) / prof.speed;
    }

    if (this.behavior === "startled") {
      if (now > this.nextRetarget) {
        this.target.ax = (Math.random() * 2 - 1) * 26;
        this.target.ay = 6 + Math.random() * 16;
        this.target.az = (Math.random() * 2 - 1) * 16;
        this.target.eyeX = (Math.random() * 2 - 1) * 0.8;
        this.target.eyeY = (Math.random() * 2 - 1) * 0.5;
        this.target.bx = this.target.ax * 0.5;
        this.nextRetarget = now + 140 + Math.random() * 160;
      }
      this.target.eyeOpen = 1;
    } else if (this.behavior === "speaking") {
      const amp = prof.energy;
      this.target.ax = Math.sin(time * 0.7 * prof.speed) * 3.5 * amp;
      this.target.ay = Math.sin(time * 1.1 * prof.speed) * 2.2 * amp + prof.pitch * 0.6;
      this.target.az = Math.sin(time * 0.5 * prof.speed) * 2.0 + prof.tilt * 0.6;
      this.target.eyeX = prof.gaze * 0.5;
      this.target.eyeY = 0;
      this.target.bx = this.target.ax * 0.4;
      this.target.eyeOpen = 1;
    } else if (this.behavior === "focus") {
      if (now > this.nextRetarget) {
        const lookUp = Math.random() < 0.2;
        this.target.ax = (Math.random() * 2 - 1) * 11 * (0.6 + prof.energy * 0.5);
        this.target.ay = (lookUp ? 5 + Math.random() * 7 : -11 + (Math.random() * 2 - 1) * 3) + prof.pitch * 0.4;
        this.target.az = (Math.random() * 2 - 1) * 5 + prof.tilt * 0.7;
        this.target.eyeX = this.target.ax / 34 + prof.gaze * 0.5;
        this.target.eyeY = lookUp ? 0.12 : -0.42;
        this.target.bx = this.target.ax * 0.2;
        this.nextRetarget = now + (1000 + Math.random() * 1400) / prof.speed;
      }
      this.target.eyeOpen = 1;
      if (now - this.lastEmotionAt > 2500 && now > this.nextFocusExpr) {
        this._applyExpression(FOCUS_POOL[Math.floor(Math.random() * FOCUS_POOL.length)]);
        this.nextFocusExpr = now + 2400 + Math.random() * 2200;
      }
    } else if (this.behavior === "idle") {
      this.target.eyeOpen = 1;
    } else {
      this.target.ax = Math.sin(time * 0.3) * 2;
      this.target.ay = -12 + Math.sin(time * 0.4) * 1.5;
      this.target.az = -6;
      this.target.eyeOpen = 0;
      this.target.bx = 0;
    }

    const k =
      this.behavior === "asleep" ? 0.03
      : this.behavior === "startled" ? 0.32
      : this.behavior === "focus" ? 0.05
      : 0.07;
    this.pose.ax = lerp(this.pose.ax, this.target.ax, k);
    this.pose.ay = lerp(this.pose.ay, this.target.ay, k);
    this.pose.az = lerp(this.pose.az, this.target.az, k);
    this.pose.bx = lerp(this.pose.bx, this.target.bx, k);
    this.pose.eyeX = lerp(this.pose.eyeX, this.target.eyeX, k);
    this.pose.eyeY = lerp(this.pose.eyeY, this.target.eyeY, k);
    this.pose.eyeOpen = lerp(this.pose.eyeOpen, this.target.eyeOpen, 0.12);

    const awake =
      this.behavior === "idle" || this.behavior === "speaking" || this.behavior === "focus";

    if (awake && now > this.nextSaccade) {
      this.sacX = (Math.random() * 2 - 1) * 0.22;
      this.sacY = (Math.random() * 2 - 1) * 0.14;
      this.nextSaccade = now + 900 + Math.random() * 2200;
    } else if (!awake) {
      this.sacX = 0;
      this.sacY = 0;
    }

    this.blinkValue = 1;
    if (awake) {
      if (this.blinkStart < 0 && now > this.nextBlink) {
        this.blinkStart = now;
        this.nextBlink = now + (Math.random() < 0.18 ? 220 : 2500 + Math.random() * 3500);
      }
      if (this.blinkStart >= 0) {
        const t = now - this.blinkStart;
        const DUR = 150;
        if (t >= DUR) this.blinkStart = -1;
        else this.blinkValue = t < DUR / 2 ? 1 - t / (DUR / 2) : (t - DUR / 2) / (DUR / 2);
      }
    }
  }

  /** Change what is hidden while she is on screen. The frame hook re-hides the listed parts forever, so
   *  it can never put one BACK: a part dropped from the list has to be returned to full opacity once,
   *  here, or it stays invisible with nothing left claiming it should be. */
  setHiddenParts(parts: (string | number)[]): void {
    const core = this.model.internalModel?.coreModel;
    for (const was of this.cfg.hideParts ?? []) {
      if (parts.includes(was)) continue;
      try {
        if (typeof was === "number") core?.setPartOpacityByIndex?.(was, 1);
        else core?.setPartOpacityById?.(was, 1);
      } catch {
      }
    }
    this.cfg.hideParts = parts;
  }

  /** Every part this model has, in the order the core lists them, so a chooser can offer them. */
  partIds(): string[] {
    const core = this.model.internalModel?.coreModel as
      | { _partIds?: string[]; _model?: { parts?: { ids?: string[] } } }
      | undefined;
    return core?._partIds ?? core?._model?.parts?.ids ?? [];
  }

  setAsleep(): void {
    this.behavior = "asleep";
    this.setEmotion("sleepy");
  }

  startle(): void {
    this.behavior = "startled";
    this.setEmotion("scared");
    this.pose.eyeOpen = 0;
    this.target.eyeOpen = 1;
    this.pose.ay = -12;
    this.nextRetarget = 0;
  }

  /** The one place the profile's `defaultEmotion` is worn. `setAsleep` keeps `sleepy`: that is the
   *  PHASE talking, not the model. */
  settle(): void {
    this.behavior = "idle";
    this.setEmotion(this.cfg.defaultEmotion ?? "neutral");
    this.nextRetarget = performance.now() + 400;
  }

  private workFocused = false;

  setFocus(on: boolean): void {
    this.workFocused = on;
    if (on) this.nextFocusExpr = 0;
    if (this.behavior === "asleep" || this.behavior === "startled") return;
    if (this.behavior === "speaking") return; // a glance-back; she resumes focus when she stops
    this.behavior = on ? "focus" : "idle";
    this.nextRetarget = 0;
  }

  setSpeaking(speaking: boolean): void {
    if (this.behavior === "asleep" || this.behavior === "startled") return;
    if (speaking) this.behavior = "speaking";
    else this.behavior = this.workFocused ? "focus" : "idle";
  }

  /** Records the amplitude; `writeFace` is what writes it, on the next frame. */
  setLipSync(amplitude: number): void {
    this.mouthAmp = Math.max(0, Math.min(1, amplitude));
  }

  /** Like setEmotion but does NOT mark it external — the focus cycle uses it, and stands down for the
   *  2.5s after the backend sets one, so her REAL reaction shows first. */
  private _applyExpression(emotion: Emotion): void {
    this.current = emotion;
    const expr = resolveExpression(emotion, this.cfg.expressions ?? {});
    if (expr !== undefined) this.model.expression(expr);
  }

  setEmotion(emotion: Emotion): void {
    this.lastEmotionAt = performance.now();
    this._applyExpression(emotion);
  }

  get emotion(): Emotion {
    return this.current;
  }
  get modelRef(): Live2DModelLike {
    return this.model;
  }
}
