// The left rail's panel — the same sticker card and circular reveal as the transcript, so every
// orbiting panel feels like one family. It lives inside a fixed-width rail and never pushes on its
// own: it grows and shrinks its HEIGHT share, so a second panel in the rail cannot push the camera.
//
// It once also had a standalone mode that animated its own width and could sit on the right, for the
// transcript. The transcript hand-rolls its own card now, and both remaining callers — FilesPanel and
// TerminalPanel, each mounted once in the rail — were left-and-railed, so those branches could not be
// reached. A panel that needs to push or to open from the right has to bring that mode back on
// purpose rather than inherit a default nothing had exercised.
"use client";

import type { ReactNode } from "react";

export default function SidePanel({
  open,
  title,
  accent,
  icon,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  accent: string;
  icon: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  const ease = "cubic-bezier(0.22, 1, 0.36, 1)";
  const origin = "100% 0%";

  const outerStyle: React.CSSProperties = {
    flex: open ? "1 1 0" : "0 0 0",
    minHeight: 0,
    overflow: "hidden",
    // It clips so the card can grow its height share from zero without spilling — but a sticker's
    // shadow lives OUTSIDE its box, and flush against the clip it was sliced off down the right-hand
    // side and along the bottom. The room goes back when the panel is open and nowhere else.
    boxSizing: "border-box",
    paddingRight: open ? 6 : 0,
    paddingBottom: open ? 6 : 0,
    transition: `flex 460ms ${ease}`,
    pointerEvents: open ? "auto" : "none",
  };

  const clipTransition = open ? `clip-path 560ms ${ease} 200ms` : `clip-path 340ms ${ease}`;

  return (
    <div style={outerStyle} inert={!open}>
      <div
        style={{
          height: "100%",
          minWidth: 0,
          width: "100%",
          display: "flex",
          flexDirection: "column",
          background: "var(--cream)",
          border: "4px solid var(--ink)",
          borderRadius: 24,
          boxShadow: "var(--shadow-pop)",
          clipPath: open ? `circle(150% at ${origin})` : `circle(0% at ${origin})`,
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
            flexShrink: 0,
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
                background: accent,
                border: "2.5px solid var(--ink)",
                color: "#fff",
              }}
            >
              {icon}
            </span>
            <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "1rem" }}>{title}</span>
          </span>
          <button
            onClick={onClose}
            aria-label={`Close ${title}`}
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
          className="kotoba-scroll"
          style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "0.9rem", display: "flex", flexDirection: "column", gap: "0.55rem" }}
        >
          {children}
        </div>
      </div>
    </div>
  );
}
