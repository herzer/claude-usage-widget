"""macOS menu-bar indicator: two mini ring dials for the 7-day caps.

Upstream ships no tray icon at all (see ``widget`` module docstring), so this
is additive: the OSD overlay keeps working exactly as before and the menu bar
becomes a second, always-visible surface showing the two weekly limits —
"All" (``weekly_utilization``) and the model-scoped cap (``scoped_label`` /
``scoped_utilization``, e.g. "Fable 5").

The icon is custom-painted rather than a glyph because two live percentages
cannot be expressed by any system symbol. It is NOT a template image: the
whole point is the warn/crit colour shift, which macOS would flatten to
monochrome if templated.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QSystemTrayIcon

from claude_usage.overlay import _bar_color, _hex_to_qcolor

# Logical (point) metrics. macOS gives a menu-bar item 22pt of height; drawing
# taller gets scaled down and turns to mush, so this is a ceiling, not a hint.
BAR_HEIGHT = 22.0
RING_D = 14.0          # ring outer diameter
RING_STROKE = 2.4
LABEL_GAP = 5.0        # ring -> its percentage text
GROUP_GAP = 9.0        # first dial -> second dial
EDGE_PAD = 2.0
PCT_FONT_PT = 10

# The two dials carry no text label (there is no room in a menu bar), so hue
# IS the label: the all-models dial keeps the theme's normal blue, the
# model-scoped one is violet — the same split ClaudeKarma uses. Severity still
# wins above the warn threshold, because "you are about to run out" matters
# more than "which cap is this".
# Three dials: 5-hour session, 7-day all-models, 7-day model-scoped.
DIAL_SESSION = "session"
DIAL_ALL = "all"
DIAL_SCOPED = "scoped"
DIAL_ORDER = (DIAL_SESSION, DIAL_ALL, DIAL_SCOPED)
DIAL_TITLES = {
    DIAL_SESSION: "5-hour",
    DIAL_ALL: "All models",
    DIAL_SCOPED: "Scoped",
}
# Config key per dial, so each can be switched off from the verbose panel.
DIAL_CONFIG_KEYS = {
    DIAL_SESSION: "menubar_show_session",
    DIAL_ALL: "menubar_show_all",
    DIAL_SCOPED: "menubar_show_scoped",
}
IDENTITY_HUES = {
    DIAL_SESSION: "#5B9BD5",   # blue   - the rolling 5-hour window
    DIAL_ALL: "#4ade80",       # green  - all-models weekly
    DIAL_SCOPED: "#a78bfa",    # violet - the model-scoped weekly cap
}

# Mid-tone variants for the MENU BAR specifically, legible on a light AND a
# dark bar without knowing which we are on.
#
# Detecting that is not actually possible: macOS tints the menu bar from the
# desktop picture, so a Mac in Light mode routinely has a DARK menu bar, while
# Qt's colorScheme() only reports the app appearance. Painting near-black text
# because the system says "Light" is how these dials rendered invisible. These
# hues clear both grounds, so legibility no longer depends on a guess.
MENUBAR_HUES = {
    DIAL_SESSION: "#3d8fd1",
    DIAL_ALL: "#1fa65a",
    DIAL_SCOPED: "#7c5cdb",
}
MENUBAR_WARN = "#d98a00"
MENUBAR_CRIT = "#d93a2b"


def _dial_color(pct: float, theme: dict[str, str], kind: str) -> QColor:
    """Identity hue below the warn threshold, severity hue above it.

    Hue is the only label these dials get, so it has to stay stable while the
    number is unremarkable — but once a cap is actually at risk, "you are
    running out" outranks "which cap this is".
    """
    if pct >= 0.85:
        return _hex_to_qcolor(MENUBAR_CRIT)
    if pct >= 0.6:
        return _hex_to_qcolor(MENUBAR_WARN)
    return _hex_to_qcolor(theme.get(f"dial_{kind}", MENUBAR_HUES[kind]))


def _pct_text(fraction: float) -> str:
    return f"{int(round(max(0.0, min(1.0, fraction)) * 100))}%"


def _draw_dial(
    p: QPainter, cx: float, cy: float, fraction: float,
    track: QColor, fill: QColor,
) -> None:
    """One ring: full track circle plus a clockwise arc from 12 o'clock."""
    rect = QRectF(cx - RING_D / 2, cy - RING_D / 2, RING_D, RING_D)
    pen = p.pen()
    pen.setColor(track)
    pen.setWidthF(RING_STROKE)
    pen.setCapStyle(Qt.FlatCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(rect)
    if fraction <= 0:
        return
    pen.setColor(fill)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawArc(rect, 90 * 16, -int(min(1.0, fraction) * 360 * 16))


def _menubar_is_dark() -> bool:
    """True when macOS is drawing a dark menu bar.

    The icon is deliberately NOT a template image (we need the warn/crit
    colours), which means macOS will not invert our text for us — so the
    percentage colour has to follow the system appearance by hand.
    """
    try:
        from PySide6.QtGui import QGuiApplication
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return True


def render_indicator_pixmap(
    theme: dict[str, str],
    values: "list[tuple[str, float]]",
    dpr: float = 2.0,
    dark: bool | None = None,
) -> QPixmap:
    """Paint the menu-bar pixmap for ``values`` -- ``(kind, fraction)`` pairs
    in draw order. An empty list yields a 1x1 transparent pixmap rather than a
    zero-width one, which Qt refuses to set as an icon."""
    if not values:
        pm = QPixmap(1, 1)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        return pm

    if dark is None:
        dark = _menubar_is_dark()

    font = QFont()
    font.setPointSize(PCT_FONT_PT)
    font.setBold(True)

    # Measure first so the item is exactly as wide as its content; slack
    # padding in a menu-bar item reads as misalignment next to its neighbours.
    probe = QPixmap(1, 1)
    probe.setDevicePixelRatio(1.0)
    mp = QPainter(probe)
    mp.setFont(font)
    fm = mp.fontMetrics()
    widths = [fm.horizontalAdvance(_pct_text(v)) for _, v in values]
    mp.end()

    width = EDGE_PAD * 2 + sum(RING_D + LABEL_GAP + w for w in widths) \
        + GROUP_GAP * (len(values) - 1)

    pm = QPixmap(int(width * dpr), int(BAR_HEIGHT * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    p.setFont(font)

    # Neutral mid-grey track: ~equally visible on a light or a dark bar.
    track = QColor(128, 128, 134, 105)
    cy = BAR_HEIGHT / 2

    x = EDGE_PAD
    for (kind, pct), w in zip(values, widths):
        col = _dial_color(pct, theme, kind)
        _draw_dial(p, x + RING_D / 2, cy, pct, track, col)
        x += RING_D + LABEL_GAP
        p.setPen(col)
        fm = p.fontMetrics()
        # Baseline from the cap-height centre so digits optically centre on
        # the ring, instead of sitting low the way ascent/2 does.
        p.drawText(QPointF(x, cy + fm.capHeight() / 2), _pct_text(pct))
        x += w + GROUP_GAP
    p.end()
    return pm


class MenuBarIndicator:
    """Owns the ``QSystemTrayIcon`` and repaints it from each stats update.

    Which of the three dials are drawn is user-controlled (``menubar_show_*``
    config keys, toggled from the verbose panel). A dial whose data the API is
    not reporting -- typically the model-scoped cap -- hides itself regardless
    of the toggle, matching how the OSD auto-hides its scoped bar.
    """

    def __init__(self, theme: dict[str, str], config: dict[str, Any],
                 menu=None, parent=None) -> None:
        self._theme = dict(theme)
        self._config = config
        self._tray = QSystemTrayIcon(parent)
        self._pcts: dict[str, float] = {k: 0.0 for k in DIAL_ORDER}
        self._scoped_label = ""
        self._has_scoped = False
        if menu is not None:
            self._tray.setContextMenu(menu)
        self._repaint()
        self._tray.show()

    @property
    def tray(self) -> QSystemTrayIcon:
        return self._tray

    def set_theme(self, theme: dict[str, str]) -> None:
        self._theme = dict(theme)
        self._repaint()

    def set_config(self, config: dict[str, Any]) -> None:
        """Re-read the per-dial toggles after the panel changes them."""
        self._config = config
        self._repaint()

    def update_stats(self, stats: Any) -> None:
        def frac(name: str) -> float:
            return max(0.0, min(1.0, float(getattr(stats, name, 0.0) or 0.0)))
        self._pcts = {
            DIAL_SESSION: frac("session_utilization"),
            DIAL_ALL: frac("weekly_utilization"),
            DIAL_SCOPED: frac("scoped_utilization"),
        }
        self._scoped_label = str(getattr(stats, "scoped_label", "") or "")
        self._has_scoped = bool(self._scoped_label)
        self._repaint()

    def _visible_dials(self) -> "list[tuple[str, float]]":
        out = []
        for kind in DIAL_ORDER:
            if kind == DIAL_SCOPED and not self._has_scoped:
                continue
            if not self._config.get(DIAL_CONFIG_KEYS[kind], True):
                continue
            out.append((kind, self._pcts.get(kind, 0.0)))
        return out

    def _repaint(self) -> None:
        dials = self._visible_dials()
        if not dials:
            self._tray.setVisible(False)
            return
        # Icon BEFORE visibility: Qt warns "No Icon set" if a tray item is
        # shown while still iconless, and on some platforms shows a gap.
        self._tray.setIcon(QIcon(render_indicator_pixmap(self._theme, dials)))
        self._tray.setVisible(True)
        names = {**DIAL_TITLES, DIAL_SCOPED: self._scoped_label or "Scoped"}
        self._tray.setToolTip("   ".join(
            f"{names[k]}: {_pct_text(v)}" for k, v in dials))

    def hide(self) -> None:
        self._tray.hide()
