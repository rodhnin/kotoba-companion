/**
 * Helper-Kotobas: each spawned subagent appears as a chibi in the corner; click one for its live
 * process. The row is capped at what the viewport can hold and the remainder becomes one counter
 * head at the end of the line.
 */
"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { mdComponents } from "@/lib/markdown";

import {
  CHIBI_GAP,
  CHIBI_SIZE,
  ROW_INSET_CSS,
  bubblePlacement,
  overflowLabel,
  splitRow,
} from "@/lib/subagent-row";
import { useKotobaStore, type Subagent } from "@/lib/store";

const CLOSED = "/subagents/chibi-closed.webp";
const OPEN = "/subagents/chibi-open.webp";

/** 0 on the server, the real width from the first client render, so the row never paints itself
 *  uncapped for a frame. Hydration is safe: the store is empty until the SSE feed says otherwise. */
function useViewportWidth(): number {
  const [width, setWidth] = useState(() => (typeof window === "undefined" ? 0 : window.innerWidth));
  useEffect(() => {
    const measure = () => setWidth(window.innerWidth);
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);
  return width;
}

export default function SubagentChibis() {
  const subagents = useKotobaStore((s) => s.subagents);
  const dismissSubagent = useKotobaStore((s) => s.dismissSubagent);
  const [openId, setOpenId] = useState<string | null>(null);
  const viewportWidth = useViewportWidth();

  const { visible, overflow } = splitRow(subagents.length, viewportWidth);
  const shown = subagents.slice(0, visible);
  const hidden = subagents.slice(visible);

  if (subagents.length === 0) return null;

  return (
    <div
      style={{
        position: "fixed",
        left: ROW_INSET_CSS,
        bottom: "clamp(12px, 2vw, 24px)",
        display: "flex",
        alignItems: "flex-end",
        gap: CHIBI_GAP,
        zIndex: 25,
      }}
    >
      {shown.map((a, i) => (
        <Chibi
          key={a.id}
          agent={a}
          index={i}
          viewportWidth={viewportWidth}
          selected={openId === a.id}
          onClick={() => setOpenId(openId === a.id ? null : a.id)}
          onDismiss={() => {
            if (openId === a.id) setOpenId(null);
            dismissSubagent(a.id);
          }}
        />
      ))}
      {overflow > 0 && <OverflowHead count={overflow} hidden={hidden} />}
      <style>{`
        @keyframes subagent-bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-5px)}}
        @keyframes think-dot{0%,80%,100%{transform:translateY(0);opacity:.45}40%{transform:translateY(-4px);opacity:1}}
        @keyframes bubble-pop{from{opacity:0;transform:scale(.9)}to{opacity:1;transform:scale(1)}}
        /* compact markdown inside a helper's tiny result bubble — tight spacing, small headings, wrap */
        .kotoba-md-compact{display:inline}
        .kotoba-md-compact>*:first-child{margin-top:0}
        .kotoba-md-compact>*:last-child{margin-bottom:0}
        .kotoba-md-compact p{margin:0 0 .4rem}
        .kotoba-md-compact ul,.kotoba-md-compact ol{margin:.2rem 0 .4rem;padding-left:1.1rem}
        .kotoba-md-compact li{margin:.1rem 0}
        .kotoba-md-compact h1,.kotoba-md-compact h2,.kotoba-md-compact h3,.kotoba-md-compact h4{font-family:var(--font-display);font-size:.8rem;font-weight:700;margin:.35rem 0 .2rem;line-height:1.2}
        .kotoba-md-compact code{font-family:ui-monospace,Menlo,monospace;font-size:.72rem;background:var(--cream-2);padding:.02rem .2rem;border-radius:4px}
        .kotoba-md-compact a{color:var(--grape);word-break:break-word}
        .kotoba-md-compact strong{font-weight:700}
      `}</style>
    </div>
  );
}

function Chibi({
  agent,
  index,
  viewportWidth,
  selected,
  onClick,
  onDismiss,
}: {
  agent: Subagent;
  index: number;
  viewportWidth: number;
  selected: boolean;
  onClick: () => void;
  onDismiss: () => void;
}) {
  const working = agent.status === "working";
  const src = selected ? OPEN : CLOSED;
  const animate = working && !selected;

  return (
    // The bob lives on the WRAPPER so the chibi AND its thinking bubble rise/fall together.
    <div
      style={{
        position: "relative",
        // the SELECTED stack lifts above siblings so a working helper's bubble never paints over the modal
        zIndex: selected ? 30 : 1,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        flexShrink: 0,
        animation: animate ? "subagent-bob 2.4s ease-in-out infinite" : "none",
      }}
    >
      {selected && <ProcessBubble agent={agent} placement={bubblePlacement(index, viewportWidth)} />}
      {animate && <ThinkingBubble />}

      <div style={{ position: "relative", width: CHIBI_SIZE, height: CHIBI_SIZE }}>
        <button
          onClick={onClick}
          aria-label={`Helper: ${agent.goal}`}
          title={agent.goal}
          style={{
            width: CHIBI_SIZE,
            height: CHIBI_SIZE,
            borderRadius: "50%",
            border: "3px solid var(--ink)",
            background: "var(--cream)",
            boxShadow: "var(--shadow-pop-sm)", // constant — clicking only opens her mouth, no movement
            padding: 0,
            overflow: "hidden",
            display: "grid",
            placeItems: "center",
          }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {/* 88%, not 112% — the art already fills 93% of its own square, so at 112% the circle bit
              through her hair on both sides and cut her chin. */}
          <img src={src} alt="helper" style={{ width: "88%", height: "88%", objectFit: "cover" }} />
        </button>
        <StatusBadge status={agent.status} />

        {!working && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onDismiss();
            }}
            aria-label="Dismiss helper"
            title="Dismiss"
            style={{
              position: "absolute",
              top: -3,
              right: -3,
              width: 18,
              height: 18,
              borderRadius: "50%",
              border: "2.5px solid var(--ink)",
              background: "var(--cream)",
              display: "grid",
              placeItems: "center",
              padding: 0,
              boxShadow: "1px 1px 0 var(--ink)",
            }}
          >
            <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" strokeWidth="3.6" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        )}
      </div>
    </div>
  );
}

/** The tail of the row, drawn as one more head with the count on it. It takes the same 60px on the
 *  same baseline as the heads, so it can never be the element that overflows. */
function OverflowHead({ count, hidden }: { count: number; hidden: Subagent[] }) {
  const label = overflowLabel(count);
  const goals = hidden.slice(0, 8).map((a) => `· ${a.goal}`);
  if (hidden.length > goals.length) goals.push(`· …and ${hidden.length - goals.length} more`);

  return (
    <div
      role="img"
      aria-label={`${count} more helper${count === 1 ? "" : "s"}`}
      title={goals.join("\n")}
      style={{ position: "relative", width: CHIBI_SIZE, height: CHIBI_SIZE, flexShrink: 0 }}
    >
      <span
        style={{
          position: "absolute",
          left: 5,
          top: -4,
          width: CHIBI_SIZE,
          height: CHIBI_SIZE,
          borderRadius: "50%",
          border: "3px solid var(--ink)",
          background: "var(--cream-2)",
          opacity: 0.6,
        }}
      />
      <span
        style={{
          position: "absolute",
          inset: 0,
          borderRadius: "50%",
          border: "3px solid var(--ink)",
          background: "var(--cream)",
          boxShadow: "var(--shadow-pop-sm)",
          display: "grid",
          placeItems: "center",
          fontFamily: "var(--font-display)",
          fontWeight: 700,
          fontSize: label.length > 3 ? "0.92rem" : "1.1rem",
          color: "var(--ink)",
          letterSpacing: "-0.02em",
        }}
      >
        {label}
      </span>
    </div>
  );
}

function StatusBadge({ status }: { status: Subagent["status"] }) {
  const bg = status === "working" ? "var(--sun)" : status === "error" ? "var(--live)" : "var(--mint)";
  return (
    <span
      style={{
        position: "absolute",
        right: -2,
        bottom: 2,
        width: 18,
        height: 18,
        borderRadius: "50%",
        border: "2.5px solid var(--ink)",
        background: bg,
        boxShadow: "1px 1px 0 var(--ink)",
        display: "grid",
        placeItems: "center",
      }}
    >
      {status === "working" ? (
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--ink)", animation: "pulse-live 0.9s ease-in-out infinite" }} />
      ) : status === "error" ? (
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3.6" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
      ) : (
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" strokeWidth="3.6" strokeLinecap="round" strokeLinejoin="round"><path d="M4 12.5l5 5L20 6" /></svg>
      )}
    </span>
  );
}

