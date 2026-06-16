#!/usr/bin/env python3
# =============================================================================
# Pitwall - CORE (the shared "brain").
# =============================================================================
# This module holds everything that has NOTHING to do with how the widget looks:
# reading Claude's transcripts, the token math + pricing, the 5-hour-window logic,
# config load/save, drift history, and the single-instance lock. It imports NO GUI
# toolkit, so the widget builds on it without the brain dragging in Qt:
#   * pitwall_qt.py - the PySide6 widget (the one and only face)
# Fix the numbers ONCE here and the widget gets it.
# =============================================================================

import os
import re
import sys
import json
import time
import math
import glob
import colorsys
import tempfile
import subprocess
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "pitwall_config.json")
# Append-only history of every time you correct the widget with the real numbers off
# Settings → Usage. Each row records what the widget THOUGHT vs what you TOLD it, so the
# drift report can show how often you correct it and which clock (reset time vs amount)
# is to blame. Kept out of the config so the config stays small and this never gets lost.
CORRECTIONS_PATH = os.path.join(HERE, "pitwall_corrections.jsonl")
# Legacy filenames (pre-Pitwall rename). Read-only fallback: a config/history written
# by an older build still loads, and the next save migrates it to the new name.
LEGACY_CONFIG_PATH = os.path.join(HERE, "token_steward_config.json")
LEGACY_CORRECTIONS_PATH = os.path.join(HERE, "token_steward_corrections.jsonl")
# Demo mode write-lock. The demo construct is a throwaway showcase fed entirely by a
# fake slider — it must never touch the user's real state. Flipping this True at demo
# startup makes the WHOLE process incapable of writing config/corrections, centrally,
# so none of the ~15 scattered save_config call sites can leak through. (Reads stay
# live so the demo still mirrors the user's real theme/size.)
DEMO_READONLY = False
PROJECTS_ROOT = os.path.expanduser("~/.claude/projects")
# Claude writes one file per *running* session here, named by OS process id:
#   <pid>.json -> {pid, sessionId, cwd, status, entrypoint, ...}
# It's deleted on clean exit, so its presence + a live PID = the window is open.
SESSIONS_DIR = os.path.expanduser("~/.claude/sessions")
# Background jobs keep their state here: <job>/state.json -> {sessionId, ...}.
# A session listed here is a real Claude process with NO terminal window.
JOBS_DIR = os.path.expanduser("~/.claude/jobs")

# --- defaults (override any of these in pitwall_config.json) --------------------
DEFAULTS = {
    "window_hours": 5,        # Claude's rolling usage window
    # what the header shows — rename it to anything, or leave the defaults
    "name": "Pitwall",
    "tagline": "",
    # Colour theme: "system" follows the Windows light/dark setting (default),
    # "dark" / "light" pin it. Apple-flavored palette either way.
    "theme": "system",
    "refresh_seconds": 15,    # how often Pitwall re-reads the transcripts
    "tip_seconds": 20,        # how often the tip rotates
    "x": None, "y": None,     # remembered window position
    "w": None,                # remembered EXPANDED width (drag the right edge); height auto-fits
    "collapsed_w": None,      # remembered COLLAPSED-strip width (drag the left edge); separate from "w"
    "ui_scale": 1.15,         # text size
    # pace = $/hour you're currently burning. Colors are a gentle speedometer.
    "pace_amber": 6.0,
    "pace_red": 16.0,
    # Manual reset override (ISO-8601 UTC). When set & still in the future, Pitwall
    # counts down to THIS instead of its transcript guess — so you can sync it to
    # the real number on Claude's Settings -> Usage screen. Auto-clears once past.
    "reset_override": None,
    # Per-session colour: a session goes from green to red as the context it
    # re-reads every turn grows. 'ctx_red' = tokens at which it's fully red (the
    # "restart me" point). 'max_sessions' caps how many rows the widget lists.
    "ctx_red": 220000,
    "max_sessions": 5,
    # Your Claude plan: "pro" | "max5" | "max20" pins the limit bar to that plan's
    # known per-window ceiling. Set null to LEARN your usual limit from history instead.
    "plan": "max5",
    # "Usual limit" learning: Pitwall looks back this many days at your COMPLETED
    # 5-hour windows and takes the 90th-percentile spend as your typical ceiling,
    # then projects how long until you'd reach it at the current pace.
    "limit_lookback_days": 14,
    # Calibration: the last numbers you read off Claude's Settings → Usage screen
    # (session %, weekly %s, reset), pinned beside Pitwall's own estimate so you can
    # SEE how close the estimate is. 'derived_ceiling' = the real per-window
    # allowance backed out from (your $ spent ÷ the real % it showed) — e.g. if
    # $126 of usage is really 26%, the true ceiling is ~$487, not the rough $35.
    "calibration": None,
    # Flip to true once the calibrated ceiling proves accurate, and Pitwall's own %
    # will measure against THAT instead of the rough plan figure.
    "use_calibrated_ceiling": False,
    # Drift correction (lives in Settings). Pitwall learns how far its raw % tends to
    # drift from the website (the correction history), then — only if you agree — nudges the
    # FRONT-PAGE % by that much. "off" = show the raw estimate; "auto" = apply the measured
    # median drift; "manual" = apply your own offset. 'manual_pct' = points the widget reads
    # HIGH (positive → subtracted from the shown %). The raw estimate is always what gets
    # measured/logged, so the learning stays honest even while a correction is applied.
    "drift_adjust": {"mode": "off", "manual_pct": 0.0},
    # Headline focus-follow: make the big heat / "this chat" line track the session in the
    # window you're ACTUALLY looking at, instead of just the most-recently-active one.
    #   "window" = follow the active window (keyboard Alt+Tab AND mouse-click) — DEFAULT
    #   "mouse"  = follow the window under the mouse pointer (hover)
    #   "off"    = always the most-recent open session (old behaviour)
    # Windows-only; on other OSes it quietly has no effect.
    "focus_follow": "window",
    # Silence the rotating tip banner (the functional FLASH_MISS warning still shows).
    "tips_off": False,
    # --- "Nudge me" (user-driven auto-handoff, the SAFE FLOOR) ------------------
    # OFF until you opt in. When armed, Pitwall WATCHES the active session and taps your
    # shoulder when a fresh start would actually save — YOU (not Pitwall) then type
    # /pitstop then /clear. In this mode Pitwall issues no command and pushes no text;
    # every reset is a human keystroke, which is what makes it the safe floor.
    "nudge_armed": False,
    # Break-even floor (tokens). Below this, a fresh session's ~88k "re-priming tax"
    # costs MORE than the conversation it sheds, so a hand-off would be bad advice and
    # Pitwall stays quiet. Measured at ~88k in the hand-off spike; 90k default.
    "nudge_breakeven_tok": 90000,
    # Which rung of the save-recommendation ladder first earns an ACTIVE tap. The
    # passive pill climbs the whole ladder; the nudge waits for THIS rung. One of
    # SAVE_TIERS' words.
    "nudge_tier": "Save & start fresh",
    # Snooze: an epoch-second deadline. While now < this, "Nudge me" stays quiet even
    # when it would otherwise tap (the user clicked "Snooze 1h" on a tap). 0 = not
    # snoozed. Default snooze window is 1 hour (owner, 2026-06-08).
    "nudge_snooze_until": 0,
    # --- Auto real-usage (the off-screen /usage capture) -----------------------
    # OFF until you opt in (this flag is also the kill-switch). When enabled, Pitwall
    # silently runs `claude /usage` off-screen every `interval_min`, OCRs the REAL
    # session/weekly %s + reset, and pins them into the calibration/reset fields —
    # so the displayed % and countdown track Claude's own numbers with no action
    # from you. The manual Sync / drift-adjust stay as a fallback. `idle_min` = how
    # long with no Claude activity before captures pause (they resume + sync the
    # instant real CLI work returns). last_sync/last_ok are written at runtime.
    "auto_usage": {"enabled": False, "interval_min": 30, "interval_min_user_set": False,
                   "idle_min": 10, "last_sync": None, "last_ok": None},
    # --- Manual rate overrides (Settings → Accuracy → Rates) --------------------
    # Claude Code's transcripts log token COUNTS but not the $ rates, so Pitwall ships its
    # own price table (DEFAULT_RATES). When Anthropic changes prices, the user reads the new
    # numbers off the pricing page (the "Check rates" button opens it; Pitwall makes no
    # network request) and types them here. {family: {"in": $perM, "out": $perM}}; cache
    # prices re-derive from input. Empty = use the built-in defaults. apply_rate_overrides()
    # folds this into the active RATES table at load.
    "rate_overrides": {},
    # ISO-8601 UTC stamp of the last time the user checked or entered rates — shown as
    # "Last checked: <date>" so they can see how current the prices are. None = never.
    "rates_last_checked": None,
}

# How long "Snooze 1h" silences taps for (seconds). (Owner, 2026-06-08) chose 1 hour;
# the tap's label ("Snooze 1h") must match this.
NUDGE_SNOOZE_SECONDS = 3600

# The tap footer's one-click snooze choices (seconds, label) — the momentary mute
# (owner, 2026-06-12: "an hour-long suppress is too blunt"). 1h keeps the old default.
NUDGE_SNOOZE_CHOICES = ((300, "5m"), (900, "15m"), (1800, "30m"), (3600, "1h"))

# What the tap's ✕ means: quiet for 10 minutes. Before 2026-06-12 dismiss only hid
# the popup until the next 1-second re-check put it straight back — the "blasted
# with notifications" video. A dismiss must buy real quiet or it's a lie.
NUDGE_DISMISS_SECONDS = 600

# Anthropic public list prices, $ per token (input / output / cache-write / cache-read).
# Base input/output (per 1M tokens) — current as of 2026-06: Fable 5 $10/$50, Opus 4.x $5/$25,
# Sonnet 4.x $3/$15, Haiku 4.5 $1/$5. (Opus was historically $15/$75 — that old rate over-counted
# Opus spend 3×; corrected here.) The 1M-context models bill these rates at ALL context
# sizes — there is NO >200K long-context premium tier to model. Cache WRITES are priced by
# lifetime: a 5-minute write is ~1.25× input, a 1-hour write ~2× input. Each turn's
# transcript says which (the ephemeral_1h / ephemeral_5m split), so Pitwall prices them
# correctly — a Claude *subscription* writes the pricier 1-hour kind. Reads are ~0.1× input.
# Edit if Anthropic's list prices change.
DEFAULT_RATES = {
    "fable":  {"in": 10/1e6, "out": 50/1e6, "cw5m": 12.5/1e6, "cw1h": 20/1e6, "cr": 1.00/1e6},
    "opus":   {"in": 5/1e6, "out": 25/1e6, "cw5m": 6.25/1e6, "cw1h": 10/1e6, "cr": 0.50/1e6},
    "sonnet": {"in": 3/1e6, "out": 15/1e6, "cw5m": 3.75/1e6, "cw1h":  6/1e6, "cr": 0.30/1e6},
    "haiku":  {"in": 1/1e6, "out":  5/1e6, "cw5m": 1.25/1e6, "cw1h":  2/1e6, "cr": 0.10/1e6},
}
# Cache prices are fixed multiples of the input price (Anthropic's published model — see
# the note above): a 1-hour cache write ≈ 2× input, a 5-minute write ≈ 1.25×, a cache read
# ≈ 0.1×. DEFAULT_RATES embodies exactly these multiples; a manual rate override (Settings →
# Accuracy → Rates) re-derives the three cache columns from the entered input price so they
# stay internally consistent and the user only has to type the two headline numbers.
_CW1H_MULT, _CW5M_MULT, _CR_MULT = 2.0, 1.25, 0.1

# The ACTIVE price table the cost math reads. Starts as a copy of the built-in defaults;
# apply_rate_overrides() rebuilds it from DEFAULTS + the user's manual overrides at load
# (and again after a Save). Kept as a separate name from DEFAULT_RATES so "Reset to
# built-in" always has the untouched defaults to fall back to.
RATES = {fam: dict(r) for fam, r in DEFAULT_RATES.items()}

# Where "Check rates" sends the user. Pitwall makes NO network request itself (owner's
# explicit choice — preserves the "no network requests" promise and avoids fragile
# pricing-page scraping); it just opens Anthropic's published prices in the browser so the
# user can read the current numbers and type them back in. This is the developer-platform
# pricing page, which lands directly on the per-million-token API table (input / cache /
# output for every model) — NOT claude.com/pricing, which shows only the Pro/Max
# subscription plans and no token prices. (Verified current 2026-06-16.)
ANTHROPIC_PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing"


def apply_rate_overrides(cfg):
    """Rebuild the active RATES table from the built-in defaults plus any manual per-1M
    overrides in cfg['rate_overrides'] ({family: {'in': $perM, 'out': $perM}}). Cache
    write/read prices re-derive from the entered input price by the standard multipliers, so
    the user only types input/output. Called at load and after a Save so usage_cost /
    collect_components price with the user's current numbers. Bad/partial overrides are
    skipped, never crash."""
    global RATES
    new = {fam: dict(r) for fam, r in DEFAULT_RATES.items()}
    ov = (cfg or {}).get("rate_overrides")
    if isinstance(ov, dict):
        for fam, vals in ov.items():
            if fam not in new or not isinstance(vals, dict):
                continue
            base = new[fam]
            try:
                tin = float(vals["in"]) / 1e6 if vals.get("in") is not None else base["in"]
                tout = float(vals["out"]) / 1e6 if vals.get("out") is not None else base["out"]
            except (TypeError, ValueError):
                continue
            if tin <= 0 or tout <= 0:
                continue
            new[fam] = {"in": tin, "out": tout, "cw5m": tin * _CW5M_MULT,
                        "cw1h": tin * _CW1H_MULT, "cr": tin * _CR_MULT}
    RATES = new

# Each Claude plan's typical per-window ceiling ($ API-equivalent). Choosing one pins the
# limit bar to a REAL number; leaving the plan unset learns your 'usual limit' from history.
PLANS = {
    "free":  {"label": "Free",    "usd": 2.0},
    "pro":   {"label": "Pro",     "usd": 18.0},
    "max5":  {"label": "Max 5x",  "usd": 35.0},
    "max20": {"label": "Max 20x", "usd": 140.0},
}

TIPS = [
    'Long chats cost the most. Every reply re-reads the whole conversation, so the longer a session runs, the pricier each turn gets.',
    'When a chat gets long and pricey, ask Claude: “Write a short summary of where we left off.” Paste it into a fresh session — the new one starts cheap. Click to copy.',
    'Batch your asks. “Do A, B and C” in one message is cheaper than three separate messages.',
    'Say “be brief” when you don\'t need the play-by-play. Less writing out = lower cost.',
    'Trim what you paste in. A big file or long log stays in the conversation and adds to every later reply, not just the next one.',
    'Starting something new? Open a fresh session for it instead of continuing a long one.',
    'Stick to one topic per session — a chat that hops between topics carries all of them in every reply.',
    'Hover a session row and click ↻ to start a fresh, cheaper session in the same project.',
    'Click an open session\'s name to light up the window it lives in.',
    'Code reviews cost less when you paste just the function in question, not the whole file.',
    'Ask for a one-sentence answer first — skip the full explanation when you already know the topic.',
    'Type /clear to wipe the conversation without closing the session — useful when you change topics midway. Click to copy.',
    'Bigger models cost more on every reply. Save Fable and Opus for work where judgment really matters — Sonnet and Haiku handle routine tasks for a fraction of the price.',
    'Ask for the handoff summary “in three bullets” — a short note keeps the next session cheap.',
    'Paste just the relevant error lines, not the full log — shorter context costs less every turn after.',
    'Every token saved compounds — 10% fewer per turn means 10% more work for the same budget.',
    'Ask for small, targeted edits instead of full rewrites — usually faster and always cheaper.',
    'Standing instructions (like a CLAUDE.md file) are re-read on every reply — keep them short.',
    'Short chats cost less per turn — the model re-reads everything, so a shorter history is just cheaper.',
    'File trees and directory listings are surprisingly large. Share only the path that matters.',
    'When a topic is fully resolved, open a new session — finished context carried forward just adds cost.',
    'Ask for bullet points instead of prose when a list is what you need — shorter answers cost less.',
    'Attachments stay in the chat and add to the cost of every reply that follows, not just the first.',
    'When iterating on a design, a fresh session is cheaper — re-reading a long design thread costs more each turn.',
    'If a big file matters to the whole task, share it once at the start — bringing it in later costs more.',
    'Each session row shows the chat\'s size — the bigger the chat, the more every reply costs.',
    'Drag this widget\'s right edge to set your preferred width — it remembers across sessions.',
    'The A- and A+ buttons in Settings scale all the text — pick the size that works on your monitor.',
]

# The one summary ask the whole app teaches (hand-off dialog, hot nudge, tip above).
# A single constant so every surface quotes EXACTLY the same words.
HANDOFF_SUMMARY_ASK = "Write a short summary of where we left off."

# Click-to-copy tips (stranger-test rule 3, owner 2026-06-12): tips that quote text
# the user should TYPE. Maps the exact tip text -> the clipboard payload. Both faces:
# when the showing tip is in this dict, clicking the tip copies the payload.
TIP_COPY = {
    TIPS[1]: HANDOFF_SUMMARY_ASK,
    TIPS[3]: "be brief",
    TIPS[11]: "/clear",
}


def hot_nudge_text(pace):
    """The burning-hot tip-strip nudge, shared by both faces (one wording, one brain).
    Clicking it copies HANDOFF_SUMMARY_ASK (the faces wire the click)."""
    return (f"\U0001f4a1  You're burning ~${pace:.0f}/hr. Type "
            f"“{HANDOFF_SUMMARY_ASK}” — then paste it into a fresh session. "
            f"New sessions cost far less per reply. Click to copy.")


