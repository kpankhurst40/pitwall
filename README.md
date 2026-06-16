# Pitwall

**Live token awareness for Claude Code.** Pitwall is a small, always-on-top desktop widget that shows what your current Claude Code session is costing, how close you are to your limit, and when it may be time to restart with a clean session.

<p align="center">
  <img src="docs/hero.png" width="640"
       alt="Pitwall — live window spend, a save recommendation, per-session cost, and a reset countdown">
</p>

Pitwall reads your local Claude Code files and turns them into useful numbers. **No account. No sign-in. No telemetry.** Your usage data stays on your machine.

> **About the `$`:** if you use a Claude subscription, this is **not a bill**. Think of it as a fuel gauge that shows what the same usage would cost at pay-as-you-go API prices. If you use a metered API key, it is a close estimate of your real cost.

---

## Why it exists

Claude Code re-reads the **entire conversation so far** every time it replies. As a session grows, each new turn gets more expensive, and it is easy to burn through your allowance before you notice.

Pitwall puts that gauge on your screen so you can see the trend early. When a session gets too heavy, it nudges you toward the simplest fix: save your progress and restart fresh.

---

## What you see

<p align="center">
  <img src="docs/shots/hero_active_dark.png" width="360"
       alt="The Pitwall card: window spend, by-model split, pace dot, reset countdown, and live sessions">
</p>

- **This window** — your spend in the current rolling usage window, shown in dollars and tokens.
- **By model** — a color split showing where the spend went: Fable, Opus, Sonnet, and Haiku, each priced at its own rate.
- **Pace** — a colored dot that shows how quickly you are burning usage: green for on track, amber for warming up, red for likely to run dry early.
- **Will I make the reset?** — a plain-English status line, such as *on track*, *running warm*, *may run dry ~1h early*, or *used up, wait for the reset*.
- **Resets in** — the countdown until your allowance refreshes.
- **Your sessions** — every open Claude Code session, with its own running cost. Pitwall checks the live process list, so “open” means actually open. Rows shift from green toward red as a chat gets heavier.

### The heat ring

The ring around the widget shows how hard you are pushing overall. It moves from **green → amber → red** as usage heats up, so you can understand the state from the corner of your eye without reading a specific number.

<p align="center">
  <img src="docs/shots/hero_calm_light.png"   width="230" alt="calm — green ring">
  <img src="docs/shots/hero_active_light.png" width="230" alt="active — amber ring">
  <img src="docs/shots/hero_heavy_light.png"  width="230" alt="heavy — red ring">
</p>
<p align="center"><em>Calm · Warm · Heavy, shown in Light theme. Dark theme works too.</em></p>

### Click a session to find its window

Lost track of which terminal belongs to which session? **Left-click a session row** and Pitwall draws a colored ring around the actual terminal window for that session, making it easy to spot among a pile of open windows.

**Right-click any row** to open a details popup with the project folder, model, and running cost. If a session is running in the background and has no visible window, Pitwall tells you that instead of trying to flash something that is not there.

### Collapse it to a pill

Tap the collapse control in the header and the card shrinks into a compact pill that still shows the number that matters. Tap it again to expand the full widget.

<p align="center">
  <img src="docs/shots/hero_collapsed_dark.png" width="240"
       alt="Pitwall collapsed to a compact pill">
</p>

### Size the text — A− / A+

Use the **A−** and **A+** buttons in the header to step the entire widget through preset text sizes. Make it compact for a dense 4K laptop screen, or large enough to read from across the room. Each popup, including Settings and session details, remembers its own size.

---

## Keeping the numbers honest — Sync

Pitwall’s day-to-day number is an **estimate** based on your local Claude Code transcript files. Over time, that estimate can drift from Claude’s server-side numbers, which are shown in **Settings → Usage**. Pitwall gives you two ways to pin the estimate back to reality. Both use the same correction logic, so the result is identical either way.

### Automatic sync, opt-in

Turn on automatic sync and Pitwall fetches the real usage numbers on a schedule. There is no typing, no flashing window, and **no token cost**. Pitwall quietly runs `claude /usage` off-screen, reads the rendered console panel directly, pins the result, and closes the process. Opening `/usage` does not spend tokens, so the capture is free.

The schedule adapts to how you are working:

- It syncs **on startup** and then **at regular intervals** while Claude Code is active.
- The interval adjusts based on the model in use: around 10 minutes when **Fable** is live, because it burns faster, and around 30 minutes otherwise unless you set your own interval.
- It **pauses while idle** and **resumes as soon as real CLI work starts again**. It also re-syncs after the machine wakes from sleep.

