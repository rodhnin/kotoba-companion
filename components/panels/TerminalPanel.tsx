/**
 * Work/voice terminal — the idol-pop look wrapped around a real terminal feel: each action is a
 * sticker card with a coloured type chip, the command in mono and a status mark, and its output sits
 * in a dark mini-console inside the card. A ✓ is the narrowest claim on the panel — the command ran
 * and the process said 0 — so every other way a row can end gets its own face, six in all. Refusal
 * and interruption are grey on purpose: neither is a fault, and painting your own "No" red reports
 * your decision as a malfunction. The console is closed by default because a card is
 * a status row; it opens to the whole output, where a listing the card trimmed to one line still lives.
 */
"use client";

import { useEffect, useRef, useState, type ComponentType } from "react";

import SidePanel from "@/components/panels/SidePanel";
import { BrainIcon, FileCodeIcon, FileTextIcon, GlobeIcon, SparkIcon, SpinnerIcon, TerminalIcon } from "@/components/icons";
import { stepStatus, useKotobaStore, type LogLine, type StepStatus } from "@/lib/store";

const KIND: Record<string, { label: string; color: string; on: string; icon: ComponentType<{ width?: number; height?: number }> }> = {
  shell: { label: "bash", color: "var(--mint)", on: "#fff", icon: TerminalIcon },
  code: { label: "python", color: "var(--grape)", on: "#fff", icon: FileCodeIcon },
  web: { label: "web", color: "var(--live)", on: "#fff", icon: GlobeIcon },
  file: { label: "file", color: "var(--coral)", on: "#fff", icon: FileTextIcon },
  mcp: { label: "mcp", color: "var(--sun)", on: "var(--ink)", icon: SparkIcon },
  skill: { label: "skill", color: "var(--grape)", on: "#fff", icon: BrainIcon },
  memory: { label: "memory", color: "var(--grape)", on: "#fff", icon: BrainIcon },
  tool: { label: "tool", color: "var(--ink)", on: "#fff", icon: SparkIcon },
};

const STATUS: Record<Exclude<StepStatus, "running">, { glyph: string; color: string; on: string; body: string; title: string }> = {
  ok: { glyph: "✓", color: "var(--mint)", on: "#fff", body: "#e9f7ef", title: "ran and finished cleanly" },
  failed: { glyph: "×", color: "var(--live)", on: "#fff", body: "#ff9d9d", title: "ran and failed" },
  refused: { glyph: "⊘", color: "var(--cream-2)", on: "var(--ink)", body: "#ddd6ea", title: "never ran — the reason is below" },
  interrupted: { glyph: "■", color: "var(--cream-2)", on: "var(--ink)", body: "#ddd6ea", title: "stopped while it was running" },
  pending: { glyph: "…", color: "var(--sun)", on: "var(--ink)", body: "#ffe9a8", title: "waiting for your approval — it hasn't run" },
  unknown: { glyph: "?", color: "var(--cream-2)", on: "var(--ink)", body: "#ddd6ea", title: "it ended, but nothing said how" },
};

/** Tidy the backend result: drop its 2-space indent, lift "exit=N" off the top, drop lone
 *  stdout:/stderr: labels. */
function parseResult(result?: string): string {
  if (!result) return "";
  const lines = result.split("\n").map((l) => l.replace(/^ {1,2}/, ""));
  if (/^exit=-?\d+$/.test(lines[0] || "")) lines.shift();
  return lines.join("\n").replace(/^(stdout|stderr):\s*$/gm, "").trim();
}

