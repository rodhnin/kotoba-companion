/**
 * Zustand store — her current emotion and the work-mode UI state.
 *
 * Five rules govern it, each stated at the line that keeps it: third-party text is scrubbed at the
 * door and two values are deliberately kept raw, a row's ending is told rather than inferred, a step
 * cannot un-finish, the face does not outlive its call, and a REST rehydrate may not roll back a newer
 * SSE frame.
 */
"use client";

import { create } from "zustand";

import type { Emotion } from "@/lib/expressions";
// Relative + extension so plain node can load this module directly.
import { scrub, scrubDeep } from "./text-security.ts";


/**
 * THE RULE, one reading of "which run owns the work surface" shared by both clients: latch the
 * `run_id` of the NEWEST `work_started`; a bracket frame (`working`, `work_done`) may touch the
 * surface only when it names that run, or the latch is empty. A mismatched frame is a
 * superseded job's late teardown — unrouted it stopped the keepalive and wiped the chip and chibis of
 * the NEW job, and the resync, repair-only by design, never put them back. Absence may not simply be
 * dropped: a producer too old to stamp `run_id` never stamped its `work_started` either, so its latch
 * is empty and everything still routes. An events-channel gap can outlive the run the latch names, so
 * the reconnect resync releases it whatever the backend answers.
 */
export function ownsWorkBracket(latched: string, frameRunId?: string): boolean {
  return !latched || (frameRunId ?? "") === latched;
}

export type StepOutcome = "ok" | "failed" | "refused" | "interrupted" | "pending";
export type StepStatus = StepOutcome | "running" | "unknown";

export type LogLine = {
  id: string;
  stepKind?: string;
  action?: string;
  result?: string;
  full?: string;
  ok?: boolean;
  outcome?: string;
  interrupted?: boolean;
  running?: boolean;
  pending?: boolean;
  text?: string;
};
export type StepEvent = {
  phase?: "start" | "done";
  id?: string;
  step_kind?: string;
  action?: string;
  result?: string;
  full?: string;
  ok?: boolean;
  outcome?: string;
  interrupted?: boolean;
  pending?: boolean;
  text?: string;
};

const OUTCOMES: readonly string[] = ["ok", "failed", "refused", "interrupted", "pending"];

/** A row's ending is TOLD, never inferred. Inference gave four endings the same mark: a non-zero exit,
 *  a command killed at its timeout and an action the user refused all came back as non-empty text, so
 *  `ok` was true and each drew a green ✓. Anything unrecognised — an older backend, an outcome invented
 *  later — is `unknown` and never `ok`, because a mark nobody chose must look like one. */
export function stepStatus(log: LogLine): StepStatus {
  if (log.running) return "running";
  if (log.outcome && OUTCOMES.includes(log.outcome)) return log.outcome as StepOutcome;
  if (log.outcome) return "unknown";
  if (log.pending) return "pending";
  if (log.interrupted) return "interrupted";
  if (log.ok === true) return "ok";
  if (log.ok === false) return "failed";
  return "unknown";
}
export type FileArtifact = {
  path: string;
  action: "created" | "edited";
  kind?: "text" | "image";
  updatedAt?: number; // ms epoch
  seen?: boolean;
};
export type PanelId = "chat" | "terminal" | "files" | "report" | "settings";
/** Every LABEL here is the backend's own (core/interaction.request_approval), so third-party text can
 *  only ever be a VALUE. Do not let a server name a field. */
export type ApprovalNotice = {
  head?: string;
  alert?: string;
  facts?: [string, string][];
  warn?: string;
  quote?: { title?: string; text?: string };
};
export type InputRequest = {
  mode: "input" | "approval" | "open_link";
  label: string;
  detail?: string;
  name?: string;
  inputKind?: "text" | "key" | "link" | "secret";
  url?: string;
  /** true = a backend tool BLOCKS on the answer: deliver via POST /input, never echo into the chat. */
  wait?: boolean;
  id: string;
  requestId?: string;
  family?: string;
  canAlways?: boolean;
  canAlwaysExact?: boolean;
  alwaysNote?: string;
  notice?: ApprovalNotice;
};
export type Subagent = {
  id: string;
  goal: string;
  steps: string[];
  status: "working" | "done" | "error";
  summary?: string;
};

