"""Versioned schema migrations, applied once on startup.

Each entry in _STEPS is an idempotent SQL script; `PRAGMA user_version` tracks how many have been
applied, and every step at or past that index runs, bumping the version after each.
Databases predating versioning read 0, and step 0 is IF NOT EXISTS throughout, so it no-ops for them.

To add a migration, APPEND — never edit an existing entry: indexes appended to step 0 never reached
databases already past it, and the hot queries silently full-scanned. Views live in _VIEW_STATEMENTS,
not a step, since they carry no data. A table rebuild must run inside BEGIN IMMEDIATE, or
executescript's commit between statements leaves a window where the table does not exist."""
from __future__ import annotations

import aiosqlite

# Step 0: bootstrap — the original CREATE TABLE schema (IF NOT EXISTS throughout, so idempotent).
_STEP_0 = """
CREATE TABLE IF NOT EXISTS soul_config (
  id            TEXT PRIMARY KEY DEFAULT 'default',
  name          TEXT,
  language      TEXT NOT NULL DEFAULT 'auto',
  voice_id      TEXT,
  avatar_model  TEXT NOT NULL DEFAULT 'mao_pro/runtime/mao_pro.model3.json',
  personality   TEXT,
  address_style TEXT NOT NULL DEFAULT 'name',
  emotional_rules TEXT,
  tool_patterns TEXT,
  quirks        TEXT,
  updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_profile (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL,
  updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS memory_facts (
  id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
  fact        TEXT NOT NULL,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
  id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
  started_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_at    TIMESTAMP,
  turn_count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS turns (
  id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
  session_id  TEXT NOT NULL REFERENCES sessions(id),
  role        TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
  content     TEXT NOT NULL,
  -- `emotion` and `tools_used` are both DEAD: every insert_turn call site passes role and content
  -- only, so both are NULL in every row and no query reads either — the face is driven live by the
  -- SSE emotion channel, never replayed from history. Dropping them would be a new migration step
  -- over live data, all risk for zero reclaimed behavior.
  emotion     TEXT,
  tools_used  TEXT,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
  content,
  turn_id    UNINDEXED,
  session_id UNINDEXED
);

-- Keep FTS5 in sync. Pass rowid = new.rowid explicitly, or FTS5 assigns its own
-- internal rowid and breaks future delete/update sync.
CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
  INSERT INTO turns_fts(rowid, content, turn_id, session_id)
  VALUES (new.rowid, new.content, new.id, new.session_id);
END;

-- Audit trail for every write/exec/network action: what ran, when, whether it was
-- approved and by whom (allowlist / auto-safe / user / default).
CREATE TABLE IF NOT EXISTS audit_log (
  id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
  session_id  TEXT,
  action      TEXT NOT NULL,
  risk_kind   TEXT,
  approved    INTEGER NOT NULL DEFAULT 0,
  approver    TEXT,
  detail      TEXT,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Secrets the user typed into the input card. Stored backend-only, NEVER in the chat /
-- transcript. Tools read them by name when calling a service; they're not put into the LLM context.
CREATE TABLE IF NOT EXISTS saved_keys (
  name        TEXT PRIMARY KEY,
  value       TEXT NOT NULL,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Persisted exec approvals ("smart approval"): when the user approves a command with "always", what
-- they granted is saved here so the gate auto-approves it next time instead of re-asking. `scope` says
-- HOW WIDE the grant is and is the only thing telling the two apart: 'command' is a family (the first
-- token, e.g. `npm`, `git`, or a fixed tool token like `execute_code`) and matches every command that
-- starts with it; 'exact' is one whole command line and matches nothing but itself. Managed from Settings.
-- Step 2 makes (pattern, scope) the key — the width is part of the identity, not an attribute of it.
CREATE TABLE IF NOT EXISTS approved_commands (
  pattern     TEXT PRIMARY KEY,     -- a command family / tool token, or (scope='exact') a whole command
  scope       TEXT NOT NULL DEFAULT 'command',
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Scheduled reminders / proactive nudges. A worker fires due jobs.
CREATE TABLE IF NOT EXISTS cronjobs (
  id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
  session_id  TEXT,
  message     TEXT NOT NULL,
  due_at      TIMESTAMP NOT NULL,
  recurring   TEXT,                 -- NULL = one-shot; else 'daily'|'weekly'|'hourly'
  fired_at    TIMESTAMP,            -- last time it fired (NULL = never)
  active      INTEGER NOT NULL DEFAULT 1,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

"""

# Step 1: the three hot query paths, which otherwise full-scan on every turn.
_STEP_1 = """
-- turns: fetch_recent_turns (WHERE session_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?)
-- Note: rowid cannot appear in an index column list; (session_id, created_at) is sufficient.
CREATE INDEX IF NOT EXISTS idx_turns_session_time ON turns(session_id, created_at);
-- cronjobs: CRON_DUE (WHERE active=1 AND fired_at IS NULL AND due_at <= now ORDER BY due_at)
CREATE INDEX IF NOT EXISTS idx_cronjobs_due ON cronjobs(active, fired_at, due_at);
-- audit_log: AUDIT_LOG_RECENT (ORDER BY created_at DESC LIMIT ?)
CREATE INDEX IF NOT EXISTS idx_audit_log_time ON audit_log(created_at);
"""

