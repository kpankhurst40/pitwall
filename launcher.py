#!/usr/bin/env python3
"""Pitwall — install-lane launcher.

Pitwall has ONE look: the PySide6 (Qt) widget in `pitwall_qt.py`. This launcher's
only job on the source-install lane is to make sure PySide6 is present, then start
the widget:

  * If a widget is already up, say so and exit (no second instance).
  * If PySide6 is installed, start `pitwall_qt.py`.
  * If it isn't, offer to `pip install PySide6` once (with a live progress window),
    then start. If the user declines or the install can't run, say what to do and exit.

Why this file is Tkinter and not PySide6: the launcher has to run *before* PySide6
might exist, so its small bootstrap dialogs can only use what ships with Python
(Tkinter). It spawns the chosen face as an independent process and exits.

A packaged .exe build (Johny) bundles PySide6 and runs `pitwall_qt.main()` directly
via `build/pitwall_entry.py`, skipping this launcher entirely.
"""
import importlib.util
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk

import ts_core as ts

HERE = os.path.dirname(os.path.abspath(__file__))
QT_FACE = "pitwall_qt.py"

# ---- palette (borrow the shared design tokens so the dialogs are on-brand) ----
BG, CARD, EDGE = ts.BG, ts.PANEL, ts.EDGE
INK, MUT, FAINT = ts.INK, ts.MUT, ts.FAINT
ACCENT, GREEN = ts.ACCENT, ts.GREEN

TITLE_SIZE = 15   # one size for every top-level dialog title (Sarah's type-scale fix)


# --------------------------------------------------------------------------- #
# process helpers
# --------------------------------------------------------------------------- #
def _windowless_python():
    """Prefer pythonw.exe so a launched face has no console window."""
    exe = sys.executable
    if os.name == "nt" and exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    return exe


def have_pyside6():
    try:
        return importlib.util.find_spec("PySide6") is not None
    except Exception:
        return False


def launch_face(face):
    """Start a face as an independent process, then let the launcher exit."""
    path = os.path.join(HERE, face)
    try:
        subprocess.Popen([_windowless_python(), path], cwd=HERE)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# small Tk helpers
# --------------------------------------------------------------------------- #
def _center(win, w, h):
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")


def _root():
    r = tk.Tk()
    r.configure(bg=BG)
    try:
        r.tk.call("tk", "scaling", 1.25)
    except Exception:
        pass
    return r


def _btn(parent, text, cmd, *, primary=False, big=False):
    b = tk.Button(
        parent, text=text, command=cmd, cursor="hand2",
        bg=(ACCENT if primary else CARD), fg=("#0b0e13" if primary else INK),
        activebackground=(ACCENT if primary else EDGE),
        activeforeground=("#0b0e13" if primary else INK),
        relief="flat", bd=0, padx=18, pady=(10 if big else 7),
        font=("Segoe UI", 11 if big else 10, "bold" if primary else "normal"),
    )
    if not primary:
        # a thin outline so the quiet secondary button reads as clickable, not text
        b.configure(highlightthickness=1, highlightbackground=EDGE,
                    highlightcolor=EDGE)
    return b


# --------------------------------------------------------------------------- #
# "Pitwall needs PySide6" prompt
# --------------------------------------------------------------------------- #
def ask_install():
    """Returns 'install' | None (window closed / cancelled)."""
    result = {"choice": None}
    win = _root()
    win.title("Pitwall — one quick download")

    wrap = tk.Frame(win, bg=BG)
    wrap.pack(fill="both", expand=True, padx=22, pady=20)
    tk.Label(wrap, text="Pitwall needs PySide6", bg=BG, fg=INK,
             font=("Segoe UI", TITLE_SIZE, "bold")).pack(anchor="w")
    tk.Label(wrap,
             text="Pitwall draws its widget with a graphics package called PySide6 "
                  "(about 100MB). It installs once, then Pitwall starts instantly "
                  "every time after. Install it now?",
             bg=BG, fg=MUT, wraplength=480, justify="left",
             font=("Segoe UI", 10)).pack(anchor="w", pady=(6, 18))

    row = tk.Frame(wrap, bg=BG)
    row.pack(fill="x")

    def pick(choice):
        result["choice"] = choice
        win.destroy()

    _btn(row, "Install PySide6  (~100MB)", lambda: pick("install"),
         primary=True, big=True).pack(side="left")
    _btn(row, "Not now", lambda: pick(None)).pack(side="left", padx=(10, 0))

    _center(win, 540, 230)
    win.attributes("-topmost", True)
    win.mainloop()
    return result["choice"]


