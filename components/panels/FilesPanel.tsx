/**
 * Kotoba's file library — a real folder on disk (~/.kotoba/files), shown across turns, reconnects and
 * reloads. The disk is the truth: `filesEpoch` bumps on a `files_changed` event and the list is re-read
 * rather than merged, and a delete is optimistic only because that same re-read repairs it.
 *
 * The path is protocol and the name is content: open, delete and the raw URL round-trip a path to disk
 * byte-for-byte, so it is kept RAW — scrubbing it would orphan a hostile-named file — and the gate sits
 * on every drawn NAME instead, because an on-disk filename can carry an override that reverses it on
 * screen. How the raw URL, the iframe and the portalled viewer are wired is stated at each of them.
 */
"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import { mdComponents } from "@/lib/markdown";
import remarkGfm from "remark-gfm";

import SidePanel from "@/components/panels/SidePanel";
import { DownloadIcon, ExternalLinkIcon, FileCodeIcon, FileImageIcon, FileTextIcon, FolderIcon, SearchIcon } from "@/components/icons";
import { apiFetch, tokenUrl } from "@/lib/api";
import { useKotobaStore, type FileArtifact } from "@/lib/store";
import { scrub } from "@/lib/text-security";

const CODE_EXT = new Set(["py", "js", "ts", "tsx", "jsx", "json", "html", "css", "scss", "sh", "rb", "go", "rs", "java", "c", "cpp", "yml", "yaml", "toml"]);
const TEXT_EXT = new Set(["md", "txt", "rst", "log", "csv", "env", "pdf"]);
const IMG_EXT = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp"]);

type Category = "all" | "code" | "image" | "doc";

function extOf(path: string): string {
  const name = path.split("/").pop() || path;
  return name.includes(".") ? name.slice(name.lastIndexOf(".") + 1).toLowerCase() : "";
}

function categoryOf(f: FileArtifact): Exclude<Category, "all"> {
  const ext = extOf(f.path);
  if (f.kind === "image" || IMG_EXT.has(ext)) return "image";
  if (CODE_EXT.has(ext)) return "code";
  return "doc";
}

function fileVisual(path: string): { icon: ReactNode; accent: string } {
  const ext = extOf(path);
  if (!ext) return { icon: <FolderIcon width={16} height={16} />, accent: "var(--grape)" };
  if (CODE_EXT.has(ext)) return { icon: <FileCodeIcon width={16} height={16} />, accent: "var(--grape)" };
  if (IMG_EXT.has(ext)) return { icon: <FileImageIcon width={16} height={16} />, accent: "var(--mint)" };
  if (TEXT_EXT.has(ext)) return { icon: <FileTextIcon width={16} height={16} />, accent: "var(--coral)" };
  return { icon: <FileTextIcon width={16} height={16} />, accent: "var(--ink)" };
}

/** A real path, so an HTML file's relative assets resolve as siblings. Segments are encoded ONE at a
 *  time: a whole-path encodeURIComponent would eat the slashes. The token rides on the FIRST hit only
 *  — the backend sets a cookie and redirects token-less, so URLs and history never keep it. */
const rawUrl = (apiUrl: string, path: string) =>
  tokenUrl(`${apiUrl}/api/files/raw/${path.split("/").map(encodeURIComponent).join("/")}`);

