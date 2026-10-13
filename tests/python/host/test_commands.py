# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Host spec: bounded JSON input, strict flags, and one safe stdout response."""

import io
import json
import os

import sys

import pytest

if sys.platform == "win32":
    pytest.skip(
        "The Unix command bridge requires a Unix platform", allow_module_level=True
    )

from nemo_fabric.host import cli, protocol


@pytest.mark.parametrize(
    "flags", [[], ["--live"], ["--active"], ["--ready"], ["--operational"]]
)
def test_removed_check_is_rejected_before_transport(monkeypatch, capsys, flags):
    from unittest.mock import AsyncMock

    mock_client = AsyncMock()
    monkeypatch.setattr(cli.transport, "client", mock_client)
    assert cli.main(["check", *flags]) == 1
    response = json.loads(capsys.readouterr().err)
    assert response["operation"] == "unknown"
    assert response["error"]["code"] == "invalid_request"
    assert response["changed"] is False
    assert response["error"]["effects"] == "none"
    mock_client.assert_not_awaited()


def test_start_bootstraps_without_extra_lifecycle_flags(tmp_path, config):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    request = cli.parse_command(
        ["start", "--agent", "researcher", "--config", str(path)]
    )
    assert request["config"] == config


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["unknown"],
        ["stop", "--agent", "researcher"],
        ["invoke", "--agent", "researcher", "--agent", "researcher"],
        ["start", "--live"],
        ["invoke", "--surprise"],
        ["stop", "--expected-descriptor-fingerprint", "a" * 64],
        [
            "stop",
            "--agent",
            "researcher",
            "--runtime-id",
            "\u0000",
        ],
        [
            "stop",
            "--agent",
            "\u0000",
            "--runtime-id",
            "r",
        ],
        ["configure", "--agent", "researcher", "--config"],
    ],
)
def test_invalid_flags_emit_one_safe_json_diagnostic_and_exit_one(arguments, capsys):
    assert cli.main(arguments) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.endswith("\n")
    assert len(captured.err.splitlines()) == 1
    response = json.loads(captured.err)
    assert response["status"] == "failed"
    assert response["error"]["code"] == "invalid_request"
    assert response["error"]["effects"] == "none"


@pytest.mark.parametrize(
    "arguments",
    [["serve", "--agent", "researcher"], ["serve", "--expected-generation", "x"]],
)
def test_serve_errors_use_stderr_without_command_response(arguments, capsys):
    assert cli.main(arguments) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err


@pytest.mark.parametrize(
    "operation,flags,fields",
    [
        (
            "stop",
            [
                "--agent",
                "researcher",
                "--runtime-id",
                "r",
            ],
            {"agent": "researcher", "runtime_id": "r"},
        ),
        ("serve", [], {}),
    ],
)
def test_named_commands_construct_versioned_requests(operation, flags, fields):
    assert cli.parse_command([operation, *flags]) == {
        "interface_version": 3,
        "operation": operation,
        **fields,
    }


def test_config_and_input_files_are_objects_without_wrappers(tmp_path, config):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(config))
    request = cli.parse_command(
        [
            "start",
            "--agent",
            "researcher",
            "--config",
            str(path),
        ]
    )
    assert request["config"] == config
    path.write_text('{"prompt":"hello"}')
    request = cli.parse_command(
        ["invoke", "--agent", "researcher", "--runtime-id", "r", "--input", str(path)]
    )
    assert request["input"] == {"prompt": "hello"}


@pytest.mark.parametrize("value", ["route", [], [1, {"stream": True}], 1, False, None])
def test_input_accepts_sdk_json_values(tmp_path, value):
    path = tmp_path / "input.json"
    path.write_text(json.dumps(value))
    request = cli.parse_command(
        ["invoke", "--agent", "researcher", "--runtime-id", "r", "--input", str(path)]
    )
    assert request["input"] == value


