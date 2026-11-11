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
