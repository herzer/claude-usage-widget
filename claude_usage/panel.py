"""Verbose panel, card-based, light + dark.

Replaces the stacked-label popup with a card layout: a big
5-hour ring card beside a 7-day limits card, an activity heatmap with a
Week/Month switch, and the toggles that decide which dials the menu bar shows.

Design rules being honoured here (house rules, not preference):
  * light AND dark are paired tokens -- neither is an afterthought;
  * ONE declared control height, never a height left to emerge from padding;
  * singular/plural written out, never "(s)";
  * no data stored in a display string -- the dial toggles key off enum-ish
    dial ids from `menubar`, never off the visible label.
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QPaintEvent, QPen,
)
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from claude_usage.menubar import (
    DIAL_ALL, DIAL_CONFIG_KEYS, DIAL_ORDER, DIAL_SCOPED, DIAL_SESSION,
    IDENTITY_HUES,
)

# --- Declared control height (see house rule) -------------------------------
DIAL_LABELS = {
    DIAL_SESSION: "5-hour",
    DIAL_ALL: "All models",
    # Generic on purpose: the API names this cap (today "Fable 5"), and it is
    # whichever model-scoped weekly you are closest to hitting -- not a fixed
    # model. "Scoped" was jargon that made users ask what it meant.
    DIAL_SCOPED: "Model cap",
}

CONTROL_H = 26
CONTROL_H_SM = 22

PANEL_W = 420
CARD_RADIUS = 14
CARD_PAD = 14
GUTTER = 12


# --- Paired tokens. Every colour is defined in BOTH maps, never only one. ----
DARK = {
    "bg":        "#1e1e24",   # a touch lighter than upstream's near-black
    "card":      "#26262e",
    "card_top":  "#2e2e38",   # top of the surface gradient
    "card_edge": "#33333d",
    "edge_hi":   "#ffffff",   # lit top edge; alpha applied separately
    "edge_hi_a": "20",       # Qt parses #AARRGGBB, NOT CSS #RRGGBBAA -- never inline it
    "track":     "#35353f",
    "text":      "#f1f1f5",
    "text_2":    "#a2a2b0",
    "text_dim":  "#71717f",
    "warn":      "#f5a524",
    "crit":      "#f04438",
    "heat_0":    "#2a2a33",
}
LIGHT = {
    "bg":        "#f5f5f8",
    "card":      "#fbfbfd",
    "card_top":  "#ffffff",
    "card_edge": "#e4e4ea",
    "edge_hi":   "#ffffff",
    "edge_hi_a": "210",
    "track":     "#e9e9ef",
    "text":      "#1b1b21",
    "text_2":    "#5c5c6a",
    "text_dim":  "#8c8c9a",
    "warn":      "#b26a00",
    "crit":      "#c0392f",
    "heat_0":    "#eaeaf0",
}
# Accents are shared but darkened for light mode so they clear 4.5:1 on white.
ACCENTS_DARK = dict(IDENTITY_HUES)
ACCENTS_LIGHT = {DIAL_SESSION: "#2f6fae", DIAL_ALL: "#1a8f4a", DIAL_SCOPED: "#6d43cf"}


def tokens(dark: bool) -> dict[str, str]:
    t = dict(DARK if dark else LIGHT)
    for k, v in (ACCENTS_DARK if dark else ACCENTS_LIGHT).items():
        t[f"accent_{k}"] = v
    return t


def accent_for(kind: str, pct: float, t: dict[str, str]) -> QColor:
    """Identity hue normally, severity hue once the cap is actually at risk."""
    if pct >= 0.85:
        return QColor(t["crit"])
    if pct >= 0.60:
        return QColor(t["warn"])
    return QColor(t[f"accent_{kind}"])


def _font(pt: float, weight: int = QFont.Normal, caps: bool = False) -> QFont:
    f = QFont()
    f.setPointSizeF(pt)
    f.setWeight(weight)
    if caps:
        f.setCapitalization(QFont.AllUppercase)
        f.setLetterSpacing(QFont.PercentageSpacing, 108)
    return f


class Card(QFrame):
    """Rounded surface every section sits on."""

    def __init__(self, t: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self._t = t
        self._live = True
        self.setAttribute(Qt.WA_StyledBackground, False)

    def set_tokens(self, t: dict[str, str]) -> None:
        self._t = t
        self.update()

    def set_live(self, live: bool) -> None:
        """Not live: accents go gray so last-known values read as such."""
        self._live = bool(live)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        t = self._t
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        # A shallow top-down gradient reads as a lit surface rather than a
        # flat swatch; it is deliberately narrow in range so it never looks
        # like a "glossy" 2010 gradient.
        grad = QLinearGradient(0, r.top(), 0, r.bottom())
        grad.setColorAt(0.0, QColor(t.get("card_top", t["card"])))
        grad.setColorAt(1.0, QColor(t["card"]))
        p.setBrush(grad)
        p.setPen(QPen(QColor(t["card_edge"]), 1))
        p.drawRoundedRect(r, CARD_RADIUS, CARD_RADIUS)
        # Lit top edge: one hairline inside the border, clipped to the top
        # third so it reads as a highlight, not a second border.
        hi = QColor(t.get("edge_hi", "#ffffff"))
        hi.setAlpha(int(t.get("edge_hi_a", "0")))
        if hi.alpha():
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(hi, 1))
            inner = r.adjusted(1, 1, -1, -1)
            p.save()
            p.setClipRect(QRectF(inner.left(), inner.top(),
                                 inner.width(), inner.height() * 0.34))
            p.drawRoundedRect(inner, CARD_RADIUS - 1, CARD_RADIUS - 1)
            p.restore()
        p.end()


class RingCard(Card):
    """The 5-hour limit: one large ring, the number, and the reset line."""

    RING_D = 96
    RING_STROKE = 8

    def __init__(self, t: dict[str, str], parent=None) -> None:
        super().__init__(t, parent)
        self._pct = 0.0
        self._reset_text = ""
        self.setMinimumSize(160, 196)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_value(self, pct: float, reset_text: str) -> None:
        self._pct = max(0.0, min(1.0, pct))
        self._reset_text = reset_text
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        t = self._t
        cx = self.width() / 2
        cy = CARD_PAD + self.RING_D / 2 + 6
        rect = QRectF(cx - self.RING_D / 2, cy - self.RING_D / 2,
                      self.RING_D, self.RING_D)

        pen = QPen(QColor(t["track"]), self.RING_STROKE)
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(rect)

        if self._pct > 0:
            col = accent_for(DIAL_SESSION, self._pct, t) if self._live else QColor(t["text_dim"])
            # Glow: the same arc drawn a few times underneath, wider and
            # fainter each pass. Cheaper than a blur and it survives being
            # drawn on either a light or a dark surface.
            span = -int(self._pct * 360 * 16)
            for grow, alpha in ((10, 26), (6, 34), (3, 44)):
                g = QColor(col)
                g.setAlpha(alpha)
                gp = QPen(g, self.RING_STROKE + grow)
                gp.setCapStyle(Qt.RoundCap)
                p.setPen(gp)
                p.drawArc(rect, 90 * 16, span)
            # A gradient across the arc reads as depth without a drop shadow,
            # which is what makes this feel less flat than a solid stroke.
            grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            grad.setColorAt(0.0, col.lighter(126))
            grad.setColorAt(1.0, col)
            fill = QPen(grad, self.RING_STROKE)
            fill.setCapStyle(Qt.RoundCap)
            p.setPen(fill)
            p.drawArc(rect, 90 * 16, -int(self._pct * 360 * 16))

        # Percentage: the headline. The % sign is deliberately smaller and
        # dimmer so the digits carry the glance.
        num = f"{int(round(self._pct * 100))}"
        p.setFont(_font(30, QFont.DemiBold))
        fmn = p.fontMetrics()
        p.setFont(_font(14, QFont.Medium))
        fms = p.fontMetrics()
        total = fmn.horizontalAdvance(num) + 2 + fms.horizontalAdvance("%")
        x = cx - total / 2
        p.setFont(_font(30, QFont.DemiBold))
        p.setPen(QColor(t["text"]))
        p.drawText(QPointF(x, cy + fmn.capHeight() / 2), num)
        p.setFont(_font(14, QFont.Medium))
        p.setPen(QColor(t["text_2"]))
        p.drawText(QPointF(x + fmn.horizontalAdvance(num) + 2,
                           cy + fmn.capHeight() / 2), "%")

        y = cy + self.RING_D / 2 + 26
        p.setFont(_font(9.5, QFont.DemiBold, caps=True))
        p.setPen(QColor(t["text_2"]))
        fm = p.fontMetrics()
        label = "5-hour limit"
        p.drawText(QPointF(cx - fm.horizontalAdvance(label) / 2, y), label)

        if self._reset_text:
            p.setFont(_font(11))
            p.setPen(QColor(t["text_dim"]))
            fm = p.fontMetrics()
            p.drawText(QPointF(cx - fm.horizontalAdvance(self._reset_text) / 2,
                               y + 20), self._reset_text)
        p.end()


class LimitsCard(Card):
    """The 7-day caps: all-models plus the model-scoped one, as labelled bars."""

    BAR_H = 8

    def __init__(self, t: dict[str, str], parent=None) -> None:
        super().__init__(t, parent)
        self._reset_ts = 0
        self._rows: list[tuple[str, str, float]] = []   # (kind, label, pct)
        self.setMinimumSize(212, 196)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_values(self, rows, reset_ts: int) -> None:
        """Takes the raw timestamp, not a formatted string: the card decides
        the long or short form by measuring, so it never has to parse a
        label back to shorten it."""
        self._rows = list(rows)
        self._reset_ts = int(reset_ts or 0)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        t = self._t
        x0, x1 = CARD_PAD, self.width() - CARD_PAD

        p.setFont(_font(9.5, QFont.DemiBold, caps=True))
        p.setPen(QColor(t["text_2"]))
        p.drawText(QPointF(x0, CARD_PAD + 10), "7-day limits")
        hdr_end = x0 + p.fontMetrics().horizontalAdvance("7-DAY LIMITS") + 10
        if self._reset_ts:
            # Header always wins. Try "Resets Mon 23:38"; if that would run
            # into the header, fall back to "Mon 23:38"; if even that does
            # not fit, draw nothing rather than draw a collision.
            p.setFont(_font(10))
            fm = p.fontMetrics()
            for text in (_fmt_at(self._reset_ts), _fmt_at_short(self._reset_ts)):
                tw = fm.horizontalAdvance(text)
                if x1 - tw >= hdr_end:
                    p.setPen(QColor(t["text_dim"]))
                    p.drawText(QPointF(x1 - tw, CARD_PAD + 10), text)
                    break

        y = CARD_PAD + 44
        for kind, label, pct in self._rows:
            p.setFont(_font(12.5, QFont.DemiBold))
            p.setPen(QColor(t["text"]))
            p.drawText(QPointF(x0, y), label)
            pct_s = f"{int(round(pct * 100))}%"
            fm = p.fontMetrics()
            p.setPen(QColor(t["text_2"]))
            p.drawText(QPointF(x1 - fm.horizontalAdvance(pct_s), y), pct_s)

            by = y + 12
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t["track"]))
            p.drawRoundedRect(QRectF(x0, by, x1 - x0, self.BAR_H),
                              self.BAR_H / 2, self.BAR_H / 2)
            if pct > 0:
                col = accent_for(kind, pct, t) if self._live else QColor(t["text_dim"])
                grad = QLinearGradient(x0, 0, x1, 0)
                grad.setColorAt(0.0, col.darker(118))
                grad.setColorAt(1.0, col.lighter(118))
                w = max((x1 - x0) * pct, self.BAR_H)
                # Soft halo at the leading edge, so the bar looks like it is
                # emitting rather than just ending.
                halo = QColor(col); halo.setAlpha(58)
                p.setBrush(halo)
                p.drawRoundedRect(QRectF(x0, by - 2, w, self.BAR_H + 4),
                                  (self.BAR_H + 4) / 2, (self.BAR_H + 4) / 2)
                p.setBrush(grad)
                p.drawRoundedRect(QRectF(x0, by, w, self.BAR_H),
                                  self.BAR_H / 2, self.BAR_H / 2)
            y += 56
        p.end()


class HeatmapCard(Card):
    """Activity grid. ``buckets`` is a list of 0..1 intensities, row-major."""

    CELL_MAX = 12
    CELL_GAP = 3
    GUTTER_L = 30

    def __init__(self, t: dict[str, str], rows: int = 7, cols: int = 24,
                 parent=None) -> None:
        super().__init__(t, parent)
        self._rows, self._cols = rows, cols
        self._buckets: list[float] = []
        self._row_labels: list[str] = []
        self._col_labels: list[tuple[int, str]] = []
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(self._needed_height())

    def _cell(self) -> float:
        """Cell size that fills the card width for the current column count,
        capped so a 13-week Month grid does not balloon."""
        avail = self.width() - 2 * CARD_PAD - self.GUTTER_L
        c = (avail - (self._cols - 1) * self.CELL_GAP) / max(1, self._cols)
        return max(7.0, min(float(self.CELL_MAX), c))

    def _needed_height(self) -> int:
        return int(CARD_PAD * 2 + 34 + self._rows * (self.CELL_MAX + self.CELL_GAP))

    def set_grid(self, buckets, row_labels, col_labels) -> None:
        self._buckets = list(buckets)
        self._row_labels = list(row_labels)
        self._col_labels = list(col_labels)
        self.setMinimumHeight(self._needed_height())
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        t = self._t
        cell = self._cell()
        pitch = cell + self.CELL_GAP
        x0 = CARD_PAD + self.GUTTER_L
        y0 = CARD_PAD + 30

        p.setFont(_font(8.5))
        p.setPen(QColor(t["text_dim"]))
        for idx, text in self._col_labels:
            if idx < self._cols:
                p.drawText(QPointF(x0 + idx * pitch, y0 - 8), text)

        base = QColor(t["heat_0"])
        accent = QColor(t["accent_" + DIAL_ALL])
        for r in range(self._rows):
            if r < len(self._row_labels):
                p.setFont(_font(8.5))
                p.setPen(QColor(t["text_dim"]))
                p.drawText(QPointF(CARD_PAD, y0 + r * pitch + cell - 1),
                           self._row_labels[r])
            for c in range(self._cols):
                i = r * self._cols + c
                v = self._buckets[i] if i < len(self._buckets) else 0.0
                if v is None:
                    continue          # no such day (before the first Monday)
                col = QColor(base)
                if v > 0:
                    # Blend toward the accent; alpha alone would wash out on
                    # the light card, so interpolate the channels instead.
                    f = 0.18 + 0.82 * min(1.0, v)
                    col = QColor(
                        int(base.red() + (accent.red() - base.red()) * f),
                        int(base.green() + (accent.green() - base.green()) * f),
                        int(base.blue() + (accent.blue() - base.blue()) * f),
                    )
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                p.drawRoundedRect(
                    QRectF(x0 + c * pitch, y0 + r * pitch, cell, cell), 2.5, 2.5)
        p.end()


class Segmented(QWidget):
    """Two-state mode switch (Week | Month).

    A segmented control is correct here and NOT the wrong choice it would be
    for navigation: these are two views of one thing, not two places to go.
    """

    changed = Signal(str)

    def __init__(self, options: list[str], t: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self._t = t
        self._buttons: list[QPushButton] = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for i, opt in enumerate(options):
            b = QPushButton(opt)
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setFixedHeight(CONTROL_H_SM)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, o=opt: self.changed.emit(o))
            group.addButton(b)
            lay.addWidget(b)
            self._buttons.append(b)
        self.set_tokens(t)

    def set_tokens(self, t: dict[str, str]) -> None:
        self._t = t
        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none; border-radius: 6px;
                padding: 0 11px; color: {t['text_dim']}; font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:checked {{ background: {t['track']}; color: {t['text']}; }}
            QPushButton:hover:!checked {{ color: {t['text_2']}; }}
        """)


