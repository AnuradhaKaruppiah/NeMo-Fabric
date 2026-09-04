// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Versioned control protocol and resident process for a Fabric capsule.

use std::collections::BTreeMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::str::FromStr;
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Capsule-control wire protocol version.
pub const PROTOCOL_VERSION: &str = "fabric.capsule-control.v1alpha1";
/// Default Unix socket installed in a Fabric capsule.
pub const DEFAULT_SOCKET: &str = "/sandbox/.fabric/control/capsule.sock";
const MAX_MESSAGE_BYTES: usize = 4 * 1024 * 1024;
/// Maximum size of one artifact exported through capsule control.
pub const MAX_ARTIFACT_BYTES: u64 = 256 * 1024;
const CHILD_EXIT_GRACE: Duration = Duration::from_secs(2);

/// One supported capsule lifecycle operation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CapsuleOperation {
    /// Start one adapter runtime.
    Start,
    /// Invoke the active adapter runtime.
    Invoke,
    /// Stop the active adapter runtime.
    Stop,
}

impl CapsuleOperation {
    /// Stable wire name.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Start => "start",
            Self::Invoke => "invoke",
            Self::Stop => "stop",
        }
    }
}

impl FromStr for CapsuleOperation {
    type Err = ();

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "start" => Ok(Self::Start),
            "invoke" => Ok(Self::Invoke),
            "stop" => Ok(Self::Stop),
            _ => Err(()),
        }
    }
}

/// Exact process to retain behind the capsule control socket.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CapsuleAdapterProcess {
    /// Executable followed by its arguments. No shell expansion is performed.
    pub command: Vec<String>,
    /// Working directory inside the capsule.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cwd: Option<PathBuf>,
    /// Adapter-specific environment values.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub env: BTreeMap<String, String>,
}

/// Typed payload for one capsule operation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "operation", rename_all = "snake_case", deny_unknown_fields)]
pub enum CapsuleCommand {
    /// Start an adapter process and send its lifecycle start request.
    Start {
        /// Process installed in the capsule image.
        process: CapsuleAdapterProcess,
        /// Adapter lifecycle request forwarded unchanged to the process.
        lifecycle: Value,
    },
    /// Forward one invocation to the resident adapter process.
    Invoke {
        /// Adapter lifecycle request forwarded unchanged to the process.
        lifecycle: Value,
    },
    /// Stop and terminate the resident adapter process.
    Stop {
        /// Adapter lifecycle request forwarded unchanged to the process.
        lifecycle: Value,
    },
}

impl CapsuleCommand {
    /// Operation represented by this command.
    pub const fn operation(&self) -> CapsuleOperation {
        match self {
            Self::Start { .. } => CapsuleOperation::Start,
            Self::Invoke { .. } => CapsuleOperation::Invoke,
            Self::Stop { .. } => CapsuleOperation::Stop,
        }
    }

    fn lifecycle(&self) -> &Value {
        match self {
            Self::Start { lifecycle, .. }
            | Self::Invoke { lifecycle }
            | Self::Stop { lifecycle } => lifecycle,
        }
    }
}

/// One correlated request sent through OpenShell to the capsule controller.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CapsuleControlRequest {
    /// Exact protocol version.
    pub protocol_version: String,
    /// Unique operation id used for retry inspection and correlation.
    pub operation_id: String,
    /// Fabric environment id bound to the sandbox.
    pub environment_id: String,
    /// Fabric runtime id bound to the session.
    pub runtime_id: String,
    /// Maximum time the runner may wait for the adapter response.
    pub timeout_seconds: u64,
    /// Typed lifecycle operation.
    #[serde(flatten)]
    pub command: CapsuleCommand,
}

/// Stable capsule-control failure.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CapsuleFailure {
    /// Machine-readable failure code.
    pub code: String,
    /// Sanitized failure message.
    pub message: String,
}

/// Result of one correlated capsule-control operation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CapsuleControlResponse {
    /// Exact protocol version.
    pub protocol_version: String,
    /// Request operation id.
    pub operation_id: String,
    /// Request environment id.
    pub environment_id: String,
    /// Request runtime id.
    pub runtime_id: String,
    /// Request operation.
    pub operation: CapsuleOperation,
    /// Terminal operation outcome.
    #[serde(flatten)]
    pub outcome: CapsuleOutcome,
}

