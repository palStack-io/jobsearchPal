# Setup

Two ways in: a plain Linux install with systemd and cron, or Docker (one image with everything, the simplest). Either
way, most of the setup happens afterwards on the dashboard's **Setup** page.

## On a Linux server (about 30 minutes)

You need an always-on Linux box (Ubuntu 24.04 tested), **either** a Claude subscription (Pro or Max,
for Claude Code) **or** a ChatGPT subscription (Plus or Pro, for the Codex CLI), and ideally
[Tailscale](https://tailscale.com) so the dashboard is reachable only from your devices. Email sorting
can also use an OpenRouter key or a local Ollama model.

| You have | Daily run | Email sorting |
|---|---|---|
| Claude subscription | Claude Code | Claude (Haiku) |
| ChatGPT subscription | Codex CLI | Codex |
| Either, plus an OpenRouter key | as above | Jev or any OpenRouter model first, the subscription as backup |
| Either, plus a GPU or spare CPU | as above | a local Ollama model, nothing leaves the server |
| Only an OpenRouter key | Claude Code (Anthropic models) or Codex CLI (most models) on the key | Jev or any OpenRouter model |

The OpenRouter route for the daily run follows OpenRouter's own guides: Claude Code talks to its
Anthropic-compatible endpoint, and Codex uses it as a custom provider. Each run's real cost is read from
the key's usage before and after, and the run is skipped once the month's budget is reached.

1. Clone this repo to `~/auto-apply` on the server and run `bash scripts/install.sh`
   (`AGENT=codex bash scripts/install.sh` for Codex).
2. Start the dashboard (`autoapply serve`) and use its **Setup** page (see Docker below for the steps), or
   copy the examples and fill them in:
   - `config.example.yaml` → `config.yaml` (boards to watch, title keywords, dashboard host,
     work-search label and weekly minimum)
   - `profile.example.yaml` → `profile.yaml` (your contact details and standard answers)
   - `.env.example` → `.env` (`REVIEW_URL`, Gmail app password)
3. Put your files in `materials/` (see `materials/README.md`), and write `CLAUDE.local.md` with
   personal facts the agent should use (the same file works for Codex): work authorization, titles and dates, pay asks, location
   preferences, how to name files. Both are gitignored.
4. Sign in once: `claude` in the folder (sign in and trust it), or `codex login`.
5. `pytest` to check the form filler, then `bash scripts/daily.sh` for a first run.
6. Start the dashboard as a service and schedule the daily run:
   ```
   cp scripts/autoapply-review.service ~/.config/systemd/user/
   systemctl --user enable --now autoapply-review
   sudo loginctl enable-linger $USER
   crontab -e   # add the line from the top of scripts/daily.sh
   ```
7. Open `REVIEW_URL`, create your account (email and password), and sign in on each device. On the **Settings**
   page, pick the daily-run agent and model and the email-sorting order.
8. Optional, on your laptop: `bash scripts/mac-setup.sh <ssh-host>` installs `autoapply-finish`.

Updating later: `git pull && .venv/bin/pip install -e . && systemctl --user restart autoapply-review`.

## Docker (alternative to steps 1, 4 and 6)

One image has Python, Playwright's Chromium, LibreOffice and both agent CLIs, so nothing else gets
installed on the host. Your files stay in the repo folder, which is mounted into the containers.

```
git clone https://github.com/palStack-io/jobsearchPal.git ~/auto-apply && cd ~/auto-apply
docker compose up -d                        # dashboard + the daily scheduler (pulls the published image)
# then sign in to Claude or ChatGPT on the dashboard's Setup page (or: docker compose run --rm web claude)
```

Then open `http://localhost:8787/` (or `http://<server>:8787/` with `BIND` set, below). A fresh install opens
**Create your account** (email and password; sign-in is required from then on), then the **Setup** page,
which walks through everything in a browser:

1. **Base resume**: upload your .docx (a PDF is made for you). Tailored resumes are built from it.
2. **Contact details and standard answers** (prefilled from the resume), saved to `profile.yaml`.
3. **Job interests**: titles to look for and skip, US-only, the levels you want (entry, mid, senior, lead,
   manager: read from the title, so "Data Engineer" is mid-level), companies to watch (paste careers-page URLs from
   Greenhouse, Lever, Ashby, Workable or SmartRecruiters) and job sites, saved to `config.yaml`.
4. **About you** (optional, recommended): roles and level, pay ask, where you'll work, work authorization,
   what to emphasize and avoid, saved to `materials/context/setup.md`.
5. **Agent sign-in**: right on the page (paste a Claude token, or sign in with ChatGPT through a link and code); see
   [Signing in to your AI subscription](#signing-in-to-your-ai-subscription) below.
6. **Email** (optional): Gmail, iCloud or Yahoo with an app password, tested before it's saved, for job alerts, replies
   and the brief. Best is one address for the whole search. Other addresses you apply from: list them under
   **Other addresses** and forward them to the main inbox (steps shown per provider), or connect up to three as extra
   inboxes. Outlook.com and Hotmail can only be forwarded: Microsoft turned off app passwords for them in September 2024.

![Setup](setup.png)

Until steps 2 and 3 are done, the daily run does nothing. The Setup page writes the same files you could edit
by hand, so either way works; keys it saves go to `data/secrets.json` (owner-only), and `.env` wins over them.

- **Dashboard:** port 8787 on `127.0.0.1` by default. Add `BIND=<your Tailscale IP>` to `.env` to
  reach it from your phone.
- **Schedule:** `DAILY_AT=06:30`, `DAILY_DAYS=1-5` and `TZ=America/New_York` in `.env` (these are
  the defaults). Output goes to `data/cron.log` as before.
- **Your user id:** the containers run as uid 1000 so files in the folder stay yours. If `id -u`
  says otherwise, add `APP_UID` and `APP_GID` to `.env`.
- **Updating:** `git pull && docker compose pull && docker compose up -d`. The image is published for amd64 and
  arm64 (Raspberry Pi 5, Apple silicon and ARM cloud machines) at `ghcr.io/palstack-io/jobsearchpal`; set
  `JSP_VERSION=1.0.0` in `.env` to stay on one release, or build your own with `docker compose up -d --build`.
- **Logins** live in the `agent-home` volume, so a rebuild keeps them.

### Signing in to your AI subscription

The daily run uses the Claude or ChatGPT plan you already pay for, through Claude Code or Codex inside the
container. You sign in once; the dashboard and the morning scheduler share the login. Your server does not need a
screen or a browser: every way below gives you a link to open on your laptop or phone.

**Easiest: on the dashboard.** The Setup page (step 5) and **Settings > Daily run** have a sign-in card:

- **Claude:** on any computer with Claude Code (your laptop is fine), run `claude setup-token`, sign in, and paste
  the token into the card. It's checked with one tiny call, saved in `data/secrets.json` (owner-only) and used by
  every run, the scheduler included.
- **ChatGPT:** press **Sign in with ChatGPT**. The card shows a link and a code; open the link on any device and enter
  the code. Codex keeps the login in the `agent-home` volume.

Or from a terminal on the server:

**Claude (Pro or Max), either way:**

- **Sign in inside the container.** `docker compose run --rm web claude`, type `/login`, open the link it prints,
  sign in, paste the code back, then trust the `/app` folder and quit. The login is kept in the `agent-home` volume.
- **Or a long-lived token in `.env` (good for servers).** Run `docker compose run --rm web claude setup-token`,
  open the link, sign in, and copy the token it prints into `.env` as `CLAUDE_CODE_OAUTH_TOKEN=...`. Then
  `docker compose up -d` so both containers pick it up. Nothing depends on the volume, and the Setup page counts it
  as signed in. Treat the token like a password.

**ChatGPT (Codex):** `docker compose run --rm web codex login --device-auth`, open the link it prints on any device
and enter the code. (Plain `codex login` waits for a browser on the server itself, which a container doesn't have.)
Copying `~/.codex/auth.json` from a machine where you're signed in also works.

**Pay-per-use or local instead:** an OpenRouter key on the Settings page, or a local Ollama model, needs no
sign-in at all.
- **Updating:** `git pull && docker compose restart`. Code runs from the mounted folder; rebuild
  (`docker compose up -d --build`) only when dependencies change.
- Codex sign-in on a headless box: sign in on a laptop and copy `~/.codex/auth.json` in with
  `docker compose cp ~/.codex/auth.json web:/home/app/.codex/auth.json`.
