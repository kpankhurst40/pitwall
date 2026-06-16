# Pitwall fact manifest — every factual claim the app makes, and where to check it

Purpose: the app gives advice and shows prices. Facts go stale silently (the
"Opus is 5–10x Sonnet" tip shipped wrong for months before the 2026-06-12
audit caught it). This file lists every fact-bearing string, its source of
truth, and when it was last verified.

**Ritual:** walk this table top to bottom, check each claim against its
source, update LAST VERIFIED — **then re-stamp `TIPS_VERIFIED` in
`ts_core.py` (the "accurate as of" date shown in Settings under Rotating
tips)**. Runs (1) as a release gate before every packaged release, and
(2) whenever Anthropic ships a pricing or model-lineup change. Takes ~10
minutes. The user-facing disclaimer ("Anthropic can change their policy at
any time. These tips are accurate as of <date>") is honest only while this
ritual actually runs — never bump the date without the walk.

| # | Fact in the app | Where in code | Source of truth | Last verified |
|---|---|---|---|---|
| 1 | Model list prices (Fable $10/$50, Opus $5/$25, Sonnet $3/$15, Haiku $1/$5 per 1M tokens; cache write 1.25x 5-min / 2x 1-hr; cache read 0.1x) | `ts_core.py` `RATES` (~145) | platform.claude.com/docs/en/pricing | 2026-06-13 |
| 2 | Plan ceilings (Free $2, Pro $18, Max 5x $35, Max 20x $140 per window) | `ts_core.py` `PLANS` (~154) | Anthropic plan documentation / observed /usage behavior | 2026-06-12 (unchanged; re-check at each release) |
| 3 | Relative model cost in the rotating tip ("bigger models cost more") | `ts_core.py` `TIPS` (model tip) | Derives from #1 — stays true as long as price ORDER holds | 2026-06-13 |
| 4 | "Every reply re-reads the whole conversation" (tips + Token Details primer) | `ts_core.py` TIPS 1/19, `pitwall_qt.py` primer | API is stateless — platform.claude.com/docs (Messages API) | 2026-06-13 |
| 5 | /clear wipes the conversation without closing the session | `ts_core.py` TIPS | Claude Code docs (slash commands) | 2026-06-12 |
| 6 | Caching: share a big file once at the start (prefix-match caching) | `ts_core.py` TIPS | platform.claude.com/docs (prompt caching) | 2026-06-13 |
| 7 | A token ≈ ¾ of a word; 1M tokens ≈ 750k words ≈ ~4 MB | `pitwall_qt.py` "What's a token?" primer (~2813) | Anthropic token approximation guidance | 2026-06-12 |
| 8 | Model names offered/colored in the UI (Fable, Opus, Sonnet, Haiku) | `ts_core.py` `MODEL_COLORS`, `MODEL_LABEL` | Current model lineup — platform.claude.com/docs (models overview) | 2026-06-13 |
| 9 | Auto-sync default cadence claim ("about every 30 min; 10 when Fable is active") | Settings copy (AUTO-SYNC help) | Must match the actual default in code (`resync` default) | 2026-06-12 |
| 10 | Weekly-limit reset wording ("resets <date>") | card labels | Mirrors Claude's own /usage panel — verify panel wording unchanged | 2026-06-12 |

Checking method note (model routing): items where the source explicitly
states the fact (prices, model list) are cheap-model work; "has the behavior
changed?" judgment calls (e.g. #4, #6 semantics) go to the lead. The lead
verifies either way before the release gate passes.
