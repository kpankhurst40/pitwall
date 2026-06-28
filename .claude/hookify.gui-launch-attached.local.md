---
name: gui-launch-attached
enabled: true
event: bash
action: warn
pattern: '(?i)^(?!.*(?:start-process|cmd /c start|/c start|\bwt\b|os\.startfile|&\s*$)).*\bpythonw?[0-9.]*\s+(?:-m\s+[\w.]*\.gui[\w.]*|[^\s;|&]*(?:app|_gui|gui|widget)\.py)\b'
---

# Don't launch a long-lived GUI app attached to a tool call

This command looks like it launches a **GUI app in the foreground** (a `*app.py` /
`*gui.py` / `*widget.py` file, or a `-m …gui…` module) with **no detach wrapper**.

The harness force-kills each finished command's whole **process tree**, so a GUI
launched attached dies with the command — and takes any descendants with it. This is
what silently killed Kevin's live **Pitwall + The Grid** widgets on 2026-06-25.

**Launch it DETACHED instead**, so it reparents out of the tool's tree:
- PowerShell `Start-Process python <app>`
- `cmd /c start "" /MIN python <app>`  ·  Windows Terminal `wt python <app>`
- in Python, `os.startfile(...)` / a detached broker
Then verify the pid is alive in a **separate** tool call.

(Workspace rule from `C:\dev\CLAUDE.md` Lessons Learned. Advisory `warn` only.)
