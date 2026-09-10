/**
 * Channel-B bridge: SSE events → emotion + work-mode frames for the Live2D canvas and the panels.
 *
 * Read with fetch() rather than EventSource, and its liveness measured in bytes rather than in events —
 * both decisions belong to the stream layer underneath, and are made there.
 */

"use client";

import { useEffect, useRef } from "react";

import { isEmotion, type Emotion } from "@/lib/expressions";
import { SseParser, sseIsStale, type SseFrame } from "@/lib/sse-stream";

export type TaskEvent =
  /** `run_id` is the frame's ORIGIN (core/events.py) — the run that emitted it. Every frame on this
   *  channel may carry it; it is typed only on the bracket kinds, the ones routed by it
   *  (lib/store.ts `ownsWorkBracket`). */
  | { kind: "working"; on: boolean; run_id?: string }
  /** `outcome` is how it ENDED: `ok` alone cannot tell a failed command from a refusal. */
  | { kind: "step"; text?: string; phase?: "start" | "done"; id?: string; step_kind?: string; action?: string; result?: string; full?: string; ok?: boolean; outcome?: string; interrupted?: boolean; pending?: boolean }
  | { kind: "artifact"; path: string; action: "created" | "edited" }
  | { kind: "reminder"; message: string; id?: string }
  | { kind: "report_ready"; title: string }
  /** request_id identifies ONE card: a `clear` carrying it dismisses only that card, and a bare
   *  clear still means all — a timeout used to wipe the screen. */
  | { kind: "need_input"; mode: "input" | "approval" | "clear" | "open_link"; label?: string; detail?: string; name?: string; input_kind?: "text" | "key" | "link" | "secret"; url?: string; wait?: boolean; request_id?: string; family?: string; can_always?: boolean; can_always_exact?: boolean; always_note?: string }
  | { kind: "subagent_spawned"; id: string; goal: string }
  | { kind: "subagent_step"; id: string; text: string }
  | { kind: "subagent_done"; id: string; summary: string; ok?: boolean }
  | { kind: "files_changed" }  // the on-disk library changed (shell move/rename/delete) → panel re-reads
  /** Only the id rides this channel: an approval card must never queue behind 250 KB of base64. */
  | { kind: "recalled_image"; id: string; about?: string }
  | { kind: "work_started"; run_id?: string }    // a background work began → keep the call alive until work_done
  /** `cancelled` closes the work_started bracket WITHOUT announcing: the user asked for the stop. */
  | { kind: "work_done"; ok: boolean; summary: string; cancelled?: boolean; run_id?: string }
  | { kind: "task_list"; list_id: string; title: string; tasks: Array<{ id: string; text: string; status: "pending" | "active" | "done" | "dropped"; order: number }>; status: "open" | "done" | "abandoned"; rev: number };

/** Read-only probe for an end-to-end voice check, with no `close()` on purpose: abort() lands in
 *  connect()'s catch and RE-connects. The effect cleanup is the one thing that truly closes the
 *  stream. */
export type KotobaEventsHandle = { url: string; readyState: number };

export function useEmotionBridge(
  sessionId: string | null,
  apiUrl: string,
  onEmotion: (emotion: Emotion) => void,
  onTask?: (event: TaskEvent) => void,
  token?: string,
  onReconnect?: () => void,  // fired when the SSE RE-connects (backend came back) → caller re-syncs state (mute)
  onStreamDown?: (down: boolean) => void,
): void {
  const handlerRef = useRef(onEmotion);
  handlerRef.current = onEmotion;
  const taskRef = useRef(onTask);
  taskRef.current = onTask;
  const reconnectRef = useRef(onReconnect);
  reconnectRef.current = onReconnect;
  const downRef = useRef(onStreamDown);
  downRef.current = onStreamDown;

  useEffect(() => {
    if (!sessionId) return;

    const base = `${apiUrl}/api/events/${sessionId}`;
    const url = token ? `${base}?token=${encodeURIComponent(token)}` : base;

    let closed = false;
    let ctrl: AbortController | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let everConnected = false;
    let inFlight = false;
    let lastAliveAt = Date.now();

    const handle: KotobaEventsHandle = { url, readyState: 0 };
    window._kotobaEvents = handle;

    /** Never dedupe this against a flag scoped to the effect: the shell state it drives outlives the
     *  effect, so a restart swallows the recovery and leaves the banner up over a healthy stream. */
    const setDown = (d: boolean) => downRef.current?.(d);

    const dispatch = (frame: SseFrame) => {
      try {
        if (frame.event === "emotion") {
          const data = JSON.parse(frame.data);
          if (isEmotion(data.emotion)) handlerRef.current(data.emotion);
        } else if (frame.event === "task") {
          taskRef.current?.(JSON.parse(frame.data) as TaskEvent);
        }
      } catch {
        /* ignore malformed frames */
      }
    };

    const connect = async () => {
      if (closed || inFlight) return;
      inFlight = true;
      const c = new AbortController();
      ctrl = c;
      handle.readyState = 0;
      lastAliveAt = Date.now();
      try {
        const res = await fetch(url, { signal: c.signal, cache: "no-store" });
        if (!res.ok || !res.body) throw new Error(`events channel: HTTP ${res.status}`);
        handle.readyState = 1;
        lastAliveAt = Date.now();
        setDown(false);
        if (everConnected) reconnectRef.current?.();
        everConnected = true;
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        const parser = new SseParser();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          lastAliveAt = Date.now();
          for (const frame of parser.feed(decoder.decode(value, { stream: true }))) dispatch(frame);
        }
        throw new Error("events channel: stream ended");
      } catch {
        if (closed) return;
        inFlight = false;
        handle.readyState = 0;
        setDown(true);
        clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(() => void connect(), 2000);
      }
    };

    void connect();

    const watchdog = setInterval(() => {
      if (!closed && inFlight && sseIsStale(lastAliveAt, Date.now())) ctrl?.abort();
    }, 5000);

    const onVisible = () => {
      if (closed || document.visibilityState !== "visible") return;
      if (inFlight) {
        if (sseIsStale(lastAliveAt, Date.now())) ctrl?.abort();
      } else {
        clearTimeout(reconnectTimer);
        void connect();
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      closed = true;
      clearTimeout(reconnectTimer);
      clearInterval(watchdog);
      document.removeEventListener("visibilitychange", onVisible);
      ctrl?.abort();
      handle.readyState = 2;
      if (window._kotobaEvents === handle) window._kotobaEvents = undefined;
    };
  }, [sessionId, apiUrl, token]);
}
