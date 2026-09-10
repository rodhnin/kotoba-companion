/**
 * Pretty loading screen for /app while the Live2D model loads (it's a few MB + WebGL warmup, so a cold
 * visit would otherwise show an empty card). Idol-pop: the chibi Kotoba head bobs on a bouncing shadow
 * inside a dashed spinning ring, with rotating status lines and floating confetti. Fades out once
 * `done` flips true. `failed` unmounts it INSTANTLY (no "Ready!" fade — that would be a lie): this
 * fullscreen overlay captures every click, so a model that will never load must not leave it up.
 *
 * `name`/`lines` are optional and the defaults keep /app identical. The first-run screens pass their
 * own (components/Onboarding.tsx, which /setup renders).
 */
"use client";

import { useEffect, useState } from "react";

const display = "var(--font-display)";

const LINES = [
  "Waking Kotoba up…",
  "Warming up her voice…",
  "Booting expressions…",
  "Almost there…",
];

export default function CallLoader({
  done,
  failed = false,
  name = "Kotoba",
  lines = LINES,
}: {
  done: boolean;
  failed?: boolean;
  name?: string;
  lines?: string[];
}) {
  const [gone, setGone] = useState(false);
  const [line, setLine] = useState(0);

  useEffect(() => {
    if (done || failed) return;
    const iv = setInterval(() => setLine((l) => (l + 1) % lines.length), 1500);
    return () => clearInterval(iv);
  }, [done, failed, lines.length]);

  // unmount once the 600ms fade is over; it is already click-through by then
  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => setGone(true), 650);
    return () => clearTimeout(t);
  }, [done]);

  if (gone || failed) return null;

  return (
    <div
      aria-hidden={done}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 50,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: "2rem",
        background: "radial-gradient(120% 100% at 50% 0%, var(--cream) 0%, var(--cream-2) 100%)",
        opacity: done ? 0 : 1,
        transform: done ? "scale(1.04)" : "scale(1)",
        transition: "opacity 600ms ease, transform 600ms ease",
        pointerEvents: done ? "none" : "auto",
      }}
    >
      <LoaderShapes />

      {/* chibi head in a spinning dashed ring */}
      <div style={{ position: "relative", width: 220, height: 220, display: "grid", placeItems: "center" }}>
        <svg
          viewBox="0 0 100 100"
          width={220}
          height={220}
          style={{ position: "absolute", inset: 0, animation: "spin 3.2s linear infinite" }}
        >
          <circle
            cx="50"
            cy="50"
            r="46"
            fill="none"
            stroke="var(--ink)"
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray="6 12"
            opacity="0.55"
          />
        </svg>
        {/* progress arc (indeterminate, opposite spin for life) */}
        <svg
          viewBox="0 0 100 100"
          width={220}
          height={220}
          style={{ position: "absolute", inset: 0, animation: "spin 1.5s cubic-bezier(.6,.1,.4,.9) infinite reverse" }}
        >
          <circle cx="50" cy="50" r="40" fill="none" stroke="var(--coral)" strokeWidth="4" strokeLinecap="round" strokeDasharray="60 200" />
        </svg>

        {/* chibi + bouncing shadow. The art is a tight head crop with no padding, so the width IS her
            width — 160 sits inside the 220 ring; the box stays 150 tall only to place the shadow under her chin. */}
        <div style={{ position: "relative", width: 165, height: 150, display: "grid", placeItems: "center" }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/art/kotoba-chibi.webp"
            alt="Kotoba"
            style={{ width: 160, height: "auto", display: "block", animation: "kbob 1.4s ease-in-out infinite", filter: "drop-shadow(0 6px 0 rgba(33,26,46,0.12))" }}
          />
          <span
            style={{
              position: "absolute",
              bottom: 2,
              width: 78,
              height: 12,
              borderRadius: "50%",
              background: "rgba(33,26,46,0.18)",
              filter: "blur(2px)",
              animation: "kshadow 1.4s ease-in-out infinite",
            }}
          />
        </div>
      </div>

      {/* wordmark + status */}
      <div style={{ textAlign: "center" }}>
        <div style={{ fontFamily: display, fontWeight: 700, fontSize: "1.6rem" }}>
          <span style={{ color: "var(--coral)" }}>言</span> {name}
        </div>
        <div style={{ marginTop: 8, height: 22, position: "relative", minWidth: 200 }}>
          {lines.map((l, i) => (
            <span
              key={l}
              style={{
                position: "absolute",
                left: 0,
                right: 0,
                fontFamily: display,
                fontWeight: 500,
                fontSize: "0.95rem",
                color: "var(--ink)",
                opacity: i === line && !done ? 0.7 : 0,
                transform: i === line ? "translateY(0)" : "translateY(6px)",
                transition: "opacity 400ms ease, transform 400ms ease",
              }}
            >
              {done ? "Ready!" : l}
            </span>
          ))}
          {done && (
            <span style={{ position: "absolute", left: 0, right: 0, fontFamily: display, fontWeight: 600, fontSize: "0.95rem", color: "var(--mint)" }}>
              Ready!
            </span>
          )}
        </div>
      </div>

      {/* bouncing dots */}
      <div style={{ display: "flex", gap: 8 }}>
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: ["var(--coral)", "var(--grape)", "var(--mint)"][i],
              border: "2px solid var(--ink)",
              animation: `kdot 0.9s ease-in-out ${i * 0.15}s infinite`,
            }}
          />
        ))}
      </div>

      <style>{`
        @keyframes kbob { 0%,100%{ transform: translateY(0) rotate(-1deg);} 50%{ transform: translateY(-14px) rotate(1deg);} }
        @keyframes kshadow { 0%,100%{ transform: scaleX(1); opacity:0.18;} 50%{ transform: scaleX(0.7); opacity:0.1;} }
        @keyframes kdot { 0%,100%{ transform: translateY(0);} 50%{ transform: translateY(-9px);} }
      `}</style>
    </div>
  );
}

function LoaderShapes() {
  const shapes: React.CSSProperties[] = [
    { top: "16%", left: "12%", width: 22, height: 22, background: "var(--coral)", borderRadius: "50%" },
    { top: "24%", right: "14%", width: 0, height: 0, borderLeft: "14px solid transparent", borderRight: "14px solid transparent", borderBottom: "24px solid var(--grape)" },
    { bottom: "20%", left: "16%", width: 26, height: 11, background: "var(--sun)", borderRadius: 999 },
    { bottom: "24%", right: "15%", width: 18, height: 18, background: "var(--mint)", borderRadius: 5 },
  ];
  return (
    <div aria-hidden style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      {shapes.map((s, i) => (
        <span key={i} style={{ position: "absolute", animation: `drift ${5 + i}s ease-in-out ${i * 0.5}s infinite`, ["--r" as string]: "0deg", ...s }} />
      ))}
    </div>
  );
}
