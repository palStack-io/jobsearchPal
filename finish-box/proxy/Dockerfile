# The only way out for the browser box: web ports 80 and 443, never a home, tailnet or Docker address.
# With FORWARD_BOX=1 (venv installs) it also forwards 9222 and 5900 to the box, because Docker publishes no ports
# for a container that is only on an internal network.
FROM alpine:3.20
RUN apk add --no-cache squid socat
COPY squid.conf /etc/squid/squid.conf
COPY start.sh /usr/local/bin/start.sh
RUN chmod 755 /usr/local/bin/start.sh
EXPOSE 3128 9222 5900
CMD ["/usr/local/bin/start.sh"]