# Shown when the hand-off dialog couldn't open a terminal window itself. Clicking
# the tip copies "claude" (the faces wire the click).
TERMINAL_FAIL_TIP = ("⚠  Couldn't open a window automatically. Open a terminal "
                     "in the project folder and type: claude — click to copy.")

# The app's version (owner, 2026-06-12: "we should have a version number for pitwall
# somewhere"). ONE constant, both faces inherit it; shown quietly in Settings and stamped
# into the packaged build by the release pipeline. 0.9.0 = feature-done, pre-packaging;
# Johny bumps to 1.0.0 at the first packaged release, then it moves only at releases.
APP_VERSION = "0.9.1"

# Stamped by the fact-check ritual (docs/fact_check_manifest.md): re-stamp this date each
# time the manifest walk passes. Shown in Settings under "Rotating tips" (owner, 2026-06-12).
TIPS_VERIFIED = "June 13, 2026"
TIPS_VERIFIED_NOTE = ("Anthropic can change their policy at any time. "
                      f"These tips are accurate as of {TIPS_VERIFIED}.")

# --- palette -----------------------------------------------------------------
# Locked to the Apple-flavored design system — Apple's PUBLISHED light + dark system
# colours (real, not invented). Neutral gray chrome + one brand blue (interactive only)
# + green/orange/red status. Light/dark is one switch over the two columns below.
#
# The ACTIVE values live in the module-level names BG/PANEL/.../MODEL_COLORS so the widget
# (`ts.BG`) and the launcher's bootstrap dialogs keep reading them as before —
# set_theme() reassigns those names in place to repaint. W_* is the "where the
# words go" categorical legend (HIG-legal data-viz, not theme tint); MODEL_COLORS are the
# per-model categorical tags. Both retune across themes so they stay legible on either base.
# See .claude/skills/ui-ux-architect/references/design-system-apple-flavored.md
PALETTE = {
    "dark": {
        "BG": "#1C1C1E", "PANEL": "#2C2C2E", "EDGE": "#38383A",
        "INK": "#FFFFFF", "MUT": "#98989F", "FAINT": "#6C6C70",
        "ACCENT": "#0A84FF", "GREEN": "#30D158", "AMBER": "#FF9F0A", "RED": "#FF453A",
        "W_YOU": "#30D158", "W_FILES": "#40CBE0", "W_CLAUDE": "#0A84FF",
        "W_SAVED": "#FFD60A", "W_REREAD": "#FF9F0A",
        "MODEL_COLORS": {"fable": "#40CBE0", "opus": "#BF5AF0", "sonnet": "#0A84FF", "haiku": "#30D158"},
    },
    "light": {
        "BG": "#FFFFFF", "PANEL": "#F2F2F7", "EDGE": "#C6C6C8",
        "INK": "#000000", "MUT": "#6E6E73", "FAINT": "#8E8E93",
        "ACCENT": "#007AFF", "GREEN": "#34C759", "AMBER": "#FF9500", "RED": "#FF3B30",
        "W_YOU": "#34C759", "W_FILES": "#30B0C7", "W_CLAUDE": "#007AFF",
        "W_SAVED": "#FFCC00", "W_REREAD": "#FF9500",
        "MODEL_COLORS": {"fable": "#30B0C7", "opus": "#AF52DE", "sonnet": "#007AFF", "haiku": "#34C759"},
    },
}
# Names that set_theme() swaps. Everything else (MODEL_LABEL, sizes) is theme-independent.
THEME_TOKENS = ("BG", "PANEL", "EDGE", "INK", "MUT", "FAINT", "ACCENT", "GREEN",
                "AMBER", "RED", "W_YOU", "W_FILES", "W_CLAUDE", "W_SAVED",
                "W_REREAD", "MODEL_COLORS")
MODEL_LABEL = {"fable": "Fable", "opus": "Opus", "sonnet": "Sonnet", "haiku": "Haiku"}

_ACTIVE_THEME = "dark"


def os_theme():
    """'light' or 'dark' from the Windows personalization setting (AppsUseLightTheme).
    Falls back to 'dark' when it can't be read — non-Windows, or the key is missing."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        try:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        finally:
            winreg.CloseKey(key)
        return "light" if val else "dark"
    except Exception:
        return "dark"


def resolve_mode(cfg):
    """Map the config 'theme' setting ('system'|'dark'|'light') to a concrete
    'dark'/'light'. 'system' (and anything unexpected) follows the OS."""
    mode = (cfg or {}).get("theme", "system")
    if mode in ("dark", "light"):
        return mode
    return os_theme()


def set_theme(mode):
    """Repaint by reassigning this module's colour globals (BG, INK, ... MODEL_COLORS) in
    place to the 'dark'/'light' column, so the next widget build picks up the new values.
    `mode` is a concrete 'dark'/'light' (resolve 'system' via resolve_mode first).
    Returns the applied mode."""
    global _ACTIVE_THEME
    mode = "light" if mode == "light" else "dark"
    pal = PALETTE[mode]
    g = globals()
    for k in THEME_TOKENS:
        g[k] = pal[k]
    _ACTIVE_THEME = mode
    return mode


def active_theme():
    """The concrete 'dark'/'light' currently painted."""
    return _ACTIVE_THEME


# Populate the active colour names at import (dark = default) so `from ts_core import *`
# and `ts.BG` both resolve immediately; a face re-calls set_theme() with the user's choice.
set_theme("dark")

# Text-size ladder (Sarah, 2026-06-05): A−/A+ move ONE rung so the visible jump is
# even (the headline climbs ~+4–9px per step) instead of the uneven doubling a raw
# ×0.5 multiplier gave. Level 2 (115%) is the default; 235% is the 4K ceiling that
# still fits the column layout. Stored as an int 'size_level'.
SIZE_LADDER = [1.00, 1.15, 1.30, 1.50, 1.75, 2.00, 2.35]
SIZE_DEFAULT_LEVEL = 2


# Config keys that MUST be numbers — coerced after load so a bad hand-edit (the config
# is a documented edit surface on an open-source tool) can't crash startup with a
# TypeError deep in render/slice. Each falls back to its DEFAULT. (Ivan, 2026-06-05.)
_NUMERIC_CFG = ("ui_scale", "ctx_red", "max_sessions", "pace_amber", "pace_red",
                "window_hours", "refresh_seconds", "tip_seconds", "limit_lookback_days",
                "nudge_breakeven_tok")


def _sanitize_config(cfg):
    """Coerce known-numeric keys to numbers, replacing anything non-numeric with the
    DEFAULT. Keeps the rest of the (tolerant) loader intact."""
    for k in _NUMERIC_CFG:
        if k in cfg:
            try:
                cfg[k] = float(cfg[k]) if isinstance(DEFAULTS[k], float) else int(cfg[k])
            except (TypeError, ValueError):
                cfg[k] = DEFAULTS[k]
    # width keys are int-or-None (default None, so they sit outside _NUMERIC_CFG); a bad
    # hand-edit must fall back to None (= auto-fit), never crash startup. (Ivan, 2026-06-06)
    for k in ("w", "collapsed_w"):
        if cfg.get(k) is not None:
            try:
                cfg[k] = int(cfg[k])
            except (TypeError, ValueError):
                cfg[k] = None
    # auto_usage must be a dict — a bad hand-edit (e.g. "auto_usage": "on") would make
    # `cfg.get("auto_usage") or {}` keep the string, and the Settings dialog's .get()
    # then throws. Fall back to the DEFAULT dict. (Ivan, 2026-06-08)
    if not isinstance(cfg.get("auto_usage"), dict):
        cfg["auto_usage"] = dict(DEFAULTS["auto_usage"])
    # rate_overrides must be a dict (a bad hand-edit would otherwise reach
    # apply_rate_overrides and the Rates dialog's .get()). Fall back to no overrides.
    if not isinstance(cfg.get("rate_overrides"), dict):
        cfg["rate_overrides"] = {}
    # One-time rename migration (owner, 2026-06-11): a saved display name that is one
    # of the app's OWN old defaults follows the rename to Pitwall. A name the user
    # typed themselves is never touched.
    if cfg.get("name") in ("Token Steward", "Claude Token Steward"):
        cfg["name"] = DEFAULTS["name"]
    return cfg


def load_config():
    cfg = dict(DEFAULTS)
    path = CONFIG_PATH if os.path.exists(CONFIG_PATH) else LEGACY_CONFIG_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    except Exception:
        pass
    cfg = _sanitize_config(cfg)
    # fold any manual price overrides into the active RATES table now, so every reader
    # (widget, launcher, demo) prices with the user's current numbers from the first call.
    apply_rate_overrides(cfg)
    return cfg


def save_config(cfg):
    if DEMO_READONLY:        # demo construct: never write the user's real state
        return
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    except Exception:
        pass


# --- Start with Windows (per-user login autostart) --------------------------
# The source of truth is the HKCU "Run" key Windows itself reads at sign-in — NOT
# the config file — so the switch can't drift from reality if the user removes the
# entry via Task Manager → Startup. We register a command that goes through
# launcher.py (its single-instance guard + PySide6 check), or the packaged .exe
# when frozen. Same winreg style as os_theme(); silent + Windows-only.
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "Pitwall"


def _startup_command():
    """The command line to register in the Run key, with paths quoted."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'          # packaged .exe runs itself
    # source-install lane: pythonw launcher.py (windowless, single-instance guard)
    exe = sys.executable
    if os.name == "nt" and exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            exe = cand
    launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.py")
    return f'"{exe}" "{launcher}"'


def startup_enabled():
    """True if Pitwall is registered to start at Windows sign-in (HKCU Run key)."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY)
        try:
            winreg.QueryValueEx(key, _RUN_VALUE)
            return True
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False


def set_startup(on):
    """Add (on) or remove (off) the login-autostart entry. Returns True on success.
    A demo instance never touches the registry."""
    if DEMO_READONLY:        # demo construct: never write the user's real state
        return False
    try:
        import winreg
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY)
        try:
            if on:
                winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ,
                                  _startup_command())
            else:
                try:
                    winreg.DeleteValue(key, _RUN_VALUE)
                except FileNotFoundError:
                    pass            # already absent — nothing to remove
        finally:
            winreg.CloseKey(key)
        return True
    except Exception:
        return False


def append_correction(row):
    """Append one correction row (a dict) to the history. Never raises."""
    if DEMO_READONLY:        # demo construct: never write the user's real state
        return
    try:
        with open(CORRECTIONS_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass


def load_corrections(limit=400):
    """Read the correction history (oldest→newest), tolerant of bad lines."""
    rows = []
    path = CORRECTIONS_PATH if os.path.exists(CORRECTIONS_PATH) else LEGACY_CORRECTIONS_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except OSError:
        return []
    return rows[-limit:]


def drift_summary(rows):
    """Boil the correction history down to a plain-English read on HOW the widget
    drifts from the website and WHICH clock is at fault. Two independent axes:
      • amount  — the widget's % vs the real % (drifts when coverage/ceiling is off)
      • time    — the widget's guessed reset vs the real reset (the gap heuristic)
    Returns None until there's at least one usable correction."""
    use = [r for r in rows
           if r.get("pct_gap") is not None or r.get("reset_gap_min") is not None]
    n = len(use)
    if n == 0:
        return None
    pgaps = [r["pct_gap"] for r in use if r.get("pct_gap") is not None]
    rgaps = [r["reset_gap_min"] for r in use if r.get("reset_gap_min") is not None]
    med_pct = percentile(pgaps, 0.5) if pgaps else None             # signed
    med_reset = percentile([abs(x) for x in rgaps], 0.5) if rgaps else None  # minutes
    times = [t for t in (parse_ts(r.get("at", "")) for r in use) if t]
    days = ((max(times) - min(times)).total_seconds() / 86400.0
            if len(times) >= 2 else None)
    per_day = (n / days) if (days and days > 0) else None
    # trend on the AMOUNT error: is it getting better or worse over time?
    trend = None
    if len(pgaps) >= 4:
        h = len(pgaps) // 2
        early = sum(abs(x) for x in pgaps[:h]) / h
        late = sum(abs(x) for x in pgaps[h:]) / (len(pgaps) - h)
        trend = ("improving" if late < early - 1
                 else "worsening" if late > early + 1 else "steady")
    amount_bad = med_pct is not None and abs(med_pct) >= 6
    time_bad = med_reset is not None and med_reset >= 20
    if amount_bad and time_bad:
        verdict = ("Both clocks drift. The reset guess is the bigger lever — pin it "
                   "with ✎. The amount is also off, which points at coverage (this PC "
                   "may not see all your usage) or a stale ceiling — re-sync the %.")
    elif time_bad:
        verdict = ("Mostly the RESET clock. The widget guesses your window start from "
                   "gaps in your transcripts, which rarely matches Claude's real "
                   "schedule. Pinning the reset with ✎ fixes this directly.")
    elif amount_bad:
        verdict = ("Mostly the AMOUNT. Your real ceiling/coverage has drifted — this "
                   "machine may not see every session, or the ceiling is stale. "
                   "Re-syncing the % re-derives the ceiling and closes the gap.")
    else:
        verdict = "Tracking well — your corrections are small."
    return {"n": n, "med_pct": med_pct, "med_reset": med_reset,
            "per_day": per_day, "days": days, "trend": trend,
            "amount_bad": amount_bad, "time_bad": time_bad, "verdict": verdict}


def model_family(name):
    n = (name or "").lower()
    if "fable" in n or "mythos" in n:
        return "fable"
    if "opus" in n:
        return "opus"
    if "sonnet" in n:
        return "sonnet"
    if "haiku" in n:
        return "haiku"
    return "opus"   # safest (most expensive) default if a model is unknown


def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def usage_cost(u, model):
    """One usage dict -> (total_tokens, dollars, ctx_tokens).
    Cache writes are priced by their real lifetime: the transcript splits cache creation
    into 1-hour vs 5-minute writes (ephemeral_1h / ephemeral_5m), billed ~2x vs ~1.25x
    input. If an older line lacks the split, fall back to the 1-hour rate — that's what a
    Claude subscription writes. 'ctx' = what this turn re-read (input + cache), the part a
    fresh session would reset to ~0."""
    tin = u.get("input_tokens", 0) or 0
    tout = u.get("output_tokens", 0) or 0
    tcw = u.get("cache_creation_input_tokens", 0) or 0
    tcr = u.get("cache_read_input_tokens", 0) or 0
    r = RATES[model_family(model)]
    cc = u.get("cache_creation") or {}
    cw1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
    cw5m = cc.get("ephemeral_5m_input_tokens", 0) or 0
    if cw1h or cw5m:
        cw_cost = cw1h * r["cw1h"] + cw5m * r["cw5m"]
    else:
        cw_cost = tcw * r["cw1h"]
    dollars = tin * r["in"] + tout * r["out"] + cw_cost + tcr * r["cr"]
    return tin + tout + tcw + tcr, dollars, tin + tcr + tcw


