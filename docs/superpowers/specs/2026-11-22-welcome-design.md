# Day one for anyone: welcome flow, any resume, benefits opt-in, Mac/Windows

Design, 2026-11-22. Approved by Harun ("yes do that") after the install walk as a stranger (Docmost "Install walk as a
stranger (2026-11-22)"). Builds on PR #38 (day-one fixes).

## Why
A stranger can install and get as far as the AI sign-in, then stalls. Setup is one 5,500 px page. Only a Word
resume in one exact shape works for tailoring, and the app assumes everyone claims unemployment. Goal: a new person
reaches their first prepared application and morning brief without help.

## Decisions
1. **Welcome flow** at `/welcome?step=1..5`, opened after account creation and on a fresh install instead of `/setup`:
   1 resume, 2 about you (name, email, city; "Are you claiming unemployment?" and the state), 3 roles (titles, US
   only), 4 connect your AI (ChatGPT device code first, then a Claude token with the install command), 5 email
   (optional, step-by-step app-password help). Then a done screen with **Run now** and the dashboard. Each step posts
   to the existing `/setup/*` route with `next=/welcome?step=N+1`; there are no new save paths. The Setup page stays
   under Settings for everything else.
2. **Any resume works.** A PDF alone is accepted. A resume (Word or PDF) that doesn't fit the shape tailoring needs
   (`docs.outline` raises `SpecError`) is rebuilt once by the AI into that shape (`autoapply/resume_rebuild.py`):
   - The original is kept as `… - Original.<ext>`.
   - Every number in the rebuild must appear in the original text; anything else is refused, so nothing gets
     invented.
   - The person sees "We rebuilt your resume in the format tailoring uses. Check it" with the file, and confirms
     it.
   - It runs at upload when an AI is signed in, else right after the AI step, and as the first step of the daily
     run.
   - It uses the person's own agent: `claude -p` (`oneshot.ask_claude`), or Codex (`classify._ask_codex`).
3. **Unemployment is opt-in.**
   - **No:** `work_search: {claiming: false, weekly_minimum: 0}`. The Work search nav item is hidden, and the brief
     subject leaves out the work-search count.
   - **Yes:** a state is required; `weekly_minimum` comes from the state's rule (0 when the state has no fixed
     number), and the state is saved.
   - Existing installs without `claiming` are treated as claiming (no change for Harun).
4. **Mac and Windows.** `docs/setup.md` gets "On a Mac or Windows PC" (Docker Desktop: install, open it so the whale
   icon is running, then the same two commands; keep the computer awake at 6:30, or press Run now any time). The
   README Quick start says the same in one line.

## Not now
Outlook/Hotmail mail, a one-click Mac installer, a hosted version.

## Verification
- Unit tests for the rebuild (shape, number guard, original kept).
- Web tests for each welcome step's next-redirect, opt-in no and yes, and the nav and brief without claiming.
- The full suite.
- **A second stranger walk on llm**, from a fresh clone to Run now, with a Word resume in an ordinary format.
