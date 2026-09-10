/**
 * Settings accordion — reads /api/settings and applies changes through small endpoints. `api()` is the
 * one writer: it surfaces a refusal in the error banner and re-reads on success, so the backend's
 * normalised value is what the panel draws.
 *
 * Four rules, each stated again at the line that keeps it: there are two kinds of write and they
 * differ in who rolls back, the brain and the model are ONE decision rather than two unrelated keys,
 * this panel's values are server-side keys and so are kept raw and scrubbed at the draw, and Sign out
 * and Reconfigure are full navigations rather than router pushes.
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import { mdComponents } from "@/lib/markdown";
import remarkGfm from "remark-gfm";

import { SettingsIcon } from "@/components/icons";
import { apiFetch, gateIsOn, signOut } from "@/lib/api";
import { SETUP_FACE_URL, SETUP_INVITE_URL } from "@/lib/first-run";
import { refusal } from "@/lib/setup-writes";
import { scrub } from "@/lib/text-security";

type Runtime = {
  provider: string;
  base_url: string;
  model: string;
  work_model: string;
  code_model: string;
  research_model: string;
  utility_model: string;
  reasoning_effort: string;
  expressive: boolean;
  elevenlabs_agent_id: string;
  voice_mode: string;
  tts_engine: string;
  sandbox: string;
  work_timeout: number;
  work_max_iter: number;
  work_max_tool_calls: number;
  work_fail_limit: number;
};

type ModelRow = { id: string; note: string; hint: string; context: number };
type LLMProvider = {
  id: string;
  label: string;
  default_base_url: string;
  key_hint: string;
  has_key: boolean;
  key_env: string;
  models: ModelRow[];
  code_models: ModelRow[];
};

/** A key in the environment is a key she is using. Saying only "saved in the app" left a working
 *  install looking unconfigured, and sent people off to make a second key. */
function keyNote(p?: { has_key: boolean; key_env: string }): string {
  if (p?.has_key) return " (saved — type to replace)";
  if (p?.key_env) return ` (from $${p.key_env} — type to save one here instead)`;
  return "";
}

function keyPlaceholder(p?: { has_key: boolean; key_env: string }): string {
  if (p?.has_key) return "•••••••• stored";
  if (p?.key_env) return `•••••••• from $${p.key_env}`;
  return "Paste your key (stored encrypted)";
}

type InstalledModel = { dir: string; entry: string };

type Settings = {
  personality: { name: string; language: string; voice_id: string };
  avatar: { installed: InstalledModel[]; selected: InstalledModel | null; models_dir: string };
  llm: { provider: string; base_url: string; providers: LLMProvider[] };
  security: { trust: string; sandbox: string; approvals: { pattern: string; scope: string }[] };
  runtime: Runtime;
  browser_cdp: string;
  mcp_servers: { name: string; tools: number; connected: boolean }[];
  pending_mcp: { name: string; reason: string; kind: "token" | "oauth"; description: string }[];
  plugins: { name: string; source: string; tools: string[]; enabled: boolean }[];
  skills: { name: string; description: string }[];
  toolsets: { name: string; tools: number; enabled: boolean }[];
  memory: { topics: { slug: string; count: number }[]; recent: string[] };
  keys: string[];
  reminders: { id: string; message: string; due_at: string; recurring: string | null }[];
};

const ease = "cubic-bezier(0.22, 1, 0.36, 1)";

const REASONING = ["off", "minimal", "low", "medium", "high", "xhigh", "max"];
const SANDBOXES = ["local", "docker", "none"];

const EL_GUIDE_MD = `# Connect ElevenLabs (voice)

There are two voice modes, and they need different things from you. Check **Voice mode** above.

## Local (the default)

Your browser talks to this backend, which talks to ElevenLabs. Nothing has to be reachable from the internet, so this is the one that works on a laptop.

All it needs is an **ElevenLabs API key**, and the place to put one is **Brain → Run setup again** — the *API key* box in Brain saves your LLM provider's key, not this one. Kotoba stores it encrypted in its database and uses it for both listening and speaking. Pick your voice under **Personality → Voice ID**. You do **not** need an agent, and the **ElevenLabs Agent ID** field above is not used.

## ElevenLabs agent

The agent runs in *your* ElevenLabs account and calls back into this backend, so it needs a **public URL** — a tunnel or a deployed server. Everything below is for this mode only.

## 1) Create the agent
- Go to **elevenlabs.io → Conversational AI → Agents → Create agent**.
- Name it (e.g. "Kotoba").

## 2) Point it at Kotoba (Custom LLM)
In the agent's **LLM** settings, choose **Custom LLM** and set:
- **Server URL:** \`https://<your-kotoba-backend>/v1/chat/completions\`
- **API Key:** your \`KOTOBA_API_KEY\` (the bearer token from the backend's \`.env\`)

This is what makes the agent use Kotoba's brain (tools, memory, emotions) instead of a plain model.

## 3) Pick a voice → copy the Voice ID
- Choose the voice you want for the agent.
- Copy its **Voice ID** and paste it into **Personality → Voice ID** above.
- For **expressive audio tags** (\`[excited]\`, \`[whispers]\`…) to be *performed* rather than read aloud, use an **eleven_v3** voice/model. Otherwise turn off "Expressive voice" in **Brain**.

## 4) Copy the Agent ID
- Copy the agent's **Agent ID** (looks like \`agent_xxxxxxxx\`).
- Paste it into the **ElevenLabs Agent ID** field above.

## 5) Start a call
Close Settings and tap the mic — Kotoba connects to your agent.

> If the call won't connect: re-check the Agent ID, and that the agent's Custom LLM **URL + API key** match this backend.`;