class Stepper(QWidget):
    """The house stepper: ``− value +`` in one rounded field, value in
    italic. Click ± (hold to repeat), or scroll over it. It replaces the
    slider: scrolling sweeps just as well, and ± still lands on an exact
    value."""

    valueChanged = Signal(int)

    def __init__(self, value: int, lo: int, hi: int, step: int, suffix: str,
                 t: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self._v, self._lo, self._hi, self._step, self._suffix = int(value), lo, hi, step, suffix
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._minus = QPushButton("−"); self._plus = QPushButton("+")
        for b, d in ((self._minus, -1), (self._plus, 1)):
            b.setFixedSize(CONTROL_H_SM, CONTROL_H_SM)
            b.setAutoRepeat(True); b.setAutoRepeatDelay(350); b.setAutoRepeatInterval(60)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, dd=d: self.set_value(self._v + dd * self._step))
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setFixedHeight(CONTROL_H_SM)
        self._label.setMinimumWidth(52)
        lay.addWidget(self._minus); lay.addWidget(self._label); lay.addWidget(self._plus)
        self.set_tokens(t)
        self._render()

    def value(self) -> int:
        return self._v

    def set_value(self, v: int, emit: bool = True) -> None:
        v = max(self._lo, min(self._hi, int(v)))
        if v == self._v:
            return
        self._v = v
        self._render()
        if emit:
            self.valueChanged.emit(v)

    def wheelEvent(self, event) -> None:
        d = event.angleDelta().y()
        if d:
            self.set_value(self._v + (self._step if d > 0 else -self._step))
        event.accept()

    def _render(self) -> None:
        self._label.setText(f"{self._v}{self._suffix}")

    def set_tokens(self, t: dict[str, str]) -> None:
        self.setStyleSheet(f"""
            QPushButton {{ background: {t['track']}; color: {t['text_2']}; border: none;
                          font-size: 13px; font-weight: 600; padding: 0; }}
            QPushButton:first-child {{ border-top-left-radius: 6px; border-bottom-left-radius: 6px; }}
            QPushButton:last-child  {{ border-top-right-radius: 6px; border-bottom-right-radius: 6px; }}
            QPushButton:hover {{ color: {t['text']}; }}
            QLabel {{ background: {t['track']}; color: {t['text']}; font-size: 11px;
                     font-style: italic; border-left: 1px solid {t['card_edge']};
                     border-right: 1px solid {t['card_edge']}; }}
        """)


