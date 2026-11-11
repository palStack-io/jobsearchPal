# FAQ

**Does it apply to jobs for me?** It finds roles, tailors your resume and cover letter, and fills in the
real form, then stops. You press Submit on the dashboard, or with `autoapply-finish` on your laptop when
the site shows a captcha or sends a verification code.

**Does my computer, or Claude desktop, need to be on?** No. The daily run is started by cron (or the
Docker scheduler) on the server and runs the agent CLI there, headless. Your laptop is only needed for
`autoapply-finish`.

**Does it watch LinkedIn?** Through your LinkedIn job-alert emails only (see
[Where jobs come from](how-it-works.md#where-jobs-come-from)). Add more alerts with tight filters to widen it.

**Which AI does it use, and what does it cost?** Whatever you turn on in Settings: your Claude or ChatGPT
subscription covers the daily run and email sorting at no extra cost (it does count toward your plan's
usage limits). Jev, OpenRouter models and a monthly budget cap are optional.

**How do I make the cover letters sound more like me?** Settings, then **How your letters sound**: pick a
tone, an opening and a length (each shows an example), list words to never use, and paste a letter you liked.
**Show these with my resume** writes the examples once from your own resume, for your latest application (one
small AI call, saved; only facts from your resume), so you see each style with your own work.
To fix one letter, open the application and use **Rewrite this letter** on its Cover letter tab.

**Submit is greyed out.** Something in *Fix before you submit* needs you: usually a required question, a
consent, or a document check. Answer it and press **Save and refill**. Optional questions can stay blank.

**Will it apply to the same job twice?** No. A role you applied to, or filled a form for, is never offered again,
whether the run found it, you logged it by hand, or it came in from your old tracker. The same role is matched by
company and title (so "Sigma Computing, Inc." and a job board's "sigmacomputing" are one company) or by the posting
link. Other roles at a company you applied to come back after 90 days (`reapply_after_days` in `data/settings.json`;
0 keeps the whole company off for good).

**Do I still need my Google Sheet?** No. Import it once with **Bring in your old tracker** on the Dashboard, then log
anything you send outside the tool with **Log one you sent elsewhere**. **Download CSV** gives you a spreadsheet
copy whenever you want one.

**A reply was filed wrong.** Change it on the Emails page. Your choice is kept through later syncs and
`sync-email --rebuild`.

**How do I update?** `git pull`, then restart the dashboard (see [Setup](setup.md)). Your
`config.yaml`, `profile.yaml`, `.env`, `CLAUDE.local.md`, `materials/` and `data/` are gitignored, so an update
never touches them.
