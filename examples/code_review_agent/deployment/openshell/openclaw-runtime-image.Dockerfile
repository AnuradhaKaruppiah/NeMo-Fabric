# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

ARG SDK_IMAGE=fabric-openshell-sdk:source
ARG NODE_IMAGE=node:24.21.0-trixie-slim@sha256:db3ae80f5d8df06e04dabdf7b44cbf008d32de168205fa0294444aabbc08c590
ARG OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw@sha256:cc596b846506a5f4cfcee111394a2725f375f01cca2ebb492a161fd1b747f101
FROM ${NODE_IMAGE} AS node
FROM ${OPENCLAW_IMAGE} AS openclaw
FROM ${SDK_IMAGE}
USER root
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=openclaw /app /app
RUN ln -s /app/openclaw.mjs /usr/local/bin/openclaw
COPY adapters/python/openclaw/ /opt/build/openclaw/
RUN --mount=type=cache,target=/root/.cache/uv uv pip install --python /opt/fabric/bin/python --no-deps /opt/build/openclaw \
    && uv pip install --python /opt/fabric/bin/python 'httpx[http2]~=0.28' \
    && rm -rf /opt/build
COPY adapters/python/openclaw/openclaw.fabric-adapter.json /opt/fabric/adapter-descriptors/openclaw.fabric-adapter.json
COPY --chown=sandbox:sandbox examples/code_review_agent/ /sandbox/code-review/
USER sandbox:sandbox
CMD ["fabric-agent-ctl", "serve"]
