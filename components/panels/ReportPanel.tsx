// Report viewer — the widest panel. Kotoba opens it (report_ready) and talks the user through the
// PDF-style report while it's shown here. Renders the filled HTML in a sandboxed iframe; the user can
// download/print it to PDF. Idol-pop sticker frame to match the rest of the call.
"use client";

import { useEffect, useState } from "react";

import { DownloadIcon, ReportIcon } from "@/components/icons";
import { apiFetch } from "@/lib/api";

export default function ReportPanel({
  open,
  sessionId,
  apiUrl,
  title,
  onClose,
}: {
  open: boolean;
  sessionId: string;
  apiUrl: string;
  title: string | null;
  onClose: () => void;
}) {
  const [html, setHtml] = useState<string>("");
  // "loading" and "there is no report" must stay distinguishable: opened from the dock with nothing
  // made yet, a spinner reads as a report that never arrives.
  const [empty, setEmpty] = useState(false);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setEmpty(false);
    apiFetch(`${apiUrl}/api/session/${sessionId}/report`)
      .then((r) => (r.ok ? r.text() : Promise.reject(r.status)))
      .then((t) => {
        if (alive) setHtml(t);
      })
      .catch(() => {
        if (alive) {
          setHtml("");
          setEmpty(true);
        }
      });
    return () => {
      alive = false;
    };
  }, [open, apiUrl, sessionId, title]);

  const [saving, setSaving] = useState(false);

  const downloadPdf = async () => {
    if (saving) return;
    setSaving(true);
    try {
      const r = await apiFetch(`${apiUrl}/api/session/${sessionId}/report.pdf`);
      if (!r.ok) return;
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = "kotoba-report.pdf";
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch {
    } finally {
      setSaving(false);
    }
  };

  const ease = "cubic-bezier(0.22, 1, 0.36, 1)";

  return (
    <div
      style={{
        width: open ? "min(52vw, 680px)" : 0, // the wide report panel — takes the stage (solo)
        transition: `width 460ms ${ease}, margin 460ms ${ease}`,
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
          height: "min(80vh, 720px)",
          minWidth: "min(52vw, 680px)",
          display: "flex",
          flexDirection: "column",
          background: "var(--cream)",
          border: "4px solid var(--ink)",
          borderRadius: 24,
          boxShadow: "var(--shadow-pop)",
          clipPath: open ? "circle(150% at 0% 0%)" : "circle(0% at 0% 0%)",
          transition: open ? `clip-path 560ms ${ease} 200ms` : `clip-path 340ms ${ease}`,
          overflow: "hidden",
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
            flexShrink: 0,
          }}
        >
          <span style={{ display: "flex", alignItems: "center", gap: 9, minWidth: 0 }}>
            <span style={{ display: "grid", placeItems: "center", width: 30, height: 30, borderRadius: 10, background: "var(--coral)", border: "2.5px solid var(--ink)", color: "#fff", flexShrink: 0 }}>
              <ReportIcon width={17} height={17} />
            </span>
            <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "1rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {title || "Report"}
            </span>
          </span>
          <span style={{ display: "flex", gap: 7 }}>
            <button
              onClick={() => void downloadPdf()}
              disabled={saving}
              aria-label="Download as PDF"
              title="Download as PDF"
              style={{ display: "flex", alignItems: "center", gap: 6, border: "2.5px solid var(--ink)", background: "var(--mint)", color: "var(--ink)", borderRadius: 9, padding: "0.25rem 0.6rem", fontFamily: "var(--font-display)", fontWeight: 600, fontSize: "0.78rem", boxShadow: "2px 2px 0 var(--ink)", opacity: saving ? 0.6 : 1 }}
            >
              <DownloadIcon width={15} height={15} /> {saving ? "Saving…" : "PDF"}
            </button>
            <button
              onClick={onClose}
              aria-label="Close report"
              style={{ border: "2.5px solid var(--ink)", background: "var(--cream)", borderRadius: 9, width: 30, height: 30, display: "grid", placeItems: "center", padding: 0, boxShadow: "2px 2px 0 var(--ink)" }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" strokeWidth="3.2" strokeLinecap="round">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </span>
        </div>

        <div style={{ flex: 1, minHeight: 0, background: "var(--cream)" }}>
          {html ? (
            <iframe
              title="report"
              srcDoc={html}
              // allow-popups (+ escape): the report's source citations are target="_blank" anchors, and
              // without these a click silently does nothing. No allow-scripts, so only a real click opens one.
              sandbox="allow-modals allow-popups allow-popups-to-escape-sandbox"
              style={{ width: "100%", height: "100%", border: "none", background: "var(--cream)" }}
            />
          ) : (
            <div style={{ height: "100%", display: "grid", placeItems: "center", opacity: 0.6, fontWeight: 500, textAlign: "center", padding: "1.5rem" }}>
              {empty ? "No report yet — ask me for one and it opens here." : "Preparing your report…"}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
