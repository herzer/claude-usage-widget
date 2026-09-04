#!/usr/bin/env python3
"""Render HeartPanel in light and dark with representative mock data."""
import os, random, time, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap, QPainter, QColor

from claude_usage.collector import UsageStats
from claude_usage.config import DEFAULT_CONFIG
from claude_usage.panel import HeartPanel


def mock():
    now = time.time()
    rnd = random.Random(7)
    # A plausible working week: busy 09-19 on weekdays, quiet at night.
    grid = []
    for d in range(7):
        for h in range(24):
            base = 0.0
            if 8 <= h <= 20 and d < 6:
                base = rnd.uniform(0.15, 0.95) if rnd.random() > 0.25 else 0.0
            elif rnd.random() > 0.85:
                base = rnd.uniform(0.05, 0.3)
            grid.append(base)
    days = [now - (6 - i) * 86400 for i in range(7)]
    return UsageStats(
        session_utilization=float(os.environ.get('MOCK_SESSION', 0.02)), session_reset=int(now + 4 * 3600 + 42 * 60),
        weekly_utilization=float(os.environ.get('MOCK_WEEKLY', 0.20)), weekly_reset=int(now + 3 * 86400),
        scoped_utilization=float(os.environ.get('MOCK_SCOPED', 0.26)), scoped_label="Fable 5",
        subscription_type="max", today_cost=438.39,
        week_hour_grid=grid, week_hour_days=[int(d) for d in days],
        daily_heatmap=[rnd.uniform(0, 1) if rnd.random() > 0.3 else 0.0 for _ in range(91)],
    )


app = QApplication(sys.argv)
shots = []
for dark in (True, False):
    cfg = dict(DEFAULT_CONFIG); cfg["panel_dark"] = dark
    p = HeartPanel(cfg)
    p.update_stats(mock())
    p.resize(p.sizeHint())
    p.show(); app.processEvents()
    shots.append(p.grab())

w = max(s.width() for s in shots); h = max(s.height() for s in shots)
out = QPixmap(w * 2 + 24, h); out.fill(QColor("#8a8a94"))
pt = QPainter(out)
pt.drawPixmap(0, 0, shots[0]); pt.drawPixmap(w + 24, 0, shots[1])
pt.end()
out.save(sys.argv[1])
print(f"{sys.argv[1]}  panel {shots[0].width()}x{shots[0].height()}  (dark | light)")