/// Terminal capsule operation outcome.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub enum CapsuleOutcome {
    /// The adapter returned a lifecycle response.
    Succeeded {
        /// Raw adapter lifecycle response.
        output: Value,
    },
    /// Capsule control failed before a valid adapter response was returned.
    Failed {
        /// Stable capsule failure.
        error: CapsuleFailure,
    },
}

impl CapsuleControlResponse {
    fn succeeded(request: &CapsuleControlRequest, output: Value) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION.to_string(),
            operation_id: request.operation_id.clone(),
            environment_id: request.environment_id.clone(),
            runtime_id: request.runtime_id.clone(),
            operation: request.command.operation(),
            outcome: CapsuleOutcome::Succeeded { output },
        }
    }

    fn failed(request: &CapsuleControlRequest, code: &str, message: impl Into<String>) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION.to_string(),
            operation_id: request.operation_id.clone(),
            environment_id: request.environment_id.clone(),
            runtime_id: request.runtime_id.clone(),
            operation: request.command.operation(),
            outcome: CapsuleOutcome::Failed {
                error: CapsuleFailure {
                    code: code.to_string(),
                    message: message.into(),
                },
            },
        }
    }
}

/// Resolve the capsule socket from `FABRIC_CAPSULE_SOCKET` or the stable default.
pub fn default_socket_path() -> PathBuf {
    std::env::var_os("FABRIC_CAPSULE_SOCKET")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(DEFAULT_SOCKET))
}

/// One bounded request to read an adapter-declared artifact from the capsule.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactExportRequest {
    /// Absolute artifact root supplied to the adapter through `RuntimeContext`.
    pub root: PathBuf,
    /// Adapter-declared path relative to `root`.
    pub path: PathBuf,
    /// Maximum bytes the caller is willing to receive.
    pub max_bytes: u64,
}

/// Export one regular file without allowing traversal or symlink escape.
pub fn export_artifact(mut input: impl Read, mut output: impl Write) -> std::io::Result<()> {
    let request = read_json::<ArtifactExportRequest>(&mut input)?;
    if !request.root.is_absolute() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "artifact root must be absolute",
        ));
    }
    if request.path.as_os_str().is_empty()
        || request.path.is_absolute()
        || request
            .path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "artifact path must be a non-empty relative path without traversal",
        ));
    }
    if request.max_bytes == 0 || request.max_bytes > MAX_ARTIFACT_BYTES {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!("artifact max_bytes must be between 1 and {MAX_ARTIFACT_BYTES}"),
        ));
    }

    let root = request.root.canonicalize()?;
    let path = request.root.join(&request.path).canonicalize()?;
    if !path.starts_with(&root) {
        return Err(std::io::Error::new(
            std::io::ErrorKind::PermissionDenied,
            "artifact path resolves outside the artifact root",
        ));
    }
    if !path.metadata()?.is_file() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "artifact path must resolve to a regular file",
        ));
    }

    let mut content = Vec::new();
    std::fs::File::open(&path)?
        .take(request.max_bytes + 1)
        .read_to_end(&mut content)?;
    if content.len() as u64 > request.max_bytes {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!(
                "artifact exceeds the {}-byte request limit",
                request.max_bytes
            ),
        ));
    }
    output.write_all(&content)?;
    output.flush()
}

#[cfg(unix)]
struct AdapterHost {
    environment_id: String,
    runtime_id: String,
    child: Child,
    stdin: ChildStdin,
    responses: Receiver<std::result::Result<String, String>>,
}

