<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# NVIDIA NeMo Fabric Remote Agent Adapter

Use the `nvidia.fabric.remote-agent` adapter to invoke a remote agent through
an OpenAI Responses, OpenAI Chat Completions, or Anthropic Messages HTTP API.

## Install

| Installation | Runtime | Adapter |
| --- | --- | --- |
| `pip install "nemo-fabric[remote-agent]"` | Yes | Yes |
| `pip install "nemo-fabric-adapters-remote-agent[harness]"` | No | Yes |
| `pip install nemo-fabric-adapters-remote-agent` | No | Yes |

The bare, `harness`, and `full` installations contain the same adapter and HTTP
client. They do not install the independently deployed remote service.

## Configuration

Configure the API root in `HarnessConfig.settings`. `base_url` is required and
includes `/v1`; `api_type` defaults to `openai-responses`.

| Setting | Accepted values |
| --- | --- |
| `base_url` | HTTP(S) API root, such as `https://agent.example.com/v1` |
| `api_type` | `openai-responses`, `openai-completions`, or `anthropic-messages` |
| `connect_timeout_seconds` | Connection timeout; defaults to `10` |
| `read_timeout_seconds` | Timeout between response bytes; defaults to `600` |
| `relay_streaming` | Opt in to request-ID correlation with a Relay-instrumented remote service; defaults to `false` |

The adapter accepts `models`, `models.temperature`, and replacement
`instructions.system` values.
Set `models.default.api_key_env` when the service requires a credential. For
Anthropic Messages, optionally set `models.default.settings.max_tokens`; it
otherwise uses `4096`.

## Relay-backed streaming

The adapter supports `Runtime.invoke_stream()` when the independently deployed
service is instrumented with NVIDIA NeMo Relay. Set `relay_streaming: true`,
enable Relay in `FabricConfig`, configure an HTTP ATOF stream sink named
`nemo-fabric-stream`, and pass `streaming=True` to
`Fabric.start_runtime(...)`. Fabric binds the sink URL as its collector; the
remote deployment must already publish ATOF to that same URL.

The adapter adds `metadata.nemo_fabric_request_id` to the mapped OpenAI or
Anthropic request body. The remote service must carry that value into its Relay
turn correlation metadata. It receives no listener URL or correlation headers.
The remote service owns its Relay installation and configuration; the adapter
does not install or start Relay there.

Invocations on one runtime are serialized. Use a unique request ID for each
turn and fully consume or close one stream before starting the next. Each
collector URL can be bound by only one Fabric runtime at a time.

The descriptor keeps `capabilities.streaming: false` because that flag means
adapter-native OpenAI Chat Completions streaming. Protocol-native OpenAI and
Anthropic events are still reduced to the terminal result and are not exposed.

The adapter does not expose MCP, skills, tool policy, or subagents. It retains
the completed user/assistant transcript for ordered invocations in one runtime.
