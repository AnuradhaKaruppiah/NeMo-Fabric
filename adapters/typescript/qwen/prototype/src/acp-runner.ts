// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { Readable, Writable } from "node:stream";

import * as acp from "@agentclientprotocol/sdk";

const DEFAULT_START_TIMEOUT_MS = 30_000;
const DEFAULT_PROMPT_TIMEOUT_MS = 120_000;
const DEFAULT_STOP_TIMEOUT_MS = 5_000;
const DEFAULT_MAX_FRAME_BYTES = 1024 * 1024;
const DEFAULT_MAX_TEXT_BYTES = 1024 * 1024;
const DEFAULT_MAX_UPDATES = 10_000;

export interface AcpRunnerLimits {
  startTimeoutMs?: number;
  promptTimeoutMs?: number;
  stopTimeoutMs?: number;
  maxFrameBytes?: number;
  maxTextBytes?: number;
  maxUpdates?: number;
}

export interface AcpRunnerStartInput {
  command: string;
  args: string[];
  cwd: string;
  env: NodeJS.ProcessEnv;
  mcpServers?: acp.McpServer[];
  limits?: AcpRunnerLimits;
  onStderr?: (message: string) => void;
}

export interface AcpTurnResult {
  text?: string;
  stopReason: acp.StopReason;
  usage?: acp.Usage;
  updateCounts: Record<string, number>;
  permissionRequests: number;
}

interface TurnCollector {
  chunks: string[];
  textBytes: number;
  updates: number;
  updateCounts: Record<string, number>;
  permissionRequests: number;
  failure?: AcpRunnerError;
}

interface RunnerState {
  sessionId?: string;
  turn?: TurnCollector;
}

interface ResolvedLimits {
  startTimeoutMs: number;
  promptTimeoutMs: number;
  stopTimeoutMs: number;
  maxFrameBytes: number;
  maxTextBytes: number;
  maxUpdates: number;
}

export class AcpRunnerError extends Error {
  constructor(readonly code: string, message: string, cause?: unknown) {
    super(message, cause === undefined ? undefined : { cause });
    this.name = "AcpRunnerError";
  }
}

function resolveLimits(input: AcpRunnerLimits | undefined): ResolvedLimits {
  return {
    startTimeoutMs: input?.startTimeoutMs ?? DEFAULT_START_TIMEOUT_MS,
    promptTimeoutMs: input?.promptTimeoutMs ?? DEFAULT_PROMPT_TIMEOUT_MS,
    stopTimeoutMs: input?.stopTimeoutMs ?? DEFAULT_STOP_TIMEOUT_MS,
    maxFrameBytes: input?.maxFrameBytes ?? DEFAULT_MAX_FRAME_BYTES,
    maxTextBytes: input?.maxTextBytes ?? DEFAULT_MAX_TEXT_BYTES,
    maxUpdates: input?.maxUpdates ?? DEFAULT_MAX_UPDATES,
  };
}

function deadline<T>(
  promise: Promise<T>,
  milliseconds: number,
  code: string,
  message: string,
  onTimeout?: () => void,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      onTimeout?.();
      reject(new AcpRunnerError(code, message));
    }, milliseconds);
    timer.unref();
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

function boundedFrames(maximumBytes: number): TransformStream<Uint8Array, Uint8Array> {
  let currentBytes = 0;
  return new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      for (const byte of chunk) {
        if (byte === 0x0a) {
          currentBytes = 0;
        } else if (++currentBytes > maximumBytes) {
          controller.error(new AcpRunnerError("acp_frame_too_large", "The ACP agent emitted an oversized protocol frame"));
          return;
        }
      }
      controller.enqueue(chunk);
    },
  });
}