def collect_entries(window_hours, lookback_hours=None):
    """Read recent transcripts -> list of (timestamp, tokens, dollars), de-duped.
    Only files touched within the lookback (default = window +1h slack) are read,
    so this stays fast. Pass a larger lookback_hours to learn long-run history."""
    span_hours = lookback_hours if lookback_hours is not None else window_hours + 1
    cutoff = time.time() - span_hours * 3600
    entries, seen = [], set()
    pattern = os.path.join(PROJECTS_ROOT, "**", "*.jsonl")
    for path in glob.glob(pattern, recursive=True):
        try:
            if os.path.getmtime(path) < cutoff:
                continue
        except OSError:
            continue
        try:
            fh = open(path, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line or '"usage"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") != "assistant":
                    continue
                msg = o.get("message") or {}
                u = msg.get("usage")
                if not u:
                    continue
                key = (o.get("requestId"), msg.get("id"))
                if key in seen:
                    continue
                seen.add(key)
                ts = parse_ts(o.get("timestamp", ""))
                if ts is None:
                    continue
                total, dollars, _ = usage_cost(u, msg.get("model"))
                entries.append((ts, total, dollars, model_family(msg.get("model"))))
    entries.sort(key=lambda e: e[0])
    return entries


def collect_components(window_hours, start_dt, lookback_hours=None):
    """Sum a window's tokens split by KIND — new input / output (↓) / cache writes /
    cache re-reads (↑) — with a $ estimate for each and a turn count. `start_dt` bounds
    the window (entries before it are skipped); pass the same start the headline uses
    (reset - window_hours) so the totals here reconcile with the big number up top.
    Mirrors collect_entries' de-dup + file-mtime fast path so it stays cheap."""
    span_hours = lookback_hours if lookback_hours is not None else window_hours + 1
    cutoff = time.time() - span_hours * 3600
    seen = set()
    agg = {"in": 0, "out": 0, "cw": 0, "cr": 0, "turns": 0,
           "in_usd": 0.0, "out_usd": 0.0, "cw_usd": 0.0, "cr_usd": 0.0}
    for path in glob.glob(os.path.join(PROJECTS_ROOT, "**", "*.jsonl"), recursive=True):
        try:
            if os.path.getmtime(path) < cutoff:
                continue
        except OSError:
            continue
        try:
            fh = open(path, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line or '"usage"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") != "assistant":
                    continue
                msg = o.get("message") or {}
                u = msg.get("usage")
                if not u:
                    continue
                key = (o.get("requestId"), msg.get("id"))
                if key in seen:
                    continue
                ts = parse_ts(o.get("timestamp", ""))
                if ts is None or (start_dt and ts < start_dt):
                    continue
                seen.add(key)
                r = RATES[model_family(msg.get("model"))]
                tin = u.get("input_tokens", 0) or 0
                tout = u.get("output_tokens", 0) or 0
                tcw = u.get("cache_creation_input_tokens", 0) or 0
                tcr = u.get("cache_read_input_tokens", 0) or 0
                cc = u.get("cache_creation") or {}
                cw1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
                cw5m = cc.get("ephemeral_5m_input_tokens", 0) or 0
                cw_cost = (cw1h * r["cw1h"] + cw5m * r["cw5m"]) if (cw1h or cw5m) \
                    else tcw * r["cw1h"]
                agg["in"] += tin
                agg["out"] += tout
                agg["cw"] += tcw
                agg["cr"] += tcr
                agg["turns"] += 1
                agg["in_usd"] += tin * r["in"]
                agg["out_usd"] += tout * r["out"]
                agg["cw_usd"] += cw_cost
                agg["cr_usd"] += tcr * r["cr"]
    return agg


_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def collect_typed_words(window_hours, start_dt, lookback_hours=None):
    """Count the words you ACTUALLY TYPED in the window — and only those.

    Your real messages are transcript `user` entries whose content is a plain STRING;
    tool results arrive as `user` entries with LIST content, so they're excluded
    automatically. We strip injected <system-reminder> blocks and stray tags so the
    count reflects your keystrokes, not the harness. A TRUE word count, not a token
    estimate — the honest answer to "I didn't type 400,000 words.\""""
    span_hours = lookback_hours if lookback_hours is not None else window_hours + 1
    cutoff = time.time() - span_hours * 3600
    words = 0
    msgs = 0
    for path in glob.glob(os.path.join(PROJECTS_ROOT, "**", "*.jsonl"), recursive=True):
        sid = os.path.splitext(os.path.basename(path))[0]
        if sid.startswith("agent-"):          # sub-agent sidechain, not you typing
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                continue
        except OSError:
            continue
        try:
            fh = open(path, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line or '"user"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") != "user":
                    continue
                c = (o.get("message") or {}).get("content")
                if not isinstance(c, str):     # list content == tool result, not typing
                    continue
                ts = parse_ts(o.get("timestamp", ""))
                if ts is None or (start_dt and ts < start_dt):
                    continue
                clean = _TAG_RE.sub(" ", _REMINDER_RE.sub(" ", c))
                n = len(clean.split())
                if n:
                    words += n
                    msgs += 1
    return {"words": words, "msgs": msgs}


def block_windows(entries, window_hours):
    """Group entries into rolling 5-hour blocks -> list of {start,last,tok,usd}."""
    span = timedelta(hours=window_hours)
    blocks = []
    for ts, tok, usd, fam in entries:
        if (not blocks
                or ts - blocks[-1]["start"] >= span
                or ts - blocks[-1]["last"] >= span):
            blocks.append({"start": ts, "last": ts, "tok": tok, "usd": usd,
                           "by_model": {fam: {"tok": tok, "usd": usd}}})
        else:
            b = blocks[-1]
            b["last"] = ts
            b["tok"] += tok
            b["usd"] += usd
            m = b["by_model"].setdefault(fam, {"tok": 0, "usd": 0.0})
            m["tok"] += tok
            m["usd"] += usd
    return blocks


def active_window(entries, window_hours):
    """Return the live rolling block (or an idle marker)."""
    span = timedelta(hours=window_hours)
    now = datetime.now(timezone.utc)
    blocks = block_windows(entries, window_hours)
    if not blocks:
        return {"active": False, "tok": 0, "usd": 0.0, "reset": None, "pace": 0.0,
                "by_model": {}}
    b = blocks[-1]
    reset = b["start"] + span
    if now >= reset:                       # last block already elapsed -> fresh window
        return {"active": False, "tok": 0, "usd": 0.0, "reset": None, "pace": 0.0,
                "by_model": {}}
    elapsed_hr = max((now - b["start"]).total_seconds() / 3600, 1 / 60)
    return {"active": True, "tok": b["tok"], "usd": b["usd"], "reset": reset,
            "pace": b["usd"] / elapsed_hr, "by_model": b.get("by_model", {})}


def window_for_reset(entries, reset_dt, window_hours):
    """The REAL current window, anchored to a known reset time (the one you read off
    Settings → Usage and pinned). It sums all spend since reset - window_hours. Using
    the real reset keeps Pitwall's window aligned with Claude's — so the % stays truthful
    and a calibration doesn't break when Pitwall's own transcript-gap guess would reset
    the window at a different moment than Claude actually does."""
    start = reset_dt - timedelta(hours=window_hours)
    now = datetime.now(timezone.utc)
    tok, usd, by = 0, 0.0, {}
    for ts, t, u, fam in entries:
        if ts < start:
            continue
        tok += t
        usd += u
        m = by.setdefault(fam, {"tok": 0, "usd": 0.0})
        m["tok"] += t
        m["usd"] += u
    elapsed = max((now - start).total_seconds() / 3600, 1 / 60)
    return {"active": True, "tok": tok, "usd": usd, "reset": reset_dt,
            "pace": usd / elapsed, "by_model": by}


def valid_override(cfg, window_hours=None, now=None):
    """The pinned reset_override ONLY if it's a sane time for a rolling window — i.e.
    still in the future AND no more than window_hours away (a 5-hour window can't reset
    6 hours from now). A misread/garbled reset (or a stale one left over from an earlier
    bad sync) lands outside that range, so we reject it and let the caller fall back to
    the transcript-based window guess instead of showing an impossible countdown.
    Returns a tz-aware UTC datetime or None. (Both faces + inject_real_usage share this.)"""
    wh = window_hours if window_hours is not None else cfg.get("window_hours", 5)
    if now is None:
        now = datetime.now(timezone.utc)
    ov = parse_ts(cfg.get("reset_override") or "")
    if not ov:
        return None
    # +2 min slack for clock skew / the moment right at a reset boundary.
    if now < ov <= now + timedelta(hours=wh, minutes=2):
        return ov
    return None


def percentile(values, p):
    """Linear-interpolated p-th percentile (p in 0..1) of a list of numbers."""
    xs = sorted(values)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def learned_ceiling(window_hours, lookback_days, min_samples=3):
    """Your 'usual limit': the 90th-percentile spend across COMPLETED windows in
    the lookback. Returns None until there are enough finished windows to learn from.
    This mirrors the auto-detect trick from the Claude-Code-Usage-Monitor project,
    but stays entirely on your own machine and your own history."""
    entries = collect_entries(window_hours, lookback_hours=lookback_days * 24)
    span = timedelta(hours=window_hours)
    now = datetime.now(timezone.utc)
    done = [b["usd"] for b in block_windows(entries, window_hours)
            if (b["start"] + span) <= now]
    if len(done) < min_samples:
        return None
    return percentile(done, 0.9)


def fmt_tokens(n):
    if n >= 1e6:
        return f"{n/1e6:.2f}M"
    if n >= 1e3:
        return f"{n/1e3:.0f}k"
    return str(int(n))


# Tokens are not words. English rule of thumb: 1 token ≈ 0.75 of a word (rarer/longer
# words split into more tokens). We surface a WORD-equivalent as the human yardstick so
# "millions of tokens" stops feeling like "millions of words" — see Token Details §9.
WORDS_PER_TOKEN = 0.75


def words_of(tok):
    """Word-equivalent of a token count (estimate)."""
    return int(round((tok or 0) * WORDS_PER_TOKEN))


def fmt_words(tok):
    """Word-equivalent, comma-grouped for the Token Details hero figures."""
    return f"{words_of(tok):,}"


def session_split(s):
    """One session's new-vs-re-read mix this window, as percentages of its own tokens.
    'new' = input + output + cache-writes (your typing, files, Claude's replies, the
    one-time save to cache); 're-read' = cache-reads (the same chat re-sent every turn,
    the cost driver). Returns (new_pct, reread_pct); (0, 0) when the session has no
    counted tokens yet. The two always sum to 100 when there's any activity."""
    new = (s.get("in", 0) or 0) + (s.get("out", 0) or 0) + (s.get("cw", 0) or 0)
    reread = s.get("cr", 0) or 0
    tot = new + reread
    if tot <= 0:
        return 0.0, 0.0
    return new / tot * 100.0, reread / tot * 100.0


# How "full" a session's context is, as a plain word a beginner can read. Driven by
# frac = ctx / ctx_red (0..1). 'Hand off' matches the restart dialog's verb on purpose.
CTX_WORDS = ((0.45, "Light"), (0.75, "Filling"), (0.90, "Heavy"))


def fullness_word(frac):
    for thr, w in CTX_WORDS:
        if frac < thr:
            return w
    return "Hand off"


def session_row_frac(s, ctx_red):
    """Heat fraction for a session ROW's dot + state word: that session's CHAT
    FULLNESS (context tokens vs the re-read ceiling). Every open row's dot, its
    state word, the "this chat" dot, and the save pill therefore show ONE heat —
    how full the chat is (owner, 2026-06-12: "the dot on the open row, 'busy',
    'this chat filling', and the save pill all need to be the same heat").
    Supersedes the earlier 2026-06-12 model that coloured rows by $-share of the
    5h bar — the dots tell the CHAT story, the bar tells the 5h-$ story."""
    return min(1.0, (s.get("ctx", 0) or 0) / max(ctx_red or 1, 1))


def model_split(by_model):
    """[(family, $), ...] for families that actually spent this window, priciest first."""
    items = [(fam, v["usd"]) for fam, v in (by_model or {}).items() if v.get("usd", 0) > 0]
    items.sort(key=lambda kv: -kv[1])
    return items


def fmt_dur(hours):
    """Compact human duration from a float number of hours: '40m', '2h05m', '1d3h'."""
    mins = int(round(hours * 60))
    if mins < 1:
        return "<1m"
    h, m = divmod(mins, 60)
    if h >= 24:
        return f"{h // 24}d{h % 24}h"
    if h <= 0:
        return f"{m}m"
    return f"{h}h{m:02d}m"


def _rgb(hexs):
    return tuple(int(hexs[i:i + 2], 16) for i in (1, 3, 5))


def lerp_color(frac):
    """Smoothly blend green -> amber -> red as frac goes 0 -> 0.5 -> 1.
    Blended in HSV (hue taking the short way round the wheel) so every
    in-between color stays bright and saturated — a straight RGB blend of
    green and amber sags through a muddy olive on the way (owner,
    2026-06-12: the pill's "weird green")."""
    frac = max(0.0, min(1.0, frac))
    g, a, r = _rgb(GREEN), _rgb(AMBER), _rgb(RED)
    if frac <= 0.5:
        lo, hi, t = g, a, frac / 0.5
    else:
        lo, hi, t = a, r, (frac - 0.5) / 0.5
    h1, s1, v1 = colorsys.rgb_to_hsv(*(c / 255.0 for c in lo))
    h2, s2, v2 = colorsys.rgb_to_hsv(*(c / 255.0 for c in hi))
    dh = ((h2 - h1 + 0.5) % 1.0) - 0.5
    c = colorsys.hsv_to_rgb((h1 + dh * t) % 1.0,
                            s1 + (s2 - s1) * t,
                            v1 + (v2 - v1) * t)
    return "#%02x%02x%02x" % tuple(round(ch * 255) for ch in c)


# --- curated pitstop checkpoint recap (the "where we left off" line) --------
# The native Claude session title is just the FIRST instruction a session was given
# ("Read last memory", "Follow resume grid instructions") — useless as a recap and
# often identical across sessions. The pitstop ritual writes a curated per-project
# checkpoint (~/.claude/pitstop/resume_<slug>.txt) whose opening line IS the recap.
# We lift that line (matched to a session by the work folder named INSIDE the file,
# not by the drifting filename slug) and show it as the row label instead.
# (Self-contained on purpose: The Grid has its own copy of this; the two apps stay
# untangled — see C:\dev\CLAUDE.md.)
PITSTOP_DIR = os.path.join(os.path.expanduser("~"), ".claude", "pitstop")
# the launch path is one whitespace-free token; stop at the first space so a trailing
# sentence on the same line can't bleed into the captured path.
_START_IN_RE = re.compile(r"start this session in\s+(\S+)", re.IGNORECASE)
# "Resume <Project> - / – / — " lead-in stripped so the recap doesn't just repeat a title.
_RESUME_LEAD_RE = re.compile(r"^\s*Resume\b[^-–—]*[-–—]\s*", re.IGNORECASE)


def _ck_norm_dir(p):
    """Normalise a work folder for matching: lowercased, forward slashes, trailing
    slashes and sentence punctuation (picked up when lifted from prose) stripped."""
    return str(p or "").lower().replace("\\", "/").rstrip("./,;: ")


def _clean_recap(first_line):
    """Turn a checkpoint's opening line into a tight recap: drop the trailing
    'Start this session in <path>' instruction and the leading 'Resume <name> -'."""
    text = first_line.strip()
    idx = text.lower().find("start this session in")
    if idx != -1:
        text = text[:idx]
    text = _RESUME_LEAD_RE.sub("", text)
    return text.strip().rstrip(".").strip()


def checkpoint_index(pitstop_dir=PITSTOP_DIR):
    """Map each project folder (normalised) -> its newest checkpoint recap. Matched
    by the 'Start this session in <path>' line INSIDE the file (slugs drift; the path
    inside is authoritative). Returns {} if the pitstop folder is absent/unreadable."""
    index = {}
    if not os.path.isdir(pitstop_dir):
        return {}
    for f in glob.glob(os.path.join(pitstop_dir, "resume_*.txt")):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                first = next((ln for ln in fh if ln.strip()), "")
        except OSError:
            continue
        m = _START_IN_RE.search(first)
        if not m:
            continue
        recap = _clean_recap(first)
        if not recap:
            continue
        key = _ck_norm_dir(m.group(1))
        try:
            mtime = os.path.getmtime(f)
        except OSError:
            continue
        if key not in index or mtime > index[key][0]:
            index[key] = (mtime, recap)
    return {k: v[1] for k, v in index.items()}


def session_name(s):
    """A short human label for a session: its curated checkpoint recap (where we left
    off), else its native AI title, else the project folder."""
    recap = (s.get("recap") or "").strip()
    if recap:
        return recap
    label = (s.get("label") or "").strip()
    if label:
        return label
    cwd = (s.get("cwd") or "").rstrip("\\/")
    return os.path.basename(cwd) or cwd or s["sid"][:8]


def pid_alive(pid):
    """True only if the process id is currently running (not just a stale file)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return bool(ok) and code.value == 259        # STILL_ACTIVE


TRACKED_CLI_EXES = ("claude", "codex")   # codex: future-proofing (owner, 2026-06-11)


def pid_image(pid):
    """Lowercase image basename of a running pid, ".exe" stripped ("claude",
    "cmd", "claude.exe.old.178..."), or None when it can't be read."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if os.name != "nt":
        return None
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(len(buf))
        if not k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return None
        name = os.path.basename(buf.value).lower()
        return name[:-4] if name.endswith(".exe") else name
    finally:
        k.CloseHandle(h)


def live_registry():
    """Map sessionId -> its running-process record (with a verified 'alive' flag)."""
    reg = {}
    for p in glob.glob(os.path.join(SESSIONS_DIR, "*.json")):
        try:
            with open(p, encoding="utf-8") as fh:
                o = json.load(fh)
        except Exception:
            continue
        sid = o.get("sessionId")
        if not sid:
            continue
        alive = pid_alive(o.get("pid"))
        if alive and os.name == "nt":
            # A pid being alive isn't enough — a dead session's pid can be recycled
            # by an unrelated process, and the CLI auto-updater leaves orphaned
            # "claude.exe.old.<ts>" zombies running for days (the "dev" ghost row,
            # owner, 2026-06-11). Only an EXACT-named CLI binary counts as a live
            # session; an unreadable image name keeps the old behavior (fail-open).
            img = pid_image(o.get("pid"))
            if img is not None and img not in TRACKED_CLI_EXES:
                alive = False
        o["alive"] = alive
        prev = reg.get(sid)
        if not prev or (o.get("updatedAt") or 0) >= (prev.get("updatedAt") or 0):
            reg[sid] = o
    return reg


def jobs_info():
    """One dict per background job that has a state.json, with just the fields the
    faces need. Unreadable/half-written files are skipped (the daemon rewrites them
    constantly)."""
    jobs = []
    for p in glob.glob(os.path.join(JOBS_DIR, "*", "state.json")):
        try:
            with open(p, encoding="utf-8") as fh:
                o = json.load(fh)
        except Exception:
            continue
        jobs.append({
            "dir": os.path.dirname(p),
            "name": o.get("name"),
            "state": o.get("state"),
            "tempo": o.get("tempo"),
            "needs": o.get("needs"),
            "sessionId": o.get("sessionId"),
            "resumeSessionId": o.get("resumeSessionId"),
        })
    return jobs


def bg_session_ids(jobs=None):
    """Session ids that belong to background jobs — real Claude processes with no
    terminal window. The faces tag these rows 'bg' so the user isn't sent hunting
    for a window that doesn't exist (owner, 2026-06-12)."""
    ids = set()
    for j in (jobs_info() if jobs is None else jobs):
        for k in ("sessionId", "resumeSessionId"):
            if j.get(k):
                ids.add(j[k])
    return ids


def process_map():
    """pid -> (parent_pid, lowercase exe basename) for every live process, in one
    Toolhelp pass. {} off-Windows or on any failure (callers must degrade)."""
    if os.name != "nt":
        return {}
    import ctypes
    import ctypes.wintypes as wt

    class _PE32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD),
            ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
            ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
            ("szExeFile", wt.WCHAR * 260),
        ]

    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    snap = k.CreateToolhelp32Snapshot(0x00000002, 0)        # TH32CS_SNAPPROCESS
    if not snap:
        return {}
    entry = _PE32W()
    entry.dwSize = ctypes.sizeof(_PE32W)
    pmap = {}
    try:
        if k.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                pmap[entry.th32ProcessID] = (entry.th32ParentProcessID,
                                             entry.szExeFile.lower())
                if not k.Process32NextW(snap, ctypes.byref(entry)):
                    break
    finally:
        k.CloseHandle(snap)
    return pmap


def daemon_hosted(pid, pmap=None):
    """True when a session's process has a CLI binary among its ANCESTORS — the
    shape of a daemon-run background job (daemon claude -> pty-host claude ->
    session claude). A windowed session's parents are terminals/shells, never the
    CLI itself. Catches daemon respawns whose session id never made it into the
    job's state.json (the 2026-06-12 zombie-twin gap). Fail-open to False: an
    unreadable process tree must not mark a normal session 'bg'."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pmap is None:
        pmap = process_map()
    cli_exes = tuple(e + ".exe" for e in TRACKED_CLI_EXES)
    seen = set()
    cur = pmap.get(pid)
    for _ in range(8):                      # bounded walk; pid tables can have cycles
        if cur is None:
            return False
        parent = cur[0]
        if parent in seen or parent == 0:
            return False
        seen.add(parent)
        pcur = pmap.get(parent)
        if pcur is None:
            return False
        if pcur[1] in cli_exes:
            return True
        cur = pcur
    return False


def registry_stamp():
    """Cheap change-detector for the live-session registry: the sorted filenames of
    the registry dir. One file per session PROCESS, so the stamp changes exactly
    when a session appears or exits — the faces poll it every ~2s and refresh
    immediately on change, closing the post-pitstop blind spot where a freshly
    spawned CLI had no row for up to refresh_seconds (owner, 2026-06-12 video).
    Filenames only, deliberately: mtimes change every turn and would storm the
    full refresh while a session merely works."""
    try:
        with os.scandir(SESSIONS_DIR) as it:
            return tuple(sorted(e.name for e in it if e.name.endswith(".json")))
    except OSError:
        return ()


def _user_text(msg):
    """Plain text of a user message dict; None for tool results / structured noise."""
    content = (msg or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p) or None
    return None


def collect_sessions(window_hours):
    """Per-CLI-session rollup: one row per transcript active inside the window.
    Each carries its $ spend, last-activity time, 'ctx' = the tokens the most
    recent turn re-read (input + cache) — the thing a restart would reset to ~0 —
    and the window's token split by KIND ('in'/'out'/'cw'/'cr') so the UI can show
    THAT session's own new-vs-re-read mix (cr = the chat re-read every turn, the
    cost driver; new = in + out + cw)."""
    cutoff = time.time() - (window_hours + 1) * 3600
    span = timedelta(hours=window_hours)
    now = datetime.now(timezone.utc)
    sessions = []
    for path in glob.glob(os.path.join(PROJECTS_ROOT, "**", "*.jsonl"), recursive=True):
        sid = os.path.splitext(os.path.basename(path))[0]
        if sid.startswith("agent-"):     # sub-agent sidechain, not a CLI window
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                continue
        except OSError:
            continue
        s = {"sid": sid, "tok": 0,
             "usd": 0.0, "last": None, "ctx": 0, "cwd": None, "label": None,
             "first": None, "branch": None, "model": None, "path": path,
             "in": 0, "out": 0, "cw": 0, "cr": 0}
        seen = set()
        try:
            fh = open(path, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("cwd"):
                    s["cwd"] = o["cwd"]
                if o.get("gitBranch"):
                    s["branch"] = o["gitBranch"]
                if o.get("aiTitle"):
                    s["label"] = o["aiTitle"]
                # first real user message: the terminal tab title is a truncation of it,
                # so it's the strongest key for matching a session to its window
                if (s["first"] is None and o.get("type") == "user"
                        and not o.get("isSidechain") and not o.get("isMeta")):
                    t = _user_text(o.get("message"))
                    if t:
                        s["first"] = " ".join(t.split())[:160]
                if o.get("type") != "assistant":
                    continue
                msg = o.get("message") or {}
                u = msg.get("usage")
                if not u:
                    continue
                key = (o.get("requestId"), msg.get("id"))
                if key in seen:
                    continue
                seen.add(key)
                total, dollars, ctx = usage_cost(u, msg.get("model"))
                s["tok"] += total
                s["usd"] += dollars
                s["in"] += u.get("input_tokens", 0) or 0
                s["out"] += u.get("output_tokens", 0) or 0
                s["cw"] += u.get("cache_creation_input_tokens", 0) or 0
                s["cr"] += u.get("cache_read_input_tokens", 0) or 0
                ts = parse_ts(o.get("timestamp", ""))
                if ts and (s["last"] is None or ts > s["last"]):
                    s["last"] = ts
                    s["ctx"] = ctx               # context this turn re-read
                    s["model"] = model_family(msg.get("model"))   # model on latest turn
        if s["last"] and (now - s["last"]) < span and s["usd"] > 0:
            sessions.append(s)

    # --- merge in the live process registry (the real "open?" signal) ---
    reg = live_registry()
    jobs = jobs_info()
    bg = bg_session_ids(jobs)
    # A job that says it's working but is blocked needing something is stuck —
    # the daemon will sit on it (or respawn it) forever without a human. Tag the
    # matching session rows so the faces can warn instead of listing a riddle
    # (owner, 2026-06-12 — the overnight zombie job).
    stuck = set()
    for j in jobs:
        if j.get("state") == "working" and (j.get("tempo") == "blocked"
                                            or j.get("needs")):
            for k in ("sessionId", "resumeSessionId"):
                if j.get(k):
                    stuck.add(j[k])
    pmap = process_map()
    by_id = {s["sid"]: s for s in sessions}
    for sid, r in reg.items():
        s = by_id.get(sid)
        if s is None:                      # an open window with no spend yet this window
            if not r.get("alive"):
                continue
            s = {"sid": sid, "tok": 0, "usd": 0.0, "last": None, "ctx": 0,
                 "cwd": r.get("cwd"), "label": None, "first": None,
                 "branch": None, "model": None, "path": None,
                 "in": 0, "out": 0, "cw": 0, "cr": 0}
            sessions.append(s)
            by_id[sid] = s
        s["open"] = bool(r.get("alive"))
        # 'bg' from the job's own state file, OR from process ancestry: a daemon
        # respawn gets a fresh session id the state file never mentions, but its
        # process is still a child of the CLI daemon (the 2026-06-12 zombie twin).
        s["daemon"] = s["open"] and daemon_hosted(r.get("pid"), pmap)
        s["bg"] = s["open"] and (sid in bg or s["daemon"])
        s["stuck"] = s["open"] and sid in stuck
        s["status"] = r.get("status")
        s["entrypoint"] = r.get("entrypoint")
        s["pid"] = r.get("pid")
        if not s.get("cwd"):
            s["cwd"] = r.get("cwd")
    for s in sessions:                     # closed sessions had no registry entry
        s.setdefault("open", False)
        s.setdefault("bg", False)
        s.setdefault("daemon", False)
        s.setdefault("stuck", False)
        s.setdefault("status", None)
        s.setdefault("entrypoint", None)
        s.setdefault("pid", None)
        s.setdefault("path", None)

    # attach the curated checkpoint recap (the "where we left off" row label),
    # built once for the whole rollup and matched by each session's work folder.
    idx = checkpoint_index()
    for s in sessions:
        s["recap"] = idx.get(_ck_norm_dir(s.get("cwd"))) if s.get("cwd") else None

    # open windows first, then most-recent activity
    sessions.sort(key=lambda s: (0 if s["open"] else 1,
                                 -(s["last"].timestamp() if s["last"] else 0)))
    return sessions


# =============================================================================
# Focus-follow — which CLI window (and so which session) is the user looking at
# =============================================================================
# Pitwall's headline heat normally tracks "the most-recently-active open session".
# FocusTracker lets it instead track the session in the window you're focused on.
# Two signals: the ACTIVE window (GetForegroundWindow — keyboard Alt+Tab AND
# mouse-click) or the window under the CURSOR (WindowFromPoint).
#
# A terminal window can't be mapped to its claude session by process tree (ConPTY
# puts claude.exe in a different process from the WindowsTerminal window), so the
# pairing is AUTO-LEARNED by coincidence: while a terminal window is focused,
# whichever session is doing work / is busy earns votes toward that window, and the
# window's title (an AI summary) vs the session's title is a backup hint. Strongest
# evidence wins. Pitwall's OWN window is never bound (matched by our own PID), and the
# last real terminal window is remembered so glancing at Pitwall doesn't lose it.
#
# Windows-only (ctypes, lazily loaded like the rest of this module's Win32 use). On
# any other OS, or if a Win32 call is unavailable, every method degrades to "no
# focused session" and the caller falls back to its normal most-recent pick.

_FOCUS_WIN32 = None          # cached dict of ctypes handles, or False if unavailable
_GA_ROOT = 2
_PROC_QUERY_LIMITED = 0x1000
# Process images that mean "this window is a terminal" (where a CLI session lives).
TERMINAL_IMAGES = {"windowsterminal.exe", "wt.exe", "conhost.exe", "powershell.exe",
                   "pwsh.exe", "cmd.exe", "alacritty.exe", "wezterm-gui.exe"}


def _focus_win32():
    """Build (once) the Win32 callables the tracker needs, with explicit arg/return
    types so 64-bit HWNDs aren't truncated. Returns the handle dict, or False if this
    isn't Windows / the calls can't be set up."""
    global _FOCUS_WIN32
    if _FOCUS_WIN32 is not None:
        return _FOCUS_WIN32
    if not sys.platform.startswith("win"):
        _FOCUS_WIN32 = False
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        u.GetCursorPos.restype = wintypes.BOOL
        u.WindowFromPoint.argtypes = [wintypes.POINT]
        u.WindowFromPoint.restype = wintypes.HWND
        u.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        u.GetAncestor.restype = wintypes.HWND
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                               ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                      ctypes.POINTER(wintypes.FILETIME),
                                      ctypes.POINTER(wintypes.FILETIME),
                                      ctypes.POINTER(wintypes.FILETIME)]
        k.GetProcessTimes.restype = wintypes.BOOL
        # window-flash plumbing (the click-a-session-row -> ring its window feature)
        u.EnumWindows.restype = wintypes.BOOL
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsWindowVisible.restype = wintypes.BOOL
        u.IsIconic.argtypes = [wintypes.HWND]
        u.IsIconic.restype = wintypes.BOOL
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetWindowRect.restype = wintypes.BOOL
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_uint]
        u.SetWindowPos.restype = wintypes.BOOL
        u.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
        u.GetWindow.restype = wintypes.HWND
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongW.restype = ctypes.c_long
        u.FlashWindowEx.argtypes = [ctypes.c_void_p]
        u.FlashWindowEx.restype = wintypes.BOOL
        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        u.GetAsyncKeyState.restype = ctypes.c_short
        try:
            d = ctypes.WinDLL("dwmapi")
            d.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                                ctypes.c_void_p, wintypes.DWORD]
            d.DwmGetWindowAttribute.restype = ctypes.c_long   # HRESULT
        except Exception:
            d = None
        _FOCUS_WIN32 = {"ct": ctypes, "wt": wintypes, "u": u, "k": k, "d": d}
    except Exception:
        _FOCUS_WIN32 = False
    return _FOCUS_WIN32


def _win_root(hwnd):
    w = _focus_win32()
    if not hwnd:
        return 0
    return int(w["u"].GetAncestor(hwnd, _GA_ROOT) or 0)


def _foreground_window():
    w = _focus_win32()
    return _win_root(w["u"].GetForegroundWindow())


def _cursor_window():
    w = _focus_win32()
    pt = w["wt"].POINT()
    if not w["u"].GetCursorPos(w["ct"].byref(pt)):
        return 0
    return _win_root(w["u"].WindowFromPoint(pt))


def _win_title(hwnd):
    w = _focus_win32()
    buf = w["ct"].create_unicode_buffer(512)
    w["u"].GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _win_pid(hwnd):
    w = _focus_win32()
    pid = w["wt"].DWORD()
    w["u"].GetWindowThreadProcessId(hwnd, w["ct"].byref(pid))
    return pid.value


def _win_image(pid):
    """Basename of the process's exe (read-only, low-priv handle, always closed)."""
    if not pid:
        return ""
    w = _focus_win32()
    h = w["k"].OpenProcess(_PROC_QUERY_LIMITED, False, pid)
    if not h:
        return ""
    try:
        size = w["wt"].DWORD(1024)
        buf = w["ct"].create_unicode_buffer(1024)
        if w["k"].QueryFullProcessImageNameW(h, 0, buf, w["ct"].byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        w["k"].CloseHandle(h)


def _proc_start_time(pid):
    """Process creation time as a raw FILETIME int, or None. The anti-recycling check:
    a reused PID gets a new start time, so (pid, start_time) names ONE specific process.
    Same format the session→window map hook records, so the two compare directly."""
    w = _focus_win32()
    if not w or not pid:
        return None
    h = w["k"].OpenProcess(_PROC_QUERY_LIMITED, False, pid)
    if not h:
        return None
    try:
        ft = [w["wt"].FILETIME() for _ in range(4)]
        if not w["k"].GetProcessTimes(h, *[w["ct"].byref(x) for x in ft]):
            return None
        return (ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime
    finally:
        w["k"].CloseHandle(h)


_SIDMAP_DIR = os.path.join(os.path.expanduser("~"), ".claude", "pitstop", "sidmap")


def _sidmap_window(sid):
    """The terminal hwnd a hook recorded for this session, re-verified, or None.

    The session_window_map.py hook (UserPromptSubmit, lives with the pitstop tools)
    runs INSIDE each session's process tree, so it can identify the hosting terminal
    window deterministically — no title or focus guessing. This is the strongest
    evidence window_for_sid has, but only after re-verifying the record against the
    live window: handles and PIDs get recycled, so every field must still match
    (window alive + visible + still a terminal + same owning process, by pid AND
    process start time). Any mismatch or missing piece → None, fall through.
    Sessions without the hook (other machines, public users) simply have no map file."""
    try:
        with open(os.path.join(_SIDMAP_DIR, sid + ".json"), encoding="utf-8") as fh:
            fp = json.load(fh).get("fp") or {}
        hwnd, pid = int(fp["hwnd"]), int(fp["pid"])
        start = int(fp["pid_start"])
    except Exception:
        return None
    w = _focus_win32()
    if not w or not w["u"].IsWindowVisible(hwnd):
        return None
    if _win_pid(hwnd) != pid or _proc_start_time(pid) != start:
        return None
    if _win_image(pid).lower() not in TERMINAL_IMAGES:
        return None
    r = window_rect(hwnd)
    if not r or r[2] <= 0 or r[3] <= 0:
        return None
    return hwnd


def _terminal_windows():
    """Every visible top-level window owned by a terminal process — the windows a
    CLI session could live in. Minimized windows count (their visible flag stays set)."""
    w = _focus_win32()
    if not w:
        return []
    found = []

    @w["ct"].WINFUNCTYPE(w["wt"].BOOL, w["wt"].HWND, w["wt"].LPARAM)
    def cb(hwnd, _):
        try:
            if w["u"].IsWindowVisible(hwnd):
                if _win_image(_win_pid(hwnd)).lower() in TERMINAL_IMAGES:
                    r = window_rect(int(hwnd))
                    # zero-size phantoms (console pty-host helper windows) aren't
                    # real windows a session could live in — and would poison the
                    # only-one-terminal shortcut
                    if r and r[2] > 0 and r[3] > 0:
                        found.append(int(hwnd))
        except Exception:
            pass
        return True

    w["u"].EnumWindows(cb, 0)
    return found


def _norm_title(s):
    """Window title with leading spinner glyphs stripped, whitespace collapsed, lowered."""
    s = (s or "").strip()
    while s and not s[0].isalnum():
        s = s[1:].lstrip()
    return " ".join(s.split()).lower()


def window_rect(hwnd):
    """(left, top, width, height) of a window's VISIBLE frame in PHYSICAL pixels, or
    None if it's gone. Uses the DWM extended frame bounds: plain GetWindowRect includes
    the invisible resize borders Windows 10/11 draw around every window (~7px a side),
    so a ring placed on it floats off the frame instead of hugging it."""
    w = _focus_win32()
    if not w:
        return None
    r = w["wt"].RECT()
    if w.get("d"):
        try:
            if w["d"].DwmGetWindowAttribute(hwnd, 9,            # EXTENDED_FRAME_BOUNDS
                                            w["ct"].byref(r),
                                            w["ct"].sizeof(r)) == 0:
                return (r.left, r.top, r.right - r.left, r.bottom - r.top)
        except Exception:
            pass
    if not w["u"].GetWindowRect(hwnd, w["ct"].byref(r)):
        return None
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def window_minimized(hwnd):
    w = _focus_win32()
    return bool(w and w["u"].IsIconic(hwnd))


def window_focused(hwnd):
    """True when hwnd is the foreground window."""
    return bool(_focus_win32()) and _foreground_window() == hwnd


def pointer_pressed_in(hwnd):
    """True while the left mouse button is down with the pointer over hwnd."""
    w = _focus_win32()
    if not w:
        return False
    try:
        if not (w["u"].GetAsyncKeyState(0x01) & 0x8000):     # VK_LBUTTON held
            return False
        return _cursor_window() == hwnd
    except Exception:
        return False


def flash_taskbar(hwnd):
    """Flash the window's taskbar button until it's brought to the foreground — the
    standard Windows 'look here' cue, for targets with no frame on screen (minimized)."""
    w = _focus_win32()
    if not w:
        return False
    ct, wt = w["ct"], w["wt"]

    class FLASHWINFO(ct.Structure):
        _fields_ = [("cbSize", wt.UINT), ("hwnd", wt.HWND), ("dwFlags", wt.DWORD),
                    ("uCount", wt.UINT), ("dwTimeout", wt.DWORD)]

    fi = FLASHWINFO(ct.sizeof(FLASHWINFO), hwnd, 0x2 | 0xC, 0, 0)  # TRAY | TIMERNOFG
    return bool(w["u"].FlashWindowEx(ct.byref(fi)))


def place_window_above(hwnd, x, y, width, height, target=None):
    """Pin a native window to a PHYSICAL-pixel rect without focusing it. With a
    target, the window slots into the z-order DIRECTLY above the target, so anything
    covering the target covers this window too (owner test, flash6: a topmost ring drew
    over windows stacked on its target and read as detached). Without a target it
    pins topmost. (Win32 placement sidesteps Qt's per-monitor logical-pixel mapping.)"""
    w = _focus_win32()
    if not w:
        return
    flags = 0x0010                                    # NOACTIVATE
    after = -1                                        # HWND_TOPMOST
    if target:
        prev = int(w["u"].GetWindow(target, 3) or 0)  # GW_HWNDPREV: window above target
        if prev == hwnd:
            flags |= 0x0004                           # NOZORDER — already in place
            after = 0
        else:
            after = prev                              # 0 → HWND_TOP (target is topmost)
    w["u"].SetWindowPos(hwnd, after, x, y, width, height, flags)


def _dwm_cloaked(w, hwnd):
    """Cloaked windows (suspended UWP apps etc.) pass IsWindowVisible but are
    not really on screen — without this filter they'd false-positive the
    topmost-anomaly walk."""
    if not w["d"]:
        return False
    val = w["wt"].DWORD(0)
    try:
        if w["d"].DwmGetWindowAttribute(hwnd, 14,        # DWMWA_CLOAKED
                                        w["ct"].byref(val), 4) == 0:
            return bool(val.value)
    except Exception:
        pass
    return False


def topmost_anomaly(hwnd):
    """The intermittent always-on-top loss (Kevin's repro hint 2026-06-12:
    'after a new cli has spawned from a pstop'). Observed live: the widget can
    KEEP WS_EX_TOPMOST yet sit under normal-band windows, so check both.
    Returns (anomalous, evidence). Anomalous = the flag is gone, OR a visible
    non-topmost window sits ABOVE us in z (band corruption). A topmost window
    above us (context menus, other pinned apps) is the band working normally —
    NOT an anomaly, and the reason the guard must never blindly reassert."""
    w = _focus_win32()
    if not w or not hwnd:
        return False, []
    hwnd = _win_root(hwnd)        # Tk hands us a child frame; Qt the top-level
    if not w["u"].GetWindowLongW(hwnd, -20) & 0x8:       # GWL_EXSTYLE/TOPMOST
        return True, ["WS_EX_TOPMOST flag stripped"]
    evidence = []
    cur = int(w["u"].GetWindow(hwnd, 3) or 0)            # GW_HWNDPREV → upward
    for _ in range(200):
        if not cur:
            break
        if (w["u"].IsWindowVisible(cur)
                and not (w["u"].GetWindowLongW(cur, -20) & 0x8)
                and not _dwm_cloaked(w, cur)):
            r = w["wt"].RECT()
            w["u"].GetWindowRect(cur, w["ct"].byref(r))
            if r.right - r.left >= 40 and r.bottom - r.top >= 40:
                evidence.append("%s pid=%s" % (
                    (_win_title(cur) or "(untitled)")[:60], _win_pid(cur)))
                if len(evidence) >= 3:
                    break
        cur = int(w["u"].GetWindow(cur, 3) or 0)
    return bool(evidence), evidence


def topmost_reassert(hwnd, evidence=None):
    """Put the widget back at the top of the topmost band — no focus steal,
    no move/resize — and log what was sitting above it so the culprit is on
    record for the eventual root cause."""
    w = _focus_win32()
    if not w or not hwnd:
        return
    hwnd = _win_root(hwnd)
    w["u"].SetWindowPos(hwnd, -1, 0, 0, 0, 0,            # HWND_TOPMOST
                        0x0001 | 0x0002 | 0x0010)        # NOSIZE|NOMOVE|NOACTIVATE
    try:
        with open(os.path.join(HERE, "topmost_guard.log"), "a",
                  encoding="utf-8") as f:
            f.write("%s reasserted topmost; above us: %s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"),
                "; ".join(evidence or []) or "(flag stripped)"))
    except OSError:
        pass


