# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Task-path and lifecycle regressions for the Model A review example."""

from pathlib import Path
import gzip
import json
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from nemo_fabric import Fabric
from examples.code_review_agent import (
    BASE_DIR,
    hermes_config,
    openclaw_config,
    with_openshell,
)
from examples.code_review_agent.__main__ import _review_turns
from examples.code_review_agent.openshell.verify_run import hermes_read_calls
from examples.code_review_agent.openshell.verify_run import verify_results
from examples.code_review_agent.openshell.verify_run import verify_relay


@pytest.mark.parametrize(
    "first,second,accepted",
    [
        (["100", "162"], ["100"], True),
        (["100"], ["200"], False),
        (["100", "200"], ["100", "200"], False),
        ([], ["100"], False),
    ],
)
def test_openclaw_gateway_identity_is_correlated_to_both_user_turns(
    first, second, accepted
):
    results = [
        {
            "status": "succeeded",
            "runtime_id": "runtime-review",
            "output": {"response": "calculator.py returns 41"},
        },
        {
            "status": "succeeded",
            "runtime_id": "runtime-review",
            "output": {"response": "label-review"},
        },
    ]
    evidence = {
        "processes": {
            "57": {"role": "adapter"},
            "100": {"role": "openclaw_gateway"},
            "162": {"role": "openclaw_gateway"},
        },
        "user_turns_by_session": {"session-review": 2},
        "gateway_pids_by_user_turn": {"session-review": {"1": first, "2": second}},
        "read_calls": [{"path": "/sandbox/calculator.py"}],
    }
    if accepted:
        verify_results(results, evidence, harness="openclaw", label="label-review")
    else:
        with pytest.raises(ValueError, match="Gateway PID"):
            verify_results(results, evidence, harness="openclaw", label="label-review")


@pytest.mark.parametrize(
    "failure",
    [None, "model", "correlation", "completion", "read", "atif", "scope", "missing"],
)
def test_relay_verification_requires_collected_correlated_completed_evidence(
    tmp_path, failure
):
    results = []
    for number in (1, 2):
        invocation = f"invocation-{number}"
        request = f"request-{number}"
        metadata = {
            "api_request_id": f"runtime-review:{request}:api:1",
            "otel.status_code": "OK",
        }
        records = [
            {
                "uuid": "model",
                "name": "openai.chat_completions",
                "scope_category": "start",
                "metadata": metadata,
                "data": {"content": {"model": "review-model"}},
            },
            {
                "uuid": "model",
                "name": "openai.chat_completions",
                "scope_category": "end",
                "metadata": metadata,
                "data": {},
            },
        ]
        if number == 1:
            records += [
                {
                    "uuid": "read",
                    "name": "read_file",
                    "scope_category": "start",
                    "metadata": metadata,
                    "data": {"path": "/sandbox/calculator.py"},
                },
                {
                    "uuid": "read",
                    "name": "read_file",
                    "scope_category": "end",
                    "metadata": metadata,
                    "data": json.dumps({"content": "return 41"}),
                },
            ]
            if failure == "model":
                records[0]["data"]["content"]["model"] = "other-model"
            elif failure == "correlation":
                metadata["api_request_id"] = "runtime-other:request-other:api:1"
            elif failure == "completion":
                metadata["otel.status_code"] = "ERROR"
            elif failure == "read":
                records[-1]["data"] = '{"content":"denied","success":false}'
        root = (
            tmp_path
            / ("runtime-other" if failure == "scope" else "runtime-review")
            / invocation
        )
        root.mkdir(parents=True)
        artifacts = []
        for kind, data in [
            ("atof", "\n".join(json.dumps(record) for record in records)),
            (
                "atif",
                json.dumps(
                    {
                        "steps": [{"source": "agent"}],
                        "extra": {
                            "observed_events": [] if failure == "atif" else records
                        },
                    }
                ),
            ),
        ]:
            path = root / (kind + ".gz")
            path.write_bytes(gzip.compress(data.encode()))
            artifacts.append({"kind": kind, "path": str(path)})
        if failure == "missing":
            path.unlink()
        results.append(
            {
                "runtime_id": "runtime-review",
                "invocation_id": invocation,
                "request_id": request,
                "output": {"model": "review-model"},
                "artifacts": {"artifacts": artifacts},
            }
        )
    if failure:
        with pytest.raises((ValueError, FileNotFoundError)):
            verify_relay(results)
    else:
        summary = verify_relay(results)
        assert summary["file_reads"] == ["/sandbox/calculator.py"]
        assert [invocation["model_calls"] for invocation in summary["invocations"]] == [
            1,
            1,
        ]


