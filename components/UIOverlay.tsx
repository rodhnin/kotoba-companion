// Meeting-style call dock. Idol-pop sticker buttons (SVG, no emojis). Panels orbit the camera;
// Mic is a REAL mute toggle (separate from Leave). Buttons pulse when there's something to show.
// The status line's words and tone come from lib/call-status.ts — one decider for every claim
// the shell makes about the call, so a fatal voice loss can never leave "just talk" up.
"use client";

import { ChatIcon, FolderIcon, MicIcon, MicOffIcon, PhoneEndIcon, ReportIcon, SettingsIcon, SpinnerIcon, TerminalIcon } from "@/components/icons";
import { callStatusLine, type CallPhase } from "@/lib/call-status";
import type { VoiceLost } from "@/lib/voice-errors";

function ControlButton({
  onClick,
  bg,
  color = "var(--ink)",
  label,
  active,
  pulse,
  dot,
  disabled,
  children,
}: {
  onClick?: () => void;
  bg: string;
  color?: string;
  label: string;
  active?: boolean;
  pulse?: boolean;
  dot?: string; // accent color of a notification dot, if any
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={disabled ? undefined : onClick}
      aria-label={label}
      aria-pressed={active}
      title={label}
      style={{
        position: "relative",
        width: 56,
        height: 56,
        borderRadius: 18,
        border: "3px solid var(--ink)",
        background: bg,
        color,
        opacity: disabled ? 0.55 : 1,
        cursor: disabled ? "default" : "pointer",
        boxShadow: active ? "2px 2px 0 var(--ink)" : "var(--shadow-pop-sm)",
        transform: active ? "translate(1px,1px)" : "none",
        display: "grid",
        placeItems: "center",
        transition: "transform 120ms ease, box-shadow 120ms ease, background 160ms ease",
        animation: pulse ? "pulse-live 1.1s ease-in-out infinite" : "none",
      }}
    >
      {children}
      {dot && (
        <span
          style={{
            position: "absolute",
            top: -4,
            right: -4,
            width: 14,
            height: 14,
            borderRadius: "50%",
            background: dot,
            border: "2.5px solid var(--ink)",
          }}
        />
      )}
    </button>
  );
}

export default function UIOverlay({
  phase,
  working,
  isMuted,
  micBlocked = false,
  voiceLost = null,
  onStartCall,
  onToggleMute,
  onLeave,
  onToggleChat,
  chatOpen,
  onToggleTerminal,
  terminalOpen,
  hasLogs,
  onToggleFiles,
  filesOpen,
  hasFiles,
  onToggleReport,
  reportOpen,
  hasReport,
  onToggleSettings,
  settingsOpen,
}: {
  phase: CallPhase;
  working: boolean;
  isMuted: boolean;
  micBlocked?: boolean;
  voiceLost?: VoiceLost | null;
  onStartCall: () => void;
  onToggleMute: () => void;
  onLeave: () => void;
  onToggleChat: () => void;
  chatOpen: boolean;
  onToggleTerminal: () => void;
  terminalOpen: boolean;
  hasLogs: boolean;
  onToggleFiles: () => void;
  filesOpen: boolean;
  hasFiles: boolean;
  onToggleReport: () => void;
  reportOpen: boolean;
  hasReport: boolean;
  onToggleSettings: () => void;
  settingsOpen: boolean;
}) {
  const status = callStatusLine({ phase, working, isMuted, micBlocked, voiceLost });

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "0.85rem" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontFamily: "var(--font-display)",
          fontWeight: 600,
          fontSize: "0.88rem",
          color: status.tone === "calm" ? "var(--ink)" : "#fff",
          background: status.tone === "working" ? "var(--grape)" : status.tone === "alert" ? "var(--live)" : "var(--cream)",
          border: "3px solid var(--ink)",
          borderRadius: 999,
          padding: "0.32rem 1.1rem",
          boxShadow: "var(--shadow-pop-sm)",
          transition: "background 200ms ease",
        }}
      >
        {working && <SpinnerIcon width={15} height={15} />}
        {status.text}
      </div>

      {phase === "offline" ? (
        <div style={{ display: "flex", gap: "0.7rem", alignItems: "center" }}>
          <ControlButton bg="var(--sun)" label="Call Kotoba" onClick={onStartCall}>
            <MicOffIcon />
          </ControlButton>
          {/* Settings is always reachable, even before a call starts (configure her, then call). */}
          <ControlButton bg={settingsOpen ? "var(--ink)" : "#fff"} color={settingsOpen ? "#fff" : "var(--ink)"} label="Settings" active={settingsOpen} onClick={onToggleSettings}>
            <SettingsIcon />
          </ControlButton>
        </div>
      ) : (
        <div style={{ display: "flex", gap: "0.7rem", alignItems: "center" }}>
          <ControlButton bg={terminalOpen ? "var(--ink)" : "#fff"} color={terminalOpen ? "#fff" : "var(--ink)"} label="Toggle terminal" active={terminalOpen} pulse={working && !terminalOpen} dot={!terminalOpen && hasLogs ? "var(--mint)" : undefined} onClick={onToggleTerminal}>
            <TerminalIcon />
          </ControlButton>
          <ControlButton bg={filesOpen ? "var(--grape)" : "#fff"} color={filesOpen ? "#fff" : "var(--ink)"} label="Toggle files" active={filesOpen} dot={!filesOpen && hasFiles ? "var(--sun)" : undefined} onClick={onToggleFiles}>
            <FolderIcon />
          </ControlButton>
          {hasReport && (
            <ControlButton bg={reportOpen ? "var(--coral)" : "#fff"} color={reportOpen ? "#fff" : "var(--ink)"} label="Toggle report" active={reportOpen} pulse={!reportOpen} dot={!reportOpen ? "var(--coral)" : undefined} onClick={onToggleReport}>
              <ReportIcon />
            </ControlButton>
          )}
          <ControlButton bg={chatOpen ? "var(--grape)" : "#fff"} color={chatOpen ? "#fff" : "var(--ink)"} label="Toggle transcript" active={chatOpen} onClick={onToggleChat}>
            <ChatIcon />
          </ControlButton>
          <ControlButton bg={settingsOpen ? "var(--ink)" : "#fff"} color={settingsOpen ? "#fff" : "var(--ink)"} label="Settings" active={settingsOpen} onClick={onToggleSettings}>
            <SettingsIcon />
          </ControlButton>

          {/* divider */}
          <span style={{ width: 3, height: 34, background: "var(--ink)", opacity: 0.18, borderRadius: 2, margin: "0 0.15rem" }} />

          {/* mic = REAL mute (session stays live); separate Leave hangs up */}
          {phase === "ringing" ? (
            <ControlButton bg="var(--sun)" label="Connecting" disabled>
              <SpinnerIcon />
            </ControlButton>
          ) : (
            <ControlButton
              bg={isMuted ? "var(--cream-2)" : "var(--mint)"}
              color="var(--ink)"
              label={isMuted ? "Unmute microphone" : "Mute microphone"}
              active={isMuted}
              onClick={onToggleMute}
            >
              {isMuted ? <MicOffIcon /> : <MicIcon />}
            </ControlButton>
          )}

          <ControlButton bg="var(--live)" color="#fff" label="Leave call" onClick={onLeave}>
            <PhoneEndIcon />
          </ControlButton>
        </div>
      )}
    </div>
  );
}
