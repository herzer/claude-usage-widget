#!/bin/bash
# Recover the OSD when it has been minimized and/or parked on a secondary
# display. Quits the widget, resets the display-related config, relaunches.
# Order matters: the widget rewrites config on exit, so it must die first.

CFG="$HOME/.config/claude-usage/config.json"
WIDGET="/Users/herzer/Claude Usage Widget/.venv/bin/claude-usage"

echo "Quitting the running widget..."
# Match only the Python process, NOT any shell whose command line merely
# mentions claude-usage. A bare `pkill -f claude-usage` also kills terminal
# tabs, editors and background jobs that reference the name -- it took out an
# unrelated background task the first time this script ran.
pkill -f "Python.*bin/claude-usage" 2>/dev/null
pkill -f "Python.*-m claude_usage" 2>/dev/null
for i in $(seq 1 20); do
  pgrep -f "Python.*claude.usage" >/dev/null || break
  sleep 0.25
done

echo "Resetting position / minimize state..."
python3 - "$CFG" <<'PY'
import json, sys, shutil
p = sys.argv[1]
shutil.copy(p, p + ".bak")            # keep a backup of the old layout
c = json.load(open(p))
before = {k: c.get(k) for k in ("osd_minimized","osd_position","osd_x","osd_y","osd_opacity")}
c["osd_minimized"] = False            # un-collapse the thin strip
c["osd_position"]  = "top-right"      # anchor to the PRIMARY (built-in) screen
c["osd_visible"]   = True
c["osd_opacity"]   = 1.0              # fully opaque so it cannot be missed
c.pop("osd_x", None)                  # drop the stale custom coords
c.pop("osd_y", None)
json.dump(c, open(p, "w"), indent=4, sort_keys=True)
print("  was:", before)
print("  now: minimized=False position=top-right opacity=1.0 (custom x/y cleared)")
print("  backup:", p + ".bak")
PY

echo "Relaunching (backgrounded, so this Terminal stays free)..."
"$WIDGET" --detach
sleep 2
pgrep -f "bin/claude-usage" >/dev/null \
  && echo "OK - widget running; look at the TOP-RIGHT of your built-in screen." \
  || echo "x  did not start; check ~/.cache/claude-usage/widget.log"
