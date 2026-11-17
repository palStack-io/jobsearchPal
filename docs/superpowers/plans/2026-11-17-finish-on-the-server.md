# Finish on the Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A signed-in person presses **Finish it here** on a job and fills, checks and submits that application in a live
Chrome that runs on their own server, from any browser, with nothing installed where they sit.

**Architecture:** Two small containers per install: a browser box (Xvfb + Chromium + x11vnc + socat) and an outbound
proxy (Squid that refuses private addresses). The app drives the box's Chromium with Playwright `connect_over_cdp`,
reusing the existing filler and confirmation watcher, and relays the box's VNC to a noVNC viewer through one
WebSocket route behind the sign-in.

**Tech Stack:** Python 3.12, FastAPI/Starlette (WebSocket), uvicorn + `websockets`, Playwright (sync API, CDP), Docker
Compose, Debian `chromium`, `x11vnc`, `Xvfb`, `socat`, Alpine `squid`, noVNC 1.5.0 (vendored), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-17-finish-on-the-server-design.md` (approved by Harun 2026-11-17).

**Base:** branch from `origin/main` (the local checkout is 8 commits behind; `job.html` and `web.py` changed there).
Branch name `feat/finish-on-the-server`. Commit steps below run only once Harun has said to commit (monorepo rule: no
git unless asked); otherwise leave the work staged-free and say so. No Claude attribution in commits or the PR.

## Global Constraints

- Never click Submit on the person's behalf. `handoff` and everything new only fill and watch.
- The box and proxy publish nothing except `127.0.0.1` ports in `docker-compose.finish.yml` (venv install); in
  `docker-compose.yml` they publish no ports at all.
- The box mounts nothing from the host. Files go through CDP (`set_input_files`, verified 2026-11-17).
- The proxy refuses, after DNS resolution: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`,
  `169.254.0.0/16`, `100.64.0.0/10`, `0.0.0.0/8`, `::1`, `fc00::/7`, `fe80::/10`. It allows ports 80 and 443 only.
- One session per app. Idle close after **20 minutes** without viewer input; close **3 minutes** after a confirmation.
- Fresh browser context per session; the context is closed on every end path.
- Feature off unless `finish.on_server: true` in `config.yaml`. With it off, every page renders exactly as today.
- A plain `docker compose up -d` (no profile) behaves exactly as before.
- Copy follows `VOICE.md` (no emojis, one exclamation mark at most, errors are ours with a next step) and the UI follows
  skill `palstack-design-standard`.
- Images: `ghcr.io/palstack-io/jobsearchpal-finish` and `ghcr.io/palstack-io/jobsearchpal-finish-proxy`, amd64 +
  arm64, tags as the app image (`:main`, `:X.Y.Z`, `:X.Y`, `:latest`).
- Testing never touches `~/auto-apply/data` on ubuntu and never submits a real posting.

## Review Focus

1. **WebSocket opened without a session cookie, or from another site** (cross-site WebSocket hijacking): must be
   refused with close code 4401 / 4403 before any byte reaches the box. Pinned in Task 6.
2. **Chromium's Host-header check on a Docker install**: connecting to `http://finish:9222` is refused by Chromium's
   DNS-rebinding guard; the app must resolve the service name to an IP first. Pinned in Task 4
   (`test_cdp_url_resolves_names`).
3. **Person closes the browser tab, or the box restarts, mid-session**: the session must end as `closed` or `error`, the
   lock must free, and **Finish it here** must work again without restarting the app. Pinned in Task 4.
4. **A second press of Finish it here (other tab, other device) while one runs**: no second browser; the page says one
   is open and offers to close it. Pinned in Task 5.
5. **Job site redirects to or embeds a home address** (`http://192.168.x.x`, a name resolving to `100.x`): refused by
   the proxy, the rest of the page still loads. Pinned in Task 2's container test.

---

## File Structure

