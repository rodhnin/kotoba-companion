/**
 * The receipt for an installed face, and the proof of it — the same card, because a count of files is
 * a claim about a disk while her drawing is the whole path answering at once: the serving route, pixi
 * resolving textures against the entry file, the layer that reads what a model declares, the expression
 * map. The install already succeeded by the time this mounts, so nothing here may take the step down
 * with it: without WebGL or a loadable model she is stood in for and every fact stays correct.
 *
 * It is shaped as an identity document because that is what it is. `BlankPassport` is its unissued
 * stub, so the step has something to report beside before any model has arrived.
 */
"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

import { CheckIcon, EyeIcon, FaceIcon, FolderIcon } from "@/components/icons";
import PartsWardrobe, { type Part } from "@/components/PartsWardrobe";
import { useAvatar } from "@/lib/avatar";
import type { Emotion } from "@/lib/expressions";

const Live2DCanvas = dynamic(() => import("@/components/Live2DCanvas"), { ssr: false });

export type Landed = { files: number; bytes: number; replaced: boolean };
/** What the backend counted on disk, for every visit after the install that produced `Landed`. */
export type OnDisk = { files: number; bytes: number };

const display = "var(--font-display)";
const body = "var(--font-body)";
const mono = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace';

/** A model that resolves neither way would leave an empty frame under a card claiming she is here. */
const PATIENCE = 15000;

