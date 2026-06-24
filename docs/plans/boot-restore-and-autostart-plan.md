# Plan — Reopen open Claude sessions after a reboot (+ Start-on-boot, + a small Pitwall tidy)

**Owner:** Claude (lead) · **Date:** 2026-06-24 · **Status:** PLAN — awaiting Kevin's go
**Feature home:** Pitwall (the always-on widget) · **Reuses:** The Grid's existing launcher

---

## What Kevin asked for, in plain English

> "If Windows reboots, I want all the Claude CLIs that were open to come back automatically —
> with their memory — so I pick up where I left off."

Plus, decided in this session:
- **Fully automatic** restore (no "Restore?" prompt — they just come back).
- **Same conversation** comes back (not a fresh session), via Claude Code's `--resume`.
- **Auto-checkpointing** added, so even a *surprise* reboot (3am Windows Update) loses almost nothing.
- A **"Start on boot" toggle for BOTH** Pitwall and The Grid.
- While we're in the Pitwall code anyway: **move the "sync now" button** off the weekly line and
  onto the 5-hour line.

---

## The one idea that makes this simple

Your conversations are **already saved to disk as you work** (`~/.claude/projects/<proj>/<sid>.jsonl`),
and those files **survive a reboot**. Claude Code can reopen the exact conversation with
`--resume <session-id>`. So we are NOT trying to "freeze and thaw" live memory — we're just:

1. **remembering which sessions were open** (a small list on disk), and
2. **at login, telling each one to come back** with `--resume`.

Two jobs, and each lands in an app that already does half of it:

| Job | Needs | Lives in |
|---|---|---|
| **Record** what's open, continuously | something always running | **Pitwall** (already polls the live session registry every cycle) |
| **Relaunch** with `--resume` | a reviewed session launcher | **The Grid's `grid_launch.ps1`** (already does Heavy/`--resume`) |

**Decision (this session):** Pitwall *owns the feature* (record → checkpoint → restore), but it
**reuses The Grid's existing `grid_launch.ps1`** to do the actual spawning — no duplicate launcher.
On boot, Pitwall first **makes sure The Grid is up** (Kevin's call 2026-06-24): if The Grid isn't
already running, Pitwall launches it, *then* relaunches the CLIs — so the cockpit is on screen when
the sessions come back. This fits Pitwall's standing boundary ("monitors + **controls** + notifies,
never holds a CLI conversation" — restarting a session is *control*, it never touches the
conversation).

**Tradeoff Kevin accepted:** Pitwall (a possibly-OSS cost widget) gains a workspace feature. We keep
it as a **local module in the PRIVATE `pitwall` repo**, never in the public `claude-token-steward`.

---

## The pieces (each tagged with the model that should build it)

### Phase 0 — Pitwall tidy (bundled because we're already in `pitwall_qt.py`)

- **P0.1 `[Sonnet]` + Sarah review — Move "sync now" to the 5-hour line.**
  Today the `sync now` control (`self.sync_link`) sits in `_build_blockB`, on the weekly freshness
  row (`srow`, with the "as of 11:40 AM" stamp `self.lsync`). Move it up to **Block A**, under the
  5-hour barline (`self.proj`, in `_build_blockA`).
  *Open design point for Sarah:* does the freshness stamp ("as of …") travel **with** the button, or
  does only the button move and the stamp stay by the weekly line? Recommend they stay **together**
  under the 5-hour line — the synced number drives *both* bars, so its freshness reads best next to
  the top one. Sarah confirms placement/alignment before it ships.

### Phase 1 — The recorder (Pitwall)

