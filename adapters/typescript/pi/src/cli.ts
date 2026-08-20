#!/usr/bin/env node
// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { serve } from "nemo-fabric-adapters-common";

import { PiSdkSessionFactory } from "./pi-sdk.js";
import { PiAdapterRuntime } from "./runtime.js";

await serve(() => new PiAdapterRuntime(new PiSdkSessionFactory()));
