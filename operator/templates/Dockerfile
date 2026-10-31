# SMD Operator — customer-shape Hermes container
#
# Builds Hermes from upstream at a pinned ref, installs the hermes-smd
# overlay plugins + companion CLI, layers in our Operator adapter / skill
# library / connectors / safety_substrate tests.
#
# Memory (ADR 0016, revised 2026-05-30): Phase 1 runs on Hermes' always-on
# flat-file core (MEMORY.md/USER.md). Honcho — the inferred-memory engine —
# is deferred to Phase 2, when it is vendored as the real plastic-labs/honcho
# v3.0.7 source (api + deriver, pgvector). The earlier in-container `honcho-ai`
# server install was fictional (that package is the client SDK, not the server)
# and is removed. Postgres + Redis remain installed below but are NOT started
# at boot in Phase 1 — they exist only to serve Honcho and are reused in Phase 2.
#
# The container runs a multi-process startup under tini (PID 1, zombie
# reaper). bootstrap.sh fetches customer.yaml, materializes profiles, runs the
# safety substrate, and execs the Hermes gateway. See bootstrap.sh.
#
# Per §6 of the locked build plan and ADR 0019: customer.yaml is NOT baked
# into the image. Provisioning writes it to R2 at vaults/<slug>/customer.yaml;
# bootstrap.sh fetches it to /opt/data/customer.yaml on first boot.
#
# Per-customer build context expects:
#   - operator/adapter/         (Operator adapter — Python)
#   - operator/skills/          (canonical skill library — markdown)
#   - operator/connectors/      (Tier-1 custom MCP wrappers)
#   - operator/safety_substrate/ (Phase A.5 invariant fixtures + runner)
#   - operator/templates/bootstrap.sh

# ---------- Build args ----------
# THE Hermes pin. These three ARG defaults are the single source of truth for
# which upstream Hermes every seat runs (ADR 0024, amended 2026-10-19): one image
# is built per release and deployed to every seat, so a per-seat pin could only
# ever disagree with the image. customer.yaml no longer carries hermes_ref for
# the image to obey; provision-customer.sh and `fleet build` pass no Hermes
# build-args, and boot-smoke's hermes-sha-matches-pin reads these lines. The
# shape is {upstream release tag}@{40-hex-sha}. Upstream's tags were dated
# (v2026.9.14) through the 0.21 line and are semver (v0.22.5) since; nothing
# parses the tag, only the sha is asserted. We clone NousResearch/hermes-agent
# directly; the venturecrane/hermes-agent fork was retired by ADR 0024.
#
# HERMES_REF is the full pin string, retained for provenance and logging.
ARG HERMES_REF=v0.22.5@bf0203a794a07aac3ff764972ab9087baa4e4f4e

# Upstream date-tag to clone (the part before '@').
ARG HERMES_UPSTREAM_TAG=v0.22.5

# Upstream commit SHA (the part after '@'). The post-clone assertion verifies
# the cloned HEAD matches this exactly; any divergence (upstream re-tag, stale
# pin) fails the build. This is the integrity check proving we ship unmodified
# upstream Hermes.
ARG HERMES_UPSTREAM_SHA=bf0203a794a07aac3ff764972ab9087baa4e4f4e

# The hermes-smd overlay (plugin pack + the `hermes-smd` CLI + the importable
# `shared` policy core) is this repo's operator/hermes-smd-overlay/, built from the
# build context below (ADR 0095). There is no overlay pin: the ss-console commit
# the image is built from (SS_CONSOLE_SHA, declared late) is its version.

# No CUSTOMER_SLUG build-arg (2026-10-19, fleet release). The image is identical
# across seats; the slug reaches the Machine only as fly.toml [env]
# CUSTOMER_SLUG / SMD_CUSTOMER_SLUG. No RUN ever read it, but an ARG that differs
# per seat still forced a separate build per seat, so it is gone.

# ---------- Stage 1: clone Hermes upstream at the pinned tag, assert SHA ----------
FROM alpine/git:2.45.2 AS hermes_source
ARG HERMES_UPSTREAM_TAG
ARG HERMES_UPSTREAM_SHA
WORKDIR /src
RUN git clone --depth 1 --branch ${HERMES_UPSTREAM_TAG} \
    https://github.com/NousResearch/hermes-agent.git . \
 && git rev-parse HEAD > /src/HERMES_SHA \
 && INSTALLED_SHA="$(cat /src/HERMES_SHA)" \
 && if [ "${INSTALLED_SHA}" != "${HERMES_UPSTREAM_SHA}" ]; then \
      echo "FATAL: upstream tag ${HERMES_UPSTREAM_TAG} points at ${INSTALLED_SHA}" >&2 ; \
      echo "       Expected pinned SHA ${HERMES_UPSTREAM_SHA} (the Dockerfile's HERMES_UPSTREAM_SHA)." >&2 ; \
      echo "       Upstream re-tagged, or the pin is stale; refusing to build." >&2 ; \
      echo "       See ADR 0024." >&2 ; \
      exit 1 ; \
    fi

# ---------- Stage 2: runtime image ----------
FROM ghcr.io/astral-sh/uv:0.11.6-python3.13-trixie AS uv_source
# Node 26 source stage (Hermes v2026.8.18 / 0.20.4 promotion, ss-console#2444).
# Debian 13 trixie's apt `nodejs` is 20.19.x, EOL April 2026. Upstream Hermes
# pins its toolchain to Node 26 everywhere and, as of v2026.8.18, ships
# `.npmrc engine-strict=true` with `engines.node >=22.22.0` — so `npm install`
# over the Hermes tree hard-fails on the apt Node (vfy_01M0DP257XDYJ0Z04V1ZF57G2X:
# seat had v20.19.2 / npm 9.2.0). We copy node + npm from the official image.
# Node is needed at RUNTIME too: `@oktopeak/clio-mcp` (below) is a Node stdio
# MCP server the gateway launches.
#
# Pinned 2026-10-28 (ss-console#3564): node:26.12.1-bookworm-slim index digest
# 88e48077..., NODE_VERSION 26.12.1 in the linux/amd64 config blob, bundled npm
# 11.20.0 (nodejs.org/dist/index.json). That bump fixes CVE-2026-111497 (a
# high-severity Brotli-decoder RCE in Node < 26.12.1; the previous digest
# 9e6f9357... was 26.5.1). npm 11.20.0 satisfies Hermes' `engines.npm`
# range `<11.10.0 || >=11.17.0` (v0.22.5 package.json), and the image build
# prints `node --version` / `npm --version`.
# Upstream: v0.22.5 (bf0203a7) has no `node_source` stage any more. Its image
# takes Node from its package manager's lock (pm/lock.json: node v26.12.1,
# npm 12.0.2). So we now DELIBERATELY diverge in how we get Node (the official
# image's binaries, pinned by digest), at the same Node version; npm differs
# (11.20.0 vs 12.0.2) and both satisfy the engines range. Every digest bump
# re-checks the engines.npm range and this comparison.
FROM node:26.12.1-bookworm-slim@sha256:88e480776c6149ff1d098cd304f44bf18057fb5d284a3329b78162d29821e28c AS node_source
# Python 3.14 source stage for the Hermes venv (Hermes v0.22.5 promotion,
# ss-console#3564). Hermes v0.22 supports only 3.14: its uv.lock's
# supported-markers are python_full_version >= '3.14', and Debian 13 ships 3.13.
# The interpreter is copied from the official image (same Debian 13 trixie, so
# the same glibc and the same apt libssl/libsqlite3 its extension modules link
# against), pinned by index digest so a rebuild gets the same bytes. It is NOT
# a uv-managed download: that lands under /root (mode 0700) and the non-root
# uids cannot execute it (the rc=126 crash-loop recorded at ENV UV_PYTHON below).
# The image builds python3.14 with --enable-shared and RUNPATH $ORIGIN/../lib,
# so bin/ + lib/ relocate to /opt/python3.14 as a unit; the runtime stage checks
# with ldd that every library they need resolves on this base.
FROM python:3.14.10-slim-trixie@sha256:fa99b65193ea9dbde3fc2367f43d3a2907571ce2a15213a86f0053e1d49d8456 AS python314_source
FROM debian:13.4

