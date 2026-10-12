# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""One model declaration for the consumer, creation-time policy, and task probe."""

import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit


def validate_access(access):
    if not isinstance(access.get("model"), str) or not access["model"].strip():
        raise ValueError("model must be a non-empty model ID")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", access.get("api_key_env", "")):
        raise ValueError("api_key_env must name a task environment variable")
    url = urlsplit(access["base_url"])
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or any(part in {".", ".."} for part in url.path.split("/"))
        or "%" in url.path
        or "//" in url.path
    ):
        raise ValueError(
            "base_url must be an HTTP(S) endpoint without credentials, query, or traversal"
        )
    port = url.port if url.port is not None else (443 if url.scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise ValueError("base_url port must be between 1 and 65535")
    return url.hostname, port, url.path.rstrip("/") + "/chat/completions"


def inference_policy(access, harness):
    host, port, path = validate_access(access)
    callers = {
        "openclaw": ["/usr/local/bin/node"],
        "hermes": ["/usr/local/bin/python*", "/opt/hermes-venv/bin/python*"],
    }
    return {
        "model_inference": {
            "name": "Review model inference",
            "endpoints": [
                {
                    "host": host,
                    "port": port,
                    "protocol": "rest",
                    "enforcement": "enforce",
                    "rules": [{"allow": {"method": "POST", "path": path}}],
                }
            ],
            "binaries": [{"path": binary} for binary in callers[harness]],
        }
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=["openclaw", "hermes"], required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    access = {
        "model": os.environ.get(
            "CODE_REVIEW_MODEL", "nvidia/nemotron-3-ultra-550b-a55b"
        ),
        "base_url": os.environ.get(
            "CODE_REVIEW_BASE_URL", "http://10.86.19.10:8000/v1"
        ),
        "api_key_env": os.environ.get(
            "CODE_REVIEW_API_KEY_ENV", "FABRIC_LOCAL_INFERENCE_KEY"
        ),
    }
    network = inference_policy(access, args.harness)
    filename = "hermes-policy.yaml" if args.harness == "hermes" else "policy.yaml"
    template = Path(__file__).with_name(filename).read_text()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "model-access.json").write_text(
        json.dumps(access, indent=2) + "\n"
    )
    (args.output_dir / "policy.yaml").write_text(
        template
        + "network_policies: "
        + json.dumps(network, indent=2).replace("\n", "\n  ")
        + "\n"
    )


if __name__ == "__main__":
    main()