function collectUpdate(state: RunnerState, notification: acp.SessionNotification, limits: ResolvedLimits): void {
  const turn = state.turn;
  if (turn === undefined || notification.sessionId !== state.sessionId || turn.failure !== undefined) return;
  turn.updates += 1;
  if (turn.updates > limits.maxUpdates) {
    turn.failure = new AcpRunnerError("acp_update_limit_exceeded", "The ACP agent emitted too many updates for one turn");
    return;
  }
  const kind = notification.update.sessionUpdate;
  turn.updateCounts[kind] = (turn.updateCounts[kind] ?? 0) + 1;
  if (kind !== "agent_message_chunk" || notification.update.content.type !== "text") return;
  const text = notification.update.content.text;
  turn.textBytes += Buffer.byteLength(text, "utf8");
  if (turn.textBytes > limits.maxTextBytes) {
    turn.failure = new AcpRunnerError("acp_text_limit_exceeded", "The ACP agent emitted too much assistant text for one turn");
    return;
  }
  turn.chunks.push(text);
}

function validateMcpCapabilities(capabilities: acp.AgentCapabilities, servers: acp.McpServer[]): void {
  for (const server of servers) {
    if ("type" in server && server.type === "http" && capabilities.mcpCapabilities?.http !== true) {
      throw new AcpRunnerError("acp_mcp_unsupported", "The ACP agent does not advertise HTTP MCP support");
    }
    if ("type" in server && server.type === "sse" && capabilities.mcpCapabilities?.sse !== true) {
      throw new AcpRunnerError("acp_mcp_unsupported", "The ACP agent does not advertise SSE MCP support");
    }
  }
}

async function terminate(child: ChildProcessWithoutNullStreams, timeoutMs: number): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill("SIGTERM");
  await new Promise<void>((resolve) => {
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      resolve();
    }, timeoutMs);
    timer.unref();
    child.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

export class AcpProcessRunner {
  readonly capabilities: acp.AgentCapabilities;
  readonly session: acp.NewSessionResponse;
  private readonly child: ChildProcessWithoutNullStreams;
  private readonly connection: acp.ClientConnection;
  private readonly limits: ResolvedLimits;
  private readonly state: RunnerState;
  private readonly processFailure: Promise<never>;
  private readonly lifecycle: { closing: boolean };

  private constructor(input: {
    child: ChildProcessWithoutNullStreams;
    connection: acp.ClientConnection;
    capabilities: acp.AgentCapabilities;
    session: acp.NewSessionResponse;
    limits: ResolvedLimits;
    state: RunnerState;
    processFailure: Promise<never>;
    lifecycle: { closing: boolean };
  }) {
    this.child = input.child;
    this.connection = input.connection;
    this.capabilities = input.capabilities;
    this.session = input.session;
    this.limits = input.limits;
    this.state = input.state;
    this.processFailure = input.processFailure;
    this.lifecycle = input.lifecycle;
  }

  static async start(input: AcpRunnerStartInput): Promise<AcpProcessRunner> {
    const limits = resolveLimits(input.limits);
    const child = spawn(input.command, input.args, { cwd: input.cwd, env: input.env, shell: false });
    if (input.onStderr === undefined) {
      child.stderr.resume();
    } else {
      child.stderr.setEncoding("utf8");
      child.stderr.on("data", (message: string) => input.onStderr?.(message));
    }
    const state: RunnerState = {};
    const lifecycle = { closing: false };
    const processFailure = new Promise<never>((_resolve, reject) => {
      child.once("error", () => reject(new AcpRunnerError("acp_process_unavailable", "The ACP agent process could not be started")));
      child.once("exit", (code, signal) => {
        if (!lifecycle.closing) {
          reject(new AcpRunnerError("acp_process_exited", `The ACP agent process exited unexpectedly (${code ?? signal ?? "unknown"})`));
        }
      });
    });
    void processFailure.catch(() => undefined);
    const output = Writable.toWeb(child.stdin) as WritableStream<Uint8Array>;
    const inputStream = (Readable.toWeb(child.stdout) as ReadableStream<Uint8Array>).pipeThrough(boundedFrames(limits.maxFrameBytes));
    const app = acp.client({ name: "nemo-fabric-acp-prototype" })
      .onRequest(acp.methods.client.session.requestPermission, () => {
        if (state.turn !== undefined) state.turn.permissionRequests += 1;
        return { outcome: { outcome: "cancelled" } };
      })
      .onNotification(acp.methods.client.session.update, (context) => collectUpdate(state, context.params, limits));
    const connection = app.connect(acp.ndJsonStream(output, inputStream));
    try {
      const initialized = await deadline(
        Promise.race([
          connection.agent.request(acp.methods.agent.initialize, {
            protocolVersion: acp.PROTOCOL_VERSION,
            clientCapabilities: {},
            clientInfo: { name: "NVIDIA NeMo Fabric ACP prototype", version: "0.1.0" },
          }),
          processFailure,
        ]),
        limits.startTimeoutMs,
        "acp_initialize_timeout",
        "Timed out while initializing the ACP agent",
      );
      if (initialized.protocolVersion !== acp.PROTOCOL_VERSION) {
        throw new AcpRunnerError("acp_protocol_mismatch", "The ACP agent negotiated an unsupported protocol version");
      }
      const capabilities = initialized.agentCapabilities ?? {};
      const mcpServers = input.mcpServers ?? [];
      validateMcpCapabilities(capabilities, mcpServers);
      const session = await deadline(
        Promise.race([
          connection.agent.request(acp.methods.agent.session.new, { cwd: input.cwd, mcpServers }),
          processFailure,
        ]),
        limits.startTimeoutMs,
        "acp_session_timeout",
        "Timed out while creating an ACP session",
      );
      state.sessionId = session.sessionId;
      return new AcpProcessRunner({
        child,
        connection,
        capabilities,
        session,
        limits,
        state,
        processFailure,
        lifecycle,
      });
    } catch (error) {
      lifecycle.closing = true;
      connection.close(error);
      await terminate(child, limits.stopTimeoutMs);
      if (error instanceof AcpRunnerError) throw error;
      throw new AcpRunnerError("acp_start_failed", "The ACP agent failed during startup", error);
    }
  }