/** The path is protocol and stays raw; the gate sits on the drawn NAME, which can carry an RLO. */
function FileRow({ file, onOpen, onNewTab, onDelete, label }: { file: FileArtifact; onOpen: () => void; onNewTab: () => void; onDelete: () => void; label?: string }) {
  const showTag = !file.seen;
  const badge = file.action === "created" ? "new" : "edited";
  const badgeBg = file.action === "created" ? "var(--mint)" : "var(--sun)";
  // Two-click delete confirm (there's no undo): first click arms, second deletes; auto-disarms after 3s.
  const [confirming, setConfirming] = useState(false);
  useEffect(() => {
    if (!confirming) return;
    const t = setTimeout(() => setConfirming(false), 3000);
    return () => clearTimeout(t);
  }, [confirming]);
  const name = scrub(label || file.path.split("/").pop() || file.path);
  // The extension is pinned outside the clipped part: the ellipsis eats the END of a name, so
  // report.html and report.pdf rendered as identical cards.
  const dot = name.lastIndexOf(".");
  const stem = dot > 0 ? name.slice(0, dot) : name;
  const ext = dot > 0 ? name.slice(dot + 1).toUpperCase() : "";
  const { icon, accent } = fileVisual(file.path);
  return (
    <div
      style={{
        display: "flex", alignItems: "center", gap: 8, width: "100%",
        background: "#fff", border: "2.5px solid var(--ink)", borderRadius: 12,
        padding: "0.45rem 0.5rem 0.45rem 0.65rem", boxShadow: "2px 2px 0 var(--ink)", animation: "rise 0.22s ease both",
      }}
    >
      <button onClick={onOpen} title="Open here" style={{ display: "flex", alignItems: "center", gap: 10, flex: 1, minWidth: 0, textAlign: "left", cursor: "pointer", background: "transparent", border: "none", padding: 0 }}>
        <span style={{ display: "grid", placeItems: "center", width: 30, height: 30, borderRadius: 9, background: "var(--cream-2)", border: "2px solid var(--ink)", color: accent, flexShrink: 0 }}>
          {icon}
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0, flex: 1 }} title={name}>
          <span style={{ fontWeight: 600, fontSize: "0.85rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 }}>{stem}</span>
          {ext && (
            <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "0.58rem", letterSpacing: "0.04em", color: "var(--ink)", background: "var(--cream-2)", border: "2px solid var(--ink)", borderRadius: 6, padding: "0 0.28rem", flexShrink: 0 }}>
              {ext}
            </span>
          )}
        </span>
      </button>
      {showTag && (
        <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "0.6rem", letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--ink)", background: badgeBg, border: "2px solid var(--ink)", borderRadius: 999, padding: "0.1rem 0.45rem", flexShrink: 0 }}>
          {badge}
        </span>
      )}
      <button
        onClick={onNewTab}
        title="Open in a new tab"
        aria-label="Open in a new tab"
        style={{ display: "grid", placeItems: "center", width: 28, height: 28, borderRadius: 8, background: "var(--cream-2)", border: "2px solid var(--ink)", color: "var(--ink)", flexShrink: 0, cursor: "pointer", boxShadow: "1.5px 1.5px 0 var(--ink)" }}
      >
        <ExternalLinkIcon width={14} height={14} />
      </button>
      <button
        onClick={() => { if (confirming) { onDelete(); setConfirming(false); } else { setConfirming(true); } }}
        title={confirming ? "Click again to delete" : "Delete file"}
        aria-label={confirming ? "Confirm delete" : "Delete file"}
        style={{ display: "grid", placeItems: "center", width: 28, height: 28, borderRadius: 8, background: confirming ? "var(--coral)" : "var(--cream-2)", border: "2px solid var(--ink)", color: confirming ? "#fff" : "var(--ink)", flexShrink: 0, cursor: "pointer", boxShadow: "1.5px 1.5px 0 var(--ink)" }}
      >
        {confirming ? (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6L9 17l-5-5" /></svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m2 0v14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V6" /><path d="M10 11v6M14 11v6" /></svg>
        )}
      </button>
    </div>
  );
}

function FolderRow({ name, badge, onOpen }: { name: string; badge?: "new" | "edited"; onOpen: () => void }) {
  const badgeBg = badge === "edited" ? "var(--sun)" : "var(--mint)";
  return (
    <button
      onClick={onOpen}
      title="Open folder"
      style={{
        display: "flex", alignItems: "center", gap: 10, width: "100%", textAlign: "left", cursor: "pointer",
        background: "var(--cream-2)", border: "2.5px solid var(--ink)", borderRadius: 12,
        padding: "0.5rem 0.65rem", boxShadow: "2px 2px 0 var(--ink)", animation: "rise 0.22s ease both",
      }}
    >
      <span style={{ display: "grid", placeItems: "center", width: 30, height: 30, borderRadius: 9, background: "var(--sun)", border: "2px solid var(--ink)", color: "var(--ink)", flexShrink: 0 }}>
        <FolderIcon width={16} height={16} />
      </span>
      <span style={{ fontWeight: 700, fontSize: "0.85rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, fontFamily: "var(--font-display)" }}>{scrub(name)}</span>
      {badge && (
        <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "0.6rem", letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--ink)", background: badgeBg, border: "2px solid var(--ink)", borderRadius: 999, padding: "0.1rem 0.45rem", flexShrink: 0 }}>
          {badge}
        </span>
      )}
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.5, flexShrink: 0 }}><path d="M9 6l6 6-6 6" /></svg>
    </button>
  );
}

