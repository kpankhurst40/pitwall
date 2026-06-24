#!/usr/bin/env python3
r"""
boot_restore.py — Pitwall's "reopen my Claude sessions after a Windows reboot".

WORKSPACE-SPECIFIC, PRIVATE MODULE. This file knows about Kevin's C:\dev layout
(The Grid lives at C:\dev\the-grid\src) so it is intentionally kept OUT of the public
claude-token-steward product. pitwall_qt imports it LAZILY and silently no-ops if it
is absent — so an OSS build without this file simply doesn't have the feature, and
nothing breaks. ts_core stays generic; only this module is workspace-coupled.

THE FLOW (decided with Kevin 2026-06-24, see docs/plans/boot-restore-and-autostart-plan.md)
------------------------------------------------------------------------------------------
Pitwall (always-on) writes a snapshot of the open sessions every poll
(ts.record_open_sessions). On the FIRST Pitwall start after an OS reboot, if the user
has "Reopen my Claude sessions on boot" on, this module:

  1. claims the boot (once-per-boot marker) so reopening Pitwall mid-day never re-fires;
  2. ensures The Grid (the cockpit) is visible — launches it if not already running;
  3. fires `--resume <id>` for EVERY recorded session, with NO cap (Kevin's call),
     reusing The Grid's reviewed launcher grid_launch.ps1 (Heavy mode).

SAFETY (the auto-spawn-at-login path — Ivan-reviewed)
-----------------------------------------------------
  * The snapshot is INERT DATA (work_dir + session_id). We never read or execute any
    field as code; the session id is UUID-validated before use; args are passed to the
    launcher as a LIST (no shell, nothing injectable).
  * We only ever use Heavy / `--resume`, which REOPENS a conversation and waits — it
    does NOT auto-run a prompt (only the Light path injects a fixed prime). So a
    restored session is idle-until-the-user-types, even though grid_launch.ps1 runs at
    bypassPermissions (Kevin's standing posture for Grid-launched CLIs).
  * DEDUPE GUARD: a session that is ALREADY running is skipped (never opens a
    duplicate), which also makes the boot-detection edge cases harmless.
  * Best-effort throughout: any failure logs to restore.log and is swallowed — a boot
    restore must never crash or hang the widget. The actual launches run off the GUI
    thread (see pitwall_qt._kick_boot_restore).
"""

from __future__ import annotations

import os
import re
import json
import time
import subprocess
from datetime import datetime, timezone

import ts_core as ts

# The Grid — Kevin's workspace cockpit + the reviewed launch engine we reuse.
GRID_SRC = r"C:\dev\the-grid\src"
GRID_LAUNCH_PS1 = os.path.join(GRID_SRC, "grid_launch.ps1")
GRID_APP = os.path.join(GRID_SRC, "grid_app.py")

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
_BOOT_MARKER = os.path.join(ts._pitwall_runtime_dir(), "boot_marker.json")
_LOG = os.path.join(ts._pitwall_runtime_dir(), "restore.log")


def available() -> bool:
    """True if this machine has The Grid's launcher where we expect it. pitwall_qt
    uses this to decide whether to even SHOW the 'reopen on boot' toggle, so an OSS
    build (or a machine without The Grid) doesn't advertise a dead feature."""
    return os.path.isfile(GRID_LAUNCH_PS1)


def _log(msg: str) -> None:
    """Append a timestamped line to restore.log (self-diagnosing; never raises)."""
    try:
        os.makedirs(ts._pitwall_runtime_dir(), exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp}  {msg}\n")
    except Exception:
        pass


# --- once-per-boot gate ------------------------------------------------------
def _system_boot_epoch() -> int | None:
    """Unix time the machine last booted, from GetTickCount64 (uptime). No admin,
    no deps. None off-Windows / on any failure (caller then fails safe)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.GetTickCount64.restype = ctypes.c_ulonglong
        uptime_s = k.GetTickCount64() / 1000.0
        return int(time.time() - uptime_s)
    except Exception:
        return None


def claim_boot_restore() -> bool:
    """Return True at most ONCE per OS boot, stamping a marker so a later Pitwall
    restart within the same boot returns False. Fails safe to False (no restore) if
    the boot time can't be read — better to skip than to re-spawn windows."""
    cur = _system_boot_epoch()
    if cur is None:
        _log("boot epoch unreadable -> skipping restore (fail-safe)")
        return False
    prev = None
    try:
        with open(_BOOT_MARKER, encoding="utf-8") as fh:
            prev = json.load(fh).get("boot_epoch")
    except (OSError, ValueError):
        prev = None
    # GetTickCount64 vs wall clock drifts a hair between reads; treat boots within
    # 90s as "the same boot" so we never double-fire.
    if isinstance(prev, (int, float)) and abs(cur - prev) <= 90:
        return False
    try:
        os.makedirs(ts._pitwall_runtime_dir(), exist_ok=True)
        with open(_BOOT_MARKER, "w", encoding="utf-8") as fh:
            json.dump({"boot_epoch": cur,
                       "stamped_at": datetime.now(timezone.utc).isoformat()}, fh)
    except OSError:
        _log("could not stamp boot marker -> skipping restore (fail-safe)")
        return False
    return True