# LAYER CACHE ORDER IS LOAD-BEARING (fleet release, 2026-10-19). Every RUN after
# an ARG declaration in a stage sees that ARG and is invalidated when its value
# changes. SS_CONSOLE_SHA changes with every release, so it is declared
# immediately before its first use below, never here: apt, npm, uv and the
# Hermes build stay cached across releases and a code-only release rebuilds only the layers
# from the overlay install down. tests/operator-dockerfile.test.ts pins this.
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright
ENV HERMES_HOME=/opt/data
ENV PATH="/opt/data/.local/bin:/usr/lib/postgresql/17/bin:${PATH}"
# Pin every uv invocation to the SYSTEM interpreter (Hermes v0.20.4 promotion,
# ss-console#2444). The v2026.8.18 tree ships `.python-version: 3.11`; because
# this Dockerfile copies the WHOLE tree before `uv sync` (upstream copies only
# pyproject.toml + uv.lock, so their image never sees the pin), every bare
# `uv sync` / `uv venv` — the Hermes venv, the workspace-broker venv, the
# per-connector venvs — resolved that pin, downloaded a managed CPython into
# /root/.local/share/uv (mode 0700), and symlinked venv pythons into it. The
# non-root broker uid then got `Permission denied` executing its own venv
# python (rc=126 crash-loop; the pilot seat went down and was restored by
# image-ref rollback). UV_PYTHON outranks .python-version, so this keeps the
# venvs that name no interpreter (the workspace broker, the connectors, the
# medchron runner) on Debian 13's python3, 3.13. The one exception is the
# Hermes venv: since v0.22 Hermes runs only on 3.14 (its tree's
# .python-version says 3.14), so its `uv sync` passes an explicit --python for
# /opt/python3.14 (the python314_source stage above, world-readable, never
# under /root), which outranks this variable, and --no-python-downloads so a
# missing interpreter fails the build instead of fetching a managed one.
ENV UV_PYTHON=/usr/bin/python3

