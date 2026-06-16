r"""
test_nudge.py — unit tests for the "Nudge me" (Mode 2) decision core in ts_core.

Covers the two gates that make the shoulder-tap honest and safe:
  - nudge_arm_block_reason  : the F12 refuse-to-arm guard (a metered/off-subscription
                              key present -> must NOT arm; owner, 2026-06-08 = refuse).
  - nudge_decision          : armed + not-env-blocked + past the break-even floor +
                              at/above the configured save-ladder rung -> tap, else None.

Run:  python tests/test_nudge.py    (no pytest needed; plain asserts + a summary)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ts_core as C


def cfg(**over):
    """A minimal config: armed, default break-even (90k) and default ctx_red (220k)
    so the save-ladder rungs sit at known token counts. Override per test."""
    base = {
        "nudge_armed": True,
        "nudge_breakeven_tok": C.DEFAULTS["nudge_breakeven_tok"],   # 90_000
        "ctx_red": C.DEFAULTS["ctx_red"],                          # 220_000
        "nudge_tier": "Save & start fresh",                        # frac >= 0.75
    }
    base.update(over)
    return base


def sess(ctx):
    return {"sid": "t", "ctx": ctx, "usd": 1.0}


CASES = []


def check(name, cond):
    CASES.append((name, bool(cond)))


# ---- F12: nudge_arm_block_reason -------------------------------------------
check("clean env arms", C.nudge_arm_block_reason({}) is None)
check("API key blocks", C.nudge_arm_block_reason({"ANTHROPIC_API_KEY": "sk-x"}) is not None)
check("block names the var", "ANTHROPIC_API_KEY" in (C.nudge_arm_block_reason({"ANTHROPIC_API_KEY": "sk-x"}) or ""))
check("empty key ignored", C.nudge_arm_block_reason({"ANTHROPIC_API_KEY": ""}) is None)
check("whitespace key ignored", C.nudge_arm_block_reason({"ANTHROPIC_API_KEY": "   "}) is None)
check("base_url blocks", C.nudge_arm_block_reason({"ANTHROPIC_BASE_URL": "http://proxy"}) is not None)
check("bedrock flag blocks", C.nudge_arm_block_reason({"CLAUDE_CODE_USE_BEDROCK": "1"}) is not None)
check("vertex flag blocks", C.nudge_arm_block_reason({"CLAUDE_CODE_USE_VERTEX": "1"}) is not None)

# F-N1: widened M1 family — two new exact keys + the ANTHROPIC_DEFAULT_ prefix.
check("custom headers blocks",
      C.nudge_arm_block_reason({"ANTHROPIC_CUSTOM_HEADERS": "x-key: v"}) is not None)
check("api-key-helper blocks",
      C.nudge_arm_block_reason({"ANTHROPIC_API_KEY_HELPER": "/usr/bin/getkey"}) is not None)
check("ANTHROPIC_DEFAULT_ prefix blocks (sonnet)",
      C.nudge_arm_block_reason({"ANTHROPIC_DEFAULT_SONNET_MODEL": "some-endpoint"}) is not None)
check("ANTHROPIC_DEFAULT_ prefix blocks (opus)",
      C.nudge_arm_block_reason({"ANTHROPIC_DEFAULT_OPUS_MODEL": "some-endpoint"}) is not None)
check("ANTHROPIC_DEFAULT_ block names the offending var",
      "ANTHROPIC_DEFAULT_HAIKU_MODEL" in
      (C.nudge_arm_block_reason({"ANTHROPIC_DEFAULT_HAIKU_MODEL": "e"}) or ""))
check("empty ANTHROPIC_DEFAULT_ var ignored",
      C.nudge_arm_block_reason({"ANTHROPIC_DEFAULT_SONNET_MODEL": ""}) is None)
# F-N1 negatives: must NOT cause false refusals on ordinary subscription machines.
check("HTTP_PROXY does NOT block", C.nudge_arm_block_reason({"HTTP_PROXY": "http://p:8080"}) is None)
check("AWS_* does NOT block", C.nudge_arm_block_reason({"AWS_ACCESS_KEY_ID": "AKIA..."}) is None)
check("GOOGLE_* does NOT block",
      C.nudge_arm_block_reason({"GOOGLE_APPLICATION_CREDENTIALS": "/c/creds.json"}) is None)

# ---- nudge_decision: the arm gate ------------------------------------------
check("disarmed -> silent", C.nudge_decision(sess(200_000), cfg(nudge_armed=False), env={}) is None)
check("armed but env-blocked -> silent",
      C.nudge_decision(sess(200_000), cfg(), env={"ANTHROPIC_API_KEY": "sk-x"}) is None)

# ---- nudge_decision: the break-even floor ----------------------------------
# Tier lowered to "All clear" so ONLY the floor can gate — isolates the floor.
check("below break-even -> silent (even at 'All clear' tier)",
      C.nudge_decision(sess(89_999), cfg(nudge_tier="All clear"), env={}) is None)
check("at break-even -> taps (tier permitting)",
      C.nudge_decision(sess(90_000), cfg(nudge_tier="All clear"), env={}) is not None)

# ---- nudge_decision: the save-ladder rung ----------------------------------
# ctx_red 220k: 'Save & start fresh' = frac in [0.75, 0.90) = ctx in [165k, 198k).
check("past floor but below rung -> silent",
      C.nudge_decision(sess(120_000), cfg(), env={}) is None)          # frac 0.545 -> 'Maybe save soon'
check("at the rung -> taps",
      C.nudge_decision(sess(170_000), cfg(), env={}) is not None)      # frac 0.77 -> 'Save & start fresh'
check("above the rung (higher word) -> taps",
      C.nudge_decision(sess(205_000), cfg(), env={}) is not None)      # frac 0.93 -> 'Hand off now'

# ---- nudge_decision: the payload -------------------------------------------
d = C.nudge_decision(sess(170_000), cfg(), env={})
check("payload has headline+detail", d and d.get("headline") and d.get("detail"))
check("detail tells you to /pitstop then /clear",
      d and "/pitstop" in d["detail"] and "/clear" in d["detail"])
check("payload carries the ctx + ladder word", d and d.get("ctx") == 170_000 and d.get("word"))

# ---- unknown tier word -> fail safe (stay quiet) ----------------------------
check("unknown tier word -> silent",
      C.nudge_decision(sess(205_000), cfg(nudge_tier="bogus"), env={}) is None)

# ---- snooze: a 1h quiet window on top of an armed, would-otherwise-tap session
# Baseline: at the rung with no snooze AND now passed -> taps.
check("no snooze (now given) -> taps",
      C.nudge_decision(sess(170_000), cfg(), env={}, now=1000.0) is not None)
# Snooze the config from a fixed 'now', then a tap within the window is silent and a
# tap after it returns.
_snz = C.nudge_snooze(cfg(), now=1000.0)                       # quiet until 1000+3600
check("snooze sets a future deadline", _snz["nudge_snooze_until"] == 1000.0 + C.NUDGE_SNOOZE_SECONDS)
check("snooze window default is 1h", C.NUDGE_SNOOZE_SECONDS == 3600)
check("within snooze window -> silent",
      C.nudge_decision(sess(170_000), _snz, env={}, now=2000.0) is None)   # 2000 < 4600
check("at snooze expiry -> taps again",
      C.nudge_decision(sess(170_000), _snz, env={}, now=4600.0) is not None)
check("past snooze window -> taps again",
      C.nudge_decision(sess(170_000), _snz, env={}, now=9999.0) is not None)
# Snooze must not override the harder gates: disarmed/blocked stay silent regardless.
check("snooze irrelevant when disarmed",
      C.nudge_decision(sess(170_000), C.nudge_snooze(cfg(nudge_armed=False), now=1000.0),
                       env={}, now=9999.0) is None)

# ---- momentary mute (owner, 2026-06-12): custom snooze lengths + the ✕ ------
check("snooze takes a custom length",
      C.nudge_snooze(cfg(), now=1000.0, seconds=300)["nudge_snooze_until"] == 1300.0)
check("dismiss constant is 10 minutes", C.NUDGE_DISMISS_SECONDS == 600)
check("snooze choices are 5m/15m/30m/1h",
      tuple(s for s, _ in C.NUDGE_SNOOZE_CHOICES) == (300, 900, 1800, 3600))
check("snooze choice labels match their seconds",
      tuple(t for _, t in C.NUDGE_SNOOZE_CHOICES) == ("5m", "15m", "30m", "1h"))

# ---- push_quiet_state / set_push_quiet: the bell's wider mute ----------------
# Point the module at a throwaway pitstop_config.json AND a throwaway Claude
# settings.json ("until next pitstop" is honored only while the pitstop Stop
# hook is registered — review 2026-06-12 LOW-2); restore both afterwards.
import json
import tempfile

_real_cfg = C.PITSTOP_CONFIG
_real_settings = C.CLAUDE_SETTINGS
_tmpdir = tempfile.mkdtemp(prefix="ts_nudge_quiet_")
C.PITSTOP_CONFIG = os.path.join(_tmpdir, "pitstop_config.json")
C.CLAUDE_SETTINGS = os.path.join(_tmpdir, "settings.json")


def _write_cfg(obj):
    with open(C.PITSTOP_CONFIG, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _read_cfg():
    with open(C.PITSTOP_CONFIG, encoding="utf-8") as fh:
        return json.load(fh)


def _write_settings(armed):
    obj = ({"hooks": {"Stop": [{"hooks": [{"command": "python pitstop_nudge.py"}]}]}}
           if armed else {"hooks": {"Stop": []}})
    with open(C.CLAUDE_SETTINGS, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


_write_settings(True)

check("quiet: missing file -> not muted", C.push_quiet_state(now=1000.0) == (False, ""))
_write_cfg({"push": {"quiet_until": 2000}})
check("quiet: future quiet_until -> muted with remainder",
      C.push_quiet_state(now=1000.0) == (True, "17m left"))
check("quiet: past quiet_until -> not muted",
      C.push_quiet_state(now=3000.0) == (False, ""))
_write_cfg({"push": {"quiet_until": True}})
check("quiet: boolean quiet_until ignored (strict number)",
      C.push_quiet_state(now=0.0) == (False, ""))
_write_cfg({"push": {"quiet_until": float("inf")}})
check("quiet: non-finite quiet_until ignored (LOW-1: Infinity must not mute forever)",
      C.push_quiet_state(now=1000.0) == (False, ""))
# >24h out = a hand edit (the UI writes <=1h) and is IGNORED outright (LOW-2).
# Not min()-capped: a rolling cap re-extends from every poll's `now` and would
# never expire — exactly the permanent mute the finding was about.
_write_cfg({"push": {"quiet_until": 10_000_000.0}})
check("quiet: far-future quiet_until ignored entirely (LOW-2: no rolling cap)",
      C.push_quiet_state(now=1000.0) == (False, ""))
_write_cfg({"push": {"quiet_until": 1000.0 + 7200.0}})
check("quiet: in-window long mute still honored (2h -> 120m left)",
      C.push_quiet_state(now=1000.0) == (True, "120m left"))
_write_cfg({"push": {"quiet_until_pitstop": True}})
check("quiet: until-pitstop flag -> muted with the label",
      C.push_quiet_state(now=1000.0) == (True, "until next pitstop"))
_write_settings(False)
check("quiet: until-pitstop ignored when no Stop hook is registered (LOW-2: "
      "nothing could ever confirm-clear it)",
      C.push_quiet_state(now=1000.0) == (False, ""))
_write_settings(True)
_write_cfg({"push": {"quiet_until_pitstop": "true"}})
check("quiet: string until-pitstop ignored (strict is-True)",
      C.push_quiet_state(now=1000.0) == (False, ""))

# set_push_quiet writes ONLY the two quiet keys — launch switches + the per-push
# config survive byte-for-byte in value terms.
_write_cfg({"remote_control": True, "auto_mode": True, "full_auto": True,
            "push": {"nudge": {"enabled": True, "message": "m"}}})
check("set: happy path reports success", C.set_push_quiet(seconds=900, now=1000.0) is True)
_after = _read_cfg()
check("set: quiet_until lands at now+seconds", _after["push"]["quiet_until"] == 1900.0)
check("set: until-pitstop defaults off", _after["push"]["quiet_until_pitstop"] is False)
check("set: launch switches untouched",
      _after["remote_control"] is True and _after["auto_mode"] is True
      and _after["full_auto"] is True)
check("set: per-push config untouched",
      _after["push"]["nudge"] == {"enabled": True, "message": "m"})
C.set_push_quiet(until_pitstop=True, now=1000.0)
_after = _read_cfg()
check("set: until-pitstop mode clears the clock and sets the flag",
      _after["push"]["quiet_until"] == 0 and _after["push"]["quiet_until_pitstop"] is True)
check("quiet state reads the flag set_push_quiet wrote",
      C.push_quiet_state(now=99999.0) == (True, "until next pitstop"))
C.set_push_quiet(now=1000.0)
_after = _read_cfg()
check("set: no args = unmute now",
      _after["push"]["quiet_until"] == 0 and _after["push"]["quiet_until_pitstop"] is False)
check("quiet state confirms the unmute", C.push_quiet_state(now=1000.0) == (False, ""))

# MEDIUM-1 (review 2026-06-12): a present-but-unparseable config must be REFUSED,
# never rebuilt from scratch — the bell's contract is "touch only the two quiet
# keys", and clobbering would silently erase the launch switches + push messages.
with open(C.PITSTOP_CONFIG, "w", encoding="utf-8") as fh:
    fh.write('{"remote_control": true,,}')
with open(C.PITSTOP_CONFIG, encoding="utf-8") as fh:
    _broken = fh.read()
check("set: unparseable config -> write refused", C.set_push_quiet(seconds=900) is False)
with open(C.PITSTOP_CONFIG, encoding="utf-8") as fh:
    check("set: unparseable config left byte-untouched", fh.read() == _broken)
_write_cfg(["parses", "but", "not", "a", "dict"])
check("set: non-dict config -> write refused", C.set_push_quiet(seconds=900) is False)
os.remove(C.PITSTOP_CONFIG)
check("set: MISSING file is the one rebuild-from-scratch case",
      C.set_push_quiet(seconds=900, now=1000.0) is True
      and _read_cfg()["push"]["quiet_until"] == 1900.0)

C.PITSTOP_CONFIG = _real_cfg
C.CLAUDE_SETTINGS = _real_settings


# ---- report ----------------------------------------------------------------
fails = [n for n, ok in CASES if not ok]
for n, ok in CASES:
    print(f"  [{'PASS' if ok else 'FAIL'}] {n}")
print(f"\n{len(CASES) - len(fails)}/{len(CASES)} passed")
sys.exit(1 if fails else 0)
