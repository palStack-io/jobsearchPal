# How it works

What happens each morning, where roles come from, and how replies, interviews and follow-ups are handled.

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

## A morning, step by step

1. **Email first.** New mail from application systems and recruiters is gathered over read-only
   IMAP. Keyword rules only pick the candidates. A model then reads each one and files it as a
   confirmation, interview, rejection, offer, recruiter contact or not job-related, and matches it to
   the application it's about (see [Email sorting](#email-sorting)).
2. **Interview prep and follow-ups.** Any application with a new interview email gets a prep pack, and
   each [follow-up reminder](#follow-up-reminders) gets a drafted reply (see [Interview prep](#interview-prep)).
3. **Find roles** from the sources below. Anything already tracked or applied to is skipped.
4. **Rank and pick** the best one or two, and skip the clear misses with a reason (the Stats page
   shows why roles were skipped).
5. **Tailor** a resume and cover letter from your base resume and `CLAUDE.local.md`, plus a
   `notes.md` with the fit/gap table, form answers and pay ask. Letters follow **How your letters sound**
   (Settings, then Letters): tone, how a letter opens, length, words to never use, your own notes and letters you
   liked, each choice shown with a sample line first. It's saved to `materials/context/voice.md`.
6. **Fill the real form.** Playwright answers the questions, uploads both PDFs, takes a screenshot
   and stops before Submit. Consent and self-ID questions get only your own standing answers, highlighted for you to check.
7. **Brief.** One email (sent to yourself over Gmail SMTP) with what's ready, interview prep
   packs, new replies, your work-search count for the week, and the status of every application.
8. **You decide.** Open the dashboard, read the answers, tick the review box and press Submit, or
   finish on your laptop when a site wants a code. Then the confirmation email arrives and the next
   morning's sync files it.

## Where jobs come from

- **Company job boards you watch** (Greenhouse, Lever, Ashby, Workable, SmartRecruiters) through
  their public APIs: the freshest source, and apply-ready. The run adds good companies as it finds
  them (`autoapply board add`), and on Mondays it looks for about 10 new ones.
- **Job sites with open APIs:** RemoteOK, Remotive, Hacker News "Who is hiring?", The Muse, Himalayas
  and Jobicy. No key needed.
- **Job sites with a free key:** Adzuna (a large US aggregator) and USAJOBS (federal jobs). Add the keys on
  the Setup page or in `.env`.
- **Any RSS or Atom job feed** (`rss_feeds` in `config.yaml`, or the Setup page): We Work Remotely, jobs.ac.uk and
  many others have them. Sites that answer automated requests with a bot check (HigherEdJobs does) are
  left to their job-alert emails; jobsearchPal doesn't try to get around those checks.
- **Job-alert emails** from LinkedIn, Indeed (including its job-match emails), HigherEdJobs (Job Agent emails), Built In,
  Glassdoor, Wellfound, ZipRecruiter and Dice. Every role in them is saved on the **Leads** page with its location,
  salary when given, source and a rough fit score. After each email sync the top leads are looked up on the
  company's own job board (no AI) and join the roles the daily run can tailor; star others and the next run
  searches the web for them. Parsing is plain Python, from the "View job" blocks these emails use.
- **Any job you paste.** Found a role somewhere else? Open **Add a job** on the Leads page and paste the whole post
  (a LinkedIn job page copied with Cmd+A, a careers page, a job email) or a link to a Greenhouse, Lever or Ashby
  posting. It reads out the company, title, location, work setting and pay range and drops the page clutter; you
  check the fields, then save. A role you already applied to is caught and nothing is added. Plain Python, no AI.

**See every source in one place.** The Leads page opens with *Where your jobs come from*: the sources finding
jobs now (with how many roles each brought in), the ones that need a step (a missing key, Gmail not connected),
and everything not on yet in one line. **See list** shows every company you watch, with a filter. The dashboard
nudges you when fewer than half are on.

Each run saves what every source returned to `data/sources.json`; the Setup page shows the counts, and a
source that failed, next to its name.

**LinkedIn** is covered only through its alert emails. LinkedIn has no public jobs API, and
scraping it or signing in as you breaks its terms and risks your account. To widen what the run
sees, set up several daily alerts with tight filters in LinkedIn (Jobs → set alert), for example
"analytics engineer · Remote · United States · past 24 hours" or "staff data engineer · Boston ·
Hybrid". Send them to the inbox the sync reads. Each alert you add shows up in the next morning's run.

How LinkedIn roles flow through a run:

1. The run reads the alert emails (from `jobalerts-noreply@linkedin.com` and `jobs-noreply@linkedin.com`)
   in the inbox the sync uses.
2. It lists the roles in them that fit your interests.
3. For each good one it looks up the same posting on the company's own careers page (Greenhouse, Lever,
   Ashby and so on) and prepares the application there. Roles it couldn't match to a company page yet
   are listed in the brief as runners-up "from LinkedIn alerts (need ATS links)".