| File | Responsibility |
|---|---|
| `finish-box/box/Dockerfile`, `finish-box/box/start.sh` | Browser box image: Xvfb, Chromium on CDP 127.0.0.1:9223, socat 0.0.0.0:9222 → it, x11vnc :5900, version file |
| `finish-box/proxy/Dockerfile`, `finish-box/proxy/squid.conf` | Outbound proxy image on :3128 |
| `docker-compose.finish.yml` | Venv installs: box + proxy, box ports on 127.0.0.1 |
| `docker-compose.yml` | Docker installs: `finish` + `finish-proxy` services under profile `finish`, internal network |
| `autoapply/fillers/base.py` | `handoff` takes an optional connected browser; `watch_for_sent` takes a `tick` hook |
| `autoapply/finish_here.py` | Session state machine, one at a time, worker thread, idle reaper, box health and version |
| `autoapply/vnc_relay.py` | WebSocket ↔ TCP byte relay |
| `autoapply/web.py` | `_mark_finished` extracted; `/finish-here/*` routes; WS route with its own auth; job/setup/settings context |
| `autoapply/templates/finish_here.html` | Viewer page |
| `autoapply/static/novnc/` | Vendored noVNC 1.5.0 `core/` + `vendor/` + LICENSE |
| `autoapply/templates/job.html`, `setup.html`, `settings.html`, `autoapply/brief.py` | Entry points and health line |
| `.github/workflows/docker.yml` | Build and publish the two new images |
| `tests/test_finish_here.py`, `tests/test_finish_here_web.py`, `tests/test_vnc_relay.py`, `tests/test_finish_box.py` | Tests |
| `README.md`, `docs/dashboard.md`, `docs/setup.md`, `docs/privacy-and-permissions.md`, `CLAUDE.md`, `AGENTS.md` | Docs |

---

### Task 1: The browser box and proxy images, and the venv compose file

**Files:**
- Create: `finish-box/box/Dockerfile`, `finish-box/box/start.sh`, `finish-box/proxy/Dockerfile`,
  `finish-box/proxy/squid.conf`, `docker-compose.finish.yml`
- Test: `tests/test_finish_box.py` (skipped unless `JSP_FINISH_DOCKER=1`)

**Interfaces:**
- Produces: box listens on `9222` (CDP via socat; Chromium itself on `127.0.0.1:9223`) and `5900` (VNC, no password,
  `-shared -forever`), display `:99` at `1280x900x24`; `/etc/jobsearchpal-finish-version` holds the image version
  (build arg `VERSION`, default `dev`); Chromium flags `--proxy-server=$FINISH_PROXY` (default
  `http://finish-proxy:3128`), `--user-data-dir` under `/tmp`, `--no-first-run`, `--window-size=1280,900`,
  `--window-position=0,0`, `--disable-dev-shm-usage`. Runs as a non-root user. Proxy listens on `3128`.
- `docker-compose.finish.yml`: services `finish` (ports `127.0.0.1:${FINISH_CDP_PORT:-9222}:9222`,
  `127.0.0.1:${FINISH_VNC_PORT:-5900}:5900`, networks `finish-inner` only) and `finish-proxy` (networks `finish-inner`
  and `egress`), `finish-inner` with `internal: true`, both `restart: unless-stopped`, images
  `ghcr.io/palstack-io/jobsearchpal-finish{,-proxy}:${JSP_VERSION:-latest}` with `build:` contexts for local builds.
  Note: with `internal: true` a published port still works for the host; verify in Step 2.

