/**
 * The plan tab — the work-mode todo list, collapsed to a button in the corner and expanded into a card
 * that shares the same viewport anchor, so the two never appear to move.
 *
 * A SETTLED LIST MUST NEVER LOOK LIKE WORK IN PROGRESS. She closes a run's unmarked steps as
 * "abandoned" precisely so this panel cannot show a spinner at 1/3 for the rest of the call — honour
 * that ending rather than painting it as done. With no list at all the tab renders nothing: an
 * empty-state decoration would be a panel claiming there is a plan.
 */
"use client";

import { ListCheckIcon, SpinnerIcon } from "@/components/icons";
import { useKotobaStore, type Task } from "@/lib/store";

const EASE = "cubic-bezier(0.22, 1, 0.36, 1)";

const discBase: React.CSSProperties = {
  flexShrink: 0,
  display: "grid",
  placeItems: "center",
  width: 18,
  height: 18,
  borderRadius: "50%",
  border: "2px solid var(--ink)",
};

function StatusDisc({ task, working }: { task: Task; working: boolean }) {
  if (task.status === "done") {
    return (
      <span style={{ ...discBase, background: "var(--mint)" }}>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M4 12.5l5 5L20 6" />
        </svg>
      </span>
    );
  }
  if (task.status === "active") {
    return (
      <span style={{ ...discBase, background: "var(--grape)", color: "#fff" }}>
        {working
          ? <SpinnerIcon width={11} height={11} className="kotoba-task-spin" />
          : <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#fff" }} />}
      </span>
    );
  }
  if (task.status === "dropped") {
    return (
      <span style={{ ...discBase, background: "var(--cream-2)" }}>
        <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" strokeWidth="3.5" strokeLinecap="round">
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      </span>
    );
  }
  return (
    <span style={{ ...discBase, background: "var(--cream-2)", fontSize: "0.6rem", fontWeight: 700, fontFamily: "var(--font-display)", color: "var(--ink)" }}>
      {task.order}
    </span>
  );
}

function TaskRow({ task, working }: { task: Task; working: boolean }) {
  return (
    <div style={{ background: "#fff", border: "2.5px solid var(--ink)", borderRadius: 13, boxShadow: "var(--shadow-pop-sm)", padding: "0.45rem 0.55rem", display: "flex", alignItems: "center", gap: 9 }}>
      <StatusDisc task={task} working={working} />
      <span style={{
        flex: 1,
        fontSize: "0.82rem",
        lineHeight: 1.35,
        fontFamily: "var(--font-body)",
        color: "var(--ink)",
        textDecoration: task.status === "dropped" ? "line-through" : "none",
        opacity: task.status === "dropped" ? 0.5 : 1,
        wordBreak: "break-word",
      }}>
        {task.text}
      </span>
    </div>
  );
}

const ANCHOR: React.CSSProperties = {
  position: "fixed",
  right: "clamp(12px, 2vw, 28px)",
  bottom: "clamp(12px, 2vw, 24px)",
  zIndex: 24,
};

export default function TaskTab({ working }: { working: boolean }) {
  const tasks = useKotobaStore((s) => s.tasks);
  const tasksOpen = useKotobaStore((s) => s.tasksOpen);
  const setTasksOpen = useKotobaStore((s) => s.setTasksOpen);

  if (!tasks) return null;

  const sorted = [...tasks.tasks].sort((a, b) => a.order - b.order);
  const doneCount = tasks.tasks.filter((t) => t.status === "done").length;
  const total = tasks.tasks.length;
  const isComplete = tasks.status === "done";
  const isAbandoned = tasks.status === "abandoned";
  // A settled list must never look like work in progress — see the header.
  const hasActive = tasks.status === "open" && tasks.tasks.some((t) => t.status === "active");

  // Tab accent: grape while active, mint when done, sun when abandoned (stopped ≠ finished), white idle.
  const tabBg = hasActive ? "var(--grape)" : isComplete ? "var(--mint)" : isAbandoned ? "var(--sun)" : "#fff";
  const coloredTab = hasActive || isComplete || isAbandoned;
  const tabColor = hasActive || isComplete ? "#fff" : "var(--ink)";
  const accentBg = hasActive ? "var(--grape)" : isComplete ? "var(--mint)" : isAbandoned ? "var(--sun)" : "var(--ink)";

  return (
    <>
      {/* Reduced-motion: freeze the active-task spinner without touching the global spin keyframe. */}
      <style>{`@media (prefers-reduced-motion: reduce) { .kotoba-task-spin { animation: none !important; } }`}</style>

      {/* Collapsed tab — hidden while card is open */}
      <button
        onClick={() => setTasksOpen(true)}
        aria-label="Open task plan"
        style={{
          ...ANCHOR,
          display: tasksOpen ? "none" : "flex",
          alignItems: "center",
          gap: 7,
          height: 46,
          padding: "0 0.8rem",
          border: "3px solid var(--ink)",
          borderRadius: 18,
          background: tabBg,
          color: tabColor,
          fontFamily: "var(--font-display)",
          fontWeight: 700,
          fontSize: "0.88rem",
          boxShadow: "var(--shadow-pop-sm)",
          cursor: "pointer",
          whiteSpace: "nowrap",
        }}
      >
        <ListCheckIcon width={18} height={18} />
        <span>
          <span style={{ color: coloredTab ? "inherit" : "var(--coral)" }}>言</span>
          {" "}Plan
        </span>
        <span style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          background: coloredTab ? "rgba(255,255,255,0.22)" : "var(--cream-2)",
          border: "2px solid var(--ink)",
          borderRadius: 999,
          padding: "0 5px",
          fontSize: "0.65rem",
          fontWeight: 700,
          minWidth: 26,
          height: 18,
        }}>
          {doneCount}/{total}
        </span>
      </button>

      {/* Expanded card — clip-path circle from bottom-right corner, symmetric 460ms. */}
      <div
        style={{
          ...ANCHOR,
          width: "min(86vw, 320px)",
          maxHeight: "min(52vh, 420px)",
          display: "flex",
          flexDirection: "column",
          background: "var(--cream)",
          border: "4px solid var(--ink)",
          borderRadius: 24,
          boxShadow: "var(--shadow-pop)",
          clipPath: tasksOpen
            ? "circle(150% at 100% 100%)"
            : "circle(0% at 100% 100%)",
          transition: `clip-path 460ms ${EASE}`,
          pointerEvents: tasksOpen ? "auto" : "none",
        }}
        inert={!tasksOpen}
      >
        {/* Header: white strip + accent square + title + counter + close */}
        <div style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0.7rem 0.8rem 0.7rem 0.9rem",
          borderBottom: "3px solid var(--ink)",
          background: "#fff",
          borderTopLeftRadius: 20,
          borderTopRightRadius: 20,
          flexShrink: 0,
        }}>
          <span style={{ display: "flex", alignItems: "center", gap: 9 }}>
            <span style={{
              display: "grid",
              placeItems: "center",
              width: 30,
              height: 30,
              borderRadius: 10,
              background: accentBg,
              border: "2.5px solid var(--ink)",
              color: "#fff",
            }}>
              <ListCheckIcon width={17} height={17} />
            </span>
            <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "1rem" }}>
              {tasks.title || "Plan"}
            </span>
            <span style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              background: "var(--cream-2)",
              color: "var(--ink)",
              border: "2px solid var(--ink)",
              borderRadius: 999,
              padding: "0 5px",
              fontSize: "0.65rem",
              fontWeight: 700,
              minWidth: 26,
              height: 18,
            }}>
              {doneCount}/{total}
            </span>
            {isAbandoned && (
              /* Named, not implied by a colour: she stopped short, and the count alone reads as progress. */
              <span style={{
                fontFamily: "var(--font-body)",
                fontSize: "0.68rem",
                fontWeight: 600,
                color: "var(--ink)",
                opacity: 0.7,
              }}>
                stopped early
              </span>
            )}
          </span>
          <button
            onClick={() => setTasksOpen(false)}
            aria-label="Close plan"
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
              cursor: "pointer",
            }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" strokeWidth="3.2" strokeLinecap="round">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        {/* Body: padding wrapper so the cream-2 well sits inside the card border-radius. */}
        <div style={{ flex: 1, minHeight: 0, padding: "0.7rem", display: "flex", flexDirection: "column" }}>
          <div
            className="kotoba-scroll"
            style={{
              flex: 1,
              minHeight: 0,
              overflowY: "auto",
              background: "var(--cream-2)",
              border: "2.5px solid var(--ink)",
              borderRadius: 14,
              padding: "0.6rem",
              display: "flex",
              flexDirection: "column",
              gap: "0.5rem",
            }}
          >
            {sorted.map((t) => (
              /* A step left active on a settled list gets the dot, not the spinner — nothing is running. */
              <TaskRow key={t.id} task={t} working={working && tasks.status === "open"} />
            ))}
          </div>
        </div>
      </div>
    </>
  );
}
