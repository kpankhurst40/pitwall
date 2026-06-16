@echo off
rem Pitwall.bat - start Pitwall (or tell you it is already running).
rem
rem All the smarts live in launcher.py (so there is ONE place to maintain):
rem   * If a widget is already up: launcher shows a friendly "already running"
rem     popup and exits - no second widget, no silent no-op.
rem   * If not: launcher makes sure PySide6 is installed, then starts the widget.
rem The launcher itself is short-lived (checks PySide6, starts the widget, exits).

start "" pythonw "%~dp0launcher.py"
exit /b 0