#[cfg(unix)]
impl AdapterHost {
    fn spawn(
        environment_id: &str,
        runtime_id: &str,
        process: &CapsuleAdapterProcess,
    ) -> std::io::Result<Self> {
        let Some((program, args)) = process.command.split_first() else {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "adapter command must not be empty",
            ));
        };
        if program.is_empty() || args.iter().any(String::is_empty) {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "adapter command arguments must not be empty",
            ));
        }
        let mut command = Command::new(program);
        command
            .args(args)
            .envs(&process.env)
            .env("FABRIC_RUNTIME_ID", runtime_id)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        if let Some(cwd) = &process.cwd {
            command.current_dir(cwd);
        }
        let mut child = command.spawn()?;
        let Some(stdin) = child.stdin.take() else {
            let _ = child.kill();
            let _ = child.wait();
            return Err(std::io::Error::other(
                "adapter process stdin was not available",
            ));
        };
        let Some(stdout) = child.stdout.take() else {
            let _ = child.kill();
            let _ = child.wait();
            return Err(std::io::Error::other(
                "adapter process stdout was not available",
            ));
        };
        let (sender, responses) = mpsc::channel();
        if let Err(error) = thread::Builder::new()
            .name(format!("fabric-capsule-{runtime_id}"))
            .spawn(move || {
                let mut stdout = BufReader::new(stdout);
                loop {
                    let mut line = String::new();
                    match stdout.read_line(&mut line) {
                        Ok(0) => break,
                        Ok(_) => {
                            while line.ends_with(['\n', '\r']) {
                                line.pop();
                            }
                            if sender.send(Ok(line)).is_err() {
                                break;
                            }
                        }
                        Err(error) => {
                            let _ = sender.send(Err(error.to_string()));
                            break;
                        }
                    }
                }
            })
        {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
        Ok(Self {
            environment_id: environment_id.to_string(),
            runtime_id: runtime_id.to_string(),
            child,
            stdin,
            responses,
        })
    }

    fn exchange(&mut self, lifecycle: &Value, timeout: Duration) -> Result<Value, String> {
        if let Some(status) = self.child.try_wait().map_err(|error| error.to_string())? {
            return Err(format!(
                "adapter process exited before the operation ({status})"
            ));
        }
        let mut encoded = serde_json::to_vec(lifecycle).map_err(|error| error.to_string())?;
        encoded.push(b'\n');
        self.stdin
            .write_all(&encoded)
            .and_then(|()| self.stdin.flush())
            .map_err(|error| format!("could not write adapter lifecycle request: {error}"))?;
        let line = match self.responses.recv_timeout(timeout) {
            Ok(result) => result?,
            Err(RecvTimeoutError::Timeout) => {
                return Err(format!(
                    "adapter did not respond within {} seconds",
                    timeout.as_secs()
                ));
            }
            Err(RecvTimeoutError::Disconnected) => {
                return Err("adapter process closed its lifecycle output".to_string());
            }
        };
        serde_json::from_str(&line)
            .map_err(|error| format!("adapter returned invalid lifecycle JSON: {error}"))
    }

    fn terminate(&mut self) -> std::io::Result<()> {
        let deadline = Instant::now() + CHILD_EXIT_GRACE;
        loop {
            if self.child.try_wait()?.is_some() {
                return Ok(());
            }
            if Instant::now() >= deadline {
                self.child.kill()?;
                return self.child.wait().map(|_| ());
            }
            thread::sleep(Duration::from_millis(10));
        }
    }
}

#[cfg(unix)]
fn handle_request(
    request: &CapsuleControlRequest,
    host: &mut Option<AdapterHost>,
) -> CapsuleControlResponse {
    if request.protocol_version != PROTOCOL_VERSION {
        return CapsuleControlResponse::failed(
            request,
            "protocol_mismatch",
            format!(
                "expected `{PROTOCOL_VERSION}` but received `{}`",
                request.protocol_version
            ),
        );
    }
    if request.operation_id.trim().is_empty()
        || request.environment_id.trim().is_empty()
        || request.runtime_id.trim().is_empty()
        || request.timeout_seconds == 0
    {
        return CapsuleControlResponse::failed(
            request,
            "invalid_request",
            "operation, environment, runtime, and timeout fields must be non-empty",
        );
    }
    let timeout = Duration::from_secs(request.timeout_seconds);
    match &request.command {
        CapsuleCommand::Start { process, lifecycle } => {
            if let Some(active) = host {
                return CapsuleControlResponse::failed(
                    request,
                    "environment_in_use",
                    format!(
                        "environment is already bound to runtime `{}`",
                        active.runtime_id
                    ),
                );
            }
            let mut started =
                match AdapterHost::spawn(&request.environment_id, &request.runtime_id, process) {
                    Ok(started) => started,
                    Err(error) => {
                        return CapsuleControlResponse::failed(
                            request,
                            "adapter_start_failed",
                            error.to_string(),
                        );
                    }
                };
            match started.exchange(lifecycle, timeout) {
                Ok(output) => match validate_adapter_lifecycle(&output, CapsuleOperation::Start) {
                    Ok(true) => {
                        *host = Some(started);
                        CapsuleControlResponse::succeeded(request, output)
                    }
                    Ok(false) => {
                        let _ = started.terminate();
                        CapsuleControlResponse::succeeded(request, output)
                    }
                    Err(message) => {
                        let _ = started.terminate();
                        CapsuleControlResponse::failed(request, "adapter_start_failed", message)
                    }
                },
                Err(message) => {
                    let _ = started.terminate();
                    CapsuleControlResponse::failed(request, "adapter_start_failed", message)
                }
            }
        }
        CapsuleCommand::Invoke { .. } | CapsuleCommand::Stop { .. } => {
            let Some(active) = host.as_mut() else {
                return CapsuleControlResponse::failed(
                    request,
                    "runtime_unavailable",
                    "capsule has no active Fabric runtime",
                );
            };
            if active.environment_id != request.environment_id
                || active.runtime_id != request.runtime_id
            {
                return CapsuleControlResponse::failed(
                    request,
                    "runtime_mismatch",
                    format!("capsule is bound to runtime `{}`", active.runtime_id),
                );
            }
            let operation = request.command.operation();
            let result = active
                .exchange(request.command.lifecycle(), timeout)
                .and_then(|output| validate_adapter_lifecycle(&output, operation).map(|_| output));
            if operation == CapsuleOperation::Stop || result.is_err() {
                let termination = active.terminate();
                *host = None;
                if let Err(error) = termination {
                    return CapsuleControlResponse::failed(
                        request,
                        if operation == CapsuleOperation::Stop {
                            "adapter_stop_failed"
                        } else {
                            "adapter_invoke_failed"
                        },
                        error.to_string(),
                    );
                }
            }
            match result {
                Ok(output) => CapsuleControlResponse::succeeded(request, output),
                Err(message) => CapsuleControlResponse::failed(
                    request,
                    if operation == CapsuleOperation::Stop {
                        "adapter_stop_failed"
                    } else {
                        "adapter_invoke_failed"
                    },
                    message,
                ),
            }
        }
    }
}

