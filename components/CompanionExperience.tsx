/**
 * Call shell — mic, transcript, panels, both voice transports — on ONE notice surface, so there is
 * never a second visual language for "something is wrong". Five invariants: exactly one
 * `startSession` per call, latched at app level, because a second against a LiveKit room mid-teardown
 * is the "createOffer with closed peer connection" family; the transport is latched per call and
 * re-read only BETWEEN calls, so a Settings toggle applies to the next call, not the next page load;
 * the dispatcher routes a frame by what it names, so a `need_input` clear dismisses only its own card;
 * the work resync REPAIRS and never sets, since clearing a flag the backend calls idle removes a lie
 * while a stale read adds one; and an attachment is stashed BEFORE the turn fires.
 */
"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { ConversationProvider, useConversation } from "@elevenlabs/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import CallLoader from "@/components/CallLoader";
import PopShapes from "@/components/PopShapes";
import RoomBackdrop from "@/components/RoomBackdrop";
import SubagentChibis from "@/components/SubagentChibis";
import TranscriptPanel, { type Line } from "@/components/TranscriptPanel";
import FilesPanel from "@/components/panels/FilesPanel";
import InputRequestPanel from "@/components/panels/InputRequestPanel";
import ReportPanel from "@/components/panels/ReportPanel";
import SettingsPanel from "@/components/panels/SettingsPanel";
import TaskTab from "@/components/panels/TaskTab";
import TerminalPanel from "@/components/panels/TerminalPanel";
import UIOverlay from "@/components/UIOverlay";
import { SpinnerIcon } from "@/components/icons";
import { apiFetch, authToken, ensureAuthToken, tokenUrl } from "@/lib/api";
import { useAvatar } from "@/lib/avatar";
import { badgeLabel, type CallPhase } from "@/lib/call-status";
import { useEmotionBridge, type TaskEvent } from "@/lib/emotion-bridge";
import { useLocalVoice } from "@/lib/local-voice";
import { pickShellNotice, type ShellNotice } from "@/lib/notices";
import { playBlip, playClick, playSwoosh, playWakeChime, startRingtone } from "@/lib/sounds";
import { scrub } from "@/lib/text-security";
import { applyAgentCorrection, cleanText, stripAgentFiller } from "@/lib/transcript";
import { ownsWorkBracket, useKotobaStore, type ApprovalNotice } from "@/lib/store";
import type { Emotion } from "@/lib/expressions";
import { isImageAttachment, isPdfAttachment, isTextAttachment } from "@/lib/attachments";
import { WorkKeepalive } from "@/lib/work-keepalive";

const Live2DCanvas = dynamic(() => import("@/components/Live2DCanvas"), { ssr: false });

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// Hidden trigger sentinels, injected as user messages so she voices the event herself — never shown in the transcript.
const WORK_DONE_TRIGGER = "__work_done__";
const REMINDER_TRIGGER = "__reminder__";

export type VoiceMode = "agent" | "local";

/** `crypto.randomUUID` exists only in a SECURE context, and `http://<lan-ip>:3000` is not one — which
 *  is one self-host away, since Dockerfile.web binds 0.0.0.0. Called bare from a render-phase
 *  initializer, an absent method takes the whole page down. The fallback builds the same v4 shape from
 *  `crypto.getRandomValues`, the one member usable from an insecure context: same entropy source, no
 *  downgrade to Math.random. */