@pytest.mark.parametrize("builder", [openclaw_config, hermes_config])
def test_remote_projection_uses_task_paths_without_changing_local_config(builder):
    local = builder()
    remote = with_openshell(
        local, gateway="http://localhost:18080", image="sha256:test"
    )
    plan = Fabric().plan(remote, base_dir=BASE_DIR).to_mapping()
    assert plan["agent_config"]["skills"]["paths"] == [
        "/opt/nemo-fabric/skills/code-review"
    ]
    assert remote.environment.workspace == "/sandbox/repos/my-service"
    assert remote.environment.ownership == "caller_owned"
    assert remote.harness.resolution == "image_provided"
    assert local.environment.provider == "local"
    assert local.harness.resolution == "preinstalled"
    assert [str(path) for path in local.skills.paths] == ["./skills/code-review"]
    assert remote.models == local.models
    assert remote.tools == local.tools
    if builder == hermes_config:
        assert local.tools.enabled == ["file"]
        assert local.runtime.max_turns == 10
        assert remote.environment.env["VIRTUAL_ENV"] == "/opt/hermes-venv"
        assert remote.environment.env["PATH"].startswith("/opt/hermes-venv/bin:")
        assert local.environment.env == {}


@pytest.mark.parametrize("failure", [None, "start", "invoke", "stop", "result"])
async def test_remote_cleanup_and_two_turn_order(failure):
    fabric = MagicMock()
    environment = MagicMock()
    runtime = MagicMock()
    reference = MagicMock()
    events = []

    async def attach(*args, **kwargs):
        events.append("attach")
        return environment

    async def start(*args, **kwargs):
        events.append("start")
        if failure == "start":
            raise RuntimeError("start")
        return runtime

    async def invoke(*, input):
        events.append(input)
        if failure == "invoke":
            raise RuntimeError("invoke")
        return MagicMock(status="failed" if failure == "result" else "succeeded")

    async def stop():
        events.append("stop")
        if failure == "stop":
            raise RuntimeError("stop")

    async def release(*args):
        events.append("release")

    fabric.attach_environment = AsyncMock(side_effect=attach)
    fabric.start_runtime_in = AsyncMock(side_effect=start)
    fabric.release_environment = AsyncMock(side_effect=release)
    runtime.invoke = AsyncMock(side_effect=invoke)
    runtime.stop = AsyncMock(side_effect=stop)
    call = _review_turns(
        fabric,
        openclaw_config(),
        input_value="review",
        follow_up="follow-up",
        reference=reference,
    )
    if failure in {"start", "invoke", "stop"}:
        with pytest.raises(RuntimeError, match=failure):
            await call
    else:
        results = await call
        assert len(results) == (1 if failure == "result" else 2)
    expected = ["attach", "start"]
    if failure != "start":
        expected += ["review"]
        if failure not in {"invoke", "result"}:
            expected += ["follow-up"]
        expected += ["stop"]
    assert events == expected + ["release"]
    fabric.release_environment.assert_awaited_once_with(environment)


async def test_local_two_turns_do_not_attach_an_environment():
    fabric = MagicMock()
    runtime = MagicMock()
    runtime.invoke = AsyncMock(return_value=MagicMock(status="succeeded"))
    runtime.stop = AsyncMock()
    fabric.start_runtime = AsyncMock(return_value=runtime)
    await _review_turns(
        fabric, openclaw_config(), input_value="review", follow_up="follow-up"
    )
    assert [call.kwargs["input"] for call in runtime.invoke.await_args_list] == [
        "review",
        "follow-up",
    ]
    runtime.stop.assert_awaited_once()
    fabric.attach_environment.assert_not_called()