const size = (n: number) =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)} MB`
  : n >= 1000 ? `${Math.round(n / 1000)} kB`
  : `${n} bytes`;

/** The strip along the foot, in the grammar real documents use: fixed pitch, `<` for every space, and
 *  long enough that a narrow window clips it rather than wrapping it onto a second line. */
function strip(dir: string, counted: OnDisk | null): string {
  const name = dir.toUpperCase().replace(/[^A-Z0-9]+/g, "<").slice(0, 24) || "UNNAMED";
  const tail = counted ? `${counted.files}<${size(counted.bytes).replace(/[^0-9A-Za-z]/g, "").toUpperCase()}` : "";
  return `MDL<KTB<${name}${"<".repeat(60)}${tail}`.slice(0, 68);
}

export default function FaceReceipt({
  apiUrl,
  dir,
  path,
  landed,
  onDisk,
  stamp,
}: {
  apiUrl: string;
  dir: string;
  path: string;
  landed: Landed | null;
  onDisk?: OnDisk | null;
  /** The mark that accepts the document, placed where a real one goes. It belongs to the step, not to
   *  the card: this only keeps a corner clear for it and lets the fields wrap before they reach it. */
  stamp?: React.ReactNode;
}) {
  const counted = landed ?? onDisk ?? null;
  const avatar = useAvatar(apiUrl);
  const [drawn, setDrawn] = useState<"waiting" | "live" | "stood-in">("waiting");
  // Her neutral face, and it stays neutral: a document photo is one where the bearer is looking at
  // you. Warming her to `happy` shut her eyes on the first model tried — that face is drawn with the
  // lids down on purpose, and no model can be asked in advance which of its faces do that.
  const emotion: Emotion = "neutral";
  const [wardrobe, setWardrobe] = useState<{
    parts: Part[]; hidden: string[]; setHidden: (ids: string[]) => void;
  } | null>(null);
  const [openWardrobe, setOpenWardrobe] = useState(false);

  useEffect(() => {
    if (avatar.status === "missing") setDrawn("stood-in");
  }, [avatar.status]);

  useEffect(() => {
    if (drawn !== "waiting") return;
    const t = setTimeout(() => setDrawn("stood-in"), PATIENCE);
    return () => clearTimeout(t);
  }, [drawn]);

  return (
    <div style={{ position: "relative" }}>
    <div className="fr" style={cardStyle}>
      <style>{`
        @keyframes fr-arrive {
          from { opacity: 0; transform: translateY(12px) scale(.97) }
          62%  { opacity: 1; transform: translateY(-3px) scale(1.012) }
          to   { opacity: 1; transform: none }
        }
        @keyframes fr-ring {
          from { box-shadow: var(--shadow-pop-sm), 0 0 0 0 rgba(20,199,154,.55) }
          to   { box-shadow: var(--shadow-pop-sm), 0 0 0 16px rgba(20,199,154,0) }
        }
        @keyframes fr-open {
          from { opacity: 0; transform: scale(.86) }
          to   { opacity: 1; transform: none }
        }
        .fr { animation: fr-arrive .52s cubic-bezier(.2,.85,.25,1) both, fr-ring 1.15s ease-out .16s both; }
        .fr-frame { animation: fr-open .5s cubic-bezier(.2,.85,.25,1) .12s both; }
        .fr-line { animation: rise .34s ease both; }
        @media (prefers-reduced-motion: reduce) {
          .fr, .fr-frame, .fr-line { animation: none !important }
        }
      `}</style>

      <Guilloche />
      <Band
        stamp={landed ? (landed.replaced ? "reissued" : "issued") : "on file"}
        tone="var(--mint)"
      />

      <div style={pageStyle}>
        <div style={{ flexShrink: 0, width: 92 }}>
          <div className="fr-frame" style={frameStyle}>
            {avatar.choice && drawn !== "stood-in" && (
              <Live2DCanvas
                key={avatar.choice.url}
                config={avatar.choice.config}
                modelUrl={avatar.choice.url}
                emotion={emotion}
                conversation={null}
                phase="live"
                speaking={false}
                onReady={() => setDrawn("live")}
                onError={() => setDrawn("stood-in")}
                onModel={setWardrobe}
              />
            )}
            <div
              aria-hidden
              style={{
                position: "absolute", inset: 0, display: "grid", placeItems: "center",
                color: "rgba(255,255,255,0.30)", pointerEvents: "none",
                opacity: drawn === "live" ? 0 : 1, transition: "opacity .45s ease",
              }}
            >
              <FaceIcon width={34} height={34} />
            </div>
          </div>
          <div style={captionStyle}>
            {drawn === "live" ? "drawn from those files, now"
             : drawn === "waiting" ? "opening her eyes…"
             : "she didn't draw here"}
          </div>
          {wardrobe && wardrobe.parts.length > 0 && drawn === "live" && (
            <div style={{ display: "flex", justifyContent: "center", marginTop: 6 }}>
              <button onClick={() => setOpenWardrobe((v) => !v)} style={wardrobeMark(openWardrobe)}>
                <EyeIcon width={8} height={8} style={{ display: "block" }} />
                {openWardrobe ? "close" : "wardrobe"}
              </button>
            </div>
          )}
        </div>

        <div style={{ minWidth: 0, flex: 1, paddingRight: stamp ? 78 : 0 }}>
          <Field label="Bearer" delay={0.1} icon={<FaceIcon {...pip} />}
                 value={dir} note="hers now, and yours" />
          {counted && (
            <Field label="Contents" delay={0.18} icon={<CheckIcon {...pip} />}
                   value={`${counted.files} file${counted.files === 1 ? "" : "s"} · ${size(counted.bytes)}`}
                   note={landed ? "unpacked and checked, one at a time" : "counted on your disk just now"} />
          )}
          <Field label="Residence" delay={counted ? 0.26 : 0.18} icon={<FolderIcon {...pip} />}
                 value={path || "in her models folder"} mono={!!path} last
                 note="on your machine — move it, copy it, delete it" />

          {drawn === "stood-in" && (
            <p style={standInStyle}>
              She is installed all the same — this small preview just couldn&rsquo;t get a canvas to draw
              on. Nothing above is affected.
            </p>
          )}
        </div>
      </div>

      <Strip text={strip(dir, counted)} />
      {stamp}
    </div>

    {wardrobe && openWardrobe && (
      <PartsWardrobe
        parts={wardrobe.parts}
        hidden={wardrobe.hidden}
        onChange={(next) => {
          wardrobe.setHidden(next);
          setWardrobe({ ...wardrobe, hidden: next });
        }}
        onClose={() => setOpenWardrobe(false)}
      />
    )}
    </div>
  );
}

/** The same document before one is issued — a stub, not a full page: it is what the report bubble sits
 *  beside while there is no face, and every row it does not print is a row the step does not spend. */
export function BlankPassport() {
  return (
    <div style={{ ...cardStyle, background: "rgba(255,255,255,0.5)", boxShadow: "none",
                  borderStyle: "dashed", borderColor: "rgba(33,26,46,.42)" }}>
      <Guilloche />
      <Band stamp="not issued" tone="rgba(251,241,227,.72)" muted />
      <div style={{ ...pageStyle, gap: 11, alignItems: "center", padding: "0.45rem 0.7rem 0" }}>
        <div style={{ ...frameStyle, width: 46, height: 54, borderRadius: 8, flexShrink: 0,
                      background: "rgba(33,26,46,.06)", boxShadow: "none",
                      borderStyle: "dashed", borderColor: "rgba(33,26,46,.35)" }}>
          <div aria-hidden style={{ position: "absolute", inset: 0, display: "grid",
                                    placeItems: "center", color: "rgba(33,26,46,0.22)" }}>
            <FaceIcon width={20} height={20} />
          </div>
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <span style={{ display: "block", fontFamily: display, fontWeight: 700, fontSize: "0.53rem",
                         letterSpacing: "0.15em", textTransform: "uppercase", opacity: 0.5 }}>
            Bearer
          </span>
          <span style={{ display: "block", fontFamily: display, fontWeight: 700, fontSize: "0.88rem",
                         lineHeight: 1.2, opacity: 0.5 }}>
            nobody yet
          </span>
          <span style={{ display: "block", fontFamily: body, fontSize: "0.7rem", opacity: 0.55,
                         lineHeight: 1.3 }}>
            Pick a door below — she is issued one onto your machine, never ours.
          </span>
        </div>
      </div>
      <Strip text={strip("", null)} muted />
    </div>
  );
}

/** Title and stamp both to the LEFT: the band's right end is where the step's report bubble lands, and
 *  a document with nothing printed there is one the bubble can never cover. */
function Band({ stamp, tone, muted = false }: { stamp: string; tone: string; muted?: boolean }) {
  return (
    <div style={{ position: "relative", display: "flex", alignItems: "center", gap: 9,
                  padding: "0.28rem 0.6rem",
                  background: muted ? "rgba(33,26,46,.78)" : "var(--ink)",
                  borderRadius: "10px 10px 0 0", color: "var(--cream)" }}>
      <span style={{ fontFamily: display, fontWeight: 700, fontSize: "0.58rem",
                     letterSpacing: "0.16em", textTransform: "uppercase", opacity: 0.86,
                     whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                     flexShrink: 1, minWidth: 0 }}>
        Kotoba · model registry
      </span>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 4, flexShrink: 0,
                     border: `2px solid ${tone}`, borderRadius: 999, color: tone,
                     padding: "0 0.42rem", fontFamily: display, fontWeight: 700,
                     fontSize: "0.56rem", letterSpacing: "0.12em", textTransform: "uppercase",
                     transform: "rotate(-3deg)" }}>
        {!muted && <CheckIcon width={8} height={8} />}
        {stamp}
      </span>
    </div>
  );
}

function Strip({ text, muted = false }: { text: string; muted?: boolean }) {
  return (
    <div aria-hidden
         style={{ position: "relative", margin: "7px 0.7rem 0", padding: "4px 0 0.45rem",
                  borderTop: "2px dotted rgba(33,26,46,0.28)",
                  fontFamily: mono, fontSize: "0.56rem", letterSpacing: "0.09em",
                  whiteSpace: "nowrap", overflow: "hidden", opacity: muted ? 0.25 : 0.42 }}>
      {text}
    </div>
  );
}

function Guilloche() {
  return (
    <span aria-hidden style={{
      position: "absolute", inset: 0, borderRadius: 13, pointerEvents: "none",
      background: "repeating-linear-gradient(118deg, rgba(108,76,224,.055) 0 1.5px, transparent 1.5px 8px)",
    }} />
  );
}

function Field({
  label, value, note, delay = 0, icon, mono: isMono = false, last = false, blank = false,
}: {
  label: string;
  value: string;
  note: string;
  delay?: number;
  icon?: React.ReactNode;
  mono?: boolean;
  last?: boolean;
  blank?: boolean;
}) {
  return (
    <div
      className={blank ? undefined : "fr-line"}
      style={{
        position: "relative", marginBottom: last ? 0 : 5, paddingBottom: last ? 0 : 4,
        borderBottom: last ? "none" : "2px dotted rgba(33,26,46,0.18)",
        animationDelay: `${delay}s`, opacity: blank ? 0.45 : 1,
      }}
    >
      <span style={{ display: "flex", alignItems: "center", gap: 5, fontFamily: display,
                     fontWeight: 700, fontSize: "0.53rem", letterSpacing: "0.15em",
                     textTransform: "uppercase", opacity: 0.5 }}>
        {icon && <span style={stampStyle}>{icon}</span>}
        {label}
      </span>
      <span style={{ display: "block", fontFamily: isMono ? mono : display, fontWeight: 700,
                     fontSize: isMono ? "0.7rem" : "0.9rem", lineHeight: 1.25, marginTop: 1,
                     overflowWrap: "anywhere" }}>
        {value}
      </span>
      <span style={{ display: "block", fontFamily: body, fontSize: "0.68rem", opacity: 0.55,
                     lineHeight: 1.3 }}>
        {note}
      </span>
    </div>
  );
}

/** A second stamp on the document, not a button on a screen: the passport speaks in inked lozenges
 *  and letterspaced caps, and a bordered pill with a drop shadow read as somebody else's chrome
 *  sitting on it. Tilted the other way from the one in the band, so the two read as a pair. */
const wardrobeMark = (open: boolean): React.CSSProperties => ({
  display: "inline-flex", alignItems: "center", gap: 4,
  padding: "0.1rem 0.4rem",
  border: `2px solid ${open ? "var(--ink)" : "var(--grape)"}`,
  borderRadius: 999,
  background: open ? "var(--ink)" : "transparent",
  color: open ? "var(--cream)" : "var(--grape)",
  fontFamily: display, fontWeight: 700, fontSize: "0.52rem",
  letterSpacing: "0.12em", textTransform: "uppercase",
  transform: "rotate(2deg)", cursor: "pointer", lineHeight: 1.6,
});

const cardStyle: React.CSSProperties = {
  position: "relative",
  border: "3px solid var(--ink)",
  borderRadius: 16,
  background: "linear-gradient(180deg, #ffffff 0%, #f7fffb 100%)",
  boxShadow: "var(--shadow-pop-sm)",
  padding: 0,
  overflow: "hidden",
};

const pageStyle: React.CSSProperties = {
  position: "relative",
  display: "flex",
  gap: 13,
  alignItems: "flex-start",
  padding: "0.6rem 0.7rem 0",
};

const frameStyle: React.CSSProperties = {
  position: "relative",
  width: 92,
  height: 104,
  border: "3px solid var(--ink)",
  borderRadius: 10,
  background: "radial-gradient(120% 100% at 50% 15%, #241b3a 0%, #0c0a14 70%)",
  boxShadow: "inset 0 2px 10px rgba(0,0,0,.45)",
  overflow: "hidden",
};

const captionStyle: React.CSSProperties = {
  marginTop: 4,
  textAlign: "center",
  fontFamily: display,
  fontWeight: 700,
  fontSize: "0.58rem",
  letterSpacing: "0.02em",
  opacity: 0.5,
  lineHeight: 1.2,
};

/** An inline SVG sits on the TEXT baseline, so a badge that only centres its box leaves the glyph a
 *  descender high — the same trap the step dots carry a note about. Kill the line box, block the svg,
 *  and thicken the stroke, which at ten pixels is otherwise a hair. */
const stampStyle: React.CSSProperties = {
  display: "grid", placeItems: "center", width: 17, height: 17, flexShrink: 0, lineHeight: 0,
  borderRadius: "50%", background: "var(--mint)", border: "1.5px solid rgba(33,26,46,0.85)",
  color: "#fff", boxShadow: "0 0 6px rgba(20,199,154,0.40)",
};

const pip = { width: 10, height: 10, strokeWidth: 2.8, style: { display: "block" } } as const;

const standInStyle: React.CSSProperties = {
  margin: "7px 0 0",
  fontFamily: body,
  fontSize: "0.7rem",
  lineHeight: 1.4,
  opacity: 0.68,
};