fn validate_adapter_lifecycle(
    output: &Value,
    expected_operation: CapsuleOperation,
) -> Result<bool, String> {
    let operation = output
        .get("operation")
        .and_then(Value::as_str)
        .ok_or_else(|| "adapter lifecycle response omitted `operation`".to_string())?;
    if operation != expected_operation.as_str() {
        return Err(format!(
            "adapter returned `{operation}` for `{}`",
            expected_operation.as_str()
        ));
    }
    let status = output
        .get("outcome")
        .and_then(Value::as_object)
        .and_then(|outcome| outcome.get("status"))
        .and_then(Value::as_str)
        .ok_or_else(|| "adapter lifecycle response omitted `outcome.status`".to_string())?;
    match status {
        "succeeded" => Ok(true),
        "failed" => Ok(false),
        _ => Err(format!(
            "adapter lifecycle response returned unknown status `{status}`"
        )),
    }
}

/// Serve capsule-control requests on a Unix socket until the process is terminated.
#[cfg(unix)]
pub fn serve(socket: &Path) -> std::io::Result<()> {
    use std::os::unix::net::UnixListener;

    if let Some(parent) = socket.parent() {
        std::fs::create_dir_all(parent)?;
    }
    match std::fs::remove_file(socket) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    let listener = UnixListener::bind(socket)?;
    let mut host = None;
    for stream in listener.incoming() {
        let mut stream = stream?;
        serve_connection(&mut stream, &mut host)?;
    }
    Ok(())
}

#[cfg(unix)]
fn serve_connection<T: Read + Write>(
    stream: &mut T,
    host: &mut Option<AdapterHost>,
) -> std::io::Result<()> {
    let request = read_json_line::<CapsuleControlRequest>(&mut BufReader::new(&mut *stream))?;
    let response = handle_request(&request, host);
    write_json_line(stream, &response)
}

/// Report that capsule control requires Unix domain sockets.
#[cfg(not(unix))]
pub fn serve(_socket: &Path) -> std::io::Result<()> {
    Err(std::io::Error::new(
        std::io::ErrorKind::Unsupported,
        "Fabric capsule control requires a Unix domain socket",
    ))
}

/// Forward one stdin request to the resident capsule runner and write its response.
#[cfg(unix)]
pub fn control(
    socket: &Path,
    expected_operation: CapsuleOperation,
    mut input: impl Read,
    mut output: impl Write,
) -> std::io::Result<()> {
    use std::os::unix::net::UnixStream;

    let request = read_json::<CapsuleControlRequest>(&mut input)?;
    if request.command.operation() != expected_operation {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!(
                "control command `{}` does not match request `{}`",
                expected_operation.as_str(),
                request.command.operation().as_str()
            ),
        ));
    }
    let mut stream = UnixStream::connect(socket)?;
    write_json_line(&mut stream, &request)?;
    let response = read_json_line::<CapsuleControlResponse>(&mut BufReader::new(&mut stream))?;
    write_json(&mut output, &response)
}