function ThinkingBubble() {
  return (
    <div
      style={{
        position: "absolute",
        bottom: "calc(100% + 12px)",
        left: "50%",
        transform: "translateX(-50%)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 5,
        minWidth: 46,
        height: 28,
        background: "var(--cream)",
        border: "2.5px solid var(--ink)",
        borderRadius: 999,
        boxShadow: "2px 2px 0 var(--ink)",
      }}
    >
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          style={{
            width: 5,
            height: 5,
            borderRadius: "50%",
            background: "var(--grape)",
            animation: `think-dot 1.1s ease-in-out ${i * 0.15}s infinite`,
          }}
        />
      ))}
      <span style={{ position: "absolute", top: "100%", left: "50%", transform: "translateX(-50%)", width: 0, height: 0, borderLeft: "7px solid transparent", borderRight: "7px solid transparent", borderTop: "9px solid var(--ink)" }} />
      <span style={{ position: "absolute", top: "100%", left: "50%", transform: "translateX(-50%)", marginTop: -2.5, width: 0, height: 0, borderLeft: "5px solid transparent", borderRight: "5px solid transparent", borderTop: "6px solid var(--cream)" }} />
    </div>
  );
}

function ProcessBubble({ agent, placement }: { agent: Subagent; placement: ReturnType<typeof bubblePlacement> }) {
  const working = agent.status === "working";
  const errored = agent.status === "error";
  const accent = working ? "var(--sun)" : errored ? "var(--live)" : "var(--mint)";
  const label = working ? "helper · working" : errored ? "helper · couldn't finish" : "helper · done";
  const { left, width, tailX } = placement;
  return (
    // The wrapper owns placement, the pop and the tails; the box inside owns the radius and the
    // overflow clip. On one element the clip (there for the white header's corners) statically
    // swallowed the tails at top:100% — ThinkingBubble never clips and its tails paint.
    <div
      style={{
        position: "absolute",
        bottom: "calc(100% + 14px)",
        left,
        width,
        transformOrigin: `${tailX}px bottom`, // grows upward from the chibi — anchored, no slide-in
        animation: "bubble-pop 0.18s ease both",
      }}
    >
      <div
        style={{
          background: "var(--cream)",
          border: "3px solid var(--ink)",
          borderRadius: 18,
          boxShadow: "var(--shadow-pop)",
          overflow: "hidden",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "0.55rem 0.7rem", background: "#fff", borderBottom: "3px solid var(--ink)" }}>
          <span style={{ width: 28, height: 28, borderRadius: "50%", border: "2.5px solid var(--ink)", overflow: "hidden", flexShrink: 0, background: "var(--cream)", display: "grid", placeItems: "center" }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={CLOSED} alt="" style={{ width: "88%", height: "88%", objectFit: "cover" }} />
          </span>
          <span style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
            <span style={{ fontFamily: "var(--font-display)", fontWeight: 600, fontSize: "0.66rem", color: accent, letterSpacing: "0.04em", textTransform: "uppercase" }}>
              {label}
            </span>
            <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "0.86rem", lineHeight: 1.2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{agent.goal}</span>
          </span>
        </div>

        <div style={{ padding: "0.6rem 0.7rem", display: "flex", flexDirection: "column", gap: 8 }}>
          <div
            className="kotoba-scroll"
            style={{
              maxHeight: 150,
              overflowY: "auto",
              background: "var(--ink)",   // the Terminal panel's slab, not the cream box
              border: "2px solid var(--ink)",
              borderRadius: 10,
              padding: "0.5rem 0.6rem",
              display: "flex",
              flexDirection: "column",
              gap: 3,
              fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace",
              fontSize: "0.71rem",
              lineHeight: 1.5,
              color: "#e9f7ef",
            }}
          >
            {agent.steps.length > 0 ? (
              agent.steps.map((st, i) => (
                <span key={i} style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", color: "#e9f7ef" }}>
                  <span style={{ color: "#6bd6a0" }}>$</span> {st}
                </span>
              ))
            ) : working ? (
              // placeholder ONLY while working with nothing to show — never lingers next to a DONE block
              <span style={{ color: "rgba(233,247,239,0.6)" }}>Getting started…</span>
            ) : (
              <span style={{ color: "rgba(233,247,239,0.6)" }}>Result is ready below.</span>
            )}
          </div>

          {(agent.status === "done" || errored) && agent.summary && (
            <div className="kotoba-scroll" style={{ background: "#fff", border: "2.5px solid var(--ink)", borderRadius: 12, boxShadow: "2px 2px 0 var(--ink)", padding: "0.5rem 0.6rem", fontSize: "0.78rem", lineHeight: 1.4, maxHeight: 260, overflowY: "auto" }}>
              <span style={{ display: "inline-block", fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "0.66rem", color: errored ? "#fff" : "var(--ink)", background: errored ? "var(--live)" : "var(--mint)", border: "2px solid var(--ink)", borderRadius: 999, padding: "0.05rem 0.45rem", marginBottom: 4 }}>{errored ? "ERROR" : "DONE"}</span>
              <div className="kotoba-md-compact">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents()}>{agent.summary}</ReactMarkdown>
              </div>
            </div>
          )}
        </div>
      </div>

      <span style={{ position: "absolute", top: "100%", left: tailX - 9, width: 0, height: 0, borderLeft: "9px solid transparent", borderRight: "9px solid transparent", borderTop: "11px solid var(--ink)" }} />
      <span style={{ position: "absolute", top: "100%", left: tailX - 7, width: 0, height: 0, borderLeft: "7px solid transparent", borderRight: "7px solid transparent", borderTop: "8px solid var(--cream)" }} />
    </div>
  );
}
