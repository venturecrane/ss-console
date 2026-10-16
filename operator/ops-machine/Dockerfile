# smd-ops: SMD's own operations machine (2026-10-16).
#
# WHY. Client operations (seat rebuilds, seat probes, chronology runs, the
# work-health triage agent) ran from the Captain's laptops, which therefore held
# every client seat's production keys and copies of client files. This Machine
# is where that work runs instead: one private Fly Machine, no public services,
# an encrypted volume for the ops user's home, driven by the Captain over Fly's
# private network (fly ssh / Remote Control) under his own Claude login.
#
# Tool versions are pinned to what the laptop ran on the day of the move, so the
# scripts behave the same. Bump them deliberately.
FROM docker.io/library/node:26-bookworm-slim AS node
FROM ghcr.io/astral-sh/uv:0.9.30 AS uv

FROM docker.io/library/debian:13.4

ARG FLYCTL_VERSION=0.4.115
ARG INFISICAL_VERSION=0.43.141
ARG GH_VERSION=2.102.0
ARG CLAUDE_CODE_VERSION=2.1.301

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git tmux jq ripgrep sqlite3 openssh-client less procps \
      python3 python3-venv python3-pip build-essential unzip cron gnupg sudo locales \
 && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY --from=uv /uv /uvx /usr/local/bin/

RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && curl -fsSL "https://github.com/superfly/flyctl/releases/download/v${FLYCTL_VERSION}/flyctl_${FLYCTL_VERSION}_Linux_x86_64.tar.gz" \
      | tar -xz -C /usr/local/bin flyctl \
 && ln -s flyctl /usr/local/bin/fly \
 && curl -fsSLo /tmp/infisical.deb "https://github.com/Infisical/cli/releases/download/v${INFISICAL_VERSION}/infisical_${INFISICAL_VERSION}_linux_amd64.deb" \
 && curl -fsSLo /tmp/gh.deb "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.deb" \
 && apt-get install -y /tmp/infisical.deb /tmp/gh.deb \
 && rm -f /tmp/*.deb

# The ops user. Its home is the encrypted volume (fly.toml [mounts]); nothing
# client-related is written anywhere else on the Machine.
RUN useradd --uid 1000 --create-home --shell /bin/bash ops
COPY entrypoint.sh /usr/local/bin/ops-entrypoint
RUN chmod 0755 /usr/local/bin/ops-entrypoint

# Root only for the entrypoint itself: it chowns the freshly mounted volume and
# starts cron, which runs the ops user's jobs AS ops. Every interactive session
# and scheduled job runs as ops (fly ssh console -u ops; crontab -u ops).
# nosemgrep: dockerfile.security.missing-user-entrypoint.missing-user-entrypoint
ENTRYPOINT ["/usr/local/bin/ops-entrypoint"]