@pytest.mark.parametrize("source", ["file", "stdin"])
def test_request_preserves_sdk_metadata(tmp_path, sdk_request, monkeypatch, source):
    content = json.dumps(sdk_request.to_mapping())
    path = tmp_path / "request.json"
    path.write_text(content)
    if source == "stdin":
        monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(content.encode())))
    request = cli.parse_command(
        [
            "invoke",
            "--agent",
            "researcher",
            "--runtime-id",
            "r",
            "--request",
            "-" if source == "stdin" else str(path),
        ]
    )
    assert request["request"] == sdk_request.to_mapping()
    assert "input" not in request


@pytest.mark.parametrize(
    "flags",
    [
        [],
        ["--input", "unused", "--request", "unused"],
        ["--request", "a", "--request", "b"],
    ],
)
def test_invoke_requires_one_payload_before_reading_files(flags):
    with pytest.raises(protocol.ProtocolError):
        cli.parse_command(
            ["invoke", "--agent", "researcher", "--runtime-id", "r", *flags]
        )


@pytest.mark.parametrize(
    "value", [[], None, {"request_id": ""}, {"context": []}, {"overrides": []}]
)
def test_request_uses_sdk_validation_before_transport(
    tmp_path, value, monkeypatch, capsys
):
    from unittest.mock import AsyncMock

    path = tmp_path / "request.json"
    path.write_text(json.dumps(value))
    mock_client = AsyncMock()
    monkeypatch.setattr(cli.transport, "client", mock_client)
    assert (
        cli.main(
            [
                "invoke",
                "--agent",
                "researcher",
                "--runtime-id",
                "r",
                "--request",
                str(path),
            ]
        )
        == 1
    )
    response = json.loads(capsys.readouterr().err)
    assert response["error"]["effects"] == "none"
    mock_client.assert_not_awaited()


@pytest.mark.parametrize(
    "fingerprint", ["", "a" * 63, "a" * 64, "a" * 65, "A" * 64, "g" * 64]
)
def test_removed_fingerprint_flag_is_rejected_before_sending_command(
    tmp_path, config, fingerprint
):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(protocol.ProtocolError):
        cli.parse_command(
            [
                "start",
                "--agent",
                "researcher",
                "--config",
                str(path),
                "--expected-descriptor-fingerprint",
                fingerprint,
            ]
        )


@pytest.mark.parametrize(
    "content", [b"{}{}", b'{"x":NaN}', b'{"x":1,"x":2}', b"\xff", b"{"]
)
def test_invalid_json_files_are_rejected_before_commands(tmp_path, content):
    path = tmp_path / "input.json"
    path.write_bytes(content)
    with pytest.raises(protocol.ProtocolError, match="bounded UTF-8 JSON value"):
        cli.parse_command(
            [
                "invoke",
                "--agent",
                "researcher",
                "--runtime-id",
                "r",
                "--input",
                str(path),
            ]
        )


