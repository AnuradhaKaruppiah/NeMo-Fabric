# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

ARG SDK_IMAGE=fabric-openshell-sdk:source
FROM ${SDK_IMAGE}
USER root
ARG HERMES_SOURCE_SHA=a62979dc3601c9eae455bff5b35ba8f9bed0df30
RUN git init /opt/hermes-agent \
    && git -C /opt/hermes-agent remote add origin https://github.com/NousResearch/hermes-agent.git \
    && git -C /opt/hermes-agent fetch --depth 1 origin "${HERMES_SOURCE_SHA}" \
    && git -C /opt/hermes-agent checkout --detach FETCH_HEAD
RUN /usr/local/bin/uv venv --python /opt/fabric/bin/python /opt/hermes-venv
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_PROJECT_ENVIRONMENT=/opt/hermes-venv UV_PYTHON=/opt/hermes-venv/bin/python \
    uv sync --project /opt/hermes-agent --frozen --no-dev --no-default-groups --inexact
COPY adapter-contract/python/ /opt/build/adapter-contract/
COPY adapters/python/common/ /opt/build/common/
COPY adapters/python/hermes/ /opt/build/hermes/
RUN --mount=type=cache,target=/root/.cache/uv uv pip install --python /opt/hermes-venv/bin/python --no-deps \
      /opt/build/adapter-contract /opt/build/common /opt/build/hermes \
    && rm -rf /opt/build
COPY adapters/python/hermes/hermes.fabric-adapter.json /opt/fabric/adapter-descriptors/hermes.fabric-adapter.json
COPY --chown=sandbox:sandbox examples/code_review_agent/ /sandbox/code-review/
RUN install -d -m 0700 -o 1000 -g 1000 /sandbox/hermes-home
ENV HOME=/sandbox/hermes-home HERMES_HOME=/sandbox/hermes-home PYTHONPATH=/opt/hermes-agent ADAPTER_PYTHON=/opt/hermes-venv/bin/python
LABEL org.nvidia.nemo-fabric.hermes.source="$HERMES_SOURCE_SHA"
USER sandbox:sandbox
CMD ["fabric-agent-ctl", "serve"]