# --------------------------------------------------------------------------- #
# pip install with a live progress window
# --------------------------------------------------------------------------- #
def install_pyside6():
    """Run `pip install PySide6` with a progress window. Returns True on success.

    Tries a plain install first; if that fails, retries with --user (common when
    the active Python is not user-writable). Cancelling or any error -> False.
    """
    import queue
    import threading

    q = queue.Queue()
    state = {"proc": None, "cancelled": False, "ok": False}

    def worker():
        py = sys.executable
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for attempt in (["--disable-pip-version-check"],
                        ["--disable-pip-version-check", "--user"]):
            if state["cancelled"]:
                break
            cmd = [py, "-m", "pip", "install", *attempt, "PySide6"]
            q.put(("line", "Running: pip install PySide6"
                           + (" --user" if "--user" in attempt else "")))
            try:
                proc = subprocess.Popen(
                    cmd, cwd=HERE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1,
                    creationflags=flags)
            except Exception as e:
                q.put(("line", f"Could not start pip: {e}"))
                continue
            state["proc"] = proc
            for raw in proc.stdout:
                if state["cancelled"]:
                    break
                line = raw.rstrip()
                if line:
                    q.put(("line", line))
            proc.wait()
            if state["cancelled"]:
                break
            if proc.returncode == 0:
                state["ok"] = True
                break
            q.put(("line", f"pip exited with code {proc.returncode}; "
                           "retrying with --user…"))
        q.put(("done", None))

    win = _root()
    win.title("Installing PySide6…")
    wrap = tk.Frame(win, bg=BG)
    wrap.pack(fill="both", expand=True, padx=22, pady=20)
    tk.Label(wrap, text="Installing PySide6", bg=BG, fg=INK,
             font=("Segoe UI", TITLE_SIZE, "bold")).pack(anchor="w")
    tk.Label(wrap, text="This can take a minute. Leave this window open.",
             bg=BG, fg=MUT, font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 14))

    style = ttk.Style(win)
    try:
        style.theme_use("clam")
        style.configure("TS.Horizontal.TProgressbar", troughcolor=CARD,
                        background=ACCENT, bordercolor=EDGE, lightcolor=ACCENT,
                        darkcolor=ACCENT)
    except Exception:
        pass
    bar = ttk.Progressbar(wrap, mode="indeterminate", length=480,
                          style="TS.Horizontal.TProgressbar")
    bar.pack(fill="x")
    bar.start(14)

    status = tk.Label(wrap, text="Starting…", bg=BG, fg=FAINT, anchor="w",
                      wraplength=480, justify="left", font=("Consolas", 9))
    status.pack(fill="x", pady=(12, 14))

    def cancel():
        state["cancelled"] = True
        p = state["proc"]
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
        win.destroy()

    _btn(wrap, "Cancel", cancel).pack(anchor="e")
    win.protocol("WM_DELETE_WINDOW", cancel)

    def pump():
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "line":
                    status.config(text=payload)
                elif kind == "done":
                    win.destroy()
                    return
        except queue.Empty:
            pass
        if not state["cancelled"]:
            win.after(120, pump)

    threading.Thread(target=worker, daemon=True).start()
    _center(win, 540, 250)
    win.attributes("-topmost", True)
    win.after(120, pump)
    win.mainloop()
    return state["ok"]


def info(title, message):
    win = _root()
    win.title(title)
    wrap = tk.Frame(win, bg=BG)
    wrap.pack(fill="both", expand=True, padx=22, pady=20)
    tk.Label(wrap, text=title, bg=BG, fg=INK,
             font=("Segoe UI", TITLE_SIZE, "bold")).pack(anchor="w")
    tk.Label(wrap, text=message, bg=BG, fg=MUT, wraplength=460, justify="left",
             font=("Segoe UI", 10)).pack(anchor="w", pady=(6, 16))
    _btn(wrap, "OK", win.destroy, primary=True).pack(anchor="e")
    _center(win, 520, 215)
    win.attributes("-topmost", True)
    win.mainloop()


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    # If a widget is already up, don't start a second one — just say so. This is the
    # friendly counterpart to the per-face owning lock: without it the launcher path
    # was silent, which looked like a broken launch button.
    if ts.another_instance_running():
        info("Pitwall is already open",
             "Pitwall is already running on this PC. Look for the widget on "
             "your screen — it may be hidden behind another window, or sitting on "
             "your other monitor.")
        return

    if have_pyside6():
        launch_face(QT_FACE)
        return

    # PySide6 isn't here yet — offer the one-time download.
    if ask_install() != "install":
        return                          # declined / closed — do nothing

    if install_pyside6() and have_pyside6():
        launch_face(QT_FACE)
    else:
        info("Couldn't install PySide6",
             "PySide6 didn't install (you may be offline, or pip isn't "
             "available). Pitwall needs it to run. You can install it yourself "
             "with:\n\n    pip install PySide6\n\nthen launch Pitwall again.")


if __name__ == "__main__":
    main()