## Email sorting

**What each status means.** Every job email is filed as one of these, and the application takes the
latest one:

| Status | Means | Example |
|---|---|---|
| Received | the company got your application; you're still waiting | "Thank you for applying to …" auto-replies |
| Interview | a person asked to schedule a call or interview | a scheduling link, "are you free Tuesday?" |
| Closed | they said no, including "the position has been filled" | "we've decided to move forward with other candidates" |
| Offer | an offer | |
| Recruiter | a real person reaching out about a role | an agency or in-house recruiter's email |
| Unemployment agency | mail from your state's unemployment office | a RESEA appointment, a claim notice |
| Not job-related | alerts, newsletters, account and bank email | job-alert digests, sign-in links, statements |

An application with no reply beyond the automatic one for 30 days shows as **No reply yet**.

**A good order on the Settings page.** Sources are tried top to bottom, so put what you already pay for
first and pay-per-token last:

1. **Claude subscription** (Haiku is plenty for sorting) or **ChatGPT subscription** (Codex CLI). No extra cost.
2. **Jev** (needs an OpenRouter key): a classifier that returns a label with a probability. Anything below
   the minimum confidence moves on.
3. **OpenRouter**, any cheap chat model (for example `google/gemini-2.5-flash-lite`), for what's left.
4. **Ollama** if you run one, so nothing leaves the server.

Whatever no source is sure about waits for you on the Emails page.

- **Sources, in your order.** On the Settings page, turn on any of: your **Claude subscription**
  (Claude Code, run with no tools), your **ChatGPT subscription** (Codex CLI, read-only sandbox),
  **Jev** (TypeSafe's decision model on OpenRouter, which returns a label with a probability),
  any **OpenRouter** chat model, or a local **Ollama** model. Each email goes to the first source.
  If that source fails, or Jev is below your confidence threshold, the email goes to the next.
- **Only labels come back.** Email text is treated as data. Answers that aren't a known category or
  a known application are dropped.
- **Nothing counts until it's sorted.** Stats, outcomes, the brief and the work-search log use sorted
  emails only. Anything no source could sort waits on the **Emails** page.
- **You have the last word.** The Emails page lists every job email by tab (Not sorted, Replies,
  Recruiters, Not job-related, All), with the text, the keyword guess and who sorted it. Change the
  category or the application from a dropdown. Your choice survives rescans and later model runs.

## Interview prep

When an email is sorted as an interview, the next morning's run writes a prep pack for that company
and stage (recruiter screen, hiring manager, technical, onsite). It's shown on the dashboard's
**Interviews** card and in the morning brief, and you can print it.

- **What candidates report:** the interview process and questions others have shared for that company
  and role, from public pages only (Glassdoor and Indeed pages readable without signing in, Reddit,
  Blind, levels.fyi, the company's own blog and handbook). Every item links its source, and the pack
  says so when little is public instead of making questions up.
- **Likely questions** drawn from the posting, labeled as such.
- **Your stories:** STAR answers built only from your materials, with your real numbers.
- **About them**, **questions to ask**, and **pay** (your ask and the posted range).

When a later interview email arrives (the next stage), the pack is updated for it.
`autoapply list --prep` shows what's due.

## Follow-up reminders

The dashboard's **To do** card and the morning brief remind you when:

- a recruiter wrote and you haven't replied (after a day),
- you wrote last and a recruiter has gone quiet (after 5 days): check in,
- an interview invite is waiting for your answer, or a booking link for your time,
- an application has had no answer beyond the confirmation for two weeks: a short follow-up note, or let it go.

"Replied" is read from your Sent mail (headers only, read-only). The daily run drafts a short reply for
each one, from your materials only. **Reply in Gmail** opens a compose window with the draft filled in, and you
send it yourself. Each reminder has **Done** and **Snooze 3 days**. `autoapply list --nudges` shows what
needs a draft.
