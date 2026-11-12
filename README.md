# jobsearchPal

Your job search, and the unemployment paperwork that comes with it.

A self-hosted assistant for people looking for work, especially while collecting unemployment. It runs on a
small always-on computer you own and does two jobs:

- **The job search.** Every weekday morning an AI agent on your own subscription
  ([Claude Code](https://claude.com/claude-code) or OpenAI's [Codex CLI](https://github.com/openai/codex)) finds new
  roles, picks the best fits, tailors your resume and cover letter, and fills in the real application form, PDFs
  included. **It never presses Submit.** You review each application on a private dashboard and send it yourself.
- **The unemployment paperwork.** It keeps your state's work-search log as you go (every application, recruiter
  contact and interview, with the original emails and submit screenshots as proof), knows your state's weekly
  rule, and reminds you about claim day, RESEA meetings and other deadlines. Rules for all 50 states, DC,
  Puerto Rico and the US Virgin Islands are built in.

It reads your job email (read-only), files every reply, reminds you who to follow up with and drafts the reply,
and writes an interview prep pack when an interview comes in.

jobsearchPal is part of [palStack.io](https://palstack.io), a family of privacy-first tools built to make everyday
life a little easier. Like the other pals, it runs on your own computer: no tracking, no ads, nothing sold.

![Dashboard](docs/dashboard.png)

<details><summary>Work-search log, job sources, stats, emails, settings and the phone layout</summary>

![Work-search log](docs/work-search.png)
![Leads and job sources](docs/leads.png)
![Skills to learn](docs/skills.png)
![Stats](docs/stats-dark.png)
![Emails](docs/emails.png)
![Settings](docs/settings.png)
<img src="docs/dashboard-phone.png" width="300" alt="Dashboard on a phone">
</details>

The screenshots use fictional demo data (`scripts/demo-data.py`; regenerate them with `scripts/screenshots.py`).

## At a glance

- **Runs on its own.** Cron (or the Docker scheduler) starts the run at 6:30am on weekdays on your server.
  Your laptop, Claude desktop and any chat app can be closed; they're only for setup and fixes.
- **Prepares, never sends.** Each morning a few applications are tailored and filled in, follow-up replies
  are drafted, and a brief arrives by email. You read them on the dashboard and press Submit yourself.
- **Sounds like you.** Pick how your cover letters read (tone, opening, length, words to avoid), and rewrite
  any one letter with a short note.
- **Your AI, your order.** Pick the models on the Settings page: your Claude or ChatGPT subscription first,
  then pay-per-token sources (Jev, any OpenRouter model) or a local Ollama model as backups.
- **Your data stays with you.** Everything lives in one folder on your server, behind your own sign-in.
  Nothing is sent anywhere except to the models you turn on.

## How it works

```
6:30am weekdays (cron or the Docker scheduler) ─► scripts/daily.sh, in short steps:
  no AI:     autoapply sync-email (a small model sorts the emails) · autoapply find (boards, job sites)
  light model, one fresh session each (prompts/stages/):
    ├─ prep      interviews ─► prep pack: reported questions with sources, your stories
    ├─ drafts    follow-up reminders ─► drafted replies
    └─ rank      autoapply candidates (pre-ranked in Python) + alert emails ─► today's picks
  main model, one fresh session per pick:
    └─ apply     tailor resume + cover letter ─► autoapply scan ─► plan.json ─► autoapply fill (stops before Submit)
  ─► autoapply brief --send      morning email: what's ready, new replies, where everything stands
you ─► dashboard ─► check answers, decide consents ─► Submit
   or: autoapply-finish <job> on your laptop ─► a real Chrome window, filled in, you submit
```

The details, step by step: [How it works](docs/how-it-works.md).

## What it will not do

- **Submit for you.** The CLI has no submit command. The only Submit button is on the dashboard, behind a review tick
  box.
- **Answer consents or self-ID questions.** Only your own standing answers are pre-filled, and highlighted for you to
  check on every application.
- **Get past bot checks.** No captcha solving, no reading verification codes, no logins, no account creation.
- **Workday and other account-based sites** get materials prepared and are marked "apply by hand".

More in [Privacy and permissions](docs/privacy-and-permissions.md).

## Quick start

You need an always-on Linux computer (Ubuntu 24.04 tested) and either a Claude subscription (for Claude Code) or a
ChatGPT subscription (for the Codex CLI). With Docker:

```
git clone https://github.com/palStack-io/jobsearchPal.git ~/auto-apply && cd ~/auto-apply
docker compose up -d                        # dashboard + the daily scheduler (pulls the published image)
# then sign in to Claude or ChatGPT on the dashboard's Setup page (or: docker compose run --rm web claude)
```

Open `http://localhost:8787/`, create your account, and the **Setup** page walks you through the rest (resume, job
interests, email). The full guide, including a plain install without Docker and reaching the dashboard from your
phone, is in [docs/setup.md](docs/setup.md).

## Documentation

| Guide | What is in it |
|---|---|
| [Setup](docs/setup.md) | install on a Linux server or with Docker, the Setup page, updating |
| [How it works](docs/how-it-works.md) | the morning run step by step, where jobs come from, email sorting, interview prep, follow-up reminders |
| [The dashboard and the pal](docs/dashboard.md) | every page, the Settings tabs, the pal's voice, what helps a search |
| [Unemployment help](docs/unemployment.md) | the work-search log, your state's rules, claim and RESEA reminders |
| [Costs and limits](docs/costs-and-limits.md) | daily caps, the monthly budget, Quick pick, skill matching |
| [Privacy and permissions](docs/privacy-and-permissions.md) | what it will not do, and what each piece you turn on can reach |
| [FAQ](docs/faq.md) | common questions |
| [Development](docs/development.md) | running it locally, commands, code layout, contributing |

## Status and limits

> **Rather not run a server?** We're looking into a hosted jobsearchPal, if there's interest. Tell us at
> [support@palstack.io](mailto:support@palstack.io?subject=jobsearchPal%20hosting) or on
> [Discord](https://discord.gg/mcgcp7RdYs).

jobsearchPal is young: it's used daily by its author and has been tested from a clean Docker install, but expect
rough edges. Known limits today:

- **You need a small always-on computer** (a home server, a mini PC, a cheap cloud VM) and are comfortable running a
  few terminal commands once. Day to day, everything happens in the browser.
- **Email is Gmail, iCloud or Yahoo** (an app password, read-only). Outlook.com and Hotmail need forwarding. Off Gmail,
  the recruiter search uses plain keywords, so it is a little broader.
- **US-focused.** Job filters lean US, and the unemployment rules cover US states and territories.
- **Some sites need you.** Workday and other account-based sites get materials prepared and are marked "apply by
  hand"; captchas and emailed codes are finished by you (`autoapply-finish` opens the filled form in Chrome).
- **AI usage is real.** A full morning run is roughly 1-2 million tokens on your subscription; caps and a monthly
  budget for paid APIs are on Settings > Costs & limits.

## Contributing

Issues and pull requests are welcome, especially: corrections to a state's rules (with the agency link), new ATS
form fillers, other email providers, and reply-sorting mistakes (share the subject and sender, never the body).
Please keep personal data out of issues, tests and screenshots; use `scripts/demo-data.py`. See
[Development](docs/development.md) to get started.

## License

**AGPL-3.0** (version 3 or later), from version 1.0. Use it, change it, run it for yourself. If you run a modified
version as a service for other people, share your changes under the same license. See [LICENSE](LICENSE).

Copyright (C) 2026 Harun Gunasekaran. Releases before 1.0 (v0.1.0 and earlier) were published under the MIT license
and stay available under it.
