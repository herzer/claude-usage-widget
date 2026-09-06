# Changelog

All notable changes to this project are documented here.
This project follows [semantic versioning](https://semver.org/).

## 0.14.1 — a long rate limit no longer blinds it, and polls are logged

- **The honoured `Retry-After` is capped** (`retry_after_cap_seconds`, default 900). Honouring it in full fixed a self-sustaining lockout, but a 3600 s penalty then set the poll timer to a full hour, so the widget sat idle long after the limit had cleared. It now probes again at the cap; if still throttled, the fresh value applies.
- **One log line per poll** — time, `ok` or the error, the values, `retry_after`, and when the next poll is due. Without it, "no fresh sample" was indistinguishable from "not polling at all", and diagnosing that cost requests against the very endpoint that was throttling us.

## 0.14.0 — an app icon, and a proper launcher

- **An app icon**: one progress ring sweeping through the three dial hues — blue for the 5-hour window, green for all models, violet for the model-scoped cap. Drawn natively at every size by `tools/make-icon.py`, so it stays legible at 16 px, and built into a macOS `.icns`.
- **`tools/make-app.py` builds "Claude Usage.app"** — double-click to start the widget, or restart it if it is already running. It carries the icon and opens no Terminal.
- **The widget is a menu-bar utility now**: no Dock tile and no app-switcher entry (`macos_dock_icon` to opt back in). This also ends the Python rocket in the Dock — a Dock tile comes from the bundle, and for a process launched from an interpreter that bundle is Python's.

## 0.13.5 — the toggles work, and the handles stop flipping

- **The dial checkboxes control the strip**, not only the tray icon. With the tray off they had no visible effect at all, and the row was labelled "Menu bar" while the strip ignored it. It now reads "Show dials" and governs both surfaces; the last visible dial cannot be switched off.
- **The handles no longer flip.** Which end the scale grip sits on was recomputed from which half of the screen the strip was on, so dragging it across the middle swapped the grip and the move dots. It is now a stored preference, changed only by picking a corner preset.

## 0.13.4 — a real check mark, and the app is called Claude Usage

- **Checkboxes draw an actual tick.** Qt cannot put a glyph in `QCheckBox::indicator` without shipping an image, so the styled indicator was only ever a coloured block that read as a swatch. The panel now uses a painted checkbox whose tick colour is chosen by luminance, so it stays legible on green, blue or violet.
- **macOS calls it "Claude Usage", not "Python"** — in the menu bar, the app switcher and Force Quit. `CFBundleName` is set before Qt connects to the window server; patching afterwards is too late, because Launch Services has already registered the process.

## 0.13.3 — restart without a Terminal, and a restart that survives

- **Restart widget** in the right-click menu, and `tools/make-restart-app.py` builds a double-clickable "Restart Claude Usage.app" for when it is not running.
- **Fixed: a restart could leave nothing running.** The single-instance lock waited only 100 ms, so a `kickstart` race made the new copy exit as "already running" while launchd kept no process. It now waits five seconds for a predecessor.
- **The LaunchAgent revives a crash** (`KeepAlive { SuccessfulExit: false }`) while a deliberate Quit still stays quit.

## 0.13.2 — strip appearance is a choice

- Background opacity per appearance (`strip_bg_opacity_dark` / `_light`), **100 by default** — upstream's 0.75 `osd_opacity` had leaked into the strip. It affects only the background and border; dials, numbers and handles are always solid.
- The background lifts to solid while the pointer is over the strip (`strip_hover_solid`), so the handles are usable on any desktop.
- A contrast control per appearance (`strip_contrast_dark` / `_light`).
- An edit zone in the panel with the house stepper (− value +, hold to repeat, scroll to sweep) for the appearance it is showing.
- A stale or disconnected border stays solid regardless of opacity.

## 0.13.1 — never let stale numbers pass for live ones

- **Link state on every surface.** Live / stale / disconnected, judged in one place from the last poll's error and the time since the last *successful* fetch. The strip goes gray with an amber age badge (stale) or a red border and dashes (disconnected); the panel gains an always-visible status line with the reason and the exact fix; tray dials gray out; the menu footer reports the last *successful* update instead of the last attempt.
- **Desktop notifications** on transitions: disconnected (with the fix), stale after ten minutes, reconnected.
- **Self-healing token.** On 401 the widget makes one tiny `claude -p` call, which renews the CLI's OAuth token where `claude auth status` does not; the binary is resolved explicitly so it works under launchd. `auth_refresh_via_cli` switches it off.
- New keys: `stale_after_seconds`, `notify_stale_after_seconds`, `auth_refresh_via_cli`.

## 0.13.0 — macOS menu-bar edition (fork)

First release of the [herzer/claude-usage-widget](https://github.com/herzer/claude-usage-widget) fork.

- **Strip view**: a menu-bar-height pill with three dials (5-hour, all models, model-scoped weekly), a move handle, and a window-style resize grip; numbers move inside the rings as it grows.
- **Strip in the menu bar** (`strip_in_menubar`): floats above the macOS menu bar; Top Right parks it in the bar band.
- **Card-based panel**: 5-hour ring, 7-day limits, Week/Month activity grid from real per-hour data, dial toggles, Light/Dark switch shared with the strip.
- **Menu-bar tray dials** (`menubar_enabled`, optional): hue is the label, legible on light and dark bars.
- **Honors `Retry-After`** on 429 instead of capping backoff below the penalty window (previously a self-sustaining lockout).
- **Expired-token refresh** via the CLI on 401; the AI weekly summary can be disabled (`weekly_report_enabled`).
- Update checks point at this fork's releases; Homebrew formula removed (install from source).

## 0.12.5

### Fixed
- **"Show cost ticker" now works in the designed skins** — terminal,
  dashboard, hud, receipt and brutalist. The toggle only ever applied to the 5
  built-in themes: skins painted the ticker unconditionally, and each refresh
  restarted the marquee timer, so switching it off paused the strip for a few
  seconds and then it scrolled on regardless. With the toggle off the skin now
  gets no ticker items and the marquee stays stopped. The panel keeps its
  height on the skins by design — the strip is part of each skin's
  composition, so the row goes blank rather than the panel collapsing.
  Thanks @carr-james (#25).
- **`claude-opus-5` priced explicitly** ($5/$25, same tier as Opus 4.8) —
  the family fallback already landed on the right numbers, but every refresh
  logged an "unknown model" warning; that's gone.

## 0.12.4

### Fixed
- **`budget_projection` and `burn_alert` webhooks actually fire.** Both were
  advertised in 0.11.0, but `KNOWN_EVENTS` was never extended and the
  dispatcher silently drops unknown events — so neither webhook ever sent a
  single request. Found by a documentation audit cross-checking the config
  comment against the dispatcher; a regression test now pins every advertised
  event to the gate.
- **`show_news` added to `DEFAULT_CONFIG`** — it was the only documented key
  missing from the defaults dict (runtime behavior was already correct via
  `cfg.get` fallbacks; now `config.json.example` and the defaults agree).

### Docs
- **The GitHub wiki is retired** to a single pointer page — it had drifted
  several releases behind the README and started giving wrong answers. Its
  unique content moved into the repo first: an expanded README (FAQ section
  with a corrected privacy answer, systemd/autostart recipe, off-screen and
  Codex troubleshooting entries), `CONTRIBUTING.md`, `SECURITY.md`,
  issue/PR templates, and `docs/RELEASING.md` (maintainer checklist,
  replacing `docs/homebrew.md` and fixing its wrong sdist filename in the
  sha256 step). ~40 more stale spots fixed across README (stray Turkish
  heading, duplicate config row, outdated window-flag/Wayland/fallback
  claims). CI now runs the suite on Python 3.10–3.12 for every push and PR.

## 0.12.3

### Fixed
- **"Always on top" did nothing on X11 — and the menu looked out of sync.**
  The OSD was unconditionally typed `_NET_WM_WINDOW_TYPE_NOTIFICATION` to keep
  it out of the dock / taskbar / Alt-Tab, but X11 window managers also stack
  notification windows in a layer *above* normal ones. So turning the toggle
  off left the OSD pinned on top regardless, and the right-click menu — which
  correctly showed the setting as off — read as "wrong" against a window that
  refused to sink. Dropping the `WindowStaysOnTopHint`/`BypassWindowManagerHint`
  flags in 0.9.1 (#13) was necessary but not sufficient. The window type now
  tracks the setting: `Notification` when pinned, and unpinned it falls back to
  the `_NET_WM_WINDOW_TYPE_UTILITY` that `Qt.Tool` already sets — which
  taskbars skip just the same, so nothing is lost but the pin.

## 0.12.2

### Fixed
- **Window dragging on native Wayland.** Under a native Wayland session
  (`QT_QPA_PLATFORM=wayland`) the OSD couldn't be dragged — Wayland forbids a
  client moving its own window. It now hands the drag to the compositor via
  `QWindow.startSystemMove()`, leaving the X11 / macOS / Windows path unchanged.
  Thanks @hdogan (#23). (The drop position isn't persisted as a "custom" spot on
  Wayland, since a client can't read its own global coordinates there.)

## 0.12.1

### Changed
- **Scroll-wheel zoom now reaches 4.0×** (was 2.0×) — on hi-DPI / large displays
  a corner OSD stays readable from across the room. `SCALE_MIN`/`SCALE_STEP`
  and the default size are unchanged. Thanks @faithpricejp-source (#22).

## 0.12.0

### Added
- **Second provider: OpenAI Codex (opt-in).** Add `"codex"` to the `providers`
  config and the widget shows your local OpenAI Codex 5h/weekly usage beneath
  Claude's — two extra bars in bars view, a 2×2 ring grid in gauge — rendered
  natively in **all 11 themes**. It reads `codex app-server` over JSON-RPC
  (`account/rateLimits/read`), throttled to `codex_poll_seconds` (default 300 s)
  with an on-disk cache and a deadline-bounded read that can't hang a refresh;
  the rows auto-hide when the `codex` CLI is missing, logged out, or returns no
  data. Off by default, POSIX-only. Thanks @faithpricejp-source (#17/#18/#21).
- **statusLine-fed rate limits (opt-in).** Point `statusline_cache_path` at a
  JSON file your Claude Code `statusLine` command dumps its rate-limit payload
  to, and the widget uses it as a zero-cost, seconds-fresh source: it skips the
  `/api/oauth/usage` call while the dump is fresh (forcing a real one at most
  once per `usage_endpoint_min_seconds`) and falls back to it when the endpoint
  throttles. Off by default. Thanks @faithpricejp-source (#20).

### Fixed
- **Single-instance guard.** Launching a second `claude-usage` no longer stacks
  another OSD on top of the first — a per-user `QLockFile` makes the extra
  launch exit cleanly, and a hard-killed instance's stale lock is reclaimed
  automatically. Thanks @faithpricejp-source (#19).

## 0.11.1

### Fixed
- **Accurate pricing for current models.** `claude-fable-5` was billed at the
  Sonnet fallback (`$3/$15`) instead of its real premium tier (**`$10/$50`**),
  under-reporting Fable cost ~3.3× everywhere it's shown (cost popup, budget,
  `--statusline`, `--json`). Added explicit table entries for
  **Opus 4.8** (`$5/$25`), **Fable 5** (`$10/$50`) and **Sonnet 5**
  (`$2/$10` intro → `$3/$15` after 2026-08-31), plus a `fable` family fallback
  so future point releases inherit the right tier. Also silences the "unknown
  model" warnings these ids emitted on every refresh.

## 0.11.0

### Added
- **`--statusline`** — a one-shot CLI flag that prints one compact line
  (`S 42% · W 18% · $3.21`, plus a scoped bar when present) for Claude Code's
  native `statusLine` setting. Reuses the `--json`/`--field` collect→redact
  path, so graceful degradation (last-known restore on 429) is inherited; it
  never launches the GUI, even with `--detach`. See
  `docs/integrations/claude-code-statusline.md`.
- **Real-time burn / spike / retry-storm alerts.** A bright OSD badge
  (`▲42%` fast-burn, `▲SPIKE`, `▲STORM`) on the bars title row + gauge, plus a
  **debounced, once-per-episode** desktop notification and `burn_alert`
  webhook, when the 5-hour window burns abnormally fast or a single turn /
  retry loop spikes tokens. Fully tunable (`burn_*`, `spike_*`, `retry_storm_*`
  keys); off via `burn_alerts_enabled: false`. The badge ships for the 5
  built-in themes (the 6 skins are a follow-up; notifications fire regardless).
- **Peak-window awareness.** An unobtrusive "reduced 5h limit until …" hint in
  the detail popup during Anthropic's weekday reduced-limit window (default
  ~5–11 AM US Pacific). Data-driven and fully overridable (`peak_*` keys); the
  default Pacific path uses self-contained DST math, so it needs no `tzdata`
  and works on Windows out of the box.
- **Monthly budget cap + projection.** Set `monthly_budget_usd` > 0 to see
  month-to-date spend and a linear end-of-month projection in the popup
  (`$X / $Y this month · projected $Z`, red when over), plus a once-per-month
  notification + `budget_projection` webhook when on track to exceed the cap.
  Off (and its extra month-wide scan skipped) at the `0.0` default.

### Notes
- Extended-thinking cost breakout was investigated and **dropped as
  infeasible**: verified against 29,981 real assistant messages, `message.usage`
  reports no separate reasoning/thinking token count (it's folded into
  `output_tokens`) and on-disk thinking blocks are signature-only, so no count
  or usable proxy exists. Revisit if Claude Code starts emitting an
  `output_reasoning_tokens`-style field.

## 0.10.0

### Added
- **Model-scoped weekly usage bar** ([#15](https://github.com/bozdemir/claude-usage-widget/issues/15)).
  When Anthropic reports a separate weekly cap for a specific model — the
  new **Fable** weekly limit is the first — a third bar appears
  automatically below Session and Weekly, labelled with the model's name
  (e.g. "Fable"). It's parsed generically from the `/api/oauth/usage`
  `limits` array (the `weekly_scoped` entries), so it also covers any
  future scoped model, and it **auto-hides** when the API stops reporting
  it (the Fable cap is temporary — Anthropic moves it to usage credits
  after the free window). Rendered natively in **bars, gauge, all 6
  Claude-designed skins, and the detail popup**; the last-known value is
  retained (and expired windows cleared) across a throttled poll, exactly
  like the session/weekly bars.
- **Update-available check.** On startup and once every 24 h a daemon
  thread queries the GitHub Releases API for the latest tag; when a newer
  version is published it fires a single desktop notification
  (`Update with: pip install --upgrade claude-usage-widget`) and shows a
  banner in the right-click menu. It notifies only once per new version,
  so a user who hasn't upgraded yet isn't nagged daily, and every call is
  best-effort — a failed or throttled request is silently ignored.
- **Widget version in the right-click menu.** A dim, disabled
  `claude-usage v<version>` line at the foot of the context menu so you
  can see which build is running at a glance.

## 0.9.3

### Fixed
Twelve bugs from a comprehensive audit (10 parallel reviewers over the
post-v0.6 churn):

- **False "Credentials expired" on transient faults.** 5xx responses from
  `/api/oauth/usage` are now retried with the existing backoff, and the
  `/v1/messages` x-api-key fallback is skipped entirely for OAuth tokens —
  it could only 401 and mislabeled any server blip as an auth failure.
- **Minimized OSD hijacked clicks into the browser** when a news headline
  was cached (the news click region went negative at the 6px minimized
  height); the region now also requires the news feature to be enabled.
- **"Always on top" toggle could break the window**: re-creating the native
  window dropped the translucency / taskbar-skip / macOS-visibility
  attributes, leaving an opaque black box or a taskbar entry. All are
  re-asserted now.
- **`--detach` crashed on macOS** (AppKit init in a fork()ed child aborts);
  it now respawns a fresh process via `subprocess.Popen`.
- **News strip fixes**: fetched with the certifi SSL context (was always
  empty on macOS python.org builds), animated even when the cost ticker is
  idle (was frozen off-screen), hidden after opting back out (a cached
  headline kept rendering), and the cache honours `XDG_CONFIG_HOME`.
- **Popup cost arithmetic**: per-model rate lines now use the same
  family-fallback pricing as the computed totals, so "tokens × rate = $"
  adds up for not-yet-tabled models (both classic and skin popups).
- **Expired-window clamp bypass**: the last-known fallback now searches
  session/weekly reset timestamps independently, so a sample carrying only
  one key can't bury the other and resurrect a finished window.
- **Receipt skin paper grain** was erased by the shared painter's
  background fill every frame; custom drag positions stayed stale after a
  wheel-resize; the scrolling popup's window chrome matched the real 10px
  scrollbar width; `osd_visible: false` now restores as minimized (a truly
  hidden restore had no UI path back).

## 0.9.2

### Fixed
- **macOS: blank session/weekly from TLS verification.** Added a certifi CA
  bundle so HTTPS to `api.anthropic.com` verifies on macOS python.org builds
  (which don't trust the system keychain), which otherwise failed with
  `CERTIFICATE_VERIFY_FAILED` and blanked the bars. Complements the 0.9.1
  credential fix — the two cover different macOS failure modes. ([#14](https://github.com/bozdemir/claude-usage-widget/pull/14))

### Changed
- `certifi` is now a (small, pure-Python) runtime dependency alongside
  PySide6-Essentials; docs reframed from "single dependency" to "two
  pure-pip wheels, no system libraries".

## 0.9.1

### Added
- **"Always on top" toggle** in the right-click menu — turn it off to use the
  OSD as a normal background desktop widget the window manager stacks behind
  focused windows (`osd_always_on_top`). ([#13](https://github.com/bozdemir/claude-usage-widget/issues/13))

### Fixed
- **macOS: blank session/weekly usage.** Hardened credential loading so a
  Keychain-only install (or a GUI launch without Keychain access) no longer
  silently shows blank bars. Lookup now mirrors Claude Code: the
  `CLAUDE_CODE_OAUTH_TOKEN` env var → `~/.claude/.credentials.json` → Keychain
  (multiple service names), with an actionable error instead of a silent
  blank.

## 0.9.0

### Changed
- **Adaptive poll interval.** Default refresh is now 60s and backs off
  exponentially up to 300s when the usage endpoint rate-limits, snapping back
  on the next clean refresh (`refresh_seconds` / `refresh_max_seconds`).
- **Refined 429 handling.** A budget-based 429 (no/zero `Retry-After`) is no
  longer retried in-poll; only an explicit positive `Retry-After` is waited
  out. Expired rate-limit windows are clamped to zero on a throttled poll
  instead of resurrecting stale percentages. ([#12](https://github.com/bozdemir/claude-usage-widget/pull/12))

## 0.8.x

### Fixed
- **429 from `/api/oauth/usage` mislabeled "Credentials expired"** and blanked
  the reset countdown; now surfaces a calm "rate limited" state and retains
  the last-known reset times. ([#11](https://github.com/bozdemir/claude-usage-widget/issues/11))
- **macOS:** OSD and popups no longer auto-hide when the app loses focus
  (`WA_MacAlwaysShowToolWindow`). ([#10](https://github.com/bozdemir/claude-usage-widget/pull/10))

### Added
- **OSD Position** presets (four corners + remembered custom drag position)
  ([#4](https://github.com/bozdemir/claude-usage-widget/issues/4)), session-state
  persistence (scale / minimized / visible), and a `--detach` flag to run the
  GUI in the background.

## 0.7.x

### Added
- **Exponential backoff with jitter** on the usage fetch for transient faults.
- **Live news ticker** (opt-in) showing Anthropic/Claude headlines.

## 0.6.x

### Changed
- **Switched the primary data source to `/api/oauth/usage`** — the same
  plan-level utilisation the Claude UI shows — replacing the old per-API-key
  rate-limit header read, which under-reported real usage.

### Added
- **Six Claude-designed skins** (terminal, dashboard, hud, receipt, strip,
  brutalist) on top of the five classic palettes — 11 themes in all.
- A **theme-tinted right-click menu**, popup detail screens for every skin,
  and a per-theme loading state.

---

Earlier releases (0.1–0.5) established the core OSD overlay, detail popup,
cost estimation, forecasting, history/heatmaps, notifications, webhooks, the
localhost JSON API, CLI mode, PyPI packaging, and the Homebrew tap.