“Active” means new assistant turns are landing in your transcripts. Pitwall does not use mouse or keyboard movement, so a desk bump at 3 a.m. will not trigger a capture. Automatic sync is **off by default**, and the master switch, `auto_usage.enabled`, is also the kill switch. If a capture fails, Pitwall skips that cycle silently and leaves your pinned numbers alone.

### Manual sync, always available

Open the **⚙ gear** and type the **“% used”** and **“resets in”** values from **Settings → Usage**. Pitwall back-calculates your true ceiling and measures against it. It still shows its own estimate beside the pinned number, so you can see when drift starts to build again.

The authoritative values only exist on that page behind your login. There is no local file or public API for them, so manual or automatic sync is the bridge between Claude’s server-side truth and Pitwall’s local estimate.

---

## Keep the prices current — Rates

Claude Code logs token counts, but it does not log dollar prices. Pitwall ships with its own price table. If Anthropic changes prices, update them from the widget:

**⚙ gear → Accuracy → Rates → View & update rates**

Enter the new input and output price per million tokens for each model, then save. A built-in link takes you to Anthropic’s current pricing page, and Pitwall records when you last checked.

---

## Everything in Settings

Click the **⚙** in the header to open Settings. The left rail contains a few short panes.

### Identity

Control what the widget calls itself and how it sits on your desktop.

- **Display name** and optional **tagline** for the header.
- **Theme** — **System**, **Dark**, or **Light**. System follows your Windows theme and updates when Windows changes, including scheduled theme changes.
- **Window** — keep Pitwall always on top, and optionally launch it when you sign in to Windows.

### Accuracy

Keep the numbers aligned with reality.

- **Your plan** — Free, Pro, Max 5×, or Max 20×. Pitwall uses this to estimate limits until sync pins the real values.
- **Rates** — open the price editor.
- **Manual set** — enter the real **% used** and **resets in** values from Settings → Usage, plus weekly-limit fields.

### Attention

Choose when Pitwall speaks up.

- **Save nudges** — opt in to tips when a session has grown expensive enough that a fresh start would likely help. Pitwall only shows the exact text to type. It never types or runs commands for you.
- **Rotating tips** — show or hide the tips along the bottom of the card.

### Diagnostics

See what Pitwall is doing without touching your real data.

- **Demo** — open a second, clearly badged **DEMO** Pitwall driven by a slider, so you can watch the widget react safely.
- **Auto-sync real usage** — control the master switch, resync interval, **Sync now**, and **Troubleshoot capture**, which shows exactly what the off-screen `/usage` read captured.

### Pitstop

