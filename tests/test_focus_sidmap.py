r"""
test_focus_sidmap.py — guards the 2026-06-27 fix that made the heat ring follow the
FOCUSED CLI again, via a deterministic REVERSE of the hook-recorded session→window map.

Background: focus-follow broke when Claude Code 2.1.x stopped registering terminal
sessions. FocusTracker's vote system needs a "busy / just did work while focused" signal
to learn which session lives in a window; that signal dried up, so votes never accrued,
focused_sid returned None, and the ring fell back to the most-recent session instead of
the focused one. The fix: FocusTracker._resolve now consults _sidmap_sid (the reverse of
_sidmap_window) FIRST, so the focused window resolves deterministically with NO votes at
all — the same hook-recorded evidence window_evidence already trusts, used backwards.

These tests bite if that reverse path is removed, stops excluding closed/background
sessions, or stops taking priority over the (now-dead) vote map.

_sidmap_window does live Win32 re-verification, so it's STUBBED here with a fixture map —
the logic under test is the reverse lookup + its precedence, not the window check itself.

Run:  python tests/test_focus_sidmap.py    (no pytest needed; plain asserts + a summary)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ts_core as C

CASES = []


def check(name, cond):
    CASES.append((name, bool(cond)))


# Four sessions: two ordinary open ones in distinct windows, one background job that
# happens to carry a map entry (must NEVER match — a bg job has no window), and one
# closed session that also carries a stale map entry (must NEVER match either).
SESSIONS = [
    {"sid": "sid-A", "open": True, "bg": False},
    {"sid": "sid-B", "open": True, "bg": False},
    {"sid": "sid-bg", "open": True, "bg": True},
    {"sid": "sid-closed", "open": False, "bg": False},
]
WINMAP = {"sid-A": 1001, "sid-B": 1002, "sid-bg": 1003, "sid-closed": 1004}

# Stub the live Win32 reverse-verify with the fixture map.
_orig_sidmap_window = C._sidmap_window
C._sidmap_window = lambda sid: WINMAP.get(sid)

try:
    ft = C.FocusTracker()

    # ---- _sidmap_sid: the reverse lookup itself --------------------------------
    check("reverse: focused window 1001 -> its session A",
          ft._sidmap_sid(1001, SESSIONS) == "sid-A")
    check("reverse: focused window 1002 -> its session B",
          ft._sidmap_sid(1002, SESSIONS) == "sid-B")
    check("reverse: background session never matches (no real window)",
          ft._sidmap_sid(1003, SESSIONS) is None)
    check("reverse: closed session never matches (stale map entry)",
          ft._sidmap_sid(1004, SESSIONS) is None)
    check("reverse: no-focus (hwnd 0) -> None",
          ft._sidmap_sid(0, SESSIONS) is None)
    check("reverse: unmapped window -> None (falls through)",
          ft._sidmap_sid(9999, SESSIONS) is None)

    # ---- _resolve: the actual regression ---------------------------------------
    # The broken state was ZERO votes. With the map in place, the focused window must
    # still resolve to its session — this is the whole point of the fix.
    ft2 = C.FocusTracker()
    check("regression: resolves with NO votes (the dead-signal case)",
          ft2._resolve(1002, SESSIONS) == "sid-B")

    # Precedence: even when the stale vote map points the focused window at the WRONG
    # session, the deterministic sidmap must win.
    ft3 = C.FocusTracker()
    ft3.bind[1001] = {"sid-B": 99.0}          # votes wrongly say window 1001 == B
    check("precedence: sidmap outranks a wrong vote",
          ft3._resolve(1001, SESSIONS) == "sid-A")

    # No map entry for the focused window -> the vote/title fallback still works.
    ft4 = C.FocusTracker()
    ft4.bind[7777] = {"sid-A": 5.0}
    check("fallback: with no map entry the vote path still resolves",
          ft4._resolve(7777, SESSIONS) == "sid-A")
finally:
    C._sidmap_window = _orig_sidmap_window

# ---- summary ----------------------------------------------------------------
fails = [n for n, ok in CASES if not ok]
for n, ok in CASES:
    print("  [%s] %s" % ("PASS" if ok else "FAIL", n))
print()
print("%d/%d passed" % (len(CASES) - len(fails), len(CASES)))
if fails:
    sys.exit(1)
