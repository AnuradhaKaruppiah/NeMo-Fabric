# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

ARG SDK_IMAGE=fabric-openshell-sdk:source
FROM ${SDK_IMAGE}
USER root
ARG LANGGRAPH_VERSION=1.2.11
RUN --mount=type=cache,target=/root/.cache/uv uv pip install --python /opt/fabric/bin/python "langgraph==${LANGGRAPH_VERSION}"
COPY examples/__init__.py /opt/fabric/app/examples/__init__.py
COPY examples/langgraph_openshell/ /opt/fabric/app/examples/langgraph_openshell/
ENV PYTHONPATH=/opt/fabric/app
USER sandbox:sandbox
CMD ["fabric-agent-ctl", "serve"]