  async prompt(text: string): Promise<AcpTurnResult> {
    if (this.state.turn !== undefined) {
      throw new AcpRunnerError("acp_turn_active", "An ACP prompt turn is already active");
    }
    const turn: TurnCollector = {
      chunks: [],
      textBytes: 0,
      updates: 0,
      updateCounts: {},
      permissionRequests: 0,
    };
    this.state.turn = turn;
    const request = Promise.race([
      this.connection.agent.request(acp.methods.agent.session.prompt, {
        sessionId: this.session.sessionId,
        prompt: [{ type: "text", text }],
      }),
      this.processFailure,
    ]);
    try {
      const response = await deadline(
        request,
        this.limits.promptTimeoutMs,
        "acp_prompt_timeout",
        "Timed out while waiting for the ACP prompt turn",
        () => {
          void this.cancel().catch(() => undefined);
        },
      );
      if (turn.failure !== undefined) throw turn.failure;
      const combined = turn.chunks.join("");
      return {
        ...(combined.length === 0 ? {} : { text: combined }),
        stopReason: response.stopReason,
        ...(response.usage == null ? {} : { usage: response.usage }),
        updateCounts: { ...turn.updateCounts },
        permissionRequests: turn.permissionRequests,
      };
    } catch (error) {
      if (error instanceof AcpRunnerError) throw error;
      try {
        await deadline(this.processFailure, 100, "acp_process_status_timeout", "The ACP process status did not settle");
      } catch (processError) {
        if (processError instanceof AcpRunnerError && processError.code !== "acp_process_status_timeout") throw processError;
      }
      throw new AcpRunnerError("acp_prompt_failed", "The ACP prompt turn failed");
    } finally {
      this.state.turn = undefined;
    }
  }

  async cancel(): Promise<void> {
    await this.connection.agent.notify(acp.methods.agent.session.cancel, { sessionId: this.session.sessionId });
  }

  async close(): Promise<void> {
    if (this.lifecycle.closing) return;
    this.lifecycle.closing = true;
    try {
      if (this.state.turn !== undefined) await this.cancel().catch(() => undefined);
      if (this.capabilities.sessionCapabilities?.close != null) {
        await deadline(
          this.connection.agent.request(acp.methods.agent.session.close, { sessionId: this.session.sessionId }),
          this.limits.stopTimeoutMs,
          "acp_close_timeout",
          "Timed out while closing the ACP session",
        ).catch(() => undefined);
      }
    } finally {
      this.connection.close();
      await terminate(this.child, this.limits.stopTimeoutMs);
    }
  }
}
