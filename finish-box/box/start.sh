#!/bin/sh
# Virtual screen, VNC on it, the DevTools port forwarded off loopback, then Chromium (restarted if it exits, so
# closing the last window never stops the box).
set -u
# /tmp survives `docker restart`: a stale lock from the last run would keep Xvfb (and so Chromium) down.
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
Xvfb :99 -screen 0 1280x900x24 -nolisten tcp &
for _ in $(seq 50); do [ -e /tmp/.X11-unix/X99 ] && break; sleep 0.1; done
x11vnc -display :99 -rfbport 5900 -nopw -shared -forever -quiet -noxdamage &
socat TCP-LISTEN:9222,fork,reuseaddr TCP:127.0.0.1:9223 &
while true; do
  rm -rf /tmp/chrome
  chromium --no-sandbox --no-first-run --no-default-browser-check --disable-dev-shm-usage \
    --remote-debugging-address=127.0.0.1 --remote-debugging-port=9223 --user-data-dir=/tmp/chrome \
    --proxy-server="$FINISH_PROXY" --window-size=1280,900 --window-position=0,0 \
    --allow-file-access-from-files about:blank
  sleep 1
done