# ---------- System packages ----------
# tini: PID 1 zombie reaper (signals + orphaned MCP stdio subprocesses).
# postgresql-17 + redis: installed for Phase 2 Honcho's data plane but NOT
# started at boot in Phase 1 (Debian 13 trixie ships PostgreSQL 17 as default;
# postgresql-16 is not in trixie's repos — keep 17 so the Phase-2 vendor lands
# cleanly). awscli: bootstrap.sh fetches customer.yaml from R2 (S3-compat).
# curl + ca-certificates: R2 fetch + general TLS.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential curl unzip python3 python3-pip python3-venv \
      ripgrep ffmpeg gcc python3-dev libffi-dev procps git openssh-client \
      docker-cli tini ca-certificates util-linux \
      postgresql-17 postgresql-client-17 redis-server awscli \
      poppler-utils fonts-dejavu-core antiword \
      libssl3t64 libsqlite3-0 libffi8 libbz2-1.0 liblzma5 libzstd1 zlib1g \
      libreadline8t64 libncursesw6 libtinfo6 libuuid1 libgdbm6t64 libdb5.3t64 \
 && rm -rf /var/lib/apt/lists/*
# The libssl3t64 ... libdb5.3t64 line (Hermes v0.22.5 promotion, ss-console#3564):
# every shared library the /opt/python3.14 interpreter's extension modules link
# (read from the python:3.14.10-slim-trixie ELF headers, _tkinter excepted).
# Most arrive anyway as dependencies of the packages above; naming them makes
# the interpreter's needs explicit, and the ldd check after its COPY fails the
# build on any that does not resolve.
# antiword (2026-10-09): the negotiation watch reads offer letters saved as
# legacy Word .doc with it (the build asserts it).
# poppler-utils + fonts-dejavu-core (ss#2614): the chronology runner's exhibit
# rendering fallback and matplotlib's timeline visuals need a real font.
# antiword (2026-10-07): the litigation status job reads court papers a firm
# saved as legacy Word .doc; antiword is the extractor (the build asserts it).

# ---------- runtime principals ----------
# The gateway and credential broker are distinct non-root principals. The
# shared group grants socket access only; it grants no credential-file access.
# audit-readers (OP-P1-4): the broker (workspace-broker) OWNS the audit ledger
# file; hermes joins audit-readers to READ it via group but, lacking ownership
# and group-write, cannot open it for write. The agent's only write path is the
# broker's append-only audit_append verb.
RUN groupadd -r workspace-connectors \
 && groupadd -r audit-readers \
 && useradd -u 10000 -m -d /opt/data hermes \
 && useradd -u 10001 -r -m -d /opt/workspace-broker workspace-broker \
 && usermod -a -G workspace-connectors hermes \
 && usermod -a -G workspace-connectors workspace-broker \
 && usermod -a -G audit-readers hermes \
 && usermod -a -G audit-readers workspace-broker
# medchron (ss#2614): the uid the chronology runner's driver child runs as. No
# home, no shell, no group beyond smokeball-token, which is the ONE thing it
# shares with hermes: the Smokeball refresh-token file (setgid dir, 0660),
# because both principals mint against the same rotating token. The runner
# daemon itself stays root (it owns the queue and the job dirs); only the
# stages that read the matter and call the model run as this uid.
RUN groupadd -r smokeball-token \
 && useradd -u 10002 -r -M -d /nonexistent -s /usr/sbin/nologin medchron \
 && usermod -a -G smokeball-token medchron \
 && usermod -a -G smokeball-token hermes
COPY --chmod=0755 --from=uv_source /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/

# Node 26 + npm from the node_source stage (see the stage comment above). npm
# and npx are recreated as symlinks into the bundled npm install, mirroring
# upstream's Dockerfile; no corepack (unbundled upstream; nothing here needs it).
COPY --chmod=0755 --from=node_source /usr/local/bin/node /usr/local/bin/
COPY --from=node_source /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && node --version && npm --version

# Python 3.14 for the Hermes venv, from the python314_source stage (see the
# stage comment above): the interpreter binary, its shared library and stdlib,
# and its headers (a package with no 3.14 wheel would build against them).
# /opt/python3.14 is root-owned and world-readable and executable, because the
# hermes uid (10000) runs the gateway on it. The checks fail the BUILD rather
# than a seat: every library the interpreter and its extension modules need
# resolves (_tkinter excepted; no Tk is installed and nothing imports it), and
# the hermes uid can execute it and import the modules the gateway uses.
COPY --from=python314_source /usr/local/bin/python3.14 /opt/python3.14/bin/python3.14
COPY --from=python314_source /usr/local/lib/ /opt/python3.14/lib/
COPY --from=python314_source /usr/local/include/python3.14/ /opt/python3.14/include/python3.14/
RUN chown -R root:root /opt/python3.14 \
 && chmod -R a+rX /opt/python3.14 \
 && missing="$(ldd /opt/python3.14/bin/python3.14 /opt/python3.14/lib/libpython3.14.so.1.0 \
      $(ls /opt/python3.14/lib/python3.14/lib-dynload/*.so | grep -v _tkinter) 2>&1 | grep 'not found' || true)" \
 && { [ -z "$missing" ] || { echo "FATAL: /opt/python3.14 needs libraries this image lacks:" >&2; echo "$missing" >&2; exit 1; }; } \
 && setpriv --reuid=10000 --regid=10000 --clear-groups /opt/python3.14/bin/python3.14 -I -c \
      "import sys, ssl, sqlite3, ctypes, lzma, bz2, zlib, decimal, uuid, compression.zstd; \
assert sys.version_info[:2] == (3, 14), sys.version; \
assert sys.prefix == '/opt/python3.14', sys.prefix; \
print('python3.14 OK for uid 10000:', sys.version.split()[0], ssl.OPENSSL_VERSION, 'sqlite', sqlite3.sqlite_version)"

WORKDIR /opt/hermes

# ---------- Pull Hermes source from stage 1 ----------
COPY --from=hermes_source /src/ /opt/hermes/
COPY --from=hermes_source /src/HERMES_SHA /opt/hermes/HERMES_SHA

# ---------- Layer-cached Hermes deps install ----------
ENV npm_config_install_links=false
# Playwright browsers are PREFETCHED with curl, then `playwright install` runs
# against the already-populated cache (Hermes v0.20.4 promotion, ss-console#2444).
# WHY: at v2026.8.18 `npx playwright install --with-deps chromium --only-shell`
# deterministically WEDGED on the Depot builder — the chromium-headless-shell
# download hit 100% and Node's downloader never observed the socket close (three
# independent builds; one sat silent for 59 minutes). Upstream's own Dockerfile
# wraps the same command in a 3-attempt retry loop (their :201-202) — they hit
# it too — but a retry cannot rescue a hang, and every retry re-enters the same
# downloader. So: `--dry-run` prints each browser's install location + download
# url; curl (hard --max-time, --retry, immune to the close-hang) fetches each
# zip; unzip + the INSTALLATION_COMPLETE marker reproduce playwright's own
# on-disk layout; the real install then finds every browser present, downloads
# nothing, and only installs apt deps. The timeout+retry loop stays as the
# backstop so NO variant of this step can wedge the seat-rebuild path:
# rollback-by-rebuild depends on this Dockerfile finishing.
#
# `npm ci`, not `npm install` (2026-09-25 code review, Dependencies 3): Hermes
# ships a root package-lock.json whose workspaces include web/ and ui-tui/, and
# `npm ci` installs exactly that lock or fails the build if it disagrees with
# package.json, where `npm install` would quietly re-resolve. Checked against
# v2026.9.14 (345cd2b0): `npm ci` on the lock as committed adds 1,340 packages
# and both `npm run build`s below succeed with nothing else installed, which is
# why the former `(cd web && npm install)` and `(cd ui-tui && npm install)`
# lines are gone (inherited from an older upstream Dockerfile; upstream's own
# at this SHA runs only the root install).
RUN npm ci --prefer-offline --no-audit \
 && npx playwright install --dry-run chromium --only-shell > /tmp/pw-plan.txt \
 && cat /tmp/pw-plan.txt \
 && awk '/Install location:/{loc=$3} /Download url:/{print loc" "$3}' /tmp/pw-plan.txt > /tmp/pw-pairs.txt \
 && [ -s /tmp/pw-pairs.txt ] \
 && while read -r loc url; do \
      echo "prefetch: $url -> $loc"; \
      curl -fL --retry 3 --retry-delay 5 --max-time 900 -o /tmp/pw-browser.zip "$url"; \
      mkdir -p "$loc"; \
      unzip -q /tmp/pw-browser.zip -d "$loc"; \
      rm -f /tmp/pw-browser.zip; \
      touch "$loc/INSTALLATION_COMPLETE"; \
    done < /tmp/pw-pairs.txt \
 && for i in 1 2 3; do \
      timeout 900 npx playwright install --with-deps chromium --only-shell && break || \
      { [ "$i" = 3 ] && exit 1; echo "playwright install failed/wedged (attempt $i); retrying in 10s" >&2; sleep 10; }; \
    done \
 && rm -f /tmp/pw-plan.txt /tmp/pw-pairs.txt \
 && npm cache clean --force

# `--extra all` deliberately EXCLUDES the `messaging` extra (python-telegram-bot,
# discord.py, slack-bolt) — verified against the pinned ref's pyproject. Without
# `--extra messaging` the native Telegram adapter imports fail and the platform is
# inert (TELEGRAM_AVAILABLE=False), so a TELEGRAM_BOT_TOKEN env would silently do
# nothing. We add it explicitly: lock-governed (frozen) and forward-aligned with the
# Slack/Discord channels the productized Operator will offer. See ADR 0033.
# `--extra anthropic` (Hermes v0.20.4 promotion, ss-console#2444): v2026.8.18
# moved the anthropic SDK out of `[all]` into its own extra (pinned
# anthropic==0.87.0 for CVE-2026-34450/34452; upstream's own Dockerfile passes
# it too). Without it the gateway boots but EVERY turn dies at
# build_anthropic_client with ImportError — observed live on the pilot seat's
# first 0.20.4 rehearsal turn. bedrock/azure/matrix/otlp/hindsight stay out:
# no seat consumes them.
# `--python /opt/python3.14/...` and `--no-python-downloads` (Hermes v0.22.5
# promotion, ss-console#3564): Hermes v0.22 runs only on 3.14; see ENV UV_PYTHON
# above. The checks after it prove the venv is the 3.14 one, and that the three
# things the gateway's network and session layers rest on work in it: sqlite
# with FTS5 (state.db's session search), an OpenSSL default verify path that
# exists (v0.22 verifies TLS against the OS store, agent/ssl_verify.py), and an
# importable certifi.
RUN uv sync --frozen --no-python-downloads --python /opt/python3.14/bin/python3.14 --no-install-project --extra all --extra messaging --extra anthropic \
 && /opt/hermes/.venv/bin/python -I -c \
      "import os, sqlite3, ssl, sys, certifi; \
assert sys.version_info[:2] == (3, 14), sys.version; \
db = sqlite3.connect(':memory:'); db.execute('CREATE VIRTUAL TABLE t USING fts5(x)'); \
paths = ssl.get_default_verify_paths(); \
assert (paths.cafile and os.path.isfile(paths.cafile)) or (paths.capath and os.path.isdir(paths.capath)), paths; \
assert os.path.isfile(certifi.where()), certifi.where(); \
print('hermes venv OK:', sys.version.split()[0], 'fts5', paths.openssl_capath, certifi.where())"
RUN cd web && npm run build && cd ../ui-tui && npm run build
# --python is explicit because ENV UV_PYTHON (above) would otherwise aim this
# bare `uv pip` at the SYSTEM interpreter (PEP 668 refusal on Debian); an
# explicit flag outranks the env var and targets the project venv uv sync made.
RUN uv pip install --no-cache-dir --no-deps --python /opt/hermes/.venv/bin/python -e "."

# ---------- Hermes install stamp: the version a seat reports ----------
# `hermes --version` on every v0.22.5 seat printed "vunknown (2026.9.24)"
# (2026-10-28). Hermes resolves its identity from /opt/hermes/install-stamp.json
# first and live git second (hermes_cli/version_info.py). This image wrote no
# stamp, and the git fallback cannot answer at runtime: /opt/hermes is root-owned
# and the gateway runs as uid 10000, so every git call dies on "detected dubious
# ownership" and the version degrades to "unknown". Upstream's own image writes
# the stamp with this same script (their Dockerfile, "Image provenance + install
# stamp"); we name the pinned tag and sha explicitly rather than let it read git.
# `--update-mechanism external`: the image is rebuilt and released, it never
# updates itself, and Hermes' updater, channel switch and launcher publication
# all stand down for an external stamp. No `--distribution docker`: that value
# makes pm/runtime.py demand a packaged pm-runtime this image does not build.
# The check after it runs AS uid 10000 (the gateway's uid, where git cannot
# answer) and fails the BUILD unless Hermes reports exactly the pinned tag at the
# pinned sha, so a stamp that stops being read cannot ship.
ARG HERMES_UPSTREAM_TAG
ARG HERMES_UPSTREAM_SHA
RUN /opt/hermes/.venv/bin/python scripts/write_install_stamp.py \
      --output /opt/hermes/install-stamp.json \
      --commit "${HERMES_UPSTREAM_SHA}" \
      --base-version "${HERMES_UPSTREAM_TAG#v}" \
      --distance 0 \
      --source docker \
      --update-mechanism external \
 && chmod 0644 /opt/hermes/install-stamp.json \
 && runuser -u hermes -- /opt/hermes/.venv/bin/python -c \
      "import sys; from hermes_cli.version_info import get_version_info; \
i = get_version_info(); \
want_version, want_sha = sys.argv[1], sys.argv[2]; \
assert i.derived_version == want_version, ('hermes reports', i.derived_version, 'expected', want_version, i); \
assert i.commit == want_sha, ('hermes reports commit', i.commit, 'expected', want_sha); \
print('hermes version OK as uid', __import__('os').getuid(), i.derived_version, i.commit[:12], i.source)" \
      "${HERMES_UPSTREAM_TAG#v}" "${HERMES_UPSTREAM_SHA}"

# ---------- Honcho: deferred to Phase 2 (ADR 0016, revised 2026-05-30) ----------
# No in-container Honcho server is installed. The prior `honcho-ai` pip install
# was fictional — that package is the Honcho CLIENT SDK, not the server; the
# real server is the plastic-labs/honcho v3.0.7 SOURCE repo (fastapi api +
# `python -m src.deriver` worker, pgvector, mandatory LLM provider). Phase 2
# vendors it the same way upstream Hermes is vendored above (clone at a pinned
# tag, own venv). Phase 1 runs on Hermes' flat-file memory core.

# ---------- Pinned third-party Python (every venv built below) ----------
# Every `uv pip install` of a third-party package below installs from one of
# these hash-locked files with --require-hashes, so a seat built today and a
# seat built next month carry the same bytes, and an upstream release (or a
# hijacked one) cannot enter the image without a commit here. Before this
# (2026-09-10 code review, Dependencies HIGH, third review running) the broker
# venv named three packages with no version and the connector and runner venvs
# resolved their pyproject ranges at build time, so no two builds were the same
# and nothing audited what they got. Files are regenerated by
# operator/requirements/compile.sh; bin/tests/test_requirements_pins.py fails
# when a pyproject declares a dependency the matching file does not pin, and
# tests/operator-dockerfile.test.ts fails on any install line that names a
# package without going through a file. Our own local packages install
# afterwards with --no-deps (a path cannot carry a hash).
COPY operator/requirements/ /app/requirements/

# ---------- Security overrides for the Hermes venv ----------
# Hermes' uv.lock at HERMES_UPSTREAM_SHA is installed exactly (the `uv sync
# --frozen` above), with these exceptions and no others. hermes-security.txt
# moves one Hermes-locked package to the release that fixes a named advisory
# (the admission rule is hermes-security.in's header):
#   brotlicffi 1.2.0.2 -> 1.2.1.0: PYSEC-2026-4323 / GHSA-9v8h-qqgm-4m35, an
#     out-of-bounds write in the Brotli decoder, reachable through aiohttp's
#     `br` response decoding. A patch release of a binding; no API change.
# (The certifi and urllib3 overrides of 2026-10-27, DP-1, were removed at the
# v0.22.5 promotion: its lock carries certifi 2026.10.19 and urllib3 2.8.0.)
# Every other Hermes-locked version stays where Hermes put it, and
# pip_audit_gate.py audits this venv's effective set (Hermes' lock with these
# overrides applied) with an empty allowlist. compile.sh resolves the file
# against the rest of Hermes' locked set, so no other Hermes package refuses
# this version; hermes-agent's own exact pin on brotlicffi is the one
# requirement the move leaves unmet, and hermes_pip_check.py (the overlay step
# below) tolerates that one by name, plus the mismatches Hermes' own uv.lock
# overrides record, and fails on anything else. --no-config: this runs in
# /opt/hermes, whose [tool.uv] overrides cannot carry a hash.
RUN uv pip install --no-cache-dir --python /opt/hermes/.venv/bin/python \
      --no-config --require-hashes --no-deps -r /app/requirements/hermes-security.txt

# ---------- hermes-smd overlay (plugins + CLI) ----------
# Pip-install the overlay from the build context (operator/hermes-smd-overlay/,
# ADR 0095: one repository for the seat runtime; until 2026-10-22 this was a
# pinned git URL). It provides the `hermes-smd` console script (the bootstrap
# translator and the customer-sync sidecar) and the importable `shared` policy
# core the runtime plugins depend on. The pack Hermes LOADS is the separate
# COPY further down; this source copy is removed once installed.
#
# Three steps, so no RUNTIME package resolves from PyPI at build time
# (2026-09-25 code review, Dependencies 3; before it this line let uv pick boto3
# and sentry-sdk on every build, outside any lock and outside pip_audit_gate.py):
#   1. the overlay's third-party dependencies that Hermes' own lock does not
#      already install, hash-locked in hermes-overlay.txt (compile.sh derives
#      it from operator/hermes-smd-overlay/pyproject.toml, constrained to Hermes'
#      uv.lock at HERMES_UPSTREAM_SHA with the security override above and
#      Hermes' own lock overrides applied, so no Hermes-locked version moves).
#      Since v0.22.5 this file also carries PyYAML: Hermes moved to ruamel.yaml
#      and no longer installs it, and the overlay imports `yaml`;
#      --no-config for the same reason as the broker venv below: this runs in
#      /opt/hermes, whose [tool.uv] overrides cannot carry a hash;
#   2. the overlay itself, a path install, --no-deps. Its build backend
#      (setuptools, wheel) resolves from PyPI under build isolation, exactly as
#      the Hermes install above does; `--no-build-isolation` for both is the
#      filed follow-up;
#   3. `uv pip check` through hermes_pip_check.py, which fails the BUILD, not
#      a seat, if any requirement the overlay (or anything else in the venv)
#      declares is unmet. It tolerates only hermes-agent's exact pin on each
#      package hermes-security.txt overrides (while the installed version is
#      that file's pin), and the mismatches Hermes' own uv.lock records as
#      overrides (browser-harness's pillow==12.3.0 against the locked 13.0.0),
#      the rule Hermes' installer applies (pm/pip_check.py). That
#      is the backstop for an overlay that grows a dependency before compile.sh
#      is re-run; the `overlay` CI job runs compile.sh and fails on a diff.
COPY operator/hermes-smd-overlay/ /app/overlay-src/
RUN uv pip install --no-cache-dir --python /opt/hermes/.venv/bin/python \
      --no-config --require-hashes --no-deps -r /app/requirements/hermes-overlay.txt \
 && uv pip install --no-cache-dir --python /opt/hermes/.venv/bin/python \
      --no-deps /app/overlay-src \
 && python3 -I /app/requirements/hermes_pip_check.py \
      /opt/hermes/.venv/bin/python /app/requirements/hermes-security.txt /opt/hermes/uv.lock \
 && rm -rf /app/overlay-src

# ---------- Workspace capability broker ----------
# Google libraries and credentials live in a broker-only venv. The Hermes venv
# deliberately has neither, so execute_code and terminal cannot recreate an
# ambient privileged connector path.
COPY operator/workspace_broker/ /opt/workspace-broker/workspace_broker/
# --no-config on every --require-hashes install (2026-09-11). This RUN executes
# under `WORKDIR /opt/hermes` (set above; /app only becomes the workdir later),
# so uv discovers Hermes' own pyproject.toml and applies its `[tool.uv]`
# override-dependencies — including `pynacl>=1.6,<1.7`, which upstream forces to
# dodge a vulnerable discord.py pin. Hash-checking mode then refuses the build,
# because an inherited override is not `==` pinned and cannot carry a hash. The
# pinned file itself is complete and correct; nothing in its closure needs
# pynacl. `--no-config` makes the install depend on the file we pin and nothing
# ambient, which is the property hash-checking is for.
RUN uv venv /opt/workspace-broker/.venv \
 && uv pip install --python /opt/workspace-broker/.venv/bin/python \
      --no-config --require-hashes -r /app/requirements/broker.txt \
 && chmod -R a+rX /opt/workspace-broker \
 && chown -R root:root /opt/workspace-broker

# ---------- Clio MCP server (law vertical practice-management connector) ----------
# @oktopeak/clio-mcp (MIT) is a LOCAL stdio MCP server. The overlay (v0.4.6+)
# materializes customer.yaml `mcp:clio-oktopeak` into the profile mcp_servers
# block as a `clio-mcp` command server, so the binary must be on PATH for the
# gateway to launch. Pinned to 2.0.0, the version the connector reality-check
# and live loop test ran against. It reads its OAuth token from
# ~/.clio-mcp/tokens.enc (seeded by bootstrap.sh) under ENCRYPTION_KEY.
#
# Installed with `npm ci` from operator/npm/clio-mcp/package-lock.json, not
# `npm install -g name@version` (2026-09-25 code review, Dependencies 3): the
# old line pinned the package and let its ~120 transitive packages float to
# whatever the registry served that day. The lock fixes every version and
# integrity hash; security.yml's npm audit reads it and Dependabot security
# updates act on it (the top-level pin moves only by a deliberate edit, see
# .github/dependabot.yml). The bin is linked onto PATH at the
# same /usr/local/bin a global install used. Presence checks prove the
# install so a regression fails the BUILD, not a seat; it is a stdio MCP
# server, so invoking it here would block on stdin.
COPY operator/npm/clio-mcp/package.json operator/npm/clio-mcp/package-lock.json /opt/clio-mcp/
RUN cd /opt/clio-mcp && npm ci --omit=dev --no-audit --no-fund \
 && ln -s /opt/clio-mcp/node_modules/.bin/clio-mcp /usr/local/bin/clio-mcp \
 && test -x "$(command -v clio-mcp)" \
 && test -f /opt/clio-mcp/node_modules/@oktopeak/clio-mcp/package.json \
 && npm cache clean --force

# NOTE: web search needs NO installed binary. It is Hermes' NATIVE brave-free
# provider (plugins/web/brave_free, bundled in the pinned Hermes), selected by
# customer.yaml `backend: native:brave-free` -> config web.search_backend and
# keyed on BRAVE_SEARCH_API_KEY. The former @brave/brave-search-mcp-server install
# was removed with the ADR 0070 native cut (2026-07-08) — MCP-wrapping a native
# feature was the redundant layer.

# Stage the overlay pack at a NON-VOLUME path (/app/overlay-pack) so bootstrap.sh
# can refresh the per-customer volume's overlay from it on EVERY boot. /opt/data
# is the Fly volume mount and SHADOWS anything installed at build time, so
# without this the volume keeps whatever overlay a past provision left behind
# and a release never reaches the running gateway (the ss-console#1285 delivery
# bug: the overlay was inert because the volume's copy was stale and bootstrap
# only checked presence, never freshness). A SECOND copy of the same tree,
# deliberately: the pack is never the directory the pip install above ran in.
# Hard-gated on BOTH the fan-out __init__.py and the gateway:startup activation
# gate (the live boot check, ss-console#1285) so a tree lacking either fails the
# build instead of shipping a Machine with no live governance gate.
#
# No `hermes plugins install` here any more (2026-10-22): Hermes discovers a
# plugin as a directory under $HERMES_HOME/plugins, and the boot selects it in
# the root and profile homes by writing plugins.enabled into config.yaml
# (bootstrap/plugin_selection.py; translate for every persona). Not `hermes
# plugins enable`: from v0.22 that runs package-manager admission, which
# commits a dependency env on the volume that strips this venv from every later
# hermes process (#3564, the reverted #3567). The install step's scan opt-out
# (plugins.scan_on_install) went with it; nothing scans a pack it did not clone.
#
# FAIL-CLOSED: a `|| echo "WARN ...; continuing"` once swallowed an install
# failure and booted a silently harness-less Machine — the exact fail-open
# defect the venture forbids. The pack gate here and the policy-core import
# below are hard build gates: a Machine whose trust/inbound plugins cannot load
# their own policy core MUST NOT build green.
COPY operator/hermes-smd-overlay/ /app/overlay-pack/
RUN test -f /app/overlay-pack/__init__.py \
 && test -f /app/overlay-pack/hooks/smd-overlay-activation/handler.py

# Deterministic build-time assertion that the `shared` policy core is importable
# in the venv. This catches a tree whose install did not land (the shape of the
# 2026-05-29 `ModuleNotFoundError: shared.outbound_gate`) at BUILD time rather
# than as a runtime plugin-load failure. Runtime plugin REGISTRATION (Hermes
# actually loaded the pack) is verified live by boot-smoke-test.sh step 7, which
# runs `hermes plugins list | grep hermes-smd-` inside the booted Machine where a
# profile exists.
RUN /opt/hermes/.venv/bin/python3 -c \
      "import shared.outbound_gate, shared.inbound, shared.action_classes; \
from shared.outbound_gate import evaluate; \
print('overlay policy core import OK (outbound_gate.evaluate resolved)')"

# ---------- Layer in SMD's Operator bits ----------
WORKDIR /app

# Operator adapter (trust-ceiling enforcement layer)
COPY operator/adapter/ /app/adapter/

# Canonical skill library (markdown + references)
COPY operator/skills/ /app/skills/

# Hosted-agent role packages (ADR 0090): role.yaml + the persona prompt. A seat
# with `seat.role: <role>` gets /app/roles/<role>/<persona> rendered verbatim
# into every profile's SOUL.md by the overlay's translate (bootstrap/role_persona.py);
# a seat whose role package is missing here fails its boot rather than running
# without the persona's safety rules. A seat with no role never reads this dir.
# Pinned by tests/role-persona-delivery.test.ts.
COPY operator/roles/ /app/roles/

# Drafting discipline + the mechanical gate checker (ss-console #2083).
#
# The destination mirrors the repo path on purpose. Four work-product skills
# (demand-letter-drafter, discovery-response-drafter, follow-up-discovery-drafter,
# mediation-brief-drafter) load `operator/templates/drafting/drafting-discipline.md`
# verbatim into every drafting run, and their SKILL.md bodies invoke
# `python3 operator/templates/drafting/drafting_gate_check.py` by that literal
# relative path. The final WORKDIR below is /app, so ONLY this destination makes
# the cited path resolve. A tidier /app/drafting/ would put both files in the
# image and still leave every citation dangling, which is the same defect wearing
# a better filename.
#
# Absent from this COPY set until 2026-07-30: a whole-filesystem `find` on the
# live seat hermes-pilot-smokeball returned neither drafting-discipline.md nor
# drafting_gate_check.py (vfy_01KYTXF76E1QHWQ2FQ9CYRGQBV), so the drafting lane
# ran on every seat with neither its discipline nor its gates while four SKILL.md
# files asserted both were loaded. The checker is stdlib-only, so the apt
# `python3` above is the whole runtime dependency.
#
# Presence is the instance. The class is guarded by
# tests/shipped-runtime-paths.test.ts, which derives the shipped set from the
# COPY lines in this file and fails on any runtime path a shipped SKILL.md cites
# but the image does not carry.
COPY operator/templates/drafting/ /app/operator/templates/drafting/

# ---------- The distillation compilers: the establishment control (ADR 0085 §4) ----------
#
# ADR 0085 §4 moves the witness-never-author line for admin-instructed
# establishment, and what replaces the blanket prohibition is provenance +
# mediation + THESE FIVE FILES. The leak check refuses client prose beyond the
# approved fixed strings, the digit invariant refuses asserted numbers, and the
# self-test demotes any block rule the firm's own writing violates. They are the
# mechanism that makes an agent-derived spec trustworthy enough to install.
#
# THE DESTINATION IS A REPO MIRROR, AND THAT IS LOAD-BEARING, NOT TIDINESS.
# Three import relationships resolve off the files' OWN locations, so a flattened
# layout breaks them at import time:
#
#   * spec_leak_check.py:  Path(__file__).resolve().parents[2] / "operator" /
#     "templates" / "drafting" / "drafting_gate_check.py". From
#     /opt/smd/operator/bin/, parents[2] is /opt/smd — which is precisely why the
#     mirror root exists and why the gate check must ALSO sit under it. It
#     imports the checker rather than reimplementing it so the containment
#     threshold (_HELD_OUT_NGRAM) cannot drift between the check that stops a
#     draft reproducing held-out record text and the check that stops a spec
#     reproducing the corpus.
#   * spec_selftest.py and spec_fixed_strings.py: Path(__file__).resolve().parent
#     / "voice_profile.py" — a SIBLING load, executed at MODULE level (`_VP =
#     _load_profile_module()`), so a misplaced voice_profile.py is an immediate
#     ImportError, not a latent one.
#
# WHY /opt/smd RATHER THAN /app. The overlay's establishment intake names these
# five paths as CONSTANTS (establish_intake/gates.py: COMPILER_BIN_DIR,
# DRAFTING_GATE_CHECK, REQUIRED_COMPILERS) and probes for them before every run:
# an absent compiler REFUSES the run rather than skipping the gate, because a
# gate that silently did not run reads exactly like a gate that passed (Law 12).
# /app is swept to the agent uid by the blanket chown below; /opt/smd is not, and
# must not be — the SEC-31 argument that keeps drafting_gate_check.py root-owned
# is the same argument here, only stronger. An agent that owns the compilers owns
# the control that decides whether its own authored spec is installable.
#
# WHY drafting_gate_check.py SHIPS TWICE. /app/operator/templates/drafting/ is
# where four SKILL.md bodies invoke it by literal relative path against WORKDIR
# (#2083); /opt/smd/operator/templates/drafting/ is where spec_leak_check
# resolves it. Both COPY the SAME repo file, so the two cannot drift. Moving the
# /app copy to satisfy the intake would break every drafting skill instead.
#
# Only the one file is mirrored, not the whole drafting tree: the checker takes
# every path it reads from argv and loads no siblings, so a fuller mirror would
# ship bytes nothing resolves. tests/shipped-runtime-paths.test.ts pins the
# shipped set against the intake's constants and executes the loaders inside a
# layout-faithful temp tree, so a partial mirror that stops being sufficient
# fails there rather than on a live seat.
COPY operator/bin/voice_profile.py /opt/smd/operator/bin/voice_profile.py
COPY operator/bin/spec_fixed_strings.py /opt/smd/operator/bin/spec_fixed_strings.py
COPY operator/bin/spec_leak_check.py /opt/smd/operator/bin/spec_leak_check.py
COPY operator/bin/spec_selftest.py /opt/smd/operator/bin/spec_selftest.py
COPY operator/templates/drafting/drafting_gate_check.py /opt/smd/operator/templates/drafting/drafting_gate_check.py

# Root-owned, world-readable, agent-unwritable. COPY already lands root:root, so
# this is belt-and-braces against a future --chown creeping in, and it makes the
# posture greppable next to the files it protects rather than inferable from the
# absence of a chown 90 lines below. a+rX (not a+rx) so directories are
# traversable without marking the .py files executable — the root daemon invokes
# them as `python3 <path>`, never as programs.
RUN chown -R root:root /opt/smd && chmod -R a+rX /opt/smd

# Tier-1 custom MCP wrappers (one subdir per connector)
COPY operator/connectors/ /app/connectors/

# ---------- Author-built MCP connectors: one isolated venv per connector ----------
# Each connector under /app/connectors/ (except the `_sdk` library it shares) is
# a Python stdio MCP server. Install each into its OWN venv via the proven
# workspace-broker pattern above, so connectors never share a dependency closure
# with each other or with the Hermes venv. The overlay registry launches each by
# the ABSOLUTE path to its venv console-script
# (/opt/connectors/<name>/.venv/bin/<name>-mcp) — no PATH manipulation, no
# ~/.local/bin. A connector is INERT until a customer.yaml binds it; baking it
# here costs only disk, never a running process (identical posture to a skill in
# the catalog that no persona enables). The `*-mcp` assertion fails the build
# loudly rather than letting a missing console-script surface as a runtime
# MCP-spawn failure on a live Machine.
RUN set -eu; \
    for cdir in /app/connectors/*/; do \
      name=$(basename "$cdir"); \
      [ "$name" = "_sdk" ] && continue; \
      [ -f "${cdir}pyproject.toml" ] || continue; \
      venv="/opt/connectors/${name}/.venv"; \
      echo "== installing author-built connector: ${name} =="; \
      mkdir -p "/opt/connectors/${name}"; \
      uv venv "$venv"; \
      reqs="/app/requirements/connector-${name}.txt"; \
      [ -f "$reqs" ] || { echo "FATAL: connector ${name} has no pinned requirements at operator/requirements/connector-${name}.txt; run operator/requirements/compile.sh and commit the result"; exit 1; }; \
      uv pip install --python "$venv/bin/python" --no-cache --no-config --require-hashes -r "$reqs"; \
      uv pip install --python "$venv/bin/python" --no-cache --no-deps /app/connectors/_sdk "$cdir"; \
      ls "$venv"/bin/*-mcp >/dev/null 2>&1 || { echo "FATAL: connector ${name} produced no *-mcp console-script"; exit 1; }; \
    done; \
    chmod -R a+rX /opt/connectors

# ---------- The chronology runner (routine 11, ss#2614) ----------
# operator/runners/medchron is installed into its OWN root-owned venv from the
# same three paths CI installs it from (the connector SDK, the Smokeball
# connector, the runner), so the daemon and the driver child never share a
# dependency closure with the Hermes venv or the broker venv. The venv is
# root:root a+rX: the medchron uid executes it and can write nothing in it.
# The `medchron` console-script assertion fails the BUILD, not a seat.
# The demand job (docs/specs/operator/demand-drafting-routine.md) ships in the
# same venv: its house renderer and format check are package modules
# (medchron/demand/house.py, format_check.py) over python-docx, which the
# hash-locked requirements already carry; the import line below fails the build
# if either is missing. The firm's house reference .docx is NOT baked in: it is
# a firm input, fetched from the vault (vaults/<slug>/demand/) by entrypoint.sh
# with the voice files, the skeleton and the prompts, each sha-pinned.
# The drafting job (medchron/drafting/) ships in the same venv the same way:
# its renderer is the connector's docx_format, its inputs vaults/<slug>/drafting/.
# The litigation status job (medchron/litigation/) ships in the same venv too:
# striprtf (hash-pinned in medchron.txt) reads .rtf court papers, the apt
# antiword above reads .doc, and its inputs come from vaults/<slug>/litigation/.
# The case file manager (medchron/casefile/) ships in the same venv: it reads
# with the litigation job's extractors and text cache, and its inputs come from
# vaults/<slug>/casefile/ (casefile-firm.yaml and prompts/).
COPY operator/runners/ /app/runners/
# The pricing table the runner's cents cap and ledger price from, beside the
# venv (NOT under /opt/smd, which is the establishment compilers' contract
# tree and is pinned file-by-file by tests/shipped-runtime-paths.test.ts).
COPY operator/adapter/cost_telemetry/anthropic_pricing.json /opt/medchron/anthropic_pricing.json
RUN set -eu; \
    uv venv /opt/medchron/.venv; \
    uv pip install --python /opt/medchron/.venv/bin/python --no-cache --no-config \
      --require-hashes -r /app/requirements/medchron.txt; \
    uv pip install --python /opt/medchron/.venv/bin/python --no-cache --no-deps \
      /app/connectors/_sdk /app/connectors/smokeball /app/runners/medchron; \
    [ -x /opt/medchron/.venv/bin/medchron ] || { echo "FATAL: the medchron runner produced no console-script"; exit 1; }; \
    /opt/medchron/.venv/bin/python -c "import medchron.daemon, medchron.stages.upload, medchron.demand_lane, medchron.demand.run, medchron.drafting_lane, medchron.drafting.run, medchron.negotiation_lane, medchron.negotiation.run, medchron.note_lane, medchron.note_by_smd, medchron.litigation_lane, medchron.litigation.run, striprtf.striprtf, smokeball_connector.docx_format, docx, medchron.casefile_lane, medchron.casefile.run, medchron.casefile.firm"; \
    command -v antiword >/dev/null || { echo "FATAL: antiword is missing (the litigation job and the negotiation watch read .doc with it)"; exit 1; }; \
    chown -R root:root /opt/medchron; \
    chmod -R a+rX /opt/medchron

# Safety-substrate tests — bootstrap.sh runs these on container start
COPY operator/safety_substrate/ /app/safety_substrate/

# Env-consumption contract — the single source of truth the boot-time invariant
# (safety_substrate/tests/test_invariant_9) reads to enforce that no var the
# agent consumes is stripped from its env (the voice-class regression guard).
COPY operator/contracts/ /app/contracts/

# Bootstrap entrypoint
COPY operator/templates/bootstrap.sh /app/bootstrap.sh
COPY operator/templates/entrypoint.sh /app/entrypoint.sh
# The retrying vault fetch the demand, drafting, litigation and case file lanes' firm
# inputs share (2026-10-14: a single silent failed fetch at boot left a lane
# without inputs for a whole release). Sourced before the lane files.
COPY operator/templates/entrypoint-fetch-inputs.sh /app/entrypoint-fetch-inputs.sh
# Which job lanes this seat authors (ADR 0100) and only their shared tree: the
# chronology firm config and controls, the run dir and its bind mount.
COPY operator/templates/entrypoint-lanes.sh /app/entrypoint-lanes.sh
# Sourced by entrypoint.sh (the demand lane's queue, job dirs and firm inputs);
# its own file because entrypoint.sh sits at its shell size ratchet.
COPY operator/templates/entrypoint-demand.sh /app/entrypoint-demand.sh
# The drafting lane's twin of it (queue, job dirs, firm inputs from vaults/<slug>/drafting/).
COPY operator/templates/entrypoint-drafting.sh /app/entrypoint-drafting.sh
# The litigation status lane's twin (queue, job dirs, state dir, firm inputs
# from vaults/<slug>/litigation/ and the broker's copy of its budget).
COPY operator/templates/entrypoint-litigation.sh /app/entrypoint-litigation.sh
# The case file manager's twin (queue, job dirs, state dir, firm inputs from
# vaults/<slug>/casefile/ validated at boot, the broker's copy of its budget;
# the text cache is the litigation lane's).
COPY operator/templates/entrypoint-casefile.sh /app/entrypoint-casefile.sh
# The negotiation watch's twin (queue, job dirs, state dir; no firm inputs).
COPY operator/templates/entrypoint-negotiation.sh /app/entrypoint-negotiation.sh
# A marketing seat's advertising-gate registers (ADR 0098) from
# vaults/<slug>/marketing/, root:hermes 0640 under /var/lib/smd-config/marketing/.
COPY operator/templates/entrypoint-marketing.sh /app/entrypoint-marketing.sh
# The lane-proof gate's seed and modes (lane-proofs.yaml from the vault, the
# holds and proof-run trees), after the lanes' own trees.
COPY operator/templates/entrypoint-lane-proofs.sh /app/entrypoint-lane-proofs.sh
# What this image can run, uploaded to R2 at every boot so the CI config publisher
# refuses a customer.yaml naming a skill or pre_run this image lacks (2026-10-13
# A&P outage). Generated from /app/skills with the overlay's own pin hash, after
# both are in place; a failure here fails the build rather than ship no manifest.
COPY operator/templates/entrypoint-image-manifest.sh /app/entrypoint-image-manifest.sh
COPY operator/templates/gen-image-manifest.py /app/gen-image-manifest.py
# The ss-console commit this image is built from, declared this late so every
# layer above caches across releases (each RUN after an ARG is invalidated when
# the ARG changes). Since the fold (ADR 0095) it is also the overlay's version:
# the gate's heartbeat reports it as the fleet-view `version`, `fleet release`
# requires the heartbeat to echo the release's recorded sha, and the overlay's
# resolve_overlay_ref() reads the sentinel file when pip's direct_url.json
# carries no commit (a path install never does). 40-hex only: `fleet build` and
# provision-customer.sh pass `git rev-parse HEAD`; an empty or short value would
# bake an image no heartbeat can ever be matched to.
ARG SS_CONSOLE_SHA=""
RUN printf '%s' "${SS_CONSOLE_SHA}" | grep -Eq '^[0-9a-f]{40}$' \
      || { echo "FATAL: SS_CONSOLE_SHA must be the 40-hex ss-console commit (got '${SS_CONSOLE_SHA}')" >&2; exit 1; } \
 && printf '%s\n' "${SS_CONSOLE_SHA}" > /app/OVERLAY_REF
ENV SMD_OVERLAY_REF=${SS_CONSOLE_SHA}
# The Hermes pin this image was built at, for the in-image self-test's
# hermes-sha-matches-pin check (/app/selftest.sh). Re-declared here because a
# global ARG is not visible inside a stage until it is named again.
ARG HERMES_UPSTREAM_SHA
ENV SMD_HERMES_UPSTREAM_SHA=${HERMES_UPSTREAM_SHA}
RUN /opt/hermes/.venv/bin/python3 -I /app/gen-image-manifest.py /app/skills /app/image-manifest.json "${SS_CONSOLE_SHA}"
# REQUIRED_ENV / OPTIONAL_ENV, generated from operator/contracts/env-consumption.yaml
# (operator/bin/gen-env-arrays.py); bootstrap.sh sources this instead of hardcoding them.
COPY operator/templates/_env-arrays.generated.sh /app/_env-arrays.generated.sh
# The retired repo-skill names bootstrap.sh prunes from the volume after the
# additive skill seed (one home: operator/templates/retired-skills.txt).
COPY operator/templates/retired-skills.txt /app/retired-skills.txt
RUN chmod +x /app/bootstrap.sh /app/entrypoint.sh

# Curator disable guard (ADR 0017). bootstrap.sh step 7b runs this after the
# overlay materializes profile configs, ensuring the autonomous curator is off
# before Hermes starts; the boot smoke test re-runs it with --check.
COPY operator/templates/ensure-curator-disabled.py /app/ensure-curator-disabled.py
COPY operator/templates/ensure-disabled-skills.py /app/ensure-disabled-skills.py
COPY operator/templates/ensure-operator-identity.py /app/ensure-operator-identity.py
COPY operator/templates/ensure-telegram-allowlist.py /app/ensure-telegram-allowlist.py
# Egress webhook-subscription reconciler orchestrator (hermes venv; dispatches to
# each connector's own venv). Invoked non-fatally by bootstrap.sh after the R2 strip.
COPY operator/bin/webhook_reconcile.py /app/webhook_reconcile.py
# OP-P1-4 affirmative proof: boot-smoke runs this as the agent uid to confirm
# the ledger is not agent-writable.
COPY operator/templates/audit-write-fail-probe.py /app/audit-write-fail-probe.py
# OP-P2-1 affirmative proof: boot-smoke runs this as root to confirm NO agent-uid
# process (gateway or same-uid child) still carries the account-wide R2 key.
COPY operator/templates/r2-account-key-strip-probe.py /app/r2-account-key-strip-probe.py
# Boot-smoke proof that the staged AgentMail webhook secret is the vendor's
# (scott 2026-09-15: the global value was staged, every inbound rejected 401).
COPY operator/templates/agentmail-webhook-secret-probe.py /app/agentmail-webhook-secret-probe.py
# ADR 0089: on a seat that may send AS staff, boot-smoke asks Microsoft whether
# the agent's own Graph app holds Mail.Send (it must not: with Send As granted,
# that would let agent-run code send as a person with no approval).
COPY operator/templates/msgraph-read-app-cannot-send-probe.py /app/msgraph-read-app-cannot-send-probe.py
COPY operator/templates/msgraph-read-app-cannot-read-staff-probe.py /app/msgraph-read-app-cannot-read-staff-probe.py
# ADR 0053: boot-smoke asserts every baked author-built connector's declared
# tool_classes agree with the overlay's enforced action-class map (the cross-repo
# manifest<=map check, run where both the baked manifest and the installed overlay
# are present).
COPY operator/templates/connector-classification-probe.py /app/connector-classification-probe.py
# ss#2167: boot-smoke asserts the matter-mixing READ fence is installed and
# discriminates (refuses a second matter, allows the same one). The capture path
# it depends on swallows exceptions by contract, so a broken fence is otherwise
# indistinguishable from a clean fleet — no error, no alert, sends simply stop
# being checked.
COPY operator/templates/matter-mixing-fence-probe.py /app/matter-mixing-fence-probe.py
# The in-image self-test: the boot smoke's own check list, run here as root
# at boot and hourly by selftest.sh (forked from entrypoint.sh), its results read
# by the heartbeat. Root-owned with the probes above (the /app/*.sh, *.py chown).
COPY operator/templates/seat-checks.sh /app/seat-checks.sh
COPY --chmod=0755 operator/templates/selftest.sh /app/selftest.sh
COPY operator/templates/lane-memory-caps-probe.py /app/lane-memory-caps-probe.py
# The seat-spec public key the root config_applier verifies every signed spec
# pointer against (ADR 0094 amendment). Re-owned root below with the other
# governance files, as are the image manifest and capabilities it checks a
# spec's requirements against: an agent that could replace the key could sign
# its own config, and one that could edit the manifests could pass it.
COPY operator/templates/seat-spec.pub /app/seat-spec.pub

# What this image can do for a skill (fleet release, 2026-10-19). Skills now
# reach seats separately from the image (`fleet release --skills`), so a skill
# version must be checked against what the seat's IMAGE provides: the files
# under /app and /opt a skill may cite, the tools the trust plugin knows
# (overlay shared.action_classes), the broker verbs (workspace_broker/verbs.py)
# and the action classes. Generated LAST, after every COPY into /app and /opt,
# so the path list is the image's real tree. A failure here fails the build.
COPY operator/templates/gen-image-capabilities.py /app/gen-image-capabilities.py
RUN /opt/hermes/.venv/bin/python3 -I /app/gen-image-capabilities.py /app/image-capabilities.json

# NOTE: customer.yaml is NOT copied into the image. It lives on the Fly
# volume at /opt/data/customer.yaml; bootstrap.sh fetches it from R2 on
# first boot if the volume copy is absent. See §6 of the build plan and
# the customer.yaml -> per-profile config translation ADR.

# ---------- Volume data dirs for Postgres + Redis ----------
# The volume mounts at /opt/data, but initdb (run on first boot in
# bootstrap.sh) needs the parent dir to exist with hermes ownership. We
# create them here so the directory tree is right before the volume mounts;
# the volume mount will shadow them on first boot, then bootstrap.sh's
# mkdir + initdb populate the mounted directories.
RUN mkdir -p /opt/data/honcho/pg /opt/data/honcho/redis /opt/data/.local/bin

# ---------- Permissions ----------
# Everything Hermes / Honcho / bootstrap writes to must be owned by hermes
# (uid 10000). The plugin install above ran as root and dropped files into
# /opt/data/.hermes — chown sweeps them to the right owner.
RUN chmod -R a+rX /opt/hermes /app \
 && chown -R hermes:hermes \
      /opt/hermes/.venv \
      /opt/hermes/ui-tui \
      /opt/hermes/node_modules \
      /app \
      /opt/data

# SEC-31 (audit 2026-06-15): the blanket `chown -R hermes:hermes /app` above
# swept the GOVERNANCE artifacts to the agent uid — the pinned overlay pack the
# gateway:startup activation gate force-loads, the boot scripts, and the
# boot-smoke probes. A code-executing agent must not own its own governance
# self-check. Re-assert root ownership on them. This is functionally inert:
# bootstrap only READS these (it COPIES /app/overlay-pack to the hermes-owned
# volume and the gateway loads from THERE; it execs the *.sh; it runs the *.py as
# scripts) — none are imported in place, so no __pycache__ write depends on the
# agent owning them. They stay world-readable/executable (a+rX from the chmod
# above). NOTE: /app/safety_substrate is intentionally NOT re-owned — its
# run_invariants.py writes a boot log under that tree, so it must stay
# hermes-writable (a smaller, separately-tracked residual).
#
# /app/operator/templates/drafting is in this set for the same reason (#2083):
# drafting_gate_check.py is the mechanical gate a draft must pass before it
# reaches the attorney, and drafting-discipline.md is the rule it enforces. An
# agent that owns its own gate checker can rewrite the check it is graded by.
# Nothing writes under that tree at runtime: drafting_gate_check.py is INVOKED
# as a script (`python3 <path>`), and advertising_gate_check.py (ADR 0098) is
# loaded by the overlay's shared/advertising_gate.py from this exact path, where
# a failed __pycache__ write is ignored by the import system; no write depends on
# the agent owning it, and a+rX below keeps it readable and traversable.
RUN chown -R root:root \
      /app/overlay-pack \
      /app/operator/templates/drafting \
      /app/_env-arrays.generated.sh \
      /app/OVERLAY_REF \
      /app/seat-spec.pub /app/image-manifest.json /app/image-capabilities.json \
 && chown root:root /app/*.sh /app/*.py \
 && chmod -R a+rX /app/overlay-pack /app/operator/templates/drafting

ENV HERMES_WEB_DIST=/opt/hermes/hermes_cli/web_dist

VOLUME [ "/opt/data" ]

# tini as PID 1: reaps zombies, forwards signals to the foreground child
# and starts a root-only launcher. The launcher creates broker-owned runtime
# paths, starts the broker as uid 10001, strips Google secrets, then execs the
# existing bootstrap and gateway as uid 10000 with no_new_privs.
# nosemgrep: dockerfile.security.missing-user-entrypoint.missing-user-entrypoint - Root PID 1 only creates protected paths, starts uid 10001, and execs uid 10000.
ENTRYPOINT [ "/usr/bin/tini", "-g", "--", "/app/entrypoint.sh" ]
