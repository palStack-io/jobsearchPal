# The browser box for "Finish it here": a visible Chromium on a virtual screen, driven by jobsearchPal over the
# DevTools protocol (9222) and shown to you through VNC (5900). It mounts nothing: files reach it over 9222.
# It reaches the internet only through the finish-proxy container (see docker-compose.finish.yml).
FROM debian:bookworm-slim
ARG VERSION=dev
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium xvfb x11vnc socat fonts-liberation fonts-noto-color-emoji \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 1000 box \
 && printf '%s\n' "$VERSION" > /etc/jobsearchpal-finish-version
COPY start.sh /usr/local/bin/start.sh
RUN chmod 755 /usr/local/bin/start.sh
USER box
ENV DISPLAY=:99 FINISH_PROXY=http://finish-proxy:3128
EXPOSE 9222 5900
CMD ["/usr/local/bin/start.sh"]