export type TaskStatus = "pending" | "active" | "done" | "dropped";
export type Task = {
  id: string;
  text: string;
  status: TaskStatus;
  order: number;
};
export type TaskList = {
  list_id: string;
  title: string;
  tasks: Task[];
  status: "open" | "done" | "abandoned";
  rev: number;
};

interface KotobaState {
  emotion: Emotion;

  working: boolean;
  /** The latched owner of the work surface — see ownsWorkBracket. "" = no marked bracket. */
  workRunId: string;
  logs: LogLine[];
  files: FileArtifact[];
  openPanels: PanelId[];
  reportTitle: string | null;
  inputRequests: InputRequest[];
  subagents: Subagent[];
  tasks: TaskList | null;
  tasksOpen: boolean;
  tasksAutoOpenedFor: string | null; // list_id of the plan we already auto-opened — one-shot courtesy

  setEmotion: (emotion: Emotion) => void;

  setWorking: (working: boolean) => void;
  latchWorkRun: (runId?: string) => void;
  pushStep: (ev: StepEvent) => void;
  pushFile: (path: string, action: "created" | "edited") => void;
  setFiles: (files: FileArtifact[]) => void;
  markFileSeen: (path: string) => void;
  filesEpoch: number;
  bumpFilesEpoch: () => void;
  setReport: (title: string | null) => void;
  pushInputRequest: (req: InputRequest) => void;
  dismissInputRequest: (id?: string) => void; // no id → dismiss all (the call ended / everything cancelled)
  setTasks: (list: TaskList, fromRehydrate?: boolean) => void;
  setTasksOpen: (open: boolean) => void;
  spawnSubagent: (id: string, goal: string) => void;
  addSubagentStep: (id: string, text: string) => void;
  finishSubagent: (id: string, summary: string, ok?: boolean) => void;
  dismissSubagent: (id: string) => void;
  clearSubagents: () => void;
  clearWork: () => void;
  toggleBig: (id: PanelId) => void;
  openBig: (id: PanelId) => void;
  toggleSmall: (id: PanelId) => void;
  openSmall: (id: PanelId) => void;
}

const BIG: PanelId[] = ["report", "settings"];

