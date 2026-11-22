# Bot checks on application forms

Written 2026-11-22. What the job boards do to spot automated form filling, what jobsearchPal does about it, and
what it deliberately doesn't do.

## What happens

Greenhouse, Ashby and Lever protect their application forms. Most use a score rather than a puzzle up front
(reCAPTCHA v3 or Enterprise, or the board's own checks). The score watches how the form was filled:

- values that appear in a field with no keystrokes
- a whole form completed in a few seconds
- no pointer movement and no scrolling
- no time spent on the page before typing
- a brand-new browser with no history

A low score doesn't block the page. It adds a **verification step at Submit**, usually an image challenge, or quietly
drops the application. On one real install, 12 of 37 fill reports from Ashby ended at a challenge after Submit, and
19 (Ashby and Greenhouse) ended "Submit was pressed but not confirmed".

Until 2026-11-22 the filler set every value directly (`fill()`), ticked boxes programmatically and finished a form in
about 3 seconds in a fresh headless browser: everything on the list above.

## What jobsearchPal does: fill at a person's pace

`autoapply/fillers/pace.py`, used by every path that fills a form: the morning run, Submit from the review page,
Finish it here, and `autoapply-finish` on a Mac.

| Step | Pace |
|---|---|
| After the page loads | reads for 1.5 to 4 seconds |
| Before each field | scrolls it into view, moves the pointer to it in steps, pauses 0.4 to 1.4 seconds |
| Short answers | clicks in, clears any prefill, types key by key at 35 to 140 ms a key, with an occasional pause between words |
| Long answers (over 300 characters) | pasted, after a short pause, the way people paste a prepared paragraph |
| Dropdowns and boxes | the pointer goes to them before they're chosen |
| Before Submit | 3 to 8 seconds to look the form over, then the pointer moves to the button |

A form now takes a couple of minutes instead of seconds. The morning run takes a few minutes longer for that.

**Turning it off:** `AUTOAPPLY_FILL_PACE=fast` in the environment (the test suite does this). Anything else, or
nothing, is the human pace.

## What it records

Every fill report has `verification`: true when the site showed a bot check (a visible captcha or challenge frame)
after filling or at Submit. That shows whether pacing is working, board by board:

```bash
.venv/bin/python - <<'EOF'
import sqlite3, json, collections
c = sqlite3.connect("file:data/autoapply.db?mode=ro", uri=True)
n, v = collections.Counter(), collections.Counter()
for ats, rj in c.execute("select ats, report_json from jobs where report_json is not null"):
    r = json.loads(rj); n[ats] += 1; v[ats] += bool(r.get("verification"))
for ats in n: print(ats, v[ats], "of", n[ats])
EOF
```

## What it doesn't do, on purpose

- **It never solves a captcha** and never sends one to a solving service.
- **It doesn't fake a browser fingerprint** or hide that the browser is automated (no stealth plugins).
- **It doesn't retry a blocked Submit.** A verification step that still appears is left to you: open the job and
  use **Finish it here** (or `autoapply-finish` on your Mac). The form is filled, you answer the check and press
  Submit yourself.

Those would mean working against the boards' protection. That breaks their terms, can get applications flagged, and
breaks without warning when a board changes its checks. Pacing is what a careful person filling a form looks like.

## Next, if checks still appear

- Fill inside the Finish it here box's browser (headed, one long-lived profile) instead of a fresh headless one.
- Show the verification rate per board on the Stats page.
