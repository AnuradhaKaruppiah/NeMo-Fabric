# OpenShell environment provider

This optional Fabric integration binary translates the internal Fabric environment
provider protocol into the public OpenShell Rust SDK. It is intentionally
out-of-process so `nemo-fabric-core` does not depend on OpenShell or its gRPC
stack.

The provider is built as `fabric-environment-openshell`. Fabric locates that
name on `PATH`, or operators can set `NEMO_FABRIC_OPEN_SHELL_PROVIDER` to an
absolute binary path.

The dependency is pinned to an exact public OpenShell revision until the Rust
SDK has a stable independently released crate. Health, exec, wait, and delete
operations use the curated SDK. Creation and attachment verification use the
SDK's documented raw escape hatch because the curated types do not yet expose
the complete sandbox policy and launch specification.

The deployment-oriented attach operation accepts a caller-owned sandbox
reference and verifies its immutable identity, readiness, image, command, and
declared policy before returning an environment handle. Releasing that handle
detaches Fabric without deleting the sandbox.

Fabric-specific behavior belongs here rather than in the OpenShell repository.

## Dependency decision

The provider uses the Rust SDK instead of invoking the OpenShell CLI so runtime
operations keep typed requests, structured errors, and a direct authentication
boundary. Keeping the SDK in this out-of-process crate avoids adding its gRPC
and transport graph to `nemo-fabric-core`. The exact Git revision is the
narrowest reproducible option because this SDK revision is not yet published as
an independently versioned crate. The resolved additions are permissively
licensed (Apache-2.0, MIT, BSD, ISC, or compatible combinations); no unresolved
license exception was identified by the repository license inventory.