const crumbBtn: React.CSSProperties = {
  border: "none", background: "transparent", color: "var(--grape)", cursor: "pointer",
  fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "0.78rem", padding: 0,
};

// Hoisted, not written into the JSX: the renderer memoises on this object, and a literal is a new key
// every render — the anchors would be rebuilt under the cursor.
const REPORT_LINK: React.CSSProperties = { color: "var(--grape)", fontWeight: 600 };

/** Everything this viewer DRAWS is scrubbed — the "Code" view is the Trojan Source case exactly — and
 *  it is portalled to <body>, because a fixed overlay inside a transformed ancestor is trapped. */
function FileViewer({ path, apiUrl, onClose }: { path: string; apiUrl: string; onClose: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState(false);
  const name = scrub(path.split("/").pop() || path);
  const ext = name.includes(".") ? name.slice(name.lastIndexOf(".") + 1).toLowerCase() : "";
  const isHtml = ext === "html" || ext === "htm";
  const isMarkdown = ext === "md" || ext === "markdown";
  const isImage = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico"].includes(ext);
  const [showSource, setShowSource] = useState(!isHtml && !isMarkdown);
  const [mounted, setMounted] = useState(false);
  const viewUrl = rawUrl(apiUrl, path);

  useEffect(() => setMounted(true), []);  // portal target only exists on the client

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (isImage) return;  // images render via <img src={viewUrl}>, no text fetch
    let alive = true;
    apiFetch(`${apiUrl}/api/files/open?path=${encodeURIComponent(path)}`)
      .then((r) => (r.ok ? r.text() : Promise.reject(r.status)))
      .then((t) => alive && setText(scrub(t, { newlines: true })))
      .catch(() => alive && setErr(true));
    return () => {
      alive = false;
    };
  }, [apiUrl, path, isImage]);

  const btn = (bg: string): React.CSSProperties => ({
    display: "flex", alignItems: "center", gap: 5, border: "2.5px solid var(--ink)", background: bg,
    color: "var(--ink)", borderRadius: 9, padding: "0.2rem 0.55rem", fontFamily: "var(--font-display)",
    fontWeight: 600, fontSize: "0.74rem", boxShadow: "2px 2px 0 var(--ink)", textDecoration: "none", cursor: "pointer",
  });

  if (!mounted) return null;
  return createPortal(
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 9999, display: "grid", placeItems: "center", background: "rgba(20,14,28,0.55)", backdropFilter: "blur(5px)", animation: "rise 0.2s ease both", padding: "2.5vh 2vw" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "min(92vw, 900px)", height: "min(88vh, 760px)", display: "flex", flexDirection: "column", background: "var(--cream)", border: "4px solid var(--ink)", borderRadius: 18, boxShadow: "var(--shadow-pop)", overflow: "hidden" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "0.6rem 0.75rem", borderBottom: "3px solid var(--ink)", background: "#fff", flexShrink: 0 }}>
          <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "0.92rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</span>
          <span style={{ display: "flex", gap: 6, flexShrink: 0 }}>
            {(isHtml || isMarkdown) && (
              <button onClick={() => setShowSource((s) => !s)} style={btn(showSource ? "var(--sun)" : "var(--cream-2)")}>
                {showSource ? "Preview" : "Code"}
              </button>
            )}
            <a href={viewUrl} target="_blank" rel="noopener noreferrer" style={btn("var(--grape)")}>
              <ExternalLinkIcon width={13} height={13} /> New tab
            </a>
            <a href={viewUrl} download={name} style={btn("var(--mint)")}>
              <DownloadIcon width={13} height={13} /> Save
            </a>
            <button onClick={onClose} aria-label="Close" style={{ border: "2.5px solid var(--ink)", background: "var(--cream)", borderRadius: 9, width: 28, height: 28, display: "grid", placeItems: "center", padding: 0, boxShadow: "2px 2px 0 var(--ink)", cursor: "pointer" }}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" strokeWidth="3.2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
            </button>
          </span>
        </div>
        {isImage ? (
          <div style={{ flex: 1, display: "grid", placeItems: "center", overflow: "auto", background: "var(--cream-2)", padding: "0.85rem" }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={viewUrl} alt={name} style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain", borderRadius: 8, border: "2px solid var(--ink)" }} />
          </div>
        ) : err ? (
          <div style={{ flex: 1, display: "grid", placeItems: "center", padding: "1rem", textAlign: "center", opacity: 0.7 }}>
            Couldn&apos;t open this one — it may have been a binary file or it&apos;s no longer around.
          </div>
        ) : text === null ? (
          <div style={{ flex: 1, display: "grid", placeItems: "center", opacity: 0.6 }}>Opening…</div>
        ) : isHtml && !showSource ? (
          // Real URL (not srcDoc) so relative assets resolve. allow-same-origin is intentionally absent:
          // without it the iframe is a unique-null origin and cannot read window.parent.sessionStorage.
          <iframe
            title={name}
            src={viewUrl}
            sandbox="allow-scripts"
            style={{ flex: 1, width: "100%", border: "none", background: "#fff" }}
          />
        ) : isMarkdown && !showSource ? (
          // react-markdown is XSS-safe by default — it does NOT emit raw HTML.
          <div className="md-view kotoba-scroll" style={{ flex: 1, overflow: "auto", padding: "1.4rem 1.6rem", color: "var(--ink)", fontFamily: "var(--font-body)", fontSize: "0.9rem", lineHeight: 1.6 }}>
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={mdComponents(REPORT_LINK)}
            >
              {text}
            </ReactMarkdown>
          </div>
        ) : (
          <pre style={{ flex: 1, margin: 0, padding: "0.85rem", overflow: "auto", fontFamily: "ui-monospace, monospace", fontSize: "0.8rem", lineHeight: 1.5, color: "var(--ink)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            {text}
          </pre>
        )}
      </div>
    </div>,
    document.body,
  );
}

const CHIPS: { id: Category; label: string }[] = [
  { id: "all", label: "All" },
  { id: "code", label: "Code" },
  { id: "image", label: "Images" },
  { id: "doc", label: "Docs" },
];

export default function FilesPanel({ open, onClose, apiUrl }: { open: boolean; onClose: () => void; apiUrl: string }) {
  const files = useKotobaStore((s) => s.files);
  const setFiles = useKotobaStore((s) => s.setFiles);
  const markFileSeen = useKotobaStore((s) => s.markFileSeen);
  const filesEpoch = useKotobaStore((s) => s.filesEpoch);
  const bumpFilesEpoch = useKotobaStore((s) => s.bumpFilesEpoch);
  const [viewing, setViewing] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [cat, setCat] = useState<Category>("all");
  const [cwd, setCwd] = useState("");  // current folder; "" = root

  const open1 = (path: string) => {
    markFileSeen(path);
    apiFetch(`${apiUrl}/api/files/seen`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }).catch(() => {});
  };

  const del1 = (path: string) => {
    setFiles(files.filter((f) => f.path !== path));
    if (viewing === path) setViewing(null);
    apiFetch(`${apiUrl}/api/files/${path.split("/").map(encodeURIComponent).join("/")}`, { method: "DELETE" })
      .catch(() => {})
      .finally(() => bumpFilesEpoch());
  };

  useEffect(() => {
    if (!open) return;
    let alive = true;
    apiFetch(`${apiUrl}/api/files`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d: { files?: { path: string; kind?: string; action?: string; seen?: boolean; updated_at?: number }[] }) => {
        if (!alive || !d.files) return;
        setFiles(
          d.files.map((f) => ({
            path: f.path,
            action: f.action === "edited" ? "edited" : "created",
            kind: f.kind === "image" ? "image" : "text",
            seen: !!f.seen,
            updatedAt: f.updated_at ? f.updated_at * 1000 : undefined, // disk epoch seconds → ms
          })),
        );
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [open, apiUrl, setFiles, filesEpoch]);  // filesEpoch bumps on a files_changed event → re-read the disk

  const searching = query.trim().length > 0;

  const { folders, folderBadges, shown } = useMemo(() => {
    const byCat = files.filter((f) => (cat === "all" ? true : categoryOf(f) === cat));
    if (searching) {
      const q = query.trim().toLowerCase();
      return {
        folders: [] as string[],
        folderBadges: {} as Record<string, "new" | "edited">,
        shown: byCat.filter((f) => f.path.toLowerCase().includes(q)).sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0)),
      };
    }
    const prefix = cwd ? cwd + "/" : "";
    const folderSet = new Set<string>();
    // A folder badges if it CONTAINS anything unseen at ANY depth ("new" wins over "edited").
    const folderTag: Record<string, "new" | "edited"> = {};
    const here: FileArtifact[] = [];
    for (const f of byCat) {
      if (prefix && !f.path.startsWith(prefix)) continue;
      const rel = f.path.slice(prefix.length);
      const slash = rel.indexOf("/");
      if (slash === -1) here.push(f);
      else {
        const sub = rel.slice(0, slash);
        folderSet.add(sub);
        if (!f.seen) {
          const tag = f.action === "edited" ? "edited" : "new";
          if (folderTag[sub] !== "new") folderTag[sub] = tag;
        }
      }
    }
    return {
      folders: [...folderSet].sort((a, b) => a.localeCompare(b)),
      folderBadges: folderTag,
      shown: here.sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0)),
    };
  }, [files, query, cat, cwd, searching]);

  useEffect(() => {
    if (!open) setCwd("");
  }, [open]);

  const crumbs = cwd ? cwd.split("/") : [];

  return (
    <SidePanel open={open} title="Files" accent="var(--grape)" icon={<FolderIcon width={17} height={17} />} onClose={onClose}>
      {files.length === 0 ? (
        <div style={{ margin: "auto", display: "flex", flexDirection: "column", alignItems: "center", gap: "0.9rem", padding: "1rem", textAlign: "center" }}>
          <span style={{ display: "grid", placeItems: "center", width: 64, height: 64, borderRadius: "50%", background: "var(--cream-2)", border: "3px solid var(--ink)", color: "var(--grape)", boxShadow: "var(--shadow-pop-sm)" }}>
            <FolderIcon width={28} height={28} />
          </span>
          <p style={{ opacity: 0.62, fontSize: "0.88rem", lineHeight: 1.5, maxWidth: 200, fontWeight: 500 }}>
            Files I create or edit while working live here — they stay, so you can always find them.
          </p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
          <div style={{ position: "relative", display: "flex", alignItems: "center" }}>
            <span style={{ position: "absolute", left: 9, display: "grid", placeItems: "center", color: "var(--ink)", opacity: 0.5 }}>
              <SearchIcon width={15} height={15} />
            </span>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search files…"
              style={{ width: "100%", border: "2.5px solid var(--ink)", borderRadius: 11, padding: "0.4rem 0.6rem 0.4rem 1.9rem", fontFamily: "var(--font-body)", fontSize: "0.84rem", background: "#fff", color: "var(--ink)", outline: "none", boxShadow: "2px 2px 0 var(--ink)" }}
            />
          </div>
          {!searching && cwd && (
            <div style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap", fontSize: "0.78rem", fontWeight: 600 }}>
              <button onClick={() => setCwd("")} style={crumbBtn}>Files</button>
              {crumbs.map((c, i) => (
                <span key={i} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ opacity: 0.4 }}>/</span>
                  <button onClick={() => setCwd(crumbs.slice(0, i + 1).join("/"))} style={crumbBtn}>{scrub(c)}</button>
                </span>
              ))}
            </div>
          )}
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {CHIPS.map((c) => {
              const on = cat === c.id;
              return (
                <button
                  key={c.id}
                  onClick={() => setCat(c.id)}
                  style={{
                    fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "0.72rem",
                    border: "2px solid var(--ink)", borderRadius: 999, padding: "0.18rem 0.6rem", cursor: "pointer",
                    background: on ? "var(--grape)" : "#fff", color: on ? "#fff" : "var(--ink)",
                    boxShadow: on ? "none" : "1.5px 1.5px 0 var(--ink)",
                  }}
                >
                  {c.label}
                </button>
              );
            })}
          </div>
          {!searching && folders.map((name) => (
            <FolderRow key={`d:${name}`} name={name} badge={folderBadges[name]} onOpen={() => setCwd(cwd ? `${cwd}/${name}` : name)} />
          ))}
          {folders.length === 0 && shown.length === 0 ? (
            <p style={{ opacity: 0.55, fontSize: "0.82rem", textAlign: "center", padding: "0.6rem 0" }}>
              {searching ? "No files match that." : "This folder is empty."}
            </p>
          ) : (
            shown.map((f) => (
              <FileRow key={f.path} file={f} label={searching ? f.path : undefined} onOpen={() => { open1(f.path); setViewing(f.path); }} onNewTab={() => { open1(f.path); window.open(rawUrl(apiUrl, f.path), "_blank", "noopener,noreferrer"); }} onDelete={() => del1(f.path)} />
            ))
          )}
        </div>
      )}
      {viewing && (
        <FileViewer path={viewing} apiUrl={apiUrl} onClose={() => setViewing(null)} />
      )}
    </SidePanel>
  );
}
