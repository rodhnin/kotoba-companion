// Hand-picked line icons (no emojis). Inherit color via currentColor, stroke-based for a clean look.
import type { SVGProps } from "react";

const base = {
  width: 24,
  height: 24,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export function MicIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0" />
      <line x1="12" y1="18" x2="12" y2="22" />
      <line x1="8" y1="22" x2="16" y2="22" />
    </svg>
  );
}

export function MicOffIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <line x1="3" y1="3" x2="21" y2="21" />
      <path d="M9 9v3a3 3 0 0 0 5 2.5" />
      <path d="M15 9.3V5a3 3 0 0 0-6 0v1" />
      <path d="M5 11a7 7 0 0 0 11 5.5" />
      <line x1="12" y1="18" x2="12" y2="22" />
      <line x1="8" y1="22" x2="16" y2="22" />
    </svg>
  );
}

export function PhoneEndIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <path d="M3 11c5-4 13-4 18 0l-2.5 2.5c-1-.8-2.2-1.3-3.5-1.6V15c-2-.4-4-.4-6 0v-3.1c-1.3.3-2.5.8-3.5 1.6L3 11z" />
    </svg>
  );
}

export function SparkIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18" />
    </svg>
  );
}

export function SpinnerIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p} style={{ animation: "spin 0.8s linear infinite", ...(p.style || {}) }}>
      <path d="M12 3a9 9 0 1 0 9 9" />
    </svg>
  );
}

export function ChatIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <path d="M4 5h16v11H9l-4 4V5z" />
      <line x1="8" y1="9.5" x2="16" y2="9.5" />
      <line x1="8" y1="12.5" x2="13" y2="12.5" />
    </svg>
  );
}

export function HeartIcon(p: SVGProps<SVGSVGElement>) {
  // Symmetric about x=12, ink bbox (3,4)-(21,20) → centred at (12,12). The previous hand-drawn
  // path had its notch at x=10 and a flat lopsided tip, so it sat visibly low-left in round badges.
  return (
    <svg {...base} {...p} fill="currentColor" stroke="none">
      <path d="M12 20C9.1 17.6 3 13.8 3 9.2 3 6.1 5.1 4 7.7 4c1.9 0 3.6 1.1 4.3 2.8C12.7 5.1 14.4 4 16.3 4 18.9 4 21 6.1 21 9.2c0 4.6-6.1 8.4-9 10.8z" />
    </svg>
  );
}

export function FaceIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M8.5 14.5a5 5 0 0 0 7 0" />
      <line x1="9" y1="9.5" x2="9" y2="10" />
      <line x1="15" y1="9.5" x2="15" y2="10" />
    </svg>
  );
}

export function GlobeIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18" />
    </svg>
  );
}

export function BrainIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      {/* ink spans y 4.5–19; +0.25 recentres it on (12,12) */}
      <g transform="translate(0 0.25)">
        <path d="M9.5 4.5a2.5 2.5 0 0 0-2.5 2.5 2.5 2.5 0 0 0-1 4.8A2.5 2.5 0 0 0 7 16.5a2.5 2.5 0 0 0 2.5 2.5V4.5z" />
        <path d="M14.5 4.5A2.5 2.5 0 0 1 17 7a2.5 2.5 0 0 1 1 4.8 2.5 2.5 0 0 1-1 4.7 2.5 2.5 0 0 1-2.5 2.5V4.5z" />
      </g>
    </svg>
  );
}

export function TerminalIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <rect x="3" y="4" width="18" height="16" rx="2.5" />
      <path d="M7 9l3 3-3 3M13 15h4" />
    </svg>
  );
}

export function FolderIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
    </svg>
  );
}

export function FileTextIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <path d="M6 3h8l4 4v14a0 0 0 0 1 0 0H6a0 0 0 0 1 0 0V3z" />
      <path d="M14 3v4h4" />
      <line x1="8.5" y1="12" x2="15.5" y2="12" />
      <line x1="8.5" y1="15" x2="13" y2="15" />
    </svg>
  );
}

export function FileCodeIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <path d="M6 3h8l4 4v14H6V3z" />
      <path d="M14 3v4h4" />
      <path d="M10.5 12 9 13.8l1.5 1.8M13.5 12 15 13.8l-1.5 1.8" />
    </svg>
  );
}

export function FileImageIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <rect x="4" y="4" width="16" height="16" rx="2.5" />
      <circle cx="9" cy="9.5" r="1.6" />
      <path d="M5 17l4.5-4.5L13 16l2.5-2.5L19 17" />
    </svg>
  );
}

export function SettingsIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

export function ReportIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <path d="M6 3h8l4 4v14H6V3z" />
      <path d="M14 3v4h4" />
      <line x1="9" y1="12" x2="15" y2="12" />
      <line x1="9" y1="15.5" x2="13" y2="15.5" />
    </svg>
  );
}

export function KeyboardIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <rect x="2.5" y="6" width="19" height="12" rx="2.5" />
      <path d="M6 9.5h.01M9.5 9.5h.01M13 9.5h.01M16.5 9.5h.01M7.5 13h9" />
    </svg>
  );
}

export function DownloadIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <path d="M12 3v12m0 0 4-4m-4 4-4-4" />
      <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
    </svg>
  );
}

export function CloseCircleIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M9 9l6 6M15 9l-6 6" />
    </svg>
  );
}

export function ArchiveIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <rect x="3" y="4" width="18" height="4.5" rx="1.5" />
      <path d="M5 8.5V19a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19V8.5" />
      <path d="M10 12.5h4" />
    </svg>
  );
}

export function EyeIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

export function CheckIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p} strokeWidth={3}>
      <path d="M4 12.75l5 5L20 6.25" />
    </svg>
  );
}

export function ExternalLinkIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <path d="M15 3h6v6" />
      <path d="M10 14 21 3" />
      <path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5" />
    </svg>
  );
}

export function SearchIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <circle cx="11" cy="11" r="7" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

export function ListCheckIcon(p: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...p}>
      <path d="M3.5 5.5l1.5 1.5 2.5-2.5" />
      <path d="M3.5 11.5l1.5 1.5 2.5-2.5" />
      <path d="M3.5 17.5l1.5 1.5 2.5-2.5" />
      <line x1="9.5" y1="6" x2="21" y2="6" />
      <line x1="9.5" y1="12" x2="21" y2="12" />
      <line x1="9.5" y1="18" x2="17" y2="18" />
    </svg>
  );
}
