# Contributing to Pitwall — for Claude Code

Thanks for your interest! Pitwall is intentionally small and lean, and the goal is
to keep it that way.

## Ground rules

- **One brain, one face.** All the behaviour and number-crunching lives in
  `ts_core.py` (which imports no GUI toolkit); the widget is drawn in `pitwall_qt.py`
  (PySide6). Behaviour changes go in `ts_core.py`; look changes go in `pitwall_qt.py`.
- **One dependency.** Beyond the Python standard library, Pitwall needs **PySide6**
  (the Qt graphics package). If a feature needs *another* third-party dependency, it
  probably doesn't belong in Pitwall.
- **Local and quiet.** Pitwall never sends telemetry. The only network request it
  ever makes is the one-time PySide6 install. Any change that would phone home with
  your data will be declined.
- **Read-only.** Pitwall reads your Claude files; it does not modify them.

## Getting set up

1. Install **Python 3.8+** and **PySide6** (`pip install PySide6`).
2. Clone the repo and run `python pitwall_qt.py` (or double-click `Pitwall.bat` on
   Windows — it fetches PySide6 for you the first time if it's missing).
3. You'll need **Claude Code** installed and used on the machine, so there are
   transcript files under `~/.claude/` for Pitwall to read.

`pitwall_config.json` is per-machine state and is git-ignored — delete it any time to
reset to defaults.

## Making changes

- Match the existing style: clear names, plain-English comments aimed at
  non-programmers, small focused functions.
- Test by running the widget and exercising the path you changed. A quick sanity
  check that it still imports and renders:

  ```bash
  python -c "import ast; ast.parse(open('pitwall_qt.py', encoding='utf-8').read()); print('ok')"
  ```

- If you touch pricing, update both `RATES` and the note in the README/CHANGELOG.
- Keep cross-platform behaviour in mind — guard Windows-only calls with a fallback
  (see how the live-process check already does this).

## Pull requests

- Describe **what** changed and **why**, in plain language.
- One logical change per PR where possible.
- Update `CHANGELOG.md` under an "Unreleased" heading if your change is
  user-visible.

## Ideas that would be welcome

- A nicer macOS/Linux experience (the ↻ "open a terminal" button and the
  live-process check are Windows-first today).
- A one-click "save & restart" handoff.
- Pulling list prices from a maintained source instead of hardcoding `RATES`.

Open an issue first for anything large so we can agree on the approach.
