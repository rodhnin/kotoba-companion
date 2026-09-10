/**
 * Collapsible transcript chat. Opening is TWO-PHASE so the circular reveal is not masked by the
 * width-collapse "curtain": the space opens first, then the circle grows, and closing reverses it.
 * Everything shares the 460ms the camera and the other panels use, with NO asymmetric close delay —
 * one caused a "push and return" bounce when switching panels. The wrapper anchors flex-end so the
 * push reveals the same side the circle grows from, and it carries no `overflow: hidden`, which would
 * clip the sticker shadow; the inner clip-path does the hiding.
 */
"use client";

import { useEffect, useRef, useState } from "react";

import { ChatIcon, FileTextIcon } from "@/components/icons";
import { ACCEPT_ATTR, ATTACH_HINT, isImageAttachment, isPdfAttachment, isTextAttachment, REJECT_MESSAGE } from "@/lib/attachments";
import { scrub } from "@/lib/text-security";

/**
 * `image` is either a blob: thumb of what the user attached or a gated /api/visual-memory URL she
 * recalled; `imageAlt` describes the latter (what the keepsake is OF), since "attachment" would lie.
 * `file` names an attachment with nothing to show — it is drawn as a chip, never folded into `text`,
 * so the transcript she is sent stays the words and the filename stays a label.
 */
export type Line = { id: number; from: "user" | "ai"; text: string; image?: string; imageAlt?: string; file?: string };

