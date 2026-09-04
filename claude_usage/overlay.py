"""Frameless, transparent OSD overlay — PySide6 implementation.

The OSD sits at the top-right corner of the primary screen, always on top,
showing session and weekly utilization bars with reset countdowns.

Interactions:
    Left-click (no drag)  — emit ``clicked`` (opens the detail popup)
    Left-click + drag     — move the overlay
    Right-click           — emit ``rightClicked`` (shows context menu)
    Scroll wheel          — resize (0.6x -- 4.0x)
    Right-click-drag      — not used
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QWidget

from claude_usage.collector import UsageStats
from claude_usage.skins import SKIN_MODULES, from_usage_stats as _skin_data_from_stats
from claude_usage.themes import (
    BAR_STYLE_ASCII,
    BAR_STYLE_BLOCK,
    ThemeStyle,
    get_style,
    get_theme,
)
from claude_usage.ticker import TickerItem


# Base OSD dimensions (at scale=1.0). Ticker adds ~22px to the bottom of
# the panel; when it's toggled off we collapse back to the original height.
BASE_WIDTH = 260
BASE_HEIGHT = 100
TICKER_STRIP_HEIGHT = 22
NEWS_STRIP_HEIGHT = 16  # second ticker row for latest headline
# Extra height for the optional model-scoped weekly bar (matches one
# session/weekly row's vertical footprint in _paint_full).
SCOPED_ROW_HEIGHT = 31
# Gauge view is slightly taller than bars because the rings + label + reset
# stack vertically inside each column. No ticker in this view (it would
# collide with the reset line under each ring).
GAUGE_HEIGHT = 106
# Gauge view gets its own scoped-cap row height: the bars view needs a taller
# row for its label+bar stack, the gauge only tucks a slim strip under the rings.
GAUGE_SCOPED_ROW_HEIGHT = 30
# Extra height for the optional Codex ring row (a second Session/Weekly pair
# drawn beneath Claude's; same stack minus the shared top padding).
CODEX_GAUGE_ROW_HEIGHT = 118

# Supported OSD view modes. Kept as string constants so config files and
# tests don't have to import an enum.
VIEW_MODE_BARS = "bars"
VIEW_MODE_GAUGE = "gauge"
# "strip": a menu-bar-height pill the widget OWNS -- three mini dials and a
# drag handle. Built because a real QSystemTrayIcon on macOS can be hidden by
# notch overflow and cannot know whether the bar it sits on is light or dark.
VIEW_MODE_STRIP = "strip"
VIEW_MODES = (VIEW_MODE_BARS, VIEW_MODE_GAUGE, VIEW_MODE_STRIP)
STRIP_HEIGHT = 30
# Ring size is DERIVED from the pill height, not a second constant, so the
# rings always fill the same proportion of the strip however it is scaled.
STRIP_RING_FRACTION = 0.70      # ring diameter as a fraction of strip height
STRIP_STROKE_FRACTION = 0.12    # ring stroke as a fraction of ring diameter
STRIP_RADIUS_FRACTION = 0.25    # corner radius: a rounded rect, NOT a capsule
STRIP_MIN_TEXT_PX = 9           # never draw a percentage smaller than this
# Fixed chrome: the two handles do not scale with the strip.
STRIP_EDGE = 8.0                # inset from the strip's edge to a handle
STRIP_DOT_R = 1.35              # handle dot radius
STRIP_DOT_STEP = 4.0            # handle dot pitch
# Screen-anchor presets the OSD can snap to. "custom" means use the exact
# osd_x / osd_y coordinates from config (set when the user drags the widget).
OSD_POSITION_TOP_LEFT = "top-left"
OSD_POSITION_TOP_RIGHT = "top-right"
OSD_POSITION_BOTTOM_LEFT = "bottom-left"
OSD_POSITION_BOTTOM_RIGHT = "bottom-right"
OSD_POSITION_CUSTOM = "custom"
OSD_POSITIONS = (
    OSD_POSITION_TOP_LEFT, OSD_POSITION_TOP_RIGHT,
    OSD_POSITION_BOTTOM_LEFT, OSD_POSITION_BOTTOM_RIGHT,
    OSD_POSITION_CUSTOM,
)
OSD_MARGIN = 16
OSD_RADIUS = 12
OSD_BAR_HEIGHT = 6
OSD_BAR_RADIUS = 3
MINIMIZED_HEIGHT = 6

# Ticker animation: seconds-per-full-loop scales inversely with viewport
# width; we use a pixels-per-second rate instead so scale changes don't
# affect perceived speed. 30 px/s feels unhurried but still alive.
TICKER_SCROLL_PX_PER_SEC = 30.0
TICKER_FRAME_INTERVAL_MS = 40  # ~25 fps — smooth without waking the CPU

# Scroll-wheel scale limits
SCALE_MIN = 0.6
SCALE_MAX = 4.0
SCALE_STEP = 0.1

# Distance the mouse must move between press and release before a left-click
# is treated as a drag rather than a click.
DRAG_THRESHOLD = 5


def _strip_font(px: float) -> QFont:
    """Bold mono at an exact PIXEL size, so strip text scales continuously
    with the ring instead of stepping through point sizes."""
    f = _mono_font(10, bold=True)
    f.setPixelSize(max(1, int(round(px))))
    return f


def _mono_font(size_pt: int, bold: bool = False) -> QFont:
    """Return a platform-appropriate fixed-pitch font.

    Naming the family ``"monospace"`` alone is a Unix/X convention — on
    Windows it falls back to the app default (often a proportional face)
    which breaks ticker and percentage alignment. Setting ``StyleHint`` to
    ``Monospace`` tells Qt to honour the hint when resolving the family,
    so we get a real fixed-pitch font on all three OSes.
    """
    f = QFont()
    f.setStyleHint(QFont.Monospace)
    f.setFamily("monospace")
    f.setPointSize(int(size_pt))
    if bold:
        f.setBold(True)
    return f


def _ticker_quartile_thresholds(items: list[TickerItem]) -> tuple[float, float, float]:
    """Return (cool, warm, hot) cost cutoffs based on quartiles of *items*.

    With < 4 items the buffer is too small to quartile meaningfully, so
    we collapse to a single tier by returning sentinels that force every
    item into the "cool" bucket. This avoids flickering colours during the
    first seconds after startup.
    """
    if len(items) < 4:
        return (0.0, float("inf"), float("inf"))
    costs = sorted(it.cost_usd for it in items)
    n = len(costs)
    return (costs[n // 4], costs[n // 2], costs[3 * n // 4])


def _hex_to_qcolor(hex_str: str, alpha: float = 1.0) -> QColor:
    """Convert ``#RRGGBB`` to ``QColor`` with the given alpha (0.0 -- 1.0)."""
    h = hex_str.lstrip("#")
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return QColor(r, g, b, int(alpha * 255))


def _format_reset_short(reset_ts: int) -> str:
    """Compact reset label: '2h 31m' (< 24h) or 'Mon 16:00' (>= 24h)."""
    if reset_ts <= 0:
        return ""
    remaining = int(reset_ts - datetime.now().timestamp())
    if remaining <= 0:
        return "soon"
    hours, rem = divmod(remaining, 3600)
    minutes = rem // 60
    if hours < 24:
        return f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
    return datetime.fromtimestamp(reset_ts).strftime("%a %H:%M")


def _burn_badge_text(alert) -> str:
    """Short title-row badge label for an active burn/spike/storm alert.

    BMP-only glyphs (``▲`` renders in every theme mono font, unlike emoji), so
    the badge never degrades to tofu on a terminal-style skin.
    """
    kind = getattr(alert, "kind", "")
    if kind == "fast_burn":
        return f"▲{int(getattr(alert, 'delta_pct', 0))}%"
    if kind == "token_spike":
        return "▲SPIKE"
    if kind == "retry_storm":
        return "▲STORM"
    return ""


def _bar_color(pct: float, theme: dict[str, str]) -> QColor:
    """Return the progress-bar fill colour for *pct* (0.0 -- 1.0)."""
    if pct < 0.6:
        return _hex_to_qcolor(theme["bar_blue"])
    if pct < 0.85:
        return _hex_to_qcolor(theme["warn"])
    return _hex_to_qcolor(theme["crit"])


