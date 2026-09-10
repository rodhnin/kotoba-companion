/**
 * Input / Key / Approval card — when Kotoba needs the user to TYPE something (a link, an API key) or to
 * approve a sensitive action. A floating idol-pop card centred over the call: it is a focused ask, not
 * an orbiting panel. Keys are sent to the backend only and never shown in the transcript.
 *
 * An approval is drawn from the backend's NAMED fields and its `notice` is never clipped: the label is
 * one string, so clipping decides for the reader which half matters — and on an MCP install card the
 * half that lost was "Secrets needed:", on the one card where a third party is asking for a secret.
 * What BLOCKING means and why there are two grants of different width are stated at those lines.
 */
"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { CheckIcon, EyeIcon, KeyboardIcon } from "@/components/icons";
import { apiFetch } from "@/lib/api";
import { addressed, answerFor, deliveryFor, grantsOffered, headlineOf, type GrantKind } from "@/lib/approval-card";
import type { InputRequest } from "@/lib/store";

/** A value that cannot break a line cannot forge a row of its own. */
const oneLine = (value: unknown) => String(value ?? "").replace(/\s+/g, " ").trim();

type Result = { id: string; isApproval: boolean; cancelled?: boolean; value?: string; posted?: boolean };

export default function InputRequestPanel({
  request,
  sessionId,
  apiUrl,
  onResult,
}: {
  request: InputRequest | null;
  sessionId: string;
  apiUrl: string;
  onResult: (r: Result) => void;
}) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  if (!request || !mounted) return null;

  return createPortal(
    <Card key={request.id} request={request} sessionId={sessionId} apiUrl={apiUrl} onResult={onResult} />,
    document.body,
  );
}

