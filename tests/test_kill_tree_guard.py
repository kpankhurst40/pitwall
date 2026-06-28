"""Pytest wrapper so the PowerShell kill-tree guard runs in the normal suite.

The real assertions live in test_kill_tree_guard.ps1 (the guarded code is PowerShell).
This just shells out to Windows PowerShell 5.1 and fails if that test returns non-zero.
Skips cleanly off-Windows / when powershell isn't on PATH.
"""
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PS_TEST = os.path.join(HERE, "test_kill_tree_guard.ps1")
WINPS51 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                       "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def _powershell():
    if os.path.exists(WINPS51):
        return WINPS51
    return shutil.which("powershell") or shutil.which("pwsh")


@pytest.mark.skipif(os.name != "nt" or _powershell() is None,
                    reason="Windows + PowerShell required for the read_usage.ps1 kill guard")
def test_kill_tree_excludes_recycled_pid_orphans():
    exe = _powershell()
    p = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", PS_TEST],
        capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, "kill-tree guard failed:\n%s\n%s" % (p.stdout, p.stderr)
