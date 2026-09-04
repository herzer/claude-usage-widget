#!/usr/bin/env python3
"""Show the strip OSD + verbose panel ON SCREEN with mock stats, no collector.

A design surface: zero API calls, zero Keychain reads, so it can run while
the real widget is deliberately silent to let a rate-limit window drain.
Reads the user's real config so theme, scale, position and light/dark match.
Bypasses the single-instance lock (no cli entry point), so it coexists with
the real widget -- close it with:  pkill -f tools/live_mock.py
"""
import os, random, sys, time
from PySide6.QtWidgets import QApplication
from claude_usage.collector import UsageStats
from claude_usage.config import load_config
from claude_usage.overlay import UsageOverlay, VIEW_MODE_STRIP
from claude_usage.panel import HeartPanel


def mock() -> UsageStats:
    now = time.time(); rnd = random.Random(7)
    grid = []
    for d in range(7):
        for h in range(24):
            v = 0.0
            if 8 <= h <= 20 and d < 6:
                v = rnd.uniform(0.15, 0.95) if rnd.random() > 0.25 else 0.0
            elif rnd.random() > 0.85:
                v = rnd.uniform(0.05, 0.3)
            grid.append(v)
    return UsageStats(
        session_utilization=0.04, session_reset=int(now + 4 * 3600 + 36 * 60),
        weekly_utilization=0.01, weekly_reset=int(now + 6 * 86400),
        scoped_utilization=0.02, scoped_reset=int(now + 6 * 86400), scoped_label="Fable 5",
        subscription_type="max", today_cost=523.28, today_tokens=1_810_010,
        week_hour_grid=grid, week_hour_days=[int(now - (6 - i) * 86400) for i in range(7)],
        daily_heatmap=[rnd.uniform(0, 1) if rnd.random() > 0.3 else 0.0 for _ in range(91)],
    )


app = QApplication(sys.argv)
cfg = dict(load_config(os.path.expanduser("~/.config/claude-usage/config.json")))
cfg["osd_view_mode"] = VIEW_MODE_STRIP
cfg["osd_minimized"] = False
cfg["osd_position"] = "top-right"     # in menu-bar mode this means IN the bar, right
stats = mock()

osd = UsageOverlay(cfg); osd.update_stats(stats); osd.show()
panel = HeartPanel(cfg); panel.update_stats(stats); panel.resize(panel.sizeHint())
# The real app wires this in ClaudeUsageApp._on_panel_appearance; the mock
# bypasses the app, so it has to make the same connection itself.
panel.appearanceChanged.connect(osd.set_strip_dark)
# Park the panel next to the strip, clamped onto the strip's screen -- below
# it if that fits, otherwise above, never hanging off the bottom edge.
g = osd.frameGeometry()
scr = (app.screenAt(g.center()) or app.primaryScreen()).availableGeometry()
x = min(max(scr.left(), g.right() - panel.width()), scr.right() - panel.width())
y = g.bottom() + 12
if y + panel.height() > scr.bottom():
    y = g.top() - 12 - panel.height()
y = max(scr.top(), y)
panel.move(x, y); panel.show()
print(f"live mock up: strip {osd.width()}x{osd.height()} at {g.x()},{g.y()}; panel {panel.width()}x{panel.height()}", flush=True)
sys.exit(app.exec())