class AppearanceZone(QWidget):
    """The strip's edit zone: background opacity, contrast, hover-solid --
    for the appearance the panel is currently showing. Values are stored
    per appearance, so tuning dark never touches light."""

    changed = Signal(bool, str, int)      # (dark, key, value)

    def __init__(self, config: dict[str, Any], t: dict[str, str], dark: bool, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._dark = dark
        # Two rows, so the zone never widens the panel past what the
        # activity grid needs: steppers on the first, the hover switch on
        # the second. Every control shares CONTROL_H_SM.
        from PySide6.QtWidgets import QGridLayout
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setHorizontalSpacing(10)
        lay.setVerticalSpacing(6)
        self._bg = Stepper(100, 0, 100, 5, " %", t)
        self._ct = Stepper(50, 0, 100, 5, "", t)
        self._hover = QCheckBox("Solid while the pointer is over it")
        self._hover.setFixedHeight(CONTROL_H_SM)
        self._hover.setCursor(Qt.PointingHandCursor)
        for col, (label, w) in enumerate((("Background", self._bg), ("Contrast", self._ct))):
            lab = QLabel(label); lab.setObjectName("cap")
            lay.addWidget(lab, 0, col * 2, Qt.AlignVCenter)
            lay.addWidget(w, 0, col * 2 + 1, Qt.AlignVCenter)
        lay.addWidget(self._hover, 1, 0, 1, 4)
        lay.setColumnStretch(4, 1)
        self._bg.valueChanged.connect(lambda v: self.changed.emit(self._dark, "bg", v))
        self._ct.valueChanged.connect(lambda v: self.changed.emit(self._dark, "contrast", v))
        self._hover.toggled.connect(lambda on: self.changed.emit(self._dark, "hover", int(on)))
        self.set_appearance(dark)
        self.set_tokens(t)

    def set_appearance(self, dark: bool) -> None:
        """Show the values for this appearance; never emits."""
        self._dark = bool(dark)
        suffix = "dark" if dark else "light"
        self._bg.set_value(int(self._config.get(f"strip_bg_opacity_{suffix}", 100) or 100), emit=False)
        self._ct.set_value(int(self._config.get(f"strip_contrast_{suffix}", 50) or 0), emit=False)
        self._hover.blockSignals(True)
        self._hover.setChecked(bool(self._config.get("strip_hover_solid", True)))
        self._hover.blockSignals(False)

    def set_tokens(self, t: dict[str, str]) -> None:
        for w in (self._bg, self._ct):
            w.set_tokens(t)
        self.setStyleSheet(f"""
            QLabel#cap {{ color: {t['text_dim']}; font-size: 11px; font-weight: 600; }}
            QCheckBox {{ color: {t['text_2']}; font-size: 11px; spacing: 6px; }}
            QCheckBox::indicator {{ width: 12px; height: 12px; border-radius: 4px;
                border: 1.5px solid {t['track']}; background: transparent; }}
            QCheckBox::indicator:checked {{ background: {t['accent_' + DIAL_ALL]}; border-color: {t['accent_' + DIAL_ALL]}; }}
        """)


class DialToggles(QWidget):
    """Which of the three dials the menu bar shows."""

    toggled = Signal(str, bool)

    def __init__(self, config: dict[str, Any], t: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self._t = t
        self._boxes: dict[str, QCheckBox] = {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        cap = QLabel("Menu bar")
        cap.setObjectName("cap")
        lay.addWidget(cap)
        for kind in DIAL_ORDER:
            cb = QCheckBox(DIAL_LABELS[kind])
            cb.setChecked(bool(config.get(DIAL_CONFIG_KEYS[kind], True)))
            cb.setFixedHeight(CONTROL_H_SM)
            cb.setCursor(Qt.PointingHandCursor)
            cb.toggled.connect(lambda on, k=kind: self.toggled.emit(k, on))
            self._boxes[kind] = cb
            lay.addWidget(cb)
        lay.addStretch(1)
        self.set_tokens(t)

    def set_scoped_label(self, label: str) -> None:
        """Rename the scoped checkbox to the cap the API is reporting.

        The label is derived FROM the dial id, never read back to decide
        anything -- the toggle keys off DIAL_SCOPED regardless of wording.
        """
        self._boxes[DIAL_SCOPED].setText(label or DIAL_LABELS[DIAL_SCOPED])

    def set_scoped_available(self, available: bool) -> None:
        self._boxes[DIAL_SCOPED].setEnabled(available)

    def set_tokens(self, t: dict[str, str]) -> None:
        self._t = t
        dots = "".join(
            f"""QCheckBox#dial_{k}::indicator:checked {{ background: {t['accent_' + k]};
                 border-color: {t['accent_' + k]}; }}"""
            for k in DIAL_ORDER)
        for k, cb in self._boxes.items():
            cb.setObjectName(f"dial_{k}")
        self.setStyleSheet(f"""
            QLabel#cap {{ color: {t['text_dim']}; font-size: 11px; font-weight: 600; }}
            QCheckBox {{ color: {t['text_2']}; font-size: 11px; spacing: 6px; }}
            QCheckBox:disabled {{ color: {t['text_dim']}; }}
            QCheckBox::indicator {{
                width: 12px; height: 12px; border-radius: 4px;
                border: 1.5px solid {t['track']}; background: transparent;
            }}
            {dots}
        """)



def _fmt_in(reset_ts: int, now: float | None = None) -> str:
    """"Resets in 4h 42m". Singular and plural are written out, never "(s)"."""
    import time as _t
    if not reset_ts:
        return ""
    secs = int(reset_ts - (now if now is not None else _t.time()))
    if secs <= 0:
        return "Resetting now"
    h, m = secs // 3600, (secs % 3600) // 60
    if h >= 24:
        d = h // 24
        return "Resets in 1 day" if d == 1 else f"Resets in {d} days"
    if h and m:
        return f"Resets in {h}h {m}m"
    if h:
        return "Resets in 1 hour" if h == 1 else f"Resets in {h} hours"
    return "Resets in 1 minute" if m == 1 else f"Resets in {m} minutes"


def _fmt_at(reset_ts: int) -> str:
    """"Resets Thu 05:59"."""
    import time as _t
    if not reset_ts:
        return ""
    return _t.strftime("Resets %a %H:%M", _t.localtime(reset_ts))


def _fmt_at_short(reset_ts: int) -> str:
    """"Thu 05:59" -- for when the long form will not fit."""
    import time as _t
    if not reset_ts:
        return ""
    return _t.strftime("%a %H:%M", _t.localtime(reset_ts))


class HeartPanel(QWidget):
    """The verbose panel: ring card, limits card, activity grid, dial toggles."""

    dialToggled = Signal(str, bool)
    appearanceChanged = Signal(bool)      # True = dark
    scopedLabelSeen = Signal(str)         # persist the API's name for the cap
    stripStyleChanged = Signal(bool, str, int)   # (dark, 'bg'|'contrast'|'hover', value)

    def __init__(self, config: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._config = dict(config)
        self._dark = bool(config.get("panel_dark", True))
        self._t = tokens(self._dark)
        self._mode = "Week"
        self._stats: Any = None
        self.setWindowTitle("Claude Usage")
        self.setMinimumWidth(PANEL_W)

        root = QVBoxLayout(self)
        root.setContentsMargins(GUTTER, GUTTER, GUTTER, GUTTER)
        root.setSpacing(GUTTER)

        # --- header -------------------------------------------------------
        head = QHBoxLayout()
        head.setSpacing(8)
        self._title = QLabel("Claude Usage")
        self._title.setObjectName("title")
        self._plan = QLabel("")
        self._plan.setObjectName("plan")
        self._plan.setFixedHeight(CONTROL_H_SM)
        self._plan.setAlignment(Qt.AlignCenter)
        self._appearance = QPushButton("Light")
        self._appearance.setFixedHeight(CONTROL_H_SM)
        self._appearance.setCursor(Qt.PointingHandCursor)
        self._appearance.clicked.connect(self._flip_appearance)
        head.addWidget(self._title)
        head.addWidget(self._plan)
        head.addStretch(1)
        head.addWidget(self._appearance)
        root.addLayout(head)

        # Link status: always visible, because the numbers below are shown
        # even when they are last-known values -- this line is what tells
        # them apart.
        self._status = QLabel("")
        self._status.setObjectName("status")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        # --- ring + limits ------------------------------------------------
        cards = QHBoxLayout()
        cards.setSpacing(GUTTER)
        self._ring = RingCard(self._t)
        self._limits = LimitsCard(self._t)
        cards.addWidget(self._ring, 42)
        cards.addWidget(self._limits, 58)
        root.addLayout(cards)

        # --- activity -----------------------------------------------------
        act_head = QHBoxLayout()
        self._act_label = QLabel("Activity")
        self._act_label.setObjectName("section")
        self._seg = Segmented(["Week", "Month"], self._t)
        self._seg.changed.connect(self._set_mode)
        act_head.addWidget(self._act_label)
        act_head.addStretch(1)
        act_head.addWidget(self._seg)
        root.addLayout(act_head)

        self._heat = HeatmapCard(self._t)
        root.addWidget(self._heat)

        # --- dial toggles -------------------------------------------------
        self._dials = DialToggles(self._config, self._t)
        self._dials.toggled.connect(self.dialToggled.emit)
        root.addWidget(self._dials)

        # --- strip appearance edit zone (follows the panel's appearance) ---
        zone_head = QLabel("Strip appearance")
        zone_head.setObjectName("section")
        root.addWidget(zone_head)
        self._zone = AppearanceZone(self._config, self._t, self._dark)
        self._zone.changed.connect(self.stripStyleChanged.emit)
        root.addWidget(self._zone)

        self._apply_tokens()

    # -- appearance --------------------------------------------------------
    def _flip_appearance(self) -> None:
        self.set_dark(not self._dark)
        self.appearanceChanged.emit(self._dark)

    def set_dark(self, dark: bool) -> None:
        self._dark = bool(dark)
        self._t = tokens(self._dark)
        self._apply_tokens()
        self._zone.set_appearance(self._dark)
        if self._stats is not None:
            self.update_stats(self._stats)

    def _apply_tokens(self) -> None:
        t = self._t
        self._appearance.setText("Light" if self._dark else "Dark")
        for w in (self._ring, self._limits, self._heat):
            w.set_tokens(t)
        self._seg.set_tokens(t)
        self._dials.set_tokens(t)
        if hasattr(self, "_zone"):
            self._zone.set_tokens(t)
        self.setStyleSheet(f"""
            HeartPanel {{ background: {t['bg']}; }}
            QLabel#title {{ color: {t['text']}; font-size: 15px; font-weight: 700; }}
            QLabel#section {{ color: {t['text']}; font-size: 12px; font-weight: 700; }}
            QLabel#plan {{
                color: {t['text_2']}; background: {t['track']};
                border-radius: 6px; padding: 0 8px;
                font-size: 10px; font-weight: 700;
            }}
            QPushButton {{
                background: {t['track']}; color: {t['text_2']};
                border: none; border-radius: 6px; padding: 0 12px;
                font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ color: {t['text']}; }}
        """)
        self.update()

    def set_link(self, link) -> None:
        t = self._t
        color = {"live": "#1fa65a", "stale": "#d98a00", "disconnected": "#d93a2b"}.get(link.state, t["text_2"])
        dot = "●"
        text = f"{dot} {link.headline}"
        if link.advice:
            text += f"  —  {link.advice}"
        self._status.setText(text)
        self._status.setStyleSheet(f"QLabel#status {{ color: {color}; font-size: 11px; font-weight: 600; padding: 2px 2px 0 2px; }}")
        for w in (self._ring, self._limits):
            w.set_live(link.live)

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        if self._stats is not None:
            self._render_activity(self._stats)

    # -- data --------------------------------------------------------------
    def update_stats(self, stats: Any) -> None:
        self._stats = stats
        g = lambda n, d=0.0: float(getattr(stats, n, d) or d)  # noqa: E731

        self._ring.set_value(g("session_utilization"),
                             _fmt_in(int(getattr(stats, "session_reset", 0) or 0)))

        scoped_label = str(getattr(stats, "scoped_label", "") or "")
        rows = [(DIAL_ALL, "All models", g("weekly_utilization"))]
        if scoped_label:
            rows.append((DIAL_SCOPED, scoped_label, g("scoped_utilization")))
        self._limits.set_values(rows, int(getattr(stats, "weekly_reset", 0) or 0))

        # A cold or throttled start has no label yet; show the last one the API
        # gave us rather than the generic word. Still derived FROM the id --
        # nothing reads the label back to make a decision.
        if scoped_label:
            if scoped_label != self._config.get("last_scoped_label"):
                self._config["last_scoped_label"] = scoped_label
                self.scopedLabelSeen.emit(scoped_label)
        remembered = scoped_label or str(self._config.get("last_scoped_label", "") or "")
        self._dials.set_scoped_available(bool(scoped_label))
        self._dials.set_scoped_label(remembered)

        plan = str(getattr(stats, "subscription_type", "") or "")
        self._plan.setText(plan.capitalize() if plan else "")
        self._plan.setVisible(bool(plan))

        self._render_activity(stats)

    def _render_activity(self, stats: Any) -> None:
        import time as _t
        if self._mode == "Week":
            grid = list(getattr(stats, "week_hour_grid", []) or [])
            days = list(getattr(stats, "week_hour_days", []) or [])
            self._heat._rows, self._heat._cols = 7, 24
            row_labels = [_t.strftime("%a", _t.localtime(d)) for d in days] or \
                         ["" for _ in range(7)]
            col_labels = [(h, f"{h:02d}") for h in range(0, 24, 3)]
            self._heat.set_grid(grid, row_labels, col_labels)
        else:
            # Last 91 days as a calendar: columns are Monday-start weeks, rows
            # are ACTUAL weekdays. The previous version placed day i at row
            # i % 7, which silently assumed the oldest day was a Monday.
            flat = list(getattr(stats, "daily_heatmap", []) or [])
            n = 91
            vals = flat[-n:]
            vals = [0.0] * (n - len(vals)) + vals      # oldest first, today last
            now = _t.time()
            grid: dict[tuple[int, int], float] = {}
            col_labels: list[tuple[int, str]] = []
            col, last_mon, last_label_col = 0, None, -9
            for k in range(n):
                lt = _t.localtime(now - (n - 1 - k) * 86400)
                if k and lt.tm_wday == 0:
                    col += 1
                grid[(lt.tm_wday, col)] = vals[k]
                if lt.tm_mon != last_mon and col - last_label_col >= 2:
                    col_labels.append((col, _t.strftime("%b", lt)))
                    last_label_col = col
                last_mon = lt.tm_mon
            cols = col + 1
            cells = [grid.get((r, c)) for r in range(7) for c in range(cols)]
            self._heat._rows, self._heat._cols = 7, cols
            self._heat.set_grid(cells, ["Mon", "", "Wed", "", "Fri", "", "Sun"], col_labels)
