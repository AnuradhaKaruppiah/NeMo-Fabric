// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

type Version = readonly [major: number, minor: number, patch: number];

function parseVersion(value: string, label: string): Version {
  const match = /^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/.exec(value);
  if (match === null) {
    throw new Error(`${label} must be a semantic version`);
  }
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

function isOlderThan(actual: Version, minimum: Version): boolean {
  for (let index = 0; index < actual.length; index += 1) {
    const actualPart = actual[index];
    const minimumPart = minimum[index];
    if (actualPart === undefined || minimumPart === undefined) {
      throw new Error("Node.js version comparison received an incomplete version");
    }
    if (actualPart !== minimumPart) {
      return actualPart < minimumPart;
    }
  }
  return false;
}

export function assertSupportedNodeVersion(current: string, requirement: unknown): void {
  if (typeof requirement !== "string") {
    throw new Error("The Pi package must declare a Node.js engine requirement");
  }
  const minimumMatch = /^>=(\d+\.\d+\.\d+)$/.exec(requirement);
  if (minimumMatch?.[1] === undefined) {
    throw new Error(`The Pi adapter cannot enforce Node.js engine requirement ${requirement}`);
  }
  if (isOlderThan(parseVersion(current, "The current Node.js version"), parseVersion(minimumMatch[1], "The minimum Node.js version"))) {
    throw new Error(`The Pi adapter requires Node.js ${requirement}; current runtime is ${current}`);
  }
}
