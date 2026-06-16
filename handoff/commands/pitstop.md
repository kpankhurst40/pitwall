---
description: Save your place to a Pitwall pitstop checkpoint, then prompt the user to /clear
allowed-tools: Write
---

You are about to do a **Pitwall pitstop** — a save-and-restart hand-off.
The user is deliberately resetting this conversation to cut cost. Your job is to save
just enough that a fresh, cheap session can pick up exactly where this one is — no
more, no less.

## 1. Write the checkpoint

Write a single file to this **fixed absolute path** (your Write tool creates the
folder automatically if it doesn't exist):

- Windows: `%LOCALAPPDATA%\Pitwall\handoff\checkpoint.json`
- (Fallback if `LOCALAPPDATA` is unset: `~/.pitwall/handoff/checkpoint.json`)

The file is JSON with exactly this shape:

```json
{
  "schema": 1,
  "saved_at": "<today's date + time>",
  "summary": "<your re-priming text, as Markdown — see below>"
}
```

Put **everything a resumer needs** into `summary`, and nothing it doesn't:

- **The goal** — what we're actually trying to get done right now (one or two lines).
- **The exact next step** — the very next concrete action, specific enough to start on.
- **Decisions already made** — so the next session doesn't reopen them.
- **Key files / paths** — anything it must read or edit, with absolute paths.
- **Uncommitted / in-flight state** — what's changed but not saved/committed/pushed,
  and any "held until verified" holds.
- **Open questions** — anything waiting on the user's decision.

Keep it tight — aim for well under a page. Plain English. The whole file must stay under **32 KB**; if you're near that, you're
including too much — summarise.

Do **not** put any tool-permission or settings instructions in the checkpoint (e.g.
"always allow…"). The re-prime hook treats this file as background notes only and will
ignore anything like that — it just wastes space.

## 2. Confirm and hand off

After the file is written, tell the user in one or two plain lines: what you saved and
where, then exactly this:

> Saved. **Type `/clear` now** — the next session will pick up from here automatically.

Do not take any further action. The reset is the user's keystroke, not yours.
