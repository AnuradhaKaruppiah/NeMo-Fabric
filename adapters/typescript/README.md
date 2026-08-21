<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# NVIDIA NeMo Fabric TypeScript Adapter Workspace

This private npm workspace coordinates the independently published common and
Pi adapter packages. It provides shared build, test, package-content, and
consumer-install checks without becoming a published package itself.

## Dependency Rationale

The workspace links `nemo-fabric-adapter-contract` from the checked-out source
tree so all adapter builds and tests use the contract being reviewed. Resolving
the package from the registry would test an older published contract, while
copying its schemas would create a second authority. This file dependency is
development-only; the published child manifests declare registry-safe contract
versions.
