#!/usr/bin/env python3
"""Render the OSD offscreen with mock data so the design can be iterated on
without a working API token or a live desktop.

    .venv/bin/python tools/preview.py out.png [--view gauge|bars] [--scale 1.0]

NOTE: offscreen rendering uses a fallback font whose metrics differ from the
real macOS system font, so a layout that fits here can still clip on device
("text haircut", cause #3). Always confirm the final look on a real launch.
"""
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from claude_usage.collector import UsageStats  # noqa: E402
from claude_usage.config import DEFAULT_CONFIG  # noqa: E402
from claude_usage.overlay import UsageOverlay  # noqa: E402

import time  # noqa: E402


def mock_stats() -> UsageStats:
    """Numbers taken from a real ClaudeKarma reading, so the preview is
    representative rather than round."""
    now = int(time.time())
    return UsageStats(
        session_utilization=0.01,
        session_reset=now + 4 * 3600 + 50 * 60,
        weekly_utilization=0.20,
        weekly_reset=now + 3 * 86400,
        scoped_utilization=0.26,
        scoped_reset=now + 3 * 86400,
        scoped_label="Fable 5",
        subscription_type="max",
        today_cost=438.39,
        today_tokens=969853,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="preview.png")
    ap.add_argument("--view", default="gauge", choices=("gauge", "bars"))
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--theme", default=None)
    ap.add_argument("--popup", action="store_true", help="render the verbose panel instead of the OSD")
    args = ap.parse_args()

    app = QApplication(sys.argv)          # noqa: F841 - needed for QPixmap
    cfg = dict(DEFAULT_CONFIG)
    cfg["osd_view_mode"] = args.view
    cfg["osd_scale"] = args.scale
    cfg["osd_minimized"] = False
    cfg["osd_opacity"] = 1.0
    if args.theme:
        cfg["theme"] = args.theme

    if args.popup:
        from claude_usage.widget import UsagePopup
        pop = UsagePopup(cfg)
        pop.update_stats(mock_stats())
        pop.resize(pop.sizeHint())
        pop.show()
        app.processEvents()
        pm = pop.grab()
        pm.save(args.out)
        print(f"{args.out}  {pm.width()}x{pm.height()}  POPUP theme={cfg.get('theme')}")
        return 0

    osd = UsageOverlay(cfg)
    osd.update_stats(mock_stats())
    osd.resize(osd.sizeHint() if osd.sizeHint().isValid() else osd.size())
    pm = osd.grab()
    pm.save(args.out)
    print(f"{args.out}  {pm.width()}x{pm.height()}  view={args.view} scale={args.scale}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
