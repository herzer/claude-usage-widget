"""Link state: is what the widget shows LIVE, STALE, or DISCONNECTED?

The collector deliberately falls back to the last known values when a poll
fails, so the numbers on screen never blank out. That is only honest if the
surfaces say, loudly, when those numbers stopped being current -- a widget
that exists to pace a weekly budget must never let seventeen-hour-old
figures pass for live ones. This module is the single source of that
judgement; every surface renders from it and never re-derives it.
"""
from __future__ import annotations

from dataclasses import dataclass

LIVE = "live"
STALE = "stale"
DISCONNECTED = "disconnected"

# Errors that no amount of waiting will fix: the token is gone, rejected,
# or lacks permission. Matched against the collector's error strings.
_DISCONNECT_MARKERS = ("expired", "No credentials", "403", "not found")
AUTH_ADVICE = "Run in Terminal: claude auth login --claudeai"
# An age at or above this means "there has never been a successful fetch".
NEVER = 3 * 10 ** 8


@dataclass(frozen=True)
class LinkState:
    state: str            # LIVE / STALE / DISCONNECTED
    age_s: float          # seconds since the last SUCCESSFUL fetch
    error: str            # the collector's error string ("" when live)
    retry_after: float    # seconds the server asked us to wait, 0 if none
    headline: str         # one line for the panel / tooltip
    advice: str           # what to do, "" when nothing

    @property
    def live(self) -> bool:
        return self.state == LIVE


def age_text(seconds: float) -> str:
    """Written-out age: '45 seconds', '1 minute', '2 hours 5 minutes',
    '3 days'. Singular and plural are both written; never 'minute(s)'."""
    s = max(0, int(round(seconds)))
    if s < 60:
        return "1 second" if s == 1 else f"{s} seconds"
    m, h, d = s // 60, s // 3600, s // 86400
    if d >= 1:
        hh = (s % 86400) // 3600
        out = "1 day" if d == 1 else f"{d} days"
        if hh:
            out += " " + ("1 hour" if hh == 1 else f"{hh} hours")
        return out
    if h >= 1:
        mm = (s % 3600) // 60
        out = "1 hour" if h == 1 else f"{h} hours"
        if mm:
            out += " " + ("1 minute" if mm == 1 else f"{mm} minutes")
        return out
    return "1 minute" if m == 1 else f"{m} minutes"


def age_short(seconds: float) -> str:
    """Compact age for the strip's badge: '45s', '17m', '3h', '2d'."""
    s = max(0, int(round(seconds)))
    if s >= NEVER:
        return "?"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def classify(error: str, retry_after: float, age_s: float,
             stale_after_s: float = 600.0) -> LinkState:
    """Decide the link state from the last poll's error and the time since
    the last successful fetch.

    * DISCONNECTED -- the error says waiting cannot help (expired token, no
      credentials, 403). The numbers on screen are last-known and there is
      a specific thing the user must do.
    * STALE -- any other error, OR no error but nothing fresh for longer
      than ``stale_after_s`` (a stopped timer, a hung poll).
    * LIVE -- the last poll succeeded and it was recent.
    """
    err = (error or "").strip()
    ra = float(retry_after or 0.0)
    age = max(0.0, float(age_s))
    last = "No data received yet." if age >= NEVER else f"Last data {age_text(age)} ago."
    if any(m.lower() in err.lower() for m in _DISCONNECT_MARKERS):
        return LinkState(DISCONNECTED, age, err, ra,
                         f"Disconnected. {last} {err}", AUTH_ADVICE)
    if err:
        wait = f" Retrying in {age_text(ra)}." if ra > 0 else " Retrying."
        return LinkState(STALE, age, err, ra, f"Stale. {last} {err}.{wait}", "")
    if age > stale_after_s:
        text = "Stale. No data received yet." if age >= NEVER else f"Stale. No update for {age_text(age)}."
        return LinkState(STALE, age, "", 0.0, text, "")
    return LinkState(LIVE, age, "", 0.0, f"Live. Updated {age_text(age)} ago.", "")
