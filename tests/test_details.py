r"""
test_details.py — unit tests for the 2026-06-12 session-troubleshooting additions
in ts_core:

  - daemon_hosted     : process-ancestry bg detection (a CLI binary among a
                        session's ancestors = daemon-run background job; the
                        zombie-twin gap where a respawn's session id never
                        reaches the job's state.json).
  - jobs_info /
    bg_session_ids /
    stuck derivation  : reading job state files; working+blocked/needs = stuck.
  - session_details / : the right-click details view both faces render, and the
    details_text        copy-all payload.

Run:  python tests/test_details.py    (no pytest needed; plain asserts + a summary)
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ts_core as C

CASES = []


def check(name, cond):
    CASES.append((name, bool(cond)))


# ---- daemon_hosted (injected process map — no live processes touched) -------
# daemon shape: session 100 <- pty-host 50 (claude) <- daemon 40 (claude)
PMAP_DAEMON = {100: (50, "claude.exe"), 50: (40, "claude.exe"),
               40: (1, "winlogon.exe")}
# windowed shape: session 200 <- WindowsTerminal 60 <- explorer 1
PMAP_WINDOW = {200: (60, "windowsterminal.exe"), 60: (1, "explorer.exe")}

check("daemon-hosted session detected", C.daemon_hosted(100, PMAP_DAEMON))
check("windowed session is NOT daemon", not C.daemon_hosted(200, PMAP_WINDOW))
check("unknown pid is NOT daemon", not C.daemon_hosted(999, PMAP_WINDOW))
check("None pid is NOT daemon", not C.daemon_hosted(None, PMAP_DAEMON))
check("junk pid is NOT daemon", not C.daemon_hosted("x", PMAP_DAEMON))
# parent-pid cycle without a CLI ancestor must terminate, not hang
PMAP_CYCLE = {300: (301, "a.exe"), 301: (300, "b.exe")}
check("pid cycle terminates False", not C.daemon_hosted(300, PMAP_CYCLE))

# ---- jobs_info / bg_session_ids / stuck shape (temp JOBS_DIR) ----------------
_tmp = tempfile.mkdtemp(prefix="ts_jobs_")
os.makedirs(os.path.join(_tmp, "j1"))
with open(os.path.join(_tmp, "j1", "state.json"), "w", encoding="utf-8") as fh:
    json.dump({"state": "working", "tempo": "blocked", "needs": "open this session",
               "name": "stuck job", "sessionId": "sid-stuck",
               "resumeSessionId": "sid-stuck-r"}, fh)
os.makedirs(os.path.join(_tmp, "j2"))
with open(os.path.join(_tmp, "j2", "state.json"), "w", encoding="utf-8") as fh:
    json.dump({"state": "working", "tempo": "steady", "needs": None,
               "name": "healthy job", "sessionId": "sid-ok"}, fh)
_old_jobs_dir = C.JOBS_DIR
C.JOBS_DIR = _tmp
try:
    jobs = C.jobs_info()
    check("jobs_info reads both jobs", len(jobs) == 2)
    ids = C.bg_session_ids(jobs)
    check("bg ids include sessionId + resume", {"sid-stuck", "sid-stuck-r",
                                                "sid-ok"} <= ids)
    stuck = {j["sessionId"] for j in jobs
             if j["state"] == "working" and (j["tempo"] == "blocked" or j["needs"])}
    check("blocked+needs job is stuck", "sid-stuck" in stuck)
    check("healthy job is NOT stuck", "sid-ok" not in stuck)
finally:
    C.JOBS_DIR = _old_jobs_dir

# ---- session_details / details_text ------------------------------------------
# Point the pitstop paths at nowhere for this section so the details rows are
# machine-independent (the real toolchain on the dev box would append a live
# "Pitstop watch" row); pitstop_watch gets its own controlled section below.
C.PITSTOP_DIR = os.path.join(tempfile.gettempdir(), "ts_no_such_pitstop_dir")

CLOSED = {"sid": "abc12345-feed-dead-beef-000000000000", "open": False, "bg": False,
          "daemon": False, "stuck": False, "pid": None, "tok": 1234567, "usd": 4.56,
          "in": 1000, "out": 2000, "cw": 3000, "cr": 1228567,
          "cwd": r"C:\dev\myproject", "branch": "main", "model": "fable",
          "label": "Some closed session", "first": "hello world this is a test",
          "path": r"C:\tmp\abc.jsonl",
          "last": datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)}
rows = C.session_details(CLOSED)
keys = [k for k, _ in rows]
check("details: name first", keys[0] == "Name")
check("details: full session id", any("abc12345-feed-dead-beef" in v for _, v in rows))
check("details: closed status", dict(rows)["Status"] == "closed")
check("details: no Process row when pid None", "Process" not in keys)
check("details: transcript path present", dict(rows)["Transcript"] == r"C:\tmp\abc.jsonl")
check("details: window row only for open", "Window" not in keys)
check("details: spend line has $ and tokens",
      "$4.56" in dict(rows)["This 5h window"]
      and "1.2M" in dict(rows)["This 5h window"].replace("1.23M", "1.2M"))

OPEN_BG = dict(CLOSED, open=True, bg=True, daemon=True, stuck=True,
               pid=999999991, status="busy")
rows2 = C.session_details(OPEN_BG)
d2 = dict(rows2)
check("details: bg window line says no window",
      d2["Window"].startswith("none — background session"))
check("details: stuck flagged in status", "STUCK" in d2["Status"])
check("details: daemon flagged in status", "daemon-hosted" in d2["Status"])
check("details: dead pid says ended", d2["Process"].startswith("999999991 (ended)"))
check("details: zero-spend line explains itself",
      "no spend" in C.session_details(dict(OPEN_BG, tok=0))[-1][1])

check("details: no pitstop row when toolchain absent",
      "Pitstop watch" not in dict(rows2))

text = C.details_text(rows)
check("copy text one line per row", len(text.splitlines()) == len(rows))
check("copy text label: value shape", text.splitlines()[0] == "Name: Some closed session")

# ---- pitstop_watch (the verification pill, owner's order 2026-06-12) ----------
# Fully controlled fixture: temp pitstop dir + configs + settings.json + marker
# dir, so the states are exercised without touching the real toolchain.
_ps = tempfile.mkdtemp(prefix="ts_ps_")
_marks = tempfile.mkdtemp(prefix="ts_psmark_")
C.PITSTOP_DIR = _ps
C.PITSTOP_CONFIG = os.path.join(_ps, "pitstop_config.json")
C.PITSTOP_NUDGE_CONFIG = os.path.join(_ps, "nudge_config.json")
C.CLAUDE_SETTINGS = os.path.join(_ps, "settings.json")
C.NUDGE_MARKER_DIR = _marks


def _ps_setup(full_auto=True, threshold=5_000_000, hook=True):
    with open(C.PITSTOP_CONFIG, "w", encoding="utf-8") as fh:
        json.dump({"remote_control": True, "auto_mode": True,
                   "full_auto": full_auto}, fh)
    with open(C.PITSTOP_NUDGE_CONFIG, "w", encoding="utf-8") as fh:
        json.dump({"threshold_tokens": threshold}, fh)
    stop = [{"hooks": [{"type": "command", "command": "python",
                        "args": [r"C:\dev\tools\pitstop_nudge.py"]}]}] if hook else []
    with open(C.CLAUDE_SETTINGS, "w", encoding="utf-8") as fh:
        json.dump({"hooks": {"Stop": stop}}, fh)


def _ps_sess(tok, sid="sid-pill-test", open_=True):
    return {"sid": sid, "open": open_, "tok": tok}


_ps_setup()
check("watch: closed session -> None", C.pitstop_watch(_ps_sess(1, open_=False)) is None)

w = C.pitstop_watch(_ps_sess(1_200_000))
check("watch: armed below threshold", w and w["state"] == "armed")
check("watch: armed label carries the mark", w and "5M" in w["label"])
check("watch: armed full-auto label says auto",
      w and w["label"] == "Pitstop auto · 5M")
check("watch: armed full-auto tip says it restarts itself",
      w and "restarts itself" in w["tip"])
check("watch: armed line names the switch", w and "Full auto on" in w["line"])

_ps_setup(full_auto=False)
w = C.pitstop_watch(_ps_sess(1_200_000))
check("watch: armed offer label says on", w and w["label"] == "Pitstop on · 5M")
check("watch: armed offer tip says nothing happens without OK",
      w and "without your OK" in w["tip"])

w = C.pitstop_watch(_ps_sess(6_000_000))
check("watch: over the mark with no marker -> due", w and w["state"] == "due")
check("watch: due label carries the overage",
      w and w["label"] == "Pitstop due · 1M over")
check("watch: due explains mid-task delay", w and "mid-task" in w["line"])
check("watch: due line says no clean break yet",
      w and "no clean break" in w["line"])

with open(os.path.join(_marks, "pitstop_nudge_lvl_sid-pill-test.txt"), "w") as fh:
    fh.write("2")
w = C.pitstop_watch(_ps_sess(6_500_000))
check("watch: marker tier 2 -> fired", w and w["state"] == "fired" and w["tier"] == 2)
check("watch: fired offer label carries the overage",
      w and w["label"] == "Pitstop offered · 1.5M over")
_ps_setup(full_auto=True)
w = C.pitstop_watch(_ps_sess(6_500_000))
check("watch: fired auto label carries the overage",
      w and w["label"] == "Pitstop fired · 1.5M over")
check("watch: fired line carries nudge level", w and "nudge 2" in w["line"])

with open(os.path.join(_marks, "pitstop_nudge_workeroff_sid-pill-test.txt"), "w") as fh:
    fh.write("worker-x")
w = C.pitstop_watch(_ps_sess(6_500_000))
check("watch: worker marker wins -> off", w and w["state"] == "off")

_ps_setup(hook=False)
w = C.pitstop_watch(_ps_sess(100, sid="sid-other"))
check("watch: no Stop hook -> unarmed", w and w["state"] == "unarmed")
check("watch: unarmed label is the alarm", w and w["label"] == "Pitstop hook missing")

with open(C.CLAUDE_SETTINGS, "w", encoding="utf-8") as fh:
    fh.write("{not json")
check("watch: mangled settings reads as unarmed, never crashes",
      C.pitstop_watch(_ps_sess(100, sid="sid-other"))["state"] == "unarmed")

_ps_setup()
# Below the pitstop mark the seam question isn't live yet — the chip counts
# down to the mark instead (owner, 2026-06-12: "can you say Seam in xxM").
w = C.pitstop_watch(_ps_sess(1_200_000))
check("watch: below the mark -> seam counts down to it",
      w and w["seam_upcoming"] is True and w["seam_label"] == "Seam in 3.8M")
check("watch: countdown tip names the mark", w and "5M pitstop mark" in w["seam_tip"])
busy_low = dict(_ps_sess(1_200_000), status="busy")
w = C.pitstop_watch(busy_low)
check("watch: below the mark busy -> still the countdown",
      w and w["seam_label"] == "Seam in 3.8M")
# At/over the mark the chip is live: green seam, or 'looking' while mid-task.
w = C.pitstop_watch(_ps_sess(6_000_000))
check("watch: over the mark, no busy status -> seam available",
      w and w["seam"] is True and w["seam_upcoming"] is False
      and w["seam_label"] == "Seam available")
busy = dict(_ps_sess(6_000_000), status="busy")
w = C.pitstop_watch(busy)
check("watch: over the mark, busy -> no seam, looking",
      w and w["seam"] is False and w["seam_label"] == "No seam · looking")
check("watch: seam tip explains the word (stranger test)",
      w and "clean break between tasks" in w["seam_tip"])
# The due pill rides the heat ramp (owner, 2026-06-12): amber at the mark,
# full red by half-a-threshold of overage. Fresh sid — sid-pill-test carries
# fired/worker markers from the tests above.
w = C.pitstop_watch(_ps_sess(5_000_000, sid="sid-heat-test"))
check("watch: due at the mark sits at ramp midpoint",
      w and w["state"] == "due" and abs(w["due_heat"] - 0.5) < 1e-9)
w = C.pitstop_watch(_ps_sess(6_000_000, sid="sid-heat-test"))
check("watch: due 1M over heats past amber",
      w and w["state"] == "due" and abs(w["due_heat"] - 0.7) < 1e-9)
w = C.pitstop_watch(_ps_sess(9_000_000, sid="sid-heat-test"))
check("watch: due caps at full red",
      w and w["state"] == "due" and w["due_heat"] == 1.0)

w = C.pitstop_watch(_ps_sess(1_200_000, sid="sid-other"))
check("watch: every tip ends with the all-states legend",
      w and "Everything this pill can say" in w["tip"]
      and "Pitstop hook missing" in w["tip"])
check("watch: legend stays out of the details line",
      w and "Everything this pill can say" not in w["line"])

rows_ps = C.session_details(_ps_sess(1_200_000, sid="sid-other"))
check("details: open session gains the Pitstop watch row",
      "Pitstop watch" in dict(rows_ps))
check("details: pitstop row is the plain-English line",
      dict(rows_ps).get("Pitstop watch", "").startswith("on — fires at 5M"))

# ---- session_row_frac (owner 2026-06-12: dots show CHAT FULLNESS — one heat with
#      the "this chat" dot + save pill; supersedes the $-share-of-bar model) -------
_row = {"usd": 20.0, "ctx": 110_000}
check("row frac: chat fullness = ctx / ctx_red",
      abs(C.session_row_frac(_row, 220_000) - 0.5) < 1e-9)
check("row frac: caps at 1.0 past the re-read ceiling",
      C.session_row_frac({"ctx": 300_000}, 220_000) == 1.0)
check("row frac: $ spend does NOT colour the dot (fullness only)",
      C.session_row_frac({"usd": 250.0, "ctx": 0}, 220_000) == 0.0)
check("row frac: zero-ctx session is stone cold",
      C.session_row_frac({"usd": 99.0, "ctx": 0}, 220_000) == 0.0)
check("row frac: missing keys do not blow up",
      C.session_row_frac({}, 220_000) == 0.0)
check("row frac: junk ctx_red does not divide by zero",
      C.session_row_frac({"ctx": 50_000}, 0) <= 1.0)

# ---- summary ----------------------------------------------------------------
fails = [n for n, ok in CASES if not ok]
for n, ok in CASES:
    print("  [%s] %s" % ("PASS" if ok else "FAIL", n))
print()
print("%d/%d passed" % (len(CASES) - len(fails), len(CASES)))
if fails:
    sys.exit(1)