function Card({
  request,
  sessionId,
  apiUrl,
  onResult,
}: {
  request: InputRequest;
  sessionId: string;
  apiUrl: string;
  onResult: (r: Result) => void;
}) {
  const [value, setValue] = useState("");
  const [reveal, setReveal] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const sentRef = useRef(false);   // single-flight: ignore a second submit (Enter + click, double-click)
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const t = setTimeout(() => inputRef.current?.focus(), 60);
    return () => clearTimeout(t);
  }, []);

  const isKey = request.inputKind === "key";        // save Kotoba's OWN reusable credential (.env in DB)
  const isSecret = request.inputKind === "secret";  // user's ONE-TIME password — masked, never saved
  const masked = isKey || isSecret;
  const isApproval = request.mode === "approval";
  const isOpenLink = request.mode === "open_link";
  const id = request.id;

  const isCode = request.family === "execute_code";
  const grants = grantsOffered(request);
  const grantLabel: Record<GrantKind, string> = {
    yes: "Yes, go ahead",
    no: "No",
    exact: "Always allow this exact command",
    family: isCode ? "Always allow her to run Python" : `Always allow “${request.family}” — don't ask again`,
  };
  const grantNote: Partial<Record<GrantKind, string>> = {
    exact: "Only this line, exactly as written — anything else still asks. Revoke it in Settings.",
    family: isCode
      ? "Any code she writes then runs on this computer without asking, until you revoke it in Settings."
      : `Every “${request.family}” command runs without asking, until you revoke it in Settings.`,
  };
  const grantStyle: Record<GrantKind, React.CSSProperties> = {
    yes: btn("var(--mint)", "var(--ink)"),
    no: btn("var(--cream-2)", "var(--ink)"),
    exact: { ...btn("var(--grape)", "#fff"), flex: 1, fontSize: "0.82rem" },
    family: { ...btn("var(--sun)", "var(--ink)"), flex: 1, fontSize: "0.82rem" },
  };

  const label = request.label ?? "";

  const notice = isApproval ? request.notice : undefined;
  const facts = (notice?.facts ?? []).filter((f) => Array.isArray(f) && f.length >= 2);

  // First NON-BLANK line: a card asking for a yes must name what runs, not fall back to the
  // placeholder. A `sh -c` heredoc puts a whole document in `label`, so the rest goes behind "Show
  // full command" — never a clipped `notice` field, which would decide which half matters.
  const approval = headlineOf(label);
  const headline = notice ? oneLine(notice.head) : isApproval ? approval.headline : label;
  const hasMore = notice ? false : isApproval ? approval.hasMore : !!request.detail;

  // Opened on the user gesture, so it is not popup-blocked; nothing is posted back or echoed.
  const openLink = () => {
    if (sentRef.current) return;
    sentRef.current = true;
    if (request.url) window.open(request.url, "_blank", "noopener,noreferrer");
    onResult({ id, isApproval: false, cancelled: true });
  };

  // request_id answers THIS card — without it the backend falls back to "whichever opened last".
  const post = (body: Record<string, unknown>) => {
    apiFetch(`${apiUrl}/api/session/${sessionId}/input`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(addressed(body, request.requestId)),
    }).catch(() => {});
  };

  const approve = (kind: GrantKind) => {
    if (sentRef.current) return;
    sentRef.current = true;
    post(answerFor(kind));
    onResult({ id, isApproval: true });
  };

  /** BLOCKING (key/secret, or any `request.wait`) resolves the tool's Future through POST /input and
   *  the value NEVER re-enters the conversation. A key card must also send its storage id (`name`), or
   *  the backend cannot namespace it and the value is promised and then dropped. */
  const submit = () => {
    if (sentRef.current) return;
    sentRef.current = true;
    const delivery = deliveryFor(request, value);
    if (delivery.kind === "post") {
      post(delivery.body);
      onResult({ id, isApproval: false, value, posted: true });
    } else {
      onResult({ id, isApproval: false, value });
    }
  };

  const cancel = () => {
    if (sentRef.current) return;
    sentRef.current = true;
    onResult({ id, isApproval: false, cancelled: true });
  };

  /** Portalled to <body>: in place it inherits the avatar stage's box, and that stage's transform traps
   *  `position: fixed`. Escaping the subtree is the only fix that holds. */
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        display: "grid",
        placeItems: "center",
        zIndex: 60,
        background: "rgba(20,14,28,0.32)",
        backdropFilter: "blur(3px)",
        animation: "rise 0.25s ease both",
      }}
    >
      <div
        style={{
          width: "min(90%, 420px)",
          maxHeight: "min(72%, 460px)",
          background: "var(--cream)",
          border: "4px solid var(--ink)",
          borderRadius: 22,
          boxShadow: "var(--shadow-pop)",
          padding: "1.1rem 1.2rem",
          display: "flex",
          flexDirection: "column",
          gap: "0.9rem",
        }}
      >
        <div style={{ display: "flex", alignItems: "flex-start", gap: 10, minHeight: 0, flexShrink: 1 }}>
          <span style={{ display: "grid", placeItems: "center", width: 34, height: 34, borderRadius: 11, background: isApproval ? "var(--sun)" : "var(--grape)", border: "2.5px solid var(--ink)", color: isApproval ? "var(--ink)" : "#fff", flexShrink: 0 }}>
            {isApproval ? <CheckIcon width={18} height={18} /> : <KeyboardIcon width={18} height={18} />}
          </span>
          <span style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0, minHeight: 0, flex: 1 }}>
            <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "1.02rem", lineHeight: 1.25, minWidth: 0, overflowWrap: "anywhere", wordBreak: "break-word" }}>
              {headline || "Is this okay?"}
            </span>
            {hasMore && (
              <>
                <button
                  onClick={() => setExpanded((e) => !e)}
                  style={{ alignSelf: "flex-start", border: "2px solid var(--ink)", background: "var(--cream-2)", color: "var(--ink)", borderRadius: 999, padding: "0.1rem 0.55rem", fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "0.68rem", cursor: "pointer" }}
                >
                  {expanded
                    ? (isApproval ? "Hide full command" : "Hide description")
                    : (isApproval ? "Show full command" : "See description")}
                </button>
                {expanded && (
                  isApproval ? (
                    // minHeight:0 or the block can't shrink when the card caps its own height.
                    <pre
                      className="kotoba-scroll"
                      style={{
                        margin: 0, maxHeight: 190, minHeight: 0, overflow: "auto", borderRadius: 10,
                        padding: "0.5rem 0.6rem", fontSize: "0.72rem",
                        lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word",
                        background: "var(--ink)", color: "#e9f7ef",
                        fontFamily: "ui-monospace, Menlo, monospace",
                      }}
                    >
                      {label}
                    </pre>
                  ) : (
                    <p
                      className="kotoba-scroll"
                      style={{
                        margin: 0, maxHeight: 190, minHeight: 0, overflow: "auto", borderRadius: 10,
                        padding: "0.5rem 0.6rem", fontSize: "0.82rem",
                        lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word",
                        background: "var(--cream-2)", color: "var(--ink)",
                        border: "2px solid var(--ink)",
                      }}
                    >
                      {request.detail}
                    </p>
                  )
                )}
              </>
            )}
          </span>
        </div>

        {notice && (
          <div className="kotoba-scroll" style={{ display: "flex", flexDirection: "column", gap: "0.5rem", minHeight: 0, flexShrink: 1, overflow: "auto" }}>
            {notice.alert && (
              <p style={{ margin: 0, background: "var(--sun)", border: "2.5px solid var(--ink)", borderRadius: 12, padding: "0.45rem 0.6rem", fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "0.84rem", lineHeight: 1.35, overflowWrap: "anywhere", wordBreak: "break-word" }}>
                {oneLine(notice.alert)}
              </p>
            )}
            {facts.map(([key, value], i) => (
              <div key={`${key}-${i}`} style={{ display: "flex", gap: 9, alignItems: "baseline" }}>
                <span style={{ flex: "0 0 5rem", fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "0.64rem", letterSpacing: "0.04em", textTransform: "uppercase", opacity: 0.6 }}>
                  {oneLine(key)}
                </span>
                <span style={{ flex: 1, minWidth: 0, fontFamily: "ui-monospace, Menlo, monospace", fontSize: "0.74rem", lineHeight: 1.45, overflowWrap: "anywhere", wordBreak: "break-word" }}>
                  {oneLine(value)}
                </span>
              </div>
            ))}
            {notice.warn && (
              <p style={{ margin: 0, background: "var(--coral)", color: "#fff", border: "2.5px solid var(--ink)", borderRadius: 12, padding: "0.45rem 0.6rem", fontSize: "0.76rem", lineHeight: 1.4 }}>
                {oneLine(notice.warn)}
              </p>
            )}
            {notice.quote?.text && (
              <div style={{ background: "var(--cream-2)", border: "2px dashed var(--ink)", borderRadius: 12, padding: "0.4rem 0.6rem" }}>
                <span style={{ display: "block", fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "0.62rem", letterSpacing: "0.04em", textTransform: "uppercase", opacity: 0.6 }}>
                  {oneLine(notice.quote.title)}
                </span>
                <p className="kotoba-scroll" style={{ margin: "0.2rem 0 0", maxHeight: 84, overflow: "auto", fontSize: "0.76rem", fontStyle: "italic", lineHeight: 1.45, overflowWrap: "anywhere", wordBreak: "break-word" }}>
                  {oneLine(notice.quote.text)}
                </p>
              </div>
            )}
          </div>
        )}

        {isApproval ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "0.55rem", flexShrink: 0 }}>
            <div style={{ display: "flex", gap: "0.7rem" }}>
              {grants.filter((k) => k === "yes" || k === "no").map((kind) => (
                <button key={kind} onClick={() => approve(kind)} style={grantStyle[kind]}>
                  {kind === "yes" && <CheckIcon width={17} height={17} />} {grantLabel[kind]}
                </button>
              ))}
            </div>
            {grants.filter((k) => k === "exact" || k === "family").map((kind) => (
              <div key={kind} style={{ display: "flex", flexDirection: "column", gap: "0.55rem" }}>
                <button onClick={() => approve(kind)} style={grantStyle[kind]}>
                  {grantLabel[kind]}
                </button>
                <p style={{ fontSize: "0.7rem", opacity: 0.6, lineHeight: 1.4, margin: "-0.2rem 0 0" }}>
                  {grantNote[kind]}
                </p>
              </div>
            ))}
            {!grants.includes("family") && request.alwaysNote && (
              <p style={{ fontSize: "0.7rem", opacity: 0.6, lineHeight: 1.4, margin: "-0.2rem 0 0" }}>
                {request.alwaysNote}
              </p>
            )}
          </div>
        ) : isOpenLink ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "0.7rem", flexShrink: 0 }}>
            <a
              href={request.url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => { e.preventDefault(); openLink(); }}
              style={{ fontFamily: "var(--font-body)", fontSize: "0.8rem", color: "var(--grape)", background: "#fff", border: "2.5px solid var(--ink)", borderRadius: 12, padding: "0.55rem 0.7rem", overflowWrap: "anywhere", wordBreak: "break-word", textDecoration: "none" }}
            >
              {request.url}
            </a>
            <div style={{ display: "flex", gap: "0.6rem" }}>
              <button onClick={openLink} style={btn("var(--mint)", "var(--ink)")}>
                Open in new tab
              </button>
              <button onClick={cancel} style={btn("var(--cream-2)", "var(--ink)")}>
                Not now
              </button>
            </div>
          </div>
        ) : (
          <>
            <div style={{ position: "relative", display: "flex", alignItems: "center" }}>
              <input
                ref={inputRef}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && value.trim() && submit()}
                type={masked && !reveal ? "password" : "text"}
                placeholder={masked ? "Type it here — hidden, never shown in chat" : "Type here…"}
                style={{ ...field(), paddingRight: masked ? 42 : 12 }}
              />
              {masked && (
                <button onClick={() => setReveal((r) => !r)} aria-label="Show/hide" style={{ position: "absolute", right: 8, border: "none", background: "transparent", color: "var(--ink)", opacity: 0.6, display: "grid", placeItems: "center" }}>
                  <EyeIcon width={18} height={18} />
                </button>
              )}
            </div>
            {masked && (
              <p style={{ fontSize: "0.74rem", opacity: 0.6, lineHeight: 1.4, margin: "-0.3rem 0 0" }}>
                {isSecret
                  ? "Used once for this step and then discarded — it's never saved and never appears in the conversation."
                  : "Saved with your credentials on the server only — it never appears in the conversation."}
              </p>
            )}
            <div style={{ display: "flex", gap: "0.6rem", justifyContent: "flex-end", flexShrink: 0 }}>
              <button onClick={cancel} style={btn("var(--cream-2)", "var(--ink)", true)}>
                Cancel
              </button>
              <button onClick={submit} disabled={!value.trim()} style={{ ...btn("var(--coral)", "#fff", true), opacity: value.trim() ? 1 : 0.5 }}>
                Send
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function field(): React.CSSProperties {
  return {
    width: "100%",
    border: "2.5px solid var(--ink)",
    borderRadius: 12,
    padding: "0.6rem 0.75rem",
    fontFamily: "var(--font-body)",
    fontSize: "0.92rem",
    background: "#fff",
    color: "var(--ink)",
    outline: "none",
  };
}

function btn(bg: string, color: string, slim = false): React.CSSProperties {
  return {
    flex: slim ? "0 0 auto" : 1,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 7,
    border: "3px solid var(--ink)",
    background: bg,
    color,
    borderRadius: 13,
    padding: slim ? "0.5rem 1rem" : "0.65rem 1rem",
    fontFamily: "var(--font-display)",
    fontWeight: 600,
    fontSize: "0.9rem",
    boxShadow: "2px 2px 0 var(--ink)",
  };
}
