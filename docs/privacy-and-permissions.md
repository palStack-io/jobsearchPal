# Privacy, permissions and limits

What the tool will not do, and what each piece you turn on can reach.

## What it will not do

- **Submit for you.** The CLI has no submit command. The only Submit button is on the dashboard,
  behind a review tick box, and each approval covers one click.
- **Answer consents or self-ID questions.** Privacy acknowledgements, AI-policy consents and
  demographic questions are never answered by the agent (`hold_for_review` in `profile.yaml`). You can
  save your own standing answers on Settings > Setup; the filler pre-fills those, and the review page shows
  them in a highlighted **Check these** box so you confirm them on every application.
- **Get past bot checks.** No captcha solving, no reading verification codes, no logins, no
  account creation. When a site asks for a code, use `autoapply-finish` and enter it yourself.
- **Workday and other account-based sites.** Materials are prepared and the role is flagged
  "apply by hand".

`AGENTS.md` gives the agent these rules (Codex reads it directly; `CLAUDE.md` imports it). With Claude
Code, `.claude/settings.json` also blocks edits to the tool's own code; Codex runs with writes limited to
this folder, so for it that rule is an instruction only. Either way these are guardrails, not a sandbox:
the review step is the real safety net.

## Permissions: what you hand over

Know what each piece can reach before you turn it on.

**The daily agent (Claude Code).** It runs unattended with `--permission-mode acceptEdits`, so it can
create and edit files in this folder without asking. Commands it may run come from the `allow` list in
`.claude/settings.json`: the `autoapply` steps it needs, `soffice` for PDFs, `python3` (used to build the
tailored .docx), `mkdir`, `ls` and `cat`, plus web search and fetch for company research. Anything
else stops the step, and the dashboard shows that step as failed. The `deny` list blocks
`autoapply serve`, `sqlite3`, and edits to `autoapply/` and `.claude/`. Be aware that `python3:*` is broad: Python
can do whatever your user account can. If that's too much for you, run it in Docker or as a separate
low-privilege user.

**The daily agent (Codex).** Runs with `--sandbox workspace-write`, network on and no approval prompts:
writes are limited to this folder, but there is no command allow list.

**Email: Gmail, iCloud or Yahoo (app password, optional).** An app password grants full IMAP and SMTP access to the mailbox,
the same as your password, minus the 2-step check. jobsearchPal uses it to read job emails in All Mail, Spam and Trash (each opened read-only,
messages fetched with `BODY.PEEK` so they stay unread) and to send you the morning brief. It never
sends anything else, never deletes and never marks as read. It is stored in `.env` or `data/secrets.json`
(owner-only). It also reads the headers (not bodies) of mail you sent, to tell when you've replied. A separate
job-search Gmail limits what the password can reach. To cut it off, revoke it at myaccount.google.com/apppasswords.

**OpenRouter key (optional).** Pay per token. Email sorting and Quick pick stop for the month at
the budget on Settings > Costs & limits; set a credit limit on the key itself too.

**The dashboard.** It shows your applications, emails and answers. It listens on `127.0.0.1` unless you
set `BIND`. Sign-in is always on: the first page of a new install is **Create your account** (the email for
your job search and a password of at least 10 characters), and every page after that needs it. The password is
kept as a salted scrypt hash in `data/account.json` (owner-only); a sign-in lasts 30 days in a signed cookie.
Five wrong tries from one address make it wait a few minutes. Forgot it: run `autoapply reset-password` on the
server (Docker: `docker compose exec web autoapply reset-password`), which also signs every browser out. There
is no password-change screen. The cookie isn't marked Secure because the setup is plain http, so prefer a
private network such as Tailscale before you open it to your phone. `AUTOAPPLY_TOKEN` is now only the machine
key `autoapply finish` uses to fetch a filled form (`/bundle` and `/file`); it is made in `data/secrets.json`
when you create the account.

**Finish it here (optional).** When it's on, a browser runs in a container on your server and visits the job site
you're finishing. It reaches the internet only through a second container, a proxy that refuses home, tailnet and
Docker addresses, so a job site's page can't reach your other machines. None of your folders are mounted into it:
the resume and letter for that one application are handed to the form through the browser connection, and a fresh
browser profile is used and closed for each application, so nothing (cookies, files, history) is kept. You see it
through the job page, behind your sign-in; its own ports listen on the server only. It never presses Submit.

**What leaves your server.** Job emails, your resume and notes go to the model you pick (your Claude or
ChatGPT subscription, or OpenRouter/Jev if you turn them on); Ollama stays local. Postings are fetched from
public job boards. The form filler types your real name, email and files into the company's own form, and
nothing is sent until you press Submit.

**Report a problem (installs connected to palStack only).** An install is connected only when its `.env` sets
`PALSTACK_MANAGER_URL` and `PALSTACK_MANAGER_SECRET`, which palStack gives to installs it runs. A self-hosted install
without them has no report form, no extra routes and sends nothing. On a connected install, Settings shows
"Report a problem": when you tick "Send this report to the palStack team" and press Send, we send what you write,
your app version and which screen you were on, nothing else, and keep it for 12 months. adminPal (palStack's admin
console) can also ask a connected install for totals: whether the account exists and whether an application was
sent in the last 30 days, as counts, never names, companies or text. There is no automatic error reporting.

**What stays on disk, unencrypted.** Resumes, tailored documents, answers, copies of job emails and form
screenshots live in `materials/` and `data/`. Keep the folder private and back it up.

**Adding a step?** If a prompt uses a new `autoapply` command, add it to the `allow` list. A test
(`tests/test_daily.py`) fails if a prompt uses a command the agent isn't allowed to run.
