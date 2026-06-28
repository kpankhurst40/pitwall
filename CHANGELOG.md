# Changelog

All notable changes to Pitwall are documented here. The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- **The heat ring follows the focused CLI again (v0.9.2).** Clicking into a
  monitored Claude CLI is supposed to switch the headline heat ring to *that*
  session — a feature that quietly broke when Claude Code 2.1.x stopped registering
  terminal sessions. The ring's "which session is in this window?" logic relied on a
  learned vote that only accrues when a session is marked busy/just-worked while
  focused; that signal disappeared, so the ring fell back to the most-recently-active
  session instead of the one you clicked into. Fixed by resolving the focused window
  through the hook-recorded session→window map in reverse (the same deterministic,
  re-verified evidence the row-click window-flash already uses) before falling back to
  the old vote/title path. Guarded by `tests/test_focus_sidmap.py`.

### Changed
- **Session rows now show "where we left off", not the launch instruction (v0.9.1).**
  Each Claude-session row used to be labelled with the native AI session title — which
  is just the first thing you told that session ("Read last memory", "Follow resume
  grid instructions"), uninformative and often identical across sessions. Rows now
  show the curated pitstop checkpoint recap instead, matched to the session by the work
  folder named *inside* the checkpoint file (slugs drift). Falls back to the AI title,
  then the project folder, when a session has no checkpoint. (Self-contained recap
  reader in `ts_core`; The Grid keeps its own copy — the two apps stay untangled.)

### Removed
- **The Tkinter "Lite" look is gone — Pitwall is now Qt-only.** Pitwall ships as a
  single PySide6 widget. The first-run "Lite vs Crisp" picker is gone too: the
  launcher now just makes sure PySide6 is installed (offering a one-time
  `pip install` if it's missing) and starts the widget. Earlier entries below that
  mention "both looks" or the "Crisp look" refer to that older two-look design.

### Added
- **Start with Windows.** A new switch in **Settings → Identity → Window** registers
  Pitwall to launch automatically when you sign in to Windows (a per-user entry, no
  admin needed). The switch reads the real registration state every time you open
  Settings, so it can't drift from a change made elsewhere (e.g. Task Manager →
  Startup). Off by default.
- **Session details on right-click.** Right-click any CLI session row for a
  troubleshooting view: the session's full id, its process id verified against the live
  process, the window Pitwall would flash *and the evidence for why*, background-job
  state, working folder, transcript file path, model, and this window's spend. A
  **copy all** control puts the whole thing on the clipboard so you can paste it into a
  chat or a bug report.
- **"stuck" tag for blocked background jobs.** A background job whose own state file says
  it is working-but-blocked (waiting on something nobody can see) now wears an amber
  *stuck* tag next to its row, instead of posing as a quietly idle session.

### Fixed
- **Background jobs that respawn under a new session id are still tagged `bg`.** The job
  daemon can restart a background session under a fresh id its state file never mentions;
  Pitwall now also recognizes background sessions by their process ancestry, so a respawned
  twin can't pose as a normal windowed session (and can't be bound to the wrong window).
- **A freshly started CLI shows up within ~2 seconds.** New sessions used to be invisible
  for up to one full refresh interval — exactly the moment after a restart when you most
  want to find the new window. Pitwall now watches for session processes appearing or
  exiting and refreshes immediately.
- **Only one widget at a time — for real.** The single-instance guard could miss an
  already-running copy on Windows and launch a second widget. A timing quirk in how the
  Windows "is one already running?" check read its result has been fixed, so a second launch
  now reliably shows the *"already running"* note and leaves the one widget alone.
- **Pop-up windows no longer split across two monitors.** When the widget sat on a second
  screen, the Settings and Token Details windows could open straddling the gap between
  monitors (or partly off-screen). They now always open fully on the same screen as the
  widget.
- **Bigger text no longer overlaps in the session list.** At larger font sizes the CLI
  session rows could collide (the session name overlapping the model and the numbers).
  The session-name column now flexes to the space available and the card widens with the
  text, so rows stay clean at every size.
- **Launching a second time now tells you what happened.** Before, launching Pitwall
  again (via the shortcut or `Pitwall.bat`) while it was already open did nothing at
  all — which looked like a broken launch button. It now shows a friendly *"Pitwall
  is already open"* note pointing you to the widget already on your screen.
- **Settings confirms when you save.** Clicking **Save** in the Settings window used
  to close it instantly with no feedback. It now shows a brief green *"Saved ✓"* before the
  window closes, so you can see the change took.

## [1.1.0] — 2026-06-06

### Added
- **A crisp new look — and a one-time picker to choose it.** Pitwall now comes in
  two interchangeable "looks" over the same engine: **Lite** (the original Tkinter widget —
  built into Python, nothing to download) and **Crisp** (a new PySide6 widget that's sharp
  and smooth on high-DPI / 4K screens). The first time you launch it, a small picker asks
  which you'd like. Crisp needs the **PySide6** package; if it's missing, the picker offers
  to fetch it for you (`pip install PySide6` — the only time Pitwall touches the
  network) and falls back to Lite automatically if you're offline or decline. Re-open the
  picker any time with `launcher.py --choose`.
- **Conversation heat + a save nudge (Crisp look).** The Crisp widget shows how "full" your
  current chat has become — `this chat · Light / Filling / Heavy / Hand off` — and wraps the
  card in a heat ring that warms **green → amber → red** as the conversation grows (and
  gently pulses, faster and hotter, as you near a hand-off). A plain-English pill on the
  headline tells you what to do about it: *All clear → Looking good → Maybe save soon → Save
  & start fresh → Hand off now*. It surfaces the single highest-impact habit — save and
  start a fresh session — and makes it impossible to miss.
