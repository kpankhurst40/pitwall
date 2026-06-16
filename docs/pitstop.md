# Pitstop — save & restart, explained

The longer a Claude Code session runs, the more it costs to continue: every reply
re-reads the **entire conversation so far**. At some point it is genuinely cheaper to
**save your place, start a fresh session, and bring that memory straight back** — so the
new session picks up exactly where you left off, without the heavy pile of past
conversation that every reply was paying to re-read. You keep the thread; you drop the
weight.

That move is the **pitstop**, and it's the one habit Pitwall is built around.

> **You always press the keys.** Pitwall never types into your session, runs a command,
> or resets anything on its own. It shows you *when* a pitstop is worth taking and makes
> the steps one tap to copy — but the reset itself is your keystroke. That
> human-in-the-loop design is deliberate: it's what makes Pitwall safe to run against a
> live session.

## The ritual

When a session has grown heavy:

1. **Type `/pitstop`** in the session. Claude writes a short checkpoint — the goal, the
   exact next step, decisions already made, key files — to a small file on your own
   machine.
2. **Type `/clear`.** That's a true cost reset: the heavy conversation is cleared.
3. **The fresh session re-primes itself** from that checkpoint as background context, so
   it picks up exactly where you left off — light and cheap again.

The checkpoint is a single small file kept in your local app data
(`%LOCALAPPDATA%\Pitwall\handoff\`), **outside any repo**, and it's **deleted the moment
the fresh session reads it** — nothing lingers on disk.

> **Setup note (honest):** the auto re-prime ships in the repo's **`handoff/` folder** but
> isn't auto-installed yet — wiring it up is a short one-time manual step today (copy one
> command file, register one SessionStart hook; see
> [`handoff/README.md`](../handoff/README.md)). Auto-install from Settings is planned, not
> shipped — we won't pretend otherwise.

## The one-click pitstop (↻) — nothing to install

You don't have to set anything up to pitstop. Hover any open session row in the widget and
a **↻** appears. Click it and Pitwall:

1. Shows a short guide — how heavy that chat has grown, and **exactly what to type** to
   have Claude save your progress (with a one-click **Copy**).
2. On your OK, **opens a fresh terminal running Claude in the same project folder.**

You paste the saved summary into the new window and carry on. It works out of the box and
nothing runs without your click. *(Windows; on macOS/Linux it falls back gracefully.)*

## The nudge — an optional shoulder-tap that tells you *when*

Pitwall watches each session's token count. Turn on **"Tell me when a fresh session would
save money"** (⚙ gear) and, when a chat passes the point where a fresh start would
actually pay off, Pitwall taps you with a tip showing exactly what to do.

- **Default OFF.** It's pure advice — it shows the steps, it never performs them.
- A fresh session carries a small re-priming cost, so Pitwall stays quiet below the point
  where starting over actually saves you money.
- The threshold is yours to set (**⚙ gear → nudge at**). It accepts `3M`, `2.5M`, `300k`,
  or a plain number, and reminds you again every additional million tokens.
- Snooze a tap for 5m / 15m / 30m / 1h, or dismiss it to quiet it for 10 minutes
  (dismissing doesn't disarm the feature).

## Why there's no "do it all for me" button

A truly hands-free pitstop — one that spawns the new window and acts in it with no
permission prompts — would mean a file on disk could steer a fresh Claude session with no
human in the loop. On a shared or compromised machine that's a remote-code-execution risk,
so the shipped product **does not enable it**. Every pitstop here ends with *you* pressing
the keys. That's the safe floor.

---

## ⚠️ The experimental hands-free path — DANGER, do not use

The development toolchain contains a fully hands-free variant: a pitstop that opens the
replacement window **and runs it with every permission prompt turned off**, acting on a
file from disk with no human in the loop. It is
**deliberately not enabled in the shipped product, and it is not supported.** This section
exists for honesty and education only.

**Why it is genuinely dangerous.** A session running with all prompts off will do whatever
the resume file on disk tells it to — edit or delete files, run commands, send data out —
**with nobody asked and nobody watching.** Anything that can write that file (other
software on the machine, another user, malware, a mistake — or content that flowed into the
checkpoint from something Claude read) can therefore make Claude do those things silently. You cannot meaningfully "accept" this risk with a checkbox, because
the harm can be introduced *after* you click — by something that isn't you.

**The only context in which anyone should ever even experiment with it:** on a throwaway,
isolated virtual machine you fully control and don't care about losing, with nothing
sensitive on it and no network access you'd regret, while you sit and watch it the whole
time with your finger on the kill switch — purely to learn how it behaves, accepting
**100% of the responsibility and the consequences yourself.** On any real machine, on any
shared machine, or unattended: **don't.** There is no safe way to do this casually, and we
won't pretend there is — if you go there, you go entirely on your own volition, and you
should expect that something can go wrong.

(There are no enable-it instructions here on purpose. If you're equipped to do this safely,
you're equipped to read the toolchain source and understand exactly what you're turning on
first.)