# --- The Grid: ensure the cockpit is up --------------------------------------
def _pythonw() -> str:
    """The windowless python to launch The Grid GUI with."""
    import sys
    exe = sys.executable
    if os.name == "nt" and exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    return exe


def _grid_running() -> bool:
    """Is The Grid already open? Detect by command line (a python(w) process whose
    argv mentions grid_app.py). The Grid has no single-instance mutex, so on a
    detection FAILURE we assume it IS running (return True) — that avoids opening a
    duplicate cockpit, at the cost of maybe not surfacing it."""
    if os.name != "nt":
        return True
    query = (
        "Get-CimInstance Win32_Process -Filter "
        "\"Name='python.exe' OR Name='pythonw.exe'\" | "
        "Where-Object { $_.CommandLine -like '*grid_app.py*' } | "
        "Select-Object -First 1 -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", query],
            capture_output=True, text=True, timeout=20,
            creationflags=_CREATE_NO_WINDOW)
        return bool(out.stdout.strip())
    except Exception:
        _log("grid detection failed -> assuming it is up (no duplicate launch)")
        return True


def ensure_grid_running() -> None:
    """Make the cockpit visible: if The Grid isn't up, launch it and give it a beat
    to appear before the session windows start arriving."""
    if not os.path.isfile(GRID_APP):
        _log(f"grid_app.py not found at {GRID_APP} -> not launching The Grid")
        return
    if _grid_running():
        _log("The Grid already running")
        return
    try:
        subprocess.Popen([_pythonw(), GRID_APP], cwd=GRID_SRC,
                         creationflags=_CREATE_NO_WINDOW)
        _log("launched The Grid")
        time.sleep(1.5)      # off the GUI thread; lets the cockpit paint first
    except Exception as e:
        _log(f"failed to launch The Grid: {e!r}")


# --- fire the resumes --------------------------------------------------------
def _resume(work_dir: str, session_id: str) -> bool:
    """Reopen ONE session in its own pinned terminal via the reviewed grid_launch.ps1
    (Heavy / --resume). Args passed as a LIST (no shell). Returns True if launched."""
    name = os.path.basename(os.path.normpath(work_dir)) or "session"
    cmd = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", GRID_LAUNCH_PS1,
        "-WorkDir", work_dir,
        "-Name", name,
        "-Mode", "Heavy",
        "-SessionId", session_id,
    ]
    try:
        subprocess.Popen(cmd, creationflags=_CREATE_NO_WINDOW)
        return True
    except Exception as e:
        _log(f"resume failed for {name} ({session_id}): {e!r}")
        return False


def run_boot_restore(cfg: dict) -> None:
    """Entry point (call OFF the GUI thread). Honours the config flag, the once-per-boot
    gate, and the already-running dedupe guard, then reopens every recorded session."""
    if not cfg.get("reopen_sessions_on_boot", True):
        return
    if not available():
        _log("The Grid launcher not found -> boot restore unavailable")
        return
    if not claim_boot_restore():
        return                                  # not the first start this boot

    snapshot = ts.load_open_sessions()
    if not snapshot:
        _log("fresh boot, but no recorded sessions to restore")
        return

    # DEDUPE: never reopen a session that is already running (covers the
    # first-install-mid-day edge where the snapshot lists currently-open sessions).
    alive = set(ts.live_registry().keys())
    todo = [s for s in snapshot
            if s["session_id"] not in alive and _UUID_RE.match(s["session_id"])]
    if not todo:
        _log(f"fresh boot: {len(snapshot)} recorded, none need reopening")
        return

    _log(f"fresh boot: reopening {len(todo)} session(s)")
    ensure_grid_running()
    ok = 0
    launched: set[str] = set()
    for i, s in enumerate(todo):
        sid = s["session_id"]
        # IDEMPOTENCY (Ivan D): re-check liveness right before each launch so a
        # bypassed boot-gate (deleted marker / >90s restart misread) can never
        # double-open a window — we skip anything we already fired or that has
        # since come up on its own.
        if sid in launched or sid in set(ts.live_registry().keys()):
            continue
        # STAGGER (Ivan C, thundering herd): space the resumes ~1s apart so N
        # sessions don't all cross their token thresholds in the same instant and
        # fire full_auto/pitstop together (the 2026-06-11 fork-the-session-line
        # failure, here auto-triggered). Still "fire all" — just not the same tick.
        if i:
            time.sleep(1.0)
        if _resume(s["work_dir"], sid):
            launched.add(sid)
            ok += 1
    _log(f"boot restore done: {ok}/{len(todo)} launched")
