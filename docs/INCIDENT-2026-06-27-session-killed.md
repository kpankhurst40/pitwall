# Incident — Pitwall force-killed every open Claude session (2026-06-27)

**Status: RESOLVED & GUARDED.** Fix shipped, Ivan PASS×2, regression test in place.
**Severity: HIGH** — it cost Kevin all his in-progress CLI windows, twice.

This is the **one-stop writeup** of the whole episode — what happened, what we found, and
what was done. If you're a Pitwall session picking this up, start here; the deeper docs are
linked at the bottom.

---

## 1. What happened (plain English)

Kevin had a dozen Claude Code sessions open. Around **17:28 on 2026-06-27**, the session that
was busy grading plugins (and, on 2026-06-25, the Pitwall + Grid widgets) **vanished with no
warning** — no crash dialog, no error, just gone.

The culprit was **Pitwall's own usage-sync**. To read your token numbers, Pitwall quietly
launches a hidden `claude /usage` in the background, reads the figures, then cleans up after
itself by **killing "its own process tree."** The cleanup is what went wrong: it killed
**five completely unrelated, live Claude sessions** that were never part of its tree.

In short: a routine background reading was reaching out and killing Kevin's real work.

## 2. Why it happened (the root cause)

It comes down to three facts about how Windows tracks processes:

1. **Windows recycles process IDs (pids).** When a process dies, its ID number is quickly
   handed to a brand-new, unrelated process.
2. **Windows never updates a child's record of "who my parent is" when the parent dies.** The
   child is left pointing at a pid that's now free — and may now belong to something else.
3. **Claude sessions spawn deep chains** (`cmd → node → cmd → node`) whose short-lived links
   keep dying and freeing pids, leaving live "grandchild" processes orphaned.

Pitwall found "its tree" by following the parent→child links from the pid it had just
spawned. But that spawn got a **recycled pid** that several of Kevin's orphaned sessions still
*recorded as their parent*. So the cleanup walked straight into those strangers and killed
them. There was **no check that the processes were actually the ones Pitwall started** — a pid
number alone was trusted, and a pid number is a lie waiting to happen.

This wasn't guessed — it was **reproduced**: feeding the old cleanup one real
dead-but-still-referenced pid from the live machine swept in 5 unrelated live processes.

## 3. How we proved it (and ruled out the alternatives)

From this machine's Windows event logs around the time of death, we confirmed:

- ❌ **Not a crash** — no Application Error would-be record for node/claude existed.
- ❌ **Not power/sleep** — no Kernel-Power or dirty-shutdown events; the machine stayed up.
- ❌ **Not out-of-memory** — no resource-exhaustion event, despite the heavy task.

Windows recorded **no abnormal termination at all** — exactly the fingerprint of a process
that was **force-terminated from the outside** by another command. That, plus the
reproduction above, pinned it on the tree-kill.

## 4. What was done (the fix)

**On the Pitwall side — shipped:**

- The cleanup now proves **identity** before killing anything. A process is only "ours" if its
  **(pid + creation-time) pair** matches what we recorded — creation-time is fixed for the
  life of a process, so a recycled pid (different creation-time) is rejected.
- It also only adopts a process created **at/after** the instant Pitwall spawned (excludes
  every pre-existing session) and built along **monotonic edges** (a real child is always
  younger than its parent; a stale-link impostor is older → rejected, at any depth).
- **Fail-safe direction:** any uncertainty now *leaves a process alive* (at worst a leaked
  hidden helper), never mis-kills a stranger.
- **Where:** `scripts/read_usage.ps1` (`Build-Tree` / `Kill-Ours`).
- **Guarded by:** `tests/test_kill_tree_guard.ps1` (+ pytest wrapper) — it synthesizes a
  recycled-pid impostor and asserts it's excluded, so this bug bites loudly if it ever
  regresses.
- **Shipped:** commit `a34570f`, pushed. **Ivan reviewed twice — PASS**, no HIGH/CRITICAL,
  the founding Rule 4 invariant logged as restored. Verified live: a real capture returned
  correct numbers with **all 12 open CLIs surviving**.

**On the shared-tools side (`C:\dev\tools`, dev workspace) — audited CLEAN:**

- `pitstop_handoff.py` already uses the **gold-standard** method (pins the process with a
  handle and kills *through that same handle* — the verify and the kill can't refer to
  different processes).
- `cloudcheck.py` walks parents **read-only** (never kills).
- `dispatch_worker.py` has **no kill path** at all.
- No other tree-kill-by-pid code was found.

## 5. The durable lesson (already recorded)

> **A pid is a lie waiting to happen.** Never identify a process to kill by its pid alone, nor
> by "Claude pids that appeared since we launched." Verify the **(pid, creation-time) pair**,
> or — best of all — use a Win32 **Job Object** (kernel-accounted, recycle-immune, no graph
> walk). Build trust along monotonic parent→child edges, and design so uncertainty leaves a
> process *alive*, never kills a stranger.

Logged as a `[RULE]` in `C:\dev\pitwall\CLAUDE.md` (now one of the founding five rules).

## 6. Still open (for whoever continues this)

If you want to fully close the loop, the **wider workspace** hasn't been swept for the same
pattern beyond `C:\dev\tools`. Worth a read-only grep for the dangerous verbs
(`taskkill`, `Stop-Process`, `TerminateProcess`, `ParentProcessId`, `Win32_Process`,
`os.kill`, `.terminate(`, `.kill(`) across **The Grid's launch code** especially, since it
also spawns sessions. Hand any hits to a full-strength review. (Checklist in the handoff doc
below.)

---

## Where the deeper detail lives

- **Technical root-cause + fix-pattern handoff (for engineers auditing other tools):**
  `C:\dev\tools\PROCESS_KILL_PID_RECYCLE_HANDOFF.md`
- **The original investigation thread (dev ⇄ Pitwall coordination):**
  `C:\dev\pitwall\TEAM_NOTES.md` (2026-06-27 note)
- **The [RULE] / lesson:** `C:\dev\pitwall\CLAUDE.md` (Lessons Learned)
- **Ivan's security review log:** `C:\dev\pitwall\docs\plans\security-review-log.md`

*Written 2026-06-27 by Claude (dev-workspace lead) for the Pitwall project.*
