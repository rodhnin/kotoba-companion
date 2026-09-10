// `PIXI` is how pixi-live2d-display finds the renderer. The rest are handles for driving the
// avatar and the voice from the browser console or an end-to-end harness.
import type { KotobaEventsHandle } from "@/lib/emotion-bridge";
import type { Live2DController } from "@/lib/live2d-controller";

declare global {
  interface Window {
    PIXI?: unknown; // assigned inside an effect, never at module scope: SSR has no `window`
    _live2dController?: Live2DController; // exposed once the model has loaded
    _kotobaEvents?: KotobaEventsHandle; // exposed by useEmotionBridge; readyState mirrors EventSource
    _kotobaVoice?: unknown; // live LocalVoiceClient while a local-mode call runs
  }
}

export {};
