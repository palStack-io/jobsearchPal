#!/bin/sh
set -u
if [ "${FORWARD_BOX:-}" = "1" ]; then
  socat TCP-LISTEN:9222,fork,reuseaddr TCP:${BOX_HOST:-finish}:9222 &
  socat TCP-LISTEN:5900,fork,reuseaddr TCP:${BOX_HOST:-finish}:5900 &
fi
exec squid -N -f /etc/squid/squid.conf
