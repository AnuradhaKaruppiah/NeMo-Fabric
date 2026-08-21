// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { execFileSync, spawnSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const npmCli = process.env.npm_execpath;
if (npmCli === undefined) {
  throw new Error("npm_execpath is required; run this check through npm");
}

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDirectory, "../../..");
const packageRoots = [
  join(repositoryRoot, "adapter-contract/typescript"),
  join(repositoryRoot, "adapters/typescript/common"),
  join(repositoryRoot, "adapters/typescript/pi"),
];

function npm(args, cwd) {
  return execFileSync(process.execPath, [npmCli, ...args], {
    cwd,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "inherit"],
  });
}

const temporaryRoot = await mkdtemp(join(tmpdir(), "nemo-fabric-ts-install-"));
try {
  const tarballs = [];
  for (const packageRoot of packageRoots) {
    npm(["run", "build"], packageRoot);
    const result = JSON.parse(
      npm(
        ["pack", "--json", "--ignore-scripts", "--pack-destination", temporaryRoot],
        packageRoot,
      ),
    );
    tarballs.push(join(temporaryRoot, result[0].filename));
  }

  const consumerRoot = join(temporaryRoot, "consumer");
  await mkdir(consumerRoot);
  await writeFile(
    join(consumerRoot, "package.json"),
    `${JSON.stringify({ name: "nemo-fabric-adapter-install-check", private: true }, null, 2)}\n`,
    "utf8",
  );
  npm(
    [
      "install",
      "--ignore-scripts",
      "--no-audit",
      "--no-fund",
      "--package-lock=false",
      ...tarballs,
    ],
    consumerRoot,
  );

  const piRoot = join(consumerRoot, "node_modules/nemo-fabric-adapters-pi");
  const descriptor = JSON.parse(await readFile(join(piRoot, "pi.fabric-adapter.json"), "utf8"));
  if (descriptor.runner?.command !== "node" || descriptor.runner?.script !== "dist/cli.js") {
    throw new Error("Installed Pi descriptor does not reference its packaged CLI");
  }

  const invocation = spawnSync(process.execPath, [join(piRoot, descriptor.runner.script)], {
    cwd: consumerRoot,
    encoding: "utf8",
    input: "{}\n",
    timeout: 60_000,
  });
  if (invocation.error) {
    throw invocation.error;
  }
  if (invocation.status !== 0) {
    throw new Error(
      `Installed Pi CLI failed (status ${invocation.status}, signal ${invocation.signal}): ${invocation.stderr}`,
    );
  }
  const response = JSON.parse(invocation.stdout.trim());
  if (response.outcome?.error?.code !== "lifecycle_invalid_operation") {
    throw new Error(`Installed Pi CLI returned an unexpected response: ${invocation.stdout}`);
  }
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}