export default function TranscriptPanel({
  open,
  lines,
  onToggle,
  onSend,
  onSendFile,
  callActive,
  busy = false,
}: {
  open: boolean;
  lines: Line[];
  onToggle: () => void;
  onSend?: (text: string) => void;
  onSendFile?: (file: File, caption: string) => void;
  callActive?: boolean;
  busy?: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState("");
  const [attach, setAttach] = useState<{ file: File; preview?: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [lines]);

  useEffect(() => {
    const url = attach?.preview;
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [attach]);

  const canSend = !!callActive && !busy;

  const MAX_MEDIA = 10 * 1024 * 1024; // images / PDF
  const MAX_TEXT = 1 * 1024 * 1024;   // text files (further capped to chars in the handler)

  const fileKind = (f: File): "image" | "pdf" | "text" | null => {
    if (isImageAttachment(f)) return "image";
    if (isPdfAttachment(f)) return "pdf";
    if (isTextAttachment(f)) return "text";
    return null;
  };

  const stageFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = ""; // allow re-picking the same file
    if (!f) return;
    setErr(null);
    const kind = fileKind(f);
    if (!kind) { setErr(REJECT_MESSAGE); return; }
    const limit = kind === "text" ? MAX_TEXT : MAX_MEDIA;
    if (f.size > limit) { setErr(`That file is too large (max ${Math.round(limit / 1024 / 1024)} MB).`); return; }
    setAttach({ file: f, preview: kind === "image" ? URL.createObjectURL(f) : undefined });
  };

  const clearAttach = () => setAttach(null);

  const send = () => {
    if (!canSend) return;
    const t = draft.trim();
    if (attach) {
      onSendFile?.(attach.file, t);     // file (+ optional caption) → routed by the parent
      setAttach(null);
      setDraft("");
      return;
    }
    if (!t) return;
    onSend?.(t);
    setDraft("");
  };

  const ease = "cubic-bezier(0.22, 1, 0.36, 1)";
  const wrapTransition = `width 460ms ${ease}, margin 460ms ${ease}`;
  const clipTransition = open
    ? `clip-path 560ms ${ease} 200ms`   // reveal kicks in mid-push
    : `clip-path 340ms ${ease}`;

  return (
    <div
      style={{
        width: open ? "min(34vw, 360px)" : 0,
        transition: wrapTransition,
        display: "flex",
        justifyContent: "flex-end",
        flexShrink: 0,
        marginLeft: open ? "1.1rem" : 0,
        pointerEvents: open ? "auto" : "none",
      }}
      inert={!open}
    >
      <div
        style={{
          height: "min(72vh, 600px)",
          minWidth: "min(34vw, 360px)",
          display: "flex",
          flexDirection: "column",
          background: "var(--cream)",
          border: "4px solid var(--ink)",
          borderRadius: 24,
          boxShadow: "var(--shadow-pop)",
          clipPath: open ? "circle(150% at 100% 0%)" : "circle(0% at 100% 0%)",
          transition: clipTransition,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0.7rem 0.8rem 0.7rem 0.9rem",
            borderBottom: "3px solid var(--ink)",
            background: "#fff",
            borderTopLeftRadius: 20,
            borderTopRightRadius: 20,
          }}
        >
          <span style={{ display: "flex", alignItems: "center", gap: 9 }}>
            <span
              style={{
                display: "grid",
                placeItems: "center",
                width: 30,
                height: 30,
                borderRadius: 10,
                background: "var(--coral)",
                border: "2.5px solid var(--ink)",
                color: "#fff",
              }}
            >
              <ChatIcon width={17} height={17} />
            </span>
            <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "1rem" }}>Transcript</span>
          </span>
          <button
            onClick={onToggle}
            aria-label="Close transcript"
            style={{
              border: "2.5px solid var(--ink)",
              background: "var(--cream)",
              borderRadius: 9,
              width: 28,
              height: 28,
              display: "grid",
              placeItems: "center",
              padding: 0,
              boxShadow: "2px 2px 0 var(--ink)",
            }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" strokeWidth="3.2" strokeLinecap="round">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        <div
          ref={scrollRef}
          className="kotoba-scroll"
          style={{ flex: 1, overflowY: "auto", padding: "0.9rem", display: "flex", flexDirection: "column", gap: "0.6rem" }}
        >
          {lines.length === 0 && (
            <div style={{ margin: "auto", display: "flex", flexDirection: "column", alignItems: "center", gap: "0.9rem", padding: "1rem", textAlign: "center" }}>
              <span
                style={{
                  display: "grid",
                  placeItems: "center",
                  width: 64,
                  height: 64,
                  borderRadius: "50%",
                  background: "var(--cream-2)",
                  border: "3px solid var(--ink)",
                  color: "var(--grape)",
                  boxShadow: "var(--shadow-pop-sm)",
                }}
              >
                <ChatIcon width={28} height={28} />
              </span>
              <p style={{ opacity: 0.62, fontSize: "0.88rem", lineHeight: 1.5, maxWidth: 200, fontWeight: 500 }}>
                Your conversation shows up here once the call starts.
              </p>
            </div>
          )}
          {lines.map((l) => (
            <div
              key={l.id}
              style={{
                alignSelf: l.from === "ai" ? "flex-start" : "flex-end",
                maxWidth: "85%",
                background: l.from === "ai" ? "#fff" : "var(--grape)",
                color: l.from === "ai" ? "var(--ink)" : "#fff",
                border: "2.5px solid var(--ink)",
                borderRadius: 14,
                borderBottomLeftRadius: l.from === "ai" ? 4 : 14,
                borderBottomRightRadius: l.from === "ai" ? 14 : 4,
                padding: "0.5rem 0.75rem",
                fontSize: "0.88rem",
                lineHeight: 1.4,
                boxShadow: "2px 2px 0 var(--ink)",
                animation: "rise 0.25s ease both",
                overflowWrap: "anywhere",  // long unbroken filenames (session_…txt) must wrap, not overflow
                whiteSpace: "pre-wrap",
              }}
            >
              {l.from === "ai" && (
                <div style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "0.7rem", color: "var(--coral)", marginBottom: 2 }}>
                  Kotoba
                </div>
              )}
              {l.file && (
                <div style={{
                  display: "flex", alignItems: "center", gap: 5, marginBottom: l.text ? 5 : 0,
                  fontSize: "0.78rem", fontWeight: 600, opacity: 0.85,
                }}>
                  <FileTextIcon width={14} height={14} style={{ flex: "0 0 auto" }} aria-hidden />
                  {l.file}
                </div>
              )}
              {l.image && (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={l.image}
                  alt={l.imageAlt || "attachment"}
                  style={{ display: "block", maxWidth: "100%", maxHeight: 180, borderRadius: 9, border: "2px solid var(--ink)", marginBottom: l.text ? 6 : 0 }}
                />
              )}
              {l.text}
            </div>
          ))}
        </div>

        {/* Composer — attach and/or type, sent together. Works while muted (mute only stops the mic);
            requires the call live, since it rides the ElevenLabs session. */}
        <div
          style={{
            padding: "0.6rem 0.7rem",
            borderTop: "3px solid var(--ink)",
            background: "#fff",
            borderBottomLeftRadius: 20,
            borderBottomRightRadius: 20,
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
        >
          {err && (
            <div style={{ fontSize: "0.75rem", color: "var(--live)", fontWeight: 600 }}>{err}</div>
          )}
          {attach && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, background: "var(--cream-2)", border: "2px solid var(--ink)", borderRadius: 10, padding: "0.35rem 0.5rem" }}>
              {attach.preview ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={attach.preview} alt="" style={{ width: 34, height: 34, objectFit: "cover", borderRadius: 6, border: "1.5px solid var(--ink)" }} />
              ) : (
                <span style={{ display: "grid", placeItems: "center", width: 34, height: 34, borderRadius: 6, background: "#fff", border: "1.5px solid var(--ink)", fontSize: "0.62rem", fontWeight: 700 }}>
                  {(scrub(attach.file.name).split(".").pop() || "?").toUpperCase().slice(0, 4)}
                </span>
              )}
              {/* A picked file's NAME came from wherever the file did — a download can carry an RLO. */}
              <span style={{ flex: 1, minWidth: 0, fontSize: "0.8rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{scrub(attach.file.name)}</span>
              <button onClick={clearAttach} aria-label="Remove attachment" style={{ flexShrink: 0, border: "2px solid var(--ink)", background: "var(--cream)", borderRadius: 7, width: 24, height: 24, display: "grid", placeItems: "center", padding: 0, cursor: "pointer" }}>
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" strokeWidth="3.2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
              </button>
            </div>
          )}
          <div style={{ display: "flex", alignItems: "flex-end", gap: 8 }}>
            {/* A label-wrapped file input is the pattern that always opens the native picker: the
                browser handles the click itself with guaranteed user activation, where a scripted
                `input.click()` needs transient activation it may not be granted and can silently fail
                to open the dialog. The input is visually hidden but still in layout, so disabling it
                with no live call makes clicking the label a no-op — and so the focus ring belongs on
                the LABEL, the real target being a 1x1 box clipped to nothing. */}
            <label
              aria-label="Attach a file"
              title={callActive ? ATTACH_HINT : "Start the call to attach"}
              style={{
                flexShrink: 0, width: 38, height: 38, display: "grid", placeItems: "center",
                border: "2.5px solid var(--ink)", background: canSend ? "var(--cream)" : "var(--cream-2)",
                borderRadius: 11, boxShadow: "2px 2px 0 var(--ink)", cursor: canSend ? "pointer" : "default",
                opacity: canSend ? 1 : 0.5,
              }}
            >
              <input type="file" accept={ACCEPT_ATTR} onChange={stageFile} disabled={!canSend} style={{ position: "absolute", width: 1, height: 1, padding: 0, margin: -1, overflow: "hidden", clip: "rect(0 0 0 0)", whiteSpace: "nowrap", border: 0 }} />
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21.44 11.05l-8.49 8.49a5.5 5.5 0 0 1-7.78-7.78l8.49-8.49a3.5 3.5 0 0 1 4.95 4.95l-8.49 8.49a1.5 1.5 0 0 1-2.12-2.12l7.78-7.78" />
              </svg>
            </label>
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              rows={1}
              placeholder={callActive ? (attach ? "Add a note… (optional)" : "Type a message…") : "Start the call to chat"}
              disabled={!callActive}
              style={{
                flex: 1, resize: "none", maxHeight: 96,
                border: "2.5px solid var(--ink)", borderRadius: 11, padding: "0.5rem 0.65rem",
                fontFamily: "var(--font-body)", fontSize: "0.88rem", lineHeight: 1.35,
                background: callActive ? "var(--cream)" : "var(--cream-2)", color: "var(--ink)",
                outline: "none", opacity: callActive ? 1 : 0.6,
              }}
            />
            <button
              onClick={send}
              disabled={!canSend || (!draft.trim() && !attach)}
              aria-label="Send message"
              style={{
                flexShrink: 0, width: 38, height: 38, display: "grid", placeItems: "center",
                border: "2.5px solid var(--ink)",
                background: canSend && (draft.trim() || attach) ? "var(--coral)" : "var(--cream-2)",
                color: canSend && (draft.trim() || attach) ? "#fff" : "var(--ink)",
                borderRadius: 11, boxShadow: "2px 2px 0 var(--ink)",
                cursor: canSend && (draft.trim() || attach) ? "pointer" : "default",
                opacity: canSend && (draft.trim() || attach) ? 1 : 0.5,
              }}
            >
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
