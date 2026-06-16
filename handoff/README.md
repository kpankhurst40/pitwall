# Pitwall hand-off — "Nudge me" (Mode 2)

The one-window hand-off that lets a long, expensive Claude Code session reset its
cost **without losing its place**. The user stays in control the whole time — Pitwall only
taps their shoulder; *they* press the keys.

```
 Pitwall taps  →  user types /pitstop  →  user types /clear  →  fresh session
 ("save now")      (agent writes the         (true cost reset)    re-primed from
                    checkpoint file)                               the checkpoint
```

This folder is the **engine** for that. It has two pieces:

| File | Role |
|---|---|
| `commands/pitstop.md` | The `/pitstop` slash command. Tells the agent to write a short checkpoint to a fixed file, then hand the user off to `/clear`. |
| `reprime_hook.py` | A Claude Code **SessionStart** hook. On the fresh session it reads that checkpoint and feeds it back as **background context** (never as an auto-instruction). One-shot: it **deletes** the checkpoint after reading, so it can't re-fire and no copy of the summary lingers on disk. |

The checkpoint lives **outside any repo**, at a fixed absolute path:

- Windows: `%LOCALAPPDATA%\Pitwall\handoff\checkpoint.json`
- Fallback: `~/.pitwall/handoff/checkpoint.json`

## Safety contract (Ivan's Mode-2 ruling, 2026-06-08)

The hook treats the checkpoint as an untrusted file on the path into a trusted
session, and is built to these guardrails (unit-tested in
`../tests/test_reprime_hook.py`):

- **G1** one fixed absolute path, outside the repo, read absolutely (never cwd-relative).
- **G2** fails safe: missing → inject nothing; malformed/unreadable → a visible
  "couldn't restore" note, never the bad body.
- **G3** hard 32 KiB cap; over-cap → fail-safe.
- **G4** inert prose only — it lifts a text summary and *nothing else*; any embedded
  "always allow…"/permission directive is ignored by construction.
- **G5** emits `additionalContext` **only — never `initialUserMessage`**. Passive
  context can't self-fire (proven by the self-start spike), so the next *action* is
  always a human keystroke. This is the whole reason Mode 2 is the safe floor.

The re-primed session keeps the **same** permission walls — the hand-off never widens
anything (a hook structurally can't).

## Install

> Not auto-installed yet — Pitwall will wire this up from its Settings panel in build #3.
> Until then, to try it by hand:

**1. Install the `/pitstop` command** (user-level, so it works in any project):

```
copy commands\pitstop.md  %USERPROFILE%\.claude\commands\pitstop.md
```

**2. Register the SessionStart hook** in `%USERPROFILE%\.claude\settings.json`
(merge into any existing `hooks` block; use the absolute path to this file):

```json
{
  "hooks": {
    "SessionStart": [
      { "matcher": "clear",   "hooks": [ { "type": "command", "command": "python \"C:\\path\\to\\repo\\handoff\\reprime_hook.py\"" } ] },
      { "matcher": "startup", "hooks": [ { "type": "command", "command": "python \"C:\\path\\to\\repo\\handoff\\reprime_hook.py\"" } ] },
      { "matcher": "resume",  "hooks": [ { "type": "command", "command": "python \"C:\\path\\to\\repo\\handoff\\reprime_hook.py\"" } ] }
    ]
  }
}
```

`clear` is the one that matters for the hand-off; `startup`/`resume` cover the case
where the user closes and reopens the terminal after a `/pitstop`. The one-shot delete
means it injects at most once per saved checkpoint regardless of how many fire.

## Test

```
python tests\test_reprime_hook.py     # pure-core guardrail tests (G1–G5)
```
