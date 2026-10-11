# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

FROM node:24.16.0-bookworm-slim AS node
FROM python:3.12-slim-trixie

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update \
    && apt-get install --yes --no-install-recommends iproute2 nftables git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global npm@11.16.0 \
    && npm install --global openclaw@2026.9.4 --allow-scripts=openclaw \
    && npm cache clean --force

COPY adapter-contract /opt/build/adapter-contract
COPY adapters-common /opt/build/adapters-common
COPY adapters-openclaw /opt/build/adapters-openclaw
RUN python -m pip install --no-cache-dir /opt/build/adapter-contract \
      /opt/build/adapters-common /opt/build/adapters-openclaw \
    && rm -rf /opt/build \
    && groupadd --gid 1500 sandbox \
    && useradd --uid 1500 --gid 1500 --create-home sandbox \
    && mkdir -p /sandbox/artifacts /sandbox/.fabric/control \
    && chown -R sandbox:sandbox /sandbox

COPY --chmod=0755 fabric-runtime-server fabric-runtime-ctl /usr/local/bin/
COPY examples/code_review_agent/repos/my-service /sandbox/repos/my-service
COPY examples/code_review_agent/skills/code-review /opt/nemo-fabric/skills/code-review
COPY examples/code_review_agent/openshell/capture_evidence.py /opt/nemo-fabric/capture_evidence.py
ENV HOME=/home/sandbox
ENV SQLITE_TMPDIR=/tmp TMPDIR=/tmp
WORKDIR /sandbox
USER sandbox:sandbox
CMD ["fabric-runtime-server", "serve"]