def test_custom_remote_skills_require_explicit_task_paths():
    config = openclaw_config()
    config.skills.paths = [Path("other-host-skill")]
    with pytest.raises(ValueError, match="absolute task paths"):
        with_openshell(config, gateway="http://localhost", image="sha256:test")


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"content": "return 41", "not_found": False}, ["calculator.py"]),
        ({"error": "missing file"}, []),
        ({"content": "", "not_found": True}, []),
        ({"content": "denied", "success": False}, []),
    ],
)
def test_hermes_read_evidence_requires_a_completed_tool_call(result, expected):
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "read-1",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"calculator.py"}',
                    },
                },
                {
                    "id": "unfinished",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"other.py"}',
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "read-1",
            "content": json.dumps(result),
        },
    ]
    assert hermes_read_calls(messages) == expected


@pytest.mark.parametrize(
    "failure", [None, "runtime", "label", "read", "process", "state"]
)
def test_hermes_live_acceptance_rejects_missing_evidence(failure):
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "read-1",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"calculator.py"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "read-1",
            "content": '{"content":"return 41"}',
        },
    ]
    results = [
        {
            "status": "succeeded",
            "runtime_id": "runtime-1",
            "output": {
                "response": "calculator.py returns 41",
                "messages": messages,
                "hermes_home": "/sandbox/artifacts/runtimes/runtime-1",
            },
        },
        {
            "status": "succeeded",
            "runtime_id": "runtime-1",
            "output": {
                "response": "The label was review-123",
                "messages": messages,
                "hermes_home": "/sandbox/artifacts/runtimes/runtime-1",
            },
        },
    ]
    evidence = {"processes": {"57": {"role": "adapter"}}}
    if failure == "runtime":
        results[1]["runtime_id"] = "runtime-2"
    elif failure == "label":
        results[1]["output"]["response"] = "I do not recall"
    elif failure == "read":
        results[0]["output"]["messages"] = []
    elif failure == "process":
        evidence["processes"]["58"] = {"role": "adapter"}
    elif failure == "state":
        results[1]["output"]["hermes_home"] = "/sandbox/different-state"
    if failure is not None:
        with pytest.raises(ValueError):
            verify_results(results, evidence, harness="hermes", label="review-123")
    else:
        summary = verify_results(
            results, evidence, harness="hermes", label="review-123"
        )
        assert summary["file_reads"] == ["calculator.py"]


@pytest.mark.parametrize(
    "options",
    [
        ["--variant", "pi", "--environment", "openshell", "--image", "sha256:test"],
        ["--variant", "openclaw", "--environment", "openshell"],
        ["--image", "sha256:test"],
        [
            "--variant",
            "openclaw",
            "--environment",
            "openshell",
            "--image",
            "sha256:test",
            "--service",
        ],
        [
            "--variant",
            "openclaw",
            "--environment",
            "openshell",
            "--image",
            "sha256:test",
            "--relay",
        ],
    ],
)
def test_cli_rejects_unsupported_environment_combinations(options):
    completed = subprocess.run(
        [sys.executable, "-m", "examples.code_review_agent", *options, "--plan"],
        cwd=BASE_DIR.parents[1],
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 2


def test_cli_plans_without_attachment_and_saves_host_results(tmp_path):
    destination = tmp_path / "evidence" / "plan.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.code_review_agent",
            "--variant",
            "openclaw",
            "--environment",
            "openshell",
            "--image",
            "sha256:test",
            "--plan",
            "--results-file",
            str(destination),
        ],
        cwd=BASE_DIR.parents[1],
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    saved = json.loads(destination.read_text())
    assert saved == json.loads(completed.stdout)
    assert saved["agent_config"]["skills"]["paths"] == [
        "/opt/nemo-fabric/skills/code-review"
    ]
