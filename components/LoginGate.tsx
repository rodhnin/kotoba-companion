/**
 * Dev access gate screen. The password is verified server-side at /gate — this only posts the
 * attempt.
 *
 * It also owns the one failure the screen used to swallow: a password that IS right, followed by a
 * bounce straight back to this form because the browser never kept the session cookie. See
 * lib/gate-diagnostics.ts — the loop is silent otherwise, and looks exactly like a wrong password.
 */
"use client";

import { useEffect, useRef, useState } from "react";

import { EyeIcon, HeartIcon, SparkIcon, SpinnerIcon } from "@/components/icons";
import { setAuthToken } from "@/lib/api";
import { markGateAccepted, takeGateLoopSignal } from "@/lib/gate-diagnostics";
import { safeNext } from "@/lib/safe-next";

type Mood = "greet" | "wrong" | "success" | "thinking";

const LINES: Record<Mood, string[]> = {
  greet: [
    "Oh! It's you~ Say the secret word and I'll let you in.",
    "Psst — this little door is just for us. What's the password?",
    "Welcome back! I've been waiting for you~",
  ],
  thinking: ["Mmm, let me check that..."],
  wrong: ["Mmm... that's not quite it. Try once more?", "Hehe, nope~ that's not the word."],
  success: ["Yay! Come on in~"],
};

const OPEN = "/subagents/chibi-open.webp";
const CLOSED = "/subagents/chibi-closed.webp";