def test_fifo_and_oversized_file_are_rejected_without_blocking(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    large = tmp_path / "large.json"
    large.write_bytes(b" " * (protocol.REQUEST_LIMIT + 1))
    for path in (fifo, large):
        with pytest.raises(protocol.ProtocolError):
            cli.parse_command(
                [
                    "invoke",
                    "--agent",
                    "researcher",
                    "--runtime-id",
                    "r",
                    "--input",
                    str(path),
                ]
            )


def test_stdin_is_read_only_when_explicitly_selected(monkeypatch):
    stream = io.TextIOWrapper(io.BytesIO(b'{"prompt":"hello"}'))
    monkeypatch.setattr("sys.stdin", stream)
    request = cli.parse_command(
        ["invoke", "--agent", "researcher", "--runtime-id", "r", "--input", "-"]
    )
    assert request["input"] == {"prompt": "hello"}


def test_combined_socket_request_limit_includes_framing():
    request = {
        "interface_version": 3,
        "operation": "invoke",
        "agent": "researcher",
        "runtime_id": "r",
        "input": {"prompt": "x" * protocol.REQUEST_LIMIT},
    }
    with pytest.raises(protocol.ProtocolError) as error:
        protocol.encode_request(request)
    assert error.value.code == "request_too_large"


def test_oversized_response_reports_unknown_effects():
    response = protocol.envelope(
        "invoke", {"output": "x" * protocol.RESPONSE_LIMIT}, changed=None
    )
    encoded = protocol.encode_response(response)
    assert len(encoded) <= protocol.RESPONSE_LIMIT
    result = json.loads(encoded)
    assert result["status"] == "failed"
    assert result["changed"] is None
    assert result["error"]["effects"] == "unknown"


@pytest.mark.parametrize(
    "response",
    [
        {
            "operation": "invoke",
            "status": "succeeded",
            "changed": None,
            "result": None,
            "error": None,
        },
        {
            "operation": "invoke",
            "status": "succeeded",
            "changed": None,
            "result": {"runtime_id": "r", "fabric_result": {"status": "failed"}},
            "error": None,
        },
        {
            "operation": "stop",
            "status": "succeeded",
            "changed": True,
            "result": {},
            "error": None,
        },
        {
            "operation": "check",
            "status": "succeeded",
            "changed": False,
            "result": {"health": None},
            "error": None,
        },
    ],
)
def test_malformed_success_response_cannot_establish_success(response):
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_response(response, response["operation"])


@pytest.mark.parametrize("source", ["unusable_stdin", "missing_file"])
def test_unusable_input_preserves_existing_bridge_error_code(
    source, tmp_path, monkeypatch, capsys
):
    path = str(tmp_path / "missing.json")
    if source == "unusable_stdin":
        monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"PRIVATE")))
        path = "-"
    assert (
        cli.main(
            [
                "start",
                "--agent",
                "researcher",
                "--config",
                path,
            ]
        )
        == 1
    )
    response = json.loads(capsys.readouterr().err)
    assert response["error"]["code"] == "invalid_input"
    assert response["error"]["effects"] == "none"
    assert response["changed"] is False


@pytest.mark.parametrize("operation", ["start", "invoke", "stop"])
def test_command_stdout_is_the_sdk_value(
    operation, host_request, mock_runtime, monkeypatch, capsys
):
    from unittest.mock import AsyncMock

    request = host_request(operation)
    if operation == "start":
        value = mock_runtime.handle.to_mapping()
        result = value
    elif operation == "invoke":
        value = mock_runtime.invoke.return_value.to_mapping()
        result = {"runtime_id": "runtime-1", "fabric_result": value}
    else:
        value = None
        result = {"runtime_id": "runtime-1"}
    response = protocol.envelope(
        operation, result, changed=None if operation == "invoke" else True
    )
    protocol.validate_response(response, operation)
    monkeypatch.setattr(cli, "parse_command", lambda _: request)
    mock_client = AsyncMock(return_value=response)
    monkeypatch.setattr(cli.transport, "client", mock_client)
    assert cli.main([operation]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(captured.out.splitlines()) == 1
    assert json.loads(captured.out) == value
    mock_client.assert_awaited_once()


@pytest.mark.parametrize("output", [None, {"partial": "review"}])
def test_failed_sdk_result_preserves_output_and_declared_artifacts(
    output, host_request, mock_runtime, monkeypatch, capsys
):
    from unittest.mock import AsyncMock
    from nemo_fabric import RunResult

    value = RunResult.from_mapping(
        {
            **mock_runtime.invoke.return_value.to_mapping(),
            "status": "failed",
            "output": output,
            "error": {"code": "agent_failed", "stage": "invoke", "message": "Failed"},
            "artifacts": {
                "artifacts": [{"name": "review", "kind": "review", "path": "review.md"}]
            },
        }
    ).to_mapping()
    response = protocol.envelope(
        "invoke", {"runtime_id": "runtime-1", "fabric_result": value}, changed=None
    )
    protocol.validate_response(response, "invoke")
    monkeypatch.setattr(cli, "parse_command", lambda _: host_request("invoke"))
    monkeypatch.setattr(cli.transport, "client", AsyncMock(return_value=response))
    assert cli.main(["invoke"]) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == value


def test_private_response_rejects_mismatched_sdk_runtime_identity(mock_runtime):
    response = protocol.envelope(
        "invoke",
        {
            "runtime_id": "another-runtime",
            "fabric_result": mock_runtime.invoke.return_value.to_mapping(),
        },
        changed=None,
    )
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_response(response, "invoke")
