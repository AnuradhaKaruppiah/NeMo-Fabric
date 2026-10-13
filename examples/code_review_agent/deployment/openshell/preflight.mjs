// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Node probes OpenClaw's endpoint under the same binary policy as its agent.
const access = JSON.parse(process.argv[2]);
const key = process.env[access.api_key_env];
try {
  if (!key) throw new Error("model credential environment variable is missing in the sandbox");
  let response;
  try {
    response = await fetch(access.base_url.replace(/\/$/, "") + "/chat/completions", {
      method: "POST",
      headers: {"Content-Type": "application/json", Authorization: "Bearer " + key},
      body: JSON.stringify({model: access.model, messages: [{role: "user", content: "Reply OK."}], max_tokens: 8}),
      signal: AbortSignal.timeout(30000),
    });
  } catch {
    throw new Error("model preflight failed: endpoint unreachable");
  }
  if (!response.ok) throw new Error(`model preflight failed: HTTP ${response.status}`);
  const result = await response.json().catch(() => { throw new Error("model preflight failed: invalid response"); });
  if (!result.choices?.length) throw new Error("model preflight failed: response has no completion choices");
  console.log(JSON.stringify({model: access.model, reachable: true, credential_present: true}));
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