- [ ] **Step 1: Write `tests/test_finish_box.py`** (module skipped unless `os.environ.get("JSP_FINISH_DOCKER") == "1"`;
  a module fixture runs `docker compose -f docker-compose.finish.yml -p jspfinishtest up -d --build` and `down` after):
  - `test_cdp_answers`: `GET http://127.0.0.1:9222/json/version` → 200 and `"Browser"` in the JSON.
  - `test_vnc_answers`: TCP connect to `127.0.0.1:5900`, first 12 bytes start with `b"RFB 003."`.
  - `test_upload_without_mount`: `connect_over_cdp`, `set_content("<input type=file id=f>")`, `set_input_files` a temp
    file holding `b"only on the host"`; page reads back the same bytes.
  - `test_public_site_loads`: `goto("https://example.com")` → status 200.
  - `test_home_addresses_refused`: for each of `http://192.168.10.247:8123/`, `http://100.122.220.35:8787/`,
    `http://10.0.0.1/`, `http://127.0.0.1:8787/`, `http://169.254.169.254/`: `page.goto` raises or returns status 403
    (Squid's deny page); never a 200 from the target.
  - `test_version_file`: in a page, `goto("file:///etc/jobsearchpal-finish-version")` → body text equals `dev`.
- [ ] **Step 2: Run it, expect FAIL** (`JSP_FINISH_DOCKER=1 pytest tests/test_finish_box.py -v`: compose file missing).
- [ ] **Step 3: Write the images and compose file.** Box base `debian:bookworm-slim`, apt `chromium xvfb x11vnc socat
  fonts-liberation fonts-noto-color-emoji ca-certificates`. `start.sh` starts Xvfb, waits for the display, starts
  x11vnc and socat in the background, then `exec`s Chromium on `about:blank` and restarts it if it exits (a loop, so
  the person closing the last window does not kill the container). Squid config: `acl home dst` with the Global
  Constraints ranges, `acl Safe_ports port 80 443`, `http_access deny home`, `http_access deny !Safe_ports`,
  `http_access deny CONNECT !Safe_ports`, `http_access allow all` (the proxy is reachable only on `finish-inner`),
  no cache (`cache deny all`), access log to stdout.
- [ ] **Step 4: Run it, expect PASS** (on ubuntu or any amd64 Docker host; on the Mac if Docker Desktop is present).
- [ ] **Step 5: Commit** `finish-box/ docker-compose.finish.yml tests/test_finish_box.py` — "Finish box: browser and
  proxy images for finishing applications on your server".

### Task 2: Docker install wiring and image publishing

**Files:**
- Modify: `docker-compose.yml`, `.github/workflows/docker.yml`
- Test: `tests/test_compose.py` (new)

**Interfaces:**
- Consumes: Task 1 images.
- Produces: services `finish` and `finish-proxy` with `profiles: ["finish"]`, no `ports:`; network `finish-inner`
  (`internal: true`) joined by `web`, `finish`, `finish-proxy`; `web` and `finish-proxy` also on `default`. The
  Docker-install config default for the box is host `finish`, CDP `9222`, VNC `5900`.

- [ ] **Step 1: Write `tests/test_compose.py::test_finish_is_opt_in`**: load `docker-compose.yml` with `yaml.safe_load`;
  assert `finish` and `finish-proxy` have `profiles == ["finish"]`, neither has `ports`, `networks["finish-inner"]
  ["internal"] is True`, and `scheduler` is not on `finish-inner`. Add `test_finish_compose_binds_loopback` for
  `docker-compose.finish.yml`: every `ports` entry starts with `127.0.0.1:`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Edit `docker-compose.yml`** (keep the header comment style; add the two lines that say how to turn it
  on: `docker compose --profile finish up -d`).
- [ ] **Step 4: Extend `docker.yml`**: a matrix dimension `image` over `{name: jobsearchpal, context: .}`,
  `{name: jobsearchpal-finish, context: finish-box/box}`, `{name: jobsearchpal-finish-proxy, context:
  finish-box/proxy}`, with per-image digest artifacts (`digest-<image>-<arch>`) and a merge job per image; pass
  `VERSION` as a build arg from the tag (`main` on a branch push). Keep pinned action SHAs as they are.
- [ ] **Step 5: Run the compose tests, expect PASS;** `actionlint .github/workflows/docker.yml` if available, else
  `python -c "import yaml;yaml.safe_load(open('.github/workflows/docker.yml'))"`.
- [ ] **Step 6: Commit** — "Docker installs: finish box under the finish profile; publish both images".

### Task 3: `handoff` accepts a connected browser; `watch_for_sent` gets a tick hook

**Files:**
- Modify: `autoapply/fillers/base.py:377-428`
- Test: `tests/test_finish_watch.py`, `tests/test_fillers.py`

**Interfaces:**
- Produces:
  - `watch_for_sent(page, a, on_sent=None, poll_ms=1000, max_s=None, tick: Callable[[], bool] | None = None) -> str`:
    `tick()` is called once per poll in the watching thread; returning `False` ends the watch (returns what was seen).
  - `handoff(plan, profile, wait=True, on_sent=None, browser: Browser | None = None, on_page: Callable[[Page], None] |
    None = None, tick=None) -> Report`: with `browser`, opens `browser.new_context(viewport=None)` instead of
    launching, and closes **only that context** at the end (never the browser). `on_page(page)` is called once the
    form is filled and before watching. Without `browser` the behaviour is byte-for-byte today's.

- [ ] **Step 1: Write tests.** `test_tick_false_stops_watch` (fixture form page, `tick` returns False on the 2nd call,
  `watch_for_sent` returns `""` within 2 s). `test_handoff_with_browser_keeps_browser_open` (launch a local headless
  Chromium as the "box", call `handoff(..., browser=b, wait=True, tick=lambda: False)` against the `gh` fixture with a
  minimal plan, then assert `b.is_connected()` and `len(b.contexts) == 0`). `test_handoff_on_page_called_after_fill`
  (on_page receives a Page whose first text input already holds the plan's first name).
- [ ] **Step 2: Run, expect FAIL** (`pytest tests/test_finish_watch.py tests/test_fillers.py -k "tick or browser or
  on_page" -v`).
- [ ] **Step 3: Implement** the two signature changes in `autoapply/fillers/base.py`.
- [ ] **Step 4: Run those tests and the whole of `tests/test_finish_watch.py` and `tests/test_finish.py`, expect PASS.**
- [ ] **Step 5: Commit** — "handoff can use a browser it did not launch".

### Task 4: `finish_here` sessions

**Files:**
- Create: `autoapply/finish_here.py`
- Test: `tests/test_finish_here.py`

**Interfaces:**
- Consumes: `handoff(..., browser=, on_page=, tick=)` and `watch_for_sent` from Task 3; `fillers.base.Report`.
- Produces:
  - `@dataclass Box(host: str, cdp_port: int = 9222, vnc_port: int = 5900)`; `box_from(settings) -> Box | None`
    (None unless `settings.raw["finish"]["on_server"]` is true, and always None when `AUTOAPPLY_DEMO` is set, so the
    public demo never offers it; defaults host `127.0.0.1`).
  - `cdp_url(box) -> str`: `http://<IPv4 of box.host>:<cdp_port>` (resolve with `socket.gethostbyname`, because
    Chromium refuses non-IP, non-localhost Host headers).
  - `health(box, timeout=2.0) -> dict`: `{"ok": bool, "version": str, "error": str}`; reads `/json/version`, and the
    version from `file:///etc/jobsearchpal-finish-version` through CDP.
  - `class Sessions` (one per app): `start(job_id: str, plan: dict, profile: dict, mark_sent: Callable[[str, str],
    dict]) -> dict` (returns `{"started": True}` or `{"busy": <job_id>}`), `status() -> dict` (`job_id`, `state` in
    `starting|filling|ready|sent|closed|error`, `unanswered: list[str]`, `error: str`, `started_at`, `last_input`),
    `touch()`, `paste(text: str) -> bool`, `close() -> None`. Constants `IDLE_S = 20 * 60`, `AFTER_SENT_S = 3 * 60`.
  - Threading: one worker thread owns Playwright (sync API is single-threaded). `paste` and `close` enqueue commands
    the worker runs inside `tick`; `tick` also enforces `IDLE_S` (from `last_input`) and `AFTER_SENT_S`. Paste uses
    `page.keyboard.insert_text(text)` on the focused element. On any exception the state becomes `error` with a
    VOICE.md-style message, the context closes, and the lock frees.

- [ ] **Step 1: Write tests** against a local headless Chromium launched with `--remote-debugging-port=0`
  (read the port from `DevToolsActivePort`), and the existing fixture server:
  `test_cdp_url_resolves_names` (`Box("localhost")` → `http://127.0.0.1:…`);
  `test_off_by_default` (`box_from` with no `finish` key → None); `test_demo_never_offers_it` (`on_server: true`
  plus `AUTOAPPLY_DEMO=1` → None);
  `test_start_fills_then_ready` (state goes `starting`→`filling`→`ready`; `unanswered` matches the Report);
  `test_second_start_is_busy`; `test_paste_types_into_focused_field`;
  `test_confirmation_marks_sent_once` (fixture page submits itself; `mark_sent` called exactly once with the job id;
  state `sent`); `test_idle_close` (monkeypatch `IDLE_S = 1`; state `closed`, browser has 0 contexts, a new `start`
  works); `test_browser_gone_frees_lock` (kill the Chromium mid-`ready`; state becomes `error`; a new `start` returns
  `{"started": True}` once a new Chromium is up); `test_health_reports_version_mismatch_field`.
- [ ] **Step 2: Run, expect FAIL** (`pytest tests/test_finish_here.py -v`).
- [ ] **Step 3: Implement `autoapply/finish_here.py`.**
- [ ] **Step 4: Run, expect PASS;** then the full suite (`pytest -q`).
- [ ] **Step 5: Commit** — "Finish here: one session at a time on the server's browser".

### Task 5: Routes, the viewer page and noVNC

**Files:**
- Modify: `autoapply/web.py` (extract `_mark_finished(job_id: str, confirmation: str) -> dict` from `/finished`
  and use it there; add routes); `pyproject.toml` (add `"websockets>=12"`)
- Create: `autoapply/templates/finish_here.html`, `autoapply/static/novnc/` (noVNC v1.5.0 `core/`, `vendor/`,
  `LICENSE.txt`, copied from the release tarball; record the version and SHA-256 of the tarball in
  `autoapply/static/novnc/VERSION`)
- Test: `tests/test_finish_here_web.py`

**Interfaces:**
- Consumes: `finish_here.Sessions`, `box_from`, `health`; `_mark_finished`.
- Produces routes (all behind the existing sign-in middleware; declare `status`, `paste`, `close` before the
  `{job_id:path}` ones):
  - `GET /finish-here/status` → `Sessions.status()` JSON.
  - `POST /finish-here/paste` (form `text`) → `{"ok": bool}`; `POST /finish-here/close` → 303 to `/job/<id>`.
  - `POST /finish-here/{job_id:path}` → builds the plan as `/bundle` does (including `finish.record_run`), calls
    `start(..., mark_sent=_mark_finished)`; 303 to `GET /finish-here/{job_id}`; if busy with another job, 303 to the
    viewer of the running job with `?busy=1`. 404 when the feature is off.
  - `GET /finish-here/{job_id:path}` → `finish_here.html` with `job`, `status`, `busy`.
- Viewer copy (VOICE.md): title "Finish it here"; while filling "Filling in the form…"; ready "It's filled in. Answer
  the captcha or code, check it, and press Submit on the site."; sent "Sent. That's one more door you knocked on.";
  closed "Closed. Nothing was sent from here."; busy "Another application is open in the browser."
  with a **Close it** button; error "We lost the browser. Try again in a moment." One primary action per state.
  Unanswered questions listed under "It couldn't answer these". **Paste text** box + button. **Done** (closes).
  noVNC `RFB` on `wss|ws://<host>/finish-here/vnc`, `scaleViewport = true`, `resizeSession = false`; viewer fills the
  width on a phone.

- [ ] **Step 1: Write tests** with `web_client.client` and a fake `Sessions` injected via
  `app.state.finish_sessions`: `test_routes_404_when_off`; `test_signed_out_viewer_redirects_to_login`;
  `test_start_redirects_to_viewer`; `test_busy_redirects_to_running_job`; `test_status_json_shape`;
  `test_paste_forwards_text`; `test_finished_endpoint_still_marks_sent` (existing `/finished` behaviour unchanged after
  the extract); `test_viewer_has_one_primary_action_per_state` (render each state, count `class="btn primary"` == 1).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** the routes, template and vendored noVNC.
- [ ] **Step 4: Run, expect PASS;** full suite.
- [ ] **Step 5: Commit** — "Finish here: viewer page and routes".

### Task 6: The VNC relay and its own sign-in check

**Files:**
- Create: `autoapply/vnc_relay.py`
- Modify: `autoapply/web.py` (WebSocket route `/finish-here/vnc`)
- Test: `tests/test_vnc_relay.py`

**Interfaces:**
- Produces: `async def relay(ws: WebSocket, host: str, port: int, on_client_data: Callable[[], None]) -> None`:
  accepts the WebSocket with subprotocol `binary` when offered, opens `asyncio.open_connection(host, port)`, pumps
  bytes both ways until either side closes, calls `on_client_data()` on every client frame (that is
  `Sessions.touch`). Route checks, in order, before `accept`: session cookie valid (`auth.valid_cookie`) else close
  `4401`; `Origin` header host equals the request `Host` else close `4403`; a session in state `filling|ready|sent`
  else close `4404`.

- [ ] **Step 1: Write tests** with an in-test asyncio TCP echo server standing in for VNC:
  `test_relay_echoes_bytes`; `test_no_cookie_closes_4401`; `test_foreign_origin_closes_4403`;
  `test_no_session_closes_4404`; `test_client_frames_touch_session`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS;** full suite.
- [ ] **Step 5: Commit** — "Finish here: VNC relay behind the sign-in".

### Task 7: Entry points: job page, brief, setup step, Settings health

**Files:**
- Modify: `autoapply/templates/job.html` (finish card), `autoapply/web.py` (job, setup and settings context:
  `finish_on`, `box_health`), `autoapply/brief.py` (Ready item line), `autoapply/templates/setup.html` (new step
  `id="finish"`), `autoapply/templates/settings.html` (one health row)
- Test: `tests/test_finish_here_web.py`, `tests/test_daily.py` or the existing brief tests

**Interfaces:**
- Consumes: `box_from`, `health`.
- Behaviour:
  - Job page, feature on and box healthy: the card's first step is a form `POST /finish-here/<id>` with the primary
    button **Finish it here**; the `autoapply-finish` command follows under "Or on your Mac"; "fill it in by hand"
    stays last. Feature off or box down: the card is exactly today's (box down adds one line: "The browser on your
    server isn't answering, so use your Mac for now.").
  - Brief: when on, a Ready item reads `Review · Finish it here` linking `/job/<id>#finish`, instead of the
    `autoapply-finish` code. When off, unchanged.
  - Setup step "Finish applications in the browser" (optional): what it does in two sentences; the command for the
    install type (`docker compose --profile finish up -d` when `/.dockerenv` exists, else
    `docker compose -f docker-compose.finish.yml up -d`); a **Check** button that calls `health` and, when ok, sets
    `finish.on_server: true` in `config.yaml` with the right host (`finish` in Docker, `127.0.0.1` otherwise).
    Without Docker available it says the Mac command is the way. Writes `config.yaml` with a `.bak-<date>` copy first,
    as other setup steps do (follow the existing setup writer).
  - Settings: "Browser on your server: running (version X)" / "not answering" / "version X, the app is Y: update it
    with <command>".

- [ ] **Step 1: Write tests:** `test_job_card_offers_finish_here_when_healthy`, `test_job_card_unchanged_when_off`
  (HTML of the card equal to a snapshot taken before the change), `test_job_card_box_down_line`,
  `test_brief_ready_line_on_and_off`, `test_setup_check_turns_it_on` (fake `health` ok → config has `on_server: true`
  and a backup file exists), `test_settings_version_mismatch_line`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS;** full suite; then measure the job page and viewer with the design-standard probe
  (`flows/design-tokens-probe.js` on llm) at 390 px and 1280 px.
- [ ] **Step 5: Commit** — "Finish it here on the job page, the brief, setup and Settings".

### Task 8: Docs

**Files:**
- Modify: `README.md` (install: the optional finish profile), `docs/dashboard.md` (Finish with the command → Finish it
  here first), `docs/setup.md` (the new step, both install types, how to update the box), `docs/privacy-and-permissions.md`
  (a browser on your server visits the job site through a proxy that cannot reach your home network; nothing kept after
  it closes; no files of yours are copied into it), `CLAUDE.md` and `AGENTS.md` (new module, routes, config keys,
  ports, images).

- [ ] **Step 1: Write the docs.** Same voice and format as the existing pages.
- [ ] **Step 2: Check** every command in them by running it on the test host (Task 9).
- [ ] **Step 3: Commit** — "Docs: finishing applications in the browser".

### Task 9: End-to-end on a copy, then Harun's install

**Files:** none in the repo (record results in the PR and Docmost).

- [ ] **Step 1: Copy instance on ubuntu.** Clone the branch to `~/repos/jsp-finish-test`, new venv, a config with a
  separate `data_dir` holding a **copy** of `autoapply.db` (opened read-write only in the copy), port `8799` on
  `127.0.0.1`, plus `docker compose -f docker-compose.finish.yml -p jspfinishtest up -d --build` with
  `FINISH_CDP_PORT=9232 FINISH_VNC_PORT=5910`. Add a fixture job whose plan points at the repo's `tests/fixtures/gh`
  form. The box cannot reach the host (that is the proxy doing its job), so serve `tests/fixtures` with
  `python3 -m http.server` on `127.0.0.1` and give it its own quick tunnel; the fixture job's URL is that public address.
- [ ] **Step 2: Quick tunnel:** `cloudflared tunnel --url http://127.0.0.1:8799` (throwaway trycloudflare.com address).
  From the Mac (Playwright) and from Harun's phone if he is around: sign in, **Finish it here**, see the filled form,
  type in a field, paste text, reach the fixture confirmation → the job shows sent with the command. Signed out: viewer
  redirects, WebSocket closes 4401. Journal shows public IPs, not 127.0.0.1.
- [ ] **Step 3: Idle and crash:** set `IDLE_S` to 60 via env for the copy only; confirm the close; `docker restart` the
  box mid-session; confirm `error` and a clean restart.
- [ ] **Step 4: Docker install:** on llm (no real data there), `docker compose --profile finish up -d --build` from the
  branch in a scratch folder, repeat Step 2's checks through `ssh -L`. Then `docker compose up -d` without the profile:
  no `finish` containers start.
- [ ] **Step 5: Clean up:** stop the quick tunnel, `docker compose ... down`, `docker rmi` the test images, remove
  `~/repos/jsp-finish-test` and the llm scratch folder (monorepo cleanup rule).
- [ ] **Step 6: Open the PR** (only after Harun says to push), CI green, merge on Harun's approval; release tag per the
  repo's release flow.
- [ ] **Step 7: Harun's install:** on ubuntu `~/auto-apply`: `git pull`, `pip install -e .`, `docker compose -f
  docker-compose.finish.yml up -d`, run the setup step's **Check**, `systemctl --user restart autoapply-review`.
  Harun finishes one real application himself. Update the homelab inventory (two containers), Gatus if a check is
  added, the Docmost change log and a Docmost page "Finish it here" (design, rollout, rollback: set
  `finish.on_server: false` and `docker compose -f docker-compose.finish.yml down`), brainPal overlay (new
  module + images) with a rescan and live update, and memory.