function newSessionId(): string {
  if (typeof crypto?.randomUUID === "function") return crypto.randomUUID();
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function CompanionInner({ sessionId, agentId, voiceMode, onTransportSettled }: {
  sessionId: string;
  agentId?: string;
  voiceMode: VoiceMode;
  onTransportSettled: () => void;
}) {
  const [phase, setPhase] = useState<CallPhase>("offline");
  const phaseRef = useRef<CallPhase>("offline");
  phaseRef.current = phase;
  const [lines, setLines] = useState<Line[]>([]);
  const [modelReady, setModelReady] = useState(false);
  const [modelFailed, setModelFailed] = useState(false);
  const avatar = useAvatar(API_URL);
  const [modelNoticeGone, setModelNoticeGone] = useState(false);
  const [bridgeDown, setBridgeDown] = useState(false);
  const stopRingRef = useRef<(() => void) | null>(null);
  // The ringtone re-arms itself on a module-level AudioContext and this closure is its only stopper —
  // without this cleanup, navigating away mid-ring (the wordmark is a <Link>) leaves it playing forever.
  useEffect(() => () => stopRingRef.current?.(), []);
  const thumbUrlsRef = useRef<string[]>([]);
  useEffect(() => () => {
    for (const u of thumbUrlsRef.current) URL.revokeObjectURL(u);
    thumbUrlsRef.current = [];
  }, []);
  const ringStartRef = useRef(0);
  // Single-flight latch: a second startSession against a room mid-teardown throws "createOffer with
  // closed peer connection". Cleared only by the status effect — the SDK's status is the one truth.
  const callRequestedRef = useRef(false);
  const workKeepaliveRef = useRef<WorkKeepalive | null>(null);
  const lineSeq = useRef(0);
  const nextId = () => ++lineSeq.current;
  const [sending, setSending] = useState(false);

  const [callMode, setCallMode] = useState<VoiceMode | null>(null); // transport LATCHED per call — a mid-call Settings flip must not switch it
  const activeMode = callMode ?? voiceMode;
  const isLocalMode = activeMode === "local";
  const modeRef = useRef(activeMode);
  modeRef.current = activeMode;

  const appendLine = useCallback((from: "user" | "ai", text: string, image?: string, imageAlt?: string, file?: string) => {
    let clean = cleanText(text);
    if (from === "ai") clean = stripAgentFiller(clean, modeRef.current);
    if (!clean && !image && !file) return;
    setLines((prev) => {
      const last = prev[prev.length - 1];
      if (last && last.from === from && last.text === clean && !image && !file) return prev;
      // text goes through cleanText's gate; imageAlt and file skip it — both are ours to gate here.
      return [...prev, {
        id: nextId(), from, text: clean, image,
        imageAlt: imageAlt === undefined ? undefined : scrub(imageAlt),
        file: file === undefined ? undefined : scrub(file),
      }];
    });
  }, []);

  const onMessage = useCallback(({ message, source }: { message: string; source: string }) => {
    if (!message) return;
    if (message.startsWith("__") && message.endsWith("__")) return;
    appendLine(source === "user" ? "user" : "ai", message);
    playBlip(source === "user");
  }, [appendLine]);

  const onAgentResponseCorrection = useCallback(
    ({ corrected_agent_response }: { corrected_agent_response?: string }) => {
      const corrected = stripAgentFiller(cleanText(corrected_agent_response || ""), modeRef.current);
      if (!corrected) return;
      setLines((prev) => applyAgentCorrection(prev, corrected, () => ({ id: nextId(), from: "ai", text: corrected })));
    },
    [],
  );
  const conversation = useConversation({ onMessage, onAgentResponseCorrection });
  // useConversation returns a NEW object each render: read it through a ref, or an effect-deps chain
  // re-runs clearWork() every render (React error #185 loop).
  const conversationRef = useRef(conversation);
  conversationRef.current = conversation;

  const localVoice = useLocalVoice(sessionId, API_URL, { onMessage, onAgentResponseCorrection });
  const localVoiceRef = useRef(localVoice);
  localVoiceRef.current = localVoice;

  const sendUserText = useCallback((text: string) => {
    if (modeRef.current === "local") {
      localVoiceRef.current.sendText(text);
      return;
    }
    conversationRef.current.sendUserMessage(text);
  }, []);

  const emotion = useKotobaStore((s) => s.emotion);
  const setEmotion = useKotobaStore((s) => s.setEmotion);

  const working = useKotobaStore((s) => s.working);
  const logs = useKotobaStore((s) => s.logs);
  const files = useKotobaStore((s) => s.files);
  const openPanels = useKotobaStore((s) => s.openPanels);
  const reportTitle = useKotobaStore((s) => s.reportTitle);
  const inputRequests = useKotobaStore((s) => s.inputRequests);
  const setWorking = useKotobaStore((s) => s.setWorking);
  const latchWorkRun = useKotobaStore((s) => s.latchWorkRun);
  const pushStep = useKotobaStore((s) => s.pushStep);
  const pushFile = useKotobaStore((s) => s.pushFile);
  const bumpFilesEpoch = useKotobaStore((s) => s.bumpFilesEpoch);
  const setReport = useKotobaStore((s) => s.setReport);
  const pushInputRequest = useKotobaStore((s) => s.pushInputRequest);
  const dismissInputRequest = useKotobaStore((s) => s.dismissInputRequest);
  const spawnSubagent = useKotobaStore((s) => s.spawnSubagent);
  const addSubagentStep = useKotobaStore((s) => s.addSubagentStep);
  const finishSubagent = useKotobaStore((s) => s.finishSubagent);
  const clearSubagents = useKotobaStore((s) => s.clearSubagents);
  const setTasks = useKotobaStore((s) => s.setTasks);
  const toggleBig = useKotobaStore((s) => s.toggleBig);
  const openBig = useKotobaStore((s) => s.openBig);
  const toggleSmall = useKotobaStore((s) => s.toggleSmall);
  const openSmall = useKotobaStore((s) => s.openSmall);
  const clearWork = useKotobaStore((s) => s.clearWork);

  const chatOpen = openPanels.includes("chat");
  const terminalOpen = openPanels.includes("terminal");
  const filesOpen = openPanels.includes("files");
  const reportOpen = openPanels.includes("report");
  const settingsOpen = openPanels.includes("settings");

  // Every moment the NEXT call could start, which is what the Settings label promises — never mid-call,
  // where the transport is latched.
  useEffect(() => {
    if (phase === "offline" && !settingsOpen) onTransportSettled();
  }, [phase, settingsOpen, onTransportSettled]);

  const handleEmotion = useCallback((e: Emotion) => setEmotion(e), [setEmotion]);

  const stopWorkKeepalive = useCallback(() => {
    workKeepaliveRef.current?.stop();
  }, []);
  /** The LATCHED mode, and read at start: in local mode there is no call to hold, so no timer is created. */
  const startWorkKeepalive = useCallback(() => {
    if (!workKeepaliveRef.current) {
      workKeepaliveRef.current = new WorkKeepalive(() => conversationRef.current.sendUserActivity());
    }
    workKeepaliveRef.current.start(modeRef.current);
  }, []);
  useEffect(() => stopWorkKeepalive, [stopWorkKeepalive]);

  const announceWork = useCallback(
    (ok: boolean, summary: string) => {
      try {
        if (modeRef.current === "local") {
          localVoiceRef.current.sendText(WORK_DONE_TRIGGER);
          return;
        }
        const msg = ok ? `(Background work finished: ${summary})` : "(Background work failed.)";
        conversationRef.current.sendContextualUpdate(msg);
        conversationRef.current.sendUserMessage(WORK_DONE_TRIGGER);
      } catch {
      }
    },
    [],
  );
  const announcedOnConnectRef = useRef(false);
  const workEventAtRef = useRef(0);

  const handleTask = useCallback(
    (ev: TaskEvent) => {
      // The newest work_started owns the work surface; a bracket frame naming another run is a
      // superseded job's late teardown or a live turn's chip.
      if (ev.kind === "work_started") {
        workEventAtRef.current = Date.now();
        latchWorkRun(ev.run_id);
        setWorking(true);
        clearSubagents();
        startWorkKeepalive();
      } else if (ev.kind === "working") {
        if (!ownsWorkBracket(useKotobaStore.getState().workRunId, ev.run_id)) return;
        workEventAtRef.current = Date.now();
        setWorking(ev.on);
        if (ev.on) openSmall("terminal");
      } else if (ev.kind === "step") {
        pushStep(ev);
      } else if (ev.kind === "artifact") {
        pushFile(ev.path, ev.action);
      } else if (ev.kind === "files_changed") {
        bumpFilesEpoch();
      } else if (ev.kind === "recalled_image") {
        if (ev.id) {
          appendLine("ai", "", tokenUrl(`${API_URL}/api/visual-memory/${encodeURIComponent(ev.id)}`), ev.about);
          openSmall("chat");
        }
      } else if (ev.kind === "reminder") {
        try {
          sendUserText(REMINDER_TRIGGER);
        } catch {
        }
        playBlip(false);
      } else if (ev.kind === "report_ready") {
        setReport(ev.title);
        openBig("report");
        playSwoosh(true);
      } else if (ev.kind === "need_input") {
        if (ev.mode === "clear") {
          dismissInputRequest(ev.request_id);
        } else {
          pushInputRequest({
            id: ev.request_id ?? `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            requestId: ev.request_id,
            mode: ev.mode,
            label: ev.label ?? "",
            detail: ev.detail,
            name: ev.name,
            inputKind: ev.input_kind,
            url: ev.url,
            wait: ev.wait,
            family: ev.family,
            canAlways: ev.can_always,
            canAlwaysExact: ev.can_always_exact,
            alwaysNote: ev.always_note,
            notice: (ev as { notice?: ApprovalNotice }).notice,
          });
        }
      } else if (ev.kind === "subagent_spawned") {
        spawnSubagent(ev.id, ev.goal);
      } else if (ev.kind === "subagent_step") {
        addSubagentStep(ev.id, ev.text);
      } else if (ev.kind === "subagent_done") {
        finishSubagent(ev.id, ev.summary, ev.ok !== false);
      } else if (ev.kind === "work_done") {
        if (!ownsWorkBracket(useKotobaStore.getState().workRunId, ev.run_id)) return;
        workEventAtRef.current = Date.now();
        latchWorkRun("");
        stopWorkKeepalive(); // FIRST: sendUserActivity suppresses agent speech ~2s, and now she must speak
        setWorking(false);
        clearSubagents();
        if (!ev.cancelled) announceWork(ev.ok, ev.ok ? ev.summary : "");
      } else if (ev.kind === "task_list") {
        setTasks({ list_id: ev.list_id, title: ev.title, tasks: ev.tasks, status: ev.status, rev: ev.rev });
      }
    },
    [setWorking, latchWorkRun, openSmall, openBig, pushStep, pushFile, bumpFilesEpoch, setReport, pushInputRequest, dismissInputRequest, spawnSubagent, addSubagentStep, finishSubagent, clearSubagents, appendLine, announceWork, startWorkKeepalive, stopWorkKeepalive, sendUserText, setTasks],
  );
  const [authTok, setAuthTok] = useState<string>(() => authToken());
  useEffect(() => {
    ensureAuthToken().then((t) => t && setAuthTok(t));
  }, []);
  const resyncMute = useCallback(() => {
    if (!sessionId) return;
    if (modeRef.current === "local") return;
    apiFetch(`${API_URL}/api/session/${sessionId}/mute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ muted: conversationRef.current?.isMuted ?? false }),
    }).catch(() => {});
  }, [sessionId]);

  const rehydrateTasks = useCallback(() => {
    if (!sessionId) return;
    apiFetch(`${API_URL}/api/session/${sessionId}/tasks`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d?.list) setTasks(d.list, true); })
      .catch(() => {});
  }, [sessionId, setTasks]);

  /** Repair only: it may CLEAR `working`, never set it — a REST read can be older than a live frame.
   *  The run latch is RELEASED either way: a gap can outlive the run it names (the job ended inside it,
   *  its successor's `work_started` lost with it), and kept, it would eat the successor's own close as
   *  well as the next unmarked work_done (a deferred command's announce) — lib/store.ts ownsWorkBracket. */
  const resyncWork = useCallback(() => {
    if (!sessionId) return;
    const askedAt = Date.now();
    apiFetch(`${API_URL}/api/session/${sessionId}/work`)
      .then((r) => (r.ok ? r.json() : null))
      .then((w) => {
        if (!w || workEventAtRef.current >= askedAt) return;
        if (w.status !== "running") setWorking(false);
        latchWorkRun("");
      })
      .catch(() => {});
  }, [sessionId, setWorking, latchWorkRun]);

  const resyncState = useCallback(() => {
    resyncMute();
    rehydrateTasks();
    resyncWork();
  }, [resyncMute, rehydrateTasks, resyncWork]);

  useEmotionBridge(sessionId, API_URL, handleEmotion, handleTask, authTok, resyncState, setBridgeDown);

  const isMuted = isLocalMode ? localVoice.isMuted : conversation.isMuted;

  // The EL SDK's internal WebRTC rejections would otherwise surface as app crashes — swallow just those.
  useEffect(() => {
    const onRej = (e: PromiseRejectionEvent) => {
      const msg = String(e.reason?.message || e.reason || "");
      if (
        msg.includes("error_type") ||
        msg.includes("peer connection") ||
        msg.includes("createOffer") ||
        msg.includes("DataChannel")
      )
        e.preventDefault();
    };
    window.addEventListener("unhandledrejection", onRej);
    return () => window.removeEventListener("unhandledrejection", onRej);
  }, []);

  const status = isLocalMode ? localVoice.status : conversation.status;
  const isSpeaking = isLocalMode ? localVoice.isSpeaking : conversation.isSpeaking;
  const voiceLost = isLocalMode ? localVoice.voiceLost : null;
  const voiceDown = voiceLost !== null || (isLocalMode && localVoice.micBlocked);

  useEffect(() => {
    if (status === "connecting") {
      setPhase("ringing");
    } else if (status === "connected") {
      const elapsed = Date.now() - ringStartRef.current;
      const hold = Math.max(0, 1800 - elapsed);
      const t = window.setTimeout(() => {
        stopRingRef.current?.();
        setPhase("live");
        if (!announcedOnConnectRef.current && sessionId) {
          announcedOnConnectRef.current = true;
          apiFetch(`${API_URL}/api/session/${sessionId}/work`)
            .then((r) => (r.ok ? r.json() : null))
            .then((w) => {
              if (w && w.pending) announceWork(!!w.ok, w.summary || "");
            })
            .catch(() => {});
        }
      }, hold);
      return () => window.clearTimeout(t);
    } else {
      callRequestedRef.current = false;
      announcedOnConnectRef.current = false;
      setCallMode(null);
      stopRingRef.current?.();
      setPhase("offline");
      clearWork();
    }
  }, [status, clearWork, sessionId, announceWork]);

  const startCall = useCallback(() => {
    if (callRequestedRef.current) return;
    callRequestedRef.current = true;
    setCallMode(voiceMode);
    playClick();
    ringStartRef.current = Date.now();
    stopRingRef.current = startRingtone();
    playWakeChime();
    if (voiceMode === "local") {
      localVoice.start();
      return;
    }
    try {
      conversation.startSession({
        agentId,
        customLlmExtraBody: { session_id: sessionId },
      } as unknown as Parameters<typeof conversation.startSession>[0]);
    } catch {
      stopRingRef.current?.();
      callRequestedRef.current = false;
    }
  }, [conversation, localVoice, voiceMode, sessionId, agentId]);

  const endCall = useCallback(() => {
    if (!callRequestedRef.current) return;
    callRequestedRef.current = false;
    playClick();
    stopRingRef.current?.();
    stopWorkKeepalive();
    if (sessionId) {
      apiFetch(`${API_URL}/api/session/${sessionId}/leave`, { method: "POST" }).catch(() => {});
    }
    if (modeRef.current === "local") {
      localVoice.end();
      return;
    }
    try {
      conversation.endSession();
    } catch {
    }
  }, [conversation, localVoice, sessionId, stopWorkKeepalive]);

  const toggleMute = useCallback(() => {
    playClick();
    if (modeRef.current === "local") {
      localVoice.setMuted(!localVoice.isMuted);
      return;
    }
    const next = !conversation.isMuted;
    try {
      conversation.setMuted(next);
    } catch {
    }
    if (sessionId) {
      apiFetch(`${API_URL}/api/session/${sessionId}/mute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ muted: next }),
      }).catch(() => {});
    }
  }, [conversation, localVoice, sessionId]);

  const toggleChat = useCallback(() => {
    playSwoosh(!chatOpen);
    toggleSmall("chat");
  }, [chatOpen, toggleSmall]);

  const markTextTurn = useCallback(async () => { // awaited BEFORE sending: the flag has to be set when the turn fires
    if (!sessionId || modeRef.current === "local") return;
    try {
      await apiFetch(`${API_URL}/api/session/${sessionId}/text-turn`, { method: "POST" });
    } catch {
    }
  }, [sessionId]);

  const handleSend = useCallback(async (text: string) => {
    const t = text.trim();
    if (!t) return;
    await markTextTurn();
    try {
      sendUserText(t);
      appendLine("user", t);
      playBlip(true);
    } catch {
    }
  }, [sendUserText, appendLine, markTextTurn]);

  /** Stash the file, THEN fire the turn: the other order loses the attachment. */
  const handleSendFile = useCallback(async (file: File, caption: string) => {
    const isImage = isImageAttachment(file);
    const isPdf = isPdfAttachment(file);
    const isText = isTextAttachment(file);

    if (isText) {
      await markTextTurn();
      try {
        let content = await file.text();
        const MAX = 20000;
        if (content.length > MAX) content = content.slice(0, MAX) + "\n…(truncated)";
        const msg = (caption ? caption + "\n\n" : "") + `File "${file.name}":\n${content}`;
        appendLine("user", caption, undefined, undefined, file.name);
        playBlip(true);
        sendUserText(msg);
      } catch {
        appendLine("ai", "Mmm, I couldn't read that file — could you try again?");
      }
      return;
    }

    if (isPdf) {
      try {
        const latin1 = new TextDecoder("latin1").decode(await file.arrayBuffer());
        const pages = (latin1.match(/\/Type\s*\/Page[^s]/g) || []).length;
        if (pages > 5) {
          appendLine("ai", `That PDF looks like about ${pages} pages — I can only take five for now. Could you send a shorter piece?`);
          return;
        }
      } catch {
      }
    }

    const thumb = isImage ? URL.createObjectURL(file) : undefined;
    let drawn = false;
    setSending(true);
    try {
      const dataUrl: string = await new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result));
        r.onerror = () => reject(new Error("read failed"));
        r.readAsDataURL(file);
      });
      const res = await apiFetch(`${API_URL}/api/session/${sessionId}/attachment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: isImage ? "image" : "pdf", data_url: dataUrl, name: file.name }),
      });
      if (!res.ok) {
        // The cap answers 409 with the sentence that fits it. Carry the door's own words out to the
        // catch: "try again" is advice that cannot work when the answer is "send this one first".
        const said = await res.json().then((d) => String(d?.detail || "")).catch(() => "");
        throw new Error(said || `${res.status}`);
      }
      appendLine("user", caption, thumb, undefined, isImage || caption ? undefined : file.name);
      if (thumb) {
        thumbUrlsRef.current.push(thumb);
        drawn = true;
      }
      playBlip(true);
      await markTextTurn();
      sendUserText(caption || (isImage ? "__image_only__" : "__file_only__"));
    } catch (e) {
      if (thumb && !drawn) URL.revokeObjectURL(thumb);
      const why = e instanceof Error ? e.message : String(e);
      console.error("[kotoba] attachment failed:", why);
      appendLine("ai", why.length > 3 ? why : "Mmm, I couldn't attach that one — could you try again?");
    } finally {
      setSending(false);
    }
  }, [sendUserText, appendLine, markTextTurn, sessionId]);

  const toggleTerminal = useCallback(() => {
    playSwoosh(!terminalOpen);
    toggleSmall("terminal");
  }, [terminalOpen, toggleSmall]);

  const toggleFiles = useCallback(() => {
    playSwoosh(!filesOpen);
    toggleSmall("files");
  }, [filesOpen, toggleSmall]);

  const toggleReport = useCallback(() => {
    playSwoosh(!reportOpen);
    toggleBig("report");
  }, [reportOpen, toggleBig]);

  const toggleSettings = useCallback(() => {
    playSwoosh(!settingsOpen);
    toggleBig("settings");
  }, [settingsOpen, toggleBig]);

  const notice: ShellNotice | null = pickShellNotice({
    bridgeDown,
    voiceNotice: localVoice.errorNotice,
    dismissVoice: localVoice.dismissError,
    isLocalMode,
    agentStatus: conversation.status,
    agentMessage: conversation.message,
    modelFailed: modelFailed && !modelNoticeGone,
    modelMissing: avatar.status === "missing" && !modelNoticeGone,
    modelsDir: avatar.modelsDir,
    dismissModel: () => setModelNoticeGone(true),
  });

  const handleInputResult = useCallback(
    (r: { id: string; isApproval: boolean; cancelled?: boolean; value?: string; posted?: boolean }) => {
      dismissInputRequest(r.id);
      if (r.isApproval || r.cancelled) return;
      if (r.posted) return; // already delivered via POST /input (possibly a secret) — NEVER echo it into the conversation
      if (r.value && r.value.trim()) {
        sendUserText(r.value.trim());
      }
    },
    [dismissInputRequest, sendUserText],
  );

  return (
    <main
      style={{
        position: "fixed",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        background: "radial-gradient(120% 100% at 50% 0%, var(--cream) 0%, var(--cream-2) 100%)",
        overflow: "hidden",
      }}
    >
      <PopShapes />
      {/* A model nobody installed is never going to be ready: the loader is fullscreen and eats every
          click, so it has to come down on absence exactly as it does on a failure. */}
      <CallLoader done={modelReady} failed={modelFailed || avatar.status === "missing"} />

      <header
        style={{
          position: "fixed",
          top: 22,
          left: 0,
          right: 0,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "0 clamp(16px, 4vw, 48px)",
          zIndex: 6,
        }}
      >
        <Link href="/" title="Back to home" style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "1.5rem", color: "var(--coral)" }}>言</span>
          <span style={{ fontFamily: "var(--font-display)", fontWeight: 600, fontSize: "1.1rem" }}>· Kotoba</span>
        </Link>
        <LiveBadge phase={phase} voiceDown={voiceDown} />
      </header>

      <div
        style={{
          position: "relative",
          zIndex: 2,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          maxWidth: "96vw",
        }}
      >
        <div
          style={{
            width: terminalOpen || filesOpen ? "min(30vw, 340px)" : 0,
            marginRight: terminalOpen || filesOpen ? "1.1rem" : 0,
            height: "min(80vh, 720px)",
            display: "flex",
            flexDirection: "column",
            gap: terminalOpen && filesOpen ? "0.9rem" : 0,
            flexShrink: 0,
            overflow: "hidden",
            transition: "width 460ms cubic-bezier(.22,1,.36,1), margin 460ms cubic-bezier(.22,1,.36,1)",
          }}
        >
          <TerminalPanel open={terminalOpen} onClose={toggleTerminal} />
          <FilesPanel open={filesOpen} onClose={toggleFiles} apiUrl={API_URL} />
        </div>

        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "1.5rem" }}>
          <div
            style={{
              position: "relative",
              width: (() => {
                const wide = reportOpen || settingsOpen;
                const open = (terminalOpen || filesOpen ? 1 : 0) + (chatOpen ? 1 : 0) + (wide ? 1 : 0);
                if (wide) return open >= 2 ? "min(28vw, 320px)" : "min(36vw, 410px)";
                if (open >= 2) return "min(46vw, 520px)";
                if (open === 1) return "min(58vw, 640px)";
                return "min(90vw, 880px)";
              })(),
              aspectRatio: "16 / 10",
              transition: "width 460ms cubic-bezier(0.22,1,0.36,1)",
              borderRadius: 28,
              border: "4px solid var(--ink)",
              boxShadow: "var(--shadow-pop)",
              background: "#0c0a14",
              overflow: "hidden",
              animation: phase === "ringing" ? "shake 0.4s ease-in-out infinite" : "rise 0.6s cubic-bezier(.2,.8,.2,1) both",
            }}
          >
            <RoomBackdrop />
            {avatar.choice && (
              <Live2DCanvas
                key={avatar.choice.url}
                config={avatar.choice.config}
                modelUrl={avatar.choice.url}
                emotion={emotion}
                conversation={isLocalMode ? localVoice : conversation}
                phase={phase}
                speaking={isSpeaking}
                working={working}
                onReady={() => setModelReady(true)}
                onError={() => setModelFailed(true)}
              />
            )}

            {working && (
              <div
                style={{
                  position: "absolute",
                  top: 14,
                  left: 14,
                  display: "flex",
                  alignItems: "center",
                  gap: 7,
                  background: "var(--grape)",
                  border: "2.5px solid var(--ink)",
                  borderRadius: 999,
                  padding: "0.28rem 0.7rem",
                  color: "#fff",
                  fontFamily: "var(--font-display)",
                  fontWeight: 600,
                  fontSize: "0.78rem",
                  boxShadow: "var(--shadow-pop-sm)",
                  zIndex: 5,
                  animation: "rise 0.3s ease both",
                }}
              >
                <SpinnerIcon width={13} height={13} />
                working…
              </div>
            )}

            <div
              style={{
                position: "absolute",
                left: 14,
                bottom: 14,
                display: "flex",
                alignItems: "center",
                gap: 8,
                background: "rgba(20,14,28,0.62)",
                backdropFilter: "blur(6px)",
                border: "2px solid rgba(255,255,255,0.25)",
                borderRadius: 12,
                padding: "0.3rem 0.7rem",
                color: "#fff",
                fontFamily: "var(--font-display)",
                fontWeight: 600,
                fontSize: "0.85rem",
                zIndex: 4,
              }}
            >
              <SpeakingDot speaking={isSpeaking} />
              Kotoba
            </div>

            {notice && (
              <div
                role="alert"
                style={{
                  position: "absolute",
                  top: 14,
                  right: 14,
                  maxWidth: "min(74%, 420px)",
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 8,
                  background: notice.fatal ? "var(--live)" : "var(--sun)",
                  border: "2.5px solid var(--ink)",
                  borderRadius: 14,
                  padding: "0.45rem 0.4rem 0.45rem 0.7rem",
                  color: notice.fatal ? "#fff" : "var(--ink)",
                  fontFamily: "var(--font-display)",
                  fontWeight: 600,
                  fontSize: "0.78rem",
                  lineHeight: 1.35,
                  boxShadow: "var(--shadow-pop-sm)",
                  zIndex: 6,
                  animation: "rise 0.3s ease both",
                }}
              >
                <span style={{ flex: 1 }}>{notice.text}</span>
                {notice.onDismiss && (
                  <button
                    onClick={notice.onDismiss}
                    aria-label="Dismiss notice"
                    style={{
                      background: "transparent",
                      border: "none",
                      color: "inherit",
                      cursor: "pointer",
                      fontSize: "0.95rem",
                      fontWeight: 700,
                      lineHeight: 1,
                      padding: "0 0.25rem",
                    }}
                  >
                    ×
                  </button>
                )}
              </div>
            )}

            {phase === "ringing" && (
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  display: "grid",
                  placeItems: "center",
                  background: "rgba(12,10,20,0.35)",
                  zIndex: 5,
                }}
              >
                <span
                  style={{
                    fontFamily: "var(--font-display)",
                    fontWeight: 700,
                    fontSize: "1.4rem",
                    color: "#fff",
                    background: "var(--live)",
                    border: "3px solid var(--ink)",
                    borderRadius: 999,
                    padding: "0.4rem 1.2rem",
                    boxShadow: "var(--shadow-pop-sm)",
                    animation: "pulse-live 1s ease-in-out infinite",
                  }}
                >
                  calling…
                </span>
              </div>
            )}

            <InputRequestPanel
              request={inputRequests[inputRequests.length - 1] ?? null}
              sessionId={sessionId}
              apiUrl={API_URL}
              onResult={handleInputResult}
            />
          </div>

          <UIOverlay
            phase={phase}
            working={working}
            isMuted={isMuted}
            micBlocked={isLocalMode ? localVoice.micBlocked : false}
            voiceLost={voiceLost}
            onStartCall={startCall}
            onToggleMute={toggleMute}
            onLeave={endCall}
            onToggleChat={toggleChat}
            chatOpen={chatOpen}
            onToggleTerminal={toggleTerminal}
            terminalOpen={terminalOpen}
            hasLogs={logs.length > 0}
            onToggleFiles={toggleFiles}
            filesOpen={filesOpen}
            hasFiles={files.some((f) => !f.seen)}
            onToggleReport={toggleReport}
            reportOpen={reportOpen}
            hasReport={!!reportTitle}
            onToggleSettings={toggleSettings}
            settingsOpen={settingsOpen}
          />
        </div>

        <ReportPanel
          open={reportOpen}
          sessionId={sessionId}
          apiUrl={API_URL}
          title={reportTitle}
          onClose={toggleReport}
        />

        <SettingsPanel open={settingsOpen} apiUrl={API_URL} onClose={toggleSettings} />

        <TranscriptPanel
          open={chatOpen}
          lines={lines}
          onToggle={toggleChat}
          onSend={handleSend}
          onSendFile={handleSendFile}
          callActive={phase === "live"}
          busy={sending}
        />
      </div>

      <SubagentChibis />
      <TaskTab working={working} />
    </main>
  );
}