export const useKotobaStore = create<KotobaState>((set) => ({
  emotion: "neutral",

  working: false,
  workRunId: "",
  logs: [],
  files: [],
  openPanels: [],
  reportTitle: null,
  inputRequests: [],
  subagents: [],
  tasks: null,
  tasksOpen: false,
  tasksAutoOpenedFor: null,

  setEmotion: (emotion) => set({ emotion }),

  setWorking: (working) => set({ working }),
  latchWorkRun: (runId) => set({ workRunId: runId ?? "" }),
  pushStep: (raw) =>
    set((s) => {
      const ev = scrubDeep(raw);
      if (ev.phase === "done" && ev.id) {
        const i = s.logs.findIndex((l) => l.id === ev.id);
        if (i >= 0) {
          const next = [...s.logs];
          next[i] = {
            ...next[i], result: ev.result, full: ev.full, ok: ev.ok, running: false, pending: !!ev.pending,
            outcome: ev.outcome, interrupted: !!ev.interrupted,
          };
          return { logs: next };
        }
      }
      const id = ev.id || `${Date.now()}-${Math.random()}`;
      const block: LogLine = ev.phase === "start"
        ? { id, stepKind: ev.step_kind, action: ev.action ?? ev.text, running: true }
        : { id, stepKind: ev.step_kind, action: ev.action, result: ev.result, full: ev.full, ok: ev.ok, outcome: ev.outcome, interrupted: !!ev.interrupted, text: ev.text, running: false, pending: !!ev.pending };
      const i = s.logs.findIndex((l) => l.id === id);
      if (i >= 0) {
        // A step cannot un-finish: a re-sent `done` would put the row back to running, and no second
        // `done` is coming.
        if (ev.phase === "start" && s.logs[i].running === false) return s;
        const next = [...s.logs];
        next[i] = { ...next[i], ...block };
        return { logs: next };
      }
      return { logs: [...s.logs, block].slice(-200) };
    }),
  pushFile: (path, action) =>
    set((s) => {
      const kind: FileArtifact["kind"] = /\.(png|jpe?g|gif|webp|bmp|svg|ico)$/i.test(path) ? "image" : "text";
      const now = Date.now();
      const idx = s.files.findIndex((f) => f.path === path);
      if (idx >= 0) {
        const prev = s.files[idx];
        const next = [...s.files];
        next[idx] = { ...prev, action: prev.action === "created" && action === "created" ? "created" : "edited", updatedAt: now, seen: false };
        return { files: next };
      }
      return { files: [...s.files, { path, action, kind, updatedAt: now, seen: false }] };
    }),
  setFiles: (files) =>
    set({ files }),
  markFileSeen: (path) =>
    set((s) => ({ files: s.files.map((f) => (f.path === path ? { ...f, seen: true } : f)) })),
  filesEpoch: 0,
  bumpFilesEpoch: () => set((s) => ({ filesEpoch: s.filesEpoch + 1 })),
  setReport: (title) => set({ reportTitle: title === null ? null : scrub(title) }),
  /** Scrubbed HERE, the one door into state: no draw site should have to remember. */
  pushInputRequest: (raw) =>
    set((s) => {
      const req = scrubDeep(raw);
      return s.inputRequests.some((r) => r.id === req.id)
        ? s
        : { inputRequests: [...s.inputRequests, req].slice(-8) };
    }),
  /** Match through the SAME gate the push used: bare `scrub` turns a newline into a space, so an id
   *  carrying one would never match its own card. */
  dismissInputRequest: (id) =>
    set((s) => ({ inputRequests: id ? s.inputRequests.filter((r) => r.id !== scrub(id, { newlines: true })) : [] })),
  /** A late REST rehydrate must not roll back a newer SSE frame: `rev` only orders WITHIN one list. */
  setTasks: (rawList, fromRehydrate) =>
    set((s) => {
      const list = scrubDeep(rawList);
      if (s.tasks && s.tasks.list_id === list.list_id && s.tasks.rev > list.rev) return s;
      if (fromRehydrate && s.tasks && s.tasks.list_id !== list.list_id) return s;
      const isNew = s.tasksAutoOpenedFor !== list.list_id;
      return {
        tasks: list,
        tasksOpen: isNew ? true : s.tasksOpen,
        tasksAutoOpenedFor: isNew ? list.list_id : s.tasksAutoOpenedFor,
      };
    }),
  setTasksOpen: (open) => set({ tasksOpen: open }),
  /** The subagent id stays RAW: it is matched against the raw id on every later frame. */
  spawnSubagent: (id, goal) =>
    set((s) =>
      s.subagents.some((a) => a.id === id)
        ? s
        : { subagents: [...s.subagents, { id, goal: scrub(goal, { newlines: true }), steps: [], status: "working" }] },
    ),
  addSubagentStep: (id, text) =>
    set((s) => ({
      subagents: s.subagents.map((a) => (a.id === id ? { ...a, steps: [...a.steps, scrub(text, { newlines: true })].slice(-40) } : a)),
    })),
  finishSubagent: (id, summary, ok = true) =>
    set((s) => ({
      subagents: s.subagents.map((a) =>
        a.id === id ? { ...a, status: ok ? "done" : "error", summary: scrub(summary, { newlines: true }) } : a,
      ),
    })),
  dismissSubagent: (id) => set((s) => ({ subagents: s.subagents.filter((a) => a.id !== id) })),
  clearSubagents: () => set({ subagents: [] }),
  /** `files` are deliberately KEPT on call end; a plan is not, and neither is the face — a call that
   *  ended on `sad` opened the next one still wearing it. */
  clearWork: () =>
    set({ logs: [], working: false, workRunId: "", emotion: "neutral", reportTitle: null, inputRequests: [], subagents: [], tasks: null, tasksOpen: false, tasksAutoOpenedFor: null }),
  toggleBig: (id) =>
    set((s) => (s.openPanels.includes(id) ? { openPanels: s.openPanels.filter((p) => p !== id) } : { openPanels: [id] })),
  openBig: (id) => set({ openPanels: [id] }),
  toggleSmall: (id) =>
    set((s) => {
      const base = s.openPanels.filter((p) => !BIG.includes(p));
      return {
        openPanels: base.includes(id) ? base.filter((p) => p !== id) : [...base, id],
      };
    }),
  openSmall: (id) =>
    set((s) => {
      const base = s.openPanels.filter((p) => !BIG.includes(p));
      return { openPanels: base.includes(id) ? base : [...base, id] };
    }),
}));
