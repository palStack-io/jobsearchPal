# Costs and limits

Everything runs on what you already pay for, plus optional pay-per-token sources, and the Settings
page shows both:

- **Daily caps:** roles to tailor per day (the heaviest step), interview prep packs per day and follow-up
  drafts per day, each of which can be switched off. The daily run reads them first (`autoapply list --limits`).
- **Monthly budget for paid APIs:** once Jev and OpenRouter have billed that much this month (sorting and,
  if it runs on OpenRouter, the daily run), sorting uses only your subscriptions and local models and a
  daily run on OpenRouter is skipped until the 1st.
- **Usage, last 30 days:** daily runs and tokens, what that would cost at API prices (a gauge of how heavy
  each step is, since your subscription covers it), and what OpenRouter actually billed, by day.

**Why the run is split into steps.** An agent session re-reads its whole conversation on every turn, so one
long morning session gets heavier with each step (one run used about 6.8M tokens, mostly these re-reads).
Each step now starts fresh, roles are pre-ranked in Python so the agent reads only the top 12 with trimmed
descriptions, and the light steps use a lighter model. Costs & limits shows tokens per step.

**Quick pick.** With an OpenRouter key, set *Quick pick* on Settings > Daily run to any cheap model
(e.g. `google/gemini-2.5-flash`). The day's roles are then picked in one chat call, usually well under a cent,
instead of an agent session. The same model reads yesterday's job-alert emails (LinkedIn, Indeed, HigherEdJobs)
and lists the roles that fit; a short light-model agent step then looks up only those on the company's own site.
It picks only among tracked roles, and falls back to the agent if a reply is bad or on the weekly discovery day. Tailoring and form
filling stay with the agent and its main model, since they need many tool turns and good writing.

To spend less: choose Sonnet or Haiku for the daily run, tailor one role a day, and keep email sorting on
Haiku or a local model.

**Skill matching (optional, off by default).** The built-in ranking only counts words your resume shares with a
posting, so "Looker" and "LookML" or "BI" and "business intelligence" never meet. Turn on skill matching
(Settings > Daily run) and a model reads each new role's must-have and nice-to-have skills and checks them against
your own editable skills list (with synonyms; it can draft the list from your resume once). Roles that need what you
have rank higher, and the dashboard shows "4 of 5 must-haves · missing Looker". It costs tokens on every role it
reads, and the Settings page says how many before you turn it on. Measured: 8 roles in one call took about 26,000
tokens through Claude Code (most of it Claude Code's own overhead); a small OpenRouter model reads 25 roles a day for
roughly 10 cents a month.

**The board:** a star puts a lead on your board and costs nothing; only **Find it on their site** on a board card
sends it to the paid lookup, once (on one real install each lookup step averaged about 210,000 tokens). A **learning plan** on the Skills page is one short request, only when you press it: about 6,000 tokens on
OpenRouter (skipped once the monthly budget is reached, then it uses your Claude subscription), about 20,000 through
Claude Code, most of that Claude Code's own overhead.

**Tailor in one call (Settings > Daily run > Advanced, off by default).** An apply step run as an agent session
re-reads its whole context on every turn: on one real install that was 360,000 to 780,000 tokens a role over 20 to
35 turns, about 90% of it re-reads. With this on, the app gathers the posting, your base resume, your notes
(CLAUDE.local.md, materials/context, voice.md) and the form's questions first, and one request writes the resume,
the cover letter, the answers and notes.md; the app then builds the files and fills the form as before. Measured on
the same install: about 20,000 tokens and 90 seconds for a full role, about 12,000 when the kit already exists. At
most two short follow-up requests (a resume still over two pages, a required question left blank). Consent and
self-ID questions, and the ones your profile answers, never reach the model. It runs on your Claude subscription;
roles from a job feed, forms on other sites and anything that fails go to the agent, as before.