export default function LoginGate({ next }: { next: string }) {
  const [pw, setPw] = useState("");
  const [reveal, setReveal] = useState(false);
  const [mood, setMood] = useState<Mood>("greet");
  const [line, setLine] = useState(LINES.greet[0]);
  const [speaking, setSpeaking] = useState(true);
  const [mouthOpen, setMouthOpen] = useState(false);
  const [shake, setShake] = useState(false);
  const [entered, setEntered] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sessionDropped, setSessionDropped] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Entrance pop runs ONCE: toggling the shake animation off would otherwise replay kg-pop and "flash".
  useEffect(() => {
    const t = setTimeout(() => setEntered(true), 420);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    if (mood !== "greet") return;
    let i = 0;
    const id = setInterval(() => {
      i = (i + 1) % LINES.greet.length;
      setLine(LINES.greet[i]);
      setSpeaking(true);
    }, 4200);
    return () => clearInterval(id);
  }, [mood]);

  useEffect(() => {
    if (!speaking) {
      setMouthOpen(false);
      return;
    }
    const flap = setInterval(() => setMouthOpen((m) => !m), 300);
    const stop = setTimeout(() => setSpeaking(false), 1700);
    return () => {
      clearInterval(flap);
      clearTimeout(stop);
    };
  }, [speaking, line]);

  useEffect(() => {
    inputRef.current?.focus();
    setSessionDropped(takeGateLoopSignal());
  }, []);

  const say = (m: Mood) => {
    const pool = LINES[m];
    setMood(m);
    setLine(pool[Math.floor(Math.random() * pool.length)]);
    setSpeaking(true);
  };

  const submit = async () => {
    if (busy || !pw.trim()) return;
    setBusy(true);
    setSessionDropped(false);
    say("thinking");
    try {
      const res = await fetch("/gate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pw }),
      });
      if (res.ok) {
        // `open` means no password is configured, so the typed word is not a token — storing it would
        // make gateIsOn() lie and hand the backend a Bearer header it never asked for.
        const open = await res.json().then((d) => d?.open === true).catch(() => false);
        // The secret word IS the backend token — stash it so it survives the hard nav below.
        if (!open) setAuthToken(pw);
        markGateAccepted();
        say("success");
        // HARD navigation on purpose: the App Router caches the earlier 307 redirect to /login, so a
        // soft nav would replay it and bounce us back; a full load re-evaluates with the fresh cookie.
        // Re-checked at the sink against the real origin, not just trusted from the page's props.
        setTimeout(() => window.location.assign(safeNext(next, window.location.origin)), 850);
        return;
      }
      say("wrong");
      setShake(true);
      setTimeout(() => setShake(false), 500);
      setPw("");
      inputRef.current?.focus();
    } catch {
      say("wrong");
    } finally {
      setBusy(false);
    }
  };

  const accent =
    mood === "wrong" ? "var(--live)" : mood === "success" ? "var(--mint)" : "var(--coral)";

  return (
    <main
      style={{
        minHeight: "100dvh",
        display: "grid",
        placeItems: "center",
        background: "var(--cream)",
        position: "relative",
        overflow: "hidden",
        padding: "1.5rem",
      }}
    >
      <style>{`
        @keyframes kg-bob { 0%,100%{ transform: translateY(0) rotate(-1deg) } 50%{ transform: translateY(-10px) rotate(1deg) } }
        @keyframes kg-pop { 0%{ transform: scale(.9); opacity:0 } 100%{ transform: scale(1); opacity:1 } }
        @keyframes kg-float { 0%,100%{ transform: translateY(0) rotate(0) } 50%{ transform: translateY(-22px) rotate(8deg) } }
      `}</style>

      {/* floating idol-pop shapes */}
      <Shape style={{ top: "12%", left: "14%", background: "var(--sun)", borderRadius: 14, width: 34, height: 34, animationDelay: "0s" }} />
      <Shape style={{ top: "22%", right: "16%", background: "var(--grape)", borderRadius: "50%", width: 26, height: 26, animationDelay: ".8s" }} />
      <Shape style={{ bottom: "18%", left: "20%", background: "var(--mint)", borderRadius: "50%", width: 20, height: 20, animationDelay: "1.6s" }} />
      <Shape tri style={{ bottom: "16%", right: "18%", animationDelay: "1.1s" }} />

      <div
        style={{
          position: "relative",
          width: "min(94vw, 440px)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          animation: shake ? "shake .45s" : entered ? "none" : "kg-pop .4s ease both",
        }}
      >
        {/* chibi */}
        <img
          src={mouthOpen ? OPEN : CLOSED}
          alt="Kotoba"
          draggable={false}
          style={{
            width: 150,
            height: 150,
            objectFit: "contain",
            marginBottom: -14,
            zIndex: 2,
            animation: "kg-bob 3.4s ease-in-out infinite",
            filter: "drop-shadow(4px 6px 0 rgba(33,26,46,.18))",
            userSelect: "none",
          }}
        />

        {/* speech bubble */}
        <div
          style={{
            position: "relative",
            background: "#fff",
            border: "3px solid var(--ink)",
            borderRadius: 18,
            padding: "0.7rem 1rem",
            boxShadow: "var(--shadow-pop-sm)",
            fontFamily: "var(--font-body)",
            fontSize: "0.96rem",
            lineHeight: 1.35,
            textAlign: "center",
            maxWidth: 340,
            marginBottom: "1.1rem",
            zIndex: 1,
          }}
          key={line}
        >
          {/* up-tail */}
          <span
            style={{
              position: "absolute",
              top: -11,
              left: "50%",
              transform: "translateX(-50%) rotate(45deg)",
              width: 16,
              height: 16,
              background: "#fff",
              borderLeft: "3px solid var(--ink)",
              borderTop: "3px solid var(--ink)",
            }}
          />
          {line}
        </div>

        {/* card */}
        <div
          style={{
            width: "100%",
            background: "var(--cream-2)",
            border: "4px solid var(--ink)",
            borderRadius: 22,
            boxShadow: "var(--shadow-pop)",
            padding: "1.3rem 1.25rem 1.4rem",
            display: "flex",
            flexDirection: "column",
            gap: "0.9rem",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 9, justifyContent: "center" }}>
            <SparkIcon width={18} height={18} />
            <h1
              style={{
                fontFamily: "var(--font-display)",
                fontWeight: 700,
                fontSize: "1.15rem",
                margin: 0,
                letterSpacing: "-0.01em",
              }}
            >
              Kotoba — private
            </h1>
            <HeartIcon width={16} height={16} />
          </div>

          <div style={{ position: "relative", display: "flex", alignItems: "center" }}>
            <input
              ref={inputRef}
              value={pw}
              onChange={(e) => setPw(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              type={reveal ? "text" : "password"}
              placeholder="Secret word…"
              autoComplete="off"
              spellCheck={false}
              style={{
                width: "100%",
                border: `3px solid ${accent}`,
                borderRadius: 13,
                padding: "0.7rem 44px 0.7rem 0.85rem",
                fontFamily: "var(--font-body)",
                fontSize: "1rem",
                background: "#fff",
                color: "var(--ink)",
                outline: "none",
                transition: "border-color .25s",
              }}
            />
            <button
              type="button"
              onClick={() => setReveal((r) => !r)}
              aria-label="Show or hide"
              style={{
                position: "absolute",
                right: 8,
                border: "none",
                background: "transparent",
                color: "var(--ink)",
                opacity: 0.55,
                display: "grid",
                placeItems: "center",
                cursor: "pointer",
              }}
            >
              <EyeIcon width={19} height={19} />
            </button>
          </div>

          <button
            onClick={submit}
            disabled={busy || !pw.trim()}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
              border: "3px solid var(--ink)",
              background: busy ? "var(--cream)" : "var(--coral)",
              color: busy ? "var(--ink)" : "#fff",
              borderRadius: 14,
              padding: "0.7rem 1rem",
              fontFamily: "var(--font-display)",
              fontWeight: 700,
              fontSize: "1rem",
              boxShadow: "3px 3px 0 var(--ink)",
              cursor: busy || !pw.trim() ? "default" : "pointer",
              opacity: !pw.trim() ? 0.55 : 1,
              transition: "opacity .2s, background .2s",
            }}
          >
            {busy ? (
              <>
                <SpinnerIcon width={17} height={17} /> …
              </>
            ) : mood === "success" ? (
              "Opening~"
            ) : (
              "Let me in"
            )}
          </button>

          {sessionDropped && <SessionDroppedNotice />}

          <p
            style={{
              fontSize: "0.72rem",
              opacity: 0.5,
              textAlign: "center",
              margin: 0,
              lineHeight: 1.4,
            }}
          >
            This space is just for us — the secret word keeps everyone else out.
          </p>
        </div>
      </div>
    </main>
  );
}

