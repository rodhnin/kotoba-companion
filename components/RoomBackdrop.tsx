// The webcam backdrop: Kotoba's room (still PNG) with subtle animated "life" so it doesn't feel dead
// — slow-drifting dust motes + a gentle warm light breathe. Sits behind the Live2D avatar in the card.
"use client";

import { useMemo } from "react";

export default function RoomBackdrop() {
  // A handful of dust motes at random positions/durations — memoized so they don't reshuffle.
  const motes = useMemo(
    () =>
      Array.from({ length: 14 }, (_, i) => ({
        left: `${(i * 37 + 11) % 100}%`,
        bottom: `${(i * 23) % 60}%`,
        delay: `${(i * 0.9) % 8}s`,
        dur: `${7 + (i % 5)}s`,
        size: 2 + (i % 3),
      })),
    [],
  );

  return (
    <div style={{ position: "absolute", inset: 0, overflow: "hidden" }}>
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundImage: "url(/scene/room.png)",
          backgroundSize: "cover",
          backgroundPosition: "center 38%",
          transform: "scale(1.04)",
        }}
      />
      {/* warm light breathe + soft vignette for the screen feel */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(120% 90% at 50% 30%, rgba(255,210,150,0.10), transparent 60%), radial-gradient(140% 120% at 50% 100%, rgba(20,12,30,0.45), transparent 55%)",
          mixBlendMode: "multiply",
          animation: "drift 9s ease-in-out infinite",
          ["--r" as string]: "0deg",
        }}
      />
      {motes.map((m, i) => (
        <span
          key={i}
          style={{
            position: "absolute",
            left: m.left,
            bottom: m.bottom,
            width: m.size,
            height: m.size,
            borderRadius: "50%",
            background: "rgba(255,240,210,0.9)",
            boxShadow: "0 0 6px rgba(255,235,200,0.8)",
            animation: `mote ${m.dur} linear ${m.delay} infinite`,
          }}
        />
      ))}
    </div>
  );
}