function StepCard({ log }: { log: LogLine }) {
  const [expanded, setExpanded] = useState(false);
  // Legacy plain line (no structure) → a small muted card.
  if (!log.stepKind && !log.action) {
    return (
      <div style={{ background: "#fff", border: "2px solid var(--ink)", borderRadius: 11, boxShadow: "2px 2px 0 var(--ink)", padding: "0.45rem 0.6rem", fontSize: "0.78rem", color: "var(--ink)", whiteSpace: "pre-wrap", wordBreak: "break-word", fontFamily: "var(--font-body)" }}>
        {log.text}
      </div>
    );
  }
  const meta = KIND[log.stepKind || "tool"] || KIND.tool;
  const Icon = meta.icon;
  const status = stepStatus(log);
  const mark = status === "running" ? null : STATUS[status];
  const body = parseResult(log.result);
  const full = (log.full ?? "").trim();
  // Nothing to open when the output already IS the line on the card — compared after the same tidying,
  // so `exit=0` framing alone is not "more".
  const hasMore = full !== "" && parseResult(full) !== body;
  const shown = expanded && hasMore ? full : body;

  return (
    <div style={{ background: "#fff", border: "2.5px solid var(--ink)", borderRadius: 13, boxShadow: "var(--shadow-pop-sm)", padding: "0.5rem 0.55rem", display: "flex", flexDirection: "column", gap: 7, animation: "rise 0.2s ease both" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 0 }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 4, flexShrink: 0, background: meta.color, color: meta.on, border: "2px solid var(--ink)", borderRadius: 999, padding: "1px 7px 1px 5px", fontSize: "0.6rem", fontWeight: 700, letterSpacing: "0.03em", textTransform: "uppercase", fontFamily: "var(--font-display)" }}>
          <Icon width={11} height={11} />
          {meta.label}
        </span>
        <span title={log.action} style={{ color: "var(--ink)", fontSize: "0.78rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, fontFamily: "ui-monospace, Menlo, monospace" }}>
          {log.action}
        </span>
        {mark === null ? (
          <span style={{ color: "var(--grape)", flexShrink: 0, display: "inline-flex" }}><SpinnerIcon width={13} height={13} /></span>
        ) : (
          <span
            title={mark.title}
            style={{ flexShrink: 0, display: "grid", placeItems: "center", width: 18, height: 18, borderRadius: "50%", border: "2px solid var(--ink)", background: mark.color, color: mark.on, fontSize: "0.62rem", fontWeight: 900, lineHeight: 1 }}
          >
            {mark.glyph}
          </span>
        )}
      </div>
      {mark !== null && shown && (
        <pre className="kotoba-scroll" style={{ margin: 0, background: "var(--ink)", color: mark.body, borderRadius: 9, padding: "0.45rem 0.55rem", fontSize: "0.72rem", lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: expanded ? 340 : 200, overflow: "auto", fontFamily: "ui-monospace, Menlo, monospace" }}>
          {shown}
        </pre>
      )}
      {mark !== null && hasMore && (
        <button
          onClick={() => setExpanded((e) => !e)}
          title={expanded ? "back to the summary line" : "everything the tool returned"}
          style={{ alignSelf: "flex-start", border: "2px solid var(--ink)", background: "var(--cream-2)", color: "var(--ink)", borderRadius: 999, padding: "0.1rem 0.55rem", fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "0.62rem", cursor: "pointer" }}
        >
          {expanded ? "Hide full output" : "Show full output"}
        </button>
      )}
    </div>
  );
}

export default function TerminalPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const logs = useKotobaStore((s) => s.logs);
  const working = useKotobaStore((s) => s.working);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [logs]);

  return (
    <SidePanel open={open} title="Terminal" accent="var(--mint)" icon={<TerminalIcon width={17} height={17} />} onClose={onClose}>
      <div
        ref={scrollRef}
        className="kotoba-scroll"
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          background: "var(--cream-2)",
          borderRadius: 14,
          border: "2.5px solid var(--ink)",
          padding: "0.6rem 0.6rem",
          display: "flex",
          flexDirection: "column",
          gap: "0.55rem",
        }}
      >
        {logs.length === 0 ? (
          <div style={{ margin: "auto", display: "flex", flexDirection: "column", alignItems: "center", gap: "0.7rem", padding: "1rem", textAlign: "center" }}>
            <span style={{ display: "grid", placeItems: "center", width: 52, height: 52, borderRadius: "50%", background: "#fff", border: "3px solid var(--ink)", color: "var(--mint)", boxShadow: "var(--shadow-pop-sm)" }}>
              <TerminalIcon width={24} height={24} />
            </span>
            <p style={{ opacity: 0.62, fontSize: "0.85rem", lineHeight: 1.5, maxWidth: 200, fontWeight: 500, fontFamily: "var(--font-body)", color: "var(--ink)" }}>
              {working ? "Getting started…" : "Nothing running yet — ask me to run or build something."}
            </p>
          </div>
        ) : (
          logs.map((l) => <StepCard key={l.id} log={l} />)
        )}
      </div>
    </SidePanel>
  );
}