export default function SettingsPanel({
  open,
  apiUrl,
  onClose,
}: {
  open: boolean;
  apiUrl: string;
  onClose: () => void;
}) {
  const [s, setS] = useState<Settings | null>(null);
  const [name, setName] = useState("");
  const [language, setLanguage] = useState("auto");
  const [voiceId, setVoiceId] = useState("");
  const [elGuide, setElGuide] = useState(false);
  const [openSections, setOpenSections] = useState<Set<string>>(new Set(["Personality"]));
  const [error, setError] = useState("");
  const [apiKeyDraft, setApiKeyDraft] = useState("");
  const [llmTest, setLlmTest] = useState("");
  const personalityDirty = useRef(false);

  const toggleSection = useCallback((title: string) => {
    setOpenSections((prev) => {
      const next = new Set(prev);
      next.has(title) ? next.delete(title) : next.add(title);
      return next;
    });
  }, []);

  const load = useCallback(() => {
    apiFetch(`${apiUrl}/api/settings`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d: Settings) => {
        setS(d);
        if (personalityDirty.current) return; // never overwrite a draft somebody is still typing
        setName(d.personality.name);
        setLanguage(d.personality.language);
        setVoiceId(d.personality.voice_id || "");
      })
      .catch(() => setS(null));
  }, [apiUrl]);

  useEffect(() => {
    if (open) {
      personalityDirty.current = false;
      load();
    }
  }, [open, load]);

  const api = useCallback(
    async (path: string, method: string, body?: unknown): Promise<boolean> => {
      try {
        const r = await apiFetch(`${apiUrl}${path}`, {
          method,
          headers: { "Content-Type": "application/json" },
          body: body ? JSON.stringify(body) : undefined,
        });
        if (!r.ok) {
          const said = refusal(await r.json().catch(() => null));
          setError(scrub(said || `Error ${r.status}`));
          return false;
        }
        await load();
        return true;
      } catch {
        setError("Couldn't reach the server.");
        return false;
      }
    },
    [apiUrl, load],
  );

  /** Nothing optimistic: a refusal must leave the select reading what is really configured. Both go
   *  through the GUARDED setup endpoints rather than /api/settings/runtime, which would write provider
   *  and model as two unrelated keys — switching companies with the old model still set leaves a pair
   *  that cannot work, and "Test connection" then reports a perfectly good key as a bad one. */
  const setBrain = useCallback(
    async (body: { provider?: string; model?: string }) => {
      setLlmTest("");
      const path = body.model === undefined ? "/api/setup/provider" : "/api/setup/model";
      await api(path, "POST", body);
    },
    [api],
  );

  /** Optimistic — so the refusal has to re-read, or it stays on screen once the banner clears. */
  const setRuntime = useCallback(
    (key: keyof Runtime, value: string | number | boolean) => {
      setS((prev) => (prev ? { ...prev, runtime: { ...prev.runtime, [key]: value } } : prev));
      void api("/api/settings/runtime", "POST", { key, value }).then((ok) => {
        if (!ok) load();
      });
    },
    [api, load],
  );

  /** Drop the guard BEFORE the write so the read-back on success still lands; put it back on a refusal
   *  so the typed text survives. */
  const savePersonality = useCallback(async () => {
    personalityDirty.current = false;
    const ok = await api("/api/settings/soul", "POST", { name, language, voice_id: voiceId });
    if (!ok) personalityDirty.current = true;
  }, [api, name, language, voiceId]);

  /** An empty draft is "I typed nothing", never "erase my key" — sending it deleted the stored key. */
  const saveKey = useCallback(
    async (provider: string) => {
      if (!apiKeyDraft.trim()) { setError("Type a key first — use Remove to erase the stored one."); return; }
      const ok = await api("/api/settings/llm-key", "POST", { provider, key: apiKeyDraft });
      if (ok) { setApiKeyDraft(""); setLlmTest(""); }
    },
    [api, apiKeyDraft],
  );

  const removeKey = useCallback(
    async (provider: string) => {
      if (!window.confirm(`Remove the stored ${provider} API key?`)) return;
      const ok = await api("/api/settings/llm-key", "POST", { provider, key: "" });
      if (ok) { setApiKeyDraft(""); setLlmTest(""); }
    },
    [api],
  );

  /** Tiny round-trip test — the result NEVER echoes the key. */
  const testLlm = useCallback(async () => {
    setLlmTest("Testing…");
    try {
      const r = await apiFetch(`${apiUrl}/api/settings/llm-test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const j = await r.json();
      setLlmTest((j?.ok ? "✓ " : "× ") + scrub(j?.detail || ""));
    } catch {
      setLlmTest("× Couldn't reach the server.");
    }
  }, [apiUrl]);

  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(""), 5000);
    return () => clearTimeout(t);
  }, [error]);

  const editPersonality = (set: (v: string) => void) => (v: string) => {
    personalityDirty.current = true;
    set(v);
  };

  const sec = (title: string) => openSections.has(title);
  const menu = modelMenu(s);

  return (
    <div
      style={{
        width: open ? "min(38vw, 480px)" : 0,
        transition: `width 460ms ${ease}, margin 460ms ${ease}`,
        display: "flex",
        justifyContent: "flex-end",
        flexShrink: 0,
        marginLeft: open ? "1.1rem" : 0,
        pointerEvents: open ? "auto" : "none",
      }}
      inert={!open}
    >
      <div
        style={{
          height: "min(80vh, 720px)",
          minWidth: "min(38vw, 480px)",
          display: "flex",
          flexDirection: "column",
          background: "var(--cream)",
          border: "4px solid var(--ink)",
          borderRadius: 24,
          boxShadow: "var(--shadow-pop)",
          clipPath: open ? "circle(150% at 0% 0%)" : "circle(0% at 0% 0%)",
          transition: open ? `clip-path 560ms ${ease} 200ms` : `clip-path 340ms ${ease}`,
          overflow: "hidden",
        }}
      >
        <Header onClose={onClose} />
        {error && <ErrorBanner msg={error} onClose={() => setError("")} />}
        <div className="kotoba-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "0.9rem 1rem", display: "flex", flexDirection: "column", gap: "0.7rem" }}>
          {!s ? (
            <div style={{ margin: "auto", opacity: 0.6, fontWeight: 500 }}>Loading…</div>
          ) : (
            <>
              <Section title="Personality" accent="var(--coral)" open={sec("Personality")} onToggle={toggleSection} summary={name || "(unnamed)"}>
                <Field label="Name" value={name} onChange={editPersonality(setName)} placeholder="(unnamed)" />
                <SelectField label="Language" value={language} onChange={editPersonality(setLanguage)} options={[
                  { v: "auto", t: "Auto (match the user)" }, { v: "en", t: "English" }, { v: "es", t: "Spanish" }, { v: "ja", t: "Japanese" },
                  { v: "fr", t: "French" }, { v: "pt", t: "Portuguese" },
                ]} />
                <Field label="Voice ID (ElevenLabs)" value={voiceId} onChange={editPersonality(setVoiceId)} placeholder="(default)" />
                <button style={primaryBtn} onClick={savePersonality}>
                  Save
                </button>
                {/* An empty list is the fresh install, so it names the folder rather than drawing a
                    select with nothing in it — a control with no options reads as a broken control. */}
                {s.avatar?.installed?.length ? (
                  <SelectField label="Avatar model (applies on reload)" value={s.avatar.selected?.dir || ""}
                    onChange={(v) => api("/api/settings/avatar", "POST", { model: v })}
                    options={s.avatar.installed.map((m) => ({ v: m.dir, t: m.dir }))} />
                ) : (
                  <span style={{ fontSize: "0.75rem", opacity: 0.75, lineHeight: 1.5 }}>
                    No Live2D model installed — unpack a Cubism 4 model into its own folder under{" "}
                    <code>{scrub(s.avatar?.models_dir || "~/.kotoba/models")}</code> and reload.
                  </span>
                )}
                <div style={{ borderTop: "2px dashed var(--ink)", opacity: 0.9, margin: "0.5rem 0 0.2rem", paddingTop: "0.7rem", display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                  <SelectField label="Voice mode (applies to the next call)" value={s.runtime.voice_mode || "local"}
                    onChange={(v) => setRuntime("voice_mode", v)}
                    options={[
                      { v: "agent", t: "ElevenLabs agent (WebRTC — needs a public backend URL)" },
                      { v: "local", t: "Local (browser ↔ this backend, no tunnel)" },
                    ]} />
                  <SelectField label="Voice engine (local mode)" value={s.runtime.tts_engine || "expressive"}
                    onChange={(v) => setRuntime("tts_engine", v)}
                    options={[
                      { v: "expressive", t: "Expressive (eleven_v3 — performs emotion tags)" },
                      { v: "fast", t: "Fast (flash — lowest latency, flat voice)" },
                    ]} />
                  <CommitField label="ElevenLabs Agent ID" value={s.runtime.elevenlabs_agent_id || ""}
                    onCommit={(v) => setRuntime("elevenlabs_agent_id", v)} placeholder="agent_xxxxxxxx (from your ElevenLabs agent)" />
                  <button style={secondaryBtn} onClick={() => setElGuide(true)}>How to set up ElevenLabs →</button>
                </div>
              </Section>

              <Section title="Brain" accent="var(--grape)" open={sec("Brain")} onToggle={toggleSection} summary={s.runtime.model} hint="Provider, API key, the models she thinks with, and how much she reasons.">
                <SelectField label="Provider" value={s.runtime.provider || "openai"} onChange={(v) => setBrain({ provider: v })}
                  options={(s.llm?.providers || []).map((p) => ({ v: p.id, t: p.label }))} />
                <CommitField label="Base URL (optional — leave blank for the provider default)"
                  value={s.runtime.base_url || ""} onCommit={(v) => setRuntime("base_url", v)}
                  placeholder={(s.llm?.providers.find((p) => p.id === (s.runtime.provider || "openai"))?.default_base_url) || "https://api.openai.com/v1"} />
                <Field label={`API key${keyNote(s.llm?.providers.find((p) => p.id === (s.runtime.provider || "openai")))}`}
                  value={apiKeyDraft} onChange={setApiKeyDraft} secret
                  placeholder={keyPlaceholder(s.llm?.providers.find((p) => p.id === (s.runtime.provider || "openai")))} />
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <button style={{ ...primaryBtn, alignSelf: "auto" }} onClick={() => saveKey(s.runtime.provider || "openai")}>Save key</button>
                  {s.llm?.providers.find((p) => p.id === (s.runtime.provider || "openai"))?.has_key && (
                    <button style={{ ...secondaryBtn, alignSelf: "auto" }} onClick={() => removeKey(s.runtime.provider || "openai")}>Remove</button>
                  )}
                  <button style={{ ...secondaryBtn, alignSelf: "auto" }} onClick={testLlm}>Test connection</button>
                  {llmTest && <span style={{ fontSize: "0.78rem", opacity: 0.85 }}>{llmTest}</span>}
                </div>

                {/* Per-role models — empty = inherit (work and utility from companion, code and research from
                    work). allowCustom = type any model id. */}
                <SelectField label="Companion model (voice)" value={s.runtime.model}
                  onChange={(v) => setBrain({ provider: s.runtime.provider || "openai", model: v })}
                  options={menu.models} allowCustom />
                <SelectField label="Work model (background)" value={s.runtime.work_model || ""} onChange={(v) => setRuntime("work_model", v)}
                  options={[{ v: "", t: "(same as companion)" }, ...menu.models]} allowCustom />
                <SelectField label="Code model" value={s.runtime.code_model || ""} onChange={(v) => setRuntime("code_model", v)}
                  options={[{ v: "", t: "(same as work)" }, ...menu.code, ...menu.models]} allowCustom />
                <SelectField label="Research model" value={s.runtime.research_model || ""} onChange={(v) => setRuntime("research_model", v)}
                  options={[{ v: "", t: "(same as work)" }, ...menu.models]} allowCustom />
                <SelectField label="Utility model (emotion / memory / captions)" value={s.runtime.utility_model || ""} onChange={(v) => setRuntime("utility_model", v)}
                  options={[{ v: "", t: "(same as companion)" }, ...menu.models]} allowCustom />
                <SelectField label="Reasoning effort" value={s.runtime.reasoning_effort || "off"} onChange={(v) => setRuntime("reasoning_effort", v)}
                  options={REASONING.map((r) => ({ v: r, t: r }))} />
                <Toggle label="Expressive voice (audio tags)" on={s.runtime.expressive} onChange={(v) => setRuntime("expressive", v)} />
                <ReconfigureRow />
              </Section>

              <Section title="Capabilities" accent="var(--mint)" open={sec("Capabilities")} onToggle={toggleSection}
                summary={`${s.toolsets.filter((t) => t.enabled).length}/${s.toolsets.length} on`} hint="Turn whole tool families on or off.">
                {s.toolsets.map((t) => (
                  <Toggle key={t.name} label={`${t.name} · ${t.tools} tool${t.tools === 1 ? "" : "s"}`} on={t.enabled}
                    onChange={(v) => api("/api/settings/toolset", "POST", { name: t.name, enabled: v })} />
                ))}
              </Section>

              <Section title="MCP servers" accent="var(--sun)" open={sec("MCP servers")} onToggle={toggleSection}
                summary={`${s.mcp_servers.length} connected`} hint="External tools she's connected to.">
                {s.mcp_servers.length === 0 && <Empty>None connected yet.</Empty>}
                {s.mcp_servers.map((m) => (
                  <ListRow key={m.name} label={`${m.name} · ${m.tools} tools`} onDelete={() => api(`/api/mcp/${encodeURIComponent(m.name)}`, "DELETE")} />
                ))}
                <ConnectKnown onConnect={(n) => api("/api/mcp/connect", "POST", { name: n })} />
              </Section>

              {(s.pending_mcp?.length ?? 0) > 0 && (
                <Section title="Needs connection" accent="var(--live)" open={sec("Needs connection")} onToggle={toggleSection}
                  summary={`${s.pending_mcp.length} waiting`} hint="Servers Kotoba found that need authentication before she can use them.">
                  {s.pending_mcp.map((p) =>
                    p.kind === "token" ? (
                      <PendingToken key={p.name} name={p.name} description={p.description}
                        onConnect={(token) => api("/api/mcp/connect", "POST", { name: p.name, token })}
                        onDismiss={() => api(`/api/mcp/${encodeURIComponent(p.name)}`, "DELETE")} />
                    ) : (
                      <PendingOAuth key={p.name} name={p.name} description={p.description} apiUrl={apiUrl} onDone={load}
                        onDismiss={() => api(`/api/mcp/${encodeURIComponent(p.name)}`, "DELETE")} />
                    ),
                  )}
                </Section>
              )}

              <Section title="Plugins" accent="var(--grape)" open={sec("Plugins")} onToggle={toggleSection}
                summary={`${s.plugins.length}`} hint="Community add-ons (folder or pip). Toggle to enable/disable.">
                {s.plugins.length === 0 && <Empty>No plugins installed.</Empty>}
                {s.plugins.map((p) => (
                  <Toggle key={p.name} label={`${p.name} · ${p.tools.length} tool${p.tools.length === 1 ? "" : "s"} (${p.source})`}
                    on={p.enabled} onChange={(v) => api("/api/settings/toolset", "POST", { name: `plugin:${p.name}`, enabled: v })} />
                ))}
              </Section>

              <Section title="Skills" accent="var(--coral)" open={sec("Skills")} onToggle={toggleSection}
                summary={`${s.skills.length}`} hint="Knowledge guides she consults.">
                {s.skills.length === 0 && <Empty>No skills loaded.</Empty>}
                {s.skills.map((sk) => (
                  <Card key={sk.name}>
                    <b style={{ fontFamily: "var(--font-display)", fontSize: "0.86rem" }}>{scrub(sk.name)}</b>
                    {sk.description ? <span style={{ opacity: 0.65, fontSize: "0.8rem" }}>{scrub(sk.description)}</span> : null}
                  </Card>
                ))}
              </Section>

              <Section title="Memory" accent="var(--grape)" open={sec("Memory")} onToggle={toggleSection}
                summary={`${s.memory.topics.length} topics`} hint="What she remembers, by topic.">
                {s.memory.topics.length === 0 && <Empty>Nothing saved yet.</Empty>}
                {s.memory.topics.map((t) => (
                  <ListRow key={t.slug} label={`${t.slug} · ${t.count}`} onDelete={() => api(`/api/memory/topic/${encodeURIComponent(t.slug)}`, "DELETE")} />
                ))}
              </Section>

              <Section title="Reminders" accent="var(--mint)" open={sec("Reminders")} onToggle={toggleSection}
                summary={`${s.reminders.length}`}>
                {s.reminders.length === 0 && <Empty>No reminders set.</Empty>}
                {s.reminders.map((r) => (
                  <ListRow key={r.id} label={`${r.message} (${r.due_at} UTC)`} onDelete={() => api(`/api/cron/${r.id}`, "DELETE")} />
                ))}
              </Section>

              <Section title="Saved keys" accent="var(--sun)" open={sec("Saved keys")} onToggle={toggleSection}
                summary={`${s.keys.length}`} hint="Stored on the server, never shown.">
                {s.keys.length === 0 && <Empty>No keys saved.</Empty>}
                {s.keys.map((k) => (
                  <ListRow key={k} label={k} onDelete={() => api(`/api/keys/${encodeURIComponent(k)}`, "DELETE")} />
                ))}
              </Section>

              <Section title="Security" accent="var(--live)" open={sec("Security")} onToggle={toggleSection} summary={s.runtime.sandbox}
                hint="Where her code/shell runs. 'none' disables execution; 'local' runs on this machine (gated by approvals).">
                <SelectField label="Sandbox" value={s.runtime.sandbox} onChange={(v) => setRuntime("sandbox", v)}
                  options={SANDBOXES.map((x) => ({ v: x, t: x }))}
                  warn={s.runtime.sandbox === "none" ? "Execution disabled." : undefined} />
                <Row k="Trust level" v={s.security.trust} />
                <div style={{ marginTop: 4 }}>
                  <label style={lbl}>Allowed commands</label>
                  {(s.security.approvals?.length ?? 0) === 0 && <Empty>None — she asks before anything that isn&apos;t a simple read.</Empty>}
                  {/* Two widths in one list, so each row says which it is. */}
                  {s.security.approvals?.map((a) => (
                    <ListRow key={a.pattern}
                      label={a.scope === "exact" ? `just this line: ${a.pattern}`
                        : a.pattern === "execute_code" ? "running code (Python)"
                        : `any “${a.pattern}” command`}
                      onDelete={() => api(`/api/approvals?pattern=${encodeURIComponent(a.pattern)}`, "DELETE")} />
                  ))}
                </div>
                <SignOutRow />
              </Section>

              <Section title="Advanced (work)" accent="var(--grape)" open={sec("Advanced (work)")} onToggle={toggleSection}
                summary={`${s.runtime.work_timeout}s`} hint="Limits for background tasks. Higher = more thorough but slower/costlier.">
                <NumField label="Work timeout (seconds)" value={s.runtime.work_timeout} min={30} onCommit={(v) => setRuntime("work_timeout", v)} />
                <NumField label="Max iterations" value={s.runtime.work_max_iter} min={1} onCommit={(v) => setRuntime("work_max_iter", v)} />
                <NumField label="Max tool calls" value={s.runtime.work_max_tool_calls} min={1} onCommit={(v) => setRuntime("work_max_tool_calls", v)} />
                <NumField label="Fail limit" value={s.runtime.work_fail_limit} min={1} onCommit={(v) => setRuntime("work_fail_limit", v)} />
                <Row k="Browser CDP" v={s.browser_cdp || "(none)"} />
              </Section>
            </>
          )}
        </div>
      </div>
      {elGuide && typeof document !== "undefined" && createPortal(
        <div onClick={() => setElGuide(false)}
          style={{ position: "fixed", inset: 0, zIndex: 60, background: "rgba(33,26,46,0.45)", display: "grid", placeItems: "center", padding: "1.2rem" }}>
          <div onClick={(e) => e.stopPropagation()}
            style={{ width: "min(640px, 92vw)", maxHeight: "85vh", display: "flex", flexDirection: "column", overflow: "hidden", background: "var(--cream)", border: "3px solid var(--ink)", borderRadius: 18, boxShadow: "6px 6px 0 var(--ink)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0.6rem 0.9rem", borderBottom: "3px solid var(--ink)", background: "#fff", flexShrink: 0, borderRadius: "15px 15px 0 0" }}>
              <strong style={{ fontFamily: "var(--font-display)" }}>ElevenLabs setup</strong>
              <button onClick={() => setElGuide(false)} aria-label="Close" style={{ ...secondaryBtn, padding: "0.2rem 0.6rem", alignSelf: "auto" }}>✕</button>
            </div>
            <div className="md-view kotoba-scroll" style={{ overflow: "auto", flex: 1, padding: "0.6rem 1rem 1.2rem" }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents()}>{EL_GUIDE_MD}</ReactMarkdown>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}

function Header({ onClose }: { onClose: () => void }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0.7rem 0.8rem 0.7rem 0.9rem", borderBottom: "3px solid var(--ink)", background: "#fff", flexShrink: 0 }}>
      <span style={{ display: "flex", alignItems: "center", gap: 9 }}>
        <span style={{ display: "grid", placeItems: "center", width: 30, height: 30, borderRadius: 10, background: "var(--ink)", border: "2.5px solid var(--ink)", color: "#fff" }}>
          <SettingsIcon width={17} height={17} />
        </span>
        <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "1rem" }}>Settings</span>
      </span>
      <button onClick={onClose} aria-label="Close settings" style={{ border: "2.5px solid var(--ink)", background: "var(--cream)", borderRadius: 9, width: 28, height: 28, display: "grid", placeItems: "center", padding: 0, boxShadow: "2px 2px 0 var(--ink)" }}>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" strokeWidth="3.2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
      </button>
    </div>
  );
}

function ErrorBanner({ msg, onClose }: { msg: string; onClose: () => void }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "0.5rem 0.8rem", background: "var(--live)", color: "#fff", borderBottom: "3px solid var(--ink)", fontSize: "0.82rem", fontWeight: 600, flexShrink: 0 }}>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{msg}</span>
      <button onClick={onClose} aria-label="Dismiss" style={{ border: "2px solid #fff", background: "transparent", borderRadius: 7, width: 22, height: 22, display: "grid", placeItems: "center", flexShrink: 0, padding: 0 }}>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3.4" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
      </button>
    </div>
  );
}

function Section({ title, accent, hint, summary, open, onToggle, children }: {
  title: string; accent: string; hint?: string; summary?: string; open: boolean; onToggle: (t: string) => void; children: React.ReactNode;
}) {
  return (
    <div style={{ background: "#fff", border: "3px solid var(--ink)", borderRadius: 16, boxShadow: "var(--shadow-pop-sm)", overflow: "hidden", flexShrink: 0 }}>
      <button onClick={() => onToggle(title)} aria-expanded={open}
        style={{ width: "100%", display: "flex", alignItems: "center", gap: 9, padding: "0.7rem 0.85rem", background: "transparent", border: "none", cursor: "pointer", textAlign: "left" }}>
        <span style={{ width: 13, height: 13, borderRadius: 4, background: accent, border: "2.5px solid var(--ink)", flexShrink: 0 }} />
        <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "0.95rem", flex: 1 }}>{title}</span>
        {!open && summary && (
          <span style={{ fontSize: "0.74rem", fontWeight: 600, opacity: 0.55, fontFamily: "var(--font-display)", maxWidth: 140, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{scrub(summary)}</span>
        )}
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"
          style={{ transform: open ? "rotate(180deg)" : "rotate(0deg)", transition: "transform 200ms", flexShrink: 0 }}>
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", padding: "0 0.85rem 0.85rem", borderTop: "2px dashed rgba(0,0,0,0.1)", paddingTop: "0.7rem" }}>
          {hint && <p style={{ fontSize: "0.74rem", opacity: 0.6, margin: "-0.1rem 0 0.1rem" }}>{hint}</p>}
          {children}
        </div>
      )}
    </div>
  );
}

// Leaf components scrub what they DRAW: the store keeps these server-side values raw on purpose.
function Row({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.85rem", gap: 12 }}>
      <span style={{ opacity: 0.7 }}>{k}</span>
      <span style={{ fontWeight: 600, fontFamily: "var(--font-display)", textAlign: "right", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{scrub(v)}</span>
    </div>
  );
}

function Field({ label, value, onChange, placeholder, secret }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string; secret?: boolean }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <label style={lbl}>{label}</label>
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} style={field}
        type={secret ? "password" : "text"} autoComplete={secret ? "off" : undefined} spellCheck={secret ? false : undefined} />
    </div>
  );
}

function CommitField({ label, value, onCommit, placeholder }: { label: string; value: string; onCommit: (v: string) => void; placeholder?: string }) {
  const [draft, setDraft] = useState(value);
  const [editing, setEditing] = useState(false);
  useEffect(() => { if (!editing) setDraft(value); }, [value, editing]);
  const commit = () => { setEditing(false); if (draft !== value) onCommit(draft); };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <label style={lbl}>{label}</label>
      <input
        value={draft}
        onFocus={() => setEditing(true)}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") { e.currentTarget.blur(); } }}
        placeholder={placeholder}
        style={field}
      />
    </div>
  );
}

/** The ACTIVE provider only: a list mixing both companies offered ids the install could only 404 on,
 *  and one of them accepts the call and silently drops every tool she has. */
function modelMenu(s: Settings | null): { models: { v: string; t: string }[]; code: { v: string; t: string }[] } {
  const active = s?.llm?.providers?.find((p) => p.id === (s.runtime.provider || "openai"));
  const row = (m: ModelRow) => ({ v: m.id, t: m.hint ? `${m.id} · ${m.hint} the price` : m.id });
  return { models: (active?.models ?? []).map(row), code: (active?.code_models ?? []).map(row) };
}

function SelectField({ label, value, onChange, options, allowCustom, warn }: {
  label: string; value: string; onChange: (v: string) => void; options: { v: string; t: string }[]; allowCustom?: boolean; warn?: string;
}) {
  const opts = allowCustom && value && !options.some((o) => o.v === value) ? [{ v: value, t: value }, ...options] : options;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <label style={lbl}>{label}</label>
      <select value={value} onChange={(e) => onChange(e.target.value)} style={field}>
        {opts.map((o) => <option key={o.v} value={o.v}>{o.t}</option>)}
      </select>
      {warn && <span style={{ fontSize: "0.72rem", color: "var(--live)", fontWeight: 600 }}>⚠ {warn}</span>}
    </div>
  );
}

function NumField({ label, value, min, onCommit }: { label: string; value: number; min: number; onCommit: (v: number) => void }) {
  const [v, setV] = useState(String(value));
  useEffect(() => setV(String(value)), [value]);
  const commit = () => {
    const n = Math.max(min, Math.round(Number(v) || min));
    setV(String(n));
    if (n !== value) onCommit(n);
  };
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
      <label style={{ ...lbl, textTransform: "none", fontSize: "0.82rem", fontWeight: 500, opacity: 0.85 }}>{label}</label>
      <input type="number" min={min} value={v} onChange={(e) => setV(e.target.value)} onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        style={{ ...field, width: 92, textAlign: "right" }} />
    </div>
  );
}

function Toggle({ label, on, onChange }: { label: string; on: boolean; onChange: (v: boolean) => void }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, fontSize: "0.85rem" }}>
      <span>{scrub(label)}</span>
      <button onClick={() => onChange(!on)} aria-label={`Toggle ${label}`}
        style={{ width: 46, height: 26, borderRadius: 999, border: "2.5px solid var(--ink)", background: on ? "var(--mint)" : "var(--cream-2)", position: "relative", flexShrink: 0, transition: "background 160ms", boxShadow: "2px 2px 0 var(--ink)" }}>
        <span style={{ position: "absolute", top: 1.5, left: on ? 22 : 2, width: 18, height: 18, borderRadius: "50%", background: "#fff", border: "2px solid var(--ink)", transition: "left 160ms" }} />
      </button>
    </div>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2, background: "var(--cream)", border: "2px solid var(--ink)", borderRadius: 10, padding: "0.45rem 0.6rem" }}>
      {children}
    </div>
  );
}

/** `gateIsOn()` is read after mount: the token lives in sessionStorage, which SSR cannot see. */
function SignOutRow() {
  const [shown, setShown] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => setShown(gateIsOn()), []);
  if (!shown) return null;

  // A FULL navigation, not a router push: the gate lives in middleware, and the in-memory app state
  // (session id, token, open panels, a live call) must not survive into the next person's session.
  const go = async () => {
    setBusy(true);
    await signOut();
    window.location.href = "/login";
  };

  return (
    <div style={{ marginTop: 10, borderTop: "2px dashed var(--ink)", paddingTop: 10 }}>
      <label style={lbl}>This browser</label>
      <p style={{ fontSize: "0.78rem", opacity: 0.6, margin: "2px 0 6px" }}>
        Ends the 12-hour session on this device. Her memory and files are untouched.
      </p>
      <button style={secondaryBtn} onClick={go} disabled={busy}>
        {busy ? "Signing out…" : "Sign out"}
      </button>
    </div>
  );
}

function ReconfigureRow() {
  return (
    <div style={{ marginTop: 10, borderTop: "2px dashed var(--ink)", paddingTop: 10 }}>
      <label style={lbl}>Set her up again</label>
      <p style={{ fontSize: "0.78rem", opacity: 0.6, margin: "2px 0 6px" }}>
        Walks through the first-meeting screens again — brain, model, key, names, language, voice. Each
        answer overwrites only itself, and a step you skip keeps exactly what it had.
      </p>
      {/* Full navigation for the same reason as Sign out, and this is the only door onto first run on
          a configured machine — the route sends anyone arriving without the invitation back to /app. */}
      <button style={secondaryBtn} onClick={() => { window.location.href = SETUP_INVITE_URL; }}>
        Run setup again
      </button>
      {/* The face step alone. /app offers it once per browser and then stops asking, so this is the
          standing way back — and walking the other eight questions to reach it is why nobody did. */}
      <button style={{ ...secondaryBtn, marginTop: 8 }}
              onClick={() => { window.location.href = SETUP_FACE_URL; }}>
        Give her a face
      </button>
    </div>
  );
}

function ListRow({ label, onDelete }: { label: string; onDelete: () => void }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, fontSize: "0.84rem", background: "var(--cream)", border: "2px solid var(--ink)", borderRadius: 10, padding: "0.35rem 0.55rem" }}>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{scrub(label)}</span>
      <button onClick={onDelete} aria-label="Remove" style={{ border: "2px solid var(--ink)", background: "#fff", borderRadius: 7, width: 24, height: 24, display: "grid", placeItems: "center", flexShrink: 0, padding: 0 }}>
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" strokeWidth="3.4" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
      </button>
    </div>
  );
}

function ConnectKnown({ onConnect }: { onConnect: (name: string) => void }) {
  const [sel, setSel] = useState("");
  return (
    <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
      <select value={sel} onChange={(e) => setSel(e.target.value)} style={{ ...field, flex: 1 }}>
        {/* Ordered by how far a stranger is from using it: the three that need nothing, then the ones
            that do. Every suffix names what is still missing, and the last one's is not ours to phrase —
            it has to agree with known.setup_note("google calendar"), which is the 400 the connect
            answers with. Pinned by tests/connect-known-order.test.mjs. */}
        <option value="">Connect a server…</option>
        <option value="filesystem">Filesystem</option>
        <option value="browser">Browser (Playwright)</option>
        <option value="memory">Memory</option>
        {/* OAuth vendors: picking one records it under "Needs connection" with a "Sign in" button (the
            connect can't finish here — it needs a browser sign-in). */}
        <option value="notion">Notion (sign-in)</option>
        <option value="linear">Linear (sign-in)</option>
        <option value="slack">Slack (sign-in)</option>
        <option value="github">GitHub (needs a token)</option>
        <option value="google calendar">Google Calendar (setup in Google Cloud)</option>
      </select>
      <button style={{ ...primaryBtn, opacity: sel ? 1 : 0.5 }} disabled={!sel} onClick={() => sel && onConnect(sel)}>
        Connect
      </button>
    </div>
  );
}

function PendingToken({ name, description, onConnect, onDismiss }: { name: string; description: string; onConnect: (token: string) => void; onDismiss: () => void }) {
  const [token, setToken] = useState("");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10, paddingBottom: 8, borderBottom: "1.5px dashed rgba(0,0,0,0.15)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <strong style={{ fontSize: "0.88rem" }}>{scrub(name)}</strong>
        <button onClick={onDismiss} aria-label="Dismiss" style={{ border: "2px solid var(--ink)", background: "#fff", borderRadius: 7, width: 22, height: 22, display: "grid", placeItems: "center", flexShrink: 0, padding: 0 }}>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" strokeWidth="3.4" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      </div>
      {description && <span style={{ fontSize: "0.76rem", opacity: 0.6 }}>{scrub(description)}</span>}
      <div style={{ display: "flex", gap: 8 }}>
        <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="Paste API key / token" autoComplete="off" style={{ ...field, flex: 1 }} />
        <button style={{ ...primaryBtn, opacity: token ? 1 : 0.5 }} disabled={!token} onClick={() => { if (token) { onConnect(token); setToken(""); } }}>
          Connect
        </button>
      </div>
    </div>
  );
}

function PendingOAuth({ name, description, apiUrl, onDone, onDismiss }: { name: string; description: string; apiUrl: string; onDone: () => void; onDismiss: () => void }) {
  const [phase, setPhase] = useState<"idle" | "waiting" | "error">("idle");
  const [err, setErr] = useState("");

  const connect = async () => {
    setErr("");
    setPhase("waiting");
    try {
      const r = await apiFetch(`${apiUrl}/api/mcp/oauth/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!r.ok) throw new Error("start");
      const { flow_id, auth_url } = await r.json();
      window.open(auth_url, "_blank", "noopener,noreferrer");
      // Long-poll: the backend awaits the browser callback + token exchange, then connects.
      const f = await apiFetch(`${apiUrl}/api/mcp/oauth/finish`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, flow_id }),
      });
      if (!f.ok) throw new Error("finish");
      onDone();
    } catch {
      setErr("Sign-in didn't complete. Try again.");
      setPhase("error");
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10, paddingBottom: 8, borderBottom: "1.5px dashed rgba(0,0,0,0.15)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <strong style={{ fontSize: "0.88rem" }}>{scrub(name)}</strong>
        <button onClick={onDismiss} aria-label="Dismiss" style={{ border: "2px solid var(--ink)", background: "#fff", borderRadius: 7, width: 22, height: 22, display: "grid", placeItems: "center", flexShrink: 0, padding: 0 }}>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" strokeWidth="3.4" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      </div>
      {description && <span style={{ fontSize: "0.76rem", opacity: 0.6 }}>{scrub(description)}</span>}
      {phase === "waiting" ? (
        <span style={{ fontSize: "0.76rem", opacity: 0.75, fontStyle: "italic" }}>Approve the sign-in in the new tab…</span>
      ) : (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button style={primaryBtn} onClick={connect}>Sign in</button>
          {err && <span style={{ fontSize: "0.74rem", color: "var(--coral)" }}>{err}</span>}
        </div>
      )}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p style={{ fontSize: "0.82rem", opacity: 0.55 }}>{children}</p>;
}

const field: React.CSSProperties = {
  width: "100%",
  border: "2.5px solid var(--ink)",
  borderRadius: 10,
  padding: "0.45rem 0.6rem",
  fontFamily: "var(--font-body)",
  fontSize: "0.86rem",
  background: "var(--cream)",
  color: "var(--ink)",
  outline: "none",
};

const lbl: React.CSSProperties = { fontSize: "0.72rem", fontWeight: 600, opacity: 0.65, textTransform: "uppercase", letterSpacing: "0.04em" };

const primaryBtn: React.CSSProperties = {
  alignSelf: "flex-start",
  border: "2.5px solid var(--ink)",
  background: "var(--coral)",
  color: "#fff",
  borderRadius: 11,
  padding: "0.4rem 0.9rem",
  fontFamily: "var(--font-display)",
  fontWeight: 600,
  fontSize: "0.84rem",
  boxShadow: "2px 2px 0 var(--ink)",
};

const secondaryBtn: React.CSSProperties = {
  ...primaryBtn,
  background: "var(--cream-2)",
  color: "var(--ink)",
};