/// Report that capsule control requires Unix domain sockets.
#[cfg(not(unix))]
pub fn control(
    _socket: &Path,
    _expected_operation: CapsuleOperation,
    _input: impl Read,
    _output: impl Write,
) -> std::io::Result<()> {
    Err(std::io::Error::new(
        std::io::ErrorKind::Unsupported,
        "Fabric capsule control requires a Unix domain socket",
    ))
}

fn read_json<T: for<'de> Deserialize<'de>>(reader: &mut impl Read) -> std::io::Result<T> {
    let mut bytes = Vec::new();
    reader
        .take((MAX_MESSAGE_BYTES + 1) as u64)
        .read_to_end(&mut bytes)?;
    if bytes.len() > MAX_MESSAGE_BYTES {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("capsule message exceeds {MAX_MESSAGE_BYTES} bytes"),
        ));
    }
    serde_json::from_slice(&bytes)
        .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidData, error))
}

fn read_json_line<T: for<'de> Deserialize<'de>>(reader: &mut impl BufRead) -> std::io::Result<T> {
    let mut bytes = Vec::new();
    reader
        .take((MAX_MESSAGE_BYTES + 1) as u64)
        .read_until(b'\n', &mut bytes)?;
    if bytes.len() > MAX_MESSAGE_BYTES {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("capsule message exceeds {MAX_MESSAGE_BYTES} bytes"),
        ));
    }
    if bytes.last() == Some(&b'\n') {
        bytes.pop();
    }
    serde_json::from_slice(&bytes)
        .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidData, error))
}

fn write_json(writer: &mut impl Write, value: &impl Serialize) -> std::io::Result<()> {
    serde_json::to_writer(&mut *writer, value)
        .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidData, error))?;
    writer.flush()
}

