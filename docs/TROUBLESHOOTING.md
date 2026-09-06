# Troubleshooting: why the bars are blank

Everything below was learned on one Mac over one long night, checking each claim against the actual HTTP status rather than the widget's error string. It is written down so nobody repeats it.

## The three errors mask each other

`--%` / `0%` on screen comes from three different failures, and they stack: a 429 hides a 401, and both look identical to "no data yet". The widget's error strings are accurate — read them, and read the `retry_after_seconds` field next to them:

```bash
.venv/bin/claude-usage --once | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['rate_limit_error'], d.get('retry_after_seconds'))"
```

- **`No credentials …`** — the token was not found in any of the three places the widget looks (below). The only case where a Keychain permission prompt is involved.
- **`Credentials expired …`** — HTTP **401**. A token *was* found and the API rejected it.
- **`Rate limited …`** — HTTP **429**. The widget keeps its last known values; if it never had any, that is `0%`.
- **`OAuth usage error 403`** — HTTP **403**, authenticated but not permitted. Seen with a `claude setup-token` token: it is built for headless inference and lacks the scope this endpoint wants.

## The usage endpoint has a tiny budget, and the penalty escalates

`/api/oauth/usage` is a low-budget endpoint shared with Claude Code itself. Four authenticated requests in two minutes were enough to earn `Retry-After: 3600`. Unauthenticated failures (401s) did not count; authenticated ones did. Requests made *inside* a penalty window re-trigger it.

Upstream parsed `Retry-After` and then discarded it, capping its own backoff at `refresh_max_seconds` (300 s) — so against a 647 s penalty every poll landed inside the window and the widget locked itself out indefinitely. This fork carries `Retry-After` onto the stats and the poll timer waits it out, even past the cap.

**While diagnosing: one request, then wait.** A checking loop is the single most effective way to make the diagnosis look worse than it is.

## Where the token comes from

Lookup order, first hit wins:

1. `CLAUDE_CODE_OAUTH_TOKEN` in the environment
2. `~/.claude/.credentials.json`
3. macOS Keychain, service `Claude Code-credentials`

Consequences:

- **A stale file shadows a good Keychain token forever.** If you ever stored a token in `.credentials.json`, that is what the widget reads, regardless of what the CLI has since done.
- **The CLI refreshes its token only when it makes a real API call.** `claude auth status` reports `loggedIn: true` as long as a blob exists and re-saves it *without* refreshing; launching the TUI and quitting makes no call. If the CLI's refresh token has itself expired, the CLI cannot renew anything — a one-turn `claude -p` will say `401 OAuth access token has expired. Re-authenticate to continue.` The only fix is `claude auth login --claudeai`.
- **Inspect the Keychain item without exposing the secret:** `security find-generic-password -s "Claude Code-credentials"` (no `-w`) prints the account and modification date only. A `mdat` older than your last sign-in tells you the CLI is not writing there.

## The token lapses every day — and what renews it

After `claude auth login` the access token in the Keychain lasts roughly eight hours. The CLI renews it from its refresh token **only when it makes a real API call**; `claude auth status` re-saves the blob without renewing, and the desktop app never touches the CLI's item. So if you sign in once and then work in the desktop app, the widget's polls start returning 401 the same evening.

Proven on device: a one-turn `claude -p "ok"` renews the token (the Keychain item's `mdat` advances) and the very next poll succeeds. That is what the widget's self-heal does on a 401 (`auth_refresh_via_cli`, throttled to once per fifteen minutes). It resolves the CLI binary explicitly — under launchd, `PATH` is `/usr/bin:/bin:/usr/sbin:/sbin`, so `shutil.which("claude")` finds nothing and a naive hook silently never runs.

If the *refresh* token has expired too, that call returns `401 OAuth access token has expired. Re-authenticate to continue.` — nothing automatic can help, and the widget shows **disconnected** with the one fix: `claude auth login --claudeai`.

## How the widget tells you

Failed polls keep the last known numbers on screen, so the surfaces must say when those stopped being current. `claude_usage/link.py` is the single judgement — live, stale, or disconnected — from the last poll's error and the time since the last *successful* fetch (seeded from `~/.claude/usage-history.jsonl`, which only ever gains a line on success, so a restart cannot pretend to be fresh). The strip, panel, tray and menu all render from it, and notifications fire on transitions. If you ever see plain colors over numbers you doubt, the panel's status line is the truth.

## Things that looked like fixes and were not

- **Claude Code's `statusLine`** does not carry `rate_limits` in current versions (it sends `context_window`, `cost`, `cwd`, `model`, `session_id`, `workspace` …). Upstream's `statusline_cache_path` feature was written against an older Claude Code and cannot work. Also, `~/.claude/settings.json` is rewritten by the app, so a hand-added `statusLine` key does not survive a restart.
- **A `claude setup-token` token** authenticates but gets 403 from the usage endpoint.
- **Reimplementing the OAuth refresh inside the widget** cannot help when the refresh token is what expired.

## The widget vanished

**A restart can leave nothing running (fixed in 0.13.3).** `launchctl kickstart -k` SIGTERMs the old process and starts the new one immediately. The new copy took the single-instance lock with a 100 ms timeout, so if the old one had not finished shutting down it printed "already running", exited 0, and launchd — with `KeepAlive` false — was left with no process at all. The lock now waits five seconds for a predecessor, and the LaunchAgent uses `KeepAlive { SuccessfulExit: false }`, which revives a crash while still honoring a deliberate Quit. Symptom to recognize: `launchctl list | grep claude-usage` shows `-` instead of a PID, and `last exit code = 0` with nothing in the log.


- **It died with the shell that launched it.** `--detach` still leaves it in the launching process tree; a Claude Code restart takes it down. `./setup-autostart.sh` hands it to launchd, which is the fix.
- **It is on another display, minimized, or off the visible area.** `./unhide-widget.sh` quits it, resets position and minimize state, and relaunches. The saved position is clamped onto the primary screen at startup, so a plain restart also rescues it.
- **`pgrep -f` says it is running when it is not.** Any shell whose command line *quotes* the pattern matches. Decide by executable: `basename "$(ps -o comm= -p $pid)"` is `Python` for the real widget — note that `ps -o comm=` returns a full path on macOS.

## The strip resized itself into a tall box

A frameless Qt window on macOS keeps `NSWindowStyleMaskResizable`, so its corners are native resize zones — pressing the scale grip started a Cocoa resize that fought the widget's own handler. The strip now clears that mask on its NSWindow (`claude-usage: strip: native resizable mask cleared` in the log confirms it). Offscreen renders never reproduce this: there is no Cocoa.
