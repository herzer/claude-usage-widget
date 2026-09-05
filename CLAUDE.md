# claude-usage-widget (heART fork) — pick-up notes

A macOS-first fork of [bozdemir/claude-usage-widget](https://github.com/bozdemir/claude-usage-widget)
(MIT). Read the [README](README.md) for what it does and how to install it;
[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for the blank-bars diagnosis
that cost a night to learn. `CLAUDE.local.md` (git-ignored) may hold personal
session notes.

## Layout

- `claude_usage/overlay.py` — the OSD. View modes `bars`, `gauge`, and **`strip`**
  (this fork): a menu-bar-height pill with three dials, a move handle, and a
  window-style resize grip. `strip_in_menubar` floats it above the macOS menu bar.
- `claude_usage/panel.py` — the card-based verbose panel (light + dark tokens).
- `claude_usage/menubar.py` — optional `QSystemTrayIcon` dials; hue is the label.
- `claude_usage/collector.py` — data. Honors `Retry-After`; credential lookup is
  env → `~/.claude/.credentials.json` → macOS Keychain.
- `tools/preview.py`, `tools/panel_preview.py` — offscreen renders (no API, no
  Keychain). `tools/live_mock.py` — the real windows on screen with mock stats.

## Working on it

```
python3 -m venv .venv && .venv/bin/pip install -e '.[menubar]' pytest
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/ -q
.venv/bin/python tools/live_mock.py        # look-and-feel, zero API calls
launchctl kickstart -k gui/$(id -u)/local.claude-usage-widget   # restart after edits
```

## Rules that came from pain

1. **Never poll `/api/oauth/usage` in a loop.** Its budget for authenticated
   requests is tiny and the penalty escalates to an hour. One request, then wait.
2. **Offscreen renders use a substitute font.** Verify text on a real display.
3. **A frameless Qt window on macOS is still natively resizable** — the corners
   are Cocoa resize zones. The strip clears `NSWindowStyleMaskResizable` itself.
4. **`ps -o comm=` returns a full path on macOS**; compare the basename.
5. **Qt parses 9-digit hex as `#AARRGGBB`**, not CSS `#RRGGBBAA`.
6. American English in every user-facing string; singular and plural written out.
