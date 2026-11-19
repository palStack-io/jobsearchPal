# Leads board, calmer dashboard, Your week, Stats tabs, Skills plan, fewer tokens (2026-11-18)

Approved by Harun on 2026-11-18 from the mockup canvas (claude.ai artifact UXfXdW75uPUfYrRTzryYxS): "the mockup looks good",
"yes" to the four board columns, "no" to counting a reached person toward the work-search log, "get started building".

## Why
The dashboard is ~2,700 px of nine cards and repeats Stats (applications per week, four stat tiles, the full
applications table). Stats is 13 sections in one 3,100 px scroll. Skills is a list of buttons with no way to act on a
gap. There is nowhere to *work* a handful of chosen roles step by step. And tokens: in the last 30 days the apply step
averaged ~830K tokens per role (25.7M of ~39M), and every starred lead started a paid agent lookup (~213K a run).

## Goals
1. **Leads board** (a separate planning board, not the application tracker). Leads gets three views:
   Inbox (today's list), Board, Applied (the applications table that leaves the dashboard).
   Columns: Shortlist, Research, Reach out, Ready to apply. "I applied" on a card logs the application (the existing
   applied-elsewhere path) and the card leaves the board for Applied.
   - Only starred leads (plus a hand "Add to board" on a dashboard shortlist role or a pasted post) come onto the board.
     Unstar or Remove sends it back to Inbox.
   - Each card: company, title, fit score, pay/location, next step + due day, notes, five steps (read the posting, look up
     the team, find one person, prep files, apply). Ticking steps is free; Prep uses credits as today.
   - A reached person is **not** counted toward the work-search log (Harun, 2026-11-18). Contacts keep their own switch.
2. **Dashboard:** keep the horizon banner exactly (sun, greeting, celebration, market line, rejection note). Remove the
   four stat tiles, Applications per week and the Applications table. Add a compact **Your week** panel (day strip,
   one warm line from the record, three thin tallies, one place to start) and a one-line **On your board** card.
3. **Your week page (/plan):** the day picker becomes a row of chips (it renders as a raw fieldset today), fields in a
   tidy grid, this week's strip at the top.
4. **Stats:** four tabs (Overview, Pipeline, Sources, Work search), each about one screen. Overview opens with one plain
   sentence on what stands out, computed in Python, then four numbers, the weekly chart and the funnel.
5. **Skills:** pick up to three gaps and get a four-week learning plan from one model call, saved and reused (never
   re-run unless asked). Links come from the curated list in `learn.SKILLS`, never from the model.
6. **Fewer tokens:** a star no longer queues a paid agent lookup. The free company-site lookup (`leads.resolve`, no AI)
   still runs; the paid lookup runs only for a card where the person presses "Find it on their site".
   The apply-step rework (one call with pre-packed context instead of a long agent session) is the next PR; this
   design records it, the build here only does the star change.

## Non-goals / anti-goals
- The board never changes the work-search count, never nags, has no streaks, never counts up numbers.
- No new AI on page load. The Skills plan is one call on a button press, cached in `pal_notes`.
- No emoji, no glyph icons (★ ✓ ↗), no tracked caps labels, no weights over 700, no half-pixel sizes on new UI.

## Look and motion (house design standard + VOICE.md)
Figtree; sizes 12 13 14 16 18 22 28 34; weights 400/600/700. One filled button per region. Line icons inline.
jobsearchPal's own motif carries the identity: sun dots for card progress, a shoreline for "Applied", water rising in
the week strip, a sun at the end of the learning path. Motion uses `--ease-out cubic-bezier(.23,1,.32,1)`, plays once,
and `prefers-reduced-motion` leaves only fades. Copy follows VOICE.md: credit from the record, one next thing, the
weekly minimum as a checklist.

## Data
- `board` table: `key` (lead key, primary), `col` (0-3), `steps` (bitmask of 5), `next_step`, `due`, `notes`,
  `added_at`, `moved_at`. A row exists only while the lead is on the board.
- `leads.want_lookup` (int, default 0): set by "Find it on their site"; `starred_for_lookup` reads it instead of the star.
- Skills plan: `pal_notes` key `learn:plan` = {skills, weeks:[{title, body, links:[name,url]}], made_at, model}.

## Approaches considered
- Board as the single pipeline (lead to offer): rejected by Harun; he wants a separate planning board.
- Board state on the `leads` row (new columns): rejected; a separate table keeps Inbox queries unchanged and lets a
  card leave cleanly.
- Skills plan with model-written links: rejected; links come from the checked list so nothing is invented.

## Left to the builder
Exact copy for empty states (from the VOICE phrase bank), phone layout of the board (columns stack, each scrolls),
the Overview sentence's rules.

## Next PR: the apply step in one call (measured 2026-11-18, not built here)
Live install, last 12 apply runs: 360K-780K tokens each, ~20-35 turns, and **about 90% of the tokens are cache
reads**: the same ~25K-token context re-read on every turn. New input (cache write) is only ~35-48K and output ~7-12K.
So the cost is turns, not content. Plan:
1. Python gathers everything up front, no model: `show` (the posting), `resume-outline`, materials/context,
   CLAUDE.local.md, the voice notes, and `scan` of the form (browser, no AI).
2. **One** model call returns `docs.json` + the plan's answers as JSON (same shapes as today).
3. Python runs `build-docs` and `fill`, and re-asks once only if a check fails.
Expected: ~50-60K tokens per role instead of ~600K, and none of the turn-by-turn failure points. The agent session
stays as the fallback for roles that need the web (feed postings to find on the company site, unusual forms).
Also found: 2 of today's 4 apply runs stopped on "needed permission for autoapply scan <url>": the link held an
unquoted `&`, which splits the shell command. Fixed in this PR by telling the agent to quote links (apply.md,
AGENTS.md); the one-call design removes the shell step entirely.
