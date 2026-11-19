# The public demo

`AUTOAPPLY_DEMO=1` turns a copy of jobsearchPal into a demo anyone can click around in. **Never set it on your own
install: it turns sign-in off.**

## What changes in demo mode

- Nobody signs in. `/login`, `/logout` and `/account` go to the dashboard. A banner says the data is fictional.
- Only form posts that change the demo's own database are allowed (logging an application, leads, the day plan,
  contacts, work-search tasks, marking a job as sent by hand, and so on; the list is `ALLOWED_POSTS` in
  `autoapply/demo.py`). Everything else gets "That part is off in the demo": Run now, Prepare, Auto apply, letters,
  Sync email, Settings, every Setup sign-in, importing a CSV, and Refill and Submit on a job page.
- `start_run` never starts the agent, and a pasted link is never fetched, even if a route were allowed by mistake.
- Form posts are capped at 64 KB and 30 a minute per visitor (`CF-Connecting-IP` behind Cloudflare).

## How palStack hosts it (jobsdemo.palstack.io)

- Image built with `--build-arg INSTALL_CLAUDE=0 --build-arg INSTALL_CODEX=0`: no agent CLIs inside.
- At every start, `scripts/demo-data.py /demo` writes the fictional data into a tmpfs, so a restart throws away
  whatever visitors changed. A cron restarts it every hour.
- The container's network has no outbound NAT (`com.docker.network.bridge.enable_ip_masquerade: "false"`), so it
  cannot reach the internet at all; read-only root filesystem, code mounted read-only, all capabilities dropped,
  512 MB memory, one CPU, 128 processes.
- The port is published on 127.0.0.1 only; the Cloudflare tunnel is the only way in.

Run one locally:

```bash
python3 scripts/demo-data.py /tmp/demo && cd /tmp/demo && AUTOAPPLY_DEMO=1 autoapply serve   # http://127.0.0.1:8799
```
