<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Qwen Code adapter

This package runs Qwen Code through its direct TypeScript SDK. One NVIDIA NeMo
Fabric runtime owns one Qwen CLI child process and a live multi-turn session.

This initial adapter slice accepts one OpenAI-compatible model, optional base
URL, temperature, top-p, system instructions in `replace` or `append` mode,
`tools.blocked`, explicit `skills.paths`, and normalized stdio and
streamable-HTTP MCP servers with per-server tool filters.
`harness.settings.permission_mode` selects Qwen's native
approval mode; its default is `default`. `yolo` allows unattended code edits
and should be used only inside an appropriate task sandbox.
The selected credential is read from the named environment variable and is not
written to settings. `tools.enabled`, per-invocation turn limits,
native streaming, and Relay telemetry are not yet claimed by the descriptor.

For a published release, install the adapter and the exact supported SDK peer
in the environment that starts the adapter:

```bash
npm install nemo-fabric-adapters-qwen @qwen-code/sdk@0.1.16
```

For source development from the repository root, install the Qwen workspace
and build the local contract and adapter packages:

```bash
just install-typescript-qwen
npm run build --prefix adapter-contract/typescript
npm run build --prefix adapters/typescript --workspace nemo-fabric-adapters-common
npm run build --prefix adapters/typescript --workspace nemo-fabric-adapters-qwen
```

Run the direct SDK suite and the Fabric process E2E with:

```bash
npm test --prefix adapters/typescript --workspace nemo-fabric-adapters-qwen
uv sync --no-default-groups --group test
uv run --no-sync pytest tests/e2e/test_qwen.py -q
```

Use `adapters/typescript/qwen/qwen.fabric-adapter.json` as the local adapter
descriptor. Set `models.default.provider` to `openai`, provide a model ID and
`api_key_env`, and make that variable available in the Fabric environment.

MCP `stdio` servers map `url` to the executable and accept normalized `args`
and `env`. `streamable-http` servers accept `custom_headers`; `${NAME}` values
resolve from the Fabric environment first and then the parent process. Remote
URLs require HTTPS, with HTTP allowed for loopback development servers.
`allowed_tools` and `blocked_tools` map to Qwen's per-server filters. Native MCP
authentication is not exposed. Only explicitly configured server names are
enabled, so ambient project MCP configuration does not leak into the session.

The pinned SDK discovers external MCP servers on the first prompt and does not
include them in `mcpServerStatus()`. The adapter captures the SDK's pinned MCP
failure diagnostic and returns `qwen_mcp_unavailable` rather than accepting a
result produced without every explicitly configured server.