class FocusTracker:
    """Stateful, per-process. The widget holds ONE instance and calls focused_sid()
    each refresh; it returns the sessionId of the session in the window you're focused
    on, or None meaning 'fall back to the normal most-recent pick'. Never raises."""

    def __init__(self):
        self.own_pid = os.getpid()
        self.bind = {}            # hwnd -> {sid: vote weight}  (learned pairing)
        self.last_terminal = {}   # mode -> hwnd of the last real terminal window seen
        self.win_title = {}       # hwnd -> last-seen window title (for the title hint)
        self.prev_last = {}       # sid -> last activity epoch (transcript-advance edges)

    def _terminal_window(self, mode):
        """Current terminal hwnd for a mode. Ignores Pitwall's own window and any
        non-terminal app, and remembers the last real terminal so focus landing on
        Pitwall itself doesn't drop the session."""
        hwnd = _cursor_window() if mode == "mouse" else _foreground_window()
        if hwnd:
            try:
                pid = _win_pid(hwnd)
                if pid and pid != self.own_pid:          # never bind Pitwall's own window
                    if _win_image(pid).lower() in TERMINAL_IMAGES:
                        self.last_terminal[mode] = hwnd
                        self.win_title[hwnd] = _win_title(hwnd)
            except Exception:
                pass
        return self.last_terminal.get(mode, 0)

    def _learn(self, hwnd, sessions):
        """Accumulate evidence that `hwnd` belongs to a session. Strongest first:
        a transcript that just advanced (+3), a title match (+2), a lone busy
        session (+1.5), or — when ambiguous — a thin spread over the busy ones."""
        # update activity-edge state for ALL sessions (seed silently on first sight).
        # Background-job sessions never vote: they have NO window, so their activity/
        # busy signals would bind them to whatever terminal happens to be focused.
        edges = []
        for s in sessions:
            last = s.get("last")
            ts = last.timestamp() if last else 0.0
            prev = self.prev_last.get(s["sid"])
            if prev is not None and ts > prev + 0.001 and not s.get("bg"):
                edges.append(s["sid"])
            self.prev_last[s["sid"]] = ts
        if not hwnd:
            return
        votes = self.bind.setdefault(hwnd, {})
        for sid in edges:
            votes[sid] = votes.get(sid, 0.0) + 3.0
        busy = [s for s in sessions
                if s.get("open") and s.get("status") == "busy" and not s.get("bg")]
        if len(busy) == 1:
            votes[busy[0]["sid"]] = votes.get(busy[0]["sid"], 0.0) + 1.5
        else:
            for s in busy:
                votes[s["sid"]] = votes.get(s["sid"], 0.0) + 0.3
        title = (self.win_title.get(hwnd) or "").lower()
        if title:
            for s in sessions:
                lab = (s.get("label") or "").lower()
                if (len(lab) > 6 and (lab in title or title in lab)
                        and not s.get("bg")):
                    votes[s["sid"]] = votes.get(s["sid"], 0.0) + 2.0
        # forget sessions that are gone, so the vote map stays small + current
        live = {s["sid"] for s in sessions}
        for sid in [x for x in votes if x not in live]:
            del votes[sid]

    def _resolve(self, hwnd, sessions):
        by_sid = {s["sid"]: s for s in sessions}
        votes = self.bind.get(hwnd)
        if votes:
            sid = max(votes, key=votes.get)
            if by_sid.get(sid, {}).get("open"):
                return sid
        title = (self.win_title.get(hwnd) or "").lower()        # fallback: title match
        if title:
            for s in sessions:
                lab = (s.get("label") or "").lower()
                if len(lab) > 6 and (lab in title or title in lab) and s.get("open"):
                    return s["sid"]
        return None

    def window_for_sid(self, sid, sessions):
        """Reverse of focused_sid: the terminal window a session lives in, or None.
        Returns a window only when the evidence is unambiguous — flashing the WRONG
        window would be worse than admitting we don't know (cli_window.py's
        'never guess' rule). See window_evidence for the evidence chain."""
        return self.window_evidence(sid, sessions)[0]

    def window_evidence(self, sid, sessions):
        """(hwnd, plain-English evidence line) — hwnd None when nothing unambiguous.
        Evidence, strongest first: the hook-recorded session→window map
        (deterministic — written from inside the session's own process tree,
        re-verified against the live window before use); it's the only terminal
        there is; a window title contains/is contained by the session's first user
        message (the tab title is a truncation of it) or its AI label; the learned
        focus pairing names it. Titles outrank votes here: _learn credits a
        session's activity to whatever window has FOCUS, so a background session's
        votes pile onto the wrong window (the 2026-06-10 wrong-window-flash bug).
        The evidence line feeds the session-details view (owner, 2026-06-12) so a
        surprising flash can be audited instead of argued with."""
        if not _focus_win32():
            return None, "window lookup unavailable (not Windows)"
        try:
            mapped = _sidmap_window(sid)
            if mapped:
                return mapped, "hook-recorded from inside the session (re-verified)"
            wins = _terminal_windows()
            if not wins:
                return None, "no terminal windows are open"
            if len(wins) == 1:
                return wins[0], "it's the only terminal window open"
            s = next((x for x in sessions if x["sid"] == sid), {})
            for key, kind in ((s.get("first"), "first message"),
                              (s.get("label"), "AI title")):
                key = " ".join((key or "").split()).lower()
                if len(key) < 10:
                    continue
                hits = []
                for h in wins:
                    t = _norm_title(_win_title(h))
                    if len(t) >= 10 and (key in t or t in key):
                        hits.append(h)
                if len(hits) == 1:
                    return hits[0], "window title matches the session's %s" % kind
            voted = [h for h in wins
                     if self.bind.get(h)
                     and max(self.bind[h], key=self.bind[h].get) == sid]
            if len(voted) == 1:
                return voted[0], "learned by watching focus while it worked"
            return None, ("%d terminal windows, none provably this session's"
                          % len(wins))
        except Exception:
            return None, "window lookup failed"

    def focused_sid(self, sessions, mode="window"):
        """sessionId of the focused session, or None to fall back. mode = 'window'
        (active window, default), 'mouse' (cursor), or 'off'."""
        if mode == "off" or not _focus_win32():
            return None
        try:
            # learning always follows the ACTIVE window (the authoritative focus),
            # even when resolving by mouse, so the pairing stays correct.
            self._learn(self._terminal_window("window"), sessions)
            return self._resolve(self._terminal_window(mode), sessions)
        except Exception:
            return None


