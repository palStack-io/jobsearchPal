# Finish on the server: a live browser inside jobsearchPal

Date: 2026-11-17. Status: ready for Harun's approval (builder check done). Approach A of three (chosen by Harun).

## Why

When a site blocks the autofill (a captcha, an emailed code), the job page tells Harun to run `autoapply-finish <job>`
on his Mac. That opens Chrome on the Mac, fills the form, attaches the files, and he presses Submit. It only works on a
computer that has the command installed, Chrome, and the machine key (`AUTOAPPLY_TOKEN`) in `~/.autoapply.env`.
Since 2026-11-17 jobsearchPal is public at https://jobs.harunguna.xyz, so Harun can review jobs from any computer. He
still can't finish one there: on Windows the setup script doesn't run, and on a borrowed computer the key and his
resume would be left behind.

The fix: Chrome runs **on the server** (ubuntu), and the job page shows that Chrome live. Harun clicks and types in it
from whatever browser he is using, solves the captcha, and presses Submit himself.

## Goals

- From any computer or phone signed in to jobsearchPal, a job that needs finishing can be finished. Nothing is installed
  on that computer, and nothing is left on it afterwards.
- The form is filled the same way `autoapply-finish` fills it today (`fillers.base.handoff`: same filler, same files).
- When the site's confirmation page shows, the job is marked "sent with the command", exactly as today (`watch_for_sent`
  → `/finished`).
- It stays behind the sign-in. No new public port, and no key in a URL.

## Non-goals

- Solving captchas or reading emailed codes automatically. Harun does that.
- More than one finishing session at a time. It is a one-person app.
- Replacing `autoapply-finish`. The laptop command stays as the second option.
- Making the picture smooth enough for video. Form filling only.

## Anti-goals (a failure even if it works)

- Anything that clicks Submit for him. `handoff` never submits, and that stays true.
- The VNC or Chrome debugging port reachable from outside ubuntu, or reachable without the jobsearchPal sign-in.
- A Chrome left running for hours on a job site after he walks away.
- Breaking the existing laptop command, the daily run, or the public demo (`AUTOAPPLY_DEMO=1` must not offer it).

## Constraints

- ubuntu runs the app as a venv plus a systemd user service, not in Docker. Docker works for Harun's user (no sudo
  needed). Nothing on ubuntu may need sudo.
- ubuntu has Playwright's Chromium (1248) and `Xvfb`, but no `x11vnc` or `websockify`.
- The public path is Cloudflare tunnel → `http://localhost:8787`. WebSockets pass through the tunnel. Requests reach
  uvicorn from 127.0.0.1, which is why the per-IP sign-in throttle keeps working; the new routes must not change that.
- The data in `~/auto-apply/data` is Harun's real job search. Build and test on a separate instance with a copy of the
  data (the private branch container at 127.0.0.1:8798 or similar), never against the live DB, and never press Submit on
  a real posting while testing.
- jobsearchPal is also a product (public repo, Docker install, the demo on llm). The feature ships off by default and is
  turned on in setup or config.
- **Scope: every jobsearchPal user (Harun, 2026-11-17, replacing "ubuntu only").** Both install types get it in this
  round; see "For every install" below. Harun's ubuntu install is the first one turned on.

## Approaches considered

| | How you see it | Trade-off | Verdict |
|---|---|---|---|
| **A. Live browser in the page (noVNC)** | Chrome runs on a virtual screen on ubuntu; the job page embeds a VNC viewer that runs in the browser; the app relays it behind the sign-in | Most to build (a container, a WebSocket relay, a viewer page), slight lag, one session at a time | **Chosen.** The only option that works from any computer with nothing installed |
| B. Remote desktop into ubuntu | An RDP app over Tailscale (xrdp already runs on :3389) | Almost nothing to build, but it needs an app and Tailscale on the laptop, and RDP must never be public | Doesn't solve the borrowed-computer case |
| C. Screenshot stream plus relayed clicks | The page shows Chrome's screencast over its debugging protocol and sends clicks and keys back | No VNC parts, but dropdowns, captcha widgets and typing get fiddly | More custom code for a worse result |

## Design

### 1. The browser box (a container on ubuntu)

A small Docker container, `jobsearchpal-finish` (compose service `finish`; how each install type runs it is in
section 5):

