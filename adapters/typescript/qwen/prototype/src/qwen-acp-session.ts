// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { delimiter, isAbsolute, join } from "node:path";
import { fileURLToPath } from "node:url";
import { mkdir, mkdtemp, realpath, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";

import type { McpServer, Usage } from "@agentclientprotocol/sdk";
import type { AgentUsage } from "nemo-fabric-adapter-contract";
import { LifecycleError, type AdapterStartInput } from "nemo-fabric-adapters-common";

import {
  rejectUnclaimedConfig,
  selectBlockedTools,
  selectMcpServers,
  selectModel,
  selectPermissionMode,
  selectSkillPaths,
  selectSystemPrompt,
} from "../../src/configuration.js";
import { QwenDiagnostics, qwenSettings, qwenWorkingDirectory } from "../../src/qwen-sdk.js";
import type { QwenSession, QwenSessionFactory, QwenTurn } from "../../src/runtime.js";
import { AcpProcessRunner, AcpRunnerError, type AcpRunnerLimits } from "./acp-runner.js";

async function resolveCommand(command: string, environment: NodeJS.ProcessEnv): Promise<string> {
  const candidates = isAbsolute(command)
    ? [command]
    : (environment.PATH ?? environment.Path ?? "")
      .split(delimiter)
      .filter((entry) => entry.length > 0)
      .map((entry) => join(entry, command));
  for (const candidate of candidates) {
    try {
      const resolved = await realpath(candidate);
      if ((await stat(resolved)).isFile()) return resolved;
    } catch {
      // Try the next path entry.
    }
  }
  throw new LifecycleError(
    "qwen_acp_mcp_command_unavailable",
    "A Qwen ACP stdio MCP command could not be resolved to an absolute executable path",
  );
}

async function qwenCliPath(): Promise<string> {
  try {
    const packageUrl = import.meta.resolve("@qwen-code/sdk/package.json");
    const path = fileURLToPath(new URL("./dist/cli/cli.js", packageUrl));
    if ((await stat(path)).isFile()) return path;
  } catch {
    // Report one stable adapter error below.
  }
  throw new LifecycleError("qwen_sdk_missing", "Install the supported @qwen-code/sdk package to run Qwen ACP");
}

async function acpMcpServers(input: AdapterStartInput, environment: NodeJS.ProcessEnv): Promise<McpServer[]> {
  const configured = selectMcpServers(
    input.config,
    input.runtimeContext.environment.env ?? {},
    process.env,
  );
  const result: McpServer[] = [];
  for (const [name, server] of Object.entries(configured)) {
    if ((server.includeTools?.length ?? 0) > 0 || (server.excludeTools?.length ?? 0) > 0) {
      throw new LifecycleError(
        "qwen_acp_mcp_filter_unsupported",
        "ACP does not carry Qwen per-server MCP tool filters",
      );
    }
    if (server.command !== undefined) {
      result.push({
        name,
        command: await resolveCommand(server.command, environment),
        args: [...(server.args ?? [])],
        env: Object.entries(server.env ?? {}).map(([variable, value]) => ({ name: variable, value })),
      });
      continue;
    }
    if (server.httpUrl !== undefined) {
      result.push({
        type: "http",
        name,
        url: server.httpUrl,
        headers: Object.entries(server.headers ?? {}).map(([headerName, value]) => ({ name: headerName, value })),
      });
      continue;
    }
    throw new LifecycleError("qwen_acp_mcp_invalid_server", "Qwen ACP received an invalid MCP server projection");
  }
  return result;
}

function systemInstructionArgs(systemPrompt: ReturnType<typeof selectSystemPrompt>): string[] {
  if (systemPrompt === undefined) return [];
  if (typeof systemPrompt === "string") return ["--system-prompt", systemPrompt];
  return ["--append-system-prompt", systemPrompt.append];
}

function usageDelta(current: Usage | undefined, previous: Usage | undefined): AgentUsage | undefined {
  if (current === undefined) return undefined;
  return {
    input_tokens: Math.max(0, current.inputTokens - (previous?.inputTokens ?? 0)),
    output_tokens: Math.max(0, current.outputTokens - (previous?.outputTokens ?? 0)),
    total_tokens: Math.max(0, current.totalTokens - (previous?.totalTokens ?? 0)),
  };
}

class QwenAcpSession implements QwenSession {
  private stopped = false;
  private previousUsage?: Usage;

  constructor(
    private readonly runner: AcpProcessRunner,
    private readonly profile: string,
    private readonly diagnostics: QwenDiagnostics,
  ) {}

  async prompt(text: string): Promise<QwenTurn> {
    try {
      const result = await this.runner.prompt(text);
      const turnUsage = usageDelta(result.usage, this.previousUsage);
      this.previousUsage = result.usage;
      if (this.diagnostics.hasMcpFailure()) {
        return {
          error: "error_mcp_unavailable",
          ...(turnUsage === undefined ? {} : { usage: turnUsage }),
        };
      }
      if (result.stopReason !== "end_turn") {
        return {
          error: result.stopReason === "max_turn_requests" ? "error_max_turns" : "error_during_execution",
          ...(turnUsage === undefined ? {} : { usage: turnUsage }),
        };
      }
      return {
        ...(result.text === undefined ? {} : { text: result.text }),
        ...(turnUsage === undefined ? {} : { usage: turnUsage }),
      };
    } catch (error) {
      if (this.diagnostics.hasMcpFailure()) {
        return { error: "error_mcp_unavailable" };
      }
      if (error instanceof AcpRunnerError) {
        throw new LifecycleError(error.code, error.message, { retryable: false });
      }
      throw new LifecycleError("qwen_acp_session_failed", "Qwen ACP session communication failed", { retryable: false });
    }
  }

  async close(): Promise<void> {
    if (this.stopped) return;
    this.stopped = true;
    try {
      await this.runner.close();
    } finally {
      await rm(this.profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
    }
  }
}

export class QwenAcpSessionFactory implements QwenSessionFactory {
  constructor(private readonly limits?: AcpRunnerLimits) {}

  async create(input: AdapterStartInput): Promise<QwenSession> {
    rejectUnclaimedConfig(input.config);
    const model = selectModel(input.config);
    const systemPrompt = selectSystemPrompt(input.config);
    const permissionMode = selectPermissionMode(input.config);
    const blockedTools = selectBlockedTools(input.config);
    const skillPaths = await selectSkillPaths(input.config, input.baseDir);
    const workspace = qwenWorkingDirectory(input);
    const configuredEnvironment = input.runtimeContext.environment.env ?? {};
    const credential = configuredEnvironment[model.apiKeyEnv] ?? process.env[model.apiKeyEnv];
    if (credential === undefined || credential.length === 0) {
      throw new LifecycleError("qwen_credential_missing", "The configured Qwen model credential variable is not set");
    }
    const profile = await mkdtemp(join(tmpdir(), "nemo-fabric-qwen-acp-"));
    let runner: AcpProcessRunner | undefined;
    try {
      const home = join(profile, "home");
      const runtime = join(profile, "runtime");
      const systemSettings = join(profile, "system-settings.json");
      await Promise.all([mkdir(home), mkdir(runtime)]);
      const environment: NodeJS.ProcessEnv = {
        ...process.env,
        ...configuredEnvironment,
        [model.apiKeyEnv]: credential,
        QWEN_HOME: home,
        QWEN_RUNTIME_DIR: runtime,
        QWEN_CODE_SYSTEM_SETTINGS_PATH: systemSettings,
        QWEN_USAGE_STATISTICS_ENABLED: "0",
        QWEN_TELEMETRY_ENABLED: "0",
        NO_COLOR: "1",
      };
      const mcpServers = await acpMcpServers(input, environment);
      const mcpServerNames = mcpServers.map((server) => server.name);
      const diagnostics = new QwenDiagnostics();
      await writeFile(
        systemSettings,
        `${JSON.stringify(qwenSettings(model, blockedTools, skillPaths, mcpServerNames))}\n`,
        { mode: 0o600 },
      );
      const noMcpMarker = `nemo-fabric-no-mcp-${process.pid}`;
      runner = await AcpProcessRunner.start({
        command: process.execPath,
        args: [
          await qwenCliPath(),
          "--acp",
          "--model",
          model.id,
          "--auth-type",
          "openai",
          "--approval-mode",
          permissionMode,
          "--extensions",
          "none",
          "--allowed-mcp-server-names",
          (mcpServerNames.length === 0 ? [noMcpMarker] : mcpServerNames).join(","),
          ...systemInstructionArgs(systemPrompt),
          ...(blockedTools.length === 0 ? [] : ["--exclude-tools", blockedTools.join(",")]),
        ],
        cwd: workspace,
        env: environment,
        mcpServers,
        limits: this.limits,
        onStderr: (message) => diagnostics.consume(message),
      });
      return new QwenAcpSession(runner, profile, diagnostics);
    } catch (error) {
      await runner?.close().catch(() => undefined);
      await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
      if (error instanceof LifecycleError) throw error;
      if (error instanceof AcpRunnerError) throw new LifecycleError(error.code, error.message);
      throw new LifecycleError("qwen_acp_start_failed", "Qwen could not start an ACP session");
    }
  }
}
