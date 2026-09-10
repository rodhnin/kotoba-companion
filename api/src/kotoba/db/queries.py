"""Raw SQL used by db.database.Database. Kept separate so queries are auditable in one place."""

SOUL_CONFIG_GET = "SELECT * FROM soul_config WHERE id = 'default'"

SOUL_CONFIG_SEED = """
INSERT INTO soul_config
  (id, name, language, voice_id, avatar_model, personality, address_style,
   emotional_rules, tool_patterns, quirks)
VALUES
  ('default', :name, :language, :voice_id, :avatar_model, :personality, :address_style,
   :emotional_rules, :tool_patterns, :quirks)
ON CONFLICT(id) DO NOTHING
"""

# Developer-authored fields only — runtime fields the user owns (name, language) are never touched, and
# neither voice_id nor avatar_model is here: both are offered as a choice, so syncing them made every
# restart quietly undo the chosen voice, and would have done the same to the chosen face.
SOUL_CONFIG_SYNC = """
UPDATE soul_config SET
  personality     = :personality,
  address_style   = :address_style,
  emotional_rules = :emotional_rules,
  tool_patterns   = :tool_patterns,
  quirks          = :quirks,
  updated_at      = CURRENT_TIMESTAMP
WHERE id = 'default'
"""

SOUL_CONFIG_FILL_VOICE = """
UPDATE soul_config SET voice_id = :voice_id, updated_at = CURRENT_TIMESTAMP
WHERE id = 'default' AND (voice_id IS NULL OR voice_id = '')
"""

SOUL_CONFIG_FILL_AVATAR = """
UPDATE soul_config SET avatar_model = :avatar_model, updated_at = CURRENT_TIMESTAMP
WHERE id = 'default' AND (avatar_model IS NULL OR avatar_model = '')
"""

# One explicit statement per updatable column — no dynamic SQL composition (avoids injection).
SOUL_CONFIG_UPDATE = {
    "name": "UPDATE soul_config SET name = :value, updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
    "language": "UPDATE soul_config SET language = :value, updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
    "voice_id": "UPDATE soul_config SET voice_id = :value, updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
    "avatar_model": "UPDATE soul_config SET avatar_model = :value, updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
}

USER_PROFILE_ALL = "SELECT key, value FROM user_profile ORDER BY key"

USER_PROFILE_UPSERT = """
INSERT INTO user_profile (key, value) VALUES (:key, :value)
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
"""

MEMORY_FACTS_ACTIVE = """
SELECT fact FROM memory_facts
WHERE expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP
ORDER BY created_at DESC
"""

SESSION_ENSURE = "INSERT OR IGNORE INTO sessions (id) VALUES (:id)"

# The MOST RECENT `limit` turns (DESC + rowid tiebreak for same-second inserts). fetch_recent_turns reverses
# them back to chronological order. ASC+LIMIT (the old bug) fed the model the OLDEST turns of a long session.
RECENT_TURNS = """
SELECT role, content FROM turns
WHERE session_id = :session_id
ORDER BY created_at DESC, rowid DESC
LIMIT :limit
"""

TURN_INSERT = """
INSERT INTO turns (session_id, role, content, emotion, tools_used)
VALUES (:session_id, :role, :content, :emotion, :tools_used)
"""

# NOT sessions.turn_count: that column is never written and reads 0 in every row.
TURNS_COUNT = "SELECT COUNT(*) AS n FROM turns"

ASSISTANT_TURNS = """
SELECT id, session_id, content, created_at FROM turns
WHERE role = 'assistant'
ORDER BY created_at, rowid
"""

# NOT `ended_at` and NOT `turn_count` — real columns no query ever writes — so the count comes from the
# turns themselves. A session with no turns in it was never a conversation (the EXISTS filter).
SESSIONS_LIST = """
SELECT s.id AS id,
       s.started_at AS started_at,
       (SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) AS turns,
       (SELECT t.content FROM turns t
         WHERE t.session_id = s.id AND t.role = 'user'
         ORDER BY t.created_at, t.rowid LIMIT 1) AS opened
FROM sessions s
WHERE EXISTS (SELECT 1 FROM turns t WHERE t.session_id = s.id)
ORDER BY s.started_at DESC, s.rowid DESC
LIMIT :limit
"""

TURNS_FTS_SEARCH = """
SELECT t.content, t.session_id, t.role, t.created_at, s.started_at
FROM turns_fts f
JOIN turns t ON t.id = f.turn_id
LEFT JOIN sessions s ON s.id = t.session_id
WHERE turns_fts MATCH :query
ORDER BY rank
LIMIT :limit
"""

AUDIT_LOG_INSERT = """
INSERT INTO audit_log (session_id, action, risk_kind, approved, approver, detail)
VALUES (:session_id, :action, :risk_kind, :approved, :approver, :detail)
"""

