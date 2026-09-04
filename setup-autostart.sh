#!/bin/bash
# Diagnose the widget's plan-data access, then enable login autostart ONLY if
# it actually works. Safe to re-run.

WIDGET="/Users/herzer/Claude Usage Widget/.venv/bin/claude-usage"
PLIST="$HOME/Library/LaunchAgents/local.claude-usage-widget.plist"
LABEL="local.claude-usage-widget"

echo "── 1/3  Stored OAuth token ─────────────────────────────"
/usr/bin/security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null \
  | python3 -c '
import sys, json, time
raw = sys.stdin.read().strip()
if not raw:
    print("  x  Keychain item not readable (approve the prompt if one appears)")
    sys.exit(0)
try:
    o = json.loads(raw).get("claudeAiOauth", {})
except Exception:
    print("  x  Keychain blob did not parse as JSON")
    sys.exit(0)
print("  token found      :", bool(o.get("accessToken")))
print("  has refreshToken :", bool(o.get("refreshToken")))
print("  subscription     :", o.get("subscriptionType") or "(none)")
exp = o.get("expiresAt")
if exp:
    secs = exp / 1000 if exp > 1e12 else exp
    d = secs - time.time()
    print("  expiresAt        :", time.strftime("%Y-%m-%d %H:%M", time.localtime(secs)),
          ("-> EXPIRED %.1f h ago  <- this is the 401" % (-d / 3600)) if d < 0
          else "-> valid for %.1f h" % (d / 3600))
'

echo
echo "── 2/3  Can the widget read your plan data? ────────────"
"$WIDGET" --once 2>/dev/null | python3 -c '
import sys, json
d = json.load(sys.stdin)
err = d.get("rate_limit_error") or ""
print("  session utilization :", "%.0f%%" % ((d.get("session_utilization") or 0) * 100))
print("  weekly  utilization :", "%.0f%%" % ((d.get("weekly_utilization") or 0) * 100))
print("  today cost          : $%.2f  (local data, always works)" % (d.get("today_cost") or 0))
print("  error               :", err if err else "none - OK")
sys.exit(1 if err else 0)
'
OK=$?

echo
echo "── 3/3  Autostart ──────────────────────────────────────"
if [ "$OK" -ne 0 ]; then
  echo "  x  NOT enabling autostart - plan data is not flowing yet."
  echo "     Fix: run 'claude' once in this Terminal to refresh the OAuth"
  echo "     token, quit it, then re-run this script."
  exit 1
fi

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load "$PLIST" 2>/dev/null
if launchctl list | grep -q "$LABEL"; then
  echo "  OK  Autostart enabled - the widget launches at every login."
  echo "      Disable with: launchctl bootout gui/\$(id -u)/$LABEL"
else
  echo "  x   launchctl did not register the agent; check $PLIST"
  exit 1
fi