- `Xvfb` virtual screen (about 1280×900), a desktop-free window setup, **Chromium** started with
  `--remote-debugging-port`, and `x11vnc` on that screen.
- Publishes **only** to `127.0.0.1` on the host (venv install) or nothing at all (Docker install): the Chrome debugging
  port and the VNC port. No VNC password (changed while planning, 2026-11-17): anything that can reach those ports is
  already on the server and could read `data/` directly, so a password would add a secret to manage and no protection.
- **Mounts nothing.** Files are uploaded through the debugging connection: tested 2026-11-17 on ubuntu, Playwright
  1.64.1 `connect_over_cdp` to Chromium 156 in a container attached a file that existed only on the host. So the box
  never sees `data/` (this replaces the read-only mount first planned).
- Restarts on failure. It may run all the time, idle (an empty screen), or be started on demand by the app. Builder's
  call; idle is simpler and costs little memory.

**Walled off from the home network (Harun, 2026-11-17).** The browser container sits on an internal Docker network
with no route out. A small outbound proxy container is its only way to the internet, and Chromium is started with
`--proxy-server` pointing at it. The proxy refuses private, link-local, loopback, CGNAT/tailnet (100.64.0.0/10) and
Docker bridge ranges, checked after DNS resolution so a public name pointing at a home address is refused too. No
host firewall rules (those need sudo). Test: from the box's Chrome, a page that fetches http://192.168.10.247:8123
(Home Assistant) and http://100.122.220.35:8787 must fail, and a public site must load.

My call: a container, not more processes on the host. It needs no sudo, it isolates a browser that visits arbitrary job
sites from the home server, and Docker installs get the same piece.

### 2. The app drives that Chrome

A new `finish_here` module, in-process in `autoapply serve`:

- `POST /finish-here/{job_id}` (signed in): one session at a time behind a lock. If one is running, the page offers to
  close it first. It builds the same bundle `/bundle` builds (and records a run, as `/bundle` does today). In a
  background thread it connects with Playwright `connect_over_cdp` to the container's Chrome, opens a fresh context,
  and runs the existing fill: `handoff` refactored so the "get a browser" step is a parameter (launch locally, or
  connect to the box), with no change to the laptop path.
- It watches with the existing `watch_for_sent`. On a confirmation it calls the same code `/finished` runs, in-process
  (no key, no HTTP), so the job is marked sent with the command.
- **Ends itself:** when Harun presses **Done** or **Close**, after 20 minutes without input from the viewer, or a few
  minutes after the confirmation shows. The context is closed, so the site's cookies don't carry over to the next job.
- `GET /finish-here/status`: state for the page (filling, ready for you, sent, closed, error) and anything the filler
  couldn't answer (`report.unanswered`, already produced today).

### 3. Seeing it: the viewer and the relay

- `GET /finish-here/{job_id}`: a full-width page with the noVNC client (vendored into `autoapply/static/novnc`, no
  CDN), a status line, the list of questions the filler left unanswered, **Paste text** (a box whose text is typed into
  the focused field through the debugging connection, for codes copied on the laptop), and **Done / Close**. It scales to
  the window and works on a phone.
- `WS /finish-here/vnc`: the app relays WebSocket frames to the container's VNC port. The app does what `websockify`
  would do, so nothing else listens publicly. **The WebSocket route checks the session cookie itself**, because the HTTP
  sign-in middleware does not run for WebSockets. It refuses when no session is running, and checks `Origin` against the
  request host.
- Needs WebSocket support in uvicorn (`websockets`, i.e. `uvicorn[standard]`) plus a small client for the relay.

### 4. The job page

In the "Finish this one yourself" card (`templates/job.html`), the first, primary step becomes **Finish it here**,
which opens the viewer page. The `autoapply-finish` command moves below it ("Or on your Mac"), and "fill it in by hand"
stays last. Shown only when `finish.on_server` is on in config and the box answers. Otherwise the card is as today.

### 5. For every install

Two published images, built by the repo's existing GitHub Actions image workflow (`.github/workflows/docker.yml`,
same amd64 + arm64 matrix, same tags: `:main` on a push to main, `:X.Y.Z` and `:latest` on a release tag):
`ghcr.io/palstack-io/jobsearchpal-finish` (the browser box) and `ghcr.io/palstack-io/jobsearchpal-finish-proxy` (the
outbound proxy). The app image does not grow.

