#!/usr/bin/env bash

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

EXAMPLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export OPENSHELL_EXAMPLE_MODE="${OPENSHELL_EXAMPLE_MODE:-both}"
exec bash "${EXAMPLE_ROOT}/../openshell/run-demo.sh" "${EXAMPLE_ROOT}/demo.sh" "$@"