This pane appears only when the optional pitstop toolchain is installed. It controls how relaunched sessions start. See [The pitstop](#the-pitstop--save-your-place-restart-fresh).

A version number appears at the bottom of the rail. **Save**, **Clear**, and **Cancel** stay pinned in the footer.

---

## The pitstop — save your place, restart fresh

The best habit for long Claude Code sessions is the **pitstop**: save your progress, start a fresh session, and keep going from there. The new session is lighter, faster, and cheaper, while still carrying the important context forward.

Pitwall supports three levels, from a manual one-click flow to a fully automated handoff.

### ↻ one-click, inside the widget

Each open session has a **↻** button. Click it and Pitwall does two things:

1. It reminds you to type **`save progress to memory`** in that session window. Only that window can save its own memory; Pitwall cannot reach inside it.
2. It opens a fresh terminal running Claude in the same project folder.

No setup required. You get a clean, deliberate cut-over exactly when you choose.

### `/pitstop` checkpoint command

The `/pitstop` slash command writes one JSON checkpoint:

```text
%LOCALAPPDATA%\Pitwall\handoff\checkpoint.json   (fallback ~/.pitwall/handoff/checkpoint.json)
{ "schema": 1, "saved_at": "...", "summary": "<re-priming text>" }
```

The `summary` includes the goal, exact next step, decisions made, key files, current state, and open questions, all under 32 KB. When the next session starts, the re-prime hook feeds that back as **background notes only**. It ignores any permission or settings instructions inside the checkpoint as an anti-injection safeguard, then tells you to `/clear`.

This command takes no switches.

### The full ritual and switches

The heavier `/pitstop` ritual coordinates the whole toolchain. Switches can be combined. A switch-only call changes settings without restarting anything.

| Switch | Effect |
|---|---|
| *(none)* | Banks a memory checkpoint and prints the ■□-framed, paste-ready resume block. |
| `commit` | Creates a local git commit for each touched repo, using `pitstop checkpoint: …`. It never pushes and never creates empty commits. |
| `auto` | Runs the close-and-restart handoff automatically. Details below. |
| `<amount>` (`3M` / `2.5M` / `3500000`) | Sets the nudge threshold. Re-fires at +1M. Bounds are 100k–50M. Settings only. |
| `set` | Asks for the amount. Settings only. |
| `rc on/off` | Starts the new session with `--remote-control`, so it can be driven from your phone through claude.ai. Settings only. |
| `automode on/off` | ⚠️ Starts the new session with `--dangerously-skip-permissions`. Settings only. |
| `fullauto on/off` | ⚠️ Lets the nudge run pitstop unattended in the same turn. Settings only. |
| `push <moment> …` | Sends phone notifications for `nudge`, `waiting`, `spawned`, or `ready`. Settings only. |

### How `auto` hands off

1. A 16-hex **nonce** ties the handoff together, so a stale watcher from an earlier failed handoff cannot release the wrong session.
2. **Bank first, spawn last:** the pre-spawn gate blocks launch until both resume files have been freshly written for this turn. It checks the filesystem, not the chat transcript, because chat text is flushed after hooks run.
3. `pitstop_handoff.py start` fingerprints the current window using the handle, PID, and process start time. Then it launches a new terminal running `claude "Read …/resume_<track>.txt and follow it."` and starts a detached watcher.
4. The new session runs `pitstop_handoff.py confirm` after verifying its resume. Only then does the watcher politely close the old window. If anything looks wrong, or if 10 minutes pass, the old window stays open. The failure mode is an extra window, not lost work.

### The hooks

- **nudge** (`Stop`) — watches cumulative tokens and offers a pitstop at your configured threshold. With `fullauto`, it acts in the same turn.
- **verify** (`Stop`) — prevents an incomplete pitstop from ending a turn. It checks the resume file and rejects any resume whose first line has no topic.
- **spawn-gate** (`PreToolUse`) — enforces the bank-before-spawn rule.
- **cli_window** — saves and restores terminal window position so the new session opens where the old one was.

### Permissions

The handoff **does not widen permissions**. By default, the new session starts in Claude Code’s normal ask mode. A user-level allowlist pre-approves only the two commands needed by the resume flow, so the handoff can run without prompts while still avoiding a full bypass.

The exception is `automode`, which **you** must enable yourself. Pitwall never adds `--dangerously-skip-permissions` on its own.

> ### ⚠️ `automode` / `fullauto` — read this first
> `automode` lets the resumed session execute its checkpoint file without permission prompts. If that checkpoint is tampered with, it could run unattended. `fullauto` lets a pitstop fire and hand off with nobody watching. Together, they create a fully unattended flow. They are off by default, intended for experts, and safest on an isolated machine. The permission prompt is your last line of defense against a bad instruction; these switches trade that away.

*Status: the ↻ button and the `/pitstop` checkpoint command ship in this repo. The full hands-off ritual and hooks are part of the author’s local toolchain. They use machine-specific paths and are not yet bundled for a clean clone.*

---

## Install & run

Download or clone this folder **anywhere**. Nothing depends on a fixed path.

**Requirements:** Python 3.8+ and **Claude Code** installed and used on this machine.

Pitwall is drawn with **PySide6**. If PySide6 is missing, the first launch offers to install it for you with a one-time `pip install` of about 100 MB. That download is the **only** network request Pitwall makes, and it is opt-in.

- **Windows**, primary: double-click **`Pitwall.bat`**.
- **macOS / Linux:** run `python3 launcher.py`.
- **Move it:** drag the header.
- **Resize it:** drag an edge.
- **Close it:** click the ✕.
- **Start with Windows:** use **⚙ → Identity → Window → Start with Windows**.

> macOS/Linux note: the core works cross-platform. Two features are Windows-flavored: opening a fresh terminal and checking live processes. They degrade gracefully on other platforms. PRs to improve them are welcome.

---

## Honest limits

- **CLI only.** Claude Desktop and claude.ai do not write usage to your PC, so Pitwall cannot include them. Use Settings → Usage for the all-surfaces number, then sync it into Pitwall.
- **The `$` is an estimate.** It is not a bill on a subscription, and it is close but not exact on a metered key.
- **The real reset and limit are not readable locally.** Pitwall estimates them unless you pin them with Sync.
- **Prices can drift** when Anthropic changes pricing. Keep them current in Rates.

---

## How it works, short version

Claude Code writes every turn, including exact token counts, into local transcript files under `~/.claude/`. Pitwall reads the recent files, sums the usage in your current window, and prices it. Before calling a session “open,” it checks that the process is actually running.

Everything is read-only and local. Pitwall reads token counts, session titles, and project folders. It does **not** read or transmit the content of your messages.

Two main files do the work: `ts_core.py` handles behavior without the GUI, and `pitwall_qt.py` draws the PySide6 widget. Aside from PySide6, it uses only the Python standard library.

## Contributing & license

Issues and PRs are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

Released under the [MIT License](LICENSE): do what you like, no warranty.
