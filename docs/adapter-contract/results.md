{/*
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
*/}

# Stage 4: Normalize Results and Telemetry

Every invocation produces one terminal outcome. Keep target-specific parsing
inside the adapter so consumers receive a stable NeMo Fabric `RunResult` and do
not need to understand the target's native response objects.

## Return the Current Host Value

The v1alpha2 local-host binding accepts one JSON-compatible terminal value from
`invoke`. Return a small mapping that contains the target output the consumer
needs:

```python
async def invoke(self, payload):
    native = await self.target.run(payload["request"]["input"])
    return {
        "response": native.final_text,
        "usage": {"api_calls": native.api_calls},
    }
```

Use the host's lifecycle error mechanism when the adapter cannot complete the
operation. Do not return the preview `AgentRunResult` envelope from the current
host: the host treats it as ordinary JSON and does not interpret
`status: failed`.

Put target response normalization in one function. That keeps the target code
independent of NeMo Fabric and makes a later typed-boundary migration local to
the adapter edge.

## Understand the Preview Result Boundary

`AgentRunResult` defines the intended typed southbound outcome. It is published
for schema and binding development but is not negotiated by the current local
host.

| Field | Requirement | Purpose |
| --- | --- | --- |
| `status` | Required | Reports `succeeded`, `failed`, or `cancelled`. |
| `output` | Required | Carries the primary JSON-compatible output and can be `null`. |
| `error` | Required for `failed` | Carries a stable code, safe message, retry guidance, and declared extensions. |
| `usage` | Optional | Carries normalized input, output, and total token counts plus cost when known. |
| `artifacts` | Optional | Carries target-produced artifact references relative to the runtime artifact root. |
| `extensions` | Optional | Carries adapter-owned result data validated by the descriptor. |

Use the canonical
[`agent-run-result.schema.json`](https://github.com/NVIDIA/NeMo-Fabric/blob/main/schemas/adapter-contract/agent-run-result.schema.json)
when developing the future typed boundary. A failed typed result contains an
error; a successful typed result does not contain a non-null error. Status is
explicit and is not inferred from arbitrary output fields.

## Separate Failure Classes

Use these failure classes consistently:

- A **lifecycle failure** means the adapter could not satisfy `start`,
  `invoke`, or `stop`. It is reported at the relevant NeMo Fabric error stage
  and can invalidate the runtime.
- A **terminal target failure** means the target completed the invocation with
  a failed outcome. In the current host, represent this through the adapter's
  documented JSON-compatible terminal shape. The preview typed boundary will
  represent it as `AgentRunResult.status: failed`.

Set retry guidance only when retrying at the consumer boundary is safe. NeMo
Fabric propagates failure information but does not automatically retry adapter
operations.

## Keep Artifacts Inside the Runtime Root

Write target artifacts below the artifact root supplied through
`RuntimeContext.environment`. Return relative artifact references; do not
return arbitrary host filesystem paths. NeMo Fabric combines adapter-declared
artifacts with its collected artifact manifest.

## Let NeMo Fabric Enrich the Outcome

NeMo Fabric combines the adapter value with runtime-owned information when it
constructs the consumer-facing `RunResult`:

| NeMo Fabric Adds | Source |
| --- | --- |
| Adapter, target, and runtime identity | Resolved plan and runtime handle. |
| Runtime, invocation, and request correlation | `RuntimeContext`. |
| Lifecycle stage and events | Runtime orchestration. |
| Collected artifact manifest | NeMo Fabric and adapter artifact declarations. |
| Telemetry reference | Resolved telemetry plan and runtime telemetry context. |

Do not duplicate NeMo Fabric-owned IDs, lifecycle events, or telemetry
references inside arbitrary adapter output or extensions.

## Integrate Telemetry Without Changing the Result

Telemetry configuration, correlation, and result references are NeMo
Fabric-owned. The Adapter Descriptor declares which outputs the adapter can
produce or forward. `RuntimeContext.telemetry` supplies the resolved
invocation-level context, including generated Relay configuration when
enabled.

An adapter can initialize target-native telemetry or forward NeMo
Fabric-provided Relay configuration. It must not reinterpret correlation IDs,
claim outputs it did not produce, or log unredacted telemetry environment
values.

Relay-backed ATOF records and the terminal result describe the same invocation
but remain separate. Stream exhaustion does not imply success, and stopping
stream consumption does not change the terminal outcome.

After outcomes are safe and stable, [package and register the adapter](registration-and-discovery.md).
