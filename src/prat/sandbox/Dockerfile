# PRAT's default sandbox image: the common runtimes a project's commands need, plus socat for
# the database and environment relays. A project can name its own image instead in its
# project file ([sandbox] image = "..."); that image needs sh, and socat for environments.
#
#   prat sandbox build
FROM node:22.11.0-bookworm-slim AS node

FROM python:3.12.7-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bash ca-certificates curl git make build-essential pkg-config socat \
      libpq-dev postgresql-client sqlite3 default-libmysqlclient-dev \
 && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && mkdir -p /home/prat && chmod 1777 /home/prat \
 && git config --system --add safe.directory '*'
ENV HOME=/home/prat \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false
WORKDIR /home/prat