- **Compact mode** — a ▾/▸ toggle collapses the widget to a tiny strip: a top line with
  the pace colour dot, the reset countdown, and the open-CLI count, then one minimal
  status row per open window (context-heat dot · name · busy/idle/age). The strip resizes
  itself as windows open and close. Click ▸ to expand back to the full card; the
  collapsed/expanded choice is remembered between runs.
- **Drift report** — every time you sync the real numbers off Settings → Usage, the
  widget now logs what it *thought* vs what you *told it* (to `pitwall_corrections.jsonl`).
  A new "↳ drift" line summarises how often you correct it and, crucially, whether the
  error is in the **reset clock** (the transcript-gap guess) or the **amount**
  (coverage/ceiling) — they have different fixes. Tap it for the full report.
- **Single-instance guard.** Pitwall now refuses to run two copies at once — a
  second copy would only show the same numbers and could clobber your synced
  calibration in the shared config file. Launching it again pops up *"Pitwall is
  already running"* and exits, leaving the existing widget alone. (Windows uses a
  session-local named mutex, auto-released on exit; Mac/Linux use a PID lockfile.)

### Changed
- **One shared engine under both looks.** All the token math, pricing, transcript reading,
  config, and the gauges (which ceiling to measure against, the pace verdict, the
  projection line, the save nudge) now live in a single shared module (`ts_core.py`) that
  both the Lite and Crisp widgets call. There is exactly one copy of every number, so the
  two looks can never drift apart and a fix lands in both at once. (Internal refactor — no
  change to what you see.)
- **Window always hugs its content; the whole card is a drag handle.** The height now
  auto-fits the content (it's no longer a stored/draggable dimension), so there's never a
  dead near-black band below the last row — the old behaviour could leave the window
  larger than its content, which looked like a black border and wasn't grabbable. Width
  stays adjustable via the right edge. You can now move the widget by grabbing almost
  anywhere on its body (not just the header); buttons and links still do their own thing.
  The crisp 1px edge keyline is kept. (Design: Sarah, 2026-06-05.)
- **Settings dialog reflects the front page.** The "% used" field under CURRENT SESSION now
  pre-fills with Pitwall's live estimate (the same number the front page leads with)
  instead of the last value you typed, with a reference line showing both the live % and
  what was last synced + how long ago. (Confirming-and-saving the pre-filled estimate no
  longer logs a bogus zero-gap into the drift history.)

### Fixed
- **The Settings panel is now movable and always opens on one screen.** It's a frameless
  window (no title bar), so previously there was no way to move it — and when the widget
  sat near a screen edge it opened split across two monitors. Now you can drag it by
  grabbing almost anywhere on its body (the entry fields and buttons still work normally),
  and it opens clamped fully inside the monitor the widget is on. (Same fix applied to the
  other pop-ups — plan picker, reset editor, drift report, hand-off.)
- **Window now rolls to $0 the instant the countdown hits zero**, instead of sitting at
  `0:00:00` with the previous window's spend still showing for up to one refresh cycle
  (which read as "it didn't reset"). The countdown crossing zero now forces an immediate
  re-read.
- **Expanded session rows align cleanly.** The per-session `$` amounts and state words now
  sit in fixed-width right-aligned columns (mirroring the compact strip), and long session
  names truncate with enough room so they no longer collide with the model chip.

## [1.0.0] — 2026-06-04

First public release. A single-file, no-dependency Tkinter widget for tracking
Claude Code usage.

### Added
- **5-hour rolling window** view: API-equivalent value used + token count.
- **Per-model split** of spend (Opus / Sonnet / Haiku), colour-coded; each model
  priced at its own rate.
- **Pace dot** that judges whether you're projected to run dry before the reset
  (scaled to your real ceiling), with a calm grace period right after a reset.
- **"% of window used"** bar with a plain-English will-I-make-it status line.
- **Resets-in countdown.**
- **Per-CLI-session list** with real open/closed detection (reads
  `~/.claude/sessions/<pid>.json` and verifies the live PID), context-heat colour,
  per-session spend, and the model each session is running.
- **↻ handoff** — open a fresh terminal running Claude in the same project folder.
- **Sync from Settings → Usage (calibration)** — pin the real "% used" and reset,
  back-calculate your true per-window ceiling, and show the browser's number next
  to Pitwall's own estimate to surface drift. Includes **weekly limits** (All models +
  Sonnet-only).
- **⚙ Settings panel** — rename the widget (name + tagline), pick your tier
  (Free / Pro / Max 5× / Max 20×), and sync — all in one place.
- **Hover tooltip** on the value explaining it's an API-equivalent estimate (not a
  bill) and why it isn't your exact account total.
- **Window anchoring** — when a reset is pinned, "this window" is computed from the
  real reset time, keeping Pitwall aligned with Claude's window.
- Draggable, resizable, always-on-top; remembers position and size.
- Rotating efficiency tips; a stronger nudge when you're burning fast.

### Notes
- Prices reflect current Anthropic list rates (Opus $5/$25, Sonnet $3/$15,
  Haiku $1/$5 per 1M tokens). The 1M-context models bill standard rates at all
  context sizes (no long-context premium). Edit `RATES` in `pitwall_lite.py` if prices
  change.
- Pitwall tracks **Claude Code (CLI) usage only**; Desktop and the website keep their
  usage server-side. It makes no network calls and sends no telemetry.

---

<!--
Development history that fed into 1.0.0 (oldest first):
  Initial widget · cache-write pricing by TTL + plan-limit selector · default plan
  Max 5× · per-model spend · plain-English limit line · calibrate from Settings →
  Usage + rescale pace + explain the $ · correct Opus pricing to $5/$25 · anchor
  the window to the real reset + damp the pace dot · gear-icon access + enforce the
  reset caveat · editable name/tagline + tier picker in settings.
-->
