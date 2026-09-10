/**
 * Live2D avatar inside the webcam card — pixi-live2d-display ^0.4 on pixi.js ^6, Cubism 4 core loaded in
 * layout. A failed model load must fail VISIBLY: without the .catch → onError path the fullscreen
 * CallLoader spun forever and swallowed every click.
 *
 * Why Pixi gets a fresh canvas per mount, why the reframe and the framebuffer resize are split, and
 * why two effects poll for the controller instead of reacting once are each stated at those lines.
 */
"use client";

import { type AvatarConfig, completeConfig, readModelFacts, readPartNames } from "@/lib/avatar-config";
import { useEffect, useRef } from "react";

import { getMouthAmplitude, type VoiceAudioSource } from "@/lib/audio";
import { probeFraming } from "@/lib/head-probe";
import { Live2DController } from "@/lib/live2d-controller";
import type { Emotion } from "@/lib/expressions";

type Conversation = VoiceAudioSource;

export default function Live2DCanvas({
  config: profile,
  modelUrl,
  emotion,
  conversation,
  phase,
  speaking,
  working = false,
  onReady,
  onError,
  onModel,
}: {
  /** Which model, resolved at runtime — never read from the environment here, or it would be frozen
   *  into the bundle again. Both are fixed for the life of the mount; the shell remounts on a change. */
  config: AvatarConfig;
  modelUrl: string;
  emotion: Emotion;
  conversation: Conversation | null;
  phase: "offline" | "ringing" | "live";
  speaking: boolean;
  working?: boolean;
  onReady?: () => void;
  onError?: () => void;
  /** Handed the loaded model's parts once, so a chooser can offer them without reaching for a global
   *  or the controller's type. `hidden` is normalised to ids — a profile may address a part by index,
   *  and no chooser should have to know which. */
  onModel?: (info: {
    parts: { id: string; name?: string }[];
    hidden: string[];
    setHidden: (ids: string[]) => void;
  }) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<Live2DController | null>(null);
  const conversationRef = useRef<Conversation | null>(conversation);
  conversationRef.current = conversation;
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const onModelRef = useRef(onModel);
  onModelRef.current = onModel;
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  const workingRef = useRef(working);
  workingRef.current = working;

  useEffect(() => {
    const parent = containerRef.current!;
    let destroyed = false;
    let model: { destroy: () => void } | null = null;

    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const PIXI = require("pixi.js");
      window.PIXI = PIXI;
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { Live2DModel } = require("pixi-live2d-display/cubism4");

      // Pixi builds its OWN canvas here: a canvas hands out ONE WebGL context ever, and a remount on a
      // reused one gets the destroyed context back.
      const app = new PIXI.Application({
        resizeTo: parent,
        backgroundAlpha: 0,
        antialias: true,
      });
      const view = app.view as HTMLCanvasElement;
      view.style.cssText = "position:absolute;inset:0;width:100%;height:100%;pointer-events:none;";
      parent.appendChild(view);

      // Settled once the model is loaded and can be measured; nothing is framed before that.
      let framing = { scale: profile.scale ?? 0, anchorY: profile.anchorY ?? 0 };

      const frame = () => {
        const m = controllerRef.current?.modelRef as
          | { anchor: { set: (x: number, y: number) => void }; scale: { set: (v: number) => void }; x: number; y: number; internalModel?: { originalHeight?: number } }
          | undefined;
        if (!m) return;
        const nativeH = m.internalModel?.originalHeight;
        m.anchor.set(0.5, 0.5);
        if (nativeH) m.scale.set((app.renderer.height * framing.scale) / nativeH);
        m.x = app.renderer.width / 2;
        m.y = app.renderer.height * framing.anchorY;
      };

      Live2DModel.from(modelUrl).then(async (loaded: unknown) => {
        model = loaded as { destroy: () => void };
        if (destroyed) {
          model.destroy();
          model = null;
          return;
        }
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        app.stage.addChild(loaded as any);
        // A model nobody wrote a profile for is read here, once it is loaded and can be asked. The
        // parts have to be resolved first: the probe must not measure a hat this app then hides — and
        // resolving them needs a fetch, which is why the probe measures the rig's rest pose rather
        // than the live one. Against the live pose this `await` framed a whole body.
        const facts = readModelFacts(loaded);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const settingsJson = (loaded as any).internalModel?.settings?.json;
        facts.partNames = await readPartNames(modelUrl, settingsJson);
        if (destroyed) {
          model.destroy();
          model = null;
          return;
        }
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const core = (loaded as any).internalModel?.coreModel;
        const parts = completeConfig(profile, facts).hideParts ?? [];
        const config = completeConfig(profile, facts, probeFraming(core, parts));
        framing = { scale: config.scale, anchorY: config.anchorY };
        const controller = new Live2DController(loaded as never, config);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (loaded as any).autoInteract = false; // no mouse-follow; we drive the head
        controllerRef.current = controller;
        // Put her in the phase she is ALREADY in. The effect below only polls for a controller for a
        // few seconds and then gives up, so a model that takes longer than that to arrive was left
        // `asleep` for good: the lids close, nothing reopens them, and the bigger the model the more
        // certain it is.
        if (phaseRef.current === "ringing") controller.startle();
        else if (phaseRef.current === "live") {
          controller.settle();
          if (workingRef.current) controller.setFocus(true);
        }
        window._live2dController = controller; // console/e2e handle
        const ids = controller.partIds();
        onModelRef.current?.({
          parts: ids.map((id) => ({ id, name: facts.partNames?.[id] })),
          hidden: (config.hideParts ?? [])
            .map((p) => (typeof p === "number" ? ids[p] : p))
            .filter((id): id is string => !!id),
          setHidden: (next) => controller.setHiddenParts(next),
        });
        frame();
        // one more frame so the model is actually painted before the loader hides
        requestAnimationFrame(() => requestAnimationFrame(() => onReadyRef.current?.()));
      }).catch((err: unknown) => {
        if (destroyed) return;
        console.error("[kotoba] Live2D model failed to load:", err);
        onErrorRef.current?.();
      });

      let settle: ReturnType<typeof setTimeout> | undefined;
      // A panel opening animates this width, and every app.resize() reallocates the framebuffer —
      // dozens in a row is a visible flash. The reframe runs live; the framebuffer waits for the end.
      const onResize = () => {
        frame();
        clearTimeout(settle);
        settle = setTimeout(() => {
          if (destroyed) return;
          app.resize();
          frame();
        }, 120);
      };
      const observer = new ResizeObserver(onResize);
      observer.observe(parent);

      return () => {
        destroyed = true;
        clearTimeout(settle);
        observer.disconnect();
        controllerRef.current = null;
        window._live2dController = undefined;
        model?.destroy();
        model = null;
        app.destroy(true); // removeView:true → detaches + frees the WebGL context with the canvas
      };
    } catch (err) {
      console.error("[kotoba] Live2D canvas failed to initialise:", err);
      onErrorRef.current?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let raf = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const conv = conversationRef.current;
      controllerRef.current?.setLipSync(conv?.isSpeaking ? getMouthAmplitude(conv) : 0);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  // Emotion only applies while she's awake (in a live call) — asleep keeps the drowsy face.
  useEffect(() => {
    if (phase === "live") controllerRef.current?.setEmotion(emotion);
  }, [emotion, phase]);


  // Poll briefly: the controller may mount AFTER the phase change, and the transition would be dropped.
  useEffect(() => {
    let tries = 0;
    const iv = window.setInterval(() => {
      const c = controllerRef.current;
      if (!c) {
        if (++tries > 40) window.clearInterval(iv);
        return;
      }
      window.clearInterval(iv);
      if (phase === "ringing") {
        c.startle();
      } else if (phase === "live") {
        c.settle();
        if (workingRef.current) c.setFocus(true);
      } else {
        c.setAsleep();
      }
    }, 80);
    return () => window.clearInterval(iv);
  }, [phase]);

  useEffect(() => {
    if (phase === "live") controllerRef.current?.setSpeaking(speaking);
  }, [speaking, phase]);

  useEffect(() => {
    if (phase !== "live") return;
    let tries = 0;
    const iv = window.setInterval(() => {
      const c = controllerRef.current;
      if (!c) {
        if (++tries > 40) window.clearInterval(iv);
        return;
      }
      window.clearInterval(iv);
      c.setFocus(working);
    }, 80);
    return () => window.clearInterval(iv);
  }, [working, phase]);

  return (
    <div
      ref={containerRef}
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}
    />
  );
}