def _sidmap_raw(sid):
    """The hook's recorded window fingerprint for a session, UNverified — display
    only (the verified path is _sidmap_window). None when no map file exists."""
    try:
        with open(os.path.join(_SIDMAP_DIR, sid + ".json"), encoding="utf-8") as fh:
            return json.load(fh).get("fp") or None
    except Exception:
        return None


def session_details(s, tracker=None, sessions=None):
    """Ordered (label, value) pairs describing one session row — the
    troubleshooting view (owner, 2026-06-12: 'detailed info on the cli that
    Pitwall shows, PID, name etc, so the user can paste it out'). Everything is a
    plain string so both faces render it as-is and 'copy all' is a join. Values
    are re-checked at call time (pid + image verified live, window evidence named)
    rather than echoing the row — this view exists for the moments the row looks
    wrong."""
    rows = [("Name", session_name(s)), ("Session id", s.get("sid") or "?")]

    status = s.get("status") or ("open" if s.get("open") else "closed")
    if not s.get("open"):
        status = "closed"
    notes = []
    if s.get("bg"):
        notes.append("background job")
    if s.get("daemon"):
        notes.append("daemon-hosted")
    if s.get("stuck"):
        notes.append("STUCK — needs attention")
    rows.append(("Status", status + (" (" + ", ".join(notes) + ")" if notes else "")))

    pid = s.get("pid")
    if pid:
        if pid_alive(pid):
            img = pid_image(pid)
            if img is None:
                proc = "%s (running; image unreadable)" % pid
            elif img in TRACKED_CLI_EXES:
                proc = "%s (%s.exe — verified live)" % (pid, img)
            else:
                proc = "%s (now '%s' — NOT the CLI; stale or recycled pid)" % (pid, img)
        else:
            proc = "%s (ended)" % pid
        rows.append(("Process", proc))

    if s.get("open"):
        if s.get("bg"):
            win = "none — background session, no terminal window"
            fp = _sidmap_raw(s.get("sid") or "")
            if fp and fp.get("title"):
                win += "; last recorded window was '%s'" % fp["title"]
            rows.append(("Window", win))
            # stranger-test item 17 (owner, 2026-06-12): the exact command to reach a
            # background session belongs HERE (Copy all works), not in a tooltip.
            rows.append(("Resume command",
                         "claude --resume %s" % (s.get("sid") or "?")))
        elif tracker is not None:
            hwnd, why = tracker.window_evidence(s.get("sid"), sessions or [])
            if hwnd:
                title = _win_title(hwnd) if _focus_win32() else ""
                rows.append(("Window", "'%s' — %s" % (title, why)))
            else:
                fp = _sidmap_raw(s.get("sid") or "")
                if fp and fp.get("title"):
                    rows.append(("Window", "unknown (%s); last recorded was '%s'"
                                 % (why, fp["title"])))
                else:
                    rows.append(("Window", "unknown — %s" % why))

    for j in jobs_info():
        if s.get("sid") in (j.get("sessionId"), j.get("resumeSessionId")):
            job = "%s — %s" % (j.get("name") or "unnamed", j.get("state") or "?")
            if j.get("needs"):
                job += "; needs: %s" % j["needs"]
            rows.append(("Job", job))
            rows.append(("Job folder", j.get("dir") or "?"))
            break

    if s.get("model"):
        rows.append(("Model", MODEL_LABEL.get(s["model"], s["model"])))
    if s.get("cwd"):
        rows.append(("Folder", s["cwd"]))
    if s.get("branch"):
        rows.append(("Git branch", s["branch"]))
    if s.get("first"):
        first = s["first"]
        rows.append(("First message", first[:90] + ("…" if len(first) > 90 else "")))
    if s.get("path"):
        rows.append(("Transcript", os.path.normpath(s["path"])))
    if s.get("last"):
        rows.append(("Last activity",
                     s["last"].astimezone().strftime("%Y-%m-%d %H:%M:%S")))
    if s.get("tok"):
        rows.append(("This 5h window",
                     "%s tokens · $%.2f (new %s · re-read %s)"
                     % (fmt_tokens(s["tok"]), s.get("usd", 0.0),
                        fmt_tokens((s.get("in", 0) or 0) + (s.get("out", 0) or 0)
                                   + (s.get("cw", 0) or 0)),
                        fmt_tokens(s.get("cr", 0) or 0))))
    else:
        rows.append(("This 5h window", "no spend — lifetime use may be older than "
                                       "the window"))
    w = pitstop_watch(s)
    if w:
        rows.append(("Pitstop watch", w["line"]))
    return rows


def details_text(rows):
    """The copy-all payload: one 'Label: value' line per pair."""
    return "\n".join("%s: %s" % (k, v) for k, v in rows)


def parse_reset_input(s, now_local):
    """Turn what the user types into an absolute local reset time.
    Accepts: a duration left ('4h44m', '4hr 44min', '44m', '5h'),
             a clock time ('8:30pm', '8pm', '20:30'),
             or blank/'clear' -> the string 'CLEAR'.
    Returns a tz-aware datetime, the marker 'CLEAR', or None if unparseable."""
    s = (s or "").strip().lower()
    s = s.replace("resets in", "").replace("resets", "").replace("~", "").strip()
    if s in ("", "clear", "none", "off", "-"):
        return "CLEAR"
    ampm = "am" in s or "pm" in s
    # --- duration remaining (no am/pm, mentions h/m) ---
    if not ampm and re.search(r"\d\s*(h|hr|m|min)", s):
        mh = re.search(r"(\d+)\s*h", s)
        mm = re.search(r"(\d+)\s*m", s)
        hours = int(mh.group(1)) if mh else 0
        mins = int(mm.group(1)) if mm else 0
        # A 5-hour-window reset is at most hours out; bound the input so an absurd
        # paste (e.g. '9999999999h') can't OverflowError out of timedelta — treat it
        # as unparseable instead, so callers show their "couldn't read it" message.
        # (Ivan P3, 2026-06-06 — fixed in ts_core so both faces are covered.)
        if (mh or mm) and hours <= 168 and mins <= 1440:
            return now_local + timedelta(hours=hours, minutes=mins)
    # --- clock time 'H:MM' (+ optional am/pm) ---
    m = re.match(r"^(\d{1,2}):(\d{2})", s)
    if m:
        hh, mn = int(m.group(1)), int(m.group(2))
        if ampm:
            if "pm" in s and hh != 12:
                hh += 12
            if "am" in s and hh == 12:
                hh = 0
        if ampm or hh > 12:                       # an actual clock time
            t = now_local.replace(hour=hh % 24, minute=mn, second=0, microsecond=0)
            return t + timedelta(days=1) if t <= now_local else t
        return now_local + timedelta(hours=hh, minutes=mn)   # bare 4:44 = time left
    # --- bare clock '8pm' / '8 am' ---
    m = re.match(r"^(\d{1,2})\s*(am|pm)$", s)
    if m:
        hh = int(m.group(1))
        if m.group(2) == "pm" and hh != 12:
            hh += 12
        if m.group(2) == "am" and hh == 12:
            hh = 0
        t = now_local.replace(hour=hh % 24, minute=0, second=0, microsecond=0)
        return t + timedelta(days=1) if t <= now_local else t
    return None


