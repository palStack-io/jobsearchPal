# Development

## Running it locally

```
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/playwright install chromium
.venv/bin/pytest
python3 scripts/demo-data.py /tmp/demo && cd /tmp/demo && autoapply serve   # http://127.0.0.1:8799
python3 scripts/screenshots.py                                              # refresh docs/*.png
```

The filler is tested against local copies of each ATS's form layout (`tests/fixtures/`). Real
forms change, so expect to tweak labels and selectors in `autoapply/fillers/`. Issues and pull
requests are welcome, especially new ATS fillers and better reply classification.

## Commands

```
autoapply find                         new matching roles from boards and feeds
autoapply board add ATS SLUG           watch another company's board (checked first)
autoapply alerts [--days N]            job-alert emails as text
autoapply sync-email [--days N] [--rebuild]   replies and recruiter emails → sorted → dashboard and proof
autoapply review-emails                sort emails still waiting, through the Settings page's sources
autoapply emails [--pending]           filed emails as JSON;  emails set ID KIND  settles one by hand
autoapply import-history FILE          load past applications from your old tracker (.csv or JSON rows)
autoapply export-applications          every application as CSV
autoapply add URL / list / show ID / skip ID
autoapply careers URL [--title T]      job links on a careers page, read in a real browser (finds lists a
                                       script draws, e.g. an embedded Paycom or iCIMS board, that WebFetch misses)
autoapply list --prep                  interviews that need a prep pack
autoapply list --nudges / --limits     follow-ups that need a draft; today's caps
autoapply scan URL                     a form's questions as JSON
autoapply fill PLAN.json [--job ID]    fill, upload, screenshot, stop before Submit
autoapply brief [--send]               the morning summary
autoapply daily-agent                  the agent and model the daily run will use
autoapply serve                        the dashboard
autoapply finish JOB_ID                (laptop) open the filled form in Chrome for you to submit
autoapply reset-password [--email E]   forgot the dashboard password: set a new one (signs every browser out)
autoapply pal-line                     the pal's one line for the phone push
```

## Layout

```
autoapply/cli.py         the commands the agent calls
autoapply/fillers/       scan.js reads any form; base.py fills and submits per ATS
autoapply/finder.py      job-board APIs and feeds
autoapply/emailsync.py   gathers candidate emails, proof copies, applies a settled kind
autoapply/classify.py    model review of waiting emails (Claude Code, Codex, Jev, OpenRouter, Ollama)
autoapply/prefs.py       Settings page choices (data/settings.json) and saved API keys
autoapply/prep.py        interview prep packs: what's due, where they live, safe rendering
autoapply/nudges.py      follow-up reminders from sorted emails and your Sent mail
autoapply/stats.py       dashboard and stats numbers
autoapply/pal.py         the pal's voice: greeting, celebrations, rejection note, market line
autoapply/coach.py       what helps a search: weekly plan, contacts, related titles, setback card, reflection, return
autoapply/goals.py       weekly goals: totals spread over your days, the Today card, the Sunday look-back
autoapply/voice.py       how your cover letters sound: the Letters page choices, samples, voice.md, recent letters
autoapply/web.py         dashboard, review page, work-search log
autoapply/templates/     HTML (no build step, no JavaScript framework)
prompts/stages/          the daily run's steps (rank, apply, prep, drafts, letter), one agent session each
AGENTS.md                standing rules for the agent (CLAUDE.md imports it; personal facts go in CLAUDE.local.md)
tests/                   local fixture forms and tests
```

## Contributing

Issues and pull requests are welcome, especially: corrections to a state's rules (with the agency link), new ATS
form fillers, other email providers, and reply-sorting mistakes (share the subject and sender, never the body).
Please keep personal data out of issues, tests and screenshots; use `scripts/demo-data.py`.
