#!/usr/bin/env node
// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { createRequire } from "node:module";

import { serve } from "nemo-fabric-adapters-common";

import { assertSupportedNodeVersion } from "./node-version.js";

const manifest = createRequire(import.meta.url)("../package.json") as {
  engines?: { node?: unknown };
};
assertSupportedNodeVersion(process.versions.node, manifest.engines?.node);

const [{ PiSdkSessionFactory }, { PiAdapterRuntime }] = await Promise.all([
  import("./pi-sdk.js"),
  import("./runtime.js"),
]);
await serve(() => new PiAdapterRuntime(new PiSdkSessionFactory()));