_STEP_2 = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS approved_commands_v2 (
  pattern     TEXT NOT NULL,        -- a command family / tool token, or (scope='exact') a whole command
  scope       TEXT NOT NULL DEFAULT 'command',
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (pattern, scope)
);
INSERT OR IGNORE INTO approved_commands_v2 (pattern, scope, created_at)
  SELECT pattern, scope, created_at FROM approved_commands;
DROP TABLE approved_commands;
ALTER TABLE approved_commands_v2 RENAME TO approved_commands;
COMMIT;
"""

# Append only; never edit an existing entry (module docstring says why).
# Her own store, never the user-memory one: that is read into the owner's prompt on EVERY surface,
# so a guild member's facts written there would follow him into the terminal. `source` keeps what
# somebody said about themselves apart from what a third party said about them.
_STEP_3 = """
CREATE TABLE IF NOT EXISTS discord_people (
    user_id    TEXT PRIMARY KEY,
    handle     TEXT,
    display    TEXT,
    relation   TEXT NOT NULL DEFAULT 'known',
    first_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes      TEXT
);
CREATE TABLE IF NOT EXISTS discord_person_facts (
    id         TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    user_id    TEXT NOT NULL REFERENCES discord_people(user_id),
    fact       TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'said',
    guild_id   TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_person_facts_user ON discord_person_facts(user_id, created_at);
"""

_STEPS = [_STEP_0, _STEP_1, _STEP_2, _STEP_3]

# ONE STATEMENT PER ELEMENT, never a blob: _rebuild_views runs them inside a single transaction, which
# executescript cannot do (it commits between statements), and splitting on ';' silently swallowed the
# DROP because its leading SQL comments made that chunk look like a comment.
# audit_log_read derives `regime` — whether a row's approved/approver can be trusted (detail IS NULL
# means approved≠consent) — and `authority`, a label for WHO permitted this, or that nobody did.
_VIEW_STATEMENTS = [
    "DROP VIEW IF EXISTS audit_log_read",
    """
    CREATE VIEW audit_log_read AS
    SELECT *,
      CASE
        WHEN detail IS NULL          THEN 'legacy — approved unreliable'
        WHEN approver = 'work-loop'  THEN 'pre-vocab'
        ELSE                              'current'
      END AS regime,
      CASE approver
        WHEN 'user'       THEN 'user'
        WHEN 'auto-safe'  THEN 'auto-safe'
        WHEN 'allowlist'  THEN 'allowlist'
        WHEN 'saved'      THEN 'saved-family'
        WHEN 'saved-exact' THEN 'saved-exact-command'
        WHEN 'work-loop'  THEN 'none — the loop ran it, nobody was asked (pre-vocab)'
        WHEN 'agent-loop' THEN 'none — the loop ran it, nobody was asked'
        WHEN 'error'      THEN 'nobody — the card never reached the user (channel failed), denied'
        WHEN 'expired'    THEN 'nobody — the card was shown and expired unanswered, denied'
        WHEN 'default'    THEN 'default'
        ELSE COALESCE(approver, 'unknown')
      END AS authority
    FROM audit_log
    """,
]


async def run_migrations(conn: aiosqlite.Connection) -> None:
    """Apply all pending migration steps in order, tracked by PRAGMA user_version.

    executescript issues an implicit COMMIT before running each step — fine here — and the version
    bump is NOT atomic with the step: a crash in between re-runs it, which is why every step must
    stay idempotent."""
    async with conn.execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    version = row[0] if row else 0

    for i, step in enumerate(_STEPS):
        if i < version:
            continue
        await conn.executescript(step)
        await conn.execute(f"PRAGMA user_version = {i + 1}")
        await conn.commit()

    await _rebuild_views(conn)


async def _rebuild_views(conn: aiosqlite.Connection) -> None:
    """Recreate the read views, atomically and tolerantly.

    NOT executescript: that runs each statement in autocommit, so the DROP and the CREATE are separate
    transactions. Two processes booting at once (the CLI beside the web server) then interleave into
    DROP, DROP, CREATE, CREATE — and the second CREATE dies with "view already exists", unhandled, out of
    Database.connect(). It also leaves a window where the view exists for nobody. BEGIN IMMEDIATE closes
    both; the except is the belt for a racer that got in before our lock — it rebuilt the same views
    from the same source, an identical result with nothing to redo, so the swallow re-raises only when
    the view is truly absent."""
    try:
        await conn.execute("BEGIN IMMEDIATE")
        for stmt in _VIEW_STATEMENTS:
            await conn.execute(stmt)
        await conn.commit()
    except Exception:
        await conn.rollback()
        async with conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name='audit_log_read'"
        ) as cur:
            if await cur.fetchone() is None:
                raise