def fmt_countdown(reset):
    if reset is None:
        return "—"
    secs = int((reset - datetime.now(timezone.utc)).total_seconds())
    if secs <= 0:
        return "0:00:00"
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def stable_reset(guess, prev_anchor, now=None):
    """Keep the "Resets in" countdown monotonic across refreshes.

    The raw transcript guess for the window reset is `block_start + window_hours`
    (see active_window). That block_start is data-dependent: as the oldest entries
    age out of the scan, or as new sessions get written mid-window, the latest
    block's start can shift LATER between refreshes — which pushes the guessed
    reset later and makes the countdown tick UPWARD. A countdown must only ever go
    down, then jump up exactly once when the window genuinely resets.

    Given the new `guess` and the `prev_anchor` we showed last tick, return the
    anchor to show now:
      - no guess (idle)                  -> None (clear the anchor).
      - no prior anchor                  -> adopt the guess (first reading).
      - guess <= prev_anchor             -> adopt it (countdown keeps going down).
      - guess  > prev_anchor, old anchor already elapsed (now >= prev_anchor)
                                         -> a real new window started: adopt the
                                            later guess. This is the ONE allowed
                                            jump up.
      - guess  > prev_anchor, old anchor still in the future
                                         -> transcript jitter: hold the old anchor
                                            steady. This is what kills the
                                            backwards tick and the mid-window jumps.

    NOTE: only apply this to the *guessed* reset path. A manual reset_override is
    authoritative and already stable — adopt it directly so a deliberate re-pin to
    a later time isn't suppressed.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if guess is None:
        return None
    if prev_anchor is None or guess <= prev_anchor or now >= prev_anchor:
        return guess
    return prev_anchor


def parse_pct(s):
    """A percentage typed off the Usage screen -> float 0..100, or None if blank/bad.
    Accepts '26', '26%', ' 26 % '."""
    s = (s or "").strip().replace("%", "").strip()
    if s == "":
        return None
    try:
        return max(0.0, min(100.0, float(s)))
    except ValueError:
        return None


def parse_rate(s):
    """A price typed off Anthropic's pricing page ($ per 1M tokens) -> positive float, or
    None if blank/bad. Accepts '10', '$10', '12.50', ' $3 '. Must be > 0 (a zero or
    negative price is rejected, not stored)."""
    s = (s or "").strip().lstrip("$").strip()
    if s == "":
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def age_str(iso):
    """How long ago an ISO-UTC timestamp was: 'just now', '5m ago', '2h ago'."""
    try:
        t = datetime.fromisoformat(iso)
    except Exception:
        return ""
    secs = (datetime.now(timezone.utc) - t).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs / 60)}m ago"
    if secs < 86400:
        return f"{secs / 3600:.0f}h ago"
    return f"{secs / 86400:.0f}d ago"



# --- derived gauges (shared by both faces so they can NEVER disagree) --------
# These sit on top of the raw token math above and turn it into the things the UI
# shows: which ceiling to measure against, how much of the allowance is used, the
# pace-dot colour, the projection sentence, the save-recommendation word, and a
# session's state label. The PySide6 widget calls THESE — there is exactly one copy,
# so the numbers are computed once.

# Save-recommendation ladder. Driven by THIS conversation's context fullness
# (ctx / ctx_red), NOT the 5-hour spend. Wording is the owner's "action-first" set
# (2026-06-06); cut points are Sarah's option A (2026-06-06) so each pill change
# nests inside a fullness-word band (0.45 / 0.75 / 0.90). (thr, word): show `word`
# while frac < thr; the last tier is the catch-all at/after the final threshold.
SAVE_TIERS = [
    (0.20, "All clear"),
    (0.45, "Looking good"),
    (0.75, "Maybe save soon"),
    (0.90, "Save & start fresh"),
    (2.00, "Hand off now"),
]


def save_reco(frac):
    """The save-recommendation word for a conversation-fullness fraction (0..1+)."""
    for thr, word in SAVE_TIERS:
        if frac < thr:
            return word
    return SAVE_TIERS[-1][1]


# --- "Nudge me" (Mode 2 auto-handoff, the SAFE FLOOR) ------------------------
# The user-driven hand-off mode: Pitwall watches the active session and, when a
# hand-off would actually save, taps the user's shoulder. The user (not Pitwall)
# types /pitstop then /clear. Pitwall issues no command and pushes no text — that
# one-way property is what makes this the safe floor (the whole hands-off attack
# surface is simply absent). The decision rides the SAME ctx-fullness ladder the
# pill already uses, so a tap can never contradict the word on the card.

# Env vars that would move a session OFF the logged-in subscription onto a metered,
# exfiltratable key. Mirrors the hand-off spike's HR-1 scrub set plus the Bedrock/
# Vertex family from the spike code review (M1). If ANY is set, "Nudge me" refuses
# to arm — in Mode 2 the user launches their own window, so Pitwall cannot SCRUB the
# env, it can only decline to arm (Ivan F12; owner, 2026-06-08 chose refuse, not warn).
NUDGE_OFF_SUBSCRIPTION_ENV = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_URL", "CLAUDE_API_KEY", "ANTHROPIC_MODEL",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
    "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_API_KEY_HELPER",
)

# Prefix-matched cousins (F-N1): any env var whose NAME starts with one of these forces
# a refusal too. ANTHROPIC_DEFAULT_<MODEL> (e.g. ANTHROPIC_DEFAULT_SONNET_MODEL) repoints
# a session at a specific model endpoint and rides with the metered-key family, but the
# suffix varies per model so it can't be an exact entry above. We deliberately do NOT
# match raw HTTP_PROXY / AWS_* / GOOGLE_* here: those are common on ordinary subscription
# machines and would cause false refusals, and the Bedrock/Vertex routes they'd serve are
# already caught by the CLAUDE_CODE_USE_* flags above (owner, 2026-06-08).
NUDGE_OFF_SUBSCRIPTION_ENV_PREFIXES = (
    "ANTHROPIC_DEFAULT_",
)


def _nudge_block_msg(name):
    return (f"{name} is set — your sessions could bill to a metered key instead "
            f"of your subscription. Clear it first, then arm “Nudge me”.")


def nudge_arm_block_reason(env=None):
    """Why 'Nudge me' must NOT arm right now — a short plain-English reason naming
    the offending variable — or None if it's clear to arm. F12 (owner, 2026-06-08 =
    refuse, not warn): Pitwall does not own the environment of a window the user
    launches, so it cannot guarantee the session stays on the subscription; it can
    only refuse to arm while a metered/off-subscription key is present."""
    env = os.environ if env is None else env

    def _is_set(v):
        return v is not None and str(v).strip() != ""

    for k in NUDGE_OFF_SUBSCRIPTION_ENV:
        if _is_set(env.get(k)):
            return _nudge_block_msg(k)
    for name, v in env.items():
        if _is_set(v) and name.startswith(NUDGE_OFF_SUBSCRIPTION_ENV_PREFIXES):
            return _nudge_block_msg(name)
    return None


def nudge_snooze(cfg, now=None, seconds=NUDGE_SNOOZE_SECONDS):
    """Silence taps for `seconds` from `now` (the user clicked 'Snooze 1h' on a tap).
    Mutates and returns cfg by setting cfg['nudge_snooze_until']. Does not arm/disarm
    — snooze is a temporary quiet window on top of an armed feature."""
    now = time.time() if now is None else now
    cfg["nudge_snooze_until"] = now + seconds
    return cfg


def nudge_decision(session, cfg, env=None, now=None):
    """Should 'Nudge me' actively tap the user about THIS active session now?
    Pure (no side effects, no I/O beyond reading `env` and the clock). Returns a dict
    {"headline", "detail", "ctx", "word"} to show, or None to stay quiet. Fires
    only when ALL hold:
      - the feature is armed (cfg['nudge_armed']) AND not env-blocked (F12) — a
        blocked state is treated as not-safely-armed, so no taps fire either way;
      - not currently snoozed (cfg['nudge_snooze_until'] is in the past);
      - the session's carried context is past the break-even floor — below it a
        fresh start costs MORE than it saves (the ~88k re-priming tax), so a tap
        would be bad advice;
      - the save-recommendation ladder has reached the configured nudge tier
        (default 'Save & start fresh').
    Pitwall still issues no command here — the dict is only advice the UI shows."""
    if not cfg.get("nudge_armed"):
        return None
    if nudge_arm_block_reason(env) is not None:     # F12: not safely armed → silent
        return None
    now = time.time() if now is None else now
    if now < (cfg.get("nudge_snooze_until") or 0):  # snoozed → silent
        return None
    ctx = session.get("ctx", 0) or 0
    floor = cfg.get("nudge_breakeven_tok", DEFAULTS["nudge_breakeven_tok"])
    if ctx < floor:
        return None
    ctx_red = cfg.get("ctx_red", DEFAULTS["ctx_red"]) or DEFAULTS["ctx_red"]
    word = save_reco(ctx / ctx_red)
    tier = cfg.get("nudge_tier", DEFAULTS["nudge_tier"])
    words = [w for _, w in SAVE_TIERS]
    try:
        if words.index(word) < words.index(tier):
            return None
    except ValueError:                              # unknown tier word → stay quiet
        return None
    return {
        "headline": "A fresh start would save here",
        "detail": (f"This chat re-reads ~{fmt_tokens(ctx)} tokens every turn. "
                   f"Type /pitstop, then /clear — the next session picks up where "
                   f"you left off."),
        "ctx": ctx,
        "word": word,
    }


def active_ceiling(cfg, learned=None):
    """The $ ceiling the allowance gauge measures against. Priority: the calibrated
    real ceiling (once trusted), then the chosen plan's rough ceiling, then the
    learned 'usual' (passed in, since computing it is a wide file scan). Returns
    (dollars, noun) or (None, None)."""
    cal = cfg.get("calibration") or {}
    if cfg.get("use_calibrated_ceiling") and cal.get("derived_ceiling"):
        return cal["derived_ceiling"], "calibrated"
    plan = cfg.get("plan")
    if plan in PLANS:
        return PLANS[plan]["usd"], PLANS[plan]["label"]
    if learned:
        return learned, "usual"
    return None, None


# --- AUTO real-usage injection (the off-screen `/usage` capture pipeline) --------------
# Pitwall can read the REAL Claude limit numbers straight off `claude /usage` — rendered
# off-screen so nothing ever flashes, at $0 token cost — and fold them into the SAME
# calibration + reset_override fields the manual Sync uses. So with one flag on, the
# displayed % and the countdown track Claude's own numbers automatically.
# The capture/OCR itself lives in scripts/capture_usage.ps1 (it needs Windows PowerShell
# 5.1 for the built-in WinRT OCR). This is the Python side: run it, validate the JSON,
# and apply it exactly like a manual Sync. The faces own scheduling + the on/off gate.
# Reads Claude's /usage panel as TEXT straight from the spawned session's console
# screen buffer (exact characters, no OCR -> no misread digits). The older OCR reader
# capture_usage.ps1 stays in scripts/ as a fallback. Both speak the same JSON contract.
CAPTURE_SCRIPT = os.path.join(HERE, "scripts", "read_usage.ps1")
_WINPS51 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                        "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def _valid_pct(v):
    """A percentage straight off the capture -> int 0..100, or None if missing/bad."""
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 100 else None


def capture_real_usage(timeout=70):
    """Shell out to the off-screen /usage capture and return its parsed JSON, e.g.
    {"ok":True,"session_pct":44,"session_reset":"11:10am","weekall_pct":72,
     "weekall_reset":"Jun 10, 5am","sonnet_pct":1} — or {"ok":False,"error":".."}.
    NEVER raises: every failure (missing script, timeout, bad JSON) comes back as
    ok=False so the scheduler just skips a cycle. Blocking (~15s) — call it off the
    UI thread. The reading also carries a "raw" field (the panel text) for the
    troubleshooting view. Invoked via Windows PowerShell 5.1 for parity."""
    if DEMO_READONLY:        # demo construct: never run the real /usage capture
        return {"ok": False, "error": "disabled in demo"}
    if not os.path.exists(CAPTURE_SCRIPT):
        return {"ok": False, "error": "capture script missing"}
    exe = _WINPS51 if os.path.exists(_WINPS51) else "powershell.exe"
    # CREATE_NO_WINDOW so the PowerShell host itself never flashes a console either.
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        p = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", CAPTURE_SCRIPT],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=flags)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "capture timed out"}
    except Exception as e:
        return {"ok": False, "error": "spawn failed: %s" % e}
    # The script prints exactly one JSON line to stdout; all diagnostics go to stderr.
    line = ""
    for ln in (p.stdout or "").splitlines():
        ln = ln.strip()
        if ln.startswith("{"):
            line = ln
    if not line:
        return {"ok": False, "error": "no JSON from capture (rc=%s)" % p.returncode}
    try:
        d = json.loads(line)
    except Exception:
        return {"ok": False, "error": "bad JSON from capture"}
    return d if isinstance(d, dict) else {"ok": False, "error": "capture JSON not an object"}


def inject_real_usage(cfg, capture=None):
    """Fold a real /usage reading into cfg's reset_override + calibration, exactly like
    the manual Sync — one code path, both faces. Pass an already-captured `capture`
    dict to reuse a reading (e.g. tests); otherwise it captures fresh (blocking ~15s).
    Mutates cfg in place and saves it on success. Returns a result dict:
        {"ok":True,"changed":True,"session_pct":44,"reset":"<iso>",
         "derived_ceiling":163.0,"weekall_pct":72,"sonnet_pct":1,"at":"<iso>"}
      or {"ok":False,"error":".."} — leaving cfg untouched on failure. Never raises."""
    d = capture if capture is not None else capture_real_usage()
    if not isinstance(d, dict) or not d.get("ok"):
        return {"ok": False, "error": (d or {}).get("error", "capture failed")}

    sp = _valid_pct(d.get("session_pct"))
    wa = _valid_pct(d.get("weekall_pct"))
    ws = _valid_pct(d.get("sonnet_pct"))
    if sp is None and wa is None and ws is None:
        return {"ok": False, "error": "no usable percentages in capture"}

    now_utc = datetime.now(timezone.utc)
    result = {"ok": True, "changed": False, "at": now_utc.isoformat(),
              "session_pct": sp, "weekall_pct": wa, "sonnet_pct": ws,
              "reset": None, "derived_ceiling": None}

    # 1) The session reset anchors the window + the countdown. "11:10am" -> UTC ISO,
    #    written to reset_override (the field _override_dt/effective_reset already read).
    wh = cfg.get("window_hours", 5)
    ov = None
    sr = (d.get("session_reset") or "").strip()
    if sr:
        res = parse_reset_input(sr, datetime.now().astimezone())
        if res not in (None, "CLEAR"):
            cand = res.astimezone(timezone.utc)
            # GUARD: a 5-hour window can't reset more than ~5h out. A garbled OCR read
            # ("4: lepm") that still parses, or a stale impossible time, lands outside
            # (now, now+wh] — reject it rather than pin an impossible countdown.
            if now_utc < cand <= now_utc + timedelta(hours=wh, minutes=2):
                ov = cand
                cfg["reset_override"] = ov.isoformat()
                result["reset"] = cfg["reset_override"]
                result["changed"] = True
    if ov is None:                              # fall back to an already-pinned reset
        ov = valid_override(cfg, wh, now_utc)   # honours the same sane-range guard
        if ov is None and cfg.get("reset_override"):
            cfg["reset_override"] = None         # drop a stale/impossible stored reset
            result["changed"] = True

    # 2) Calibration dict — the same shape + derivation the manual Sync writes, so
    #    active_ceiling() picks it up unchanged. Stamped source="auto" for the UI.
    cal = dict(cfg.get("calibration") or {})
    cal["at"] = now_utc.isoformat()
    cal["source"] = "auto"
    if sp is not None:
        # Store the REAL 5-hour % unconditionally — the display shows it directly
        # (like the weekly line), so it must land even when the reset OCR was garbled
        # and we had to reject it above (bug #2: the % is the truth, the ceiling is a
        # nice-to-have). The $-ceiling derivation needs a trustworthy reset to anchor
        # the window spend, so it stays gated on a valid `ov`.
        cal["session_pct"] = sp
        result["changed"] = True
        if ov is not None:
            # Measure this window's spend anchored to the REAL reset, then back out the
            # real ceiling ($ spent / fraction used) — identical to the manual path. The
            # reset is correctly pinned here, so this can't mis-derive off a guess.
            usd = 0.0
            try:
                usd = window_for_reset(collect_entries(wh), ov, wh).get("usd", 0.0)
            except Exception:
                usd = 0.0
            cal["session_usd"] = usd
            cal["derived_ceiling"] = (usd / (sp / 100.0)) if (usd > 0 and sp > 0) else None
            if cal["derived_ceiling"]:
                cfg["use_calibrated_ceiling"] = True   # syncing just FIXES the number
                result["derived_ceiling"] = cal["derived_ceiling"]
    if wa is not None:
        cal["weekly_all_pct"] = wa
        result["changed"] = True
    if ws is not None:
        cal["weekly_sonnet_pct"] = ws
        result["changed"] = True
    war = (d.get("weekall_reset") or "").strip()
    if war:
        cal["weekly_all_reset"] = war
    cfg["calibration"] = cal

    # Stamp the auto-sync heartbeat (the scheduler + UI read this).
    au = dict(cfg.get("auto_usage") or {})
    au["last_sync"] = now_utc.isoformat()
    au["last_ok"] = True
    cfg["auto_usage"] = au

    save_config(cfg)
    return result


def latest_activity_ts():
    """Newest transcript modification time (UTC datetime) across Claude's projects —
    Pitwall's cheap, parse-free 'is the CLI actually working' signal. Every assistant turn
    appends to its session .jsonl, so its mtime tracks real CLI activity; a mouse-bump or
    a 3am desk-knock writes nothing, so it can't false-trigger. The off-screen /usage
    capture writes no transcript either, so it can't self-trigger. None if nothing recent."""
    newest = 0.0
    for path in glob.glob(os.path.join(PROJECTS_ROOT, "**", "*.jsonl"), recursive=True):
        try:
            m = os.path.getmtime(path)
        except OSError:
            continue
        if m > newest:
            newest = m
    return datetime.fromtimestamp(newest, timezone.utc) if newest > 0 else None


