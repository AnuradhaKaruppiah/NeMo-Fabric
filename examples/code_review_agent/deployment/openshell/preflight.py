# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Probe inference from the task using the same Python caller and policy as Hermes."""

import json
import os
import sys
import urllib.error
import urllib.request


def probe(access):
    key = os.environ.get(access["api_key_env"])
    if not key:
        raise RuntimeError(
            "model credential environment variable is missing in the sandbox"
        )
    body = json.dumps(
        {
            "model": access["model"],
            "messages": [{"role": "user", "content": "Reply OK."}],
            "max_tokens": 8,
        }
    ).encode()
    request = urllib.request.Request(
        access["base_url"].rstrip("/") + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"model preflight failed: HTTP {error.code}") from None
    except (OSError, ValueError):
        raise RuntimeError(
            "model preflight failed: endpoint unreachable or invalid response"
        ) from None
    if not result.get("choices"):
        raise RuntimeError("model preflight failed: response has no completion choices")
    return {"model": access["model"], "reachable": True, "credential_present": True}


if __name__ == "__main__":
    try:
        print(json.dumps(probe(json.loads(sys.argv[1]))))
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
