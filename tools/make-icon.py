#!/usr/bin/env python3
"""Draw the app icon and build the macOS .icns.

One progress ring -- the widget's own shape -- sweeping through the three
dial hues in the order the strip shows them: blue for the 5-hour window,
green for all models, violet for the model-scoped cap. Drawn natively at
every size rather than downscaled, so the 16 px version keeps its stroke.

    .venv/bin/python tools/make-icon.py
"""
from __future__ import annotations

import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QConicalGradient, QLinearGradient,  # noqa: E402
                           QPainter, QPen, QPixmap)
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "claude_usage", "icons")
FRACTION = 0.72          # how far the arc sweeps -- a pleasing, obviously-partial amount
HUES = ("#5B9BD5", "#4ade80", "#a78bfa")


def draw(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    # macOS icon grid: artwork sits inside a margin, not edge to edge.
    inset = size * 0.086
    body = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    radius = body.width() * 0.225

    ground = QLinearGradient(body.topLeft(), body.bottomRight())
    ground.setColorAt(0.0, QColor("#2e2e3a"))
    ground.setColorAt(1.0, QColor("#16161c"))
    p.setPen(Qt.NoPen)
    p.setBrush(ground)
    p.drawRoundedRect(body, radius, radius)

    # Lit top edge, clipped to the upper third: depth without a gloss.
    if size >= 32:
        p.save()
        p.setClipRect(QRectF(body.left(), body.top(), body.width(), body.height() * 0.34))
        hi = QColor(255, 255, 255, 26)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(hi, max(1.0, size * 0.004)))
        p.drawRoundedRect(body.adjusted(1, 1, -1, -1), radius - 1, radius - 1)
        p.restore()

    d = size * 0.50
    stroke = size * 0.105
    ring = QRectF((size - d) / 2, (size - d) / 2, d, d)

    track = QPen(QColor(255, 255, 255, 28), stroke)
    track.setCapStyle(Qt.FlatCap)
    p.setPen(track)
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(ring)

    # Conical gradient: Qt measures degrees counter-clockwise from 3 o'clock,
    # while the arc runs CLOCKWISE from 12. Anchor the gradient at the arc's
    # END so its stops run back along the sweep -- violet at the tip, blue at
    # the start, matching the strip's left-to-right order.
    end_angle = 90.0 - 360.0 * FRACTION
    grad = QConicalGradient(ring.center(), end_angle)
    grad.setColorAt(0.00, QColor(HUES[2]))
    grad.setColorAt(FRACTION * 0.5, QColor(HUES[1]))
    grad.setColorAt(FRACTION, QColor(HUES[0]))
    grad.setColorAt(1.00, QColor(HUES[0]))
    arc = QPen(grad, stroke)
    arc.setCapStyle(Qt.RoundCap)
    p.setPen(arc)
    p.drawArc(ring, int(90 * 16), int(-FRACTION * 360 * 16))

    # The leading tip, brightened: the eye lands where the value is.
    if size >= 64:
        tip = QColor(HUES[2]).lighter(125)
        import math
        a = math.radians(end_angle)
        c = ring.center()
        r = d / 2
        p.setPen(Qt.NoPen)
        p.setBrush(tip)
        p.drawEllipse(QPointF(c.x() + r * math.cos(a), c.y() - r * math.sin(a)),
                      stroke * 0.18, stroke * 0.18)
    p.end()
    return pm


def main() -> int:
    app = QApplication(sys.argv)  # noqa: F841
    os.makedirs(OUT_DIR, exist_ok=True)

    master = draw(1024)
    png = os.path.join(OUT_DIR, "appicon.png")
    master.save(png)

    iconset = os.path.join(OUT_DIR, "ClaudeUsage.iconset")
    os.makedirs(iconset, exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = base * scale
            name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
            draw(px).save(os.path.join(iconset, name))

    icns = os.path.join(OUT_DIR, "ClaudeUsage.icns")
    try:
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns],
                       check=True, capture_output=True)
        print(f"built: {icns}")
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"iconutil failed ({exc}); PNGs are in {iconset}", file=sys.stderr)
        return 1
    print(f"built: {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
