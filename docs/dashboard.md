# The dashboard and the pal

Every page of the dashboard, the Settings tabs, and how the pal talks and helps.

## Dashboard pages

- **Dashboard:** what's **ready to submit** and a **shortlist** of today's best untailored matches (Prepare or
  Decline, with a reason), your work-search count for the week, a to-do list (replies owed, follow-ups, claim day,
  RESEA and other deadlines), interviews with their prep packs, latest replies, recruiters, applications per week
  and the full application list. **Run now** starts another batch any time.
- **Run log** (`/runs`, linked from the Running badge and the status strip): what each run did, step by step, read
  from `data/daily/*.md`. **Auto apply** on a lead also leaves its result under that lead (for example "the posting
  says it has been filled"). Confirmations and errors show in the app's own dialog, not browser popups.
- **Unemployment and RESEA emails** (Work search page): letters from your state's agency or career center (a .gov or
  state address, your state's agency domain, or names like MassHire, WorkSource, CareerSource, American Job Center),
  kept as proof, with any appointment or due date found. A RESEA date becomes a RESEA step automatically.
- **Applied elsewhere** (sidebar on every page, and the Dashboard and Leads headers): a short popup to log an
  application you sent on LinkedIn, a company site or by email. Each lead and each Ready or Shortlist role also has an
  **Applied** button that marks that exact role as applied today.
- **Your applications** (bottom of the Dashboard) are the tracker, kept in the local database: no spreadsheet or
  Google account needed. **Log one you sent elsewhere** adds an application you made on LinkedIn, a company site or
  by email, so it counts toward your week and is never offered again. **Bring in your old tracker** imports a CSV
  (Google Sheets: File, Download, CSV; Excel: Save As CSV), finding columns by name. **Download CSV** gives you the
  whole list back, and it imports cleanly again. The same role is only ever kept once, however it arrives.
- **Leads:** every role from your job-alert emails and the ones you paste, plus *Where your jobs come from* (see
  [Where jobs come from](how-it-works.md#where-jobs-come-from)). Each lead has its own page: the post and pay, then **Prep only** or **Auto apply**. Once a run has made
  something, the lead's page is the job's page below.
- **Prep only** (a lead's page, a job's page, or the dashboard shortlist): just the AI help. It tailors the resume and
  cover letter and writes answers to the form's questions (or the usual ones, for sites that need an account), then
  stops. The job's page becomes your kit: download the files, copy each answer, open the company's application page,
  and press **I applied by hand** so it counts toward your week. Workday and other account-based sites always get
  this. When Auto apply can't find a pasted role on the company's own site, it preps from the post you pasted.
- **Review page:** the filled answers, your tailored documents and a screenshot of the form. Pre-submit checks
  (documents present and current, required answers filled, consents confirmed) keep Submit locked until they pass.
  **Rewrite this letter** on the Cover letter tab takes a note ("warmer and shorter"), rewrites only that letter
  in the background, and fills the form again; it uses AI credit and never submits. Tick *Keep this for every letter*
  to add the note to how all your letters sound. The **Notes** tab shows the application's notes (fit/gap table, pay
  ask, form answers, screen prep) as a formatted document; **Open as a page** gives it a page of its own with
  **Print or save as PDF**.
- **Finish with the command:** when a site shows a captcha, the review page gives you `autoapply-finish <job>` to
  run on your laptop (**Copy command** works on the plain http address too). Each run is counted, and the job shows
  *Filled with the command*. While Chrome is open the command watches for the site's confirmation page and marks the
  job sent the moment it shows (it never clicks anything). If you close Chrome without one, nothing is marked: press
  **I sent it with the command** if you did send it. A job marked either way has **Not sent? Undo**, which puts it
  back as it was. **I applied by hand** stays under More.
- **Stats:** response and interview rates, median time to reply, funnels, results by application
  platform, reply-time buckets, weeks at your work-search minimum, and pipeline trends. *Beyond applications*
  counts the weeks you wrote a plan, the people you got in touch with (and interviews at their companies), and
  your reflections. *Your planned weeks* shows what you did against each weekly plan you set. *How applications
  went out* splits them into sent by the app, with the command, by hand, and logged from elsewhere, and says how
  many times `autoapply-finish` ran.
- **Skills:** what the roles you matched keep asking for that you don't show yet, most asked first, with the
  roles that ask and a link to each skill's own docs or free course. With Skill matching on, it uses the model's
  must-have and nice-to-have list for each role it read (nothing is read twice); otherwise a fixed list of about 80
  skills matched against the descriptions, at no cost. Gaps in the tailoring notes' fit/gap tables count double.
  Mark a skill **I have this** (it joins your skills list), **Learning**, or **Hide**. It also points to free WIOA
  training through your state's career center.
- **Emails:** see [Email sorting](how-it-works.md#email-sorting).
- **Settings:** Setup, Email sorting, Daily run, and Costs & limits tabs (see [Settings page](#settings-page)).
- **Work search:** see [Unemployment help](unemployment.md). It also holds *People you've been in touch with*.
- **Reflection** (`/reflect`): the fifteen-minute values reflection, and what you wrote before.

Light and dark mode, and it works on a phone. It looks like the other palStack apps: the Figtree typeface (served by the
app itself from `/static/fonts`, no font service), a light sidebar, and a sunrise scene on the dashboard and the sign-in page.
Motion is finite and plays once; with Reduce Motion the finished picture shows.

## Settings page

Four tabs. The menu's Settings item opens **Setup** first, with a badge while a required step is missing.

| Tab | What you set |
|---|---|
| Setup | resume, contact details, job interests and job sites, notes about you, how your letters sound (the Letters page), agent sign-in, Gmail (see [Setup](setup.md)). |
| Email sorting | which sources are on, their order (↑ ↓), the model for each (OpenRouter's list comes with live prices), Jev's minimum confidence, the Ollama address. Each source has a **Test** button that sorts a sample email and shows the answer, time and cost, plus 30-day counts of emails sorted, cost and failures. |
| Email sorting: OpenRouter key | for Jev and OpenRouter. It's stored in `data/secrets.json` (owner-only) and never shown again. A key in `.env` wins. |
| Costs and limits | daily caps for tailoring, prep packs and drafts; the monthly budget for paid APIs; 30-day usage and cost by day. |
| Daily run | the agent (Claude Code or Codex CLI, with what's installed and signed in), what pays for its model (your subscription, or an OpenRouter key billed per token), and the model. Opus writes the best cover letters; Sonnet or Haiku go easier on usage limits. |

Choices are saved in `data/settings.json`. Nothing on the page needs a restart.

## The pal

A job search is hard work, and jobsearchPal talks like a friend who knows it. It is honest about the market
and on your side, and it never promises an outcome. The rules and the phrase bank are in [VOICE.md](../VOICE.md). They
draw on research about unemployment and job searching, kept in the palStack research library.

- **The morning.** The welcome fits how the search is going: someone wrote back, an interview, a quiet stretch, a
  Monday, the evening. Good news and roles ready to send come first. When it can, the welcome names what you
  actually did ("Yesterday you sent 3, and that counts").
- **Rejections.** When one comes in, the pal says so once and kindly ("one stone in a very big ocean"), then
  points to what's ready. Lists say "closed", and the count stays exact. A rejection is never sent as a phone
  push; the brief lists them together with "the closed ones need nothing from you".
- **The weekly minimum.** A quiet tracker that speaks only when you're short: "One more by Saturday covers this week."
- **The look.** Warm paper colours rather than clinical grey, a little morning light on the sidebar, and red kept for
  what is urgent: a benefits step due within two days (or overdue), or an appeal. Other dates, claim day included
  (a routine weekly step), are sunrise amber.
- **Celebrations.** The logo's sunrise grows into a horizon across the top of the dashboard. Each celebration
  plays once, and with Reduce Motion on you see the finished picture with no movement:
  - covering the week's minimum: light across the water
  - a new interview: the sun lifts with a ring of light
  - an offer: the full sunrise
  - your first application, then 10, 25, 50 and so on: a small note on what that took. Counted up, never a target
    or a streak, and only when the one that reached it went out this week.
- **The market, honestly.** On a quiet stretch, after a rejection, on a long search and once a month in the brief,
  one dated line about the market with its source. There are no figures in the copy. It comes from
  `autoapply/market.yaml`, which maintainers refresh after each monthly jobs report, and it hides itself after
  45 days.

## What helps a search

Beyond the tone, the pal offers a few things that research on job searching supports. Each one is optional, can
be put away for good, and sends no reminders. Nothing here counts visits or opens; the Stats page shows only counts
of what you did.

- **Your plan for the week.** A short card on the dashboard: which kinds of roles, which days, and one person to
  get in touch with. You write it; the dashboard and the morning brief show it back. "Not this week" is a fine answer.
- **Weekly goals.** On Sunday, set a few totals for the week (applications, and hours for reaching out, interview
  prep and learning) and pick your days; they're spread across those days, and you can change any day. Each day a
  *Today* card shows your share: applications and people you got in touch with count themselves, and the rest get a
  Done button. A short day is credit, never a red mark; what's left can move to your next day or simply go. Next
  Sunday a look-back shows what you did next to what you planned, with an optional note, and flows into planning the
  new week. No streaks and no reminders; the plan never changes the daily run or your work-search count.
- **People you know.** Every ready role has an *Anyone you know there?* link. People you get in touch with go on
  the Work search page and are listed in your log as networking contacts. They count toward your weekly number
  only if you turn that on, because not every state accepts networking.
- **Fit first.** The daily run ranks roles on fit, never on reaching the weekly number. Fewer, better
  applications go further, and contacts and workshops can cover the week.
- **Titles close to yours.** Skills lists job titles that keep showing up in the roles found for you and share
  words with your search. After eight weeks on three titles or fewer, the dashboard offers them too. One click adds
  a title to the search.
- **Before the hard days.** In the first weeks of a search, and again around week eight, a card says honestly what
  a long search looks like and what helps on a bad day. You can write your own "When a hard day comes, I will…",
  and the pal shows it back the next time a rejection arrives.
- **Fifteen minutes on what matters to you.** Early in a search and before a first interview, the pal offers a
  short private reflection: pick up to three values and write a few lines about one. Prep packs link to it too.
- **A gentle return.** After a week or more with nothing done, the dashboard says "Welcome back" and shows what's
  ready and one to-do. The rest stays folded away until you want it.

The dashboard shows at most one of these cards at a time, so the page never piles up.