- **P1.1 `[Sonnet]` — Persist an "open sessions" snapshot.**
  Each Pitwall poll, write `open_sessions.json` to `%LOCALAPPDATA%\Pitwall\` (outside any repo, like
  the existing handoff checkpoint). Contents are **inert data only**: a list of
  `{work_dir, session_id, last_active}`. Source = the existing `ts_core.live_registry()` /
  `open_session_dirs()` — no new watching, just persisting what Pitwall already sees.
- **P1.2 `[Sonnet]` — Prune cleanly-closed sessions** so the snapshot reflects what was *genuinely*
  still open, not everything ever seen.

### Phase 2 — "Start on boot" toggles (BOTH apps)

**Autostart mechanism — where the toggle writes (DECIDED 2026-06-24):**
Use **`HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`** — per-user, **no admin /
no UAC prompt**, toggled silently from a checkbox. **NOT `HKEY_LOCAL_MACHINE\…\Run`** — HKLM needs
admin elevation, applies to *every* user on the PC (not just Kevin), and is a sensitive
malware-persistence hive Ivan would flag. (Equally valid alt: a shortcut in the Startup folder,
`shell:startup` — same effect, visible in Task Manager's Startup tab. We default to HKCU\Run for
clean programmatic toggling; switchable to the Startup folder if Kevin prefers to *see* it as a file.)

- **P2.1 `[Sonnet]` — Pitwall Settings: "Start Pitwall when I log in."** Toggles the HKCU\Run entry.
- **P2.2 `[Sonnet]` — Pitwall Settings: "Reopen my Claude sessions on boot"** (the restore behavior).
  **Default ON** per Kevin's "fully automatic" choice. Kept as its OWN switch so Pitwall can start at
  login *without* auto-reopening terminals if he ever wants that.
- **P2.3 `[Sonnet]` — The Grid Settings: "Start The Grid when I log in."** Same HKCU\Run mechanism.
  **Default OFF** (Kevin's call 2026-06-24) — independent convenience, separate from restore. Note
  this is NOT required for session-restore: Pitwall ensures The Grid is up at boot regardless (P3.1).

### Phase 3 — Restore on boot (Pitwall orchestrates, reuses The Grid's launcher) — **Ivan gate**

- **P3.1 `[Opus]` — At Pitwall startup, restore.** If P2.2 is on:
  1. **Ensure The Grid is up first** — check whether The Grid is already running (process-image check,
     not just any pid — per the 2026-06-11 pid-recycling lesson); if not, launch it and let it come up.
  2. Read `open_sessions.json` and for each entry invoke The Grid's reviewed `grid_launch.ps1` in
     **Heavy / `--resume <id>`** mode.
  **Fire ALL sessions at once — no cap, no stagger** (Kevin's call 2026-06-24).
- **P3.2 `[Opus]` — "Is this actually a fresh boot?"** Only restore on the *first* Pitwall start after
  a reboot — never every time Pitwall is reopened during the day. Gate on a once-per-boot marker
  (system boot id / uptime). Without this, closing+reopening Pitwall at noon would re-spawn the world.

### Phase 4 — Auto-checkpointing — **DROPPED 2026-06-24 (redundant given `--resume`)**

Re-evaluated during the build: because the restore uses **`--resume` (same conversation)**, Claude
Code's full transcript — written to disk continuously and untouched by a reboot — IS the memory.
`--resume` reloads it verbatim, so a surprise reboot loses only an unflushed in-flight turn (seconds),
not your place. A periodic checkpoint only helps the *cheaper "fresh + summary"* restore path, which
Kevin did not pick. Building it would add complex, Ivan-gated machinery for ~zero gain on the chosen
path. **Decision: drop it** (simplicity-first; can revisit only if we ever switch to the fresh-start
restore). External note: Pitwall can't author a session's "where I left off" summary from outside
anyway — only the live agent can (via `/pitstop`); that's the existing Nudge/Let-it-ride lane, not
this feature.

---

## Security — why Ivan reviews this before it ships (non-negotiable)

This creates a **new path where logging in auto-spawns Claude sessions from a disk file**. Two of our
own hard-won lessons govern it:

1. *"auto-executed instruction file + bypass = silent remote-code-execution."*
   → The snapshot carries **only inert data** (dir + session id). Launches go through the **fixed,
   reviewed `grid_launch.ps1`** with a **minimum permission allowlist — never `bypassPermissions`.**
2. *"a spawned session inherits EVERY standing automation — walk each one"* (the 2026-06-11
   worker/pitstop incident, where an auto-spawned session tripped the token nudge → full_auto →
   a rogue pitstop).
   → Before ship, walk **every** standing automation (token nudge, full_auto, pitstop hooks) for the
   auto-restored sessions and decide explicitly whether each applies — structurally suppress the ones
   that shouldn't (env mark + hook check), exactly as `dispatch_worker` marks `PITSTOP_WORKER`.

Ivan reviews Phases 3 and 4 as a unit (the auto-spawn path) before either ships.

---

## Ship order & verification

Build and **live-verify with Kevin after each phase** (silence = fine; he flags only what's off):

1. **Phase 0** — quick UI bundle (Sarah eyeball).
2. **Phase 1** — recorder (nothing visible yet; just starts logging the open list).
3. **Phase 2** — the three toggles (visible, testable immediately).
4. **Phase 3** — restore — **Ivan review gate** → then a real reboot test.
5. **Phase 4** — auto-checkpoint — **Ivan review gate**.

## Cross-repo footprint

- **`pitwall` repo (PRIVATE):** the whole feature (P0, P1, P2.1, P2.2, P3, P4).
- **`the-grid` repo (PRIVATE):** P2.3 only (one Settings toggle). Its `grid_launch.ps1` is **reused
  read-only** — not modified.
- **No public-repo changes.** Runtime files (`open_sessions.json`, boot marker) are per-machine state
  under `%LOCALAPPDATA%\Pitwall\`, never committed.

---

## Decisions locked (2026-06-24)

1. **Window cap on restore — NONE. Fire ALL sessions at once** regardless of count (Kevin's call).
2. **Auto-checkpoint cadence — ~5 min** (Kevin OK'd the default).
3. **Restore routing — Pitwall ensures The Grid is visible, then Pitwall ITSELF fires each
   `--resume` via `grid_launch.ps1`** (not routed through the Grid process). Confirmed by Kevin.
4. **Autostart hive — `HKCU\…\Run`** (never HKLM). See Phase 2.
5. **The Grid start-on-login — default OFF**, separate from restore.
6. **Bypass-at-boot — ACCEPTED by Kevin 2026-06-24** (Ivan condition A). Restored sessions
   launch at `bypassPermissions --remote-control` (same posture as manual Grid launches): idle
   until typed locally, but phone-drivable the instant they register. Kevin consciously accepts.
7. **~1s stagger between launches — KEPT** (Ivan condition C / herd fix). Still reopens every
   session immediately, just not in the same tick. Idempotent loop added (Ivan condition D).

**Review status:** Sarah PASS (UI nits applied). Ivan SHIP-WITH-CONDITIONS — all conditions met:
A accepted by Kevin (above), C+D fixed in code. Security log: `docs/plans/security-review-log.md`.
NOT yet committed (Kevin eyeballs first) and NOT yet live-verified.