/** Shown only after a password this screen already accepted — so it never reads as "wrong word". */
function SessionDroppedNotice() {
  return (
    <div
      style={{
        border: "3px solid var(--ink)",
        borderRadius: 13,
        background: "var(--sun)",
        boxShadow: "3px 3px 0 var(--ink)",
        padding: "0.6rem 0.7rem",
        fontSize: "0.76rem",
        lineHeight: 1.4,
        color: "var(--ink)",
      }}
    >
      <strong style={{ fontFamily: "var(--font-display)" }}>The word was right</strong> — your browser
      just didn't keep the session, so we ended up back here. Check that cookies aren't blocked for
      this address, and see <em>docs/self-hosting.md → &ldquo;Plain HTTP and login loops&rdquo;</em>.
    </div>
  );
}

function Shape({
  style,
  tri = false,
}: {
  style: React.CSSProperties;
  tri?: boolean;
}) {
  if (tri) {
    return (
      <span
        style={{
          position: "absolute",
          width: 0,
          height: 0,
          borderLeft: "16px solid transparent",
          borderRight: "16px solid transparent",
          borderBottom: "28px solid var(--coral)",
          animation: "kg-float 6s ease-in-out infinite",
          pointerEvents: "none",
          ...style,
        }}
      />
    );
  }
  return (
    <span
      style={{
        position: "absolute",
        animation: "kg-float 5.5s ease-in-out infinite",
        pointerEvents: "none",
        boxShadow: "2px 2px 0 rgba(33,26,46,.18)",
        ...style,
      }}
    />
  );
}