class UsageOverlay(QWidget):
    """Transparent, frameless OSD showing session + weekly utilisation."""

    # Emitted when the user left-clicks (without dragging).
    clicked = Signal()
    # Emitted when the user right-clicks. Handler should show a context menu.
    rightClicked = Signal(QPoint)
    # Emitted after a drag-to-move finishes, with the new top-left (x, y).
    # The controller persists these as the "custom" position in config.
    movedTo = Signal(int, int)
    # Emitted when the scroll-wheel changes the scale factor — controller
    # persists it so the OSD reopens at the same zoom.
    scaledTo = Signal(float)
    # Emitted when the minimized state flips — controller persists it.
    minimizedChanged = Signal(bool)

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__()
        cfg = config or {}
        theme_name = str(cfg.get("theme", "default"))
        self._theme = get_theme(theme_name)
        self._style: ThemeStyle = get_style(theme_name)
        # Handoff skin painter for this theme (or None when we fall back
        # to the built-in bars / gauge paint paths).
        self._skin = SKIN_MODULES.get(theme_name)
        # Latest UsageStats snapshot — skin painters consume a projected
        # copy, default paint consumes the _session_pct / _weekly_pct
        # scalars set in update_stats.
        self._last_stats: UsageStats | None = None
        self._scale: float = float(cfg.get("osd_scale", 1.0))
        self._opacity: float = float(cfg.get("osd_opacity", 0.75))
        self._minimized: bool = False
        # The strip view follows the verbose panel's appearance, not the OSD
        # theme: it is meant to read like part of the menu bar, and the panel
        # is where the user chose light or dark.
        self._strip_dark: bool = bool(cfg.get("panel_dark", True))
        # macOS only: float the strip above the menu bar and allow it to be
        # parked in the bar band. See DEFAULT_CONFIG for why it is off by
        # default.
        self._strip_in_menubar: bool = bool(cfg.get("strip_in_menubar", False))
        self._menubar_level_warned: bool = False
        # Scale-grip drag state (strip view). Kept separate from the move
        # drag so a grip press can never also start a move.
        self._scaling: bool = False
        self._scale_press: QPoint | None = None
        self._scale_start: float = 1.0
        self.setMouseTracking(True)     # hover cursor over the grip

        # Live stats — updated externally via update_stats()
        self._session_pct: float = 0.0
        self._weekly_pct: float = 0.0
        self._session_reset: int = 0
        self._weekly_reset: int = 0
        # Optional model-scoped weekly cap (e.g. Fable). _scoped_label empty
        # => no third bar. Auto-appears/hides as the API reports it.
        self._scoped_pct: float = 0.0
        self._scoped_reset: int = 0
        self._scoped_label: str = ""
        # Optional Codex provider (opt-in via `providers` config). When
        # unavailable the OSD paints exactly as before.
        self._codex_available: bool = False
        self._codex_session_pct: float = 0.0
        self._codex_session_reset: int = 0
        self._codex_weekly_pct: float = 0.0
        self._codex_weekly_reset: int = 0
        self._live_tpm: float = 0.0      # tokens/min over the last few minutes
        self._is_live: bool = False       # show the "● LIVE" dot
        self._burn_alert = None           # active burn/spike/storm badge (or None)
        self._active_subagents: int = 0  # count of running Task-tool subagents
        # Ticker tape: newest-first. The paint loop walks them oldest→newest
        # so the newest item rides in from the right edge like a news ticker.
        self._ticker_items: list[TickerItem] = []
        self._news_items: list[NewsItem] = []
        self._ticker_offset: float = 0.0
        self._news_offset: float = 0.0   # separate scroll offset for news strip
        self._latest_headline: str = ""  # single headline shown in news strip
        self._latest_news_url: str = ""  # URL opened on click
        # User toggle — default on, overridable via config; runtime flip
        # lives in the right-click menu.
        self._ticker_enabled: bool = bool(cfg.get("show_ticker", True))
        # News strip is OPT-IN — defaults to False because it makes an
        # outbound network call to a 3rd-party feed (hnrss.org / reddit),
        # something a fresh install shouldn't do silently. Users opt in via
        # the right-click menu or by setting "show_news": true in config.
        self._news_enabled: bool = bool(cfg.get("show_news", False))
        # "bars" (default) or "gauge" — the right-click menu toggles this and
        # persists to config.
        raw_mode = str(cfg.get("osd_view_mode", VIEW_MODE_BARS))
        self._view_mode: str = raw_mode if raw_mode in VIEW_MODES else VIEW_MODE_BARS

        # Screen anchor — one of OSD_POSITIONS. "custom" reads the saved
        # osd_x / osd_y coordinates (written when the user drags the widget).
        raw_pos = str(cfg.get("osd_position", OSD_POSITION_TOP_RIGHT))
        self._position: str = raw_pos if raw_pos in OSD_POSITIONS else OSD_POSITION_TOP_RIGHT
        self._custom_xy: tuple[int, int] | None = None
        cx, cy = cfg.get("osd_x"), cfg.get("osd_y")
        if cx is not None and cy is not None:
            try:
                self._custom_xy = (int(cx), int(cy))
            except (TypeError, ValueError):
                self._custom_xy = None
        # Emitted after a drag so the controller can persist the new
        # custom coordinates to config. (scope, x, y) — scope is "custom".
        # Wired in widget.py.

        # Drag tracking
        self._press_pos: QPoint | None = None        # mouse pos on press (global)
        self._press_win_pos: QPoint | None = None    # window pos on press
        self._dragging: bool = False
        self._system_move_started: bool = False      # wayland fix

        # Whether to pin above all other windows. Off => a normal background
        # desktop widget the WM can stack behind focused windows.
        self._always_on_top: bool = bool(cfg.get("osd_always_on_top", True))

        # Window setup — frameless, transparent, no taskbar; "always on top"
        # is conditional (see _window_flags).
        self.setWindowFlags(self._window_flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        # NET_WM window type — conditional on the pin state, see the method.
        self._apply_window_type_attr()
        # macOS hides Qt.Tool windows whenever the owning app is deactivated
        # (i.e. you click another app), so the OSD would silently vanish on
        # focus loss even though the process keeps running. This attribute
        # opts out of that Cocoa behaviour; it's a no-op on other platforms.
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)

        # Initial size + position (top-right of primary screen).
        self._apply_size()
        self._move_to_default_position()

        # Ticker animation timer — advances _ticker_offset each frame. We
        # only start it when there are items to scroll so the OSD stays
        # CPU-idle during quiet periods.
        self._ticker_timer = QTimer(self)
        self._ticker_timer.setInterval(TICKER_FRAME_INTERVAL_MS)
        self._ticker_timer.timeout.connect(self._advance_ticker)
        # NB: there is NO separate news-refresh timer. The collector
        # already fetches news_items (with 1h on-disk cache) on every
        # stats-refresh tick, and delivers them via the existing
        # cross-thread stats_ready Signal -> update_stats() path, which
        # runs on the GUI thread. That is the only writer of
        # _news_items / _latest_headline / _latest_news_url, so paintEvent
        # never sees a torn read.

    # ------------------------------------------------------------------ API

    def update_stats(self, stats: UsageStats) -> None:
        """Apply the latest :class:`UsageStats` and trigger a repaint."""
        self._last_stats = stats
        self._session_pct = max(0.0, min(1.0, float(stats.session_utilization)))
        self._weekly_pct = max(0.0, min(1.0, float(stats.weekly_utilization)))
        self._session_reset = int(stats.session_reset)
        self._weekly_reset = int(stats.weekly_reset)
        # Scoped weekly cap. If its presence changed since the last update,
        # the OSD needs one extra bar's worth of height — re-apply the size.
        had_scoped = bool(self._scoped_label)
        self._scoped_pct = max(0.0, min(1.0, float(getattr(stats, "scoped_utilization", 0.0))))
        self._scoped_reset = int(getattr(stats, "scoped_reset", 0) or 0)
        self._scoped_label = str(getattr(stats, "scoped_label", "") or "")
        # Optional Codex provider rows — like scoped, their appearance or
        # disappearance changes the OSD footprint.
        had_codex = self._codex_available
        self._codex_available = bool(getattr(stats, "codex_available", False))
        self._codex_session_pct = max(0.0, min(1.0, float(
            getattr(stats, "codex_session_utilization", 0.0) or 0.0)))
        self._codex_session_reset = int(getattr(stats, "codex_session_reset", 0) or 0)
        self._codex_weekly_pct = max(0.0, min(1.0, float(
            getattr(stats, "codex_weekly_utilization", 0.0) or 0.0)))
        self._codex_weekly_reset = int(getattr(stats, "codex_weekly_reset", 0) or 0)
        if bool(self._scoped_label) != had_scoped or self._codex_available != had_codex:
            self._apply_size()
        live = getattr(stats, "live_activity", None)
        if live is not None:
            self._is_live = bool(getattr(live, "is_live", False))
            self._live_tpm = float(getattr(live, "tokens_per_minute", 0.0) or 0.0)
        else:
            self._is_live = False
            self._live_tpm = 0.0
        self._active_subagents = max(0, int(getattr(stats, "active_subagent_count", 0) or 0))
        self._burn_alert = getattr(stats, "burn_alert", None)
        self._ticker_items = list(getattr(stats, "ticker_items", []) or [])
        new_news = list(getattr(stats, "news_items", []) or [])
        if new_news:
            self._news_items = new_news
            self._latest_headline = new_news[0].title
            self._latest_news_url = new_news[0].url
        # Animate whenever we have items and a view that actually draws the
        # ticker — default bars mode, or a skin that opts in via its
        # module-level WANTS_TICKER flag.
        # Skins declare WANTS_TICKER, but the user's toggle still wins —
        # without the _ticker_enabled check here, every refresh restarted the
        # marquee a few seconds after the user switched it off (issue #25).
        skin_wants_ticker = (
            self._skin is not None
            and self._ticker_enabled
            and self._ticker_items
            and getattr(self._skin, "WANTS_TICKER", False)
        )
        ticker_would_draw = not self._minimized and self._ticker_items and (
            (self._ticker_enabled and self._view_mode == VIEW_MODE_BARS and self._skin is None)
            or skin_wants_ticker
        )
        # The same timer drives the news marquee — without this, an idle
        # Claude (empty ticker) leaves _news_offset frozen at 0, where the
        # marquee math places the headline entirely outside the clip rect,
        # so the opt-in news strip is invisible exactly when it's most
        # interesting (nothing else happening).
        news_would_scroll = (
            not self._minimized and self._news_enabled and bool(self._news_items)
        )
        if ticker_would_draw or news_would_scroll:
            if not self._ticker_timer.isActive():
                self._ticker_timer.start()
        else:
            self._ticker_timer.stop()
            self._ticker_offset = 0.0
        self.update()  # schedule a paintEvent

    def set_strip_dark(self, dark: bool) -> None:
        """Repaint the strip in the panel's appearance."""
        self._strip_dark = bool(dark)
        if self._view_mode == VIEW_MODE_STRIP:
            self.update()

    # -- strip-in-menu-bar -------------------------------------------------
    def _strip_in_menubar_active(self) -> bool:
        return (self._strip_in_menubar and self._view_mode == VIEW_MODE_STRIP
                and not self._minimized)

    def _anchor_geometry(self, screen):
        """Where the OSD may live. Normally the screen minus the menu bar;
        in menu-bar mode, the whole screen, so the bar band is reachable."""
        return screen.geometry() if self._strip_in_menubar_active() \
            else screen.availableGeometry()

    def _apply_menubar_level(self) -> None:
        """Raise the native window above the menu bar, or put it back.

        Qt's WindowStaysOnTopHint maps to NSFloatingWindowLevel (3), which
        sits BELOW the menu bar (24). Only NSStatusWindowLevel (25) floats
        over it, and Qt does not expose that -- so it is set on the NSWindow
        directly via PyObjC. Idempotent, and re-run after anything that
        recreates the native window (setWindowFlags), because that resets
        the level to Qt's own.
        """
        import sys
        from PySide6.QtGui import QGuiApplication
        # Must be the COCOA platform, not merely macOS: the offscreen platform
        # (tests, preview renders) runs on a Mac too, and its winId() is not
        # an NSView -- treating it as one segfaults the interpreter.
        if sys.platform != "darwin" or QGuiApplication.platformName() != "cocoa":
            return
        want = self._strip_in_menubar_active()
        try:
            import objc
            from AppKit import (
                NSFloatingWindowLevel, NSNormalWindowLevel, NSStatusWindowLevel,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorMoveToActiveSpace,
            )
        except Exception:
            if want and not self._menubar_level_warned:
                self._menubar_level_warned = True
                print("claude-usage: strip_in_menubar needs pyobjc-framework-Cocoa; "
                      "staying below the menu bar", file=sys.stderr)
            return
        try:
            win = objc.objc_object(c_void_p=int(self.winId())).window()
            if win is None:
                return
            if want:
                win.setLevel_(NSStatusWindowLevel)
                # Show on every Space, like the menu bar itself. Qt stamps
                # MoveToActiveSpace on floating windows, and macOS refuses a
                # window that has BOTH (NSInternalInconsistencyException), so
                # clear it first. Kept separate from the level: a behavior
                # failure must never cost us the level, which is the point.
                try:
                    behavior = win.collectionBehavior()
                    behavior &= ~NSWindowCollectionBehaviorMoveToActiveSpace
                    behavior |= NSWindowCollectionBehaviorCanJoinAllSpaces
                    win.setCollectionBehavior_(behavior)
                except Exception as exc:
                    if not self._menubar_level_warned:
                        self._menubar_level_warned = True
                        print(f"claude-usage: menu-bar strip is above the bar but "
                              f"not on all Spaces: {exc}", file=sys.stderr)
            else:
                on_top = bool(getattr(self, "_always_on_top", True))
                win.setLevel_(NSFloatingWindowLevel if on_top else NSNormalWindowLevel)
        except Exception as exc:
            if not self._menubar_level_warned:
                self._menubar_level_warned = True
                print(f"claude-usage: could not set menu-bar window level: {exc}",
                      file=sys.stderr)

    def set_strip_in_menubar(self, on: bool) -> None:
        self._strip_in_menubar = bool(on)
        self._apply_menubar_level()
        if self._position != OSD_POSITION_CUSTOM:
            self._move_to_default_position()
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_menubar_level()

    def _strip_dials(self) -> list[tuple[str, float]]:
        from claude_usage.menubar import DIAL_ALL, DIAL_SCOPED, DIAL_SESSION
        dials = [(DIAL_SESSION, self._session_pct), (DIAL_ALL, self._weekly_pct)]
        if self._scoped_label:
            dials.append((DIAL_SCOPED, self._scoped_pct))
        return dials

    def _strip_layout(self):
        """Every position in the strip, derived from its height; and the
        total width. BOTH _apply_size and _paint_strip consume this, so the
        window can never be narrower than what gets painted -- the bug that
        clipped the third dial's percentage at scale.

        Percentages scale with the ring (pixel size, so they keep growing)
        and move INSIDE the ring as soon as "100%" -- the widest value, so the
        layout never flips between 4% and 40% -- fits in its inner diameter
        at a legible size.
        """
        from PySide6.QtGui import QFontMetrics
        s = self._scale
        h = STRIP_HEIGHT * s
        ring_d = h * STRIP_RING_FRACTION
        stroke = max(2.0, ring_d * STRIP_STROKE_FRACTION)
        # Handles are chrome, not content: they keep a FIXED size at every
        # scale, and only the dials grow. Move handle (2x3 dots) on the left
        # edge, centred; scale grip (triangular dots) in the bottom-right
        # corner, like a window's resize grip.
        edge = STRIP_EDGE
        dot, step = STRIP_DOT_R, STRIP_DOT_STEP
        handle_x = edge
        handle_w = step + 2 * dot            # two dot columns
        grip_w = 2 * step + 2 * dot          # 3x3 lattice
        pad = edge

        in_px = max(STRIP_MIN_TEXT_PX, ring_d * 0.28)
        out_px = max(STRIP_MIN_TEXT_PX, h * 0.33)
        f_in, f_out = _strip_font(in_px), _strip_font(out_px)
        fm_in, fm_out = QFontMetrics(f_in, self), QFontMetrics(f_out, self)
        # Fits when the widest value clears the INNER diameter with a hair of
        # margin. A generous margin here is why it never moved inside at 3x.
        inside = fm_in.horizontalAdvance("100%") + h * 0.04 <= ring_d - 2 * stroke

        dials = []
        x = handle_x + handle_w + h * 0.28
        for kind, pct in self._strip_dials():
            d = {"kind": kind, "pct": pct, "ring_x": x, "inside": inside}
            if inside:
                d["font"] = f_in
                x += ring_d + h * 0.30
            else:
                d["font"] = f_out
                d["text_x"] = x + ring_d + h * 0.15
                x = d["text_x"] + fm_out.horizontalAdvance("100%") + h * 0.35
            dials.append(d)
        # drop the trailing inter-dial gap, add the right pad
        if dials:
            x -= (h * 0.30 if inside else h * 0.35)
        grip_x = x + h * 0.20
        width = int(round(grip_x + grip_w + edge))
        return {"h": h, "pad": pad, "ring_d": ring_d, "stroke": stroke,
                "handle_x": handle_x, "dials": dials, "width": width,
                "dot": dot, "step": step,
                "grip": QRectF(grip_x, h - edge * 0.6 - grip_w, grip_w, grip_w)}

    def _strip_width(self) -> int:
        return self._strip_layout()["width"]

    def _strip_diag_ok(self) -> bool:
        """At most one strip diagnostic per second, so a drag does not spam."""
        import time as _t
        now = _t.time()
        if now - getattr(self, "_strip_diag_ts", 0.0) < 1.0:
            return False
        self._strip_diag_ts = now
        return True

    def _on_scale_grip(self, local_pos) -> bool:
        if self._view_mode != VIEW_MODE_STRIP or self._minimized:
            return False
        return self._strip_layout()["grip"].adjusted(-3, -3, 3, 3).contains(local_pos)

    def _set_scale(self, new_scale: float) -> None:
        """Apply a scale (clamped) exactly the way the wheel does."""
        new_scale = max(SCALE_MIN, min(SCALE_MAX, new_scale))
        if new_scale == self._scale:
            return
        self._scale = new_scale
        self._apply_size()          # anchored setGeometry during a drag
        self.update()
        if self._position == OSD_POSITION_CUSTOM:
            tl = self.frameGeometry().topLeft()
            self._custom_xy = (tl.x(), tl.y())

    def set_view_mode(self, mode: str) -> None:
        """Switch between bar and gauge rendering; resizes the OSD to match."""
        if mode not in VIEW_MODES or mode == self._view_mode:
            return
        self._view_mode = mode
        self._apply_size()
        self._apply_menubar_level()
        # Gauge and strip views have no ticker — stop the animation to save CPU.
        if mode in (VIEW_MODE_GAUGE, VIEW_MODE_STRIP):
            self._ticker_timer.stop()
        elif self._ticker_enabled and self._ticker_items and not self._minimized:
            self._ticker_timer.start()
        self.update()

    def view_mode(self) -> str:
        """Return the active view mode (``"bars"`` or ``"gauge"``)."""
        return self._view_mode

    def set_ticker_enabled(self, enabled: bool) -> None:
        """Show/hide the ticker strip. Resizes the OSD to match."""
        enabled = bool(enabled)
        if enabled == self._ticker_enabled:
            return
        self._ticker_enabled = enabled
        self._apply_size()
        if not enabled:
            self._ticker_timer.stop()
            self._ticker_offset = 0.0
        elif (
            self._ticker_items
            and not self._minimized
            and self._view_mode == VIEW_MODE_BARS
        ):
            self._ticker_timer.start()
        self.update()

    def is_ticker_enabled(self) -> bool:
        """Return True if the user has the cost-ticker strip enabled."""
        return self._ticker_enabled

    def set_news_enabled(self, enabled: bool) -> None:
        self._news_enabled = bool(enabled)
        self.update()

    def is_news_enabled(self) -> bool:
        return self._news_enabled

    def _advance_ticker(self) -> None:
        """One frame of ticker scroll — called by the animation timer."""
        self._ticker_offset += TICKER_SCROLL_PX_PER_SEC * (TICKER_FRAME_INTERVAL_MS / 1000.0)
        self._news_offset += TICKER_SCROLL_PX_PER_SEC * (TICKER_FRAME_INTERVAL_MS / 1000.0)
        self.update()

    def set_opacity(self, value: float) -> None:
        """Set background opacity (0.15 -- 1.0)."""
        self._opacity = max(0.15, min(1.0, float(value)))
        self.update()

    def set_theme(self, name: str) -> None:
        """Switch to a named theme and repaint."""
        self._theme = get_theme(name)
        self._style = get_style(name)
        self._skin = SKIN_MODULES.get(name)
        self._apply_size()
        self.update()

    def toggle_minimized(self) -> None:
        """Collapse to a thin progress bar or restore the full panel."""
        self._minimized = not self._minimized
        self._apply_size()
        # Minimized view has no ticker — stop the animation to save CPU.
        # Restarting requires the bars view mode too (gauge view has no ticker).
        if self._minimized:
            self._ticker_timer.stop()
        elif self._ticker_items and self._view_mode == VIEW_MODE_BARS:
            self._ticker_timer.start()
        self.update()
        self.minimizedChanged.emit(self._minimized)

    # ------------------------------------------------------------- internals

    def _skin_data_for_paint(self):
        """SkinData for the active skin, with the cost-ticker toggle applied.

        Skins paint whatever ``ticker_items`` they're handed — they have no
        notion of the user's "Show cost ticker" setting. So the toggle is
        applied HERE, once, for all skins: when it's off the skin gets an
        empty list and its marquee helper draws nothing (issue #25). The panel
        keeps its footprint — a skin's ticker strip is part of its composition,
        so the row simply stays blank rather than collapsing.
        """
        data = _skin_data_from_stats(
            self._last_stats, ticker_offset=self._ticker_offset,
        )
        if not self._ticker_enabled and data.ticker_items:
            from dataclasses import replace as _replace
            data = _replace(data, ticker_items=[])
        return data

    def _skin_base_height(self) -> float:
        """Unscaled OSD height for the active skin, folding in the optional
        scoped-cap row and the optional Codex provider rows (5h + 7d).

        ``_apply_size`` (the window) and ``paintEvent`` (the rect handed to the
        skin) both derive their height from HERE so the two can never disagree
        — a mismatch would let an extra row spill outside the painted panel.
        Scoped and Codex compose additively: a skin's ``osd_height_scoped``
        gives its one-row footprint, and ``codex_rows_height`` (2× that) covers
        the Codex 5h + 7d pair.

        (Single-source consolidation adapted from faithpricejp-source's PR #21.)
        """
        m = self._skin.METRICS
        base = m["osd_height"]
        if self._scoped_label:
            base = m.get("osd_height_scoped", base + SCOPED_ROW_HEIGHT)
        if self._codex_available:
            base += m.get("codex_rows_height", 2 * SCOPED_ROW_HEIGHT)
        return base

    def _apply_size(self) -> None:
        """Resize the window to match ``_scale``, view mode, and chrome state."""
        if self._skin is not None and not self._minimized:
            # Skins declare their own OSD footprint — honour it instead of
            # squeezing the handoff layout into the default's 260×122 box.
            # Scoped-cap and Codex rows grow it; _skin_base_height() resolves
            # all four combinations from a single place.
            m = self._skin.METRICS
            width = int(m["osd_width"] * self._scale)
            height = int(self._skin_base_height() * self._scale)
            if self.isVisible():
                tr = self.frameGeometry().topRight()
                self.resize(width, height)
                self.move(tr.x() - width, tr.y())
            else:
                self.resize(width, height)
            return

        width = int(BASE_WIDTH * self._scale)
        if self._view_mode == VIEW_MODE_STRIP:
            width = self._strip_width()
            base = STRIP_HEIGHT
            self._strip_want = (width, int(STRIP_HEIGHT * self._scale))
        elif self._view_mode == VIEW_MODE_GAUGE:
            base = GAUGE_HEIGHT + (GAUGE_SCOPED_ROW_HEIGHT if self._scoped_label else 0)
            if self._codex_available:
                base += CODEX_GAUGE_ROW_HEIGHT
        else:
            # Receipt skin always reserves the footer strip for its barcode,
            # even if the user disabled the ticker feature.
            wants_footer = self._ticker_enabled or self._style.decoration == "receipt"
            base = BASE_HEIGHT + (TICKER_STRIP_HEIGHT if wants_footer else 0)
            if self._scoped_label:
                base += SCOPED_ROW_HEIGHT
            if self._codex_available:
                base += 2 * SCOPED_ROW_HEIGHT  # Codex 5h + 7d rows
        height = MINIMIZED_HEIGHT if self._minimized else int(base * self._scale)
        # Preserve the top-right corner when resizing so the overlay doesn't
        # visually drift as the user scrolls to scale.
        if self.isVisible():
            # During a scale drag use the anchor captured at press; otherwise
            # the current top-right. Either way ONE setGeometry, not a
            # resize followed by a move: on macOS that pair paints as two
            # frames per mouse pixel, and reading geometry back between them
            # returns the stale value -- the visible "glitch".
            anchor = self._scale_anchor if (self._scaling and getattr(self, "_scale_anchor", None) is not None) \
                else self.frameGeometry().topRight()
            self.setGeometry(anchor.x() - width, anchor.y(), width, height)
        else:
            self.resize(width, height)
        # Strip only: did the native window actually take the size? A
        # disagreement here is the on-device "tall box, small dials" bug.
        want = getattr(self, "_strip_want", None)
        if (want is not None and self._view_mode == VIEW_MODE_STRIP and self.isVisible()
                and (self.width(), self.height()) != want and self._strip_diag_ok()):
            import sys
            print(f"claude-usage: strip resize refused: have {self.width()}x{self.height()}, "
                  f"want {want[0]}x{want[1]}, scale={self._scale:.2f}", file=sys.stderr)

    def _move_to_default_position(self) -> None:
        """Anchor the overlay according to the configured ``_position``.

        Corner presets are recomputed against the current screen geometry so
        they stay correct across resolution changes; "custom" restores the
        exact coordinates the user last dragged to (clamped onto a visible
        screen so an unplugged monitor can't strand the widget off-screen).
        """
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = self._anchor_geometry(screen)
        w, h = self.width(), self.height()

        if self._position == OSD_POSITION_CUSTOM and self._custom_xy is not None:
            x, y = self._custom_xy
            # Clamp so at least part of the widget stays on-screen.
            x = max(geo.x(), min(x, geo.x() + geo.width() - w))
            y = max(geo.y(), min(y, geo.y() + geo.height() - h))
            self.move(x, y)
            return

        left = geo.x() + OSD_MARGIN
        right = geo.x() + geo.width() - w - OSD_MARGIN
        top = geo.y() + OSD_MARGIN
        if self._strip_in_menubar_active():
            # Top presets park the strip INSIDE the menu bar, vertically
            # centred in the band, so "Top Right" means "in the bar, right".
            bar_h = screen.availableGeometry().y() - screen.geometry().y()
            top = screen.geometry().y() + max(0, (bar_h - h) // 2)
        bottom = geo.y() + geo.height() - h - OSD_MARGIN
        anchors = {
            OSD_POSITION_TOP_LEFT: (left, top),
            OSD_POSITION_TOP_RIGHT: (right, top),
            OSD_POSITION_BOTTOM_LEFT: (left, bottom),
            OSD_POSITION_BOTTOM_RIGHT: (right, bottom),
        }
        x, y = anchors.get(self._position, (right, top))
        self.move(x, y)

    def set_position(self, position: str) -> None:
        """Switch to a named anchor preset and reposition immediately."""
        if position not in OSD_POSITIONS:
            return
        self._position = position
        self._move_to_default_position()

    def position(self) -> str:
        """Return the current anchor preset name (one of OSD_POSITIONS)."""
        return self._position

    def _window_flags(self) -> Qt.WindowFlags:
        """Build the window flags for the current always-on-top setting.

        When pinned (default) we also set ``BypassWindowManagerHint`` so the
        OSD floats above everything on KDE/GNOME without the WM re-stacking or
        decorating it. When the user turns always-on-top OFF, we drop BOTH
        that and ``WindowStaysOnTopHint`` so the window manager treats it like
        a normal frameless tool window — i.e. it sinks behind whatever you're
        working in, the whole point of a background desktop widget. (Without
        dropping Bypass too, an unmanaged X11 window would stay stuck on top
        regardless, which is the exact complaint in issue #13.)
        """
        flags = (
            Qt.FramelessWindowHint
            | Qt.Tool                      # tool window, off the taskbar
            | Qt.WindowDoesNotAcceptFocus  # typing never steals focus
        )
        if self._always_on_top:
            flags |= Qt.WindowStaysOnTopHint | Qt.BypassWindowManagerHint
        return flags

    def _apply_window_type_attr(self) -> None:
        """Type the native window to match the always-on-top setting.

        ``_NET_WM_WINDOW_TYPE_NOTIFICATION`` keeps the OSD out of the dock /
        taskbar / Alt-Tab, but X11 window managers ALSO stack notification
        windows in a layer above normal ones. Setting it unconditionally
        therefore pinned the OSD on top even with always-on-top off, silently
        overriding the setting issue #13 added (dropping the Bypass and
        StaysOnTop flags there was necessary but not sufficient). ``Qt.Tool``
        already types the window ``_NET_WM_WINDOW_TYPE_UTILITY``, which
        taskbars skip just the same, so when unpinned we drop the Notification
        type and let the WM stack us like any normal window.
        """
        self.setAttribute(
            Qt.WA_X11NetWmWindowTypeNotification, self._always_on_top
        )

    def set_always_on_top(self, on: bool) -> None:
        """Pin the OSD above other windows, or release it to normal stacking.

        Changing window flags re-creates the native window (Qt hides it), so
        we re-show without activating and restore the position afterwards."""
        on = bool(on)
        if on == self._always_on_top:
            return
        self._always_on_top = on
        was_visible = self.isVisible()
        self.setWindowFlags(self._window_flags())
        # setWindowFlags re-creates the NATIVE window, which can drop widget
        # attributes with native-window effects. Re-assert every one we set
        # in __init__ — losing WA_TranslucentBackground paints the OSD as an
        # opaque black box, losing the X11 hint puts it back in the taskbar/
        # Alt-Tab, and losing the Mac hint makes it vanish on focus loss.
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._apply_window_type_attr()
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)
        if was_visible:
            self.show()
        self._move_to_default_position()

    def is_always_on_top(self) -> bool:
        """Return whether the OSD is pinned above other windows."""
        return self._always_on_top

    # --------------------------------------------------------------- events

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._on_scale_grip(event.position()):
            self._scaling = True
            self._scale_press = event.globalPosition().toPoint()
            self._scale_start = self._scale
            # Fixed anchor for the whole drag. _apply_size re-reads
            # frameGeometry() per step, which can lag a resize on macOS and
            # feed back into the next step.
            self._scale_anchor = self.frameGeometry().topRight()
            self._press_pos = None          # not a move, not a click
            return
        if event.button() == Qt.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._press_win_pos = self.frameGeometry().topLeft()
            self._dragging = False
            self._system_move_started = False
        elif event.button() == Qt.RightButton:
            self.rightClicked.emit(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._scaling and self._scale_press is not None:
            d = event.globalPosition().toPoint() - self._scale_press
            # Right or down grows, left or up shrinks -- the bottom-right
            # resize convention. In menu-bar mode the right edge is pinned
            # to the screen, so growth shows as the strip extending left;
            # the mapping stays the conventional one regardless.
            self._set_scale(self._scale_start + (d.x() + d.y()) / 150.0)
            return
        if self._press_pos is None:
            # Hover: advertise the grip.
            self.setCursor(Qt.SizeFDiagCursor if self._on_scale_grip(event.position())
                           else Qt.ArrowCursor)
            return
        delta = event.globalPosition().toPoint() - self._press_pos
        if not self._dragging and (abs(delta.x()) > DRAG_THRESHOLD or abs(delta.y()) > DRAG_THRESHOLD):
            self._dragging = True
            if QApplication.platformName().startswith("wayland"):
                window = self.windowHandle()
                if window is not None:
                    self._system_move_started = window.startSystemMove()
        if self._dragging and not self._system_move_started and self._press_win_pos is not None:
            self.move(self._press_win_pos + delta)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            return
        if self._scaling:
            self._scaling = False
            self._scale_press = None
            self._scale_anchor = None
            self.scaledTo.emit(self._scale)     # persist once, not per pixel
            return
        if self._press_pos is not None and not self._dragging:
            # Check if click landed on the news strip (bottom NEWS_STRIP_HEIGHT px).
            # Guards: never while minimized (h=6 makes the threshold negative,
            # which would hijack EVERY click into the browser), and only while
            # the news feature is actually enabled — a cached headline from a
            # since-disabled session must not keep stealing clicks.
            click_y = event.position().y()
            h = self.height()
            if (not self._minimized
                    and self._news_enabled
                    and click_y >= h - NEWS_STRIP_HEIGHT * self._scale
                    and self._latest_news_url):
                import webbrowser
                webbrowser.open(self._latest_news_url)
            else:
                self.clicked.emit()
        elif self._dragging and not self._system_move_started:
            # Drag finished — remember exactly where the user dropped it as
            # the new "custom" position so it survives a restart. Skipped after
            # a Wayland compositor move (startSystemMove): a Wayland client
            # can't read its own global position, so frameGeometry would persist
            # a bogus coordinate — and Wayland ignores absolute positioning
            # anyway, so there's nothing useful to remember.
            tl = self.frameGeometry().topLeft()
            self._position = OSD_POSITION_CUSTOM
            self._custom_xy = (tl.x(), tl.y())
            self.movedTo.emit(tl.x(), tl.y())
        self._press_pos = None
        self._press_win_pos = None
        self._dragging = False
        self._system_move_started = False

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Mouse wheel rescales the OSD; disabled while minimized so the
        thin capsule doesn't grow unexpectedly under the cursor."""
        if self._minimized:
            return
        # angleDelta().y() is +120 per "tick" upward, -120 downward.
        delta = event.angleDelta().y()
        if delta == 0:
            return
        step = SCALE_STEP if delta > 0 else -SCALE_STEP
        new_scale = max(SCALE_MIN, min(SCALE_MAX, self._scale + step))
        if new_scale != self._scale:
            self._scale = new_scale
            self._apply_size()
            self.update()
            self.scaledTo.emit(self._scale)
            # _apply_size preserves the top-RIGHT corner, so a resize shifts
            # the top-left. For a custom-positioned OSD the saved coordinates
            # would silently go stale and the widget would reappear at the
            # pre-resize spot after a restart — keep them in sync.
            if self._position == OSD_POSITION_CUSTOM:
                tl = self.frameGeometry().topLeft()
                self._custom_xy = (tl.x(), tl.y())
                self.movedTo.emit(tl.x(), tl.y())

    # ----------------------------------------------------------- painting

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Clear to fully transparent — WA_TranslucentBackground already does
        # this, but we set CompositionMode_Source explicitly for reliability
        # across drivers.
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        if self._minimized:
            self._paint_minimized(p, w, h)
            return

        # Skin dispatch: when a handoff skin is active, hand the whole OSD
        # over to its dedicated `paint_osd(p, rect, data, scale)` renderer.
        # The skin owns the entire panel — background, chrome, bars, ticker
        # — so the default bars / gauge code paths are skipped.
        if self._skin is not None and self._last_stats is not None:
            from PySide6.QtCore import QRectF
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setRenderHint(QPainter.TextAntialiasing, True)
            data = self._skin_data_for_paint()
            try:
                s = self._scale
                # Paint into the SAME rect height the window was sized to in
                # _apply_size — via the shared _skin_base_height() so the two
                # can never disagree and let a scoped/Codex row spill outside
                # the skin's panel.
                skin_h = int(self._skin_base_height() * s)
                self._skin.paint_osd(p, QRectF(0, 0, w, skin_h), data, self._scale)
                # Draw news inside the skin's frame: above the skin's own ticker.
                if getattr(self._skin, "WANTS_TICKER", False):
                    pad_x = 14 * s
                    if "news_bottom_pad" in self._skin.METRICS:
                        news_y = skin_h - self._skin.METRICS["news_bottom_pad"] * s
                    else:
                        ticker_h = self._skin.METRICS.get("ticker_h", NEWS_STRIP_HEIGHT) * s
                        news_y = skin_h - ticker_h - NEWS_STRIP_HEIGHT * s + 3 * s
                    # Use same font as the skin's own ticker
                    skin_fonts = getattr(self._skin, "FONTS", {})
                    from claude_usage.skins._paint import mono_font as _skin_mono
                    news_font = _skin_mono(
                        9 * s,
                        bold=True,
                        family=skin_fonts.get("family_mono", "monospace"),
                    )
                    self._draw_news_strip(p, news_y, w, pad_x, s, font=news_font)
                return
            except Exception:
                # Swallow skin-paint errors and fall through to default paint
                # so a broken skin module never leaves the OSD black. The
                # traceback goes to stderr via Qt's default path.
                import traceback
                traceback.print_exc()

        if self._view_mode == VIEW_MODE_STRIP:
            self._paint_strip(p, w, h)
            return
        if self._view_mode == VIEW_MODE_GAUGE:
            self._paint_gauge(p, w, h)
            return

        self._paint_full(p, w, h)

    def _paint_strip(self, p: QPainter, w: int, h: int) -> None:
        """Rounded strip: drag handle + three dials, laid out by _strip_layout.

        Colours come from the panel's paired light/dark tokens so the strip
        matches whichever appearance the user picked there; dial hues are the
        mid-tone menu-bar set that clears both grounds.
        """
        from claude_usage.menubar import _dial_color
        from claude_usage.panel import tokens
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        L = self._strip_layout()
        t = tokens(self._strip_dark)
        # Centre on the LAYOUT height, never the window's. If the two ever
        # disagree (seen on device), the rings, dots and grip must still
        # share one centre line -- splitting them is how the grip floated
        # away from the dials.
        cy = L["h"] / 2
        s = self._scale
        if abs(h - L["h"]) > 1 and self._strip_diag_ok():
            import sys
            print(f"claude-usage: strip paint: window {w}x{h} but layout "
                  f"{L['width']}x{L['h']:.0f} scale={self._scale:.2f}", file=sys.stderr)

        # Surface: rounded rectangle. A capsule read as a pill; this reads
        # as a control.
        radius = L["h"] * STRIP_RADIUS_FRACTION
        p.setPen(QPen(_hex_to_qcolor(t["card_edge"]), 1))
        p.setBrush(_hex_to_qcolor(t["card"], self._opacity))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), radius, radius)

        # Move handle: 2x3 dot grid, fixed size, centred on the left edge.
        p.setPen(Qt.NoPen)
        p.setBrush(_hex_to_qcolor(t["text_dim"]))
        dot, step = L["dot"], L["step"]
        hx = L["handle_x"] + dot
        for col in (0, 1):
            for row in (-1, 0, 1):
                p.drawEllipse(QPointF(hx + col * step, cy + row * step), dot, dot)

        # Scale grip (right end): the classic triangular dot grip -- rows of
        # 1, 2, 3 dots filling the lower-right corner. Same dots and spacing
        # as the move handle, so the two ends speak one language while the
        # shapes stay distinct. Drag right/down to grow, left/up to shrink.
        g = L["grip"]
        p.setPen(Qt.NoPen)
        p.setBrush(_hex_to_qcolor(t["text_dim"]))
        ox = g.left() + dot                 # 3x3 lattice filling the grip square
        oy = g.top() + dot
        for r in range(3):
            for c in range(2 - r, 3):
                p.drawEllipse(QPointF(ox + c * step, oy + r * step), dot, dot)

        ring_d, stroke = L["ring_d"], L["stroke"]
        track = _hex_to_qcolor(t["track"])
        for d in L["dials"]:
            pct, col = d["pct"], _dial_color(d["pct"], self._theme, d["kind"])
            rect = QRectF(d["ring_x"], cy - ring_d / 2, ring_d, ring_d)
            pen = QPen(track, stroke); pen.setCapStyle(Qt.FlatCap)
            p.setPen(pen); p.setBrush(Qt.NoBrush)
            p.drawEllipse(rect)
            if pct > 0:
                pen = QPen(col, stroke); pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.drawArc(rect, 90 * 16, -int(min(1.0, pct) * 360 * 16))
            p.setFont(d["font"])
            fm = p.fontMetrics()
            text = f"{int(round(pct * 100))}%"
            p.setPen(col)
            if d["inside"]:
                tx = rect.center().x() - fm.horizontalAdvance(text) / 2
            else:
                tx = d["text_x"]
            p.drawText(QPointF(tx, cy + fm.capHeight() / 2), text)

    def _paint_minimized(self, p: QPainter, w: int, h: int) -> None:
        """Thin capsule showing session utilisation."""
        track = _hex_to_qcolor(self._theme["bar_track"], 0.6)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, w, h), 3, 3)
        if self._session_pct > 0:
            fill_w = max(w * min(self._session_pct, 1.0), 4)
            p.setBrush(_bar_color(self._session_pct, self._theme))
            p.drawRoundedRect(QRectF(0, 0, fill_w, h), 3, 3)

    def _paint_gauge(self, p: QPainter, w: int, h: int) -> None:
        """Two circular-ring gauges (Session + Weekly) side-by-side.

        Each ring fills clockwise from 12 o'clock as utilisation rises. The
        ring colour tracks ``_bar_color`` so a turning-red session is just as
        alarming here as in bars mode.
        """
        s = self._scale
        radius = self._style.corner_radius * s

        # Background panel.
        bg = _hex_to_qcolor(self._theme["bg"], self._opacity)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(0, 0, w, h), radius, radius)
        if self._style.border_width > 0:
            bw = self._style.border_width * s
            border_pen = QPen(_hex_to_qcolor(self._theme.get("separator", "#000000")))
            border_pen.setWidthF(bw)
            p.setPen(border_pen)
            p.setBrush(Qt.NoBrush)
            inset = bw / 2
            p.drawRoundedRect(
                QRectF(inset, inset, w - bw, h - bw), radius, radius,
            )

        # Two columns splitting the panel; each column is one gauge stack.
        # With the Codex provider active, a second row of rings (Codex 5h /
        # 7d) is drawn beneath Claude's Session / Weekly pair.
        col_w = w / 2
        ring_d = max(50.0, min(col_w * 0.60, 70 * s))
        # Thinner stroke reads as a finer instrument; 7pt looked like a toy.
        ring_stroke = max(3.5, 5.5 * s)
        ring_rows: list[tuple[tuple[str, float, int], ...]] = [(
            ("Session", self._session_pct, self._session_reset),
            ("Weekly",  self._weekly_pct,  self._weekly_reset),
        )]
        if self._codex_available:
            ring_rows.append((
                ("Codex 5h", self._codex_session_pct, self._codex_session_reset),
                ("Codex 7d", self._codex_weekly_pct, self._codex_weekly_reset),
            ))
        # Centre each ring inside its column, with room below for labels.
        for row_idx, row in enumerate(ring_rows):
            row_top = (9 + row_idx * CODEX_GAUGE_ROW_HEIGHT) * s
            for idx, (label, pct, reset_ts) in enumerate(row):
                cx = col_w * idx + col_w / 2
                cy = row_top + ring_d / 2
                fill_color = _bar_color(pct, self._theme)
                self._draw_ring(p, cx, cy, ring_d, ring_stroke, pct, fill_color)

                # Percentage text centred in the ring.
                pct_text = f"{int(pct * 100)}%"
                pct_font_pt = max(11, int(15 * s))
                p.setFont(_mono_font(pct_font_pt, bold=True))
                p.setPen(_hex_to_qcolor(self._theme["text_primary"]))
                fm = p.fontMetrics()
                pct_w = fm.horizontalAdvance(pct_text)
                p.drawText(QPointF(cx - pct_w / 2, cy + fm.ascent() / 2 - 2 * s), pct_text)

                # Label + reset beneath the ring.
                label_y = cy + ring_d / 2 + 12 * s
                label_font_pt = max(8, int(8.5 * s))
                p.setFont(_mono_font(label_font_pt, bold=True))
                # Secondary, not primary: the number is the headline, the
                # label is just the caption telling you which number it is.
                p.setPen(_hex_to_qcolor(self._theme["text_secondary"]))
                fm = p.fontMetrics()
                lw = fm.horizontalAdvance(label)
                p.drawText(QPointF(cx - lw / 2, label_y), label)

                reset_label = _format_reset_short(reset_ts)
                if reset_label:
                    reset_font_pt = max(7, int(7 * s))
                    p.setFont(_mono_font(reset_font_pt))
                    p.setPen(_hex_to_qcolor(self._theme["text_dim"]))
                    fm = p.fontMetrics()
                    rw = fm.horizontalAdvance(reset_label)
                    p.drawText(QPointF(cx - rw / 2, label_y + 11 * s), reset_label)

        # Burn / spike badge — centred along the top strip, which sits clear
        # above both ring tops (rings begin at y = 12·s; the gauge draws no
        # title/LIVE badge up here). Same warn/crit colour as bars mode.
        burn = self._burn_alert
        if burn is not None and getattr(burn, "active", False):
            btext = _burn_badge_text(burn)
            if btext:
                p.setFont(_mono_font(max(7, int(8 * s)), bold=True))
                fm = p.fontMetrics()
                bwid = fm.horizontalAdvance(btext)
                color = (self._theme.get("crit") or "#ef4444") \
                    if getattr(burn, "severity", "") == "crit" \
                    else (self._theme.get("warn") or "#f59e0b")
                p.setPen(_hex_to_qcolor(color))
                p.drawText(QPointF(w / 2 - bwid / 2, 10 * s), btext)

        # Scoped weekly cap — a slim full-width bar spanning both columns
        # beneath the rings (a third ring would unbalance the pair).
        if self._scoped_label:
            pad_x = 14 * s
            bar_h = OSD_BAR_HEIGHT * s
            bar_w = w - 2 * pad_x
            row_y = h - 19 * s
            label = f"{self._scoped_label} {int(self._scoped_pct * 100)}%"
            p.setFont(_mono_font(max(8, int(9 * s)), bold=True))
            p.setPen(_hex_to_qcolor(self._theme["text_primary"]))
            p.drawText(QPointF(pad_x, row_y), label)
            reset_label = _format_reset_short(self._scoped_reset)
            if reset_label:
                p.setFont(_mono_font(max(7, int(7.5 * s))))
                p.setPen(_hex_to_qcolor(self._theme["text_dim"]))
                rw = p.fontMetrics().horizontalAdvance(reset_label)
                p.drawText(QPointF(w - pad_x - rw, row_y), reset_label)
            bar_y = row_y + 5 * s
            p.setPen(Qt.NoPen)
            p.setBrush(_hex_to_qcolor(self._theme["bar_track"], 0.6))
            p.drawRoundedRect(QRectF(pad_x, bar_y, bar_w, bar_h),
                              OSD_BAR_RADIUS * s, OSD_BAR_RADIUS * s)
            if self._scoped_pct > 0:
                p.setBrush(_bar_color(self._scoped_pct, self._theme))
                p.drawRoundedRect(
                    QRectF(pad_x, bar_y, max(bar_w * self._scoped_pct, bar_h), bar_h),
                    OSD_BAR_RADIUS * s, OSD_BAR_RADIUS * s)

    def _draw_ring(
        self,
        p: QPainter,
        cx: float,
        cy: float,
        diameter: float,
        stroke: float,
        fraction: float,
        fill_color: QColor,
    ) -> None:
        """Draw the track + filled-arc pair that make up one gauge."""
        track_pen = QPen(_hex_to_qcolor(self._theme["bar_track"], 0.7))
        track_pen.setWidthF(stroke)
        track_pen.setCapStyle(Qt.FlatCap)
        p.setPen(track_pen)
        p.setBrush(Qt.NoBrush)
        rect = QRectF(cx - diameter / 2, cy - diameter / 2, diameter, diameter)
        p.drawEllipse(rect)

        if fraction <= 0:
            return

        # Fill arc — Qt measures angles in sixteenths of a degree. 90° * 16
        # starts at 12 o'clock; a negative span sweeps clockwise as fraction
        # grows, matching how the bar version fills left→right.
        fill_pen = QPen(fill_color)
        fill_pen.setWidthF(stroke)
        fill_pen.setCapStyle(Qt.RoundCap)
        p.setPen(fill_pen)
        start_angle = 90 * 16
        span = -int(min(1.0, max(0.0, fraction)) * 360 * 16)
        p.drawArc(rect, start_angle, span)

    def _paint_full(self, p: QPainter, w: int, h: int) -> None:
        s = self._scale
        # Per-theme corner radius; default keeps the historical 12px curve.
        radius = self._style.corner_radius * s

        # Background
        bg = _hex_to_qcolor(self._theme["bg"], self._opacity)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(0, 0, w, h), radius, radius)
        # Receipt skin: overlay a subtle paper-grain stripe pattern so the
        # panel reads as thermal paper instead of flat fill.
        if self._style.decoration == "receipt":
            self._paint_paper_grain(p, w, h)
        # Optional heavy border — brutalist theme uses 2px for the Swiss-grid
        # vibe; receipt uses 1px for a paper-edge feel.
        if self._style.border_width > 0:
            bw = self._style.border_width * s
            border_pen = QPen(_hex_to_qcolor(self._theme.get("separator", "#000000")))
            border_pen.setWidthF(bw)
            p.setPen(border_pen)
            p.setBrush(Qt.NoBrush)
            inset = bw / 2
            p.drawRoundedRect(
                QRectF(inset, inset, w - bw, h - bw), radius, radius,
            )

        pad_x = 14 * s
        pad_y = 10 * s
        bar_h = OSD_BAR_HEIGHT * s
        bar_r = OSD_BAR_RADIUS * s
        bar_w = w - 2 * pad_x
        font_label = max(9, 10 * s)
        font_small = max(7, 7.5 * s)
        font_title = max(7, 8 * s)

        # Title — optional skin-specific ASCII prefix (e.g. "┌─ " for
        # terminal). The prefix is drawn inline so the rozet/LIVE badge
        # positioning still works off the full string width.
        title_font = _mono_font(int(font_title))
        p.setFont(title_font)
        p.setPen(_hex_to_qcolor(self._theme["text_dim"]))
        title_y = pad_y + 7 * s
        title_text = self._style.title_prefix + "CLAUDE"
        p.drawText(QPointF(pad_x, title_y), title_text)

        # Subagent rozet — only shown when > 0 so single-session users aren't
        # bothered by a permanent "0 agents" noise. Rendered just right of
        # CLAUDE in the theme's link colour to signal "active thing".
        if self._active_subagents > 0:
            title_w = p.fontMetrics().horizontalAdvance(title_text)
            rozet = f"⚙ {self._active_subagents}"
            p.setPen(_hex_to_qcolor(self._theme["text_link"]))
            p.drawText(QPointF(pad_x + title_w + 6 * s, title_y), rozet)

        # Right-aligned title-row badges: LIVE first (at the right edge), then
        # the burn/spike badge just to its left. Both use the title font that's
        # still active, and neither changes the OSD height.
        badge_right = w - pad_x

        # Live indicator — only drawn when there's recent assistant activity.
        # Renders as `● LIVE 1.2k tok/min` right-aligned against the title.
        if self._is_live and self._live_tpm > 0:
            tpm = self._live_tpm
            tpm_text = f"{tpm / 1000:.1f}k" if tpm >= 1000 else f"{int(tpm)}"
            live_text = f"● LIVE {tpm_text} tok/min"
            live_width = p.fontMetrics().horizontalAdvance(live_text)
            # Green-ish per-theme accent; fallback covers older themes.
            p.setPen(_hex_to_qcolor(self._theme.get("live_indicator", "#4ade80")))
            p.drawText(QPointF(badge_right - live_width, title_y), live_text)
            badge_right -= live_width + 8 * s

        # Burn / spike / retry-storm badge — bright warn/crit colour, drawn on
        # the title row just left of LIVE. Signals "the 5h window is burning
        # fast" or "a turn/retry-loop spiked tokens" at a glance.
        burn = self._burn_alert
        if burn is not None and getattr(burn, "active", False):
            btext = _burn_badge_text(burn)
            if btext:
                bwid = p.fontMetrics().horizontalAdvance(btext)
                color = (self._theme.get("crit") or "#ef4444") \
                    if getattr(burn, "severity", "") == "crit" \
                    else (self._theme.get("warn") or "#f59e0b")
                p.setPen(_hex_to_qcolor(color))
                p.drawText(QPointF(badge_right - bwid, title_y), btext)

        # --- Session row ---
        y = pad_y + 16 * s
        self._draw_row(
            p, y, w, pad_x, bar_w, bar_h, bar_r, font_label, font_small,
            label="Session",
            pct=self._session_pct,
            reset_label=_format_reset_short(self._session_reset),
        )

        # --- Weekly row ---
        y2 = y + 15 * s + bar_h + 10 * s
        self._draw_row(
            p, y2, w, pad_x, bar_w, bar_h, bar_r, font_label, font_small,
            label="Weekly",
            pct=self._weekly_pct,
            reset_label=_format_reset_short(self._weekly_reset),
        )

        # --- Scoped weekly row (e.g. "Fable") — only when the API reports it ---
        if self._scoped_label:
            y3 = y2 + 15 * s + bar_h + 10 * s
            self._draw_row(
                p, y3, w, pad_x, bar_w, bar_h, bar_r, font_label, font_small,
                label=self._scoped_label,
                pct=self._scoped_pct,
                reset_label=_format_reset_short(self._scoped_reset),
            )
            y2 = y3  # push the footer below the extra row

        # --- Codex provider rows — only when the codex provider is active ---
        if self._codex_available:
            for label, pct, reset_ts in (
                ("Codex 5h", self._codex_session_pct, self._codex_session_reset),
                ("Codex 7d", self._codex_weekly_pct, self._codex_weekly_reset),
            ):
                y2 = y2 + 15 * s + bar_h + 10 * s
                self._draw_row(
                    p, y2, w, pad_x, bar_w, bar_h, bar_r, font_label, font_small,
                    label=label,
                    pct=pct,
                    reset_label=_format_reset_short(reset_ts),
                )

        # --- Ticker strip / receipt footer (below the weekly row) ---
        # Receipt skin replaces the scrolling ticker with a dotted
        # perforation line + centred "— THANK YOU —" footer, matching the
        # thermal-chit design. The actual 1D barcode lives in the popup
        # footer (see widget.py), not here — it's a statement stamp, not
        # part of the at-a-glance overlay.
        footer_y = y2 + 15 * s + bar_h + 6 * s
        if self._style.decoration == "receipt":
            self._paint_receipt_footer(p, pad_x, footer_y, w - 2 * pad_x, s)
        elif self._ticker_enabled:
            self._draw_ticker(p, footer_y, w, pad_x, s)
        # News strip: right after the ticker row
        self._draw_news_strip(p, footer_y + 16 * s, w, pad_x, s)

    def _draw_ticker(
        self,
        p: QPainter,
        y: float,
        w: int,
        pad_x: float,
        s: float,
    ) -> None:
        """Right-to-left scrolling tape of recent per-turn costs and news."""
        has_costs = bool(self._ticker_items)
        has_news = bool(self._news_items)
        if not has_costs and not has_news:
            return

        # Tape geometry: clipped to the interior width so text doesn't spill
        # past the rounded corners of the OSD.
        tape_x = pad_x
        tape_w = max(0.0, w - 2 * pad_x)
        tape_h = 14 * s
        p.save()
        p.setClipRect(QRectF(tape_x, y - tape_h * 0.1, tape_w, tape_h * 1.2))

        # Monospace keeps item widths predictable as values change.
        font = _mono_font(max(7, int(7.5 * s)))
        p.setFont(font)
        fm = p.fontMetrics()
        sep_gap = int(14 * s)
        baseline = y + tape_h - 3 * s

        # Cost items only — news is shown separately in the news strip below.
        cost_ordered = list(reversed(self._ticker_items))
        strings = [self._format_ticker_item(it) for it in cost_ordered]
        widths = [fm.horizontalAdvance(s_) + sep_gap for s_ in strings]
        strip_width = sum(widths) or 1

        x_start = tape_x + tape_w - (self._ticker_offset % strip_width)
        cost_colors = {
            "hot":    _hex_to_qcolor(self._theme["crit"]),
            "warm":   _hex_to_qcolor(self._theme["warn"]),
            "cool":   _hex_to_qcolor(self._theme["bar_blue"]),
            "dim":    _hex_to_qcolor(self._theme["text_dim"]),
        }
        thresholds = _ticker_quartile_thresholds(self._ticker_items)
        copies = max(2, int(tape_w // strip_width) + 2)
        for repeat in range(copies):
            x = x_start + repeat * strip_width
            for item, text, width in zip(cost_ordered, strings, widths):
                if x + width < tape_x:
                    x += width
                    continue
                if x > tape_x + tape_w:
                    break
                p.setPen(self._ticker_color_for(item, cost_colors, thresholds))
                p.drawText(QPointF(x, baseline), text)
                x += width

        p.restore()

    def _draw_news_strip(
        self,
        p: QPainter,
        y: float,
        w: int,
        pad_x: float,
        s: float,
        font: "QFont | None" = None,
    ) -> None:
        """Scrolling strip showing the latest news headline.

        Drawn only while the news feature is enabled — a headline cached
        from a since-disabled session must not keep rendering, or the
        "opt-in" contract quietly breaks after one toggle cycle.
        """
        if not self._news_enabled or not self._latest_headline:
            return
        tape_x = pad_x
        tape_w = max(0.0, w - 2 * pad_x)
        tape_h = 13 * s
        p.save()
        p.setClipRect(QRectF(tape_x, y - tape_h * 0.1, tape_w, tape_h * 1.2))
        if font is None:
            font = _mono_font(max(7, int(7.5 * s)))
        p.setFont(font)
        fm = p.fontMetrics()
        baseline = y + tape_h - 3 * s
        text = "📰 " + self._latest_headline + "    "
        text_w = fm.horizontalAdvance(text) or 1
        news_color = _hex_to_qcolor(self._theme.get("text_link", self._theme.get("warn", "#f59e0b")))
        p.setPen(news_color)
        x_start = tape_x + tape_w - (self._news_offset % text_w)
        copies = max(2, int(tape_w // text_w) + 2)
        for i in range(copies):
            x = x_start + i * text_w
            if x + text_w < tape_x or x > tape_x + tape_w:
                continue
            p.drawText(QPointF(x, baseline), text)
        p.restore()

    @staticmethod
    def _format_ticker_item(item: TickerItem) -> str:
        """Compact tape label: ``$0.156 ← Read · 2.3k``."""
        cost = item.cost_usd
        if cost >= 1.0:
            cost_text = f"${cost:.2f}"
        elif cost >= 0.01:
            cost_text = f"${cost:.3f}"
        else:
            cost_text = f"${cost:.4f}"
        tool = item.tool or "turn"
        out = item.output_tokens
        if out >= 1000:
            out_text = f"{out / 1000:.1f}k"
        else:
            out_text = str(out)
        return f"{cost_text} ← {tool} · {out_text}"

    @staticmethod
    def _ticker_color_for(
        item: TickerItem,
        palette: dict,
        thresholds: tuple[float, float, float],
    ) -> QColor:
        """Color each item by its quartile rank in the current buffer.

        Using relative thresholds instead of fixed dollar tiers keeps the
        tape visually informative across wildly different workflows —
        Haiku-only sessions and Opus tool-heavy sessions both show the full
        colour range. Cheapest 25% dim, next 25% blue, next 25% amber,
        top 25% red.
        """
        cool_thr, warm_thr, hot_thr = thresholds
        if item.cost_usd >= hot_thr:
            return palette["hot"]
        if item.cost_usd >= warm_thr:
            return palette["warm"]
        if item.cost_usd >= cool_thr:
            return palette["cool"]
        return palette["dim"]

    def _draw_row(
        self,
        p: QPainter,
        y: float,
        w: int,
        pad_x: float,
        bar_w: float,
        bar_h: float,
        bar_r: float,
        font_label: float,
        font_small: float,
        label: str,
        pct: float,
        reset_label: str,
    ) -> None:
        """Draw one row: label on the left, reset + percentage on the right, bar below."""
        # Label + percentage baseline. Some skins (dashboard, brutalist)
        # uppercase the row label for a datasheet feel.
        p.setFont(_mono_font(int(font_label)))
        p.setPen(_hex_to_qcolor(self._theme["text_primary"]))
        baseline = y + 10 * self._scale
        label_text = label
        if self._style.label_case == "upper":
            label_text = label.upper()
        elif self._style.label_case == "lower":
            label_text = label.lower()
        p.drawText(QPointF(pad_x, baseline), label_text)

        pct_text = f"{int(pct * 100)}%"
        pct_width = p.fontMetrics().horizontalAdvance(pct_text)
        p.drawText(QPointF(w - pad_x - pct_width, baseline), pct_text)

        # Reset-time (between label and percentage, small font)
        if reset_label:
            p.setFont(_mono_font(int(font_small)))
            p.setPen(_hex_to_qcolor(self._theme["text_dim"]))
            rw = p.fontMetrics().horizontalAdvance(reset_label)
            p.drawText(
                QPointF(w - pad_x - pct_width - 8 * self._scale - rw, baseline),
                reset_label,
            )

        # Bar — skin-specific style: ASCII block glyphs for terminal, sharp
        # rectangle for brutalist/receipt, classic rounded pill otherwise.
        bar_y = y + 14 * self._scale
        self._draw_bar(p, pad_x, bar_y, bar_w, bar_h, bar_r, pct)

    def _paint_paper_grain(self, p: QPainter, w: int, h: int) -> None:
        """Thin horizontal stripes every 4px — thermal-paper grain texture."""
        ink = _hex_to_qcolor(self._theme["text_primary"], 0.04)
        pen = QPen(ink)
        pen.setWidthF(1.0)
        p.setPen(pen)
        step = max(3, int(4 * self._scale))
        y = 0
        while y < h:
            p.drawLine(QPointF(0, y), QPointF(w, y))
            y += step

    def _paint_receipt_footer(
        self, p: QPainter, x: float, y: float, w: float, s: float,
    ) -> None:
        """Receipt OSD footer: dotted perforation + centred THANK-YOU line."""
        # Row 1: perforation dots across the full width.
        dim_pen = QPen(_hex_to_qcolor(self._theme["text_dim"]))
        dim_pen.setWidthF(1.0)
        p.setPen(dim_pen)
        font = _mono_font(max(7, int(9 * s)))
        p.setFont(font)
        fm = p.fontMetrics()
        dot_w = fm.horizontalAdvance(".")
        n_dots = max(8, int(w / max(dot_w, 1)))
        perf_baseline = y + fm.ascent() - 1
        p.drawText(QPointF(x, perf_baseline), "." * n_dots)

        # Row 2: centred "— THANK YOU —" in an even smaller font.
        tiny = _mono_font(max(7, int(7 * s)))
        p.setFont(tiny)
        fm2 = p.fontMetrics()
        thanks = "— THANK YOU —"
        tw = fm2.horizontalAdvance(thanks)
        thanks_y = perf_baseline + fm2.ascent()
        p.drawText(QPointF(x + (w - tw) / 2, thanks_y), thanks)

    def _paint_barcode(
        self, p: QPainter, x: float, y: float, w: float, h: float,
    ) -> None:
        """Deterministic 1D barcode strip — no runtime randomness so the
        rendered output is pixel-stable for screenshots."""
        ink = _hex_to_qcolor(self._theme["text_primary"])
        bg_fill = _hex_to_qcolor(self._theme["bg"])
        p.setPen(Qt.NoPen)
        p.setBrush(bg_fill)
        p.drawRect(QRectF(x, y, w, h))
        # Pattern chosen to look like a real UPC-A-ish barcode without being
        # a valid encoding of anything. Digits = bar widths in units.
        pattern = (1, 2, 1, 3, 2, 1, 1, 3, 1, 2, 2, 1, 3, 1, 2, 1, 1, 3, 1, 2,
                   1, 3, 2, 1, 1, 2, 3, 1, 2, 1, 3, 1)
        total_units = sum(pattern) * 2  # bars + gaps
        unit = w / total_units
        cx = x
        for i, width_units in enumerate(pattern):
            bw = width_units * unit
            if i % 2 == 0:  # even index = bar (ink)
                p.setBrush(ink)
                p.drawRect(QRectF(cx, y, bw, h))
            cx += bw

    def _draw_bar(
        self,
        p: QPainter,
        x: float,
        y: float,
        w: float,
        h: float,
        radius: float,
        pct: float,
    ) -> None:
        """Render one usage bar in the style dictated by the current theme."""
        style = self._style.bar_style
        s = self._scale
        if style == BAR_STYLE_ASCII:
            # Monospace block glyphs — htop / btop vibe. We draw with the
            # mono font so each cell is a fixed cell width; filled vs empty
            # separate at the fraction boundary.
            cells = max(10, int(w / max(6 * s, 1)))
            filled = round(pct * cells)
            font = _mono_font(max(7, int(10 * s)))
            p.setFont(font)
            fill = _bar_color(pct, self._theme)
            track = _hex_to_qcolor(self._theme["bar_track"], 0.8)
            fm = p.fontMetrics()
            cell_w = fm.horizontalAdvance("█")
            baseline = y + h + fm.ascent() / 2 - 1
            cx = x
            for i in range(cells):
                p.setPen(fill if i < filled else track)
                p.drawText(QPointF(cx, baseline), "█" if i < filled else "░")
                cx += cell_w
            return

        if style == BAR_STYLE_BLOCK:
            # Sharp-cornered rectangles — brutalist / receipt vibe.
            p.setPen(Qt.NoPen)
            p.setBrush(_hex_to_qcolor(self._theme["bar_track"], 0.8))
            p.drawRect(QRectF(x, y, w, h))
            if pct > 0:
                p.setBrush(_bar_color(pct, self._theme))
                p.drawRect(QRectF(x, y, w * min(pct, 1.0), h))
            return

        # Default: classic rounded pill.
        p.setPen(Qt.NoPen)
        p.setBrush(_hex_to_qcolor(self._theme["bar_track"], 0.6))
        p.drawRoundedRect(QRectF(x, y, w, h), radius, radius)
        if pct > 0:
            fill_w = max(w * min(pct, 1.0), h)
            p.setBrush(_bar_color(pct, self._theme))
            p.drawRoundedRect(QRectF(x, y, fill_w, h), radius, radius)