# rowid breaks the tie: CURRENT_TIMESTAMP has one-second resolution and a busy turn writes several
# entries in the same second — without it LIMIT could drop a newer row while keeping an older one.
AUDIT_LOG_RECENT = """
SELECT session_id, action, risk_kind, approved, approver, detail, created_at
FROM audit_log
ORDER BY created_at DESC, rowid DESC
LIMIT :limit
"""

CRON_INSERT = """
INSERT INTO cronjobs (session_id, message, due_at, recurring)
VALUES (:session_id, :message, :due_at, :recurring)
"""

CRON_LIST = """
SELECT id, session_id, message, due_at, recurring, fired_at, active, created_at
FROM cronjobs WHERE active = 1 ORDER BY due_at ASC
"""

CRON_DUE = """
SELECT id, session_id, message, due_at, recurring
FROM cronjobs
WHERE active = 1 AND fired_at IS NULL AND due_at <= CURRENT_TIMESTAMP
ORDER BY due_at ASC
"""

# CLAIM, not just mark: `fired_at IS NULL` in the WHERE makes settling a compare-and-swap, so of two
# tickers reading the same due job only one gets rowcount 1 and only one delivers the reminder.
CRON_MARK_FIRED = "UPDATE cronjobs SET fired_at = CURRENT_TIMESTAMP WHERE id = :id AND fired_at IS NULL"

CRON_RESCHEDULE = "UPDATE cronjobs SET due_at = :due_at, fired_at = NULL WHERE id = :id AND fired_at IS NULL AND due_at = :was_due"

CRON_DEACTIVATE = "UPDATE cronjobs SET active = 0 WHERE id = :id"

# Recurring jobs (fired_at cleared on reschedule) are never purged.
CRON_PURGE = "DELETE FROM cronjobs WHERE (active = 0 OR fired_at IS NOT NULL) AND COALESCE(fired_at, created_at) < :cutoff"

# The conflict target is the WHOLE key: re-saving a grant refreshes its own row, and a grant of the other
# width is a different row rather than an overwrite that silently re-widens (or narrows) the first one.
APPROVED_CMD_UPSERT = """
INSERT INTO approved_commands (pattern, scope) VALUES (:pattern, :scope)
ON CONFLICT(pattern, scope) DO UPDATE SET created_at = CURRENT_TIMESTAMP
"""
APPROVED_CMD_LIST = "SELECT pattern, scope, created_at FROM approved_commands ORDER BY pattern, scope"
APPROVED_CMD_DELETE = "DELETE FROM approved_commands WHERE pattern = :pattern"
APPROVED_CMD_DELETE_SCOPED = (
    "DELETE FROM approved_commands WHERE pattern = :pattern AND scope = :scope"
)

SAVED_KEY_UPSERT = """
INSERT INTO saved_keys (name, value) VALUES (:name, :value)
ON CONFLICT(name) DO UPDATE SET value = excluded.value, created_at = CURRENT_TIMESTAMP
"""
SAVED_KEY_GET = "SELECT value FROM saved_keys WHERE name = :name"
SAVED_KEY_NAMES = "SELECT name, created_at FROM saved_keys ORDER BY name"
SAVED_KEY_DELETE = "DELETE FROM saved_keys WHERE name = :name"

PERSON_UPSERT = """
INSERT INTO discord_people (user_id, handle, display, relation)
VALUES (:user_id, :handle, :display, :relation)
ON CONFLICT(user_id) DO UPDATE SET
    -- A caller that knows only the id — the turn announcing a finished job in a DM, where there is
    -- no member to look up — must not blank a name somebody else already learned.
    handle = COALESCE(NULLIF(excluded.handle, ''), handle),
    display = COALESCE(NULLIF(excluded.display, ''), display),
    relation = excluded.relation,
    last_seen = CURRENT_TIMESTAMP
"""

PERSON_LIST = """
SELECT p.*, (SELECT COUNT(*) FROM discord_person_facts f WHERE f.user_id = p.user_id) AS facts
FROM discord_people p ORDER BY p.last_seen DESC LIMIT :limit
"""

PERSON_FACT_INSERT = """
INSERT INTO discord_person_facts (user_id, fact, source, guild_id)
VALUES (:user_id, :fact, :source, :guild_id)
"""

PERSON_FACTS = """
SELECT fact, source, created_at FROM discord_person_facts
WHERE user_id = :user_id ORDER BY created_at DESC LIMIT :limit
"""

PERSON_FACT_EXISTS = """
SELECT 1 FROM discord_person_facts
WHERE user_id = :user_id AND lower(fact) = lower(:fact) LIMIT 1
"""
