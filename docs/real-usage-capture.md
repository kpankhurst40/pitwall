# Real-usage capture — how Pitwall pins itself to Claude's real numbers

Pitwall's everyday numbers are an **estimate**: it reads your own Claude Code transcript
files on disk and does the token math locally. That estimate slowly drifts from Claude's
**server-side truth** (the figures on Claude's **Settings → Usage** page). This document explains
how the app re-pins itself to the real numbers — including the optional, hands-off "terminal-output
grab" that fetches them for you at **$0 token cost**.

> Local-only and private throughout. Nothing here sends anything off your machine — it reads your
> own usage panel and writes the result into your own local config.

## Two ways to re-pin to reality

1. **Manual Sync.** You open Claude's Settings → Usage, read off the real "% used" and "resets in",
   and type them into Pitwall. The app pins those values and records how far its estimate had
   slipped (a calibration history line). Always available; needs no setup.

2. **Auto real-usage capture** (opt-in). The app fetches the same real numbers itself, off-screen,
   on a schedule — no typing, no window flashing on your screen. This is the subsystem documented
   below. **Default: OFF.**

Both paths funnel into the **same** code (`inject_real_usage` in `ts_core.py`), so the result is
identical whether you typed the numbers or the app captured them.

## What the auto capture does, end to end

```
ts_core.AutoUsageScheduler        decides WHEN a capture should fire (cadence below)
        │
ts_core.capture_real_usage()      runs a capture script via Windows PowerShell 5.1,
        │                         off the UI thread (blocking ~10–15s), never raises
        │
        ├─ PRIMARY  scripts/read_usage.ps1   spawns `claude /usage` HIDDEN, then reads the
        │           rendered panel straight out of the console SCREEN BUFFER as TEXT
        │           (Win32 ReadConsoleOutputCharacter) — the exact characters the TUI drew,
        │           so there is NO OCR and no misread digits.
        │
        └─ FALLBACK scripts/capture_usage.ps1   spawns `claude /usage` OFF-SCREEN, grabs the
                    window image with PrintWindow, and OCRs it (WinRT OCR). Used only if the
                    text read fails. Same JSON contract as the primary.
        │
        ▼
   one line of JSON:
   {"ok":true,"session_pct":17,"session_reset":"4:10pm",
    "weekall_pct":75,"weekall_reset":"Jun 10, 5am","sonnet_pct":1}
        │                                       (or {"ok":false,"error":"…"} on any failure)
        ▼
ts_core.inject_real_usage(cfg)    validates the numbers and pins them into the config's
                                  reset + calibration fields — the same path manual Sync uses
```

### Why it can read `/usage` for free

Claude Code's `/usage` panel is rendered locally by the CLI; opening it does **not** spend tokens.
The capture spawns a throwaway `claude /usage`, reads the panel, and tears the process down — so the
whole thing costs **$0**.

For the spawned `claude` to render the panel (instead of stalling on Claude Code's "Do you trust
this folder?" prompt), it is launched in a folder Claude Code has **already trusted**
(`hasTrustDialogAccepted` in `~/.claude.json`). The `/usage` numbers are **account-level**, so which
trusted folder is used makes no difference to the result.

### Why off-screen / hidden

The primary path spawns the CLI with **no visible window**; the fallback positions it **off-screen**.
Either way you never see a console flash and your keyboard focus is never stolen.

## Safety invariant — never touch a real session (Ivan, HIGH)

The capture starts a `claude` process and must clean it up afterwards. The hard rule:

> **The capture only ever kills the process tree it spawned itself** — seeded from its *own* process
> ID and walked down to its descendants. It must **never** use a "kill any `claude.exe` that appeared
> since we launched" rule.

Why this matters: if the user opens a *real* Claude Code session during the ~10-second capture window,
a "new PIDs since launch" rule would grab and kill that real session. Keying off the capture's own
process tree makes a sibling session impossible to hit. Both scripts (`read_usage.ps1`'s `Kill-Ours`
and `capture_usage.ps1`'s `Kill-New`) implement this tree-walk. **If you edit this code, preserve the
invariant.**

## Failure is silent by design

`capture_real_usage()` **never raises**. Any failure — capture script missing, the spawn timed out,
malformed JSON, no usable numbers — comes back as `{"ok": false, "error": "…"}`, and the scheduler
simply skips that cycle and tries again next interval. A bad read can never crash the widget or
corrupt your pinned numbers; `inject_real_usage` leaves the config untouched unless it got at least
one valid percentage.

There are sanity guards on the values too: a percentage must be 0–100, and a captured reset time is
**rejected** if it falls outside the plausible window (a 5-hour window can't legitimately reset more
than ~5 hours out). When a reset read is garbled but the 5-hour **%** is good, the % is still pinned
(it's the truth the display shows directly); only the dollar-ceiling derivation, which needs a
trustworthy reset to anchor the window, is skipped.

## When a capture fires (cadence)

`AutoUsageScheduler` is pure scheduling logic — no threading, no GUI — so both faces share one
behaviour and it's testable in isolation. The cadence (locked spec):

- **on startup** — sync once when Pitwall launches;
- **every `interval_min`** (default 30) while you're actively using Claude Code;
- **pause while idle** — after `idle_min` with no real CLI activity, stop capturing;
- **resume instantly** — the moment real CLI work resumes, sync immediately and restart the clock;
- **on wake-from-sleep** — sync after the machine wakes.

"Active" vs "idle" is judged by **transcript writes** (real assistant turns append to a session's
`.jsonl`), never by mouse or keyboard input — so a desk bump at 3am can't trigger a capture, and the
off-screen `/usage` spawn (which writes no transcript) can't trigger itself.

## Turning it on / off

- The master switch is `auto_usage.enabled` in `pitwall_config.json`. **Default OFF.** The
  scheduler reads it live, so it's also the kill-switch — flip it off and the next cycle stops.
- `auto_usage.interval_min` and `auto_usage.idle_min` tune the cadence above.
- Each successful capture stamps `auto_usage.last_sync` / `last_ok`, which the UI shows so you can
  see when it last re-pinned.

## Files involved

| File | Role |
|---|---|
| `ts_core.py` → `AutoUsageScheduler` | decides *when* to capture (cadence, idle/resume) |
| `ts_core.py` → `capture_real_usage()` | runs a capture script, returns parsed JSON, never raises |
| `ts_core.py` → `inject_real_usage(cfg)` | validates + pins the numbers (shared with manual Sync) |
| `scripts/read_usage.ps1` | **primary** — hidden spawn, console-buffer text read (no OCR) |
| `scripts/capture_usage.ps1` | **fallback** — off-screen spawn, PrintWindow + WinRT OCR |
| `poc/` | the experiments that led here (off-screen capture, buffer read, OCR tuning) — *not* the shipped path |

Runtime state (`pitwall_config.json`, `pitwall_corrections.jsonl`) is per-machine and
gitignored — it is not source.