function LiveBadge({ phase, voiceDown }: { phase: CallPhase; voiceDown: boolean }) {
  const label = badgeLabel(phase, voiceDown);
  const bg = label === "LIVE" ? "var(--live)" : label === "OFFLINE" ? "var(--ink)" : "var(--sun)";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 7,
        background: bg,
        color: bg === "var(--sun)" ? "var(--ink)" : "#fff",
        border: "3px solid var(--ink)",
        borderRadius: 999,
        padding: "0.3rem 0.85rem",
        fontFamily: "var(--font-display)",
        fontWeight: 600,
        fontSize: "0.8rem",
        letterSpacing: "0.06em",
        boxShadow: "var(--shadow-pop-sm)",
      }}
    >
      <span style={{ width: 9, height: 9, borderRadius: "50%", background: "#fff", animation: phase !== "offline" ? "pulse-live 1.1s ease-in-out infinite" : "none" }} />
      {label}
    </div>
  );
}

function SpeakingDot({ speaking }: { speaking: boolean }) {
  return <span style={{ width: 8, height: 8, borderRadius: "50%", background: speaking ? "var(--mint)" : "rgba(255,255,255,0.5)", animation: speaking ? "pulse-live 0.7s ease-in-out infinite" : "none" }} />;
}

export default function CompanionExperience() {
  const [sessionId] = useState(newSessionId);

  const [agentId, setAgentId] = useState<string | undefined>(process.env.NEXT_PUBLIC_ELEVENLABS_AGENT_ID);
  const [voiceMode, setVoiceMode] = useState<VoiceMode>(
    process.env.NEXT_PUBLIC_KOTOBA_VOICE_MODE === "agent" ? "agent" : "local",
  );
  /** Token first: on a fresh tab a bare fetch 401s and the panel's agent id is lost to the env fallback. */
  const refreshTransport = useCallback(async () => {
    try {
      await ensureAuthToken();
      const r = await apiFetch(`${API_URL}/api/settings`);
      const d = r.ok ? await r.json() : null;
      const id = d?.runtime?.elevenlabs_agent_id;
      if (id) setAgentId(id);
      const vm = d?.runtime?.voice_mode;
      if (vm === "local" || vm === "agent") setVoiceMode(vm);
    } catch {
      // the env fallback stands; the call reports its own trouble
    }
  }, []);

  const providerProps = useMemo(
    () =>
      ({
        agentId,
        customLlmExtraBody: { session_id: sessionId },
      }) as unknown as React.ComponentProps<typeof ConversationProvider>,
    [sessionId, agentId],
  );

  return (
    <ConversationProvider {...providerProps}>
      <CompanionInner
        sessionId={sessionId}
        agentId={agentId}
        voiceMode={voiceMode}
        onTransportSettled={refreshTransport}
      />
    </ConversationProvider>
  );
}
