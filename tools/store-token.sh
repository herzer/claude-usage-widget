#!/bin/bash
# Store a long-lived Claude Code OAuth token where the widget reads it.
#
#   1.  claude setup-token          # browser sign-in; prints an sk-ant-oat... token
#   2.  ./tools/store-token.sh      # paste it here; input is hidden, never echoed
#
# Why this exists: Claude Code 2.1.x no longer writes the Keychain item
# ("Claude Code-credentials") this widget was built to read, so that item
# went stale in July and every poll 401s. The widget's credential lookup is
# env CLAUDE_CODE_OAUTH_TOKEN -> ~/.claude/.credentials.json -> Keychain; this
# writes the middle one, in upstream's own format, so no widget code changes.
set -e
F="$HOME/.claude/.credentials.json"

printf "Paste the token from 'claude setup-token' (input hidden): "
IFS= read -rs TOK; echo
case "$TOK" in
  sk-ant-oat*) ;;
  *) echo "That does not look like a Claude OAuth token (expected sk-ant-oat...). Nothing written."; exit 1 ;;
esac

umask 077
# Token travels over stdin only -- never argv (visible in ps) or the environment.
printf '%s' "$TOK" | python3 -c '
import json, os, sys, time
tok = sys.stdin.read().strip()
f = sys.argv[1]
data = {"claudeAiOauth": {"accessToken": tok, "source": "claude setup-token",
                          "storedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")}}
tmp = f + ".tmp"
with open(tmp, "w") as fh: json.dump(data, fh)
os.chmod(tmp, 0o600); os.replace(tmp, f)
print("stored:", f, "(mode 600)")
' "$F"
unset TOK

echo "── verifying with ONE request ──"
"$(dirname "$0")/../.venv/bin/claude-usage" --once 2>/dev/null | python3 -c '
import sys, json
d = json.load(sys.stdin); e = d.get("rate_limit_error") or ""
print("  error   :", e if e else "none - DATA IS FLOWING")
print("  session :", round((d.get("session_utilization") or 0)*100, 1), "%")
print("  weekly  :", round((d.get("weekly_utilization") or 0)*100, 1), "%")
print("  scoped  :", repr(d.get("scoped_label")), round((d.get("scoped_utilization") or 0)*100, 1), "%")
print("  plan    :", repr(d.get("subscription_type")))
sys.exit(1 if e else 0)
' && echo "── the running widget picks this up on its next poll (<= 3 min); no restart needed ──"