- **Docker install (the main path):** `docker-compose.yml` gets the `finish` and `finish-proxy` services under a compose
  profile `finish`, so `docker compose --profile finish up -d` turns them on and a plain `docker compose up -d` is
  unchanged. Networks: an internal network (`internal: true`) joins `web`, `finish` and `finish-proxy`; only
  `finish-proxy` and `web` are also on the default network. `web` reaches the box by service name, so the config default
  for a Docker install is `finish:<port>`. The box publishes no ports at all. The `web` container never gets the Docker
  socket, so the box runs whenever the profile is on (idle when no one is finishing).
- **Venv install (like Harun's):** the repo ships `docker-compose.finish.yml`, the same two images with the box's ports
  published on `127.0.0.1` only, started with `docker compose -f docker-compose.finish.yml up -d`. Config default
  `127.0.0.1:<port>`.
- **No Docker at all (a venv install on a laptop):** the feature is not offered. The person is at the machine, so the
  existing local `autoapply-finish` is the path, and the setup page says so.
- **Setup:** the setup page (`templates/setup.html`) gets an optional "Finish applications in the browser" step. It
  says what it does (a browser on your server visits the job site; nothing is kept after it closes), gives the one
  command for the person's install type, and turns `finish.on_server` on once the box answers. A health line shows on
  Settings.
- **Releases:** the box and proxy images move with the app version; the app checks the box's reported version and
  shows "update the browser box" on a mismatch instead of failing oddly.
- **Testing for every install:** the end-to-end check (below) runs for both a Docker install and a venv install, on
  amd64; arm64 is checked by the image building and starting (a Mac with Docker Desktop if one is free).

### 6. Config and docs

- `config.yaml`: `finish: {on_server: false, browser_url: http://127.0.0.1:<cdp>, vnc: 127.0.0.1:<port>}`. The demo
  forces it off.
- Docs: `README.md` (install), `docs/dashboard.md` (Finish with the command), `docs/setup.md` (turning it on, the container),
  `docs/privacy-and-permissions.md` (a browser on your server visits the job site; nothing is stored after it closes).

## What I decided (Harun can overrule)

- Container, not host processes (reasons above).
- Chromium, not Google Chrome, in the container. If a site rejects it, switch the image to Chrome.
- One session at a time; 20-minute idle close; a fresh browser context per job.
- Shipped in the product for every install type, **off by default**, turned on in setup; Harun's ubuntu install is the first one turned on. The browser is walled off behind an outbound proxy (both Harun's calls).
- The laptop command and its key remain. Retiring the key (and closing the `?t=` log issue that way) is a later decision.

## Left to the builder

- Container base image and window manager; exact ports; start on demand or always.
- How `handoff` takes its browser (parameter or small factory) without changing the laptop path's behaviour.
- The relay library (Starlette WebSocket plus an asyncio TCP stream is enough).
- Test shape: a local fixture form page with a fake captcha and a fake confirmation page, driven through the relay
  end to end. Never a real posting.

## How we'll know it works

Testing the public path without the live install (my call): the copy instance gets a temporary Cloudflare quick tunnel
(`cloudflared tunnel --url http://127.0.0.1:<copy port>`, a throwaway trycloudflare.com address, no dashboard change),
so WebSockets through Cloudflare, the Origin check, the cookie on the WebSocket route and the throttle's real-IP
behaviour are all tested behind a real tunnel before the live install is touched. Stop the quick tunnel afterwards.

1. On a copy instance: press **Finish it here** on a fixture job. The viewer shows the form filled with the files
   attached; typing and clicking work from a second computer and from a phone through jobs.harunguna.xyz. Reaching the
   fixture confirmation marks the job sent with the command.
2. Signed out, the viewer page redirects to sign-in and the WebSocket is refused. From outside ubuntu, nothing new
   answers on any port.
3. Idle for 20 minutes, the session closes itself, and the container's Chrome has no open tabs.
4. `autoapply-finish` on the Mac still works. The test suite passes. Steps 1 to 3 pass on a Docker install too
   (`--profile finish`), and a plain `docker compose up -d` without the profile behaves exactly as before.
5. Then on Harun's real install, with config on, he finishes one real application himself.