class AutoUsageScheduler:
    """The brains of the auto-sync cadence — pure logic, no threading or GUI, so it's
    testable and both faces share ONE behaviour. The face calls tick() on its refresh
    timer with the newest Claude activity time; tick() returns a reason string when a
    capture should fire NOW (else None). The face then runs the (blocking) capture on a
    worker thread, calling mark_started()/mark_done() around it.

    Cadence (locked spec): sync on Pitwall startup → every interval_min while
    active → PAUSE while idle (idle_min with no CLI activity) → the instant real CLI
    work resumes, sync immediately and restart the interval clock → also sync on
    wake-from-sleep. Idle/resume keys off transcript writes (real work), never input."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._last_fire_mono = None    # monotonic of the last fire (interval base)
        self._last_tick_wall = None    # wall clock last tick — wake-from-sleep detection
        self._was_idle = False
        self.in_flight = False         # True while the face's worker thread is capturing
        self._active_family = None     # model family of the most-recently-active session

    def _au(self):
        return self.cfg.get("auto_usage") or {}

    def _enabled(self):
        return bool(self._au().get("enabled"))

    def _interval_s(self):
        au = self._au()
        user_set = au.get("interval_min_user_set") is True
        if not user_set and self._active_family == "fable":
            return 600   # 10-min default when Fable is live (burns faster)
        try:
            return max(60, int(au.get("interval_min", 30)) * 60)
        except (TypeError, ValueError):
            return 1800

    def _idle_s(self):
        try:
            return max(60, int(self._au().get("idle_min", 10)) * 60)
        except (TypeError, ValueError):
            return 600

    def mark_started(self):
        self.in_flight = True

    def mark_done(self):
        self.in_flight = False

    def tick(self, activity_ts, now_mono=None, now_wall=None, now_utc=None,
             active_family=None):
        """Decide whether to capture now. `activity_ts` = newest Claude activity (UTC
        datetime) or None. `active_family` = model family of the focused session (used
        to select the Fable 10-min default when no user interval is set). Returns
        'startup'|'wake'|'resume'|'interval' to fire, else None. Pure: the face owns
        actually launching the capture."""
        self._active_family = active_family
        if now_mono is None:
            now_mono = time.monotonic()
        if now_wall is None:
            now_wall = time.time()
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        prev_tick_wall = self._last_tick_wall
        self._last_tick_wall = now_wall
        if not self._enabled() or self.in_flight:
            return None

        # wake-from-sleep: a wall-clock jump between ticks far larger than any normal
        # refresh gap means the machine was suspended (OS-agnostic, no power hooks).
        woke = (prev_tick_wall is not None
                and (now_wall - prev_tick_wall) > max(90, self._idle_s()))

        idle = True
        if activity_ts is not None:
            idle = (now_utc - activity_ts).total_seconds() > self._idle_s()

        # 1) Pitwall startup (or first tick after enabling) → immediate sync.
        if self._last_fire_mono is None:
            self._was_idle = idle
            return self._fire(now_mono, "startup")
        # 2) machine woke from sleep → immediate sync.
        if woke:
            self._was_idle = idle
            return self._fire(now_mono, "wake")
        # 3) real CLI work resumed after idle → immediate sync, restart interval clock.
        if self._was_idle and not idle:
            self._was_idle = False
            return self._fire(now_mono, "resume")
        self._was_idle = idle
        # 4) idle → pause periodic captures (just keep watching for resume).
        if idle:
            return None
        # 5) active → fire on the interval.
        if (now_mono - self._last_fire_mono) >= self._interval_s():
            return self._fire(now_mono, "interval")
        return None

    def _fire(self, now_mono, reason):
        self._last_fire_mono = now_mono
        return reason


def drift_offset_pts(cfg, drift=None):
    """Percentage-POINTS to subtract from the raw allowance %, per the Settings
    drift-correction choice. Positive = the engine reads HIGH (shave it down). 0 when
    off or nothing learned/typed. `drift` is a drift_summary() dict (for 'auto')."""
    da = cfg.get("drift_adjust") or {}
    mode = da.get("mode", "off")
    if mode == "manual":
        try:
            return float(da.get("manual_pct") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    if mode == "auto":
        mp = (drift or {}).get("med_pct")
        if mp is not None:
            return float(mp)
    return 0.0


def corrected_used(cfg, usd, ceiling, drift=None):
    """(frac, pct, adjusted?) for the allowance gauge. Drift Correction was removed
    2026-06-08 (Sync is the single source of truth), so this is now the RAW
    spend÷ceiling number and `adjusted` is always False. `drift` is still accepted but
    ignored, so existing callers don't break."""
    if not ceiling or ceiling <= 0:
        return 0.0, 0, False
    pct = max(0.0, min(100.0, usd / ceiling * 100.0))
    return pct / 100.0, int(round(pct)), False


# ===================================================== pitstop (save & restart ritual)
# The pitstop toolchain is per-machine: CLI hooks and scripts keep their state under
# ~/.claude/pitstop (resume files, launch options, handoff outcome logs). Pitwall
# only READS that state and WRITES the two small config files — it never runs the
# ritual itself. On a machine without that directory every helper degrades gracefully
# and the faces hide the whole section.

PITSTOP_DIR = os.path.join(os.path.expanduser("~"), ".claude", "pitstop")
PITSTOP_CONFIG = os.path.join(PITSTOP_DIR, "pitstop_config.json")
PITSTOP_NUDGE_CONFIG = os.path.join(PITSTOP_DIR, "nudge_config.json")
PITSTOP_THRESHOLD_DEFAULT = 3_000_000
PITSTOP_THRESHOLD_MIN = 100_000      # same sanity bounds as the nudge hook itself —
PITSTOP_THRESHOLD_MAX = 50_000_000   # it ignores anything outside them

# plain-English versions of the handoff watcher's outcome words (closer_*.last.json)
PITSTOP_OUTCOME_TEXT = {
    "closed": "old window closed cleanly",
    "close-requested": "close sent — the window showed its own confirm",
    "timeout": "no confirmation in time — old window left open",
    "refused": "safety check failed — old window left open",
    "superseded": "a newer pitstop took over this track",
    "window-gone": "old window was already closed",
    "dry-run": "dry run — all checks passed, nothing closed",
    "error": "watcher error — old window left open",
}


def pitstop_available():
    return os.path.isdir(PITSTOP_DIR)


def load_pitstop_config():
    """{'remote_control': bool, 'auto_mode': bool, 'full_auto': bool} — the launch
    options for the new CLI window a pitstop opens. Missing/unreadable file -> all off
    (safe default, mirrors the launcher's own fallback)."""
    try:
        with open(PITSTOP_CONFIG, encoding="utf-8") as fh:
            raw = json.load(fh)
        # all three strictly `is True` (security review 2026-06-10; LOW-2 closed
        # 2026-06-12): a hand-edited truthy string like "false" must read as OFF —
        # same rule as the launcher. save_pitstop_config always writes real bools.
        return {"remote_control": raw.get("remote_control") is True,
                "auto_mode": raw.get("auto_mode") is True,
                "full_auto": raw.get("full_auto") is True}
    except Exception:
        return {"remote_control": False, "auto_mode": False, "full_auto": False}


