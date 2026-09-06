#!/usr/bin/env python3
"""Build 'Restart Claude Usage.app' -- a double-clickable restarter.

The right-click menu can restart the widget while it is running; this is for
when it is not, or when you would rather click an icon. No Terminal window
opens: a .app bundle whose executable is a shell script runs headless.

    python3 tools/make-restart-app.py [destination-folder]

Then drag the app to your Dock. It is safe to run twice.
"""
import os
import stat
import sys

NAME = "Restart Claude Usage"
LABEL = "local.claude-usage-widget"

INFO = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>{name}</string>
    <key>CFBundleDisplayName</key><string>{name}</string>
    <key>CFBundleIdentifier</key><string>app.heartapps.claude-usage-restart</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>restart</string>
    <!-- Agent: no Dock icon, no menu bar, no window -- it just acts and exits. -->
    <key>LSUIElement</key><true/>
</dict>
</plist>
"""

SCRIPT = """#!/bin/sh
# Restart the Claude usage widget. Under launchd one command does it;
# otherwise stop any running copy, wait for the single-instance lock to be
# released, and start a fresh one.
LABEL="{label}"
WIDGET="{widget}"
TARGET="gui/$(id -u)/$LABEL"

if launchctl print "$TARGET" >/dev/null 2>&1; then
    launchctl kickstart -k "$TARGET"
    exit 0
fi

# Not launchd-owned. Match the Python process only -- a shell whose command
# line merely mentions the name must not be killed.
for pid in $(pgrep -f -- "-m claude_usage|bin/claude-usage" 2>/dev/null); do
    case "$(basename "$(ps -o comm= -p "$pid" 2>/dev/null)")" in
        Python|python3|python) kill "$pid" 2>/dev/null ;;
    esac
done
for _ in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -f -- "-m claude_usage" >/dev/null 2>&1 || break
    sleep 0.3
done
[ -x "$WIDGET" ] || exit 1
mkdir -p "$HOME/.cache/claude-usage"
"$WIDGET" --detach >> "$HOME/.cache/claude-usage/widget.log" 2>&1
"""


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    widget = os.path.join(root, ".venv", "bin", "claude-usage")
    dest = sys.argv[1] if len(sys.argv) > 1 else root
    app = os.path.join(os.path.expanduser(dest), f"{NAME}.app")
    macos = os.path.join(app, "Contents", "MacOS")
    os.makedirs(macos, exist_ok=True)

    with open(os.path.join(app, "Contents", "Info.plist"), "w") as fh:
        fh.write(INFO.format(name=NAME))
    exe = os.path.join(macos, "restart")
    with open(exe, "w") as fh:
        fh.write(SCRIPT.format(label=LABEL, widget=widget))
    os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"built: {app}")
    print("       drag it to your Dock; double-click restarts the widget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
