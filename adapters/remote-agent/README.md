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

The adapter accepts `models`, `models.temperature`, and replacement
`instructions.system` values.
Set `models.default.api_key_env` when the service requires a credential. For
Anthropic Messages, optionally set `models.default.settings.max_tokens`; it
otherwise uses `4096`.

## Relay-backed streaming

The adapter supports `Runtime.invoke_stream()` when the independently deployed
service is instrumented with NVIDIA NeMo Relay. Enable Relay in `FabricConfig`
and pass `streaming=True` to `Fabric.start_runtime(...)`. NeMo Fabric owns the
ATOF listener and sends these headers with each remote request:

| Header | Remote-service behavior |
| --- | --- |
| `x-nemo-fabric-atof-stream-url` | Add this ephemeral HTTP NDJSON URL as an invocation-scoped ATOF stream sink. Do not persist it. |
| `x-nemo-fabric-request-id` | Set `nemo_fabric_request_id` on the root Agent scope used for stream correlation. |
| `x-nemo-fabric-runtime-id` | Optional runtime correlation metadata. |
| `x-nemo-fabric-invocation-id` | Optional invocation correlation metadata. |

The remote service owns its Relay installation and configuration. The adapter
does not start Relay or install Relay packages. It invokes the remote endpoint
exactly once while the SDK handles correlation, buffering, backpressure, and
the separate terminal result.

The descriptor keeps `capabilities.streaming: false` because that flag means
adapter-native OpenAI Chat Completions streaming. Protocol-native OpenAI and
Anthropic events are still reduced to the terminal result and are not exposed.

The adapter does not expose MCP, skills, tool policy, or subagents. It retains
the completed user/assistant transcript for ordered invocations in one runtime.