def save_pitstop_config(remote_control, auto_mode, full_auto=False):
    """Write the three launch options, preserving any other keys already in the file
    (the CLI-side switches own the same file — last write wins, never clobber)."""
    if DEMO_READONLY:        # demo construct: never write the user's real state
        return
    try:
        with open(PITSTOP_CONFIG, encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            raw = {}
    except Exception:
        raw = {}
    raw["remote_control"] = bool(remote_control)
    raw["auto_mode"] = bool(auto_mode)
    raw["full_auto"] = bool(full_auto)
    os.makedirs(PITSTOP_DIR, exist_ok=True)
    with open(PITSTOP_CONFIG, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, indent=2)


def push_quiet_state(now=None):
    """The momentary mute (the header bell): is everything quiet right now, and why.
    Reads pitstop_config.json's push block: 'quiet_until' (epoch seconds — strict
    number, bools excluded, same parse discipline as the launch booleans) and
    'quiet_until_pitstop' (strict `is True`; the handoff confirm step clears it, so
    "until next pitstop" really ends at the next pitstop). The nudge hook honors the
    same two keys before composing phone pushes; the faces honor them before showing
    the tap. Returns (active, label): (False, "") when not muted; label is the
    plain-English remainder ("12m left" / "until next pitstop"). Missing/unreadable
    file -> not muted (a mute must never be the stuck-on default). Two staleness
    guards (security review 2026-06-12 LOW-1/LOW-2): a non-finite quiet_until
    (hand-edited json Infinity) is ignored, and so is one more than 24h out —
    the UI writes at most 1h, so a longer value is a hand edit, and honoring it
    even partially would re-extend on every poll into a mute that never ends
    (a min()-style cap is rolling: re-capped from each call's `now`, it stays
    in the future forever). "Until next pitstop" is honored only while the
    pitstop Stop hook is actually registered — with the hook gone no pitstop
    can ever confirm-clear the flag, so the mute would otherwise be invisible
    and permanent."""
    now = time.time() if now is None else now
    try:
        with open(PITSTOP_CONFIG, encoding="utf-8") as fh:
            push = (json.load(fh).get("push") or {})
    except Exception:
        return False, ""
    if push.get("quiet_until_pitstop") is True and pitstop_nudge_armed():
        return True, "until next pitstop"
    qu = push.get("quiet_until")
    if (isinstance(qu, (int, float)) and not isinstance(qu, bool)
            and math.isfinite(qu) and now < qu <= now + 86400.0):
        mins = max(1, int(qu - now + 59) // 60)
        return True, "%dm left" % mins
    return False, ""


def set_push_quiet(seconds=None, until_pitstop=False, now=None):
    """Write the mute the bell menu picked. seconds=N -> quiet for N seconds;
    until_pitstop=True -> quiet until the next handoff confirm clears it; both
    falsy -> unmute now. Touches ONLY the push block's two quiet keys — everything
    else in pitstop_config.json (remote_control / auto_mode / full_auto, the
    per-push switches and their messages) is preserved exactly, the
    save_pitstop_config rule. A file that exists but will not parse is NEVER
    rebuilt from scratch (security review 2026-06-12 MEDIUM-1): the write is
    refused and False returned — the mute simply doesn't land, which is the
    fail-toward-unmuted direction — rather than replacing Kevin's launch
    switches and push messages with a quiet-keys-only skeleton. The write goes
    through a temp file + os.replace so a torn write can never leave half a
    config behind."""
    if DEMO_READONLY:        # demo construct: never write the user's real state
        return False
    now = time.time() if now is None else now
    try:
        with open(PITSTOP_CONFIG, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        raw = {}
    except Exception:
        return False
    if not isinstance(raw, dict):
        return False
    push = raw.get("push")
    if not isinstance(push, dict):
        push = {}
        raw["push"] = push
    push["quiet_until"] = (now + seconds) if seconds else 0
    push["quiet_until_pitstop"] = bool(until_pitstop)
    os.makedirs(PITSTOP_DIR, exist_ok=True)
    tmp = "%s.%d.tmp" % (PITSTOP_CONFIG, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, indent=2)
    os.replace(tmp, PITSTOP_CONFIG)
    return True


def pitstop_threshold():
    """Where the CLI's token nudge first fires (it re-fires each +1M after). Mirrors
    the nudge hook exactly: unreadable/out-of-range config -> the 3M default."""
    try:
        with open(PITSTOP_NUDGE_CONFIG, encoding="utf-8") as fh:
            t = int(json.load(fh)["threshold_tokens"])
        if PITSTOP_THRESHOLD_MIN <= t <= PITSTOP_THRESHOLD_MAX:
            return t
        return PITSTOP_THRESHOLD_DEFAULT
    except Exception:
        return PITSTOP_THRESHOLD_DEFAULT


def save_pitstop_threshold(tokens):
    """Write the nudge firing point; False if outside the hook's sanity bounds (the
    hook would silently ignore a bad value, so the UI must not pretend it took)."""
    if DEMO_READONLY:        # demo construct: never write the user's real state
        return False
    if not isinstance(tokens, int) or not (
            PITSTOP_THRESHOLD_MIN <= tokens <= PITSTOP_THRESHOLD_MAX):
        return False
    try:
        with open(PITSTOP_NUDGE_CONFIG, encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            raw = {}
    except Exception:
        raw = {}
    raw["threshold_tokens"] = tokens
    os.makedirs(PITSTOP_DIR, exist_ok=True)
    with open(PITSTOP_NUDGE_CONFIG, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, indent=2)
    return True


def parse_token_amount(text):
    """'3M' / '2.5M' / '300k' / '3500000' -> int tokens, or None if unreadable."""
    s = (text or "").strip().lower().replace(",", "").replace(" ", "")
    if not s:
        return None
    try:
        if s.endswith("m"):
            return int(float(s[:-1]) * 1_000_000)
        if s.endswith("k"):
            return int(float(s[:-1]) * 1_000)
        return int(float(s))
    except ValueError:
        return None


def fmt_token_amount(n):
    """3_000_000 -> '3M', 2_500_000 -> '2.5M', 300_000 -> '300k'."""
    if n >= 1_000_000:
        m = n / 1_000_000
        if m == int(m):
            return "%dM" % int(m)
        return ("%.2f" % m).rstrip("0").rstrip(".") + "M"
    if n >= 1_000 and n % 1_000 == 0:
        return "%dk" % (n // 1_000)
    return str(n)


def pitstop_last_handoffs(limit=3):
    """Most recent auto-handoff outcomes, newest first, from the closer_*.last.json
    files the handoff watcher writes: [{'track','outcome','text','when'}]."""
    rows = []
    try:
        names = os.listdir(PITSTOP_DIR)
    except OSError:
        return rows
    for name in names:
        if not (name.startswith("closer_") and name.endswith(".last.json")):
            continue
        try:
            with open(os.path.join(PITSTOP_DIR, name), encoding="utf-8") as fh:
                d = json.load(fh)
            outcome = d.get("outcome", "?")
            rows.append({"track": d.get("track") or name[len("closer_"):-len(".last.json")],
                         "outcome": outcome,
                         "text": PITSTOP_OUTCOME_TEXT.get(outcome, d.get("detail", "")),
                         "when": d.get("when", "")})
        except Exception:
            continue
    rows.sort(key=lambda r: r["when"], reverse=True)
    return rows[:limit]


# --- the pitstop verification pill (owner's order, 2026-06-12) -----------------
# At 16M tokens the owner asked "why hasn't pitstop fired" — it HAD: the 5M mark
# was crossed mid-turn (the watch only runs between turns), and the phone push was
# suppressed because his terminal had focus. The pill closes that visibility gap
# by reporting the toolchain's ACTUAL state, read from the same files the nudge
# hook (C:/dev/tools/pitstop_nudge.py) reads and writes — never a guess.

CLAUDE_SETTINGS = os.path.expanduser("~/.claude/settings.json")
# pitstop_nudge.py drops one marker per session in the OS temp dir:
#   pitstop_nudge_lvl_<sid>.txt       — last fired nudge level (int, 0 = never)
#   pitstop_nudge_workeroff_<sid>.txt — watch suppressed: dispatched worker
NUDGE_MARKER_DIR = tempfile.gettempdir()


def pitstop_nudge_armed():
    """True when a Stop hook running pitstop_nudge.py is registered in Claude
    Code's settings — the thing that actually fires pitstops. Hook registration
    is the proof; the pitstop folder existing only means the toolchain was
    installed once."""
    try:
        with open(CLAUDE_SETTINGS, encoding="utf-8") as fh:
            stops = (json.load(fh).get("hooks") or {}).get("Stop") or []
    except Exception:
        return False
    for entry in stops:
        if not isinstance(entry, dict):
            continue
        for h in entry.get("hooks") or []:
            if not isinstance(h, dict):
                continue
            blob = " ".join([str(h.get("command", ""))]
                            + [str(a) for a in (h.get("args") or [])])
            if "pitstop_nudge" in blob:
                return True
    return False


def _nudge_tier(sid):
    """Last nudge level pitstop_nudge.py recorded for this session (0 = never)."""
    try:
        with open(os.path.join(NUDGE_MARKER_DIR,
                               "pitstop_nudge_lvl_%s.txt" % sid)) as fh:
            return int(fh.read().strip() or "0")
    except (OSError, ValueError):
        return 0


def _pill_over(over):
    """Compact 'how far past the mark' chip text: '400k over', '1.5M over'."""
    if over >= 1_000_000:
        s = "%.1f" % (over / 1e6)
        if s.endswith(".0"):
            s = s[:-2]
        return s + "M over"
    return fmt_tokens(over) + " over"


def _pill_legend(thr_s):
    """The every-state legend appended to each pill tooltip (owner's ask,
    2026-06-12: 'a hover that brings up an explanation of all the options')."""
    return (
        "\n\nEverything this pill can say:\n"
        "Pitstop auto · %s — armed; at the mark it saves and restarts itself\n"
        "Pitstop on · %s — armed; at the mark it offers a pitstop and waits "
        "for your OK\n"
        "Pitstop due · 1M over — passed the mark mid-task; there's been no "
        "clean break in the work yet, so it fires when the current step ends\n"
        "Pitstop fired · 1M over — fired automatically: the session saves a "
        "checkpoint and restarts itself\n"
        "Pitstop offered · 1M over — an offer waits for your OK in that "
        "session's window\n"
        "Pitstop off · worker — dispatched workers report and stop; they never "
        "restart\n"
        "Pitstop hook missing — NOT armed; nothing fires until the hook is "
        "re-added" % (thr_s, thr_s))


def pitstop_watch(s):
    """Verification state of the pitstop token-watch for ONE session — powers the
    faces' pitstop pill and the Session-details 'Pitstop watch' row. Returns None
    when the toolchain isn't on this machine or the session isn't open; else:
      state   'armed' | 'due' | 'fired' | 'off' | 'unarmed'
      label   short chip text       tip    full plain-English explanation,
                                           ending in the all-states legend
      line    one-line details value
      threshold / tier / full_auto  the raw values behind the words
    """
    if not pitstop_available() or not s.get("open"):
        return None
    sid = s.get("sid") or ""
    thr = pitstop_threshold()
    thr_s = fmt_token_amount(thr)
    full_auto = load_pitstop_config()["full_auto"]
    fa = "on" if full_auto else "off"
    tier = _nudge_tier(sid)
    tok = s.get("tok", 0) or 0
    # The seam (owner, 2026-06-12): a clean break between tasks — the only
    # moment a pitstop can fire. Claude's own registry says when a session is
    # mid-task (status "busy"); any other open state is between tasks.
    # Below the pitstop mark the seam question isn't live yet — instead of
    # noise the chip says when it will be (owner: "can you say Seam in xxM"):
    # the distance to the mark, where a due pitstop fires at the next break.
    seam = s.get("status") != "busy"
    seam_upcoming = tok < thr
    if seam_upcoming:
        left = thr - tok
        if left >= 1_000_000:
            _ls = ("%.1f" % (left / 1e6)).rstrip("0").rstrip(".") + "M"
        else:
            _ls = fmt_tokens(left)
        seam_label = "Seam in " + _ls
        seam_tip = ("A seam is a clean break between tasks — the only moment "
                    "a pitstop can fire. This chat is about " + _ls + " tokens "
                    "short of the " + thr_s + " pitstop mark: when it crosses "
                    "the mark, a pitstop comes due and fires at the next "
                    "clean break.")
    elif seam:
        seam_label = "Seam available"
        seam_tip = ("A seam is a clean break between tasks — the only moment "
                    "a pitstop can fire. This session is at a seam right now: "
                    "it's between tasks, so a due pitstop fires straight away.")
    else:
        seam_label = "No seam · looking"
        seam_tip = ("A seam is a clean break between tasks — the only moment "
                    "a pitstop can fire. This session is actively working, so "
                    "there's no seam yet — Pitwall is looking for one: a due "
                    "pitstop fires at the next clean break. A long step can "
                    "run well past the mark — that's normal.")
    base = {"threshold": thr, "tier": tier, "full_auto": full_auto,
            "seam": seam, "seam_upcoming": seam_upcoming,
            "seam_label": seam_label, "seam_tip": seam_tip}

    def _done(d):
        d["tip"] += _pill_legend(thr_s)
        return d

    if not pitstop_nudge_armed():
        return _done(dict(
            base, state="unarmed", label="Pitstop hook missing",
            tip=("Pitstop can NOT fire: the token-watch hook isn't registered in "
                 "Claude Code's settings file (%s). Sessions will sail past the "
                 "%s mark with no nudge. Re-add the pitstop_nudge.py Stop hook "
                 "to fix it." % (CLAUDE_SETTINGS, thr_s)),
            line=("NOT ARMED — the token-watch hook is missing from %s; nothing "
                  "fires at %s" % (CLAUDE_SETTINGS, thr_s))))

    if os.path.exists(os.path.join(NUDGE_MARKER_DIR,
                                   "pitstop_nudge_workeroff_%s.txt" % sid)):
        return _done(dict(
            base, state="off", label="Pitstop off · worker",
            tip=("The pitstop watch is off for this session: it's a dispatched "
                 "worker. Workers finish their task, write a report, and stop — "
                 "they never restart themselves."),
            line="off — dispatched worker; workers report and stop, never pitstop"))

    # How far past the mark, for the chip (owner, 2026-06-12: "add to the pill
    # 1M over, 2M over etc when that happens").
    over = tok - thr
    over_s = _pill_over(over) if over > 0 else ""

    if tier >= 1:
        if full_auto:
            return _done(dict(
                base, state="fired",
                label=("Pitstop fired · %s" % over_s if over_s
                       else "Pitstop fired · auto"),
                tip=("This session passed the %s-token mark%s and the pitstop "
                     "fired: the session was told to save a checkpoint and "
                     "restart itself in a fresh window, no questions asked "
                     "(Full auto is on in Settings → Pitstop). It re-fires once "
                     "per extra 1M tokens; this session is on nudge %d."
                     % (thr_s, " (it's %s now)" % over_s if over_s else "",
                        tier)),
                line=("FIRED — passed %s%s; full-auto handoff instructed "
                      "(nudge %d — re-fires each extra 1M)"
                      % (thr_s, ", " + over_s if over_s else "", tier))))
        return _done(dict(
            base, state="fired",
            label=("Pitstop offered · %s" % over_s if over_s
                   else "Pitstop offered"),
            tip=("This session passed the %s-token mark%s and was told to offer "
                 "you a pitstop — look for the offer in that session's window. "
                 "Nothing restarts without your OK (Full auto is off). It "
                 "re-offers once per extra 1M tokens; this session is on "
                 "nudge %d."
                 % (thr_s, " (it's %s now)" % over_s if over_s else "", tier)),
            line=("FIRED — passed %s%s; pitstop offered in the session window "
                  "(nudge %d)"
                  % (thr_s, ", " + over_s if over_s else "", tier))))

    if tok >= thr:
        # Due heats like the rest of the heat family (owner, 2026-06-12: "tune
        # the due pill to the other heat colors"): amber the moment the mark is
        # crossed, deepening to full red by half-a-threshold of overage.
        due_heat = 0.5 + 0.5 * min(1.0, max(0.0, over) / (thr * 0.5))
        return _done(dict(
            base, state="due", due_heat=due_heat,
            label=("Pitstop due · %s" % over_s if over_s else "Pitstop due"),
            tip=("This session passed the %s-token mark%s in the middle of a "
                 "task. There's been no clean break in the work yet — the "
                 "watch can only fire between tasks, so the pitstop %s the "
                 "moment the session finishes its current step. A long step "
                 "can run well past the mark — that's normal."
                 % (thr_s, " (it's %s now)" % over_s if over_s else "",
                    "runs itself" if full_auto
                    else "offer appears in that session's window")),
            line=("due — passed %s mid-task%s, no clean break yet; fires when "
                  "the current step ends (Full auto %s)"
                  % (thr_s, " (" + over_s + ")" if over_s else "", fa))))

    tok_s = fmt_tokens(tok) if tok else "0"
    if full_auto:
        tip = ("Pitstop is watching this session. When it passes %s tokens "
               "(it's at %s now), the session saves a checkpoint and restarts "
               "itself in a fresh window automatically — Full auto is on "
               "(Settings → Pitstop)." % (thr_s, tok_s))
    else:
        tip = ("Pitstop is watching this session. When it passes %s tokens "
               "(it's at %s now), the session stops and offers you a pitstop — "
               "a save-and-restart into a fresh window. Nothing happens without "
               "your OK (Full auto is off)." % (thr_s, tok_s))
    # Label carries the switch (owner, 2026-06-12: full-auto is his default and
    # the chip said only "on" — auto-ness was buried in the tooltip).
    return _done(dict(
        base, state="armed",
        label="Pitstop %s · %s" % ("auto" if full_auto else "on", thr_s),
        tip=tip,
        line=("on — fires at %s (this session ~%s) · Full auto %s"
              % (thr_s, tok_s, fa))))


def displayed_session_pct(cfg, usd, ceiling, drift=None):
    """The 5-hour-window % to DISPLAY on the bar + projection line. Prefers the REAL
    captured session % from the last Sync (calibration.session_pct) — the authoritative
    number, exactly as the weekly line shows its synced %. Falls back to the local
    spend÷ceiling ESTIMATE (with the user's drift correction) only when nothing has ever
    been synced. Pitwall reads only THIS machine's CLI transcripts, so the estimate can't
    see Desktop / web / other-machine usage and can never match Claude's own 5-hour %;
    the captured number can. Returns (frac, pct, adjusted, synced): frac 0..1 for the
    bar, pct int for the label, adjusted=True when a drift correction was applied
    (estimate path only), synced=True when the real captured number is being shown."""
    sp = (cfg.get("calibration") or {}).get("session_pct")
    if sp is not None:
        try:
            real = max(0.0, min(100.0, float(sp)))
            return real / 100.0, int(round(real)), False, True
        except (TypeError, ValueError):
            pass
    frac, pct, adjusted = corrected_used(cfg, usd, ceiling, drift)
    return frac, pct, adjusted, False


def last_synced_clock(cfg):
    """Local clock time of the last real Sync, e.g. '11:40 AM' — for the MAIN face's
    'as of HH:MM' freshness stamp. None if never synced. (Settings shows the relative
    'X ago' via age_str; the main face shows the absolute time so a glance tells you how
    fresh the synced number is without doing the subtraction.)"""
    ls = (cfg.get("auto_usage") or {}).get("last_sync")
    if not ls:
        return None
    t = parse_ts(ls)
    if not t:
        return None
    # %I is zero-padded (09:40); strip the leading zero and lowercase the meridiem,
    # no space, for a compact "2:17pm" (owner, 2026-06-08).
    return t.astimezone().strftime("%I:%M%p").lstrip("0").lower()


def pace_state(cfg, d, pace, ceiling):
    """(colour, sustainable $/hr) for the pace dot. Judges whether you're PROJECTED
    to exhaust the window before it resets (not just instantaneous pace, which is
    noisy just after a reset), damping the alarm when little is used / little time has
    elapsed. Falls back to fixed $/hr thresholds when no ceiling is known."""
    wh = cfg.get("window_hours", 5)
    if not ceiling or not wh:
        if pace < cfg.get("pace_amber", DEFAULTS["pace_amber"]):
            return GREEN, None
        if pace < cfg.get("pace_red", DEFAULTS["pace_red"]):
            return AMBER, None
        return RED, None
    sustainable = ceiling / wh
    now = datetime.now(timezone.utc)
    hrs_to_reset = (max((d["reset"] - now).total_seconds() / 3600, 0)
                    if d.get("reset") else wh)
    elapsed = max(wh - hrs_to_reset, 0)
    projected = d.get("usd", 0.0) + pace * hrs_to_reset
    ratio = projected / ceiling if ceiling else 0
    if d.get("usd", 0.0) / ceiling < 0.25:
        ratio = min(ratio, 0.99)
    elif elapsed < 0.5:
        ratio = min(ratio, 1.05)
    return (GREEN if ratio < 1.0 else AMBER if ratio < 1.3 else RED), sustainable


def projection_text(cfg, d, pace, ceiling, drift=None, on_track_tail=True):
    """(frac, sentence, colour) for the allowance projection line. Leads with the % of
    the 5-hour window used — the REAL synced number when one's been captured, else the
    local spend÷ceiling estimate — then a plain-English pace status. The % answers
    'where am I now'; the pace clause answers 'where am I headed at this burn' (Pitwall's
    only forward-looking signal, always from LOCAL spend). Colour is one of
    GREEN/AMBER/RED/MUT/FAINT for the caller to apply.
    on_track_tail=False drops the reassuring '· on track to last until it resets'
    clause (the line is just the %); warning clauses (warm / may run dry / used up /
    paused) always stay — they're signals, not filler. (Qt face, owner call 2026-06-11.)"""
    frac, pct, adjusted, synced = displayed_session_pct(
        cfg, d.get("usd", 0.0), ceiling, drift)
    if not synced and (not ceiling or ceiling <= 0):
        return 0.0, "tap to set your plan limit", FAINT
    used = f"{pct}% of your 5-hour limit used"
    # No ceiling but synced ⇒ we can show the real % but can't forecast pace.
    if not ceiling or ceiling <= 0:
        return frac, used, MUT
    remaining = ceiling - d["usd"]
    if remaining <= 0:
        return frac, f"{used} · used up, wait for the reset", RED
    if pace <= 0:
        return frac, f"{used} · paused", MUT
    color, _ = pace_state(cfg, d, pace, ceiling)
    if color == GREEN:
        if not on_track_tail:
            return frac, used, MUT
        return frac, f"{used} · on track to last until it resets", MUT
    if color == AMBER:
        return frac, f"{used} · running a bit warm", AMBER
    now = datetime.now(timezone.utc)
    hrs_to_cap = remaining / pace
    hrs_to_reset = (max((d["reset"] - now).total_seconds() / 3600, 0)
                    if d.get("reset") else None)
    gap = (hrs_to_reset - hrs_to_cap) if hrs_to_reset is not None else None
    if gap and gap > 1 / 12:
        return frac, f"{used} · may run dry ~{fmt_dur(gap)} before it resets", RED
    if not on_track_tail:
        return frac, used, MUT
    return frac, f"{used} · on track to last until it resets", MUT


def weekly_line(cfg):
    """The weekly-allowance line for the card, parallel to the 5-hour line:
    '63% of your weekly limit used · resets Wed 5am', or None when the user
    hasn't synced a weekly % yet (then the card shows no weekly line).
    Shared by both faces so the wording can't drift."""
    cal = (cfg.get("calibration") or {})
    wp = cal.get("weekly_all_pct")
    if wp is None:
        return None
    reset = cal.get("weekly_all_reset")
    tail = f" · resets {reset}" if reset else ""
    return f"{wp:.0f}% of your weekly limit used{tail}"


def session_state_word(s, now):
    """The right-hand state label for a session row: closed / busy / idle / now /
    Nm / Nh (minutes/hours since last activity). `now` is an aware UTC datetime."""
    if not s.get("open"):
        return "closed"
    if s.get("status") == "busy":
        return "busy"
    if s.get("last") is None:
        return "idle"
    secs = (now - s["last"]).total_seconds()
    if secs < 60:
        return "now"
    if secs < 3600:
        return f"{int(secs / 60)}m"
    return f"{secs / 3600:.0f}h"


# --- single-instance lock (shared infra; the visible popup is per-face) ------
# One name, used by BOTH the owning lock (acquire_single_instance_lock) and the
# non-owning probe (another_instance_running) so they can never drift apart.
# This name is the permanent single-instance identity, set at the clean-break before
# the first (0.9) release. It must NEVER change AFTER 0.9 ships — an old and a new copy
# carrying different mutex names could run side by side during an upgrade. (Renamed from
# the old codename mutex here, pre-release, precisely because this is the last safe window.)
SINGLE_INSTANCE_MUTEX = "PitwallSingleInstance"
# The demo construct runs as a SECOND instance with its own identity, so it coexists
# beside the real widget (its own single-instance guard still blocks a second demo).
DEMO_INSTANCE_MUTEX = "PitwallDemoInstance"


def _pid_alive_posix(pid):
    """True if `pid` names a live process (POSIX). Used by the lockfile fallback."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, just not ours
    except OSError:
        return False
    return True


def acquire_single_instance_lock(name=SINGLE_INSTANCE_MUTEX):
    """Make sure only ONE Pitwall instance runs at a time. Returns a lock handle to
    keep alive for the life of the process, or None if another instance already
    holds it.

    `name` selects the lock identity. The real widget uses the default; the demo
    construct passes DEMO_INSTANCE_MUTEX so it has its OWN lock and can coexist
    beside the real widget (one of each, never two of either).

    Windows: a session-local named mutex — the OS releases it automatically when
    the process exits, so there are no stale locks to clean up. Other platforms:
    a PID lockfile beside the config, validated against a live process. A guard
    that errors must NEVER block startup, so failures return a truthy sentinel."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            # use_last_error=True snapshots the Win32 last-error the instant CreateMutexW
            # returns, into ctypes thread-local storage. Reading it via a SEPARATE
            # kernel32.GetLastError() call (as before) was racy: ctypes does not preserve
            # the system last-error across foreign calls, so ERROR_ALREADY_EXISTS could be
            # clobbered to 0 before we read it — the guard then missed the running copy and
            # a SECOND widget launched. (Fixed 2026-06-06.)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            ERROR_ALREADY_EXISTS = 183
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                              wintypes.LPCWSTR]
            handle = kernel32.CreateMutexW(None, False, name)
            already = (ctypes.get_last_error() == ERROR_ALREADY_EXISTS)
            if handle and already:
                return None
            return handle or True
        except Exception:
            return True
    # POSIX fallback: PID lockfile (per-identity so the demo lock can't collide
    # with the real one)
    suffix = "" if name == SINGLE_INSTANCE_MUTEX else "." + name
    lock_path = os.path.join(HERE, ".pitwall%s.lock" % suffix)
    try:
        if os.path.exists(lock_path):
            with open(lock_path, encoding="utf-8") as fh:
                old = fh.read().strip()
            if old.isdigit() and _pid_alive_posix(int(old)):
                return None
        with open(lock_path, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        return lock_path
    except Exception:
        return True


def another_instance_running():
    """Non-owning check: is a Pitwall instance already running?

    Unlike acquire_single_instance_lock this NEVER creates or holds the lock, so
    a launcher can call it and still let the real face acquire the lock a moment
    later. Best-effort: on any error it returns False, because a guard that errors
    must never block a legitimate launch (the per-face owning lock is the ultimate
    backstop against a true double-start race)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            SYNCHRONIZE = 0x00100000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenMutexW.restype = wintypes.HANDLE
            kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                            wintypes.LPCWSTR]
            # OpenMutex succeeds only if the named mutex already exists, i.e. a
            # face is running. We close the handle immediately so we never own it.
            handle = kernel32.OpenMutexW(SYNCHRONIZE, False, SINGLE_INSTANCE_MUTEX)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    # POSIX: a live PID lockfile beside the config means an instance is up.
    lock_path = os.path.join(HERE, ".pitwall.lock")
    try:
        if os.path.exists(lock_path):
            with open(lock_path, encoding="utf-8") as fh:
                old = fh.read().strip()
            return old.isdigit() and _pid_alive_posix(int(old))
    except Exception:
        pass
    return False
