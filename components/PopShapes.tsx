// Idol-pop decorative confetti: triangles, blobs, squiggles drifting around the stage.
"use client";

const SHAPES = [
  { type: "tri", color: "var(--coral)", top: "12%", left: "8%", size: 54, r: "-12deg", d: "7s" },
  { type: "blob", color: "var(--grape)", top: "22%", right: "7%", size: 70, r: "10deg", d: "9s" },
  { type: "ring", color: "var(--mint)", bottom: "18%", left: "6%", size: 46, r: "0deg", d: "8s" },
  { type: "tri", color: "var(--sun)", bottom: "14%", right: "10%", size: 40, r: "20deg", d: "6.5s" },
  { type: "dot", color: "var(--coral)", top: "46%", left: "3%", size: 18, r: "0deg", d: "5.5s" },
  { type: "dot", color: "var(--grape)", top: "70%", right: "4%", size: 14, r: "0deg", d: "6s" },
] as const;

export default function PopShapes() {
  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 1, pointerEvents: "none", overflow: "hidden" }}>
      {SHAPES.map((s, i) => {
        const base: React.CSSProperties = {
          position: "absolute",
          width: s.size,
          height: s.size,
          animation: `drift ${s.d} ease-in-out ${i * 0.4}s infinite`,
          ["--r" as string]: s.r,
          ...("top" in s ? { top: s.top } : {}),
          ...("bottom" in s ? { bottom: s.bottom } : {}),
          ...("left" in s ? { left: s.left } : {}),
          ...("right" in s ? { right: s.right } : {}),
        };
        if (s.type === "tri")
          return (
            <span
              key={i}
              style={{
                ...base,
                width: 0,
                height: 0,
                borderLeft: `${s.size / 2}px solid transparent`,
                borderRight: `${s.size / 2}px solid transparent`,
                borderBottom: `${s.size}px solid ${s.color}`,
              }}
            />
          );
        if (s.type === "ring")
          return (
            <span
              key={i}
              style={{ ...base, borderRadius: "50%", border: `7px solid ${s.color}`, background: "transparent" }}
            />
          );
        if (s.type === "blob")
          return (
            <span
              key={i}
              style={{
                ...base,
                background: s.color,
                borderRadius: "42% 58% 63% 37% / 47% 38% 62% 53%",
              }}
            />
          );
        return <span key={i} style={{ ...base, background: s.color, borderRadius: "50%" }} />;
      })}
    </div>
  );
}
