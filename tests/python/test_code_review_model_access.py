# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deployment model access and task-side preflight regressions."""

import io
import json
import os
import subprocess
import sys
import urllib.error
from unittest.mock import MagicMock

import pytest
import yaml

from examples.code_review_agent.config import BASE_DIR
from examples.code_review_agent.openshell.model_access import (
    inference_policy,
    main,
    validate_access,
)
from examples.code_review_agent.openshell.preflight import probe


@pytest.fixture(name="access")
def access_fixture():
    return {
        "model": "review-model",
        "base_url": "http://models.example:8000/v1",
        "api_key_env": "TASK_KEY",
    }


@pytest.mark.parametrize(
    "url",
    [
        "ftp://server/v1",
        "http://user:secret@server/v1",
        "http://server/v1?key=secret",
        "http://server/v1#fragment",
        "http://server:0/v1",
        "http://server:99999/v1",
        "http://server/a/../v1",
        "http://server/%2e/v1",
        "http://server//v1",
    ],
)
def test_model_access_rejects_unsafe_or_ambiguous_endpoints(access, url):
    access["base_url"] = url
    with pytest.raises(ValueError):
        validate_access(access)


@pytest.mark.parametrize(
    "harness,binary",
    [("hermes", "/opt/hermes-venv/bin/python*"), ("openclaw", "/usr/local/bin/node")],
)
def test_generated_deployment_and_consumer_share_one_model_declaration(
    tmp_path, access, harness, binary
):
    os.environ.update(
        CODE_REVIEW_MODEL=access["model"],
        CODE_REVIEW_BASE_URL="https://models.example/api/v1/",
        CODE_REVIEW_API_KEY_ENV="TASK_KEY",
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.code_review_agent.openshell.model_access",
            "--harness",
            harness,
            "--output-dir",
            str(tmp_path),
        ],
        cwd=BASE_DIR.parents[1],
        check=True,
    )
    declaration = json.loads((tmp_path / "model-access.json").read_text())
    policy = yaml.safe_load((tmp_path / "policy.yaml").read_text())
    rule = policy["network_policies"]["model_inference"]
    assert rule["endpoints"][0] == {
        "host": "models.example",
        "port": 443,
        "protocol": "rest",
        "enforcement": "enforce",
        "rules": [{"allow": {"method": "POST", "path": "/api/v1/chat/completions"}}],
    }
    assert {"path": binary} in rule["binaries"]
    assert policy["filesystem_policy"]["read_write"] == [
        "/sandbox",
        "/home/sandbox",
        "/tmp",
        "/dev/null",
    ]
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.code_review_agent",
            "--variant",
            harness,
            "--environment",
            "openshell",
            "--image",
            "sha256:test",
            "--model-access-file",
            str(tmp_path / "model-access.json"),
            "--plan",
        ],
        cwd=BASE_DIR.parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    plan = json.loads(completed.stdout)
    model = plan["agent_config"]["models"]["default"]
    assert {key: model[key] for key in declaration} == declaration


def test_model_access_requires_a_base_url(tmp_path, monkeypatch):
    monkeypatch.delenv("CODE_REVIEW_BASE_URL", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "model_access.py",
            "--harness",
            "openclaw",
            "--output-dir",
            str(tmp_path),
        ],
    )
    with pytest.raises(SystemExit, match="CODE_REVIEW_BASE_URL"):
        main()


def test_preflight_uses_task_credential_without_printing_it(access, monkeypatch):
    os.environ["TASK_KEY"] = "private-test-key"
    response = MagicMock()
    response.__enter__.return_value = io.StringIO(
        '{"choices":[{"message":{"content":"OK"}}]}'
    )
    request = MagicMock(return_value=response)
    monkeypatch.setattr("urllib.request.urlopen", request)
    result = probe(access)
    assert result["reachable"] is True
    sent = request.call_args.args[0]
    assert sent.headers["Authorization"] == "Bearer private-test-key"
    assert json.loads(sent.data)["model"] == access["model"]
    assert request.call_args.kwargs["timeout"] == 30
    assert "private-test-key" not in json.dumps(result)


@pytest.mark.parametrize(
    "failure,match",
    [
        ("missing", "credential.*missing"),
        ("denied", "HTTP 403"),
        ("unreachable", "unreachable"),
        ("invalid", "no completion choices"),
    ],
)
def test_preflight_fails_before_agent_start(access, monkeypatch, failure, match):
    os.environ.pop("TASK_KEY", None)
    response = MagicMock()
    response.__enter__.return_value = io.StringIO("{}")
    request = MagicMock(return_value=response)
    monkeypatch.setattr("urllib.request.urlopen", request)
    if failure != "missing":
        os.environ["TASK_KEY"] = "private-test-key"
    if failure == "denied":
        request.side_effect = urllib.error.HTTPError(
            access["base_url"], 403, "private-test-key", {}, None
        )
    elif failure == "unreachable":
        request.side_effect = urllib.error.URLError("private-test-key")
    with pytest.raises(RuntimeError, match=match) as caught:
        probe(access)
    assert "private-test-key" not in str(caught.value)
    if failure == "missing":
        request.assert_not_called()


def test_policy_rejects_credential_values_in_place_of_references(access):
    access["api_key_env"] = "secret with spaces"
    with pytest.raises(ValueError, match="environment variable"):
        inference_policy(access, "hermes")
