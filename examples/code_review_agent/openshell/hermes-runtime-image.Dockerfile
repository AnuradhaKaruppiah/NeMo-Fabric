# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

FROM python:3.14-slim-trixie

RUN apt-get update \
    && apt-get install --yes --no-install-recommends iproute2 nftables git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir uv==0.11.14

# Pinned source and lockfile are staged by the launcher, without modifying an
# existing Hermes checkout. Hermes is a source-installed harness.
COPY hermes-agent /opt/hermes-agent
WORKDIR /opt/hermes-agent
ENV UV_PROJECT_ENVIRONMENT=/opt/hermes-venv
RUN uv sync --frozen --no-dev --no-default-groups \
    && uv cache clean

COPY adapter-contract /opt/build/adapter-contract
COPY adapters-common /opt/build/adapters-common
COPY adapters-hermes /opt/build/adapters-hermes
RUN uv pip install --no-sources --python /opt/hermes-venv/bin/python \
      /opt/build/adapter-contract /opt/build/adapters-common /opt/build/adapters-hermes \
    && rm -rf /opt/build \
    && uv cache clean \
    && groupadd --gid 1500 sandbox \
    && useradd --uid 1500 --gid 1500 --create-home sandbox \
    && mkdir -p /sandbox/artifacts /sandbox/.fabric/control \
    && chown -R sandbox:sandbox /sandbox

COPY --chmod=0755 fabric-runtime-server fabric-runtime-ctl /usr/local/bin/
COPY examples/code_review_agent/repos/my-service /sandbox/repos/my-service
COPY examples/code_review_agent/skills/code-review /opt/nemo-fabric/skills/code-review
COPY examples/code_review_agent/openshell/capture_evidence.py /opt/nemo-fabric/capture_evidence.py
ENV PATH=/opt/hermes-venv/bin:$PATH
COPY examples/code_review_agent/openshell/preflight.py examples/code_review_agent/openshell/preflight.mjs /opt/nemo-fabric/
ENV HOME=/home/sandbox SQLITE_TMPDIR=/tmp TMPDIR=/tmp
WORKDIR /sandbox
USER sandbox:sandbox
CMD ["fabric-runtime-server", "serve"]
