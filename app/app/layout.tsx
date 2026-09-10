// CompanionLayout — full-immersion container for the call: the room backdrop, the Live2D canvas
// over it, and the UI overlay on top.
export default function CompanionLayout({ children }: { children: React.ReactNode }) {
  return <div style={{ position: "fixed", inset: 0, overflow: "hidden" }}>{children}</div>;
}
