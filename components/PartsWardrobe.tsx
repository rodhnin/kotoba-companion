/**
 * What she keeps on, chosen by looking. Auto-hiding reads the names a rigger typed, so it only ever
 * recognises the languages somebody thought to list — a halo, a pet or a prop nobody can name stays
 * on, and no word list will ever fix that. This is the answer that works for any model: the parts as
 * the model declares them, toggled against her live portrait beside it.
 *
 * It opens to the RIGHT of the passport, absolutely, and scrolls INSIDE its own box: the step has to
 * stay one screen tall, and a model with ninety-odd parts would otherwise push it off the bottom.
 */
"use client";

import { useMemo, useState } from "react";

import { EyeIcon } from "@/components/icons";

export type Part = { id: string; name?: string };

const display = "var(--font-display)";

export default function PartsWardrobe({
  parts,
  hidden,
  onChange,
  onClose,
}: {
  parts: Part[];
  hidden: string[];
  onChange: (hidden: string[]) => void;
  onClose: () => void;
}) {
  const [q, setQ] = useState("");

  const toggle = (id: string) =>
    onChange(hidden.includes(id) ? hidden.filter((h) => h !== id) : [...hidden, id]);

  // Whatever is off sits at the top: it is the short list, and the one somebody came here to undo.
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const match = (p: Part) =>
      !needle || p.id.toLowerCase().includes(needle) || (p.name || "").toLowerCase().includes(needle);
    const off = parts.filter((p) => hidden.includes(p.id));
    const on = parts.filter((p) => !hidden.includes(p.id));
    return [...off, ...on].filter(match);
  }, [parts, hidden, q]);


  return (
    <div className="pw" style={shell}>
      <style>{`
        @keyframes pw-in { from { opacity: 0; transform: translateX(-8px) scale(.97) } to { opacity: 1; transform: none } }
        .pw { animation: pw-in .26s cubic-bezier(.2,.85,.25,1) both }
        .pw-row:hover { background: var(--cream-2) }
        @media (prefers-reduced-motion: reduce) { .pw { animation: none } }
      `}</style>

      <div style={band}>
        <span style={{ fontFamily: display, fontWeight: 800, fontSize: "0.62rem", letterSpacing: ".12em" }}>
          WHAT SHE WEARS
        </span>
        <button onClick={onClose} aria-label="Close" style={closeBtn}>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.4" strokeLinecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>

      <div style={{ padding: "0.5rem 0.55rem 0" }}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={`Search ${parts.length} parts…`}
          style={search}
        />
      </div>

      <div className="kotoba-scroll" style={list}>
        {rows.length === 0 && (
          <p style={{ opacity: 0.55, fontSize: "0.72rem", textAlign: "center", padding: "0.9rem 0.3rem" }}>
            Nothing by that name.
          </p>
        )}
        {rows.map((p) => {
          const off = hidden.includes(p.id);
          return (
            <button key={p.id} className="pw-row" onClick={() => toggle(p.id)} style={row(off)}>
              <span style={pip(off)}>{off ? <Slash /> : <EyeIcon width={11} height={11} />}</span>
              <span style={{ minWidth: 0, textAlign: "left" }}>
                <span style={{ display: "block", fontWeight: 600, fontSize: "0.73rem", ...clip }}>
                  {p.name || p.id}
                </span>
                {p.name && (
                  <span style={{ display: "block", fontSize: "0.6rem", opacity: 0.5, ...clip }}>{p.id}</span>
                )}
              </span>
            </button>
          );
        })}
      </div>

      <div style={foot}>
        <span style={{ fontSize: "0.63rem", opacity: 0.62, fontWeight: 600 }}>
          {hidden.length === 0 ? "everything on" : `${hidden.length} taken off`}
        </span>
        {hidden.length > 0 && (
          <button onClick={() => onChange([])} style={undoBtn}>put it all back</button>
        )}
      </div>
    </div>
  );
}

function Slash() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19M1 1l22 22" />
    </svg>
  );
}

const clip: React.CSSProperties = { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };

const shell: React.CSSProperties = {
  position: "absolute",
  top: 0,
  left: "calc(100% + 30px)",
  width: 214,
  zIndex: 5,
  display: "flex",
  flexDirection: "column",
  border: "3px solid var(--ink)",
  borderRadius: 14,
  background: "var(--cream)",
  boxShadow: "var(--shadow-pop-sm)",
  overflow: "hidden",
};

const band: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "0.36rem 0.5rem",
  background: "var(--ink)",
  color: "#fff",
};

const closeBtn: React.CSSProperties = {
  display: "grid", placeItems: "center", width: 17, height: 17, padding: 0,
  borderRadius: 6, border: "1.6px solid rgba(255,255,255,.55)", background: "transparent", color: "#fff",
};

const search: React.CSSProperties = {
  width: "100%", boxSizing: "border-box", padding: "0.3rem 0.45rem",
  border: "2px solid var(--ink)", borderRadius: 8, background: "#fff",
  fontSize: "0.7rem", fontWeight: 600, fontFamily: "var(--font-body)",
};

const list: React.CSSProperties = {
  maxHeight: 208, overflowY: "auto", padding: "0.4rem 0.35rem",
  display: "flex", flexDirection: "column", gap: 2,
};

const row = (off: boolean): React.CSSProperties => ({
  display: "flex", alignItems: "center", gap: 7, width: "100%",
  padding: "0.26rem 0.3rem", borderRadius: 8, border: "none",
  background: off ? "rgba(255,90,60,.10)" : "transparent",
  color: "var(--ink)", cursor: "pointer", textAlign: "left",
  fontFamily: "var(--font-body)",
});

const pip = (off: boolean): React.CSSProperties => ({
  display: "grid", placeItems: "center", flex: "0 0 auto", width: 19, height: 19,
  borderRadius: 6, border: "2px solid var(--ink)",
  background: off ? "var(--coral)" : "#fff", color: off ? "#fff" : "var(--ink)",
});

const foot: React.CSSProperties = {
  display: "flex", alignItems: "center", justifyContent: "space-between",
  gap: 6, padding: "0.34rem 0.5rem", borderTop: "2px solid var(--ink)", background: "#fff",
};

const undoBtn: React.CSSProperties = {
  border: "2px solid var(--ink)", borderRadius: 7, background: "var(--cream-2)",
  padding: "0.14rem 0.4rem", fontSize: "0.62rem", fontWeight: 700,
  fontFamily: "var(--font-body)", color: "var(--ink)", cursor: "pointer",
};
