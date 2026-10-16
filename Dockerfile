# oclocal — self-contained image: opencode CLI + the OpenAI bridge.
FROM debian:bookworm-slim

ARG TARGETARCH
ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/data/home \
    OPENCODE_DISABLE_PROJECT_CONFIG=true \
    OPENCODE_DISABLE_AUTOUPDATE=true \
    OPENCODE_URL=http://127.0.0.1:8899 \
    BRIDGE_HOST=0.0.0.0 \
    BRIDGE_PORT=8899

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends ca-certificates curl python3 \
 && rm -rf /var/lib/apt/lists/*

# opencode CLI (the free-tier holder). Pinned is not possible upstream, so this
# tracks latest; `opencode --version` reports what landed.
RUN curl -fsSL https://opencode.ai/install | bash \
 && test -x /root/.opencode/bin/opencode || test -x /usr/local/bin/opencode
ENV PATH=/root/.opencode/bin:/usr/local/bin:$PATH

WORKDIR /app
COPY bridge.py /app/bridge.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# isolated config so no host AGENTS.md persona leaks into completions
RUN mkdir -p /data/home/.config/opencode /data/workspace \
 && printf '{\n  "$schema": "https://opencode.ai/config.json",\n  "autoupdate": false,\n  "instructions": []\n}\n' \
      > /data/home/.config/opencode/opencode.json \
 && touch /data/workspace/.keep

EXPOSE 8899
ENTRYPOINT ["/app/entrypoint.sh"]
