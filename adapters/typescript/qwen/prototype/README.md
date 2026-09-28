<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Qwen Code ACP prototype

This directory is an unshipped compatibility prototype. It runs the direct
Qwen adapter's normalized configuration through Qwen Code's Agent Client
Protocol (ACP) surface without changing the NVIDIA NeMo Fabric adapter
contract. The published package excludes this directory and continues to use
the direct SDK implementation.

## Result

The same bounded ACP runner used by the Kilo Code experiment works with the
Qwen Code CLI 0.24.6 bundled in `@qwen-code/sdk` 0.1.16. The real-process
smoke test verifies:

- ACP v1 initialization and session creation;
- replacement and append system instructions;
- normalized model, credential, permission, blocked-tool, and skill projection;
- stdio MCP discovery and tool execution;
- authenticated streamable-HTTP MCP discovery;
- warm two-turn continuation with current-turn-only text;
- start followed by stop without a prompt; and
- normalization of an unavailable MCP server.

Run the deterministic transport and projector tests with:

```bash
npm run prototype:test --workspace nemo-fabric-adapters-qwen
```

Run the real bundled-CLI smoke test with:

```bash
npm run prototype:smoke --workspace nemo-fabric-adapters-qwen
```

The smoke test uses loopback-only model and MCP servers. It requires no
external model or credentials.

## Boundary

The experiment preserves the Kilo prototype's split:

- `acp-runner.ts` owns child-process launch, newline-delimited ACP transport,
  protocol negotiation, session lifecycle, cancellation, bounded event
  collection, and fail-closed permission requests.
- `qwen-acp-session.ts` owns Qwen installation discovery, isolated settings,
  credentials, model and instruction flags, tool and skill settings, MCP
  projection, usage interpretation, and Qwen-specific error classification.

The only addition needed by the transport runner was an opaque stderr callback.
The runner does not interpret Qwen messages. Qwen's projector uses the same
diagnostic classifier as the direct SDK adapter.

## Configuration coverage

| Fabric surface | Qwen ACP path | Result |
| --- | --- | --- |
| Model and credential | Isolated Qwen settings, environment, and CLI model flag | Verified |
| System instruction replace | Qwen CLI `--system-prompt` | Verified |
| System instruction append | Qwen CLI `--append-system-prompt` | Verified with the default prompt retained |
| Tool block policy | Qwen settings and CLI `--exclude-tools` | Verified on primary agent-loop requests; execution-denial parity still needs a dedicated adversarial test |
| Skills | Isolated Qwen skill directories | Verified by provider request inspection |
| stdio MCP | ACP `session/new.mcpServers` | Discovery and tool execution verified |
| streamable-HTTP MCP | ACP HTTP MCP server plus expanded headers | Authenticated discovery verified |
| Per-server MCP tool filters | No ACP field | Rejected by the Qwen projector |
| Warm continuation | One ACP session | Verified |
| Current-turn text | ACP `agent_message_chunk` notifications | Verified |
| Usage | ACP prompt response | Qwen did not emit usage; direct SDK retains the advantage |
| Unavailable MCP | Qwen diagnostic stderr plus generic ACP prompt failure | Normalized by the Qwen projector |
| Cancellation and bounds | Shared runner | Deterministically verified |
| Relay telemetry | None | Deferred |

Qwen emits ancillary model requests with a different tool schema. The primary
ACP agent-loop requests honor the excluded-tool projection. Promotion needs an
adversarial attempt to execute a denied tool so the policy is proven at the
executor boundary rather than inferred only from model schemas.

## Combined Kilo and Qwen finding

Two harnesses now validate the same conclusion: ACP standardizes transport and
session mechanics, not normalized configuration.

The reusable layer is small and concrete:

- spawn an explicit command and argument array without a shell;
- negotiate ACP v1 and validate advertised capabilities;
- create one session, send ordered prompts, cancel, and shut down safely;
- collect only current-turn assistant chunks;
- bound frames, updates, and assistant text; and
- normalize process, timeout, and protocol failures.

The following remain harness-specific:

- provider, model, and credential setup;
- system-instruction projection;
- tool and permission policy;
- skills;
- maximum-turn semantics;
- MCP filters and authentication beyond ACP's server shapes;
- installation and environment isolation;
- mode translation, such as Kilo `build` to `code`;
- usage, cost, error, and result interpretation; and
- any Relay telemetry integration.

Qwen strengthens the case for a shared runner because it reused the Kilo
transport unchanged for normal ACP operations. It also strengthens the case
against a universal adapter: Qwen needs different CLI flags, settings,
diagnostic classification, and capability trade-offs.

## Recommendation

Keep the direct SDK adapters as the production implementations for now. They
provide more complete harness semantics: Qwen reports usage through its SDK and
supports per-server MCP tool filters; Kilo reports cost and exposes deeper
connection checks.

The ACP runner is ready to extract into a focused TypeScript common module
after its public API and dependency policy are reviewed. The common module
should remain a transport/session runner. Each harness must supply a small
projector and explicit capability matrix. Do not put model providers,
credentials, system instructions, tools, skills, turn limits, or
harness-specific error parsing into the shared runner.

Before promoting an ACP-backed adapter, add:

- adversarial denied-tool execution;
- malformed response and permission-request variants;
- concurrency and two-runtime isolation;
- process-tree cleanup on every supported platform; and
- a decision for missing usage or cost fields.
