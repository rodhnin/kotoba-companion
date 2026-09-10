#!/usr/bin/env bash
# Run the real `kotoba setup` against a throwaway sandbox: same code, same screens, and nothing it
# writes reaches your own Kotoba home. Delete the sandbox when you are done.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Whatever interpreter has her installed. A venv at the root is the usual shape here, but a
# contributor who installed into the active environment has none, and hardcoding one sent this at a
# path that has never existed.
if [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"
elif [ -x "$ROOT/api/.venv/bin/python" ]; then PY="$ROOT/api/.venv/bin/python"
else PY="$(command -v python3)"; fi
"$PY" -c "import kotoba" 2>/dev/null || {
  echo "kotoba is not installed for $PY — run: pip install -e \"$ROOT/api[dev]\"" >&2; exit 1; }
BOX="$(mktemp -d /tmp/kotoba-setup-try.XXXXXX)"

# The whole home, not only the paths named below: setup seeds a personality file and the templates
# through the home directory itself, which has no variable of its own, so the individual overrides
# left it writing into the real one.
export KOTOBA_HOME="$BOX"
export DATABASE_URL="sqlite:///$BOX/try.db"
export KOTOBA_MEMORY_DIR="$BOX/memory"       KOTOBA_FILES_DIR="$BOX/files"
export KOTOBA_SETTINGS="$BOX/settings.yaml"  KOTOBA_MCP_CONFIG="$BOX/mcp.yaml"
export KOTOBA_PENDING_MCP="$BOX/pending.yaml" KOTOBA_PLUGINS_PATH="$BOX/plugins"
export KOTOBA_VISUAL_MEMORY_DIR="$BOX/visual" KOTOBA_KEYSTORE_KEY_FILE="$BOX/keystore.key"
export KOTOBA_TMP_DIR="$BOX/tmp"             KOTOBA_CLI_HISTORY="$BOX/history"
export KOTOBA_CLI_LOG="$BOX/cli.log"
# No key of any kind, so she asks from the top exactly as a stranger sees it.
export OPENAI_API_KEY="" XAI_API_KEY="" ELEVENLABS_API_KEY=""

echo "sandbox: $BOX  (nothing here touches your real Kotoba)"
# Leaving the wizard early is a normal way to use this, and it exits non-zero — under `set -e` that
# skipped the line below and left the sandbox behind with nothing saying where it was. The trap runs
# on every ending, including Ctrl+C.
trap 'printf "\ndone — remove it with:  rm -rf %s\n" "$BOX"' EXIT
# The CLI resolves api/.env from its OWN module path, never the working directory, so the real file
# is read wherever this is launched from. Only the keys blanked above are actually withheld: the
# provider, the model and the timezone still arrive, and a stranger's first run differs that much.
cd "$BOX"
"$PY" -m kotoba.cli setup "$@"
