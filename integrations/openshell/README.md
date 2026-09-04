# Experimental OpenShell environment provider

NVIDIA NeMo Fabric recognizes `environment.provider="openshell"` as an
experimental out-of-process provider. Fabric starts
`fabric-environment-openshell serve --stdio`; the binary is supplied by the
OpenShell integration and uses the OpenShell Rust SDK. OpenShell is therefore
not a dependency of `nemo-fabric-core`.

Set `NEMO_FABRIC_OPEN_SHELL_PROVIDER` to an absolute provider-binary path when
the binary is not on `PATH`. This variable is operator configuration and is not
serialized into a Fabric plan.

The first provider profile uses the existing normalized environment fields:

```python
from pathlib import Path

from nemo_fabric import EnvironmentConfig

environment = EnvironmentConfig(
    provider="openshell",
    control_location="in_env_control",
    ownership="fabric_owned",
    workspace="/sandbox",
    artifacts="/sandbox/artifacts",
    connection={
        "gateway": "https://openshell.example.com",
        "workspace": "fabric-demo",
        "token_env": "OPEN_SHELL_TOKEN",
    },
    settings={
        "image": "registry.example/fabric-capsule@sha256:<64-hex-digest>",
        "command": ["fabric-capsule-runner", "serve"],
        "policy_yaml": Path("policy.yaml").read_text(encoding="utf-8"),
        "ready_timeout_seconds": 60,
        "exec_timeout_seconds": 30,
        "delete_timeout_seconds": 30,
    },
)
```

The provider rejects caller-owned sandboxes, external control, mutable image
tags, blank commands, unknown connection/settings fields, and literal token
fields. `token_env` and `ca_cert_env` name environment variables inherited by
the provider process; their values are not returned in the normalized
environment handle.

Phase 1B implements gateway health, create/get/wait-ready, buffered exec with
bounded published output, identity-checked inspection, delete, and
wait-deleted. Phase 1C adds a resident, typed capsule-control path for process
and Python adapters. Phase 1D passes a validated OpenShell policy at sandbox
creation and collects adapter-declared artifacts through a traversal-safe,
size-bounded provider operation. Consumers prepare an environment explicitly
and pass its handle to
`start_runtime_in(plan, environment_handle)`; `start_runtime(plan)` rejects
non-local plans without contacting the provider. Fabric routes `start`,
buffered `invoke`, and `stop` as correlated
`fabric.capsule-control.v1alpha1` messages. The provider executes only the
matching `fabric-capsule-ctl` operation; it does not expose generic remote
shell through Fabric's public API. The capsule image must contain
`fabric-capsule-runner`, `fabric-capsule-ctl`, and the configured adapter plus
its target. Runtime stop and start failure do not release the environment; the
consumer calls `release_environment(environment_handle)` explicitly.

The Python orchestration deliberately keeps the same three lifecycles visible:

```python
fabric = Fabric()
environment = await fabric.prepare_environment(config)
try:
    runtime = await fabric.start_runtime_in(config, environment)
    try:
        first = await runtime.invoke(input="first turn")
        second = await runtime.invoke(input="second turn")
    finally:
        await runtime.stop()

    # Inspect the still-live sandbox and its artifacts here.
finally:
    await fabric.release_environment(environment)
```

The consumer may run multiple independent environment/runtime pairs
concurrently. A single `Runtime` remains one sequential session, and the
capsule profile allows only one active session per OpenShell environment. A
second bind returns a stable environment-in-use error. Streaming,
reconnect/resubscribe, and cancellation remain deferred.

The environment lifecycle calls are an optional development convenience. In
deployment, a consumer or platform can own OpenShell provisioning and hand an
existing normalized environment to Fabric. See
[`examples/langgraph_openshell_poc`](../../examples/langgraph_openshell_poc/)
for a real gateway/Docker vertical slice with a stateful LangGraph, an L7
policy-denied preferred route, an allowed fallback, and a collected receipt.