fn write_json_line(writer: &mut impl Write, value: &impl Serialize) -> std::io::Result<()> {
    serde_json::to_writer(&mut *writer, value)
        .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidData, error))?;
    writer.write_all(b"\n")?;
    writer.flush()
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::os::unix::net::UnixListener;

    #[test]
    fn artifact_export_is_bounded_and_cannot_escape_its_root() {
        let root = std::env::temp_dir().join(format!(
            "fabric-capsule-artifact-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        std::fs::create_dir_all(&root).expect("create artifact root");
        std::fs::write(root.join("receipt.json"), br#"{"status":"delivered"}"#)
            .expect("write artifact");

        let request = ArtifactExportRequest {
            root: root.clone(),
            path: PathBuf::from("receipt.json"),
            max_bytes: 1024,
        };
        let mut output = Vec::new();
        export_artifact(
            serde_json::to_vec(&request)
                .expect("request JSON")
                .as_slice(),
            &mut output,
        )
        .expect("export artifact");
        assert_eq!(output, br#"{"status":"delivered"}"#);

        let traversal = ArtifactExportRequest {
            path: PathBuf::from("../outside"),
            ..request
        };
        let error = export_artifact(
            serde_json::to_vec(&traversal)
                .expect("traversal JSON")
                .as_slice(),
            Vec::new(),
        )
        .expect_err("traversal must fail");
        assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);

        std::fs::remove_dir_all(root).expect("remove artifact root");
    }

    fn request(command: CapsuleCommand) -> CapsuleControlRequest {
        CapsuleControlRequest {
            protocol_version: PROTOCOL_VERSION.to_string(),
            operation_id: format!("operation-{}", command.operation().as_str()),
            environment_id: "environment-1".to_string(),
            runtime_id: "runtime-1".to_string(),
            timeout_seconds: 2,
            command,
        }
    }

    fn lifecycle(operation: &str) -> Value {
        serde_json::json!({"operation": operation, "payload": {}})
    }

    #[test]
    fn resident_host_runs_one_start_invoke_stop_session() {
        let script = r#"
while IFS= read -r line; do
  case "$line" in
    *\"operation\":\"start\"*) op=start ;;
    *\"operation\":\"invoke\"*) op=invoke ;;
    *\"operation\":\"stop\"*) op=stop ;;
  esac
  printf '{"operation":"%s","outcome":{"status":"succeeded","output":{"seen":"%s"}}}\n' "$op" "$op"
  [ "$op" = stop ] && exit 0
done
"#;
        let start = request(CapsuleCommand::Start {
            process: CapsuleAdapterProcess {
                command: vec!["/bin/sh".to_string(), "-c".to_string(), script.to_string()],
                cwd: None,
                env: BTreeMap::new(),
            },
            lifecycle: lifecycle("start"),
        });
        let start: CapsuleControlRequest =
            serde_json::from_value(serde_json::to_value(start).expect("serialize start request"))
                .expect("deserialize start request");
        let mut host = None;

        let started = handle_request(&start, &mut host);
        let second = handle_request(&start, &mut host);
        let invoked = handle_request(
            &request(CapsuleCommand::Invoke {
                lifecycle: lifecycle("invoke"),
            }),
            &mut host,
        );
        let stopped = handle_request(
            &request(CapsuleCommand::Stop {
                lifecycle: lifecycle("stop"),
            }),
            &mut host,
        );

        assert!(matches!(started.outcome, CapsuleOutcome::Succeeded { .. }));
        let _: CapsuleControlResponse = serde_json::from_value(
            serde_json::to_value(&started).expect("serialize start response"),
        )
        .expect("deserialize start response");
        assert!(matches!(
            second.outcome,
            CapsuleOutcome::Failed { ref error } if error.code == "environment_in_use"
        ));
        assert!(matches!(invoked.outcome, CapsuleOutcome::Succeeded { .. }));
        assert!(matches!(stopped.outcome, CapsuleOutcome::Succeeded { .. }));
        assert!(host.is_none());
    }

    #[test]
    fn failed_adapter_start_does_not_retain_the_process() {
        let start = request(CapsuleCommand::Start {
            process: CapsuleAdapterProcess {
                command: vec![
                    "/bin/sh".to_string(),
                    "-c".to_string(),
                    "read -r line; printf '%s\\n' '{\"operation\":\"start\",\"outcome\":{\"status\":\"failed\",\"error\":{}}}'"
                        .to_string(),
                ],
                cwd: None,
                env: BTreeMap::new(),
            },
            lifecycle: lifecycle("start"),
        });
        let mut host = None;

        let response = handle_request(&start, &mut host);

        assert!(matches!(response.outcome, CapsuleOutcome::Succeeded { .. }));
        assert!(host.is_none());
    }

    #[test]
    fn unix_socket_framing_round_trips_the_typed_session() {
        let socket = std::env::temp_dir().join(format!(
            "fabric-capsule-test-{}-{}.sock",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        let listener = UnixListener::bind(&socket).expect("bind capsule test socket");
        let server = thread::spawn(move || {
            let mut host = None;
            for _ in 0..3 {
                let (mut stream, _) = listener.accept().expect("accept capsule request");
                serve_connection(&mut stream, &mut host).expect("serve capsule request");
            }
            assert!(host.is_none());
        });
        let script = r#"
while IFS= read -r line; do
  case "$line" in
    *\"operation\":\"start\"*) op=start ;;
    *\"operation\":\"invoke\"*) op=invoke ;;
    *\"operation\":\"stop\"*) op=stop ;;
  esac
  printf '{"operation":"%s","outcome":{"status":"succeeded","output":{"seen":"%s"}}}\n' "$op" "$op"
  [ "$op" = stop ] && exit 0
done
"#;
        let requests = [
            request(CapsuleCommand::Start {
                process: CapsuleAdapterProcess {
                    command: vec!["/bin/sh".to_string(), "-c".to_string(), script.to_string()],
                    cwd: None,
                    env: BTreeMap::new(),
                },
                lifecycle: lifecycle("start"),
            }),
            request(CapsuleCommand::Invoke {
                lifecycle: lifecycle("invoke"),
            }),
            request(CapsuleCommand::Stop {
                lifecycle: lifecycle("stop"),
            }),
        ];
        for request in requests {
            let input = serde_json::to_vec(&request).expect("encode capsule request");
            let mut output = Vec::new();
            control(
                &socket,
                request.command.operation(),
                input.as_slice(),
                &mut output,
            )
            .expect("control capsule");
            let response: CapsuleControlResponse =
                serde_json::from_slice(&output).expect("decode capsule response");
            assert!(matches!(response.outcome, CapsuleOutcome::Succeeded { .. }));
        }
        server.join().expect("capsule server");
        std::fs::remove_file(socket).expect("remove capsule test socket");
    }
}
